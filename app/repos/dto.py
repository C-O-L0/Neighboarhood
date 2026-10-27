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


@dataclass(frozen=True)
class SaleRow:
    txn_id: int
    txn_month: date
    town: str
    block: str
    street: str
    building_id: int
    flat_type: str
    flat_model: str
    storey_min: int
    storey_max: int
    floor_area_sqm: Decimal
    lease_commence_year: int | None
    remaining_lease_months: int | None
    resale_price: Decimal

    @property
    def price_psm(self) -> Decimal:
        return (self.resale_price / self.floor_area_sqm).quantize(Decimal("0.01"), ROUND_HALF_UP)


@dataclass(frozen=True)
class SearchResult:
    rows: list[SaleRow]
    total: int
    page: int
    pages: int


@dataclass(frozen=True)
class TrendPoint:
    month: date
    n: int
    median_price: Decimal
    avg_psm: Decimal = field(metadata={"tol": AVG_TOL})
    prev_year_median: Decimal | None
    rolling_n: int
    rolling_median: Decimal

    @property
    def yoy_change(self) -> Decimal | None:
        if self.prev_year_median is None:
            return None
        return self.median_price - self.prev_year_median

    @property
    def yoy_pct(self) -> Decimal | None:
        if self.prev_year_median is None:
            return None
        return (self.yoy_change / self.prev_year_median * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)


@dataclass(frozen=True)
class TownRank:
    flat_type: str
    rank: int
    town: str
    n: int
    median_price: Decimal
    avg_psm: Decimal = field(metadata={"tol": AVG_TOL})
    island_median: Decimal
    beats_island: bool
