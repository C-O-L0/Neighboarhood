"""One Protocol per feature. The PostgreSQL and MongoDB repos both implement it."""

from __future__ import annotations

from typing import Protocol

from app.repos.dto import BuildingDetail, SearchResult, TownRank, TrendPoint
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
