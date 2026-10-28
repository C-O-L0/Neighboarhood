"""Benchmark runner (task 4.1a): checks, then timed calls on both databases, then the results files.

    python -m bench.run [--cases id,...] [--include-writes] [--throughput] [--label NAME]
                        [--expect-fraction F] [--runs N --warmup N] [--replace]

A plain run times the read cases; --include-writes runs only the write cases; --throughput runs only the throughput
levels. Invocations into one label append to its files. Timings are warm cache only: every case is called `warmup`
times before the timed calls, and nothing is flushed between runs.

Before any timing: missing indexes are recreated (MongoDB) or checked (PostgreSQL); db_counts must match between the
databases; the loaded fraction must match --expect-fraction; and bench.verify must pass. A refusal exits 2, a
verification mismatch exits 1.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.repos.dto import SearchResult
from app.repos.mongo.building import MongoBuildingRepo
from app.repos.pg.building import PgBuildingRepo
from bench import capture, connect, verify
from bench.cases import BenchCase, load_cases
from bench.stats import SUMMARY_COLUMNS, THROUGHPUT_COLUMNS, summarise, write_rows, write_summary
from bench.throughput import CASES as THROUGHPUT_CASES
from bench.throughput import MEASURE_SECONDS, WARMUP_SECONDS, run_throughput
from db.mongo.indexes import INDEXES, create_indexes
from db.mongo.stats import rebuild_all_stats
from etl import db_counts

ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = ROOT / "bench" / "results"
IMPORT_REPORT = ROOT / "docs" / "import_report.md"
PG_INDEX_FILE = ROOT / "db" / "postgres" / "004_indexes.sql"
FRACTION_TOLERANCE = 0.001
# One or two path segments (for example 2026-11-full/f100); each starts with a letter or digit (no "..", no "/x").
LABEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)?")

README = f"""{capture.SERVER_TIME_RULE}

Benchmark results (task 4.1a harness)

Warm cache only: every case is called `warmup` times before the timed calls, and no cache is flushed between runs.
Client timings use time.perf_counter_ns() around the whole repo call, including materialisation of the result.

Timed reads run as PostgreSQL app_ro (SELECT only) and MongoDB app. The two read identities are not symmetric in what
they could do: MongoDB app can write buyers and counters, but the harness never calls a write with it. Write cases
(--include-writes) run as app_admin in both databases, as the app's admin routes do.

Files
  raw.jsonl      every timed call: invocation, case, db, param_set, part, run, ms, rows, conn
  summary.csv    {", ".join(SUMMARY_COLUMNS)}
                 part is `call` for reads; `write` and `refresh` split an admin write (refresh is PostgreSQL only).
                 rows is the rows or documents the call returned (precomputed summaries have a different grain).
                 median_ms is the usual median, p95_ms is nearest rank.
  throughput.csv {", ".join(THROUGHPUT_COLUMNS)}
  index_on_off.csv  each indexed case with and without its index, per database (bench.explain); compare a database
                 only with itself
  env.json       software, settings and the list of invocations
  explain/       the captured statements and their plans (text and JSON)
