"""Capture what a repo runs, then EXPLAIN exactly that (task 4.1a).

A recording psycopg connection wrapper and a recording MongoDB `Database` proxy are used for one separate,
untimed call per parameter set. The timed runs use bare connections, so this overhead never enters a client timing.

Each captured PostgreSQL SELECT (or WITH ... SELECT) is run once as EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) with the
params it was executed with, inside a read-only transaction that is rolled back. Session and transaction control
(SET TRANSACTION and the like) is recorded but not explained. Each captured MongoDB find, count_documents and
aggregate is run once through the explain command with verbosity "executionStats".

Server time per call:
  PostgreSQL  Planning Time + Execution Time of the explained statement (both also kept separately).
  MongoDB     the top-level executionStats.executionTimeMillis when present, otherwise, for an aggregation
              pipeline, the largest executionTimeMillisEstimate among its stages (cumulative, so the last stage's).
  A repo that issues several statements: the sum over its explained statements, in both databases.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from pymongo.database import Database

SERVER_TIME_RULE = (
    "Server time per call: PostgreSQL is Planning Time + Execution Time from EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON); "
    "MongoDB is executionStats.executionTimeMillis when the explain output has it, otherwise (aggregation "
    "pipelines) the largest executionTimeMillisEstimate among the stages, which is cumulative so it is the last "
    "stage's. MongoDB reports whole milliseconds, so a sub-millisecond call reads as 0 or 1. A repo call that runs "
    "several statements is the sum over its explained statements, in both databases. "
    "Write cases have client timing only."
)

_SELECT = re.compile(r"\s*(select|with)\b", re.IGNORECASE)


# ------------------------------------------------------------------------------------------------ PostgreSQL


@dataclass
class PgStatement:
    """One statement a repo executed: its text, the params it was executed with, and (later) its explain."""

    text: str
    query: Any = field(repr=False)
    params: Any
    select: bool
    explained: bool = False
    explain_params: Any = None
    plan: dict | None = None

    @property
    def planning_ms(self) -> float | None:
        return None if self.plan is None else float(self.plan["Planning Time"])

    @property
    def execution_ms(self) -> float | None:
        return None if self.plan is None else float(self.plan["Execution Time"])


class RecordingConnection:
    """Wraps an idle autocommit psycopg connection and records every statement passed to execute()."""

    def __init__(self, conn: psycopg.Connection):
        self._conn = conn
        self.statements: list[PgStatement] = []

    def execute(self, query, params=None, **kwargs):
        text = query.as_string(self._conn) if isinstance(query, sql.Composable) else str(query)
        self.statements.append(PgStatement(text, query, _copy_params(params), select=bool(_SELECT.match(text))))
        return self._conn.execute(query, params, **kwargs)

    def transaction(self, *args, **kwargs):
        return self._conn.transaction(*args, **kwargs)

    def cursor(self, *args, **kwargs):
        # A cursor would let a statement slip past the recorder, so refuse it instead of missing it.
        raise NotImplementedError("RecordingConnection records execute() only")

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _copy_params(params):
    if isinstance(params, dict):
        return dict(params)
    if params is None:
        return None
    return list(params)


def explain_pg(conn: psycopg.Connection, statements: list[PgStatement], in_transaction: bool = False) -> None:
    """EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) every captured SELECT with its own params, rolling each back.

    in_transaction=True is for a caller that already holds an open transaction (the index on/off arms, task 4.1b):
    the EXPLAINs then run inside it and the caller's rollback ends them.
    """
    prefix = sql.SQL("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ")
    for st in statements:
        if not st.select:
            continue
        body = st.query if isinstance(st.query, sql.Composable) else sql.SQL(st.query)
        if in_transaction:
            row = conn.execute(prefix + body, st.params).fetchone()
            st.plan = row[0][0]
            st.explained = True
            st.explain_params = _copy_params(st.params)
            continue
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            row = conn.execute(prefix + body, st.params).fetchone()
            st.plan = row[0][0]
            st.explained = True
            st.explain_params = _copy_params(st.params)
            raise psycopg.Rollback


def pg_server_time(statements: list[PgStatement]) -> dict:
    explained = [s for s in statements if s.explained]
    if not explained:
        return {}
    planning = sum(s.planning_ms for s in explained)
    execution = sum(s.execution_ms for s in explained)
    return {"server_ms": planning + execution, "planning_ms": planning, "execution_ms": execution}


def render_pg_plan(plan: dict) -> str:
    lines: list[str] = []

    def walk(node: dict, depth: int) -> None:
        label = node["Node Type"]
        if "Index Name" in node:
            label += f" using {node['Index Name']}"
        if "Relation Name" in node:
            label += f" on {node['Relation Name']}"
        stats = (
            f"actual time={node.get('Actual Startup Time')}..{node.get('Actual Total Time')} "
            f"rows={node.get('Actual Rows')} loops={node.get('Actual Loops')}"
        )
        buffers = ""
        if "Shared Hit Blocks" in node:
            buffers = f" buffers: shared hit={node['Shared Hit Blocks']} read={node.get('Shared Read Blocks', 0)}"
        lines.append(f"{'  ' * depth}{'-> ' if depth else ''}{label} ({stats}){buffers}")
        for child in node.get("Plans", []):
            walk(child, depth + 1)

    walk(plan["Plan"], 0)
    lines.append(f"Planning Time: {plan['Planning Time']} ms")
    lines.append(f"Execution Time: {plan['Execution Time']} ms")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------------- MongoDB


class RecordingCursor:
    """Follows a find() cursor so the chained sort, skip and limit land in the recorded call."""

    def __init__(self, cursor, record: dict):
        self._cursor = cursor
        self._record = record

    def sort(self, key_or_list, direction=None):
        spec = key_or_list if direction is None else [(key_or_list, direction)]
        self._record["sort"] = [[k, d] for k, d in spec]
        self._cursor = self._cursor.sort(key_or_list, direction)
        return self

    def skip(self, n: int):
        self._record["skip"] = n
        self._cursor = self._cursor.skip(n)
        return self

    def limit(self, n: int):
        self._record["limit"] = n
        self._cursor = self._cursor.limit(n)
        return self

    def __iter__(self):
        return iter(self._cursor)

    def __next__(self):
        return next(self._cursor)


class RecordingCollection:
    def __init__(self, coll, calls: list[dict]):
        self._coll = coll
        self._calls = calls

    def find(self, filter=None, projection=None, **kwargs):
        record = {"kind": "find", "collection": self._coll.name, "filter": filter or {}, "projection": projection}
        self._calls.append(record)
        return RecordingCursor(self._coll.find(filter, projection, **kwargs), record)

    def find_one(self, filter=None, projection=None, **kwargs):
        record = {
            "kind": "find", "collection": self._coll.name, "filter": filter or {}, "projection": projection,
            "limit": 1,
        }  # fmt: skip
        self._calls.append(record)
        return self._coll.find_one(filter, projection, **kwargs)

    def count_documents(self, filter, **kwargs):
        self._calls.append({"kind": "count_documents", "collection": self._coll.name, "filter": filter})
        return self._coll.count_documents(filter, **kwargs)

    def aggregate(self, pipeline, **kwargs):
        self._calls.append(
            {"kind": "aggregate", "collection": self._coll.name, "pipeline": list(pipeline), "options": dict(kwargs)}
        )
        return self._coll.aggregate(pipeline, **kwargs)

    def __getattr__(self, name):
        raise AttributeError(f"RecordingCollection does not record {name!r}")


class RecordingDatabase:
    """Stands in for a pymongo Database for read repos and records find, count_documents and aggregate."""

    def __init__(self, db: Database):
        self._db = db
        self.calls: list[dict] = []

    def __getitem__(self, name: str) -> RecordingCollection:
        return RecordingCollection(self._db[name], self.calls)

    def __getattr__(self, name: str) -> RecordingCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


def explain_command(call: dict) -> dict:
    """The command document that the explain command wraps for a recorded call."""
    kind = call["kind"]
    if kind == "find":
        cmd: dict = {"find": call["collection"], "filter": call["filter"]}
        if call.get("projection"):
            cmd["projection"] = call["projection"]
        if call.get("sort"):
            cmd["sort"] = {k: d for k, d in call["sort"]}
        if call.get("skip"):
            cmd["skip"] = call["skip"]
        if call.get("limit"):
            cmd["limit"] = call["limit"]
        return cmd
    if kind == "count_documents":
        pipeline = [{"$match": call["filter"]}, {"$group": {"_id": 1, "n": {"$sum": 1}}}]
        return {"aggregate": call["collection"], "pipeline": pipeline, "cursor": {}}
    cmd = {"aggregate": call["collection"], "pipeline": call["pipeline"], "cursor": {}}
    cmd.update({k: v for k, v in call.get("options", {}).items() if k == "allowDiskUse"})
    return cmd


def explain_mongo(db: Database, calls: list[dict]) -> list[dict]:
    """Run each recorded call once through explain (executionStats). Returns one result per call, in order."""
    results = []
    for call in calls:
        out = db.command({"explain": explain_command(call), "verbosity": "executionStats"})
        out.pop("ok", None)
        out.pop("$clusterTime", None)
        out.pop("operationTime", None)
        results.append(out)
    return results


def mongo_call_time_ms(explain: dict) -> float | None:
    stats = explain.get("executionStats")
    if isinstance(stats, dict) and "executionTimeMillis" in stats:
        return float(stats["executionTimeMillis"])
    estimates = [
        float(stage["executionTimeMillisEstimate"])
        for stage in explain.get("stages", [])
        if isinstance(stage, dict) and "executionTimeMillisEstimate" in stage
    ]
    return max(estimates) if estimates else None


def mongo_server_time(explains: list[dict]) -> dict:
    times = [mongo_call_time_ms(e) for e in explains]
    if not explains or any(t is None for t in times):
        return {}
    total = sum(times)
    return {"server_ms": total, "planning_ms": None, "execution_ms": total}


def render_mongo_explain(call: dict, explain: dict) -> str:
    lines = [f"{call['kind']} on {call['collection']}: {mongo_call_time_ms(explain)} ms"]
    planner = explain.get("queryPlanner", {})
    plan = planner.get("winningPlan")
    if plan:
        lines.append("winning plan:")
        node: dict | None = plan
        depth = 1
        while node:
            extra = f" index={node['indexName']}" if "indexName" in node else ""
            lines.append(f"{'  ' * depth}{node.get('stage', node.get('queryPlan', {}).get('stage', '?'))}{extra}")
            node = node.get("inputStage") or (node.get("queryPlan") if "queryPlan" in node else None)
            depth += 1
    stats = explain.get("executionStats")
    if isinstance(stats, dict):
        lines.append(
            f"executionStats: nReturned={stats.get('nReturned')} keysExamined={stats.get('totalKeysExamined')} "
            f"docsExamined={stats.get('totalDocsExamined')} time={stats.get('executionTimeMillis')} ms"
        )
    for stage in explain.get("stages", []):
        if isinstance(stage, dict):
            name = next((k for k in stage if k.startswith("$")), "?")
            lines.append(
                f"stage {name}: nReturned={stage.get('nReturned')} "
                f"estimate={stage.get('executionTimeMillisEstimate')} ms"
            )
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ running


def capture_pg(prepared: Callable, conn: psycopg.Connection) -> tuple[list[PgStatement], Any]:
    recorder = RecordingConnection(conn)
    result = prepared(recorder)
    return recorder.statements, result


def capture_mongo(prepared: Callable, db: Database) -> tuple[list[dict], Any]:
    proxy = RecordingDatabase(db)
    result = prepared(proxy)
    return proxy.calls, result


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def save_pg_explain(directory: Path, case: str, param_set: str, statements: list[PgStatement]) -> None:
    base = directory / _safe(case)
    base.mkdir(parents=True, exist_ok=True)
    doc = [
        {
            "n": i,
            "select": s.select,
            "text": s.text,
            "params": s.params,
            "explained": s.explained,
            "explain_params": s.explain_params,
            "plan": s.plan,
        }
        for i, s in enumerate(statements, 1)
    ]
    (base / f"pg-{_safe(param_set)}.json").write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    text = []
    for i, s in enumerate(statements, 1):
        text.append(f"-- statement {i} ({'explained' if s.explained else 'recorded, not explained'})")
        text.append(s.text.strip())
        text.append(f"-- params: {json.dumps(s.params, default=str)}")
        if s.plan is not None:
            text.append(render_pg_plan(s.plan))
        text.append("")
    (base / f"pg-{_safe(param_set)}.txt").write_text("\n".join(text), encoding="utf-8")


def save_mongo_explain(directory: Path, case: str, param_set: str, calls: list[dict], explains: list[dict]) -> None:
    base = directory / _safe(case)
    base.mkdir(parents=True, exist_ok=True)
    doc = [{"n": i, "call": c, "explain": e} for i, (c, e) in enumerate(zip(calls, explains, strict=True), 1)]
    (base / f"mongo-{_safe(param_set)}.json").write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    text = []
    for i, (c, e) in enumerate(zip(calls, explains, strict=True), 1):
        text.append(f"-- call {i}")
        text.append(render_mongo_explain(c, e))
        text.append("")
    (base / f"mongo-{_safe(param_set)}.txt").write_text("\n".join(text), encoding="utf-8")
