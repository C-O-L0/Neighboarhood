"""Admin sales: the record form's input rules, the actions that write, and the in-process admin state.

Every action that writes (record, delete, refresh, rebuild) runs under one `threading.Lock` held in `AdminState`.
The app runs one uvicorn worker, so the lock is process-wide. It exists because `rebuild_all_stats` resets and then
`$merge`s outside a transaction: an admin write that committed during the rebuild could be overwritten by stats
computed from an older read. Serialising the actions means they never overlap. Admin route handlers are plain `def`
(thread pool), so waiting for the lock blocks only that request.

If the PostgreSQL refresh after a committed write fails, the sale stays and `stale_since` records the time; the trends
page shows a banner in PostgreSQL mode until a later refresh succeeds.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from app.repos.dto import Figures, NewSale, SaleOutcome, SaleResult
from app.services.dbmode import Timing
from app.services.saved import Notice
from app.services.search import MAX_AREA, MAX_BLOCK, MAX_PRICE, MAX_STREET, RESALE_TYPES, parse_month
from app.templating import money
from etl.clean import FLAT_MODEL_IDS, normalise_key

log = logging.getLogger(__name__)

MAX_TXN_ID = 2**63 - 1  # resale_transaction.txn_id is BIGINT
MIN_YEAR, MAX_YEAR = 1900, 2100  # building.year_completed / lease_commence_year bounds
MAX_STOREY = 60
MAX_LEASE_MONTHS = 1188
PRICE_PLACES = Decimal("0.01")
AREA_PLACES = Decimal("0.1")
LIST_LIMIT = 20

FIELDS = (
    "block", "street", "month", "flat_type", "flat_model", "storey_min", "storey_max",
    "floor_area_sqm", "remaining_lease_months", "resale_price",
)  # fmt: skip

# Digits only, so NaN, Infinity and 1E+30 never reach Decimal() (which would accept them or raise InvalidOperation).
_NUMBER = re.compile(r"\d+(\.\d+)?", re.ASCII)
_WHOLE = re.compile(r"\d{1,6}", re.ASCII)


def _decimal(value: str, label: str, upper: Decimal, places: Decimal) -> Decimal:
    """A positive decimal below `upper` with at most as many decimal places as `places`, quantized to them."""
    text = value.strip()
    if not _NUMBER.fullmatch(text) or len(text) > 20:
        raise ValueError(f"{label} must be a positive number such as 550000.50.")
    number = Decimal(text)
    if number <= 0 or number >= upper:
        raise ValueError(f"{label} must be more than 0 and less than {upper:,}.")
    quantized = number.quantize(places)
    if number != quantized:
        digits = -places.as_tuple().exponent
        raise ValueError(f"{label} must have at most {digits} decimal place{'' if digits == 1 else 's'}.")
    return quantized


def _whole(value: str, label: str, low: int, high: int) -> int:
    text = value.strip()
    if not _WHOLE.fullmatch(text) or not low <= int(text) <= high:
        raise ValueError(f"{label} must be a whole number from {low} to {high:,}.")
    return int(text)


def _key(value: str, label: str, limit: int) -> str:
    if not value.isprintable():
        raise ValueError(f"{label} contains characters that are not allowed.")
    text = normalise_key(value)
    if not text:
        raise ValueError(f"{label} is required.")
    if len(text) > limit:
        raise ValueError(f"{label} must be at most {limit} characters.")
    return text


class SaleForm(BaseModel):
    """The record-a-sale form. Every bound is checked before any `quantize`, and before either database."""

    model_config = ConfigDict(frozen=True)

    block: str
    street: str
    month: date
    flat_type: str
    flat_model: str
    storey_min: int
    storey_max: int
    floor_area_sqm: Decimal
    remaining_lease_months: int
    resale_price: Decimal

    @field_validator("block", mode="before")
    @classmethod
    def _block(cls, v):
        return _key(v, "Block", MAX_BLOCK)

    @field_validator("street", mode="before")
    @classmethod
    def _street(cls, v):
        return _key(v, "Street", MAX_STREET)

    @field_validator("month", mode="before")
    @classmethod
    def _month(cls, v):
        month = parse_month(v, "Month")
        if not MIN_YEAR <= month.year <= MAX_YEAR:
            raise ValueError(f"Month must be in a year from {MIN_YEAR} to {MAX_YEAR}.")
        return month

    @field_validator("flat_type", mode="before")
    @classmethod
    def _flat_type(cls, v):
        if v.strip() not in RESALE_TYPES:
            raise ValueError("Flat type must be one of the listed resale flat types.")
        return v.strip()

    @field_validator("flat_model", mode="before")
    @classmethod
    def _flat_model(cls, v):
        if v.strip() not in FLAT_MODEL_IDS:
            raise ValueError("Flat model must be one of the listed flat models.")
        return v.strip()

    @field_validator("storey_min", "storey_max", mode="before")
    @classmethod
    def _storey(cls, v, info):
        label = "Lowest storey" if info.field_name == "storey_min" else "Highest storey"
        return _whole(v, label, 1, MAX_STOREY)

    @field_validator("floor_area_sqm", mode="before")
    @classmethod
    def _area(cls, v):
        return _decimal(v, "Floor area", MAX_AREA, AREA_PLACES)

    @field_validator("remaining_lease_months", mode="before")
    @classmethod
    def _lease(cls, v):
        return _whole(v, "Remaining lease (months)", 0, MAX_LEASE_MONTHS)

    @field_validator("resale_price", mode="before")
    @classmethod
    def _price(cls, v):
        return _decimal(v, "Resale price", MAX_PRICE, PRICE_PLACES)

    @model_validator(mode="after")
    def _storeys(self):
        if self.storey_min > self.storey_max:
            raise ValueError("Lowest storey must not be above highest storey.")
        return self

    def new_sale(self, building_id: int) -> NewSale:
        return NewSale(
            building_id,
            self.month,
            self.flat_type,
            self.flat_model,
            self.storey_min,
            self.storey_max,
            self.floor_area_sqm,
            self.remaining_lease_months,
            self.resale_price,
        )


def parse_sale_form(raw: dict[str, str]) -> tuple[SaleForm | None, list[str]]:
    try:
        return SaleForm.model_validate({k: raw.get(k, "") for k in FIELDS}), []
    except ValidationError as exc:
        return None, [e["msg"].removeprefix("Value error, ") for e in exc.errors()]


def parse_txn_id(raw: str) -> int | None:
    """A sale id from a form field: plain digits from 1 to 2^63-1, else None."""
    text = raw.strip()
    if not text.isascii() or not text.isdigit() or len(text) > 19:
        return None
    number = int(text)
    return number if 1 <= number <= MAX_TXN_ID else None


# ------------------------------------------------------------------------------ state


@dataclass(frozen=True)
class Action:
    """The outcome of the last admin action, shown on /admin: a message and each step's own timing."""

    kind: str  # "record", "delete", "refresh" or "rebuild"
    mode: str
    notice: Notice
    timings: list[Timing] = field(default_factory=list)


