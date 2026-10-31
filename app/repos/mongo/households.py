"""Households in MongoDB (app user): invitations, accepting, leaving, and reading a buyer's household (UC10).

A household document embeds its two members and the shared shortlist; each member's buyer document holds the
household's id (`household_id`) and an empty `saved`. An invitation's `_id` is the inviter's buyer id, so a
buyer has one outstanding invitation, as in PostgreSQL.

Accepting and leaving change several documents, so each runs in a multi-document transaction
(`with_transaction`). Both write the buyer documents involved, so two transactions about the same buyer
conflict and the driver retries the later one, which then reads the new state. The unique multikey index on
`members.buyer_id` is the backstop: a buyer id can never sit in two households.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.repos.dto import (
    AcceptResult,
    Household,
    HouseholdOutcome,
    Invitation,
    Invitations,
    Member,
    Person,
    ShortlistSummary,
)
from app.repos.mongo.accounts import utc

CAP = 50
_PEOPLE = {"email": 1, "display_name": 1}


def shared_entry(entry: dict, buyer_id: int) -> dict:
    """A personal shortlist entry as it joins a household's: whoever saved it wrote its note."""
    return {**entry, "note_by": buyer_id if entry.get("note") is not None else None, "saved_by": buyer_id}


def personal_entry(entry: dict) -> dict:
    """A household entry as it returns to a personal shortlist, which records no authors."""
    return {k: v for k, v in entry.items() if k not in ("note_by", "saved_by")}


