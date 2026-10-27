"""Data transfer objects returned by both repositories (see docs/conventions.md).

Money and areas are Decimal, months are datetime.date on day 1, coordinates are Decimal
with exactly 6 dp. Never float. A field declared with metadata {"tol": Decimal("0.01")}
is a rounded aggregate that the equivalence tests compare with that tolerance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

AVG_TOL = Decimal("0.01")


@dataclass(frozen=True)
class SupplyRow:
    flat_type: str
    sold: int
    rental: int


@dataclass(frozen=True)
class FlatTypeStats:
    flat_type: str
    txn_count: int
    median_price: Decimal | None
    avg_psm: Decimal | None = field(metadata={"tol": AVG_TOL})


@dataclass(frozen=True)
class BuildingStats:
    txn_count: int
    median_price: Decimal | None
    avg_psm: Decimal | None = field(metadata={"tol": AVG_TOL})
    last_txn_month: date | None
    by_flat_type: list[FlatTypeStats]


@dataclass(frozen=True)
class RecentSale:
    txn_id: int
    txn_month: date
    flat_type: str
    flat_model: str
    storey_min: int
    storey_max: int
    floor_area_sqm: Decimal
    resale_price: Decimal

    @property
    def price_psm(self) -> Decimal:
        return (self.resale_price / self.floor_area_sqm).quantize(Decimal("0.01"), ROUND_HALF_UP)


@dataclass(frozen=True)
class BuildingDetail:
    building_id: int
    block: str
    street: str
    town: str
    town_code: str
    in_property_dataset: bool
    year_completed: int | None
    lease_commence_year: int | None
    max_floor: int | None
    land_use: dict[str, bool] | None
    latitude: Decimal | None
    longitude: Decimal | None
    supply: list[SupplyRow] | None
    total_units: int | None
    stats: BuildingStats
    recent_sales: list[RecentSale]