class AdminState:
    """In-process admin state (one worker): the write lock, the stale-figures flag and the last action."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.stale_since: datetime | None = None
        self.last: Action | None = None


# ---------------------------------------------------------------------------- messages


def _figures_text(f: Figures) -> str:
    median = "no median" if f.block_median is None else f"median {money(f.block_median)}"
    return f"{f.total_sales:,} sales on the island, {f.block_sales:,} in this block ({median})"


def _rolled_back(what: str, result: SaleResult, mode: str) -> Notice:
    where = "the transaction was rolled back" if mode == "pg" else "the transaction was aborted"
    return Notice(
        "ok",
        f"Rolled back on purpose: {what}, then failed before commit, so {where} and nothing was kept. "
        f"Before: {_figures_text(result.before)}. After: {_figures_text(result.after)}.",
    )


def _write_notice(kind: str, result: SaleResult, mode: str, refresh_failed: bool) -> Notice:
    if result.outcome is SaleOutcome.NO_SUCH_BUILDING:
        return Notice("bad", "There is no such block.")
    if result.outcome is SaleOutcome.NOT_FOUND:
        return Notice("bad", "No sale has that id.")
    if result.outcome is SaleOutcome.REJECTED:
        return Notice("bad", f"The sale was rejected: {result.reason}.")
    if result.rolled_back:
        what = "the sale was written" if kind == "record" else "the sale was deleted"
        return _rolled_back(what, result, mode)
    verb = "recorded" if kind == "record" else "deleted"
    base = f"Sale {result.txn_id} {verb}."
    if mode == "mongo":
        return Notice("ok", f"{base} The block's statistics and recent sales were updated in the same transaction.")
    if refresh_failed:
        return Notice(
            "bad",
            f"{base} The town figures refresh failed, so the town figures are stale until the next successful refresh.",
        )
    return Notice("ok", f"{base} The town figures were refreshed.")


# --------------------------------------------------------------------------- actions


def _timed(fn, *args) -> tuple[Any, float]:
    started = time.perf_counter_ns()
    result = fn(*args)
    return result, (time.perf_counter_ns() - started) / 1e6


def _refresh_pg(state: AdminState, repo) -> tuple[bool, float]:
    """Refresh the town figures; returns (succeeded, ms). Must be called with the lock held."""
    started = time.perf_counter_ns()
    try:
        repo.refresh()
    except Exception as exc:  # any failure leaves the committed write in place and the figures stale
        log.warning("town figures refresh failed: %s", type(exc).__name__)
        state.stale_since = state.stale_since or datetime.now(UTC)
        return False, (time.perf_counter_ns() - started) / 1e6
    state.stale_since = None
    return True, (time.perf_counter_ns() - started) / 1e6


def _write(state: AdminState, kind: str, mode: str, repo, call) -> Action:
    """Run `call()` (a repo write returning a SaleResult) under the lock; PostgreSQL then refreshes the town figures."""
    with state.lock:
        result, write_ms = _timed(call)
        timings = [Timing(f"{kind} sale (write)", mode, write_ms)]
        refresh_failed = False
        committed = result.outcome is SaleOutcome.OK and not result.rolled_back
        if mode == "pg" and committed:
            ok, refresh_ms = _refresh_pg(state, repo)
            refresh_failed = not ok
            timings.append(Timing("town figures refresh", mode, refresh_ms))
        action = Action(kind, mode, _write_notice(kind, result, mode, refresh_failed), timings)
        state.last = action
        return action


def record_sale(state: AdminState, mode: str, repo, form: SaleForm, simulate_failure: bool) -> Action:
    def call() -> SaleResult:
        ref = repo.find_building(form.block, form.street)
        if ref is None:
            return SaleResult(SaleOutcome.NO_SUCH_BUILDING)
        return repo.record(form.new_sale(ref.building_id), simulate_failure)

    return _write(state, "record", mode, repo, call)


def delete_sale(state: AdminState, mode: str, repo, txn_id: int, simulate_failure: bool) -> Action:
    return _write(state, "delete", mode, repo, lambda: repo.delete(txn_id, simulate_failure))


def refresh_town_figures(state: AdminState, repo) -> Action:
    """POST /admin/refresh (PostgreSQL): refresh the materialised view; a success clears the stale flag."""
    with state.lock:
        ok, ms = _refresh_pg(state, repo)
        notice = (
            Notice("ok", "Town figures refreshed.")
            if ok
            else Notice("bad", "The town figures refresh failed; they stay stale until a refresh succeeds.")
        )
        action = Action("refresh", "pg", notice, [Timing("town figures refresh", "pg", ms)])
        state.last = action
        return action


def rebuild_block_stats(state: AdminState, repo) -> Action:
    """POST /admin/rebuild-stats (MongoDB): the $merge rebuild of every block's stats and recent sales."""
    with state.lock:
        blocks, ms = _timed(repo.rebuild_stats)
        notice = Notice("ok", f"Statistics rebuilt for {blocks:,} blocks with sales.")
        action = Action("rebuild", "mongo", notice, [Timing("stats rebuild ($merge)", "mongo", ms)])
        state.last = action
        return action
