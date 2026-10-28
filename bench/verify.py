"""Full-data verification before any timing (BUILD_PLAN sections 6 and 11).

Settles first (refresh_town_stats() as app_admin), then compares the two databases as PG app_ro and MongoDB app:

  1. every Case in the equivalence registry (tests/equivalence/cases.py) whose `writes` flag is false, with
     assert_equivalent over the case's full parameter list. Cases that write are never called here;
  2. the bench-only checks: median_exact (PostgreSQL percentile_disc against the MongoDB $sortArray lower median,
     exactly equal for every parameter set) and building_recent_lookup (the $lookup returns the same sale ids in the
     same order as the embedded recent_sales, txn_month DESC then txn_id DESC, as db/mongo/stats.py).

Usage:  python -m bench.verify        Exits 1 on any mismatch.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from app.repos.pg.building import PgBuildingRepo
from bench import connect
from bench.cases import BenchCase, load_cases
from tests.equivalence.cases import CASES, Case
from tests.equivalence.compare import assert_equivalent


@dataclass
class VerifyResult:
    checked: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def settle(admin_conn) -> None:
    """The settle step: refresh the town-month view so both databases are at the same logical state."""
    admin_conn.execute("SELECT refresh_town_stats()")


def resolve_building_ids(cases: list[BenchCase], pg_conn) -> None:
    """Add building_id to the arguments of cases marked `resolve: building`, from the block and street."""
    repo = PgBuildingRepo(pg_conn)
    for case in cases:
        if case.resolve != "building":
            continue
        for ps in case.params:
            building_id = repo.find_id(ps.args["block"], ps.args["street"])
            if building_id is None:
                raise LookupError(f"case {case.id}: no block {ps.args['block']} {ps.args['street']} in the database")
            ps.args["building_id"] = building_id


def verify_registry(pg_conn, mongo_db, cases: list[Case], result: VerifyResult, force_mismatch: bool = False) -> None:
    forced = force_mismatch
    for case in cases:
        if case.writes:
            continue
        params = case.params(pg_conn)
        if not params:
            result.failures.append(f"{case.name}: no parameter sets")
            continue
        bad = 0
        for p in params:
            pg, mongo = case.pg_call(pg_conn, p), case.mongo_call(mongo_db, p)
            if forced:  # test hook: pretend the other database answered with nothing
                mongo, forced = None, False
            result.checked += 1
            try:
                assert_equivalent(pg, mongo, path=f"{case.name}{p}")
            except AssertionError as exc:
                bad += 1
                result.failures.append(str(exc))
        print(f"  {case.name}: {len(params) - bad} of {len(params)} parameter sets equivalent", flush=True)


def verify_bench_only(pg_conn, mongo_db, bench_cases: list[BenchCase], result: VerifyResult) -> None:
    by_id = {c.id: c for c in bench_cases}

    exact = by_id["median_exact"]
    for ps in exact.params:
        pg = exact.pg.prepare(ps.args)(pg_conn)
        mongo = exact.mongo.prepare(ps.args)(mongo_db)
        result.checked += 1
        if type(pg) is not type(mongo) or pg != mongo:
            result.failures.append(f"median_exact[{ps.label}]: postgres={pg!r} mongo={mongo!r}")
    print(f"  median_exact: {len(exact.params)} parameter sets checked", flush=True)

    recent = by_id["building_recent_lookup"]
    for ps in recent.params:
        looked_up = [d["_id"] for d in recent.mongo.prepare(ps.args)(mongo_db)]
        doc = mongo_db.building_profiles.find_one(
            {"block": ps.args["block"], "street": ps.args["street"]}, {"recent_sales.txn_id": 1}
        )
        embedded = [r["txn_id"] for r in doc["recent_sales"]] if doc else None
        result.checked += 1
        if looked_up != embedded:
            result.failures.append(f"building_recent_lookup[{ps.label}]: $lookup={looked_up} embedded={embedded}")
    print(f"  building_recent_lookup: {len(recent.params)} parameter sets checked", flush=True)


def run_verify(
    pg_conn,
    mongo_db,
    admin_conn,
    *,
    cases: list[Case] | None = None,
    bench_cases: list[BenchCase] | None = None,
    force_mismatch: bool = False,
    log: Callable[[str], None] = print,
) -> VerifyResult:
    """Settle, then compare. `pg_conn` is app_ro, `mongo_db` is the app user's database, `admin_conn` is app_admin."""
    result = VerifyResult()
    log("verify: settling (refresh_town_stats as app_admin)")
    settle(admin_conn)
    log("verify: equivalence registry (read cases only)")
    verify_registry(pg_conn, mongo_db, CASES if cases is None else cases, result, force_mismatch)
    log("verify: bench-only checks")
    verify_bench_only(pg_conn, mongo_db, bench_cases if bench_cases is not None else load_cases(), result)
    if result.ok:
        log(f"verify: OK ({result.checked} checks)")
    else:
        log(f"verify: FAILED, {len(result.failures)} mismatch(es); first: {result.failures[0]}")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force-mismatch", action="store_true", help=argparse.SUPPRESS)  # test hook
    args = ap.parse_args(argv)
    bench_cases = load_cases()
    s = connect.settings()
    client, mongo_db = connect.open_mongo("app", s)
    try:
        with connect.open_pg("app_ro", s) as pg, connect.open_pg("app_admin", s) as admin:
            resolve_building_ids(bench_cases, pg)
            result = run_verify(pg, mongo_db, admin, bench_cases=bench_cases, force_mismatch=args.force_mismatch)
    finally:
        client.close()
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
