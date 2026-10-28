"""One Protocol per feature. The PostgreSQL and MongoDB repos both implement it."""

from __future__ import annotations

from typing import Protocol

from app.repos.dto import (
    Account,
    BlockFoodAccess,
    BuildingDetail,
    Duplicate,
    Outcome,
    RolledBack,
    SavedBlock,
    SearchResult,
    TownRank,
    TrendPoint,
)
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
