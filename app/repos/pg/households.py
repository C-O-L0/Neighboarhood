"""Households in PostgreSQL (app_rw): invitations, accepting, leaving, and reading a buyer's household (UC10).

Every write is one transaction that first locks the buyer rows it involves, in buyer id order, so two writes
about the same people queue instead of interleaving (and cannot deadlock). The schema enforces the rest: one
household per buyer (household_member's primary key), at most two members (seat 1 or 2, unique), one
invitation per inviter, no personal saves for a member (trigger d) and at most 50 shared blocks (trigger c).
"""

from __future__ import annotations

from datetime import UTC

from psycopg import Connection

from app.repos.dto import (
    AcceptResult,
    Household,
    HouseholdOutcome,
    Invitation,
    Invitations,
    Member,
    Person,
)

CAP = 50

_LOCK = "SELECT buyer_id FROM buyer WHERE buyer_id = ANY(%s) ORDER BY buyer_id FOR UPDATE"
_IN_HOUSEHOLD = "SELECT buyer_id FROM household_member WHERE buyer_id = ANY(%s)"
_BY_EMAIL = "SELECT buyer_id FROM buyer WHERE lower(email) = lower(%s)"
_TOUCH = "UPDATE buyer SET last_active_at = now() WHERE buyer_id = %s"

_HOUSEHOLD = """
SELECT h.household_id, b.buyer_id, b.email, b.display_name, m.seat,
       (SELECT count(*)::int FROM household_saved s WHERE s.household_id = h.household_id)
FROM household_member me
JOIN household h ON h.household_id = me.household_id
JOIN household_member m ON m.household_id = h.household_id
JOIN buyer b ON b.buyer_id = m.buyer_id
WHERE me.buyer_id = %s
ORDER BY m.seat
"""
_INVITATIONS = """
SELECT i.inviter_id, a.email, a.display_name, i.invitee_id, e.email, e.display_name, i.created_at
FROM household_invitation i
JOIN buyer a ON a.buyer_id = i.inviter_id
JOIN buyer e ON e.buyer_id = i.invitee_id
WHERE i.inviter_id = %(buyer)s OR i.invitee_id = %(buyer)s
ORDER BY i.created_at, i.inviter_id
"""
_INVITE = """
INSERT INTO household_invitation (inviter_id, invitee_id) VALUES (%s, %s)
ON CONFLICT (inviter_id) DO UPDATE SET invitee_id = EXCLUDED.invitee_id, created_at = now()
"""
_INVITED = "SELECT 1 FROM household_invitation WHERE inviter_id = %s AND invitee_id = %s"
_DROP_INVITATION = "DELETE FROM household_invitation WHERE inviter_id = %s AND invitee_id = %s"
_DROP_INVITATIONS_OF = "DELETE FROM household_invitation WHERE inviter_id = ANY(%(ids)s) OR invitee_id = ANY(%(ids)s)"

_NEW_HOUSEHOLD = "INSERT INTO household DEFAULT VALUES RETURNING household_id"
_JOIN = "INSERT INTO household_member (buyer_id, household_id, seat) VALUES (%s, %s, %s)"
# The inviter's blocks start the shared shortlist; whoever saved a block wrote its note.
_SHARE_ALL = """
INSERT INTO household_saved (household_id, building_id, note, note_by, saved_by, saved_at)
SELECT %(household)s, building_id, note, CASE WHEN note IS NULL THEN NULL ELSE buyer_id END, buyer_id, saved_at
FROM saved_building WHERE buyer_id = %(buyer)s
"""
# The partner's blocks that are not shared yet, oldest first, as many as fit.
_SHARE_MORE = """
INSERT INTO household_saved (household_id, building_id, note, note_by, saved_by, saved_at)
SELECT %(household)s, s.building_id, s.note, CASE WHEN s.note IS NULL THEN NULL ELSE s.buyer_id END, s.buyer_id,
       s.saved_at
FROM saved_building s
WHERE s.buyer_id = %(buyer)s
  AND NOT EXISTS (SELECT 1 FROM household_saved h
                  WHERE h.household_id = %(household)s AND h.building_id = s.building_id)
ORDER BY s.saved_at, s.building_id
LIMIT %(room)s
"""
_NEW_TO_SHARE = """
SELECT count(*)::int FROM saved_building s
WHERE s.buyer_id = %(buyer)s
  AND NOT EXISTS (SELECT 1 FROM household_saved h
                  WHERE h.household_id = %(household)s AND h.building_id = s.building_id)
"""
_SHARED_COUNT = "SELECT count(*)::int FROM household_saved WHERE household_id = %s"
_DROP_PERSONAL = "DELETE FROM saved_building WHERE buyer_id = ANY(%s)"

_MY_HOUSEHOLD = "SELECT household_id FROM household_member WHERE buyer_id = %s"
_HOUSEMATES = """
SELECT o.buyer_id FROM household_member me
JOIN household_member o ON o.household_id = me.household_id AND o.buyer_id <> me.buyer_id
WHERE me.buyer_id = %s
"""
_PARTNER = "SELECT buyer_id FROM household_member WHERE household_id = %s AND buyer_id <> %s"
_SHARED_ROWS = "SELECT building_id, note, saved_at FROM household_saved WHERE household_id = %s"
_DISBAND = "DELETE FROM household WHERE household_id = %s"  # members and shared rows go with it (CASCADE)
_KEEP = "INSERT INTO saved_building (buyer_id, building_id, note, saved_at) VALUES (%s, %s, %s, %s)"


def _person(buyer_id, email, display_name) -> Person:
    return Person(buyer_id, email, display_name)


