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
from app.services.search import RESALE_TYPES
from app.services.trends import add_months

WINDOW_MONTHS = 12
FEW_SALES = 5  # a typical price from fewer recent sales is flagged as less reliable (R3)
CLOSEST_TOWNS = 3  # shown when no town fits the budget (UC1 A1)
MAX_BUDGET = Decimal("10000000")
FIELDS = ("flat_type", "budget")
LABELS = {"flat_type": "Flat type", "budget": "Budget"}

# "650000", "650,000", "$650,000", "650,000.50"; commas only between groups of three digits.
_MONEY = re.compile(r"\$?\s*(\d{1,3}(,\d{3})+|\d+)(\.\d{1,2})?", re.ASCII)


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
