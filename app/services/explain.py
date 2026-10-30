"""Explainer tags (USE_CASES R8): how a block or flat differs from recent sales of its flat type in its town.

A town baseline is the typical (lower median) storey band, lease start year and floor area of the town's recent
sales of one flat type. A subject, a block's recent sales or a flat a buyer typed in, gets a tag for each factor
that differs notably. Tags describe differences; they never claim to be why a price is high or low.

Block age and lease are one factor: every HDB lease is 99 years from the lease start year, so the lease left at a
sale is 99 years minus the block's age and the two tags would always appear together. All comparisons use
integer arithmetic, so the same inputs give the same tags in every database mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.repos.dto import TownBaseline
from app.services.explore import FEW_SALES

LEASE_YEARS_APART = 10


class Factor(StrEnum):
    FLOOR = "floor"
    LEASE = "lease"
    SIZE = "size"


@dataclass(frozen=True)
class Subject:
    """What is compared. storey_sum is storey_min + storey_max of the band (twice its middle storey)."""

    storey_sum: int | None = None
    lease_year: int | None = None
    area: Decimal | None = None


@dataclass(frozen=True)
class Tag:
    factor: Factor
    label: str
    higher: bool  # the subject is above the town's typical value (higher floor, longer lease, larger)


def tags(subject: Subject, baseline: TownBaseline | None) -> list[Tag] | None:
    """The notable differences, in a fixed order (floor, lease, size). [] means typical for the town.

    None when there is no baseline or it rests on fewer than FEW_SALES sales: then there is nothing fair to say.
    """
    if baseline is None or baseline.n < FEW_SALES:
        return None
    out = []
    if subject.storey_sum is not None:
        # A third or more below or above the typical middle storey.
        if 3 * subject.storey_sum <= 2 * baseline.storey_sum:
            out.append(Tag(Factor.FLOOR, "Low floor", higher=False))
        elif 3 * subject.storey_sum >= 4 * baseline.storey_sum:
            out.append(Tag(Factor.FLOOR, "High floor", higher=True))
    if subject.lease_year is not None and baseline.lease_year is not None:
        if subject.lease_year <= baseline.lease_year - LEASE_YEARS_APART:
            out.append(Tag(Factor.LEASE, "Shorter lease", higher=False))
        elif subject.lease_year >= baseline.lease_year + LEASE_YEARS_APART:
            out.append(Tag(Factor.LEASE, "Longer lease", higher=True))
    if subject.area is not None:
        # 10% or more smaller or larger than the typical floor area.
        if 10 * subject.area <= 9 * baseline.area_median:
            out.append(Tag(Factor.SIZE, "Smaller than usual", higher=False))
        elif 10 * subject.area >= 11 * baseline.area_median:
            out.append(Tag(Factor.SIZE, "Larger than usual", higher=True))
    return out
