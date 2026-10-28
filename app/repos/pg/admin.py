"""Admin sale writes in PostgreSQL (app_admin only): record, delete, list, and the town-figures refresh.

A write is one transaction on an autocommit connection (`conn.transaction()` issues a real BEGIN). Trigger (a)
(`check_resale_transaction`) runs inside the INSERT. The materialised view is refreshed by `refresh()`, a separate
statement after the commit, so the service can time it on its own and a failed refresh leaves the sale committed.
"""

from __future__ import annotations

from psycopg import Connection
from psycopg.errors import CheckViolation, ForeignKeyViolation

from app.repos.dto import BuildingRef, Figures, NewSale, SaleOutcome, SaleResult, SaleRow
from etl.clean import FLAT_MODEL_IDS, FLAT_TYPE_IDS

_FIND_BUILDING = "SELECT building_id, block, street FROM building WHERE block = %s AND street = %s"
_INSERT = """
INSERT INTO resale_transaction
    (building_id, flat_type_id, flat_model_id, txn_month, storey_min, storey_max,
     floor_area_sqm, remaining_lease_months, resale_price, source)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'admin')
RETURNING txn_id
"""
_DELETE = "DELETE FROM resale_transaction WHERE txn_id = %s RETURNING building_id"
_BUILDING_OF = "SELECT building_id FROM resale_transaction WHERE txn_id = %s"
_FIGURES = """
SELECT (SELECT count(*) FROM resale_transaction),
       count(*),
       percentile_disc(0.5) WITHIN GROUP (ORDER BY resale_price)
FROM resale_transaction
WHERE building_id = %s
"""
_ADMIN_SALES = """
SELECT txn_id, txn_month, town, block, street, building_id, flat_type, flat_model, storey_min, storey_max,
       floor_area_sqm, lease_commence_year, remaining_lease_months, resale_price
FROM v_transaction_detail
WHERE source = 'admin'
ORDER BY txn_id DESC
LIMIT %s
"""
_BUILDING_FK = "resale_transaction_building_id_fkey"


class _Abort(Exception):
    """Raised inside the transaction on purpose (the forced-failure demo) so that it rolls back."""

    def __init__(self, txn_id: int | None):
        self.txn_id = txn_id


class PgAdminRepo:
    """Built around one autocommit app_admin connection (docs/conventions.md, section 3)."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def find_building(self, block: str, street: str) -> BuildingRef | None:
        row = self.conn.execute(_FIND_BUILDING, (block, street)).fetchone()
        return None if row is None else BuildingRef(*row)

    def figures(self, building_id: int) -> Figures:
        total, block_sales, median = self.conn.execute(_FIGURES, (building_id,)).fetchone()
        return Figures(total, block_sales, median)

    def record(self, sale: NewSale, simulate_failure: bool = False) -> SaleResult:
        before = self.figures(sale.building_id) if simulate_failure else None
        params = (
            sale.building_id,
            FLAT_TYPE_IDS[sale.flat_type],
            FLAT_MODEL_IDS[sale.flat_model],
            sale.txn_month,
            sale.storey_min,
            sale.storey_max,
            sale.floor_area_sqm,
            sale.remaining_lease_months,
            sale.resale_price,
        )
        try:
            with self.conn.transaction():
                txn_id = self.conn.execute(_INSERT, params).fetchone()[0]
                if simulate_failure:
                    raise _Abort(txn_id)
        except _Abort as abort:  # the transaction block has already rolled back
            return SaleResult(SaleOutcome.OK, abort.txn_id, before=before, after=self.figures(sale.building_id))
        except CheckViolation as exc:  # trigger (a), or a CHECK the app should already have caught
            return SaleResult(SaleOutcome.REJECTED, reason=exc.diag.message_primary)
        except ForeignKeyViolation as exc:
            if exc.diag.constraint_name == _BUILDING_FK:  # the block was deleted since the lookup
                return SaleResult(SaleOutcome.NO_SUCH_BUILDING)
            raise
        return SaleResult(SaleOutcome.OK, txn_id)

    def delete(self, txn_id: int, simulate_failure: bool = False) -> SaleResult:
        before = None
        building_id = None
        if simulate_failure:
            row = self.conn.execute(_BUILDING_OF, (txn_id,)).fetchone()
            if row is None:
                return SaleResult(SaleOutcome.NOT_FOUND)
            building_id = row[0]
            before = self.figures(building_id)
        try:
            with self.conn.transaction():
                row = self.conn.execute(_DELETE, (txn_id,)).fetchone()
                if row is None:
                    return SaleResult(SaleOutcome.NOT_FOUND)  # nothing deleted, so nothing to commit
                if simulate_failure:
                    raise _Abort(txn_id)
        except _Abort:
            return SaleResult(SaleOutcome.OK, txn_id, before=before, after=self.figures(building_id))
        return SaleResult(SaleOutcome.OK, txn_id)

    def refresh(self) -> None:
        """Refresh mv_town_month_stats via the SECURITY DEFINER function, as its own statement outside a transaction."""
        self.conn.execute("SELECT refresh_town_stats()")

    def admin_sales(self, limit: int) -> list[SaleRow]:
        return [SaleRow(*r) for r in self.conn.execute(_ADMIN_SALES, (limit,)).fetchall()]
