"""Places from PostgreSQL: the block picker and blocks within a radius, one statement each.

Distances are Haversine with R = 6378100 m on double precision casts of the NUMERIC coordinates, as food access
measures them, so both databases measure alike (app/services/food.py). The radius test and the order use the
unrounded distance; ties break on building_id.
"""

from __future__ import annotations

from psycopg import Connection, sql

from app.repos.dto import NearBlock, Place
from app.repos.pg.search import like_escape
from app.services.food import EARTH_RADIUS_M, round_metres

_GET = """
SELECT b.building_id, b.block, b.street, tw.name, b.latitude IS NOT NULL, b.latitude, b.longitude
FROM building b
JOIN town tw ON tw.town_id = b.town_id
WHERE b.building_id = %(building_id)s
"""

_FIND = """
SELECT b.building_id, b.block, b.street, tw.name, b.latitude IS NOT NULL, b.latitude, b.longitude
FROM building b
JOIN town tw ON tw.town_id = b.town_id
WHERE b.street LIKE %(street)s AND (%(block)s::text IS NULL OR b.block = %(block)s)
ORDER BY b.street COLLATE "C", length(b.block), b.block COLLATE "C", b.building_id
LIMIT %(limit)s
"""

# Haversine between the place o and block b (fixed text, no request values); also used by the explore repo.
DISTANCE = f"""2 * {EARTH_RADIUS_M} * asin(sqrt(
    sin((radians(b.latitude::double precision) - radians(o.latitude::double precision)) / 2) ^ 2
    + cos(radians(o.latitude::double precision)) * cos(radians(b.latitude::double precision))
      * sin((radians(b.longitude::double precision) - radians(o.longitude::double precision)) / 2) ^ 2))"""

# One row of NULLs after the place when nothing is near it, and no row at all when the place is missing or
# unlocated, so the caller can tell [] from None.
_NEAR = sql.SQL(
    """
WITH o AS (
    SELECT building_id, latitude, longitude FROM building
    WHERE building_id = %(building_id)s AND latitude IS NOT NULL
)
SELECT n.building_id, n.block, n.street, n.town, n.dist
FROM o
LEFT JOIN LATERAL (
    SELECT b.building_id, b.block, b.street, tw.name AS town, d.dist
    FROM building b
    JOIN town tw ON tw.town_id = b.town_id
    CROSS JOIN LATERAL (SELECT {dist} AS dist) d
    WHERE b.latitude IS NOT NULL AND b.building_id <> o.building_id AND b.residential IS NOT FALSE
      AND d.dist <= %(radius)s
) n ON true
ORDER BY n.dist, n.building_id
"""
).format(dist=sql.SQL(DISTANCE))


class PgPlaceRepo:
    """Built around one idle autocommit connection (docs/conventions.md, section 3)."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def get(self, building_id: int) -> Place | None:
        row = self.conn.execute(_GET, {"building_id": building_id}).fetchone()
        return Place(*row) if row else None

    def find(self, block: str | None, street: str, limit: int) -> list[Place]:
        params = {"street": f"%{like_escape(street)}%", "block": block, "limit": limit}
        return [Place(*r) for r in self.conn.execute(_FIND, params).fetchall()]

    def near(self, building_id: int, radius_m: int) -> list[NearBlock] | None:
        rows = self.conn.execute(_NEAR, {"building_id": building_id, "radius": radius_m}).fetchall()
        if not rows:
            return None
        return [NearBlock(r[0], r[1], r[2], r[3], round_metres(r[4])) for r in rows if r[0] is not None]
