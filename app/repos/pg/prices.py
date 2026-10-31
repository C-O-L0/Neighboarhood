"""Comparable sales for a price check, from PostgreSQL: one statement per level (app/services/prices.py).

The block level is an index range scan on resale_transaction_building_month_idx; the street and town levels scan
resale_transaction_type_month_idx for the flat type and window and keep the town's (and street's) buildings.
The level's filter is fixed SQL text chosen by the level; every value is a bound parameter.
"""

from __future__ import annotations

from psycopg import Connection, sql

from app.repos.dto import Comparable
from app.services.prices import Level, Scope
from etl.clean import FLAT_TYPE_IDS, TOWN_IDS

_COMPARABLES = sql.SQL(
    """
SELECT t.txn_id, t.txn_month, b.block, b.street, t.storey_min, t.storey_max, t.floor_area_sqm, t.resale_price,
       b.lease_commence_year
FROM resale_transaction t
JOIN building b ON b.building_id = t.building_id
WHERE t.flat_type_id = %(flat_type_id)s AND t.txn_month >= %(start)s AND t.txn_month <= %(end)s
  AND {level}{storey}
ORDER BY t.txn_month DESC, t.txn_id DESC
"""
)
_LEVELS = {
    Level.BLOCK: sql.SQL("t.building_id = %(building_id)s"),
    Level.STREET: sql.SQL("b.town_id = %(town_id)s AND b.street = %(street)s"),
    Level.TOWN: sql.SQL("b.town_id = %(town_id)s"),
}
_STOREY = sql.SQL(" AND t.storey_min = %(storey_min)s")


class PgPriceRepo:
    """Built around one idle autocommit connection (docs/conventions.md, section 3)."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def comparables(self, scope: Scope) -> list[Comparable]:
        statement = _COMPARABLES.format(
            level=_LEVELS[scope.level], storey=_STOREY if scope.storey_min is not None else sql.SQL("")
        )
        params = {
            "flat_type_id": FLAT_TYPE_IDS[scope.flat_type],
            "start": scope.window.start,
            "end": scope.window.end,
            "building_id": scope.building_id,
            "town_id": TOWN_IDS[scope.town],
            "street": scope.street,
            "storey_min": scope.storey_min,
        }
        return [Comparable(*r) for r in self.conn.execute(statement, params).fetchall()]
