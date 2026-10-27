"""Town rankings from PostgreSQL: one parameterised statement.

Town-year medians are aggregated from resale_transaction over a sargable half-open date range
(the same expressions as v_town_year_stats, whose EXTRACT(YEAR ...) filter cannot use the
(flat_type_id, txn_month) index). RANK() ranks the towns within each flat type by median
descending, and a correlated scalar subquery gives the island median for the same flat type
and year.
"""

from __future__ import annotations

from psycopg import Connection, sql

from app.repos.dto import TownRank
from app.services.rankings import RankingQuery
from etl.clean import FLAT_TYPE_IDS

_SELECT = sql.SQL(
    """
WITH town_stats AS (
    SELECT t.flat_type_id, b.town_id,
           count(*)::int AS n,
           percentile_disc(0.5) WITHIN GROUP (ORDER BY t.resale_price) AS median_price,
           round(avg(t.resale_price / t.floor_area_sqm), 2) AS avg_psm
    FROM resale_transaction t
    JOIN building b ON b.building_id = t.building_id
    WHERE t.txn_month >= make_date(%s::int, 1, 1) AND t.txn_month < make_date(%s::int + 1, 1, 1){flat_filter}
    GROUP BY t.flat_type_id, b.town_id
),
ranked AS (
    SELECT s.flat_type_id, s.town_id, s.n, s.median_price, s.avg_psm,
           RANK() OVER (PARTITION BY s.flat_type_id ORDER BY s.median_price DESC) AS rank,
           (SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY i.resale_price)
            FROM resale_transaction i
            WHERE i.flat_type_id = s.flat_type_id
              AND i.txn_month >= make_date(%s::int, 1, 1) AND i.txn_month < make_date(%s::int + 1, 1, 1)
           ) AS island_median
    FROM town_stats s
)
SELECT ft.name, r.rank, tw.name, r.n, r.median_price, r.avg_psm, r.island_median,
       r.median_price > r.island_median
FROM ranked r
JOIN flat_type ft ON ft.flat_type_id = r.flat_type_id
JOIN town tw ON tw.town_id = r.town_id
ORDER BY ft.name COLLATE "C", r.rank, tw.name COLLATE "C"
"""
)


class PgRankingRepo:
    """Built around one idle autocommit connection (docs/conventions.md, section 3)."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def rank(self, query: RankingQuery) -> list[TownRank]:
        params: list = [query.year, query.year]
        flat_filter = sql.SQL("")
        if query.flat_type is not None:
            flat_filter = sql.SQL(" AND t.flat_type_id = %s")
            params.append(FLAT_TYPE_IDS[query.flat_type])
        params += [query.year, query.year]
        statement = _SELECT.format(flat_filter=flat_filter)
        rows = self.conn.execute(statement, params).fetchall()  # one statement: one snapshot
        return [TownRank(*r) for r in rows]
