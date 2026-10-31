"""Shortlists in MongoDB (app user): the embedded array of the buyer document, or of their household's.

A buyer in a household carries its id (`household_id`) and an empty `saved`; the shortlist is the household
document's `saved`. Each write is one atomic single-document `update_one`, plus a separate touch of the buyer's
last active time for a household write. The validators' `maxItems: 50` reject a 51st `$push` (WriteError code
121). A personal write filters on `household_id: null`, so a buyer who joined a household meanwhile matches
nothing and the write is redone against the household. Only the forced-failure demo uses a multi-document
transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pymongo.database import Database
from pymongo.errors import WriteError

from app.repos.dto import Outcome, RolledBack, SavedBlock
from app.repos.mongo.accounts import utc

DOCUMENT_VALIDATION_FAILURE = 121
CAP = 50
PERSONAL = {"household_id": None}  # matches a missing field or null


class _Abort(Exception):
    """Raised inside the transaction callback on purpose (the forced-failure demo) so that it aborts."""

    def __init__(self, result: RolledBack):
        self.result = result


def _block(s: dict) -> SavedBlock:
    return SavedBlock(
        s["building_id"], s["block"], s["street"], s["town"], s.get("note"), utc(s["saved_at"]),
        s.get("saved_by"), s.get("note_by"),
    )  # fmt: skip


class MongoSavedRepo:
    def __init__(self, db: Database):
        self.db = db

    def _household(self, buyer_id: int) -> int | None:
        doc = self.db.buyers.find_one({"_id": buyer_id}, {"household_id": 1})
        if doc is None:
            raise LookupError(f"no buyer {buyer_id}")
        return doc.get("household_id")

    def list(self, buyer_id: int) -> list[SavedBlock]:
        doc = self.db.buyers.find_one({"_id": buyer_id}, {"saved": 1, "household_id": 1})
        if doc is None:
            return []
        if doc.get("household_id") is not None:
            doc = self.db.households.find_one({"_id": doc["household_id"]}, {"saved": 1}) or {}
        return [_block(s) for s in sorted(doc.get("saved", []), key=lambda s: s["building_id"])]

    def save(
        self, buyer_id: int, building_id: int, note: str | None, simulate_failure: bool = False, _retry: bool = True
    ) -> Outcome | RolledBack:
        profile = self.db.building_profiles.find_one({"_id": building_id}, {"block": 1, "street": 1, "town.name": 1})
        if profile is None:
            return Outcome.NO_SUCH_BUILDING
        household = self._household(buyer_id)
        now = datetime.now(UTC)
        # The extended reference keeps what the shortlist shows, so listing needs no second collection.
        entry = {
            "building_id": building_id,
            "block": profile["block"],
            "street": profile["street"],
            "town": profile["town"]["name"],
            "note": note,
            "saved_at": now,
        }
        touch = {"$set": {"last_active_at": now}}
        # The $ne guard makes the filter fail for a block that is already saved, before any write or validation.
        if household is None:
            coll, flt = self.db.buyers, {"_id": buyer_id, **PERSONAL, "saved.building_id": {"$ne": building_id}}
            update = {"$push": {"saved": entry}, **touch}
        else:
            entry |= {"note_by": buyer_id if note is not None else None, "saved_by": buyer_id}
            coll, flt = self.db.households, {"_id": household, "saved.building_id": {"$ne": building_id}}
            update = {"$push": {"saved": entry}}
        try:
            if simulate_failure:
                result = self._save_then_abort(coll, flt, update, buyer_id, touch if household else None)
                if isinstance(result, RolledBack):
                    return result
                matched = 0
            else:
                matched = coll.update_one(flt, update).matched_count
        except WriteError as exc:
            if exc.code == DOCUMENT_VALIDATION_FAILURE and self._at_cap(buyer_id, household):
                return Outcome.CAP_REACHED
            raise
        if matched == 0:
            if _retry and self._household(buyer_id) != household:  # joined or left a household meanwhile
                return self.save(buyer_id, building_id, note, simulate_failure, _retry=False)
            return Outcome.ALREADY_SAVED
        if household is not None:
            self.db.buyers.update_one({"_id": buyer_id}, touch)
        return Outcome.OK

    def _save_then_abort(self, coll, flt: dict, update: dict, buyer_id: int, touch: dict | None):
        """Demo: run the write (and a household member's touch) in a transaction, count what it matched, abort.

        Returns the RolledBack result, or None when the filter matched nothing (the block was already saved).
        """

        def work(session):
            result = coll.update_one(flt, update, session=session)
            if touch is not None and result.matched_count:
                self.db.buyers.update_one({"_id": buyer_id}, touch, session=session)
            raise _Abort(RolledBack(matched=result.matched_count))

        try:
            with self.db.client.start_session() as session:
                session.with_transaction(work)
        except _Abort as abort:
            return abort.result if abort.result.matched else None
        raise AssertionError("the demo transaction must abort")  # pragma: no cover

    def _at_cap(self, buyer_id: int, household: int | None) -> bool:
        if household is None:
            doc = self.db.buyers.find_one({"_id": buyer_id}, {"saved.building_id": 1})
        else:
            doc = self.db.households.find_one({"_id": household}, {"saved.building_id": 1})
        return doc is not None and len(doc.get("saved", [])) >= CAP

    def _change(self, buyer_id: int, building_id: int, personal: dict, shared) -> Outcome:
        """Apply a personal update, or the household's (shared(member) gives it), to the entry for the block."""
        household = self._household(buyer_id)
        now = datetime.now(UTC)
        if household is None:
            flt = {"_id": buyer_id, **PERSONAL, "saved.building_id": building_id}
            result = self.db.buyers.update_one(
                flt, {**personal, "$set": {**personal.get("$set", {}), "last_active_at": now}}
            )
        else:
            result = self.db.households.update_one({"_id": household, "saved.building_id": building_id}, shared)
            if result.matched_count:
                self.db.buyers.update_one({"_id": buyer_id}, {"$set": {"last_active_at": now}})
        return Outcome.OK if result.matched_count else Outcome.NOT_SAVED

    def edit_note(self, buyer_id: int, building_id: int, note: str | None) -> Outcome:
        shared = {"$set": {"saved.$.note": note, "saved.$.note_by": buyer_id if note is not None else None}}
        return self._change(buyer_id, building_id, {"$set": {"saved.$.note": note}}, shared)

    def remove(self, buyer_id: int, building_id: int) -> Outcome:
        pull = {"$pull": {"saved": {"building_id": building_id}}}
        return self._change(buyer_id, building_id, pull, pull)