"""


class Refusal(Exception):
    """A pre-run check failed; nothing was timed."""


# ------------------------------------------------------------------------------------------------- checks


def pg_index_names() -> list[str]:
    return re.findall(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(\w+)", PG_INDEX_FILE.read_text(encoding="utf-8"), re.I)


def ensure_indexes(pg_conn, etl_db, log: Callable[[str], None] = print) -> None:
    """Recreate any missing MongoDB index (idempotent) and require every PostgreSQL index of 004_indexes.sql."""
    have = {r[0] for r in pg_conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'").fetchall()}
    missing_pg = [n for n in pg_index_names() if n not in have]
    if missing_pg:
        raise Refusal(f"PostgreSQL indexes missing: {', '.join(missing_pg)}")
    missing = [
        coll
        for coll, models in INDEXES.items()
        if {m.document["name"] for m in models} - set(etl_db[coll].index_information())
    ]
    if missing:
        log(f"checks: recreating MongoDB indexes on {', '.join(missing)}")
        create_indexes(etl_db, missing)
    log("checks: indexes present in both databases")


def ensure_stats(mongo_db, admin_db, log: Callable[[str], None] = print) -> None:
    """A writes run repairs an interrupted stats rebuild first.

    rebuild_all_stats resets every profile to empty stats before the $merge, so an interrupted rebuild leaves profiles
    with txn_count 0. Every block with sales must have stats: compare the profiles with sales counted in their stats
    against the distinct blocks in resale_transactions.
    """
    with_stats = mongo_db.building_profiles.count_documents({"stats.txn_count": {"$gt": 0}})
    with_sales = len(mongo_db.resale_transactions.distinct("building_id"))
    if with_stats != with_sales:
        log(f"checks: {with_stats} profiles have stats but {with_sales} blocks have sales; running rebuild_all_stats")
        rebuild_all_stats(admin_db)


def counts_equal(pg_conn, mongo_db) -> dict:
    counts = db_counts.collect(pg_conn, mongo_db)
    bad = [e for e, _, mode in db_counts.ENTITIES if mode != "pg_cleaning" and counts[e]["pg"] != counts[e]["mongo"]]
    if bad:
        raise Refusal(f"db_counts differ between the databases for: {', '.join(bad)}")
    return counts


def import_report_sales() -> int:
    m = re.search(r"Sales:\s*([\d,]+)\s+read", IMPORT_REPORT.read_text(encoding="utf-8"))
    if not m:
        raise Refusal(f"cannot find the sale count in {IMPORT_REPORT}")
    return int(m[1].replace(",", ""))


def check_fraction(counts: dict, expected: float) -> float:
    """PostgreSQL snapshot sales over the sales in docs/import_report.md; must match --expect-fraction."""
    fraction = counts["snapshot_sale"]["pg"] / import_report_sales()
    if abs(fraction - expected) > FRACTION_TOLERANCE:
        raise Refusal(
            f"loaded fraction is {fraction:.4f} of the import report, but --expect-fraction is {expected} "
            f"(tolerance {FRACTION_TOLERANCE}); reload the right data or pass the right fraction"
        )
    return fraction


# ------------------------------------------------------------------------------------------------- helpers


def count_rows(result) -> int:
    if result is None:
        return 0
    if isinstance(result, SearchResult):
        return len(result.rows)
    if isinstance(result, list | tuple):
        return len(result)
    return 1


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def type_name(obj) -> str:
    return f"{type(obj).__module__}.{type(obj).__qualname__}"


def read_csv_dicts(path: Path) -> list[dict]:
    import csv

    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def rewrite_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.unlink(missing_ok=True)
    if rows:
        write_rows(path, columns, rows)


@dataclass
class Handles:
    """Open connections. `pg` and `mongo` are bare and timed; the others are for the checks and the explains."""

    pg: object  # app_ro, timed
    pg_explain: object  # app_ro, the EXPLAIN runs
    pg_admin: object  # app_admin
    mongo: object  # database as the app user, timed
    mongo_admin: object  # database as app_admin
    mongo_etl: object  # database as the etl user (index checks only)


# --------------------------------------------------------------------------------------------- results folder


class Results:
    def __init__(self, directory: Path):
        self.dir = directory
        self.raw = directory / "raw.jsonl"
        self.summary = directory / "summary.csv"
        self.throughput = directory / "throughput.csv"
        self.env = directory / "env.json"
        self.explain = directory / "explain"

    def env_doc(self) -> dict:
        return json.loads(self.env.read_text(encoding="utf-8")) if self.env.exists() else {}

    def save_env(self, doc: dict) -> None:
        self.env.write_text(json.dumps(doc, indent=2, default=str) + "\n", encoding="utf-8")

    def has_reads(self) -> bool:
        return any(i.get("kind") == "reads" for i in self.env_doc().get("invocations", []))

    def drop_reads(self) -> None:
        """--replace: remove the earlier read invocations' rows and entries; writes and throughput stay."""
        doc = self.env_doc()
        old = {i["id"] for i in doc.get("invocations", []) if i.get("kind") == "reads"}
        doc["invocations"] = [i for i in doc.get("invocations", []) if i["id"] not in old]
        self.save_env(doc)
        keep = [r for r in read_csv_dicts(self.summary) if r["invocation"] not in old]
        rewrite_csv(self.summary, SUMMARY_COLUMNS, keep)
        if self.raw.exists():
            lines = [
                x for x in self.raw.read_text(encoding="utf-8").splitlines() if json.loads(x)["invocation"] not in old
            ]
            self.raw.write_text("".join(x + "\n" for x in lines), encoding="utf-8")

    def append_raw(self, rows: list[dict]) -> None:
        with open(self.raw, "a", encoding="utf-8", newline="") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")


