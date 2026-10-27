"""Accounts from PostgreSQL (read-only; task 3.1b adds writes)."""

from __future__ import annotations

from datetime import UTC

from psycopg import Connection

from app.repos.dto import Account

_BY_ID = """
SELECT buyer_id, email, display_name, is_admin, created_at, last_active_at, password_hash
FROM buyer
WHERE buyer_id = %s
"""


class PgAccountRepo:
    """Built around one connection (docs/conventions.md, section 3)."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def by_id(self, buyer_id: int) -> Account | None:
        row = self.conn.execute(_BY_ID, (buyer_id,)).fetchone()
        if row is None:
            return None
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
