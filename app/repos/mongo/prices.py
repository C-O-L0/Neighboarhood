"""Comparable sales for a price check, from MongoDB: one find per level on resale_transactions.

The block level uses the building_month index; the street and town levels use town_type_month (each sale
carries its town, block and street). The filter is built only from the typed values in the scope.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from pymongo.database import Database

from app.repos.dto import Comparable
from app.services.prices import Level, Scope

_FIELDS = {
    "txn_month": 1, "block": 1, "street": 1, "storey_min": 1, "storey_max": 1, "floor_area_sqm": 1,
    "resale_price": 1, "lease_commence_year": 1,
}  # fmt: skip


def _month(d: date) -> datetime:
    return datetime(d.year, d.month, 1, tzinfo=UTC)


def comparables_filter(scope: Scope) -> dict:
    query: dict = {
        "flat_type": scope.flat_type,
        "txn_month": {"$gte": _month(scope.window.start), "$lte": _month(scope.window.end)},
    }
    if scope.level == Level.BLOCK:
        query["building_id"] = scope.building_id
    else:
        query["town"] = scope.town
        if scope.level == Level.STREET:
            query["street"] = scope.street
    if scope.storey_min is not None:
        query["storey_min"] = scope.storey_min
    return query


class MongoPriceRepo:
    """Built around one Database (docs/conventions.md, section 3)."""

    def __init__(self, db: Database):
        self.db = db

    def comparables(self, scope: Scope) -> list[Comparable]:
        docs = self.db.resale_transactions.find(comparables_filter(scope), _FIELDS).sort(
            [("txn_month", -1), ("_id", -1)]
        )
        return [
            Comparable(
                txn_id=d["_id"],
                txn_month=d["txn_month"].date(),
                block=d["block"],
                street=d["street"],
                storey_min=d["storey_min"],
                storey_max=d["storey_max"],
                floor_area_sqm=d["floor_area_sqm"].to_decimal(),
                resale_price=d["resale_price"].to_decimal(),
                lease_year=d.get("lease_commence_year"),
            )
            for d in docs
        ]
