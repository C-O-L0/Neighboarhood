"""Ranking query model: the year is required, the flat type optional (absent means every resale type).

The same typed object drives the PostgreSQL and the MongoDB repo (docs/conventions.md, 8).
A GET form sends every field, so blank values count as absent (and a blank year is reported
as missing).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from app.services.search import RESALE_TYPES

MIN_YEAR = 2017
MAX_YEAR = 2026
YEARS = list(range(MAX_YEAR, MIN_YEAR - 1, -1))
FIELDS = ("year", "flat_type")
LABELS = {"year": "Year", "flat_type": "Flat type"}

_YEAR = re.compile(r"\d{4}", re.ASCII)


class RankingQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    year: int
    flat_type: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _blank_is_absent(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not (isinstance(v, str) and not v.strip())}
        return data

    @field_validator("year", mode="before")
    @classmethod
    def _year(cls, v):
        if isinstance(v, str):
            text = v.strip()
            v = int(text) if _YEAR.fullmatch(text) else None
        if not isinstance(v, int) or isinstance(v, bool) or not MIN_YEAR <= v <= MAX_YEAR:
            raise ValueError(f"Year must be a whole number from {MIN_YEAR} to {MAX_YEAR}.")
        return v

    @field_validator("flat_type", mode="before")
    @classmethod
    def _flat_type(cls, v):
        if v.strip() not in RESALE_TYPES:
            raise ValueError("Flat type must be one of the listed flat types.")
        return v.strip()


def error_messages(exc: ValidationError) -> list[str]:
    """Plain messages from a ValidationError (our validators raise ValueError)."""
    out = []
    for e in exc.errors():
        if e["type"] == "missing":
            out.append(f"{LABELS[e['loc'][0]]} is required.")
        else:
            out.append(e["msg"].removeprefix("Value error, "))
    return out
