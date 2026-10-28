"""Food access: shared constants and the town query model for the PostgreSQL and the MongoDB repo.

Both repos exclude the same status, show the same number of centres and count the same radius
(docs/conventions.md, 8: the town is validated against the fixed list before any query).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from etl.clean import TOWN_IDS

EXCLUDED_STATUS = "Under Construction"
NEAREST_COUNT = 5  # centres shown on the building page
RADIUS_M = 1000  # the "within 1 km" test, inclusive
EARTH_RADIUS_M = 6378100  # MongoDB's spherical radius (6378.1 km), so both databases measure alike
FIELDS = ("town",)
LABELS = {"town": "Town"}
_TENTH = Decimal("0.1")


def round_metres(value: float) -> Decimal:
    """Distance to 0.1 m from the unrounded float; both repos use this, so rounding is not a difference."""
    return Decimal(repr(value)).quantize(_TENTH, ROUND_HALF_UP)


class FoodQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    town: str

    @model_validator(mode="before")
    @classmethod
    def _blank_is_absent(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not (isinstance(v, str) and not v.strip())}
        return data

    @field_validator("town", mode="before")
    @classmethod
    def _town(cls, v):
        if v.strip() not in TOWN_IDS:
            raise ValueError("Town must be one of the listed towns.")
        return v.strip()


def error_messages(exc: ValidationError) -> list[str]:
    out = []
    for e in exc.errors():
        if e["type"] == "missing":
            out.append(f"{LABELS[e['loc'][0]]} is required.")
        else:
            out.append(e["msg"].removeprefix("Value error, "))
    return out
