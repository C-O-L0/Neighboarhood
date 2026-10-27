"""Saved blocks from PostgreSQL (read-only; task 3.2 adds writes)."""

from __future__ import annotations

from datetime import UTC

from psycopg import Connection

from app.repos.dto import SavedBlock

_LIST = """
SELECT s.building_id, b.block, b.street, tw.name, s.note, s.saved_at
FROM saved_building s
JOIN building b ON b.building_id = s.building_id
JOIN town tw ON tw.town_id = b.town_id
WHERE s.buyer_id = %s
ORDER BY s.building_id
"""


class PgSavedRepo:
    def __init__(self, conn: Connection):
        self.conn = conn

    def list(self, buyer_id: int) -> list[SavedBlock]:
        rows = self.conn.execute(_LIST, (buyer_id,)).fetchall()
        return [SavedBlock(r[0], r[1], r[2], r[3], r[4], r[5].astimezone(UTC)) for r in rows]
