"""Where a buyer can afford a flat type, from PostgreSQL: one parameterised statement per method.

Every statement filters on (flat_type_id, txn_month), so it can use resale_transaction_type_month_idx: the
latest month is one backward index probe per flat type (LATERAL), and the window queries are index range
scans for one flat type or for the listed resale types (= ANY). percentile_disc(0.5) is the lower median;
0.1 and 0.9 give the ends of the middle 80% of prices. The town baselines for explainer tags are the lower
medians of storey band, lease start year and floor area over the same window.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from psycopg import Connection, sql

from app.repos.dto import BlockFit, BlockFitPage, FlatTypeRange, TownBaseline, TownFit
from app.repos.pg.places import DISTANCE
from app.services.explore import PAGE_SIZE, BlockFilter, Window
from app.services.food import round_metres
from app.services.search import RESALE_TYPES
from etl.clean import FLAT_TYPE_IDS, TOWN_IDS

_RESALE_IDS = [FLAT_TYPE_IDS[t] for t in RESALE_TYPES]

_LATEST = """
SELECT max(last.txn_month)
FROM flat_type f
CROSS JOIN LATERAL (
    SELECT t.txn_month FROM resale_transaction t
    WHERE t.flat_type_id = f.flat_type_id
    ORDER BY t.txn_month DESC
    LIMIT 1
) last
"""

_RANGES = """
SELECT ft.name, count(*)::int,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY t.floor_area_sqm),
       percentile_disc(0.1) WITHIN GROUP (ORDER BY t.resale_price),
       percentile_disc(0.9) WITHIN GROUP (ORDER BY t.resale_price)
FROM resale_transaction t
JOIN flat_type ft ON ft.flat_type_id = t.flat_type_id
WHERE t.flat_type_id = ANY(%s) AND t.txn_month >= %s AND t.txn_month <= %s
GROUP BY t.flat_type_id, ft.name
ORDER BY t.flat_type_id
"""

_TOWNS = """
WITH per_town AS (
    SELECT b.town_id,
           count(*)::int AS n,
           percentile_disc(0.5) WITHIN GROUP (ORDER BY t.resale_price) AS median_price,
           (count(*) FILTER (WHERE t.resale_price <= %s))::int AS within_budget
    FROM resale_transaction t
    JOIN building b ON b.building_id = t.building_id
    WHERE t.flat_type_id = %s AND t.txn_month >= %s AND t.txn_month <= %s
    GROUP BY b.town_id
)
SELECT tw.name, p.n, p.median_price, p.within_budget
FROM per_town p
JOIN town tw ON tw.town_id = p.town_id
ORDER BY p.median_price, tw.name COLLATE "C"
"""

_BASELINES = """
WITH per_town AS (
    SELECT b.town_id,
           count(*)::int AS n,
           percentile_disc(0.5) WITHIN GROUP (ORDER BY t.storey_min + t.storey_max) AS storey_sum,
           percentile_disc(0.5) WITHIN GROUP (ORDER BY b.lease_commence_year) AS lease_year,
           percentile_disc(0.5) WITHIN GROUP (ORDER BY t.floor_area_sqm) AS area_median
    FROM resale_transaction t
    JOIN building b ON b.building_id = t.building_id
    WHERE t.flat_type_id = %s AND t.txn_month >= %s AND t.txn_month <= %s
    GROUP BY b.town_id
)
SELECT tw.name, p.n, p.storey_sum, p.lease_year, p.area_median
FROM per_town p
JOIN town tw ON tw.town_id = p.town_id
ORDER BY tw.name COLLATE "C"
"""

# Blocks that fit: medians per block over the window's sales of the flat type, then the block filters, then
# one page. The count and the page come from one statement: the count row always exists, and the page is
# LEFT JOINed to it, so a page past the end still reports the total. Optional parts are fixed SQL text.
_BLOCKS = sql.SQL(
    """
