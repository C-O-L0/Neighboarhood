"""Chart data for the trends page (docs/conventions.md, 11).

The result is embedded as JSON and read by app/static/charts.js. JSON cannot carry Decimal or date,
so money becomes a string with exactly two decimals and months become "YYYY-MM". Rounding first makes
Decimal("500000") and Decimal("500000.00") give the same text whichever database stored the value.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.repos.dto import TrendPoint
from app.services.trends import add_months

_CENT = Decimal("0.01")


def money_text(value: Decimal | None) -> str | None:
    """A price as a string with two decimals, half up; None stays None."""
    return None if value is None else str(value.quantize(_CENT, ROUND_HALF_UP))


def month_text(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def trends_chart(points: list[TrendPoint]) -> dict:
    """Every calendar month from the first to the last point; months without sales are null."""
    if not points:
        return {"months": [], "median": [], "rolling_median": [], "n": []}
    by_month = {p.month: p for p in points}
    first, last = points[0].month, points[-1].month
    months, current = [], first
    while current <= last:
        months.append(current)
        current = add_months(current, 1)
    return {
        "months": [month_text(m) for m in months],
        "median": [money_text(by_month[m].median_price) if m in by_month else None for m in months],
        "rolling_median": [money_text(by_month[m].rolling_median) if m in by_month else None for m in months],
        "n": [by_month[m].n if m in by_month else None for m in months],
    }
