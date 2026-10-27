"""Saved blocks in PostgreSQL (app_rw): list, and writes that each touch buyer.last_active_at in one transaction.

The pool connection is autocommit, so `conn.transaction()` issues a real BEGIN at READ COMMITTED. The 50-save cap
is trigger (b), which locks the buyer row (`FOR UPDATE`) before it counts; a concurrent save by the same buyer
waits there and then counts the committed rows. Do not change the isolation level without revisiting that.
"""

from __future__ import annotations

from datetime import UTC

from psycopg import Connection
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation

from app.repos.dto import Outcome, RolledBack, SavedBlock

_LIST = """
SELECT s.building_id, b.block, b.street, tw.name, s.note, s.saved_at
FROM saved_building s
JOIN building b ON b.building_id = s.building_id
JOIN town tw ON tw.town_id = b.town_id
WHERE s.buyer_id = %s
ORDER BY s.building_id
"""
_BUILDING_EXISTS = "SELECT 1 FROM building WHERE building_id = %s"
_SAVED_EXISTS = "SELECT 1 FROM saved_building WHERE buyer_id = %s AND building_id = %s"
_INSERT = "INSERT INTO saved_building (buyer_id, building_id, note) VALUES (%s, %s, %s)"
_UPDATE_NOTE = "UPDATE saved_building SET note = %s WHERE buyer_id = %s AND building_id = %s"
_DELETE = "DELETE FROM saved_building WHERE buyer_id = %s AND building_id = %s"
_TOUCH = "UPDATE buyer SET last_active_at = now() WHERE buyer_id = %s"
_BUILDING_FK = "saved_building_building_id_fkey"


class _Abort(Exception):
    """Raised inside the transaction on purpose (the forced-failure demo) so that it rolls back."""

    def __init__(self, result: RolledBack):
        self.result = result


class PgSavedRepo:
    """Built around one autocommit connection (docs/conventions.md, section 3)."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def list(self, buyer_id: int) -> list[SavedBlock]:
        rows = self.conn.execute(_LIST, (buyer_id,)).fetchall()
        return [SavedBlock(r[0], r[1], r[2], r[3], r[4], r[5].astimezone(UTC)) for r in rows]

    def save(
        self, buyer_id: int, building_id: int, note: str | None, simulate_failure: bool = False
    ) -> Outcome | RolledBack:
        try:
            with self.conn.transaction():
                # Same outcome order as MongoDB. Trigger (b) runs before the primary and foreign key checks, so
                # without these two reads a buyer at 50 would get cap_reached for a missing or saved block.
                if self.conn.execute(_BUILDING_EXISTS, (building_id,)).fetchone() is None:
                    return Outcome.NO_SUCH_BUILDING
                if self.conn.execute(_SAVED_EXISTS, (buyer_id, building_id)).fetchone() is not None:
                    return Outcome.ALREADY_SAVED
                inserted = self.conn.execute(_INSERT, (buyer_id, building_id, note)).rowcount
                updated = self.conn.execute(_TOUCH, (buyer_id,)).rowcount
                if simulate_failure:
                    raise _Abort(RolledBack(insert=inserted, update=updated))
        except _Abort as abort:  # the transaction block has already rolled back
            return abort.result
        except CheckViolation:  # trigger (b); it is the only CHECK-class rule on saved_building
            return Outcome.CAP_REACHED
        except UniqueViolation:  # a concurrent save of the same block won between the read and the insert
            return Outcome.ALREADY_SAVED
        except ForeignKeyViolation as exc:
            if exc.diag.constraint_name == _BUILDING_FK:  # the block was deleted since the read
                return Outcome.NO_SUCH_BUILDING
            raise  # a missing buyer is a fault, not an outcome
        return Outcome.OK

    def edit_note(self, buyer_id: int, building_id: int, note: str | None) -> Outcome:
        with self.conn.transaction():
            if self.conn.execute(_UPDATE_NOTE, (note, buyer_id, building_id)).rowcount == 0:
                return Outcome.NOT_SAVED  # nothing changed, so the buyer is not touched
            self.conn.execute(_TOUCH, (buyer_id,))
        return Outcome.OK

    def remove(self, buyer_id: int, building_id: int) -> Outcome:
        with self.conn.transaction():
            if self.conn.execute(_DELETE, (buyer_id, building_id)).rowcount == 0:
                return Outcome.NOT_SAVED
            self.conn.execute(_TOUCH, (buyer_id,))
        return Outcome.OK
