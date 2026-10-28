"""Throughput at 1, 4 and 8 worker processes (BUILD_PLAN section 12).

Workers use the `spawn` start method and each creates its own psycopg connection and MongoClient after it starts
(PyMongo is not fork-safe). They read as app_ro and the app user, like the timed reads, and take the case's parameter
sets round-robin. Each level runs a warm-up, then the measured period; the result is calls per second and the median
latency of the measured calls.
"""

from __future__ import annotations

import multiprocessing
import time
from collections.abc import Callable

from bench import connect
from bench.cases import BenchCase, load_cases
from bench.stats import median, p95

CASES = ("search_filtered", "building_page")
LEVELS = (1, 4, 8)
WARMUP_SECONDS = 5.0
MEASURE_SECONDS = 30.0
BARRIER_TIMEOUT = 180.0


def _worker(case_id: str, db: str, index: int, args_list: list[dict], warmup_s: float, seconds: float, barrier, queue):
    """One worker process: own connection, wait for the others, warm up, then measure."""
    try:
        case = next(c for c in load_cases() if c.id == case_id)
        side = case.pg if db == "pg" else case.mongo
        prepared = [side.prepare(a) for a in args_list]
        if db == "pg":
            handle = connect.open_pg("app_ro")
            closer = handle.close
            target = handle
        else:
            client, target = connect.open_mongo("app")
            closer = client.close
        try:
            barrier.wait(timeout=BARRIER_TIMEOUT)
            k = index
            end_warm = time.monotonic() + warmup_s
            while time.monotonic() < end_warm:
                prepared[k % len(prepared)](target)
                k += 1
            latencies: list[float] = []
            start = time.monotonic()
            end = start + seconds
            while time.monotonic() < end:
                t0 = time.perf_counter_ns()
                prepared[k % len(prepared)](target)
                latencies.append((time.perf_counter_ns() - t0) / 1e6)
                k += 1
            queue.put(("ok", latencies, time.monotonic() - start))
        finally:
            closer()
    except BaseException as exc:  # report to the parent instead of leaving it waiting
        queue.put(("error", f"{type(exc).__name__}: {exc}", 0.0))
        raise


def run_level(case_id: str, db: str, workers: int, args_list: list[dict], warmup_s: float, seconds: float) -> dict:
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(workers)
    queue = ctx.Queue()
    procs = [
        ctx.Process(target=_worker, args=(case_id, db, i, args_list, warmup_s, seconds, barrier, queue))
        for i in range(workers)
    ]
    for p in procs:
        p.start()
    results = []
    try:
        for _ in procs:
            kind, payload, elapsed = queue.get(timeout=BARRIER_TIMEOUT + warmup_s + seconds + 120)
            if kind == "error":
                raise RuntimeError(f"throughput worker failed: {payload}")
            results.append((payload, elapsed))
    finally:
        for p in procs:
            p.join(timeout=30)
            if p.is_alive():
                p.terminate()
    latencies = [x for lat, _ in results for x in lat]
    elapsed = max(e for _, e in results)
    if not latencies:
        raise RuntimeError(f"{case_id}/{db} with {workers} workers completed no calls")
    return {
        "case": case_id,
        "db": db,
        "workers": workers,
        "seconds": elapsed,
        "calls": len(latencies),
        "calls_per_s": len(latencies) / elapsed,
        "median_ms": median(latencies),
        "p95_ms": p95(latencies),
    }


def run_throughput(
    invocation: str,
    cases: list[BenchCase] | None = None,
    *,
    warmup_s: float = WARMUP_SECONDS,
    seconds: float = MEASURE_SECONDS,
    levels: tuple[int, ...] = LEVELS,
    log: Callable[[str], None] = print,
) -> list[dict]:
    """Rows for throughput.csv. `cases` must already have their building ids resolved."""
    cases = cases if cases is not None else load_cases()
    rows = []
    for case in (c for c in cases if c.id in CASES):
        args_list = [ps.args for ps in case.params]
        for db in ("pg", "mongo"):
            for workers in levels:
                row = run_level(case.id, db, workers, args_list, warmup_s, seconds)
                row["invocation"] = invocation
                log(
                    f"throughput {case.id} {db} {workers} worker(s): {row['calls_per_s']:.1f} calls/s, "
                    f"median {row['median_ms']:.2f} ms"
                )
                rows.append(row)
    return rows
