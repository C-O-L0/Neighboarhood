"""Search query model: every filter is optional and validated before any database sees it.

The same typed object drives the PostgreSQL and the MongoDB repo (docs/conventions.md, 8).
A GET form sends every field, so blank values count as absent.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from etl.clean import FLAT_MODEL_IDS, FLAT_TYPES, TOWN_IDS, normalise_key

PAGE_SIZE = 50
MAX_PAGE = 10_000
MAX_STREET = 40  # building.street VARCHAR(40)
MAX_BLOCK = 5  # building.block VARCHAR(5)
MAX_PRICE = Decimal("10000000")
MAX_AREA = Decimal("400")

FIELDS = (
    "town", "flat_type", "flat_model", "month_from", "month_to", "price_min", "price_max",
    "area_min", "area_max", "street", "block", "sort", "order", "page",
)  # fmt: skip
RESALE_TYPES = [name for name, resale in FLAT_TYPES if resale]
SORTS = {"month": "txn_month", "price": "resale_price", "area": "floor_area_sqm"}
ORDERS = ("asc", "desc")

_MONTH = re.compile(r"(\d{4})-(\d{2})", re.ASCII)
_NUMBER = re.compile(r"\d+(\.\d+)?", re.ASCII)
_DIGITS = re.compile(r"\d{1,6}", re.ASCII)


def _decimal(value: str, label: str, places: int, upper: Decimal, *, allow_zero: bool) -> Decimal:
    if not _NUMBER.fullmatch(value) or len(value) > 20:
        raise ValueError(f"{label} must be a positive number such as 550000 or 550000.50.")
    number = Decimal(value)
    if number >= upper or (number == 0 and not allow_zero):
        low = "0 or more" if allow_zero else "more than 0"
        raise ValueError(f"{label} must be {low} and less than {upper:,}.")
    if number != number.quantize(Decimal(1).scaleb(-places)):
        raise ValueError(f"{label} must have at most {places} decimal place{'' if places == 1 else 's'}.")
    return number


def parse_month(value: str, label: str) -> date:
    """A YYYY-MM string as the first of that month; shared by the search and trends forms."""
    m = _MONTH.fullmatch(value.strip())
    if not m or not 1 <= int(m[2]) <= 12 or int(m[1]) < 1:
        raise ValueError(f"{label} must look like 2024-03.")
    return date(int(m[1]), int(m[2]), 1)


def _text(value: str, label: str, limit: int) -> str | None:
    if not value.isprintable():
        raise ValueError(f"{label} contains characters that are not allowed.")
    text = normalise_key(value)
    if len(text) > limit:
        raise ValueError(f"{label} must be at most {limit} characters.")
    return text or None


class SearchQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    town: str | None = None
    flat_type: str | None = None
    flat_model: str | None = None
    month_from: date | None = None
    month_to: date | None = None
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    area_min: Decimal | None = None
    area_max: Decimal | None = None
    street: str | None = None
    block: str | None = None
    sort: str = "month"
    order: str = "desc"
    page: int = 1

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

    @field_validator("flat_type", mode="before")
    @classmethod
    def _flat_type(cls, v):
        if v.strip() not in RESALE_TYPES:
            raise ValueError("Flat type must be one of the listed flat types.")
        return v.strip()

    @field_validator("flat_model", mode="before")
    @classmethod
    def _flat_model(cls, v):
        if v.strip() not in FLAT_MODEL_IDS:
            raise ValueError("Flat model must be one of the listed flat models.")
        return v.strip()

    @field_validator("month_from", "month_to", mode="before")
    @classmethod
    def _month(cls, v, info):
        return parse_month(v, "From month" if info.field_name == "month_from" else "To month")

    @field_validator("price_min", "price_max", mode="before")
    @classmethod
    def _price(cls, v, info):
        label = "Minimum price" if info.field_name == "price_min" else "Maximum price"
        return _decimal(v.strip(), label, 2, MAX_PRICE, allow_zero=True)

    @field_validator("area_min", "area_max", mode="before")
    @classmethod
    def _area(cls, v, info):
        label = "Minimum area" if info.field_name == "area_min" else "Maximum area"
        return _decimal(v.strip(), label, 1, MAX_AREA, allow_zero=False)

    @field_validator("street", mode="before")
    @classmethod
    def _street(cls, v):
        return _text(v, "Street", MAX_STREET)

    @field_validator("block", mode="before")
    @classmethod
    def _block(cls, v):
        return _text(v, "Block", MAX_BLOCK)

    @field_validator("sort", mode="before")
    @classmethod
    def _sort(cls, v):
        if v.strip() not in SORTS:
            raise ValueError("Sort must be month, price or area.")
        return v.strip()

    @field_validator("order", mode="before")
    @classmethod
    def _order(cls, v):
        if v.strip() not in ORDERS:
            raise ValueError("Order must be asc or desc.")
        return v.strip()

    @field_validator("page", mode="before")
    @classmethod
    def _page(cls, v):
        if not _DIGITS.fullmatch(v.strip()) or not 1 <= int(v) <= MAX_PAGE:
            raise ValueError(f"Page must be a whole number from 1 to {MAX_PAGE:,}.")
        return int(v)

    @model_validator(mode="after")
    def _ranges(self):
        for lo, hi, message in (
            ("month_from", "month_to", "From month must not be after to month."),
            ("price_min", "price_max", "Minimum price must not be above maximum price."),
            ("area_min", "area_max", "Minimum area must not be above maximum area."),
        ):
            a, b = getattr(self, lo), getattr(self, hi)
            if a is not None and b is not None and a > b:
                raise ValueError(message)
        return self

    @property
    def offset(self) -> int:
        return (self.page - 1) * PAGE_SIZE


def error_messages(exc: ValidationError) -> list[str]:
    """Plain messages from a ValidationError (our validators raise ValueError)."""
    return [e["msg"].removeprefix("Value error, ") for e in exc.errors()]
