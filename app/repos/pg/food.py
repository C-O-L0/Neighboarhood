"""Food access from PostgreSQL: Haversine in SQL on hawker_centre, one statement per view.

The distance is the great-circle distance with R = 6378100 m (MongoDB's radius) on double precision
casts of the NUMERIC coordinates. Sorting and the 1 km test use the unrounded distance; ties break
on hawker_id (block view) and building_id (town view). Statuses are excluded by a bound parameter.
"""

from __future__ import annotations

from psycopg import Connection, sql

from app.repos.dto import BlockFoodAccess, HawkerDistance
from app.services.food import EARTH_RADIUS_M, EXCLUDED_STATUS, NEAREST_COUNT, RADIUS_M, round_metres

# Haversine between hawker h and block b (b is the outer row; fixed text, no request values).
_DIST = f"""2 * {EARTH_RADIUS_M} * asin(sqrt(
    sin((radians(h.latitude::double precision) - radians(b.latitude::double precision)) / 2) ^ 2
    + cos(radians(b.latitude::double precision)) * cos(radians(h.latitude::double precision))
      * sin((radians(h.longitude::double precision) - radians(b.longitude::double precision)) / 2) ^ 2))"""

_COLUMNS_SQL = sql.SQL("h.hawker_id, h.name, h.address, h.status, h.stall_count")
_DIST_SQL = sql.SQL(_DIST)
_RADIUS_SQL = sql.SQL(str(RADIUS_M))
_FIVE_SQL = sql.SQL(str(NEAREST_COUNT))
_ONE_SQL = sql.SQL("1")

_BLOCK = sql.SQL(
    """
SELECT b.building_id, b.block, b.street,
       n.hawker_id, n.name, n.address, n.status, n.stall_count, n.dist, c.within_1km
FROM building b
CROSS JOIN LATERAL (
    SELECT count(*)::int AS within_1km
    FROM hawker_centre h
    WHERE h.status <> %(status)s AND {dist} <= {radius}
) c
LEFT JOIN LATERAL (
    SELECT {columns}, {dist} AS dist
    FROM hawker_centre h
    WHERE h.status <> %(status)s
    ORDER BY dist, h.hawker_id
    LIMIT {limit}
) n ON true
WHERE b.building_id = %(building_id)s AND b.latitude IS NOT NULL
ORDER BY n.dist, n.hawker_id
"""
).format(dist=_DIST_SQL, columns=_COLUMNS_SQL, radius=_RADIUS_SQL, limit=_FIVE_SQL)

_TOWN = sql.SQL(
    """
SELECT b.building_id, b.block, b.street,
       n.hawker_id, n.name, n.address, n.status, n.stall_count, n.dist, c.within_1km
FROM building b
JOIN town tw ON tw.town_id = b.town_id
CROSS JOIN LATERAL (
    SELECT {columns}, {dist} AS dist
    FROM hawker_centre h
    WHERE h.status <> %(status)s
    ORDER BY dist, h.hawker_id
    LIMIT {limit}
) n
CROSS JOIN LATERAL (
    SELECT count(*)::int AS within_1km
    FROM hawker_centre h
    WHERE h.status <> %(status)s AND {dist} <= {radius}
) c
WHERE tw.name = %(town)s AND b.latitude IS NOT NULL
ORDER BY n.dist, b.building_id
"""
).format(dist=_DIST_SQL, columns=_COLUMNS_SQL, radius=_RADIUS_SQL, limit=_ONE_SQL)


def _hawker(row) -> HawkerDistance:
    return HawkerDistance(row[3], row[4], row[5], row[6], row[7], round_metres(row[8]))


class PgFoodRepo:
    """Built around one idle autocommit connection (docs/conventions.md, section 3)."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def block(self, building_id: int) -> BlockFoodAccess | None:
        """The block's 5 nearest open centres and the number within 1 km; None if it has no coordinates."""
        params = {"building_id": building_id, "status": EXCLUDED_STATUS}
        rows = self.conn.execute(_BLOCK, params).fetchall()  # one statement: one snapshot
        if not rows:
            return None
        first = rows[0]
        nearest = [_hawker(r) for r in rows if r[3] is not None]
        return BlockFoodAccess(first[0], first[1], first[2], nearest, first[9])

    def town(self, town: str) -> list[BlockFoodAccess]:
        """Every geocoded block of the town with its nearest open centre, nearest first then building id."""
        rows = self.conn.execute(_TOWN, {"town": town, "status": EXCLUDED_STATUS}).fetchall()
        return [BlockFoodAccess(r[0], r[1], r[2], [_hawker(r)], r[9]) for r in rows]
