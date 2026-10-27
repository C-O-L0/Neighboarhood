"""One Protocol per feature. The PostgreSQL and MongoDB repos both implement it."""

from __future__ import annotations

from typing import Protocol

from app.repos.dto import Account, BuildingDetail, Duplicate, SavedBlock, SearchResult, TownRank, TrendPoint
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
