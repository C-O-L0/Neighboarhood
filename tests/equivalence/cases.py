"""The single registry of PostgreSQL / MongoDB comparison cases.

Each feature registers a Case. `pg_call(conn, params)` and `mongo_call(db, params)` return DTOs;
`params(conn)` returns the parameter dicts to run, given a PostgreSQL connection so it can look
at the loaded data. The pytest files here iterate over CASES, and Phase 4's bench/verify.py will
iterate over the same registry on the full data.

Sampling rule: with at most SAMPLE_MAX buildings loaded (the sample), every case runs its full
parameter list; otherwise each feature returns the fixed full-data list it declares.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.repos.mongo.building import MongoBuildingRepo
from app.repos.mongo.search import MongoSearchRepo
from app.repos.pg.building import PgBuildingRepo
from app.repos.pg.search import PgSearchRepo
from app.services.search import SearchQuery

SAMPLE_MAX = 1000
FULL_DATA_STEP = 25  # building cases: every 25th building id in id order, plus the named blocks


@dataclass(frozen=True)
class Case:
    name: str
    pg_call: Callable[[Any, dict], Any]
    mongo_call: Callable[[Any, dict], Any]
    params: Callable[[Any], list[dict]]


def is_sample(conn) -> bool:
    return conn.execute("SELECT count(*) FROM building").fetchone()[0] <= SAMPLE_MAX


def building_params(conn) -> list[dict]:
    ids = [r[0] for r in conn.execute("SELECT building_id FROM building ORDER BY building_id").fetchall()]
    if len(ids) > SAMPLE_MAX:
        named = conn.execute(
            """
            SELECT building_id FROM building WHERE NOT in_property_dataset
            UNION
            SELECT building_id FROM building WHERE block = '37' AND street = 'TEBAN GDNS RD'
            UNION
            (SELECT building_id FROM building b
             WHERE in_property_dataset
               AND NOT EXISTS (SELECT 1 FROM building_flat_supply s WHERE s.building_id = b.building_id)
             ORDER BY building_id LIMIT 1)
            """
        ).fetchall()
        ids = sorted({*ids[::FULL_DATA_STEP], *(r[0] for r in named)})
    return [{"building_id": i} for i in ids]


# Search parameter sets are raw query strings, as a browser sends them. Each returns rows on the
# sample (towns BISHAN, MARINE PARADE, BUKIT TIMAH) and on full data.
DUPLICATE_PAIR = {
    "block": "18",
    "street": "MARINE TER",
    "month_from": "2017-02",
    "month_to": "2017-02",
    "flat_type": "4 ROOM",
}
TIED_PRICE = {"price_min": "550000", "price_max": "550000"}  # 79 sales on the sample, 64 of them 4 ROOM
TIED_AREA = {"area_min": "121", "area_max": "121"}  # 550 sales on the sample

SEARCH_PARAMS: list[dict] = [
    {"town": "BISHAN"},
    {"town": "MARINE PARADE", "sort": "price", "order": "asc"},
    {"flat_type": "3 ROOM"},
    {"flat_model": "Maisonette"},
    {"month_from": "2020-01", "month_to": "2020-12"},
    {"price_min": "400000", "price_max": "500000.00"},
    {"area_min": "90", "area_max": "100.0"},
    {"street": "QUEEN'S"},
    {"block": "18"},
    {"town": "BISHAN", "flat_type": "4 ROOM", "price_min": "500000", "month_from": "2022-01"},
    {"town": "MARINE PARADE", "flat_model": "Improved", "area_min": "60", "street": "marine"},
    {"sort": "month", "order": "asc"},
    {"sort": "month", "order": "desc"},
    {"sort": "price", "order": "asc"},
    {"sort": "price", "order": "desc"},
    {"sort": "area", "order": "asc"},
    {"sort": "area", "order": "desc"},
    {"page": "2"},
    {"town": "BISHAN", "page": "5", "sort": "price"},
    {**TIED_PRICE, "sort": "price", "order": "asc"},
    {**TIED_PRICE, "sort": "price", "order": "asc", "page": "2"},
    {**TIED_PRICE, "sort": "price", "order": "desc", "page": "2"},
    {**TIED_AREA, "sort": "area", "order": "desc", "page": "2"},
    {},
]
# Cases about literal matching, duplicates and paging past the end; some return no rows.
SEARCH_EDGE_PARAMS: list[dict] = [
    {"street": "ST."},
    {"street": "%"},
    {"street": "_"},
    {"street": ".*"},
    {"street": "' OR 1=1 --"},
    {"street": "\\"},
    {"street": "BISHAN ST 1_"},
    DUPLICATE_PAIR,
    {**DUPLICATE_PAIR, "page": "2"},
    {"page": "10000"},
    {"town": "BISHAN", "page": "10000", "sort": "area", "order": "asc"},
]
# Added on full data only (more than SAMPLE_MAX buildings): the street with a dot in its name.
FULL_DATA_EDGE_PARAMS: list[dict] = [
    {"street": "ST. GEORGE'S"},
    {"town": "TAMPINES", "sort": "price", "page": "3"},
    {"flat_type": "5 ROOM", "month_from": "2023-01", "month_to": "2023-06", "sort": "area", "order": "asc"},
]


def search_params(conn) -> list[dict]:
    return list(SEARCH_PARAMS)


def search_edge_params(conn) -> list[dict]:
    return SEARCH_EDGE_PARAMS + ([] if is_sample(conn) else FULL_DATA_EDGE_PARAMS)


def _query(p: dict) -> SearchQuery:
    return SearchQuery.model_validate(p)


def _pg_search(conn, p):
    return PgSearchRepo(conn).search(_query(p))


def _mongo_search(db, p):
    return MongoSearchRepo(db).search(_query(p))


CASES: list[Case] = [
    Case(
        name="building",
        pg_call=lambda conn, p: PgBuildingRepo(conn).get(p["building_id"]),
        mongo_call=lambda db, p: MongoBuildingRepo(db).get(p["building_id"]),
        params=building_params,
    ),
    Case(name="search", pg_call=_pg_search, mongo_call=_mongo_search, params=search_params),
    Case(name="search_edges", pg_call=_pg_search, mongo_call=_mongo_search, params=search_edge_params),
]
