"""Accounts from MongoDB (read-only; task 3.1b adds writes)."""

from __future__ import annotations

from datetime import UTC, datetime

from pymongo.database import Database

from app.repos.dto import Account


def utc(value: datetime | None) -> datetime | None:
    """Aware UTC, whether or not the client was created with tz_aware=True."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class MongoAccountRepo:
    def __init__(self, db: Database):
        self.db = db

    def by_id(self, buyer_id: int) -> Account | None:
        doc = self.db.buyers.find_one({"_id": buyer_id}, {"saved": 0})
        if doc is None:
            return None
        return Account(
            doc["_id"],
            doc["email"],
            doc.get("display_name"),
            doc["is_admin"],
            utc(doc["created_at"]),
            utc(doc.get("last_active_at")),
            doc["password_hash"],
        )
