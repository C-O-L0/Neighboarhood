"""Price trends from PostgreSQL: the town-month materialised view, a self-join for the previous year
and a LATERAL subquery for the rolling 12-calendar-month median, in one parameterised statement."""

from __future__ import annotations

from psycopg import Connection, sql

from app.repos.dto import TrendPoint
from app.services.trends import TrendQuery
from etl.clean import FLAT_TYPE_IDS, TOWN_IDS

_SELECT = sql.SQL(
    """
SELECT m.month, m.n, m.median_price, m.avg_psm, p.median_price, r.n, r.median_price
FROM mv_town_month_stats m
LEFT JOIN mv_town_month_stats p
       ON p.town_id = m.town_id AND p.flat_type_id = m.flat_type_id
      AND p.month = (m.month - interval '12 months')::date
CROSS JOIN LATERAL (
    SELECT count(*)::int AS n, percentile_disc(0.5) WITHIN GROUP (ORDER BY t.resale_price) AS median_price
    FROM resale_transaction t
    JOIN building b USING (building_id)
    WHERE b.town_id = m.town_id AND t.flat_type_id = m.flat_type_id
      AND t.txn_month BETWEEN (m.month - interval '11 months')::date AND m.month
) r
WHERE m.town_id = %s AND m.flat_type_id = %s"""
)


class PgTrendRepo:
    """Built around one idle autocommit connection (docs/conventions.md, section 3)."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def trend(self, query: TrendQuery) -> list[TrendPoint]:
        params: list = [TOWN_IDS[query.town], FLAT_TYPE_IDS[query.flat_type]]
        tail = ""
        if query.month_from is not None:
            tail += " AND m.month >= %s"
            params.append(query.month_from)
        if query.month_to is not None:
            tail += " AND m.month <= %s"
            params.append(query.month_to)
        statement = _SELECT + sql.SQL(tail) + sql.SQL(" ORDER BY m.month")
        rows = self.conn.execute(statement, params).fetchall()  # one statement: one snapshot
        return [TrendPoint(*r) for r in rows]