# ------------------------------------------------------------------------------------------------- timing


def time_reads(case: BenchCase, h: Handles, inv: str, res: Results, log, runs: int, warmup: int) -> list[dict]:
    """Capture, warm up, time and explain each parameter set on each database. Returns summary rows."""
    summary = []
    for ps in case.params:
        line = []
        for db, side, target, explain_target in (
            ("pg", case.pg, h.pg, h.pg_explain),
            ("mongo", case.mongo, h.mongo, h.mongo),
        ):
            if side is None:
                continue
            prepared = side.prepare(ps.args)
            # One separate, untimed call through the recorder; the timed calls below use the bare connection.
            if db == "pg":
                captured, _ = capture.capture_pg(prepared, target)
            else:
                captured, _ = capture.capture_mongo(prepared, target)
            for _ in range(warmup):
                prepared(target)
            timings: list[float] = []
            raw: list[dict] = []
            rows = 0
            for i in range(runs):
                t0 = time.perf_counter_ns()
                result = prepared(target)
                ms = (time.perf_counter_ns() - t0) / 1e6
                rows = count_rows(result)
                timings.append(ms)
                raw.append(
                    {"invocation": inv, "case": case.id, "db": db, "param_set": ps.label, "part": "call",
                     "run": i + 1, "ms": ms, "rows": rows, "conn": type_name(target)}
                )  # fmt: skip
            res.append_raw(raw)
            if db == "pg":
                capture.explain_pg(explain_target, captured)
                unexplained = [s.text for s in captured if s.select and not s.explained]
                if unexplained:
                    raise RuntimeError(f"{case.id}: captured SELECT not explained: {unexplained[0][:80]}")
                server = capture.pg_server_time(captured)
                capture.save_pg_explain(res.explain, case.id, ps.label, captured)
            else:
                explains = capture.explain_mongo(explain_target, captured)
                server = capture.mongo_server_time(explains)
                capture.save_mongo_explain(res.explain, case.id, ps.label, captured, explains)
            row = summarise(inv, case.id, case.pair, db, ps.label, timings, rows, "call", server)
            summary.append(row)
            line.append(f"{db} median {row['median_ms']:.2f} ms p95 {row['p95_ms']:.2f} ms rows {rows}")
        log(f"{case.id} [{ps.label}]: " + " | ".join(line))
    return summary


def building_ids(case: BenchCase, h: Handles) -> list[int]:
    ids = []
    for ps in case.params:
        if "block" in ps.args:
            found = PgBuildingRepo(h.pg_admin).find_id(ps.args["block"], ps.args["street"])
            if found is not None:
                ids.append(found)
    return ids


def state_snapshot(case: BenchCase, h: Handles) -> dict:
    """What a write case must leave unchanged: db_counts, the affected building pages in both databases, the MV."""
    ids = building_ids(case, h)
    mv = h.pg_admin.execute(
        "SELECT town_id, flat_type_id, month, n, median_price, avg_psm FROM mv_town_month_stats"
        " ORDER BY town_id, flat_type_id, month"
    ).fetchall()
    return {
        "counts": db_counts.collect(h.pg_admin, h.mongo),
        "pg_buildings": [PgBuildingRepo(h.pg_admin).get(i) for i in ids],
        "mongo_buildings": [MongoBuildingRepo(h.mongo).get(i) for i in ids],
        "mv": mv,
    }


