"""Behind the scenes (S12, UC8 step 4): what the last page ran in each database, and the benchmark results.

In demo mode only, call_repo hands each repo a recording connection or database (below). The recorders keep the
statements and commands as the benchmark's capture does (bench/capture.py), so /demo can show them and, on
request, EXPLAIN them with the benchmark's own helpers. A recorder measures the time it spends on its own
bookkeeping and call_repo subtracts it, so the demo bar's timings stay the repo's.

The last page's calls are kept in memory per session (the CSRF token is the key), for at most SESSIONS sessions;
the app runs one worker, so nothing needs sharing. Nothing is recorded outside demo mode.
"""

from __future__ import annotations

import csv
import itertools
import json
import statistics
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from bson.decimal128 import Decimal128
from psycopg import sql

from bench.capture import _SELECT, PgStatement, RecordingCursor

SESSIONS = 200
RESULTS = Path(__file__).resolve().parents[2] / "bench" / "results"
CHARTS = {
    "latency_by_case": "Median latency by case",
    "index_on_off": "With and without the indexes",
    "throughput": "Throughput under concurrent clients",
    "scaling": "Latency as the data grows",
}
_READS = ("find", "count_documents", "aggregate")


# ------------------------------------------------------------------------------------------------ recorders


class PgRecorder:
    """An idle autocommit psycopg connection that records each statement (and its params) before running it."""

    def __init__(self, conn):
        self._conn = conn
        self.statements: list[PgStatement] = []
        self.overhead_ns = 0

    def _record(self, query, params) -> None:
        started = time.perf_counter_ns()
        text = query.as_string(self._conn) if isinstance(query, sql.Composable) else str(query)
        kept = dict(params) if isinstance(params, dict) else None if params is None else list(params)
        self.statements.append(PgStatement(text, query, kept, select=bool(_SELECT.match(text))))
        self.overhead_ns += time.perf_counter_ns() - started

    def execute(self, query, params=None, **kwargs):
        self._record(query, params)
        return self._conn.execute(query, params, **kwargs)

    def cursor(self, *args, **kwargs):
        return _PgCursor(self._conn.cursor(*args, **kwargs), self)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _PgCursor:
    def __init__(self, cursor, recorder: PgRecorder):
        self._cursor, self._recorder = cursor, recorder

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, *exc):
        return self._cursor.__exit__(*exc)

    def execute(self, query, params=None, **kwargs):
        self._recorder._record(query, params)
        return self._cursor.execute(query, params, **kwargs)

    def executemany(self, query, params_seq, **kwargs):
        rows = list(params_seq)
        self._recorder._record(query, rows[0] if rows else None)
        return self._cursor.executemany(query, rows, **kwargs)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class MongoRecorder:
    """A Database whose collections record each read (as bench/capture.py does) and each write, then run it.

    Unlike the benchmark's proxy it passes `client`, `name` and `command` through, so transactions still work.
    """

    def __init__(self, db):
        self._db = db
        self.calls: list[dict] = []
        self.overhead_ns = 0

    def __getitem__(self, name: str) -> _MongoCollection:
        return _MongoCollection(self._db[name], self)

    def __getattr__(self, name: str):
        if name in ("client", "name", "command") or name.startswith("_"):
            return getattr(self._db, name)
        return self[name]


class _MongoCollection:
    def __init__(self, coll, recorder: MongoRecorder):
        self._coll, self._recorder = coll, recorder

    def _add(self, record: dict) -> dict:
        started = time.perf_counter_ns()
        self._recorder.calls.append(record)
        self._recorder.overhead_ns += time.perf_counter_ns() - started
        return record

    def find(self, filter=None, projection=None, **kwargs):
        record = self._add({"kind": "find", "collection": self._coll.name, "filter": filter or {},
                            "projection": projection})  # fmt: skip
        return RecordingCursor(self._coll.find(filter, projection, **kwargs), record)

    def find_one(self, filter=None, projection=None, **kwargs):
        self._add({"kind": "find", "collection": self._coll.name, "filter": filter or {}, "projection": projection,
                   "limit": 1})  # fmt: skip
        return self._coll.find_one(filter, projection, **kwargs)

    def count_documents(self, filter, **kwargs):
        self._add({"kind": "count_documents", "collection": self._coll.name, "filter": filter})
        return self._coll.count_documents(filter, **kwargs)

    def aggregate(self, pipeline, **kwargs):
        options = {k: v for k, v in kwargs.items() if k != "session"}
        self._add({"kind": "aggregate", "collection": self._coll.name, "pipeline": list(pipeline), "options": options})
        return self._coll.aggregate(pipeline, **kwargs)

    def __getattr__(self, name: str):
        target = getattr(self._coll, name)
        if not callable(target) or name.startswith("_"):
            return target

        def write(*args, **kwargs):  # update_one, insert_one, delete_many, find_one_and_update, ...
            self._add({"kind": name, "collection": self._coll.name, "args": list(args[:2])})
            return target(*args, **kwargs)

        return write


