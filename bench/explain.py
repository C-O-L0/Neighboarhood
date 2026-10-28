"""Index on and off (task 4.1b): what each database does for a case with and without its index.

    python -m bench.explain [--cases id,...] [--label NAME] [--runs N --warmup N]

Cases (the key shape is the same in both databases):
  search_price_sort  PostgreSQL (flat_type_id, resale_price, txn_id)  MongoDB {flat_type, resale_price, _id}
  search_type_month  PostgreSQL (flat_type_id, txn_month)             MongoDB {flat_type, txn_month}
  median_exact       PostgreSQL (flat_type_id, txn_month)             MongoDB {flat_type, txn_month}

MongoDB also has {town, flat_type, txn_month}, which PostgreSQL has no equivalent of; it favours MongoDB in
search_filtered, which is not an on/off case here.

Both arms of both databases are timed by this module with one method, and each database is compared only with itself
(on against off), never across databases in this table.

PostgreSQL (runs as the schema owner): the case's statements are captured once through the repo (the 4.1a capture).
Each arm is one owner transaction: (off arm only) DROP INDEX, then the warm-up and the timed runs of the captured
SELECTs with their params, then EXPLAIN (ANALYZE, BUFFERS) of each, then ROLLBACK. The repo is not called inside the
transaction (its SET TRANSACTION would fail there). DDL is transactional, so the index cannot stay dropped; the
pg_get_indexdef of every index is read before and after and compared.

MongoDB (runs as the etl user): both arms call the repo. The collection's index_information() is captured, the index
is dropped, the runs and explain("executionStats") are made, and the index is recreated in a finally clause from the
captured definition; index_information() afterwards must equal the capture. This module starts with the idempotent
create_indexes(INDEXES), so an interrupted run is repaired by the next start.

Output in bench/results/<label>/: index_on_off.csv and the plans in explain/<case>/ (index-on-* and index-off-*).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import psycopg
from psycopg import sql

from bench import capture, connect
from bench.cases import BenchCase, load_cases
from bench.run import (
    LABEL_PATTERN,
    README,
    RESULTS_ROOT,
    Refusal,
    Results,
    count_rows,
    ensure_indexes,
    new_invocation_id,
    utc_now,
)
from bench.stats import median, p95, write_rows
from db.mongo.indexes import create_indexes

INDEX_COLUMNS = [
    "invocation", "case", "db", "param_set", "arm", "index", "n", "rows",
    "median_ms", "p95_ms", "min_ms", "max_ms", "server_ms", "planning_ms", "execution_ms",
    "plan", "index_in_plan",
]  # fmt: skip

PG_SCHEMA = "public"


@dataclass(frozen=True)
class IndexCase:
    pg_index: str
    mongo_collection: str
    mongo_index: str


# Names from db/postgres/004_indexes.sql and db/mongo/indexes.py.
INDEX_CASES: dict[str, IndexCase] = {
    "search_price_sort": IndexCase("resale_transaction_type_price_idx", "resale_transactions", "type_price"),
    "search_type_month": IndexCase("resale_transaction_type_month_idx", "resale_transactions", "type_month"),
    "median_exact": IndexCase("resale_transaction_type_month_idx", "resale_transactions", "type_month"),
}

# hook(db, arm) is called inside the timed step of each arm, after the index is dropped; tests raise from it.
Hook = Callable[[str, str], None]


class ForcedFailure(RuntimeError):
    """Raised by the --force-failure test hook."""


def force_failure(db: str, arm: str) -> None:
    if arm == "off":
        raise ForcedFailure(f"forced failure in the timed step of the {db} off arm")


# ------------------------------------------------------------------------------------------- plan summaries


def pg_plan_signature(plan: dict) -> str:
    """Node types (and index names) of a plan in pre-order, for telling two plans apart."""
    parts: list[str] = []

    def walk(node: dict) -> None:
        parts.append(node["Node Type"] + (f" {node['Index Name']}" if "Index Name" in node else ""))
        for child in node.get("Plans", []):
            walk(child)

    walk(plan["Plan"])
    return " > ".join(parts)


_SKIP_KEYS = {"slotBasedPlan", "filter", "parsedQuery", "indexBounds", "keyPattern", "rejectedPlans"}


def mongo_plan_signature(explain: dict) -> str:
    """Winning-plan stages (and index names) of an explain output, in order."""
    parts: list[str] = []

    def stages(node) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("stage"), str):
                parts.append(node["stage"] + (f" {node['indexName']}" if "indexName" in node else ""))
            for key, value in node.items():
                if key not in _SKIP_KEYS and isinstance(value, dict | list):
                    stages(value)
        elif isinstance(node, list):
            for value in node:
                stages(value)

    def planners(node):
        if isinstance(node, dict):
            query_planner = node.get("queryPlanner")
            if isinstance(query_planner, dict) and "winningPlan" in query_planner:
                yield query_planner["winningPlan"]
            for key, value in node.items():
                if key != "queryPlanner":
                    yield from planners(value)
        elif isinstance(node, list):
            for value in node:
                yield from planners(value)

    for winning in planners(explain):
        stages(winning)
    return " > ".join(parts)


# ------------------------------------------------------------------------------------------------ helpers


def _timed(call: Callable[[], int], runs: int, warmup: int) -> tuple[list[float], int]:
    import time

    for _ in range(warmup):
        call()
    timings: list[float] = []
    rows = 0
    for _ in range(runs):
        t0 = time.perf_counter_ns()
        rows = call()
        timings.append((time.perf_counter_ns() - t0) / 1e6)
    return timings, rows


def plan_uses_index(signature: str, index_name: str) -> bool:
    """Whether a plan signature names the index as a whole token (type_month is not town_type_month)."""
    for statement in signature.split(" | "):
        for node in statement.split(" > "):
            parts = node.split(" ")
            if len(parts) > 1 and parts[-1] == index_name:
                return True
    return False


def _row(inv, case_id, db, label, arm, index, timings, rows, server, plan, index_name) -> dict:
    return {
        "invocation": inv,
        "case": case_id,
        "db": db,
        "param_set": label,
        "arm": arm,
        "index": index,
        "n": len(timings),
        "rows": rows,
        "median_ms": median(timings),
        "p95_ms": p95(timings),
        "min_ms": min(timings),
        "max_ms": max(timings),
        "server_ms": server.get("server_ms"),
        "planning_ms": server.get("planning_ms"),
        "execution_ms": server.get("execution_ms"),
        "plan": plan,
        "index_in_plan": plan_uses_index(plan, index_name),
    }


def pg_index_defs(conn: psycopg.Connection) -> dict[str, str]:
    """pg_get_indexdef of every index in the public schema, by index name."""
    rows = conn.execute(
        "SELECT c.relname, pg_get_indexdef(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE c.relkind = 'i' AND n.nspname = %s ORDER BY c.relname",
        [PG_SCHEMA],
    ).fetchall()
    return {name: definition for name, definition in rows}


# ------------------------------------------------------------------------------------------------ PostgreSQL


def pg_arm(conn, items, index: str, off: bool, runs: int, warmup: int, hook: Hook | None, out: dict) -> None:
    """One owner transaction: optional DROP INDEX, timed runs, EXPLAIN, ROLLBACK.

    items is [(label, captured statements)]. Fills `out` with {label: (timings, rows, statements)}, each statement
    with its plan. The transaction always ends in a rollback (the Rollback below, or the error that is raised).
    """
    arm = "off" if off else "on"
    with conn.transaction():
        if off:
            conn.execute(sql.SQL("DROP INDEX {}").format(sql.Identifier(PG_SCHEMA, index)))
            gone = conn.execute(
                "SELECT count(*) FROM pg_indexes WHERE schemaname = %s AND indexname = %s", [PG_SCHEMA, index]
            ).fetchone()[0]
            if gone:
                raise RuntimeError(f"{index} is still present after DROP INDEX")
        if hook:
            hook("pg", arm)
        for label, captured in items:
            statements = [replace(s, plan=None, explained=False, explain_params=None) for s in captured]
            selects = [s for s in statements if s.select]

            def call(selects=selects) -> int:
                rows = 0
                for st in selects:
                    rows = len(conn.execute(st.query, st.params).fetchall())
                return rows  # the last statement is the page (or the one scalar) the case returns

            timings, rows = _timed(call, runs, warmup)
            out[label] = (timings, rows, statements)
        for _, _, statements in out.values():
            capture.explain_pg(conn, statements, in_transaction=True)
        raise psycopg.Rollback


def run_pg_case(owner, case: BenchCase, spec: IndexCase, inv, res, log, runs, warmup, hook, rows_out: list) -> None:
    items = []
    for ps in case.params:
        statements, _ = capture.capture_pg(case.pg.prepare(ps.args), owner)
        items.append((ps.label, statements))
    before = pg_index_defs(owner)
    if spec.pg_index not in before:
        raise Refusal(f"PostgreSQL index {spec.pg_index} does not exist")
    results: dict[str, dict] = {"on": {}, "off": {}}
    for arm in ("on", "off"):
        try:
            pg_arm(owner, items, spec.pg_index, arm == "off", runs, warmup, hook, results[arm])
        finally:
            if pg_index_defs(owner) != before:
                raise RuntimeError(f"pg_get_indexdef changed after the {arm} arm of {case.id}")
    for label, _ in items:
        signatures = {}
        for arm in ("on", "off"):
            timings, rows, statements = results[arm][label]
            signature = " | ".join(pg_plan_signature(s.plan) for s in statements if s.plan)
            signatures[arm] = signature
            capture.save_pg_explain(res.explain, case.id, f"index-{arm}-{label}", statements)
            server = capture.pg_server_time(statements)
            rows_out.append(
                _row(inv, case.id, "pg", label, arm, spec.pg_index, timings, rows, server, signature, spec.pg_index)
            )
        log(
            f"{case.id} [{label}] pg: on {_fmt(results['on'][label][0])} | off {_fmt(results['off'][label][0])}"
            f" | plan changed: {signatures['on'] != signatures['off']}"
        )


# -------------------------------------------------------------------------------------------------- MongoDB


def _restore_options(info: dict) -> dict:
    return {k: v for k, v in info.items() if k not in ("key", "v", "ns")}


def mongo_arm(db, case: BenchCase, items, runs: int, warmup: int, hook: Hook | None, arm: str) -> dict:
    """Timed repo calls on the etl connection, then explain("executionStats") of the captured calls."""
    if hook:
        hook("mongo", arm)
    out = {}
    for label, prepared, calls in items:
        timings, rows = _timed(lambda prepared=prepared: count_rows(prepared(db)), runs, warmup)
        explains = capture.explain_mongo(db, calls)
        out[label] = (timings, rows, calls, explains)
    return out


def run_mongo_case(etl_db, case: BenchCase, spec: IndexCase, inv, res, log, runs, warmup, hook, rows_out: list) -> None:
    items = []
    for ps in case.params:
        prepared = case.mongo.prepare(ps.args)
        calls, _ = capture.capture_mongo(prepared, etl_db)
        items.append((ps.label, prepared, calls))
    coll = etl_db[spec.mongo_collection]
    before = coll.index_information()
    if spec.mongo_index not in before:
        raise Refusal(f"MongoDB index {spec.mongo_index} does not exist")
    definition = before[spec.mongo_index]
    results: dict[str, dict] = {}
    try:
        results["on"] = mongo_arm(etl_db, case, items, runs, warmup, hook, "on")
        coll.drop_index(spec.mongo_index)
        results["off"] = mongo_arm(etl_db, case, items, runs, warmup, hook, "off")
    finally:
        if spec.mongo_index not in coll.index_information():
            coll.create_index(definition["key"], name=spec.mongo_index, **_restore_options(definition))
        after = coll.index_information()
        if after != before:
            raise RuntimeError(f"index_information() of {spec.mongo_collection} changed: {before} became {after}")

    for label, _, _ in items:
        signatures = {}
        for name in ("on", "off"):
            timings, rows, calls, explains = results[name][label]
            signature = " | ".join(mongo_plan_signature(e) for e in explains)
            signatures[name] = signature
            capture.save_mongo_explain(res.explain, case.id, f"index-{name}-{label}", calls, explains)
            server = capture.mongo_server_time(explains)
            index = spec.mongo_index
            rows_out.append(_row(inv, case.id, "mongo", label, name, index, timings, rows, server, signature, index))
        log(
            f"{case.id} [{label}] mongo: on {_fmt(results['on'][label][0])} | off {_fmt(results['off'][label][0])}"
            f" | plan changed: {signatures['on'] != signatures['off']}"
        )


def _fmt(timings: list[float]) -> str:
    return f"median {median(timings):.2f} ms p95 {p95(timings):.2f} ms"


# --------------------------------------------------------------------------------------------------- main


def run_cases(owner, etl_db, cases: list[BenchCase], inv, res, log, runs, warmup, hook=None) -> list[dict]:
    """Run each case on both databases; the index of a failed step is restored before the error propagates."""
    rows: list[dict] = []
    for case in cases:
        spec = INDEX_CASES[case.id]
        r = runs if runs is not None else case.runs
        w = warmup if warmup is not None else case.warmup
        run_pg_case(owner, case, spec, inv, res, log, r, w, hook, rows)
        run_mongo_case(etl_db, case, spec, inv, res, log, r, w, hook, rows)
    return rows


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m bench.explain", description=__doc__.splitlines()[0])
    ap.add_argument("--cases", help=f"comma-separated case ids (default: {', '.join(INDEX_CASES)})")
    ap.add_argument("--label", help="results folder name under bench/results (default: UTC timestamp)")
    ap.add_argument("--runs", type=int, help="timed runs for every case (default: per case, 50)")
    ap.add_argument("--warmup", type=int, help="warm-up calls for every case (default: per case, 5)")
    ap.add_argument("--force-failure", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--results-root", type=Path, default=RESULTS_ROOT, help=argparse.SUPPRESS)
    return ap


def run(args: argparse.Namespace, log: Callable[[str], None] = print) -> int:
    wanted = args.cases.split(",") if args.cases else list(INDEX_CASES)
    unknown = sorted(set(wanted) - set(INDEX_CASES))
    if unknown:
        raise Refusal(f"not an index on/off case: {', '.join(unknown)} (choose from {', '.join(INDEX_CASES)})")
    label = args.label or f"run-{utc_now().replace(':', '').replace('-', '')}"
    if not LABEL_PATTERN.fullmatch(label):
        raise Refusal(f"label {label!r} must be letters, digits, '.', '_' or '-', with at most one '/'")
    s = connect.settings()
    if not s.pg_owner_password or not s.mongo_etl_password:
        raise Refusal("PG_OWNER_PASSWORD and MONGO_ETL_PASSWORD are needed")
    cases = [c for c in load_cases() if c.id in wanted]
    res = Results(args.results_root / label)
    started = utc_now()
    client, etl_db = connect.open_mongo("etl", s)
    try:
        with connect.open_pg("neighboar_owner", s) as owner:
            who = owner.execute("SELECT current_user").fetchone()[0]
            users = [u["user"] for u in etl_db.command("connectionStatus")["authInfo"]["authenticatedUsers"]]
            if who != "neighboar_owner" or users != ["etl"]:
                raise Refusal(f"must run as PostgreSQL neighboar_owner and MongoDB etl, not {who} and {users}")
            create_indexes(etl_db)  # idempotent: repairs an index left missing by an interrupted run
            ensure_indexes(owner, etl_db, log)
            res.dir.mkdir(parents=True, exist_ok=True)
            if not (res.dir / "README.txt").exists():
                (res.dir / "README.txt").write_text(README, encoding="utf-8")
            inv = new_invocation_id("index_on_off", res)
            log(f"invocation {inv} (index_on_off) into {res.dir}; warm cache only")
            rows = run_cases(
                owner, etl_db, cases, inv, res, log, args.runs, args.warmup,
                hook=force_failure if args.force_failure else None,
            )  # fmt: skip
            write_rows(res.dir / "index_on_off.csv", INDEX_COLUMNS, rows)
            doc = res.env_doc()
            doc.setdefault("label", label)
            doc.setdefault("note", "warm cache only; see README.txt")
            doc.setdefault("utc_start", started)
            doc.setdefault("invocations", []).append(
                {
                    "id": inv,
                    "kind": "index_on_off",
                    "cases": [c.id for c in cases],
                    "runs": args.runs,
                    "warmup": args.warmup,
                    "git_commit": os.environ.get("BENCH_GIT_COMMIT") or None,
                    "identities": {"postgresql": who, "mongodb": users[0]},
                    "utc_start": started,
                    "utc_end": utc_now(),
                    "test_hooks": ["force_failure"] if args.force_failure else [],
                }
            )
            res.save_env(doc)
        log(f"done: {inv}")
        return 0
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except Refusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except ForcedFailure as exc:
        print(f"failed (forced by the test hook): {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
