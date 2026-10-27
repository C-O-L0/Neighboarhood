"""Building page from MongoDB: one read of building_profiles."""

from __future__ import annotations

from decimal import Decimal

from bson.decimal128 import Decimal128
from pymongo.database import Database

from app.repos.dto import BuildingDetail, BuildingStats, FlatTypeStats, RecentSale, SupplyRow
from etl.clean import LAND_USE

_SIX_DP = Decimal("0.000001")


def _dec(value: Decimal128 | None) -> Decimal | None:
    return None if value is None else value.to_decimal()


def _coord(value: float) -> Decimal:
    return Decimal(repr(value)).quantize(_SIX_DP)


class MongoBuildingRepo:
    """Built around one Database (docs/conventions.md, section 3)."""

    def __init__(self, db: Database):
        self.db = db

    def get(self, building_id: int) -> BuildingDetail | None:
        doc = self.db.building_profiles.find_one({"_id": building_id})
        if doc is None:
            return None
        in_property = doc["in_property_dataset"]
        supply = None
        total_units = None
        if "supply" in doc:
            supply = sorted(
                (SupplyRow(s["flat_type"], s["sold"], s["rental"]) for s in doc["supply"]), key=lambda s: s.flat_type
            )
            total_units = sum(s.sold + s.rental for s in supply)
        st = doc["stats"]
        by_type = sorted(
            (
                FlatTypeStats(f["flat_type"], f["txn_count"], _dec(f["median_price"]), _dec(f["avg_psm"]))
                for f in st["by_flat_type"]
            ),
            key=lambda f: f.flat_type,
        )
        last = st["last_txn_month"]
        recent = sorted(
            (
                RecentSale(
                    txn_id=r["txn_id"],
                    txn_month=r["txn_month"].date(),
                    flat_type=r["flat_type"],
                    flat_model=r["flat_model"],
                    storey_min=r["storey_min"],
                    storey_max=r["storey_max"],
                    floor_area_sqm=_dec(r["floor_area_sqm"]),
                    resale_price=_dec(r["resale_price"]),
                )
                for r in doc["recent_sales"]
            ),
            key=lambda r: (r.txn_month, r.txn_id),
            reverse=True,
        )
        loc = doc.get("location")
        lon, lat = (_coord(loc["coordinates"][0]), _coord(loc["coordinates"][1])) if loc else (None, None)
        return BuildingDetail(
            building_id=doc["_id"],
            block=doc["block"],
            street=doc["street"],
            town=doc["town"]["name"],
            town_code=doc["town"]["code"],
            in_property_dataset=in_property,
            year_completed=doc.get("year_completed"),
            lease_commence_year=doc.get("lease_commence_year"),
            max_floor=doc.get("max_floor"),
            land_use={k: doc[k] for k in LAND_USE} if in_property else None,
            latitude=lat,
            longitude=lon,
            supply=supply,
            total_units=total_units,
            stats=BuildingStats(
                txn_count=st["txn_count"],
                median_price=_dec(st["median_price"]),
                avg_psm=_dec(st["avg_psm"]),
                last_txn_month=last.date() if last else None,
                by_flat_type=by_type,
            ),
            recent_sales=recent,
        )

    def find_id(self, block: str, street: str) -> int | None:
        doc = self.db.building_profiles.find_one({"block": block, "street": street}, {"_id": 1})
        return doc["_id"] if doc else None
