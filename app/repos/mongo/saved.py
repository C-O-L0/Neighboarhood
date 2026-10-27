"""Saved blocks from MongoDB: the embedded array of the buyer document (read-only; task 3.2 adds writes)."""

from __future__ import annotations

from pymongo.database import Database

from app.repos.dto import SavedBlock
from app.repos.mongo.accounts import utc


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
