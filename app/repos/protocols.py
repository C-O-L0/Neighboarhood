"""One Protocol per feature. The PostgreSQL and MongoDB repos both implement it."""

from __future__ import annotations

from typing import Protocol

from app.repos.dto import BuildingDetail


class BuildingRepo(Protocol):
    def get(self, building_id: int) -> BuildingDetail | None:
        """The building page data, or None if the id does not exist."""
        ...

    def find_id(self, block: str, street: str) -> int | None:
        """Id of the block with this normalised (block, street), or None."""
        ...