WITH {origin}
per_block AS (
    SELECT t.building_id,
           count(*)::int AS n,
           percentile_disc(0.5) WITHIN GROUP (ORDER BY t.resale_price) AS median_price,
           percentile_disc(0.5) WITHIN GROUP (ORDER BY t.storey_min + t.storey_max) AS storey_sum,
           percentile_disc(0.5) WITHIN GROUP (ORDER BY t.floor_area_sqm) AS area_median
    FROM resale_transaction t
    {town_join}
    WHERE t.flat_type_id = %(flat_type_id)s AND t.txn_month >= %(start)s AND t.txn_month <= %(end)s
    GROUP BY t.building_id
),
fits AS (
    SELECT p.building_id, b.block, b.street, tw.name AS town, p.n, p.median_price, p.storey_sum,
           p.area_median, b.lease_commence_year AS lease_year, {distance} AS distance
    FROM per_block p
    JOIN building b ON b.building_id = p.building_id
    JOIN town tw ON tw.town_id = b.town_id
    {origin_join}
    WHERE p.median_price <= %(budget)s{filters}
)
SELECT c.total, r.building_id, r.block, r.street, r.town, r.n, r.median_price, r.storey_sum, r.area_median,
       r.lease_year, r.distance
FROM (SELECT count(*)::int AS total FROM fits) c
LEFT JOIN LATERAL (
    SELECT * FROM fits ORDER BY {order} LIMIT %(limit)s OFFSET %(offset)s
) r ON true
ORDER BY {order_r}
"""
)
_ORIGIN = sql.SQL(
    "o AS (SELECT latitude, longitude FROM building WHERE building_id = %(place)s AND latitude IS NOT NULL),"
)
_DISTANCE_SQL = sql.SQL(DISTANCE)


def _blocks_statement(f: BlockFilter) -> tuple[sql.Composed, dict]:
    params: dict = {
        "flat_type_id": FLAT_TYPE_IDS[f.flat_type],
        "start": f.window.start,
        "end": f.window.end,
        "budget": f.budget,
        "limit": PAGE_SIZE,
        "offset": f.offset,
    }
    filters = []
    town_join = sql.SQL("")
    if f.town is not None:
        town_join = sql.SQL("JOIN building tb ON tb.building_id = t.building_id AND tb.town_id = %(town_id)s")
        params["town_id"] = TOWN_IDS[f.town]
    if f.min_lease_year is not None:
        filters.append(sql.SQL(" AND b.lease_commence_year >= %(min_lease_year)s"))
        params["min_lease_year"] = f.min_lease_year
    if f.min_area is not None:
        filters.append(sql.SQL(" AND p.area_median >= %(min_area)s"))
        params["min_area"] = f.min_area
    if f.place is None:
        origin, origin_join, distance = sql.SQL(""), sql.SQL(""), sql.SQL("NULL::double precision")
        order, order_r = sql.SQL("median_price, building_id"), sql.SQL("r.median_price, r.building_id")
    else:
        origin, origin_join, distance = _ORIGIN, sql.SQL("CROSS JOIN o"), _DISTANCE_SQL
        filters.append(
            sql.SQL(" AND b.latitude IS NOT NULL AND {distance} <= %(radius)s").format(distance=_DISTANCE_SQL)
        )
        params["place"], params["radius"] = f.place, f.radius_m
        order, order_r = sql.SQL("distance, building_id"), sql.SQL("r.distance, r.building_id")
    statement = _BLOCKS.format(
        origin=origin,
        town_join=town_join,
        distance=distance,
        origin_join=origin_join,
        filters=sql.Composed(filters),
        order=order,
        order_r=order_r,
    )
    return statement, params


class PgExploreRepo:
    """Built around one idle autocommit connection (docs/conventions.md, section 3)."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def latest_month(self) -> date | None:
        return self.conn.execute(_LATEST).fetchone()[0]

    def flat_type_ranges(self, window: Window) -> list[FlatTypeRange]:
        rows = self.conn.execute(_RANGES, [_RESALE_IDS, window.start, window.end]).fetchall()
        return [FlatTypeRange(*r) for r in rows]

    def towns_fit(self, flat_type: str, budget: Decimal, window: Window) -> list[TownFit]:
        params = [budget, FLAT_TYPE_IDS[flat_type], window.start, window.end]
        return [TownFit(*r) for r in self.conn.execute(_TOWNS, params).fetchall()]

    def baselines(self, flat_type: str, window: Window) -> list[TownBaseline]:
        params = [FLAT_TYPE_IDS[flat_type], window.start, window.end]
        return [TownBaseline(*r) for r in self.conn.execute(_BASELINES, params).fetchall()]

    def blocks_fit(self, f: BlockFilter) -> BlockFitPage:
        statement, params = _blocks_statement(f)
        rows = self.conn.execute(statement, params).fetchall()  # one statement: one snapshot
        blocks = [
            BlockFit(*r[1:10], distance_m=None if r[10] is None else round_metres(r[10]))
            for r in rows
            if r[1] is not None
        ]
        return BlockFitPage(total=rows[0][0], rows=blocks)
