"""Shortlists in PostgreSQL (app_rw): list, and writes that each touch buyer.last_active_at in one transaction.

A buyer's shortlist is their household's (household_saved) when they are in one, otherwise their own
(saved_building). Each write first locks the buyer row (`FOR UPDATE`) and reads the household there: accepting
an invitation locks the same row, so a write sees the household either before or after the shortlist moved,
never halfway. The 50-block caps are triggers (b) and (c), which lock the buyer or household row before they
count; a concurrent save waits there and then counts the committed rows. The pool connection is autocommit, so
`conn.transaction()` issues a real BEGIN at READ COMMITTED. Do not change the isolation level without
revisiting that.
"""

from __future__ import annotations

from datetime import UTC

from psycopg import Connection
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation

from app.repos.dto import Outcome, RolledBack, SavedBlock

_LIST = """
SELECT s.building_id, b.block, b.street, tw.name, s.note, s.saved_at, NULL::int, NULL::int
FROM saved_building s
JOIN building b ON b.building_id = s.building_id
JOIN town tw ON tw.town_id = b.town_id
WHERE s.buyer_id = %(buyer)s
UNION ALL
SELECT h.building_id, b.block, b.street, tw.name, h.note, h.saved_at, h.saved_by, h.note_by
FROM household_member m
JOIN household_saved h ON h.household_id = m.household_id
JOIN building b ON b.building_id = h.building_id
JOIN town tw ON tw.town_id = b.town_id
WHERE m.buyer_id = %(buyer)s
ORDER BY 1
"""
_OWNER = """
SELECT m.household_id
FROM buyer b LEFT JOIN household_member m ON m.buyer_id = b.buyer_id
WHERE b.buyer_id = %s
FOR UPDATE OF b
"""
_BUILDING_EXISTS = "SELECT 1 FROM building WHERE building_id = %s"
_TOUCH = "UPDATE buyer SET last_active_at = now() WHERE buyer_id = %s"

# Personal shortlist, keyed by buyer.
_P_EXISTS = "SELECT 1 FROM saved_building WHERE buyer_id = %s AND building_id = %s"
_P_INSERT = "INSERT INTO saved_building (buyer_id, building_id, note) VALUES (%s, %s, %s)"
_P_NOTE = "UPDATE saved_building SET note = %s WHERE buyer_id = %s AND building_id = %s"
_P_DELETE = "DELETE FROM saved_building WHERE buyer_id = %s AND building_id = %s"
# Household shortlist, keyed by household; the member who acts is recorded.
_H_EXISTS = "SELECT 1 FROM household_saved WHERE household_id = %s AND building_id = %s"
_H_INSERT = """
INSERT INTO household_saved (household_id, building_id, note, note_by, saved_by)
VALUES (%(owner)s, %(building)s, %(note)s::varchar,
        CASE WHEN %(note)s::varchar IS NULL THEN NULL ELSE %(buyer)s END, %(buyer)s)
"""
_H_NOTE = """
UPDATE household_saved
SET note = %(note)s::varchar, note_by = CASE WHEN %(note)s::varchar IS NULL THEN NULL ELSE %(buyer)s END
WHERE household_id = %(owner)s AND building_id = %(building)s
"""
_H_DELETE = "DELETE FROM household_saved WHERE household_id = %(owner)s AND building_id = %(building)s"
_BUILDING_FKS = ("saved_building_building_id_fkey", "household_saved_building_id_fkey")


class _Abort(Exception):
    """Raised inside the transaction on purpose (the forced-failure demo) so that it rolls back."""

    def __init__(self, result: RolledBack):
        self.result = result


class PgSavedRepo:
    """Built around one autocommit connection (docs/conventions.md, section 3)."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def list(self, buyer_id: int) -> list[SavedBlock]:
        rows = self.conn.execute(_LIST, {"buyer": buyer_id}).fetchall()
        return [SavedBlock(r[0], r[1], r[2], r[3], r[4], r[5].astimezone(UTC), r[6], r[7]) for r in rows]

    def _household(self, buyer_id: int) -> int | None:
        """Lock the buyer row and return their household id (None for a personal shortlist)."""
        row = self.conn.execute(_OWNER, (buyer_id,)).fetchone()
        if row is None:
            raise LookupError(f"no buyer {buyer_id}")  # a fault, not an outcome
        return row[0]

    def save(
        self, buyer_id: int, building_id: int, note: str | None, simulate_failure: bool = False
    ) -> Outcome | RolledBack:
        try:
            with self.conn.transaction():
                household = self._household(buyer_id)
                # Same outcome order as MongoDB. The cap triggers run before the primary and foreign key checks,
                # so without these two reads a full shortlist would give cap_reached for a missing or saved block.
                if self.conn.execute(_BUILDING_EXISTS, (building_id,)).fetchone() is None:
                    return Outcome.NO_SUCH_BUILDING
                if household is None:
                    if self.conn.execute(_P_EXISTS, (buyer_id, building_id)).fetchone() is not None:
                        return Outcome.ALREADY_SAVED
                    inserted = self.conn.execute(_P_INSERT, (buyer_id, building_id, note)).rowcount
                else:
                    if self.conn.execute(_H_EXISTS, (household, building_id)).fetchone() is not None:
                        return Outcome.ALREADY_SAVED
                    params = {"owner": household, "building": building_id, "note": note, "buyer": buyer_id}
                    inserted = self.conn.execute(_H_INSERT, params).rowcount
                updated = self.conn.execute(_TOUCH, (buyer_id,)).rowcount
                if simulate_failure:
                    raise _Abort(RolledBack(insert=inserted, update=updated))
        except _Abort as abort:  # the transaction block has already rolled back
            return abort.result
        except CheckViolation:  # trigger (b) or (c); with the buyer row locked, (d) cannot fire here
            return Outcome.CAP_REACHED
        except UniqueViolation:  # a concurrent save of the same block won between the read and the insert
            return Outcome.ALREADY_SAVED
        except ForeignKeyViolation as exc:
            if exc.diag.constraint_name in _BUILDING_FKS:  # the block was deleted since the read
                return Outcome.NO_SUCH_BUILDING
            raise
        return Outcome.OK

    def edit_note(self, buyer_id: int, building_id: int, note: str | None) -> Outcome:
        with self.conn.transaction():
            household = self._household(buyer_id)
            if household is None:
                changed = self.conn.execute(_P_NOTE, (note, buyer_id, building_id)).rowcount
            else:
                params = {"owner": household, "building": building_id, "note": note, "buyer": buyer_id}
                changed = self.conn.execute(_H_NOTE, params).rowcount
            if changed == 0:
                return Outcome.NOT_SAVED  # nothing changed, so the buyer is not touched
            self.conn.execute(_TOUCH, (buyer_id,))
        return Outcome.OK

    def remove(self, buyer_id: int, building_id: int) -> Outcome:
        with self.conn.transaction():
            household = self._household(buyer_id)
            if household is None:
                changed = self.conn.execute(_P_DELETE, (buyer_id, building_id)).rowcount
            else:
                changed = self.conn.execute(_H_DELETE, {"owner": household, "building": building_id}).rowcount
            if changed == 0:
                return Outcome.NOT_SAVED
            self.conn.execute(_TOUCH, (buyer_id,))
        return Outcome.OK
