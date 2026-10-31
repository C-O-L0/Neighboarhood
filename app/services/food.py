"""Food access: shared constants for the PostgreSQL and the MongoDB repo.

Both repos exclude the same status, show the same number of centres and count the same radius.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

EXCLUDED_STATUS = "Under Construction"
NEAREST_COUNT = 5  # centres shown on the building page
RADIUS_M = 1000  # the "within 1 km" test, inclusive
EARTH_RADIUS_M = 6378100  # MongoDB's spherical radius (6378.1 km), so both databases measure alike
_TENTH = Decimal("0.1")


def round_metres(value: float) -> Decimal:
    """Distance to 0.1 m from the unrounded float; both repos use this, so rounding is not a difference."""
    return Decimal(repr(value)).quantize(_TENTH, ROUND_HALF_UP)
