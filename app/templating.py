"""Jinja environment and the formatting filters. All page formatting lives here (docs/conventions.md, 11)."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.services.dbmode import MODE_NAMES, mode_links

BASE = Path(__file__).resolve().parent
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def money(value: Decimal | None) -> str:
    if value is None:
        return "Unknown"
    q = value.quantize(Decimal("0.01"), ROUND_HALF_UP)
    sign = "-" if q < 0 else ""
    return f"{sign}${abs(q):,.2f}"


def month(value: date | None) -> str:
    return "Unknown" if value is None else f"{_MONTHS[value.month - 1]} {value.year}"


def lease(months: int | None) -> str:
    """Remaining lease in the source's wording: '61 years 01 month', '61 years', '1 year'."""
    if months is None:
        return "Unknown"
    years, rest = divmod(months, 12)
    parts = []
    if years:
        parts.append(f"{years} year{'' if years == 1 else 's'}")
    if rest or not years:
        # Two digits after a years part, as the source writes it; a bare month count is not padded.
        digits = f"{rest:02d}" if years else str(rest)
        parts.append(f"{digits} month{'' if rest == 1 else 's'}")
    return " ".join(parts)


def storey(low: int, high: int) -> str:
    return f"{low:02d} TO {high:02d}"


def pct(value: Decimal | None) -> str:
    """A percentage to one decimal place (half up); empty when there is no value."""
    if value is None:
        return ""
    return f"{value.quantize(Decimal('0.1'), ROUND_HALF_UP)}%"


def unknown(value):
    return "Unknown" if value is None else value


def _context(request: Request) -> dict:
    state = request.state
    mode = getattr(state, "db_mode", "pg")
    return {
        "db_mode": mode,
        "db_mode_name": MODE_NAMES[mode],
        "mode_links": mode_links(request),
        "timings": getattr(state, "timings", []),
    }


templates = Jinja2Templates(directory=BASE / "templates", context_processors=[_context])
templates.env.filters.update(money=money, month=month, lease=lease, storey=storey, pct=pct, unknown=unknown)
