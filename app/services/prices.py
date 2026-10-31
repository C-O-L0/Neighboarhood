"""Is an asking price fair (UC2): the query model, the comparable levels and the verdict.

Comparables are past sales of the same flat type in the last 24 months up to the data month (and in the same
storey band when one is given): first in the same block, then on the same street in the same town, then in the
whole town. The first level with at least MIN_COMPARABLES sales is used (UC2 A1). When no level has that many,
the town's sales are used if there are any, flagged as few; with none at all there is no fair comparison (A2).

The repos only find the sales; everything here is worked out in Python from them. Price per sqm is compared by
cross-multiplying (price * area against asking price * sale area), so no rounded ratio decides a comparison and
both databases give the same verdict.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from app.repos.dto import Comparable, TownBaseline
from app.services.explore import MAX_AREA, MAX_BUDGET, MAX_ID, Window, parse_money
from app.services.search import RESALE_TYPES
from app.services.trends import add_months

WINDOW_MONTHS = 24
MIN_COMPARABLES = 5
SHOWN = 20  # comparables listed on the page; the verdict uses all of them
TOP_STOREY = 51  # the highest band offered is 49 to 51
STOREY_BANDS = tuple((lo, lo + 2) for lo in range(1, TOP_STOREY, 3))  # HDB's 3-storey bands since 2017
FIELDS = ("place_text", "place", "flat_type", "storey", "area", "price")
LABELS = {
    "place_text": "Block and street",
    "place": "Block",
    "flat_type": "Flat type",
    "storey": "Storey",
    "area": "Floor area",
    "price": "Asking price",
}
_STOREY = re.compile(r"(\d{1,2})-(\d{1,2})", re.ASCII)
_AREA = re.compile(r"\d{1,3}(\.\d)?", re.ASCII)  # square metres, as HDB records them (one decimal place)
_CENT = Decimal("0.01")


class Level(StrEnum):
    BLOCK = "block"
    STREET = "street"
    TOWN = "town"


def window_ending(latest) -> Window:
    return Window(start=add_months(latest, -(WINDOW_MONTHS - 1)), end=latest)


def storey_value(band: tuple[int, int]) -> str:
    return f"{band[0]:02d}-{band[1]:02d}"


@dataclass(frozen=True)
class Scope:
    """Which sales a repo returns: one level around a block, for one flat type, window and optional storey band."""

    level: Level
    building_id: int
    street: str
    town: str
    flat_type: str
    window: Window
    storey_min: int | None = None


class PriceQuery(BaseModel):
    """What the buyer typed. place is a block id from a previous choice; place_text is matched otherwise."""

    model_config = ConfigDict(frozen=True)

    place_text: str | None = None
    place: int | None = None
    flat_type: str
    storey: tuple[int, int] | None = None
    area: Decimal
    price: Decimal

    @model_validator(mode="before")
    @classmethod
    def _blank_is_absent(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not (isinstance(v, str) and not v.strip())}
        return data

    @model_validator(mode="after")
    def _a_block(self):
        if self.place is None and self.place_text is None:
            raise ValueError("Enter the block and street, such as 186 Bishan St 13.")
        return self

    @field_validator("place", mode="before")
    @classmethod
    def _place(cls, v):
        if isinstance(v, str) and v.strip().isascii() and v.strip().isdigit() and len(v.strip()) <= 10:
            v = int(v.strip())
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= MAX_ID:
            raise ValueError("Choose the block from the list.")
        return v

    @field_validator("flat_type", mode="before")
    @classmethod
    def _flat_type(cls, v):
        if not isinstance(v, str) or v.strip() not in RESALE_TYPES:
            raise ValueError("Flat type must be one of the listed flat types.")
        return v.strip()

    @field_validator("storey", mode="before")
    @classmethod
    def _storey(cls, v):
        m = _STOREY.fullmatch(v.strip()) if isinstance(v, str) else None
        band = (int(m[1]), int(m[2])) if m else None
        if band not in STOREY_BANDS:
            raise ValueError("Storey must be one of the listed storey bands.")
        return band

    @field_validator("area", mode="before")
    @classmethod
    def _area(cls, v):
        area = Decimal(v.strip()) if isinstance(v, str) and _AREA.fullmatch(v.strip()) else v
        if not isinstance(area, Decimal) or not Decimal(10) <= area <= MAX_AREA:
            raise ValueError("Floor area must be a number of square metres from 10 to 300, such as 93 or 92.5.")
        return area

    @field_validator("price", mode="before")
    @classmethod
    def _price(cls, v):
        amount = parse_money(v) if isinstance(v, str) else v if isinstance(v, Decimal) else None
        if amount is None or not Decimal(1000) <= amount <= MAX_BUDGET:
            raise ValueError("Asking price must be an amount in dollars such as 650,000.")
        return amount


def error_messages(exc: ValidationError) -> list[str]:
    out = []
    for e in exc.errors():
        if e["type"] == "missing":
            out.append(f"{LABELS[e['loc'][0]]} is required.")
        else:
            out.append(e["msg"].removeprefix("Value error, "))
    return out


def lower_median(values: list):
    """percentile_disc(0.5): the sorted value at position ceil(n / 2), counting from 1."""
    ordered = sorted(values)
    return ordered[max(math.ceil(len(ordered) / 2), 1) - 1]


@dataclass(frozen=True)
class Verdict:
    """The asking price against its comparables, by price per sqm."""

    n: int
    below: int  # comparables with a lower price per sqm than the asking price
    above: int  # comparables with a higher one
    asking_psm: Decimal
    low: Decimal  # lowest, typical (lower median) and highest comparable price per sqm, to the cent
    median: Decimal
    high: Decimal

    @property
    def share_below(self) -> int:
        return round(100 * self.below / self.n)

    @property
    def share_above(self) -> int:
        return round(100 * self.above / self.n)

    def position(self, value: Decimal) -> float:
        """Where a price per sqm sits on the range bar, 0 to 100 (clamped: an asking price can lie outside)."""
        if self.high == self.low:
            return 50.0
        return max(0.0, min(100.0, float((value - self.low) / (self.high - self.low) * 100)))


def verdict(sales: list[Comparable], area: Decimal, price: Decimal) -> Verdict:
    """Needs at least one sale. A comparable is cheaper per sqm when price * area < asking price * its area."""
    below = sum(1 for s in sales if s.resale_price * area < price * s.floor_area_sqm)
    above = sum(1 for s in sales if s.resale_price * area > price * s.floor_area_sqm)
    by_psm = sorted(sales, key=lambda s: (s.resale_price / s.floor_area_sqm, s.txn_id))
    typical = by_psm[max(math.ceil(len(by_psm) / 2), 1) - 1]
    return Verdict(
        n=len(sales),
        below=below,
        above=above,
        asking_psm=(price / area).quantize(_CENT, ROUND_HALF_UP),
        low=by_psm[0].price_psm,
        median=typical.price_psm,
        high=by_psm[-1].price_psm,
    )


def baseline(sales: list[Comparable], label: str) -> TownBaseline:
    """The comparables' typical storey band, lease start year and floor area, for explainer tags."""
    leases = [s.lease_year for s in sales if s.lease_year is not None]
    return TownBaseline(
        town=label,
        n=len(sales),
        storey_sum=lower_median([s.storey_min + s.storey_max for s in sales]),
        lease_year=lower_median(leases) if leases else None,
        area_median=lower_median([s.floor_area_sqm for s in sales]),
    )
