"""Accounts in MongoDB (app user): reads, registration and last-active updates."""

from __future__ import annotations

from datetime import UTC, datetime

from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.repos.dto import Account, Duplicate


def utc(value: datetime | None) -> datetime | None:
    """Aware UTC, whether or not the client was created with tz_aware=True."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _account(doc: dict) -> Account:
    return Account(
        doc["_id"],
        doc["email"],
        doc.get("display_name"),
        doc["is_admin"],
        utc(doc["created_at"]),
        utc(doc.get("last_active_at")),
        doc["password_hash"],
    )


class MongoAccountRepo:
    def __init__(self, db: Database):
        self.db = db

    def by_id(self, buyer_id: int) -> Account | None:
        doc = self.db.buyers.find_one({"_id": buyer_id}, {"saved": 0})
        return None if doc is None else _account(doc)

    def by_email(self, email: str) -> Account | None:
        doc = self.db.buyers.find_one({"email": email.lower()}, {"saved": 0})
        return None if doc is None else _account(doc)

    def create(self, email: str, password_hash: str, display_name: str | None) -> int | Duplicate:
        counter = self.db.counters.find_one_and_update(
            {"_id": "buyer"}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER
        )
        buyer_id = counter["seq"]
        try:
            self.db.buyers.insert_one(
                {
                    "_id": buyer_id,
                    "email": email.lower(),
                    "password_hash": password_hash,
                    "display_name": display_name,
                    "is_admin": False,
                    "created_at": datetime.now(UTC),
                    "last_active_at": None,
                    "saved": [],
                }
            )
        except DuplicateKeyError as exc:
            # Only the email index means "already registered"; anything else (an _id clash) is a real fault.
            if "email" in (exc.details or {}).get("keyPattern", {}):
                return Duplicate()
            raise
        return buyer_id

    def touch(self, buyer_id: int) -> None:
        self.db.buyers.update_one({"_id": buyer_id}, {"$set": {"last_active_at": datetime.now(UTC)}})
