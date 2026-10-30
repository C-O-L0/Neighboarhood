"""One Protocol per feature. The PostgreSQL and MongoDB repos both implement it."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol

from app.repos.dto import (
    Account,
    BlockFitPage,
    BlockFoodAccess,
    BuildingDetail,
    Duplicate,
    FlatTypeRange,
    NearBlock,
    Outcome,
    Place,
    RolledBack,
    SavedBlock,
    SearchResult,
    TownBaseline,
    TownFit,
    TownRank,
    TrendPoint,
)
from app.services.explore import BlockFilter, Window
from app.services.rankings import RankingQuery
from app.services.search import SearchQuery
from app.services.trends import TrendQuery


class BuildingRepo(Protocol):
    def get(self, building_id: int) -> BuildingDetail | None:
        """The building page data, or None if the id does not exist."""
        ...

    def find_id(self, block: str, street: str) -> int | None:
        """Id of the block with this normalised (block, street), or None."""
        ...


class SearchRepo(Protocol):
    def search(self, query: SearchQuery) -> SearchResult:
        """One page of matching sales and the exact total, in the order the query asks for."""
        ...


class TrendRepo(Protocol):
    def trend(self, query: TrendQuery) -> list[TrendPoint]:
        """The monthly series for one town and flat type, month ascending (months without sales are absent)."""
        ...


class RankingRepo(Protocol):
    def rank(self, query: RankingQuery) -> list[TownRank]:
        """Towns ranked by median price within each flat type for one year: flat type, rank, town order."""
        ...


class AccountRepo(Protocol):
    def by_id(self, buyer_id: int) -> Account | None:
        """The account with this id, or None."""
        ...

    def by_email(self, email: str) -> Account | None:
        """The account with this email (compared ignoring case), or None."""
        ...

    def create(self, email: str, password_hash: str, display_name: str | None) -> int | Duplicate:
        """Register a non-admin account; its new id, or Duplicate if the email is taken."""
        ...

    def touch(self, buyer_id: int) -> None:
        """Set last_active_at to now."""
        ...


class SavedRepo(Protocol):
    def list(self, buyer_id: int) -> list[SavedBlock]:
        """The buyer's saved blocks ordered by building id (empty if none or no such buyer)."""
        ...

    def save(
        self, buyer_id: int, building_id: int, note: str | None, simulate_failure: bool = False
    ) -> Outcome | RolledBack:
        """Save a block and touch last_active_at atomically.

        Outcome order: no_such_building, already_saved, cap_reached, ok. With simulate_failure the writes run in a
        transaction that is aborted on purpose and RolledBack reports what each write did before the abort.
        """
        ...

    def edit_note(self, buyer_id: int, building_id: int, note: str | None) -> Outcome:
        """Replace the note of a saved block (ok or not_saved); a not_saved call changes nothing."""
        ...

    def remove(self, buyer_id: int, building_id: int) -> Outcome:
        """Unsave a block (ok or not_saved); a not_saved call changes nothing."""
        ...


class FoodRepo(Protocol):
    def block(self, building_id: int) -> BlockFoodAccess | None:
        """The 5 nearest open hawker centres (distance, then id) and the count within 1 km; None without coordinates."""
        ...

    def town(self, town: str) -> list[BlockFoodAccess]:
        """Each geocoded block of the town with its nearest open centre, ordered by distance then building id."""
        ...


class ExploreRepo(Protocol):
    def latest_month(self) -> date | None:
        """The latest registration month with a sale (day 1); None when there are no sales."""
        ...

    def flat_type_ranges(self, window: Window) -> list[FlatTypeRange]:
        """Per resale flat type with sales in the window: count, typical area, 10th and 90th percentile price.

        Ordered as the flat types are listed in etl.clean.FLAT_TYPES.
        """
        ...

    def towns_fit(self, flat_type: str, budget: Decimal, window: Window) -> list[TownFit]:
        """Per town with sales of the flat type in the window: count, typical price and sales within budget.

        Ordered by typical price, then town name.
        """
        ...

    def blocks_fit(self, f: BlockFilter) -> BlockFitPage:
        """Blocks with sales of the flat type in the window whose typical price is within the budget, after the
        optional town, place and radius, lease and floor area filters. One page of PAGE_SIZE and the total.

        Without a place: cheapest typical price first, then building id. With a place: located blocks within the
        radius (the place itself included), nearest first, then building id.
        """
        ...

    def baselines(self, flat_type: str, window: Window) -> list[TownBaseline]:
        """Per town with sales of the flat type in the window: count and the typical storey band, lease start
        year and floor area (lower medians). Ordered by town name.
        """
        ...


class PlaceRepo(Protocol):
    def find(self, block: str | None, street: str, limit: int) -> list[Place]:
        """Blocks whose street contains the text (and with that block number, when given), at most limit.

        Ordered by street, then block number (shorter first, so 9 comes before 10), then building id.
        """
        ...

    def near(self, building_id: int, radius_m: int) -> list[NearBlock] | None:
        """Located blocks within radius_m of the block, nearest first (then building id), the block itself and
        blocks marked non-residential left out. None when the block is unknown or has no coordinates.
        """
        ...
