"""Data transfer objects returned by both repositories (see docs/conventions.md).

Money and areas are Decimal, months are datetime.date on day 1, coordinates are Decimal
with exactly 6 dp. Never float. A field declared with metadata {"tol": Decimal("0.01")}
is a rounded aggregate that the equivalence tests compare with that tolerance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

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


@dataclass(frozen=True)
class Account:
    """A buyer account. Datetimes are timezone-aware UTC; both repos return the same values."""

    buyer_id: int
    email: str
    display_name: str | None
    is_admin: bool
    created_at: datetime
    last_active_at: datetime | None
    password_hash: str


@dataclass(frozen=True)
class SavedBlock:
    building_id: int
    block: str
    street: str
    town: str
    note: str | None
    saved_at: datetime


@dataclass(frozen=True)
class Duplicate:
    """Returned by AccountRepo.create when the email is already registered (ignoring case)."""


class Outcome(StrEnum):
    """Result of a saved-block write. Both repos return the same value for the same situation."""

    OK = "ok"
    ALREADY_SAVED = "already_saved"
    NOT_SAVED = "not_saved"
    NO_SUCH_BUILDING = "no_such_building"
    CAP_REACHED = "cap_reached"


@dataclass(frozen=True)
class RolledBack:
    """The forced-failure demo: the writes ran inside a transaction and were then undone.

    PostgreSQL fills `insert` and `update` (rows affected by each statement); MongoDB fills `matched`
    (documents matched by the single update). The fields a database does not use stay None.
    """

    insert: int | None = None
    update: int | None = None
    matched: int | None = None


class SaleOutcome(StrEnum):
    """Result of an admin sale write. Both repos return the same value for the same situation."""

    OK = "ok"
    NO_SUCH_BUILDING = "no_such_building"
    REJECTED = "rejected"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class NewSale:
    """A validated sale to record. Price has scale 2 and area scale 1 (the app quantizes before either database)."""

    building_id: int
    txn_month: date
    flat_type: str
    flat_model: str
    storey_min: int
    storey_max: int
    floor_area_sqm: Decimal
    remaining_lease_months: int
    resale_price: Decimal


@dataclass(frozen=True)
class BuildingRef:
    building_id: int
    block: str
    street: str


@dataclass(frozen=True)
class Figures:
    """What the forced-failure demo compares before and after: island-wide sales and one block's figures.

    PostgreSQL derives the block figures live from resale_transaction; MongoDB reads the profile's stored stats.
    """

    total_sales: int
    block_sales: int
    block_median: Decimal | None


@dataclass(frozen=True)
class SaleResult:
    outcome: SaleOutcome
    txn_id: int | None = None
    reason: str | None = None  # the database's own rejection message, for REJECTED
    before: Figures | None = None  # set only when the forced-failure demo ran
    after: Figures | None = None

    @property
    def rolled_back(self) -> bool:
        return self.before is not None


DISTANCE_TOL = Decimal("1")  # metres: PostgreSQL Haversine (R = 6378100 m) vs MongoDB $geoNear (BUILD_PLAN section 6)


@dataclass(frozen=True)
class HawkerDistance:
    """One hawker centre and its distance from a block, in metres rounded to 0.1 (sorted on the unrounded value)."""

    hawker_id: int
    name: str
    address: str | None
    status: str
    stall_count: int | None
    distance_m: Decimal = field(metadata={"tol": DISTANCE_TOL})


@dataclass(frozen=True)
class BlockFoodAccess:
    """A geocoded block with its nearest open hawker centres (the town view uses a list of length 1)."""

    building_id: int
    block: str
    street: str
    nearest: list[HawkerDistance]
    within_1km: int


@dataclass(frozen=True)
class FlatTypeRange:
    """Recent sales of one flat type: the typical floor area and the middle 80% of prices.

    price_low and price_high are the 10th and 90th percentiles as PostgreSQL's percentile_disc defines them:
    the sorted price at position ceil(p * n), counting from 1.
    """

    flat_type: str
    n: int
    area_median: Decimal
    price_low: Decimal
    price_high: Decimal


@dataclass(frozen=True)
class TownFit:
    """Recent sales of one flat type in one town, measured against a budget."""

    town: str
    n: int
    median_price: Decimal
    within_budget: int  # sales at or under the budget

    @property
    def share_within(self) -> int:
        """Whole percent of sales at or under the budget, rounded half up."""
        return int((Decimal(100 * self.within_budget) / self.n).quantize(Decimal(1), ROUND_HALF_UP))


@dataclass(frozen=True)
class TownBaseline:
    """The typical recent sale of one flat type in one town, for explainer tags (app/services/explain.py).

    Each figure is the lower median over the window's sales: storey_sum is storey_min + storey_max (twice the
    middle storey, kept whole), lease_year the block's lease start year.
    """

    town: str
    n: int
    storey_sum: int
    lease_year: int | None
    area_median: Decimal


@dataclass(frozen=True)
class Place:
    """A block a buyer named as a place to live near. located is False when it has no coordinates."""

    building_id: int
    block: str
    street: str
    town: str
    located: bool
    latitude: Decimal | None = None  # 6 dp, when located
    longitude: Decimal | None = None


@dataclass(frozen=True)
class NearBlock:
    """A block within the chosen radius of a place, with its straight-line distance in metres (0.1 m)."""

    building_id: int
    block: str
    street: str
    town: str
    distance_m: Decimal = field(metadata={"tol": DISTANCE_TOL})


@dataclass(frozen=True)
class BlockFit:
    """A block whose typical recent price for one flat type is within budget, with what its explainer tags need.

    storey_sum and area_median are lower medians over the block's window sales (as TownBaseline), lease_year
    the block's lease start year, distance_m the distance from the chosen place (None without a place).
    """

    building_id: int
    block: str
    street: str
    town: str
    n: int
    median_price: Decimal
    storey_sum: int
    area_median: Decimal
    lease_year: int | None
    distance_m: Decimal | None = field(default=None, metadata={"tol": DISTANCE_TOL})
    latitude: Decimal | None = None  # 6 dp; None for a block without coordinates (it has no map pin)
    longitude: Decimal | None = None


@dataclass(frozen=True)
class BlockFitPage:
    total: int  # blocks that match, on every page
    rows: list[BlockFit]
