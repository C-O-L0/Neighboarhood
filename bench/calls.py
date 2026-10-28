"""The functions the benchmark times.

Read functions take (connection_or_database, args) and call the app's own repos, so the benchmark measures what the
web app runs. Write functions time their own sub-steps and return {part: nanoseconds}. The bench-only statements
(cases.yaml) are built here from bound parameters and typed pipelines, never from strings.
"""

from __future__ import annotations

import time
from decimal import Decimal

from bson.decimal128 import Decimal128

from app.repos.dto import NewSale, SaleOutcome
from app.repos.mongo.admin import MongoAdminRepo
from app.repos.mongo.building import MongoBuildingRepo
from app.repos.mongo.food import MongoFoodRepo
from app.repos.mongo.rankings import MongoRankingRepo
from app.repos.mongo.search import MongoSearchRepo
from app.repos.mongo.trends import MongoTrendRepo
from app.repos.pg.admin import PgAdminRepo
from app.repos.pg.building import PgBuildingRepo
from app.repos.pg.food import PgFoodRepo
from app.repos.pg.rankings import PgRankingRepo
from app.repos.pg.search import PgSearchRepo
from app.repos.pg.trends import PgTrendRepo
from app.services.rankings import RankingQuery
from app.services.search import SearchQuery
from app.services.trends import TrendQuery
from db.mongo.stats import rebuild_all_stats

# ------------------------------------------------------------------------------------------------- repo reads


def pg_search(conn, a):
    return PgSearchRepo(conn).search(SearchQuery.model_validate(a))


def mongo_search(db, a):
    return MongoSearchRepo(db).search(SearchQuery.model_validate(a))


def pg_trends(conn, a):
    return PgTrendRepo(conn).trend(TrendQuery.model_validate(a))


def mongo_trends(db, a):
    return MongoTrendRepo(db).trend(TrendQuery.model_validate(a))


def pg_rankings(conn, a):
    return PgRankingRepo(conn).rank(RankingQuery.model_validate(a))


def mongo_rankings(db, a):
    return MongoRankingRepo(db).rank(RankingQuery.model_validate(a))


def pg_building(conn, a):
    return PgBuildingRepo(conn).get(a["building_id"])


def mongo_building(db, a):
    return MongoBuildingRepo(db).get(a["building_id"])


def pg_food_town(conn, a):
    return PgFoodRepo(conn).town(a["town"])


def mongo_food_town(db, a):
    return MongoFoodRepo(db).town(a["town"])


# ----------------------------------------------------------------------------------------- bench-only reads


def _plain(value):
    """Decimal128 to Decimal so results from the two databases compare."""
    return value.to_decimal() if isinstance(value, Decimal128) else value


def sql_call(statement: str, binds: dict, scalar: bool):
    def call(conn):
        rows = conn.execute(statement, binds).fetchall()
        if scalar:
            return rows[0][0] if rows else None
        return rows

    return call


def _finish(docs: list, scalar: str | None):
    if scalar is None:
        return docs
    return _plain(docs[0][scalar]) if docs else None


def pipeline_call(collection: str, pipeline: list, scalar: str | None):
    def call(db):
        return _finish(list(db[collection].aggregate(pipeline)), scalar)

    return call


def find_call(collection: str, filter_: dict, projection: dict | None, scalar: str | None):
    def call(db):
        return _finish(list(db[collection].find(filter_, projection)), scalar)

    return call


# ------------------------------------------------------------------------------------------------ writes


def pg_stats_rebuild(conn, a) -> dict[str, int]:
    t0 = time.perf_counter_ns()
    conn.execute("SELECT refresh_town_stats()")
    return {"call": time.perf_counter_ns() - t0}


def mongo_stats_rebuild(db, a) -> dict[str, int]:
    t0 = time.perf_counter_ns()
    rebuild_all_stats(db)
    return {"call": time.perf_counter_ns() - t0}


def _sale(building_id: int, a: dict) -> NewSale:
    return NewSale(
        building_id,
        a["month"],
        a["flat_type"],
        a["flat_model"],
        a["storey_min"],
        a["storey_max"],
        Decimal(a["floor_area_sqm"]),
        a["remaining_lease_months"],
        Decimal(a["resale_price"]),
    )


def _record_delete(repo, a, refresh=None) -> dict[str, int]:
    """Record one sale and delete it. The finally clause deletes the sale if anything after the insert fails."""
    ref = repo.find_building(a["block"], a["street"])
    if ref is None:
        raise LookupError(f"no block {a['block']} {a['street']}")
    sale = _sale(ref.building_id, a)
    txn_id = None
    write_ns = 0
    refresh_ns = 0
    try:
        t0 = time.perf_counter_ns()
        res = repo.record(sale)
        write_ns += time.perf_counter_ns() - t0
        if res.outcome is not SaleOutcome.OK:
            raise RuntimeError(f"the sale was not recorded: {res.outcome} {res.reason}")
        txn_id = res.txn_id
        t0 = time.perf_counter_ns()
        res = repo.delete(txn_id)
        write_ns += time.perf_counter_ns() - t0
        if res.outcome is not SaleOutcome.OK:
            raise RuntimeError(f"the sale was not deleted: {res.outcome}")
        txn_id = None
        if refresh is not None:
            t0 = time.perf_counter_ns()
            refresh()
            refresh_ns = time.perf_counter_ns() - t0
    finally:
        if txn_id is not None:
            repo.delete(txn_id)
    parts = {"write": write_ns}
    if refresh is not None:
        parts["refresh"] = refresh_ns
    return parts


def pg_admin_record_delete(conn, a) -> dict[str, int]:
    repo = PgAdminRepo(conn)
    return _record_delete(repo, a, refresh=repo.refresh)


def mongo_admin_record_delete(db, a) -> dict[str, int]:
    return _record_delete(MongoAdminRepo(db), a)
