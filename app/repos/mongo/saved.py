"""Saved blocks in MongoDB (app user): the embedded array of the buyer document.

Each write is one atomic single-document `update_one`. The validator's `maxItems: 50` rejects a 51st `$push`
(WriteError code 121). Only the forced-failure demo uses a multi-document transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pymongo.database import Database
from pymongo.errors import WriteError

from app.repos.dto import Outcome, RolledBack, SavedBlock
from app.repos.mongo.accounts import utc

DOCUMENT_VALIDATION_FAILURE = 121
CAP = 50


class _Abort(Exception):
    """Raised inside the transaction callback on purpose (the forced-failure demo) so that it aborts."""

    def __init__(self, result: RolledBack):
        self.result = result


class MongoSavedRepo:
    def __init__(self, db: Database):
        self.db = db

    def list(self, buyer_id: int) -> list[SavedBlock]:
        doc = self.db.buyers.find_one({"_id": buyer_id}, {"saved": 1})
        if doc is None:
            return []
        saved = sorted(doc.get("saved", []), key=lambda s: s["building_id"])
        return [
            SavedBlock(s["building_id"], s["block"], s["street"], s["town"], s.get("note"), utc(s["saved_at"]))
            for s in saved
        ]

    def save(
        self, buyer_id: int, building_id: int, note: str | None, simulate_failure: bool = False
    ) -> Outcome | RolledBack:
        profile = self.db.building_profiles.find_one({"_id": building_id}, {"block": 1, "street": 1, "town.name": 1})
        if profile is None:
            return Outcome.NO_SUCH_BUILDING
        now = datetime.now(UTC)
        # The extended reference keeps what the saved list shows, so listing needs no second collection.
        entry = {
            "building_id": building_id,
            "block": profile["block"],
            "street": profile["street"],
            "town": profile["town"]["name"],
            "note": note,
            "saved_at": now,
        }
        # The $ne guard makes the filter fail for a block that is already saved, before any write or validation.
        flt = {"_id": buyer_id, "saved.building_id": {"$ne": building_id}}
        update = {"$push": {"saved": entry}, "$set": {"last_active_at": now}}
        try:
            if simulate_failure:
                return self._save_then_abort(flt, update, buyer_id)
            result = self.db.buyers.update_one(flt, update)
        except WriteError as exc:
            if exc.code == DOCUMENT_VALIDATION_FAILURE and self._at_cap(buyer_id):
                return Outcome.CAP_REACHED
            raise
        if result.matched_count == 0:
            return self._not_matched(buyer_id)
        return Outcome.OK

    def _save_then_abort(self, flt: dict, update: dict, buyer_id: int) -> Outcome | RolledBack:
        """Demo: run the write in a transaction, count what it matched, then abort the transaction."""

        def work(session):
            result = self.db.buyers.update_one(flt, update, session=session)
            raise _Abort(RolledBack(matched=result.matched_count))

        try:
            with self.db.client.start_session() as session:
                session.with_transaction(work)
        except _Abort as abort:
            if abort.result.matched == 0:
                return self._not_matched(buyer_id)
            return abort.result
        raise AssertionError("the demo transaction must abort")  # pragma: no cover

    def _at_cap(self, buyer_id: int) -> bool:
        doc = self.db.buyers.find_one({"_id": buyer_id}, {"saved.building_id": 1})
        return doc is not None and len(doc.get("saved", [])) >= CAP

    def _not_matched(self, buyer_id: int) -> Outcome:
        """The $ne filter matched nothing: the block is saved, or the buyer does not exist (a fault)."""
        if self.db.buyers.count_documents({"_id": buyer_id}, limit=1) == 0:
            raise LookupError(f"no buyer {buyer_id}")
        return Outcome.ALREADY_SAVED

    def edit_note(self, buyer_id: int, building_id: int, note: str | None) -> Outcome:
        result = self.db.buyers.update_one(
            {"_id": buyer_id, "saved.building_id": building_id},
            {"$set": {"saved.$.note": note, "last_active_at": datetime.now(UTC)}},
        )
        return Outcome.OK if result.matched_count else Outcome.NOT_SAVED

    def remove(self, buyer_id: int, building_id: int) -> Outcome:
        result = self.db.buyers.update_one(
            {"_id": buyer_id, "saved.building_id": building_id},
            {"$pull": {"saved": {"building_id": building_id}}, "$set": {"last_active_at": datetime.now(UTC)}},
        )
        return Outcome.OK if result.matched_count else Outcome.NOT_SAVED
