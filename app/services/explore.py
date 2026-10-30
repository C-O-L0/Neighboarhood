"""Where a buyer can afford a flat type (UC1): the recent window, the query model and the best-fit rule.

"Recent" is the last 12 registration months in the loaded data, ending at the latest month present, not
today's date (USE_CASES R4). The window is worked out here once, from the latest month, and passed to both
repos, so the PostgreSQL and MongoDB queries filter on the same two dates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from app.repos.dto import TownFit
from app.services.places import RADII_M, parse_radius
from app.services.search import RESALE_TYPES
from app.services.trends import add_months
from etl.clean import TOWN_IDS

WINDOW_MONTHS = 12
FEW_SALES = 5  # a typical price from fewer recent sales is flagged as less reliable (R3)
CLOSEST_TOWNS = 3  # shown when no town fits the budget (UC1 A1)
MAX_BUDGET = Decimal("10000000")
PAGE_SIZE = 20  # blocks per page of results
MAX_PAGE = 10000
LEASE_YEARS = 99  # every HDB lease
MAX_AREA = Decimal("300")
MAX_ID = 2**31 - 1  # building ids are PostgreSQL INT
FIELDS = ("flat_type", "budget", "town", "place", "radius", "min_lease", "min_area", "page")
LABELS = {
    "flat_type": "Flat type",
    "budget": "Budget",
    "town": "Town",
    "place": "Place",
    "radius": "Distance",
    "min_lease": "Lease left",
    "min_area": "Floor area",
    "page": "Page",
}

# "650000", "650,000", "$650,000", "650,000.50"; commas only between groups of three digits.
_MONEY = re.compile(r"\$?\s*(\d{1,3}(,\d{3})+|\d+)(\.\d{1,2})?", re.ASCII)
_AREA = re.compile(r"\d{1,3}(\.\d)?", re.ASCII)  # square metres, as HDB records them (one decimal place)


@dataclass(frozen=True)
class Window:
    """The first and last registration month of the recent window, both inclusive (day 1)."""

    start: date
    end: date


def window_ending(latest: date) -> Window:
    return Window(start=add_months(latest, -(WINDOW_MONTHS - 1)), end=latest)


def parse_money(text: str) -> Decimal | None:
    """A positive amount typed by a person, or None when it is not one."""
    text = text.strip()
    if not _MONEY.fullmatch(text):
        return None
    try:
        return Decimal(text.lstrip("$").strip().replace(",", ""))
    except InvalidOperation:
        return None


class FitQuery(BaseModel):
    """A flat type and a budget. The flat type is required: a typical price over every type means little."""

    model_config = ConfigDict(frozen=True)

    flat_type: str
    budget: Decimal

    @model_validator(mode="before")
    @classmethod
    def _blank_is_absent(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not (isinstance(v, str) and not v.strip())}
        return data

    @field_validator("flat_type", mode="before")
    @classmethod
    def _flat_type(cls, v):
        if not isinstance(v, str) or v.strip() not in RESALE_TYPES:
            raise ValueError("Flat type must be one of the listed flat types.")
        return v.strip()

    @field_validator("budget", mode="before")
    @classmethod
    def _budget(cls, v):
        amount = parse_money(v) if isinstance(v, str) else v if isinstance(v, Decimal) else None
        if amount is None or not Decimal(0) < amount <= MAX_BUDGET:
            raise ValueError("Budget must be an amount in dollars such as 650,000.")
        return amount


def error_messages(exc: ValidationError) -> list[str]:
    out = []
    for e in exc.errors():
        if e["type"] == "missing":
            out.append(f"{LABELS[e['loc'][0]]} is required.")
        else:
            out.append(e["msg"].removeprefix("Value error, "))
    return out


def fits(town: TownFit, budget: Decimal) -> bool:
    return town.median_price <= budget


def best_fit(towns: list[TownFit], budget: Decimal) -> TownFit | None:
    """The town whose sales were most often within budget, among towns whose typical price fits.

    Ties go to the lower typical price, then the town name. None when no town fits.
    """
    fitting = [t for t in towns if fits(t, budget)]
    if not fitting:
        return None
    return min(fitting, key=lambda t: (-t.within_budget / t.n, t.median_price, t.town))


def closest(towns: list[TownFit], budget: Decimal) -> list[TownFit]:
    """When nothing fits: the towns least over budget (repos return towns cheapest first)."""
    return [t for t in towns if not fits(t, budget)][:CLOSEST_TOWNS]


def lease_left_years(lease_year: int, on: date) -> int:
    """Whole years of lease left in the year of `on`. Only the lease start year is known, so this is approximate."""
    return lease_year + LEASE_YEARS - on.year


class BlocksQuery(FitQuery):
    """Blocks whose typical recent price for the flat type is within budget (UC1 step 6, UC9).

    place is a building id chosen in the place picker; radius (metres) applies only with it. min_lease is the
    least lease left in whole years, min_area the least typical floor area in sqm.
    """

    town: str | None = None
    place: int | None = None
    radius: int = 2000
    min_lease: int | None = None
    min_area: Decimal | None = None
    page: int = 1

    @field_validator("town", mode="before")
    @classmethod
    def _town(cls, v):
        if not isinstance(v, str) or v.strip() not in TOWN_IDS:
            raise ValueError("Town must be one of the listed towns.")
        return v.strip()

    @field_validator("place", mode="before")
    @classmethod
    def _place(cls, v):
        if isinstance(v, str) and v.strip().isascii() and v.strip().isdigit() and len(v.strip()) <= 10:
            v = int(v.strip())
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= MAX_ID:
            raise ValueError("Choose the place from the list of blocks.")
        return v

    @field_validator("radius", mode="before")
    @classmethod
    def _radius(cls, v):
        r = parse_radius(v) if isinstance(v, str) else v if v in RADII_M else None
        if r is None:
            raise ValueError("Distance must be 1, 2 or 5 km.")
        return r

    @field_validator("min_lease", mode="before")
    @classmethod
    def _min_lease(cls, v):
        if isinstance(v, str) and v.strip().isascii() and v.strip().isdigit() and len(v.strip()) <= 2:
            v = int(v.strip())
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v < LEASE_YEARS:
            raise ValueError("Lease left must be a whole number of years from 1 to 98.")
        return v

    @field_validator("min_area", mode="before")
    @classmethod
    def _min_area(cls, v):
        area = Decimal(v.strip()) if isinstance(v, str) and _AREA.fullmatch(v.strip()) else v
        if not isinstance(area, Decimal) or not Decimal(0) < area <= MAX_AREA:
            raise ValueError("Floor area must be a number of square metres from 1 to 300.")
        return area

    @field_validator("page", mode="before")
    @classmethod
    def _page(cls, v):
        if isinstance(v, str) and v.strip().isascii() and v.strip().isdigit() and len(v.strip()) <= 5:
            v = int(v.strip())
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= MAX_PAGE:
            raise ValueError(f"Page must be a whole number from 1 to {MAX_PAGE}.")
        return v


@dataclass(frozen=True)
class BlockFilter:
    """Typed values both repos filter blocks on, worked out once from a BlocksQuery and the window."""

    flat_type: str
    budget: Decimal
    window: Window
    town: str | None = None
    place: int | None = None
    radius_m: int = 2000
    min_lease_year: int | None = None  # the block's lease must start in this year or later
    min_area: Decimal | None = None
    page: int = 1

    @property
    def offset(self) -> int:
        return (self.page - 1) * PAGE_SIZE


def block_filter(q: BlocksQuery, window: Window) -> BlockFilter:
    min_year = None if q.min_lease is None else window.end.year + q.min_lease - LEASE_YEARS
    return BlockFilter(
        flat_type=q.flat_type,
        budget=q.budget,
        window=window,
        town=q.town,
        place=q.place,
        radius_m=q.radius,
        min_lease_year=min_year,
        min_area=q.min_area,
        page=q.page,
    )
