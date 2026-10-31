"""Compare 2 to 4 blocks side by side (S7: UC5 step 4, UC6): the input rules and what each column shows.

The figures come from the queries the block profile already runs, one block at a time: its details, its food
access and its recent sales by flat type (the explore window, R4), with the town baselines for the explainer tags.
A block without sales of the chosen flat type says so in its cells (UC5 A3).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.repos.dto import BlockFoodAccess, BuildingDetail, FlatTypeRecent
from app.services.block import MAX_AHEAD, lease_left
from app.services.explain import Tag
from app.services.search import RESALE_TYPES

MIN_BLOCKS, MAX_BLOCKS = 2, 4
FALLBACK_TYPE = "4 ROOM"  # when none of the blocks has recent sales
MAX_ID = 2**31 - 1


def parse_ids(raw: list[str]) -> list[int] | None:
    """Building ids from repeated `ids` parameters (a comma list also works), in the order given, without
    repeats. None unless there are 2 to 4 valid ones."""
    ids: list[int] = []
    for part in (p.strip() for value in raw[: MAX_BLOCKS * 2] for p in value.split(",")):
        if not part:
            continue
        if not (part.isascii() and part.isdigit() and len(part) <= 10) or not 1 <= int(part) <= MAX_ID:
            return None
        if int(part) not in ids:
            ids.append(int(part))
    return ids if MIN_BLOCKS <= len(ids) <= MAX_BLOCKS else None


def parse_flat_type(raw: str) -> str | None:
    return raw if raw in RESALE_TYPES else None


def parse_year(raw: str, now: int) -> int | None:
    raw = raw.strip()
    if raw.isascii() and raw.isdigit() and len(raw) == 4 and now <= int(raw) <= now + MAX_AHEAD:
        return int(raw)
    return None


def default_flat_type(recent: list[list[FlatTypeRecent]]) -> str | None:
    """The flat type with the most recent sales across the blocks (ties: flat type order), or None."""
    totals: dict[str, int] = {}
    for rows in recent:
        for r in rows:
            totals[r.flat_type] = totals.get(r.flat_type, 0) + r.n
    order = {t: i for i, t in enumerate(RESALE_TYPES)}
    return min(totals, key=lambda t: (-totals[t], order[t]), default=None)


@dataclass(frozen=True)
class Column:
    """One block in the comparison."""

    b: BuildingDetail
    food: BlockFoodAccess | None
    recent: FlatTypeRecent | None  # the chosen flat type's recent sales; None when it had none
    tags: list[Tag] | None
    lease_now: int | None
    lease_then: int | None  # lease left in the chosen year, when one is given


def _whole(value: Decimal) -> Decimal:
    """As the page shows it (the per_sqm filter), so two columns that read the same are both marked."""
    return value.quantize(Decimal(1), ROUND_HALF_UP)


def best(columns: list[Column]) -> dict[str, set[int]]:
    """Building ids that lead each row where more or less is plainly better: the lowest typical price and price
    per sqm, the longest lease and the most hawker centres within 1 km. A row with fewer than two values, or
    where all are equal, marks nobody."""

    def leaders(values: dict[int, Decimal | int], low: bool) -> set[int]:
        if len(values) < 2 or len(set(values.values())) == 1:
            return set()
        target = min(values.values()) if low else max(values.values())
        return {k for k, v in values.items() if v == target}

    return {
        "price": leaders({c.b.building_id: c.recent.median_price for c in columns if c.recent}, low=True),
        "psm": leaders({c.b.building_id: _whole(c.recent.avg_psm) for c in columns if c.recent}, low=True),
        "lease": leaders({c.b.building_id: c.lease_now for c in columns if c.lease_now is not None}, low=False),
        "food": leaders({c.b.building_id: c.food.within_1km for c in columns if c.food}, low=False),
    }


def column(
    b: BuildingDetail,
    food: BlockFoodAccess | None,
    recent: FlatTypeRecent | None,
    found: list[Tag] | None,
    now: int,
    at: int | None,
) -> Column:
    lease_year = b.lease_commence_year
    return Column(
        b=b,
        food=food,
        recent=recent,
        tags=found,
        lease_now=None if lease_year is None else lease_left(lease_year, now),
        lease_then=None if lease_year is None or at is None else lease_left(lease_year, at),
    )
