"""Accounts in PostgreSQL (app_rw): reads, registration and last-active updates."""

from __future__ import annotations

from datetime import UTC

from psycopg import Connection
from psycopg.errors import UniqueViolation

from app.repos.dto import Account, Duplicate

_BY_ID = """
SELECT buyer_id, email, display_name, is_admin, created_at, last_active_at, password_hash
FROM buyer
WHERE buyer_id = %s
"""
# lower(email) matches the unique index buyer_email_lower_key
_BY_EMAIL = """
SELECT buyer_id, email, display_name, is_admin, created_at, last_active_at, password_hash
FROM buyer
WHERE lower(email) = %s
"""
_CREATE = "INSERT INTO buyer (email, password_hash, display_name) VALUES (%s, %s, %s) RETURNING buyer_id"
_TOUCH = "UPDATE buyer SET last_active_at = now() WHERE buyer_id = %s"


def _account(row) -> Account:
    bid, email, name, admin, created, active, pw = row
    return Account(
        bid,
        email,
        name,
        admin,
        created.astimezone(UTC),
        None if active is None else active.astimezone(UTC),
        pw,
    )


class PgAccountRepo:
    """Built around one autocommit connection (docs/conventions.md, section 3)."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def by_id(self, buyer_id: int) -> Account | None:
        row = self.conn.execute(_BY_ID, (buyer_id,)).fetchone()
        return None if row is None else _account(row)

    def by_email(self, email: str) -> Account | None:
        row = self.conn.execute(_BY_EMAIL, (email.lower(),)).fetchone()
        return None if row is None else _account(row)

    def create(self, email: str, password_hash: str, display_name: str | None) -> int | Duplicate:
        try:
            row = self.conn.execute(_CREATE, (email.lower(), password_hash, display_name)).fetchone()
        except UniqueViolation:
            return Duplicate()
        return row[0]

    def touch(self, buyer_id: int) -> None:
        self.conn.execute(_TOUCH, (buyer_id,))