def time_writes(case: BenchCase, h: Handles, inv: str, res: Results, log, runs: int, warmup: int) -> list[dict]:
    """Time a write case as app_admin in both databases, then settle and prove the data is as it was."""
    before = state_snapshot(case, h)
    counter = h.mongo_admin.counters.find_one({"_id": "txn"})
    summary = []
    try:
        for ps in case.params:
            line = []
            for db, side, target in (("pg", case.pg, h.pg_admin), ("mongo", case.mongo, h.mongo_admin)):
                if side is None:
                    continue
                prepared = side.prepare(ps.args)
                for _ in range(warmup):
                    prepared(target)
                parts: dict[str, list[float]] = {}
                raw = []
                for i in range(runs):
                    for part, ns in prepared(target).items():
                        parts.setdefault(part, []).append(ns / 1e6)
                        raw.append(
                            {"invocation": inv, "case": case.id, "db": db, "param_set": ps.label, "part": part,
                             "run": i + 1, "ms": ns / 1e6, "rows": 1, "conn": type_name(target)}
                        )  # fmt: skip
                res.append_raw(raw)
                for part, timings in parts.items():
                    row = summarise(inv, case.id, case.pair, db, ps.label, timings, 1, part)
                    summary.append(row)
                    line.append(f"{db} {part} median {row['median_ms']:.2f} ms p95 {row['p95_ms']:.2f} ms")
            log(f"{case.id} [{ps.label}]: " + " | ".join(line))
    finally:
        verify.settle(h.pg_admin)
        if counter is not None:  # admin writes advance the txn counter for good; put it back (tests compare it)
            h.mongo_admin.counters.update_one({"_id": "txn"}, {"$set": {"seq": counter["seq"]}})
    after = state_snapshot(case, h)
    for key in before:
        if before[key] != after[key]:
            raise RuntimeError(f"{case.id}: {key} changed after the write case; reload the data before timing")
    log(f"{case.id}: settled; db_counts, building pages and the town-month view are as they were")
    return summary


