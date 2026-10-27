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
from app.repos.mongo.trends import MongoTrendRepo
from app.repos.pg.building import PgBuildingRepo
from app.repos.pg.search import PgSearchRepo
from app.repos.pg.trends import PgTrendRepo
from app.services.search import SearchQuery
from app.services.trends import TrendQuery

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


# Trend series: every (town, resale flat type) pair with sales, unbounded and with a range whose
# bounds are two months inside the series that have sales. On full data (more pairs than
# TREND_SAMPLE_PAIRS) the list is exactly 10 distinct pairs chosen by a fixed rule (select_full_pairs):
# the first 3 pairs in (town, flat type) order that have missing months, then pairs from every
# (len // 10)th position of that order that are not already chosen, until there are 10; sorted.
TREND_SAMPLE_PAIRS = 40
TREND_FULL_PAIRS = 10


def trend_series(conn) -> dict[tuple[str, str], list]:
    """(town, flat type) -> ascending months with sales, from the town-month view."""
    rows = conn.execute(
        """
        SELECT tw.name, ft.name, array_agg(m.month ORDER BY m.month)
        FROM mv_town_month_stats m
        JOIN town tw USING (town_id)
        JOIN flat_type ft USING (flat_type_id)
        GROUP BY tw.name, ft.name
        ORDER BY tw.name COLLATE "C", ft.name COLLATE "C"
        """
    ).fetchall()
    return {(t, f): months for t, f, months in rows}


def has_gap(months: list) -> bool:
    return len(months) != (months[-1].year - months[0].year) * 12 + months[-1].month - months[0].month + 1


def select_full_pairs(keys: list, gappy: set) -> list:
    """The full-data selection rule: 3 gappy pairs first, then stride pairs not yet chosen, 10 in all."""
    chosen = [k for k in keys if k in gappy][:3]
    for k in keys[:: max(1, len(keys) // TREND_FULL_PAIRS)]:
        if len(chosen) == TREND_FULL_PAIRS:
            break
        if k not in chosen:
            chosen.append(k)
    for k in keys:  # only reached when the stride gives fewer than enough distinct pairs
        if len(chosen) == TREND_FULL_PAIRS:
            break
        if k not in chosen:
            chosen.append(k)
    return sorted(chosen)


def trend_params(conn) -> list[dict]:
    series = trend_series(conn)
    keys = list(series)
    if len(keys) > TREND_SAMPLE_PAIRS:
        keys = select_full_pairs(keys, {k for k in keys if has_gap(series[k])})
    params = []
    for town, flat_type in keys:
        params.append({"town": town, "flat_type": flat_type})
        months = series[town, flat_type]
        if len(months) >= 2:
            lo, hi = months[len(months) // 4], months[3 * len(months) // 4]
            params.append(
                {"town": town, "flat_type": flat_type, "month_from": f"{lo:%Y-%m}", "month_to": f"{hi:%Y-%m}"}
            )
    return params


def _pg_trend(conn, p):
    return PgTrendRepo(conn).trend(TrendQuery.model_validate(p))


def _mongo_trend(db, p):
    return MongoTrendRepo(db).trend(TrendQuery.model_validate(p))


CASES: list[Case] = [
    Case(
        name="building",
        pg_call=lambda conn, p: PgBuildingRepo(conn).get(p["building_id"]),
        mongo_call=lambda db, p: MongoBuildingRepo(db).get(p["building_id"]),
        params=building_params,
    ),
    Case(name="search", pg_call=_pg_search, mongo_call=_mongo_search, params=search_params),
    Case(name="search_edges", pg_call=_pg_search, mongo_call=_mongo_search, params=search_edge_params),
    Case(name="trends", pg_call=_pg_trend, mongo_call=_mongo_trend, params=trend_params),
]
