"""Trend query model: town and flat type are required, the month bounds optional.

The same typed object drives the PostgreSQL and the MongoDB repo (docs/conventions.md, 8).
A GET form sends every field, so blank values count as absent (and a blank required field
is reported as missing).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from app.services.search import RESALE_TYPES, parse_month
from etl.clean import TOWN_IDS

FIELDS = ("town", "flat_type", "month_from", "month_to")
LABELS = {"town": "Town", "flat_type": "Flat type", "month_from": "From month", "month_to": "To month"}


def add_months(d: date, months: int) -> date:
    """The first of the month `months` calendar months from d (d is on day 1)."""
    index = d.year * 12 + (d.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


class TrendQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    town: str
    flat_type: str
    month_from: date | None = None
    month_to: date | None = None

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

    @field_validator("month_from", "month_to", mode="before")
    @classmethod
    def _month(cls, v, info):
        return parse_month(v, LABELS[info.field_name])

    @model_validator(mode="after")
    def _range(self):
        if self.month_from is not None and self.month_to is not None and self.month_from > self.month_to:
            raise ValueError("From month must not be after to month.")
        return self


def error_messages(exc: ValidationError) -> list[str]:
    """Plain messages from a ValidationError (our validators raise ValueError)."""
    out = []
    for e in exc.errors():
        if e["type"] == "missing":
            out.append(f"{LABELS[e['loc'][0]]} is required.")
        else:
            out.append(e["msg"].removeprefix("Value error, "))
    return out
