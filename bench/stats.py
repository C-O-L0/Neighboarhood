"""Summary statistics and the CSV writers for the benchmark results.

Timings are floats in milliseconds. Money never passes through here.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

SUMMARY_COLUMNS = [
    "invocation", "case", "pair", "db", "param_set", "part", "n", "rows",
    "median_ms", "p95_ms", "min_ms", "max_ms", "server_ms", "planning_ms", "execution_ms",
]  # fmt: skip

THROUGHPUT_COLUMNS = ["invocation", "case", "db", "workers", "seconds", "calls", "calls_per_s", "median_ms", "p95_ms"]


def median(values: Sequence[float]) -> float:
    """The usual median: the middle value, or the mean of the two middle values when n is even."""
    if not values:
        raise ValueError("median of no values")
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def percentile_nearest_rank(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile: the ceil(p/100 * n)-th smallest value (rank 1 for the smallest)."""
    if not values:
        raise ValueError("percentile of no values")
    if not 0 < p <= 100:
        raise ValueError("p must be in (0, 100]")
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def p95(values: Sequence[float]) -> float:
    return percentile_nearest_rank(values, 95)


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def write_rows(path: Path, columns: Sequence[str], rows: Iterable[Mapping]) -> int:
    """Append rows to a CSV, writing the header first when the file is new or empty. Returns the rows written."""
    new = not path.exists() or path.stat().st_size == 0
    count = 0
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(columns)
        for row in rows:
            extra = set(row) - set(columns)
            if extra:
                raise ValueError(f"unknown column(s) {sorted(extra)}")
            w.writerow([_cell(row.get(c)) for c in columns])
            count += 1
    return count


def summarise(
    invocation: str,
    case: str,
    pair: str,
    db: str,
    param_set: str,
    timings_ms: Sequence[float],
    rows: int | None,
    part: str = "call",
    server: Mapping | None = None,
) -> dict:
    """One summary.csv row from the timed runs of one case, database and parameter set."""
    server = server or {}
    return {
        "invocation": invocation,
        "case": case,
        "pair": pair,
        "db": db,
        "param_set": param_set,
        "part": part,
        "n": len(timings_ms),
        "rows": rows,
        "median_ms": median(timings_ms),
        "p95_ms": p95(timings_ms),
        "min_ms": min(timings_ms),
        "max_ms": max(timings_ms),
        "server_ms": server.get("server_ms"),
        "planning_ms": server.get("planning_ms"),
        "execution_ms": server.get("execution_ms"),
    }


def write_summary(path: Path, rows: Iterable[Mapping]) -> int:
    return write_rows(path, SUMMARY_COLUMNS, rows)