class MongoHouseholdRepo:
    """Built around one Database (docs/conventions.md, section 3)."""

    def __init__(self, db: Database):
        self.db = db

    # ---------------------------------------------------------------- reads

    def _people(self, ids, session=None) -> dict[int, Person]:
        docs = self.db.buyers.find({"_id": {"$in": list(ids)}}, _PEOPLE, session=session)
        return {d["_id"]: Person(d["_id"], d["email"], d.get("display_name")) for d in docs}

    def household(self, buyer_id: int) -> Household | None:
        buyer = self.db.buyers.find_one({"_id": buyer_id}, {"household_id": 1})
        if buyer is None or buyer.get("household_id") is None:
            return None
        doc = self.db.households.find_one({"_id": buyer["household_id"]}, {"members": 1, "saved.building_id": 1})
        if doc is None:
            return None
        people = self._people(m["buyer_id"] for m in doc["members"])
        members = sorted(
            (Member(m["buyer_id"], people[m["buyer_id"]].email, people[m["buyer_id"]].display_name, m["seat"])
             for m in doc["members"]),
            key=lambda m: m.seat,
        )  # fmt: skip
        return Household(doc["_id"], members, len(doc.get("saved", [])))

    def invitations(self, buyer_id: int) -> Invitations:
        docs = list(self.db.invitations.find({"$or": [{"_id": buyer_id}, {"invitee_id": buyer_id}]}))
        people = self._people({d["_id"] for d in docs} | {d["invitee_id"] for d in docs})
        found = sorted(
            (Invitation(people[d["_id"]], people[d["invitee_id"]], utc(d["created_at"])) for d in docs),
            key=lambda i: (i.created_at, i.inviter.buyer_id),
        )
        sent = next((i for i in found if i.inviter.buyer_id == buyer_id), None)
        return Invitations(sent, [i for i in found if i.inviter.buyer_id != buyer_id])

    def summary(self, buyer_id: int) -> ShortlistSummary:
        buyer = self.db.buyers.find_one({"_id": buyer_id}, {"household_id": 1, "count": {"$size": "$saved"}})
        if buyer is None:
            return ShortlistSummary(0, 0)
        count = buyer["count"]
        if buyer.get("household_id") is not None:
            shared = self.db.households.find_one({"_id": buyer["household_id"]}, {"count": {"$size": "$saved"}})
            count = shared["count"] if shared else 0
        return ShortlistSummary(count, self.db.invitations.count_documents({"invitee_id": buyer_id}))

    # ---------------------------------------------------------------- writes

    def _touch(self, buyer_id: int, now: datetime, session=None) -> None:
        self.db.buyers.update_one({"_id": buyer_id}, {"$set": {"last_active_at": now}}, session=session)

    def invite(self, inviter_id: int, email: str) -> HouseholdOutcome:
        """Invite the account with this email (stored lower-cased); a new invitation replaces the inviter's last."""
        invitee = self.db.buyers.find_one({"email": email.strip().lower()}, {"household_id": 1})
        if invitee is None:
            return HouseholdOutcome.NOT_FOUND
        if invitee["_id"] == inviter_id:
            return HouseholdOutcome.SELF
        inviter = self.db.buyers.find_one({"_id": inviter_id}, {"household_id": 1})
        if inviter is None:
            raise LookupError(f"no buyer {inviter_id}")
        if inviter.get("household_id") is not None:
            return HouseholdOutcome.IN_HOUSEHOLD
        if invitee.get("household_id") is not None:
            return HouseholdOutcome.PARTNER_IN_HOUSEHOLD
        now = datetime.now(UTC)
        doc = {"_id": inviter_id, "invitee_id": invitee["_id"], "created_at": now}
        self.db.invitations.replace_one({"_id": inviter_id}, doc, upsert=True)
        self._touch(inviter_id, now)
        return HouseholdOutcome.OK

    def cancel(self, inviter_id: int) -> HouseholdOutcome:
        if not self.db.invitations.delete_one({"_id": inviter_id}).deleted_count:
            return HouseholdOutcome.NO_INVITATION
        self._touch(inviter_id, datetime.now(UTC))
        return HouseholdOutcome.OK

    def decline(self, invitee_id: int, inviter_id: int) -> HouseholdOutcome:
        if not self.db.invitations.delete_one({"_id": inviter_id, "invitee_id": invitee_id}).deleted_count:
            return HouseholdOutcome.NO_INVITATION
        self._touch(invitee_id, datetime.now(UTC))
        return HouseholdOutcome.OK

    def accept(self, invitee_id: int, inviter_id: int, merge: bool) -> AcceptResult:
        """One transaction: a household of the two, the inviter's blocks shared, the invitee's merged if chosen
        (oldest first, while they fit), both personal shortlists emptied and every invitation of either removed.
        """
        pair = [inviter_id, invitee_id]

        def work(s) -> AcceptResult:
            if self.db.invitations.find_one({"_id": inviter_id, "invitee_id": invitee_id}, session=s) is None:
                return AcceptResult(HouseholdOutcome.NO_INVITATION)
            found = self.db.buyers.find({"_id": {"$in": pair}}, {"saved": 1, "household_id": 1}, session=s)
            buyers = {d["_id"]: d for d in found}
            inviter, invitee = buyers[inviter_id], buyers[invitee_id]
            if invitee.get("household_id") is not None or inviter.get("household_id") is not None:
                self.db.invitations.delete_one({"_id": inviter_id, "invitee_id": invitee_id}, session=s)
                taken = invitee.get("household_id") is not None
                return AcceptResult(HouseholdOutcome.IN_HOUSEHOLD if taken else HouseholdOutcome.PARTNER_IN_HOUSEHOLD)
            now = datetime.now(UTC)
            counter = self.db.counters.find_one_and_update(
                {"_id": "household"}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER, session=s
            )
            shared = [shared_entry(e, inviter_id) for e in inviter.get("saved", [])]
            have = {e["building_id"] for e in shared}
            new = sorted(
                (e for e in invitee.get("saved", []) if e["building_id"] not in have),
                key=lambda e: (e["saved_at"], e["building_id"]),
            )
            merged = new[: max(CAP - len(shared), 0)] if merge else []
            members = [
                {"buyer_id": inviter_id, "seat": 1, "joined_at": now},
                {"buyer_id": invitee_id, "seat": 2, "joined_at": now},
            ]
            saved = shared + [shared_entry(e, invitee_id) for e in merged]
            self.db.households.insert_one(
                {"_id": counter["seq"], "created_at": now, "members": members, "saved": saved}, session=s
            )
            joined = {"saved": [], "household_id": counter["seq"]}
            self.db.buyers.update_one({"_id": inviter_id}, {"$set": joined}, session=s)
            self.db.buyers.update_one({"_id": invitee_id}, {"$set": {**joined, "last_active_at": now}}, session=s)
            self.db.invitations.delete_many({"$or": [{"_id": {"$in": pair}}, {"invitee_id": {"$in": pair}}]}, session=s)
            return AcceptResult(HouseholdOutcome.OK, len(merged), len(new) - len(merged))

        try:
            with self.db.client.start_session() as session:
                return session.with_transaction(work)
        except DuplicateKeyError:  # member_unique: one of the two joined another household meanwhile
            return self.accept(invitee_id, inviter_id, merge)

    def leave(self, buyer_id: int) -> HouseholdOutcome:
        """The household ends: the partner keeps the shortlist as their own, and the leaver starts empty (A3)."""

        def work(s) -> HouseholdOutcome:
            buyer = self.db.buyers.find_one({"_id": buyer_id}, {"household_id": 1}, session=s)
            if buyer is None or buyer.get("household_id") is None:
                return HouseholdOutcome.NOT_IN_HOUSEHOLD
            household = self.db.households.find_one_and_delete({"_id": buyer["household_id"]}, session=s)
            now = datetime.now(UTC)
            self.db.buyers.update_one(
                {"_id": buyer_id}, {"$set": {"household_id": None, "saved": [], "last_active_at": now}}, session=s
            )
            for m in household["members"] if household else []:
                if m["buyer_id"] != buyer_id:
                    kept = [personal_entry(e) for e in household["saved"]]
                    self.db.buyers.update_one(
                        {"_id": m["buyer_id"]}, {"$set": {"household_id": None, "saved": kept}}, session=s
                    )
            return HouseholdOutcome.OK

        with self.db.client.start_session() as session:
            return session.with_transaction(work)