class PgHouseholdRepo:
    """Built around one autocommit connection (docs/conventions.md, section 3)."""

    def __init__(self, conn: Connection):
        self.conn = conn

    # ---------------------------------------------------------------- reads

    def household(self, buyer_id: int) -> Household | None:
        rows = self.conn.execute(_HOUSEHOLD, (buyer_id,)).fetchall()
        if not rows:
            return None
        return Household(rows[0][0], [Member(r[1], r[2], r[3], r[4]) for r in rows], rows[0][5])

    def invitations(self, buyer_id: int) -> Invitations:
        sent, received = None, []
        for r in self.conn.execute(_INVITATIONS, {"buyer": buyer_id}).fetchall():
            inv = Invitation(_person(*r[0:3]), _person(*r[3:6]), r[6].astimezone(UTC))
            if inv.inviter.buyer_id == buyer_id:
                sent = inv
            else:
                received.append(inv)
        return Invitations(sent, received)

    # ---------------------------------------------------------------- writes

    def _lock(self, *ids: int) -> set[int]:
        """Lock these buyer rows; return the ids of those already in a household."""
        self.conn.execute(_LOCK, (list(ids),))
        return {r[0] for r in self.conn.execute(_IN_HOUSEHOLD, (list(ids),)).fetchall()}

    def invite(self, inviter_id: int, email: str) -> HouseholdOutcome:
        """Invite the account with this email (ignoring case); a new invitation replaces the inviter's last one."""
        with self.conn.transaction():
            row = self.conn.execute(_BY_EMAIL, (email,)).fetchone()
            if row is None:
                return HouseholdOutcome.NOT_FOUND
            invitee_id = row[0]
            if invitee_id == inviter_id:
                return HouseholdOutcome.SELF
            members = self._lock(inviter_id, invitee_id)
            if inviter_id in members:
                return HouseholdOutcome.IN_HOUSEHOLD
            if invitee_id in members:
                return HouseholdOutcome.PARTNER_IN_HOUSEHOLD
            self.conn.execute(_INVITE, (inviter_id, invitee_id))
            self.conn.execute(_TOUCH, (inviter_id,))
        return HouseholdOutcome.OK

    def cancel(self, inviter_id: int) -> HouseholdOutcome:
        with self.conn.transaction():
            gone = self.conn.execute("DELETE FROM household_invitation WHERE inviter_id = %s", (inviter_id,)).rowcount
            if not gone:
                return HouseholdOutcome.NO_INVITATION
            self.conn.execute(_TOUCH, (inviter_id,))
        return HouseholdOutcome.OK

    def decline(self, invitee_id: int, inviter_id: int) -> HouseholdOutcome:
        with self.conn.transaction():
            if not self.conn.execute(_DROP_INVITATION, (inviter_id, invitee_id)).rowcount:
                return HouseholdOutcome.NO_INVITATION
            self.conn.execute(_TOUCH, (invitee_id,))
        return HouseholdOutcome.OK

    def accept(self, invitee_id: int, inviter_id: int, merge: bool) -> AcceptResult:
        """One transaction: a household of the two, the inviter's blocks shared, the invitee's merged if chosen
        (oldest first, while they fit), both personal shortlists emptied and every invitation of either removed.
        """
        with self.conn.transaction():
            members = self._lock(inviter_id, invitee_id)
            if self.conn.execute(_INVITED, (inviter_id, invitee_id)).fetchone() is None:
                return AcceptResult(HouseholdOutcome.NO_INVITATION)
            if members:
                self.conn.execute(_DROP_INVITATION, (inviter_id, invitee_id))  # it can never be accepted now
                who = HouseholdOutcome.IN_HOUSEHOLD if invitee_id in members else HouseholdOutcome.PARTNER_IN_HOUSEHOLD
                return AcceptResult(who)
            household = self.conn.execute(_NEW_HOUSEHOLD).fetchone()[0]
            self.conn.execute(_JOIN, (inviter_id, household, 1))
            self.conn.execute(_JOIN, (invitee_id, household, 2))
            self.conn.execute(_SHARE_ALL, {"household": household, "buyer": inviter_id})
            params = {"household": household, "buyer": invitee_id}
            new = self.conn.execute(_NEW_TO_SHARE, params).fetchone()[0]
            merged = 0
            if merge:
                room = CAP - self.conn.execute(_SHARED_COUNT, (household,)).fetchone()[0]
                merged = self.conn.execute(_SHARE_MORE, {**params, "room": max(room, 0)}).rowcount
            self.conn.execute(_DROP_PERSONAL, ([inviter_id, invitee_id],))
            self.conn.execute(_DROP_INVITATIONS_OF, {"ids": [inviter_id, invitee_id]})
            self.conn.execute(_TOUCH, (invitee_id,))
        return AcceptResult(HouseholdOutcome.OK, merged, new - merged)

    def leave(self, buyer_id: int) -> HouseholdOutcome:
        """The household ends: the partner keeps the shortlist as their own, and the leaver starts empty (A3)."""
        with self.conn.transaction():
            # Lock both members in id order (as every write here does), then read the household under the lock.
            self._lock(buyer_id, *(r[0] for r in self.conn.execute(_HOUSEMATES, (buyer_id,)).fetchall()))
            row = self.conn.execute(_MY_HOUSEHOLD, (buyer_id,)).fetchone()
            if row is None:
                return HouseholdOutcome.NOT_IN_HOUSEHOLD
            household = row[0]
            partner = self.conn.execute(_PARTNER, (household, buyer_id)).fetchone()
            shared = self.conn.execute(_SHARED_ROWS, (household,)).fetchall()
            self.conn.execute(_DISBAND, (household,))
            if partner is not None:
                with self.conn.cursor() as cur:
                    cur.executemany(_KEEP, [(partner[0], *r) for r in shared])
            self.conn.execute(_TOUCH, (buyer_id,))
        return HouseholdOutcome.OK