# ------------------------------------------------------------------------------------------------ the store


@dataclass
class Call:
    label: str
    mode: str
    ms: float
    pg: list[PgStatement] = field(default_factory=list)
    mongo: list[dict] = field(default_factory=list)


@dataclass
class Page:
    path: str
    request: int  # the number of the request that recorded it, so a new request starts a new page
    calls: list[Call] = field(default_factory=list)


class Captures:
    """The last page's recorded calls per session, oldest sessions dropped first."""

    def __init__(self, size: int = SESSIONS):
        self._pages: OrderedDict[str, Page] = OrderedDict()
        self._size = size
        self._ids = itertools.count(1)

    def new_id(self) -> int:
        return next(self._ids)

    def add(self, session: str, request_id: int, path: str, call: Call) -> None:
        page = self._pages.get(session)
        if page is None or page.request != request_id:
            page = Page(path, request_id)
            self._pages[session] = page
        page.calls.append(call)
        self._pages.move_to_end(session)
        while len(self._pages) > self._size:
            self._pages.popitem(last=False)

    def last(self, session: str) -> Page | None:
        return self._pages.get(session)


def _plain(value: Any):
    """JSON-friendly text for MongoDB values shown on the page."""
    if isinstance(value, Decimal128):
        return {"$numberDecimal": str(value)}
    if isinstance(value, datetime | date):
        return {"$date": value.isoformat()}
    return str(value)


def show_mongo(call: dict) -> str:
    shown = {k: v for k, v in call.items() if k not in ("kind", "collection")}
    return f"db.{call['collection']}.{call['kind']}(\n{json.dumps(shown, indent=2, default=_plain)}\n)"


def summary_pg(text: str) -> str:
    """One line for a folded statement: its first words and its length."""
    words = " ".join(text.split())
    lines = len(text.strip().splitlines())
    return f"{words[:70]}{'...' if len(words) > 70 else ''} ({lines} line{'' if lines == 1 else 's'})"


def summary_mongo(call: dict) -> str:
    if call["kind"] == "aggregate":
        n = len(call["pipeline"])
        return f"aggregate on {call['collection']}, {n} stage{'' if n == 1 else 's'}"
    return f"{call['kind']} on {call['collection']}"


def show_params(params) -> str:
    return "" if params is None else json.dumps(params, indent=2, default=str)


def is_read(call: dict) -> bool:
    return call["kind"] in _READS


# ------------------------------------------------------------------------------------------------ benchmark


@dataclass(frozen=True)
class CaseResult:
    case: str
    pair: str
    pg_ms: float | None
    mongo_ms: float | None

    @property
    def faster(self) -> str | None:
        if self.pg_ms is None or self.mongo_ms is None:
            return None
        if abs(self.pg_ms - self.mongo_ms) <= 0.1 * max(self.pg_ms, self.mongo_ms):
            return "within 10%"
        return "PostgreSQL" if self.pg_ms < self.mongo_ms else "MongoDB"


@dataclass(frozen=True)
class Benchmark:
    name: str
    folder: Path
    charts: list[tuple[str, str]]  # (file stem, title) of the charts present
    cases: list[CaseResult]


def latest_benchmark(root: Path = RESULTS) -> Benchmark | None:
    """The newest results folder (by name) with a full-data summary, as the charts and RESULTS.md report it:
    per case and database, the median of the parameter sets' medians (the "chart value")."""
    runs = sorted((p for p in root.glob("*") if (p / "f100" / "summary.csv").is_file()), key=lambda p: p.name)
    if not runs:
        return None
    folder = runs[-1]
    medians: dict[tuple[str, str], dict[str, list[float]]] = {}
    with (folder / "f100" / "summary.csv").open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["part"] != "call":
                continue
            medians.setdefault((row["case"], row["pair"]), {}).setdefault(row["db"], []).append(float(row["median_ms"]))
    cases = [
        CaseResult(
            case,
            pair,
            statistics.median(v["pg"]) if v.get("pg") else None,
            statistics.median(v["mongo"]) if v.get("mongo") else None,
        )
        for (case, pair), v in medians.items()
    ]
    charts = [(stem, title) for stem, title in CHARTS.items() if (folder / f"{stem}.svg").is_file()]
    return Benchmark(folder.name, folder, charts, cases)
