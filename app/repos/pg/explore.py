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

from psycopg import Connection

from app.repos.dto import FlatTypeRange, TownBaseline, TownFit
from app.services.explore import Window
from app.services.search import RESALE_TYPES
from etl.clean import FLAT_TYPE_IDS

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