# --------------------------------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m bench.run", description=__doc__.splitlines()[0])
    ap.add_argument("--cases", help="comma-separated case ids (default: every case of the invocation's kind)")
    ap.add_argument("--include-writes", action="store_true", help="run only the write cases")
    ap.add_argument("--throughput", action="store_true", help="run only the throughput levels")
    ap.add_argument("--label", help="results folder name under bench/results (default: UTC timestamp)")
    ap.add_argument("--expect-fraction", type=float, default=1.0, help="loaded fraction of the data (default 1)")
    ap.add_argument("--runs", type=int, help="timed runs for every case (default: per case, 50)")
    ap.add_argument("--warmup", type=int, help="warm-up calls for every case (default: per case, 5)")
    ap.add_argument("--replace", action="store_true", help="replace the label's earlier read results")
    # Test hooks and test plumbing.
    ap.add_argument("--force-mismatch", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--throughput-seconds", type=float, default=MEASURE_SECONDS, help=argparse.SUPPRESS)
    ap.add_argument("--throughput-warmup", type=float, default=WARMUP_SECONDS, help=argparse.SUPPRESS)
    ap.add_argument("--results-root", type=Path, default=RESULTS_ROOT, help=argparse.SUPPRESS)
    return ap


def server_facts(pg_conn, mongo_db) -> dict:
    version = pg_conn.execute("SHOW server_version").fetchone()[0]
    try:
        mongo_version = mongo_db.client.admin.command("buildInfo")["version"]
    except Exception:  # not every identity may run buildInfo
        mongo_version = None
    return {
        "postgresql": {"version": version, "shared_buffers": pg_conn.execute("SHOW shared_buffers").fetchone()[0]},
        "mongodb": {
            "version": mongo_version,
            "wiredtiger_cache_gb": os.environ.get("BENCH_MONGO_CACHE") or None,
            "wiredtiger_cache_source": "bench/run.sh: docker inspect of the running mongodb container",
        },
    }


def identities(h: Handles) -> dict:
    """Who the timed connections really are, read from the connections themselves."""
    pg_user = h.pg.execute("SELECT current_user").fetchone()[0]
    status = h.mongo.command("connectionStatus")
    users = [u["user"] for u in status["authInfo"]["authenticatedUsers"]]
    if pg_user != "app_ro" or users != ["app"]:
        raise Refusal(f"timed reads must run as PostgreSQL app_ro and MongoDB app, not {pg_user} and {users}")
    return {"postgresql": pg_user, "mongodb": users[0]}


def hooks_used(args: argparse.Namespace) -> list[str]:
    """Test-only options in effect, so results made with them can be told apart."""
    hooks = ["force_mismatch"] if args.force_mismatch else []
    if args.throughput and (args.throughput_seconds != MEASURE_SECONDS or args.throughput_warmup != WARMUP_SECONDS):
        hooks.append("short_throughput")
    return hooks


def new_invocation_id(kind: str, res: Results) -> str:
    base = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{kind}"
    taken = {i["id"] for i in res.env_doc().get("invocations", [])}
    inv, n = base, 1
    while inv in taken:
        n += 1
        inv = f"{base}-{n}"
    return inv


def run(args: argparse.Namespace, log: Callable[[str], None] = print) -> int:
    kind = "throughput" if args.throughput else "writes" if args.include_writes else "reads"
    if args.throughput and args.include_writes:
        raise Refusal("--include-writes and --throughput are separate invocations; run them one after the other")
    label = args.label or f"run-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    if not LABEL_PATTERN.fullmatch(label):
        raise Refusal(f"label {label!r} must be letters, digits, '.', '_' or '-', with at most one '/'")
    res = Results(args.results_root / label)

    all_cases = load_cases()
    wanted = args.cases.split(",") if args.cases else None
    if wanted:
        unknown = sorted(set(wanted) - {c.id for c in all_cases})
        if unknown:
            raise Refusal(f"unknown case id(s): {', '.join(unknown)}")
        wrong = [c.id for c in all_cases if c.id in wanted and c.writes != (kind == "writes")]
        if kind != "throughput" and wrong:
            raise Refusal(f"case(s) {', '.join(wrong)} do not belong to a {kind} run (writes need --include-writes)")
    if kind == "reads" and res.has_reads() and not args.replace:
        raise Refusal(f"label {label} already has read results; use --replace or another label")
    selected = [c for c in all_cases if (wanted is None or c.id in wanted) and c.writes == (kind == "writes")]
    if kind == "throughput":
        selected = [c for c in all_cases if wanted is None or c.id in wanted]

    s = connect.settings()
    if not s.pg_app_ro_password or not s.mongo_app_password:
        raise Refusal("PG_APP_RO_PASSWORD and MONGO_APP_PASSWORD are needed for the timed reads")
    if not s.mongo_etl_password or not s.pg_app_admin_password or not s.mongo_app_admin_password:
        raise Refusal("PG_APP_ADMIN_PASSWORD, MONGO_APP_ADMIN_PASSWORD and MONGO_ETL_PASSWORD are needed")

    started = utc_now()
    clients = []
    try:
        mongo_client, mongo_db = connect.open_mongo("app", s)
        admin_client, mongo_admin = connect.open_mongo("app_admin", s)
        etl_client, mongo_etl = connect.open_mongo("etl", s)
        clients = [mongo_client, admin_client, etl_client]
        with (
            connect.open_pg("app_ro", s) as pg,
            connect.open_pg("app_ro", s) as pg_explain,
            connect.open_pg("app_admin", s) as pg_admin,
        ):
            h = Handles(pg, pg_explain, pg_admin, mongo_db, mongo_admin, mongo_etl)
            ids = identities(h)

            # Checks before any timing (task 4.1a).
            ensure_indexes(pg, mongo_etl, log)
            if kind == "writes":
                ensure_stats(mongo_db, mongo_admin, log)
            counts = counts_equal(pg, mongo_db)
            fraction = check_fraction(counts, args.expect_fraction)
            log(f"checks: db_counts equal in both databases; loaded fraction {fraction:.4f}")
            verify.resolve_building_ids(all_cases, pg)
            result = verify.run_verify(
                pg, mongo_db, pg_admin, bench_cases=all_cases, force_mismatch=args.force_mismatch
            )
            if not result.ok:
                log("refusing to time: bench.verify failed")
                return 1

            res.dir.mkdir(parents=True, exist_ok=True)
            if not (res.dir / "README.txt").exists():
                (res.dir / "README.txt").write_text(README, encoding="utf-8")
            if kind == "reads" and args.replace:
                res.drop_reads()
            inv = new_invocation_id(kind, res)
            doc = res.env_doc()
            facts = server_facts(pg, mongo_db)
            entry = {
                "id": inv,
                "kind": kind,
                "cases": list(THROUGHPUT_CASES) if kind == "throughput" else [c.id for c in selected],
                "runs": args.runs,
                "warmup": args.warmup,
                "git_commit": os.environ.get("BENCH_GIT_COMMIT") or None,
                "fraction": fraction,
                "sale_counts": {"pg": counts["sale_all"]["pg"], "mongo": counts["sale_all"]["mongo"]},
                "utc_start": started,
                "utc_end": None,
                "test_hooks": hooks_used(args),
                "verify": {"ok": True, "checks": result.checked},
            }
            doc.setdefault("label", label)
            doc.setdefault("note", "warm cache only; see README.txt")
            doc["git_commit"] = os.environ.get("BENCH_GIT_COMMIT") or None
            doc["images"] = _json_env("BENCH_IMAGES")
            doc["docker_memory_bytes"] = _int_env("BENCH_DOCKER_MEM")
            doc["mem_limits"] = _json_env("BENCH_MEM_LIMITS")
            doc["postgresql"] = facts["postgresql"]
            doc["mongodb"] = facts["mongodb"]
            doc["sale_counts"] = entry["sale_counts"]
            doc["fraction"] = fraction
            doc["connections"] = connect.CONNECTION_SETTINGS
            doc["identities"] = ids
            doc["cpu_count"] = os.cpu_count()
            doc.setdefault("utc_start", started)
            doc["utc_end"] = None
            doc.setdefault("invocations", []).append(entry)
            res.save_env(doc)

            log(f"invocation {inv} ({kind}) into {res.dir}; warm cache only")
            if kind == "throughput":
                rows = run_throughput(
                    inv,
                    selected,
                    warmup_s=args.throughput_warmup,
                    seconds=args.throughput_seconds,
                    log=log,
                )
                write_rows(res.throughput, THROUGHPUT_COLUMNS, rows)
            else:
                summary = []
                for case in selected:
                    runs = args.runs if args.runs is not None else case.runs
                    warmup = args.warmup if args.warmup is not None else case.warmup
                    timer = time_writes if case.writes else time_reads
                    summary += timer(case, h, inv, res, log, runs, warmup)
                write_summary(res.summary, summary)

            doc = res.env_doc()
            finished = utc_now()
            doc["utc_end"] = finished
            for i in doc["invocations"]:
                if i["id"] == inv:
                    i["utc_end"] = finished
            res.save_env(doc)
        log(f"done: {inv}")
        return 0
    finally:
        for c in clients:
            c.close()


def _json_env(name: str):
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw  # run.sh passes plain text such as a list of image ids


def _int_env(name: str):
    raw = os.environ.get(name)
    return int(raw) if raw and raw.isdigit() else None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except Refusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
