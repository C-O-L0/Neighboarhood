"""Where a buyer can afford a flat type, from MongoDB: one aggregate per method on resale_transactions.

The latest month sorts on the type_month index and keeps the last month per flat type ($group with $last),
which the planner runs as a DISTINCT_SCAN: one index key per flat type. The window aggregates $match on
flat_type and txn_month, the same index. Percentiles sort the price arrays and take the element PostgreSQL's
percentile_disc picks: position ceil(p * n) counting from 1, with p as a double so the rounding matches.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from bson.decimal128 import Decimal128
from pymongo.database import Database

from app.repos.dto import BlockFit, BlockFitPage, FlatTypeRange, TownBaseline, TownFit
from app.repos.mongo.building import coord
from app.services.explore import PAGE_SIZE, BlockFilter, Window
from app.services.food import round_metres
from app.services.search import RESALE_TYPES
from etl.clean import FLAT_TYPE_IDS


def at_percentile(values: str, n: str | dict, p: float) -> dict:
    """The sorted value at position ceil(p * n), counting from 1 (as percentile_disc)."""
    index = {"$max": [{"$subtract": [{"$ceil": {"$multiply": [p, n]}}, 1]}, 0]}
    return {"$arrayElemAt": [{"$sortArray": {"input": values, "sortBy": 1}}, index]}


def _month(d: date) -> datetime:
    return datetime(d.year, d.month, 1, tzinfo=UTC)


def _in_window(window: Window) -> dict:
    return {"$gte": _month(window.start), "$lte": _month(window.end)}


def ranges_pipeline(window: Window) -> list[dict]:
    return [
        {"$match": {"flat_type": {"$in": RESALE_TYPES}, "txn_month": _in_window(window)}},
        {
            "$group": {
                "_id": "$flat_type",
                "n": {"$sum": 1},
                "areas": {"$push": "$floor_area_sqm"},
                "prices": {"$push": "$resale_price"},
            }
        },
        {
            "$project": {
                "n": 1,
                "area_median": at_percentile("$areas", "$n", 0.5),
                "price_low": at_percentile("$prices", "$n", 0.1),
                "price_high": at_percentile("$prices", "$n", 0.9),
            }
        },
    ]


def towns_pipeline(flat_type: str, budget: Decimal, window: Window) -> list[dict]:
    """Built only from the validated, typed values (docs/conventions.md, 8)."""
    limit = Decimal128(budget)
    return [
        {"$match": {"flat_type": flat_type, "txn_month": _in_window(window)}},
        {
            "$group": {
                "_id": "$town",
                "n": {"$sum": 1},
                "prices": {"$push": "$resale_price"},
                "within_budget": {"$sum": {"$cond": [{"$lte": ["$resale_price", limit]}, 1, 0]}},
            }
        },
        {"$project": {"n": 1, "within_budget": 1, "median_price": at_percentile("$prices", "$n", 0.5)}},
        {"$sort": {"median_price": 1, "_id": 1}},
    ]


def baselines_pipeline(flat_type: str, window: Window) -> list[dict]:
    """Lease years are pushed without nulls, as percentile_disc ignores nulls, so the lease median counts its own n."""
    leases = {"$filter": {"input": "$leases", "cond": {"$ne": ["$$this", None]}}}
    return [
        {"$match": {"flat_type": flat_type, "txn_month": _in_window(window)}},
        {
            "$group": {
                "_id": "$town",
                "n": {"$sum": 1},
                "storeys": {"$push": {"$add": ["$storey_min", "$storey_max"]}},
                "leases": {"$push": "$lease_commence_year"},
                "areas": {"$push": "$floor_area_sqm"},
            }
        },
        {"$set": {"leases": leases}},
        {
            "$project": {
                "n": 1,
                "storey_sum": at_percentile("$storeys", "$n", 0.5),
                "lease_year": at_percentile("$leases", {"$size": "$leases"}, 0.5),
                "area_median": at_percentile("$areas", "$n", 0.5),
            }
        },
        {"$sort": {"_id": 1}},
    ]


def _block_medians(sales: str) -> dict:
    """$project fields for the medians of an array of sales with resale_price, storey_sum and floor_area_sqm."""
    n = {"$size": sales}
    return {
        "n": n,
        "median_price": at_percentile({"$map": {"input": sales, "in": "$$this.resale_price"}}, n, 0.5),
        "storey_sum": at_percentile({"$map": {"input": sales, "in": "$$this.storey_sum"}}, n, 0.5),
        "area_median": at_percentile({"$map": {"input": sales, "in": "$$this.floor_area_sqm"}}, n, 0.5),
    }


_SALE_FIELDS = {"resale_price": 1, "floor_area_sqm": 1, "storey_sum": {"$add": ["$storey_min", "$storey_max"]}}


# The page's blocks get their location from building_profiles (the sales carry none); 20 lookups by _id.
_LOCATION = [
    {"$lookup": {"from": "building_profiles", "localField": "_id", "foreignField": "_id", "as": "building",
                 "pipeline": [{"$project": {"location": 1}}]}},
    {"$set": {"location": {"$first": "$building.location"}}},
    {"$unset": "building"},
]  # fmt: skip


def _filters_and_page(f: BlockFilter, sort: dict) -> list[dict]:
    """The budget, lease and area filters on the per-block medians, then the total and one page."""
    match: dict = {"median_price": {"$lte": Decimal128(f.budget)}}
    if f.min_lease_year is not None:
        match["lease_year"] = {"$gte": f.min_lease_year}
    if f.min_area is not None:
        match["area_median"] = {"$gte": Decimal128(f.min_area)}
    return [
        {"$match": match},
        {
            "$facet": {
                "total": [{"$count": "n"}],
                "rows": [{"$sort": sort}, {"$skip": f.offset}, {"$limit": PAGE_SIZE}, *_LOCATION],
            }
        },
    ]


def blocks_pipeline(f: BlockFilter) -> list[dict]:
    """Without a place, on resale_transactions: the window's sales grouped by block (town_type_month or
    type_month index). Built only from the validated, typed values in the filter."""
    match: dict = {"flat_type": f.flat_type, "txn_month": _in_window(f.window)}
    if f.town is not None:
        match["town"] = f.town
    return [
        {"$match": match},
        {
            "$group": {
                "_id": "$building_id",
                "block": {"$first": "$block"},
                "street": {"$first": "$street"},
                "town": {"$first": "$town"},
                "lease_year": {"$first": "$lease_commence_year"},
                "sales": {
                    "$push": {
                        "resale_price": "$resale_price",
                        "floor_area_sqm": "$floor_area_sqm",
                        "storey_sum": {"$add": ["$storey_min", "$storey_max"]},
                    }
                },
            }
        },
        {"$project": {"block": 1, "street": 1, "town": 1, "lease_year": 1, **_block_medians("$sales")}},
        *_filters_and_page(f, {"median_price": 1, "_id": 1}),
    ]


def blocks_near_pipeline(f: BlockFilter, origin: dict) -> list[dict]:
    """With a place, on building_profiles: $geoNear finds the located blocks within the radius (the place
    included), then each block's window sales of the flat type come from a $lookup on building_id (the
    building_month index)."""
    query: dict = {} if f.town is None else {"town.name": f.town}
    return [
        {
            "$geoNear": {
                "near": origin,
                "key": "location",
                "distanceField": "distance",
                "spherical": True,
                "maxDistance": f.radius_m,
                "query": query,
            }
        },
        {
            "$lookup": {
                "from": "resale_transactions",
                "localField": "_id",
                "foreignField": "building_id",
                "pipeline": [
                    {"$match": {"flat_type": f.flat_type, "txn_month": _in_window(f.window)}},
                    {"$project": _SALE_FIELDS},
                ],
                "as": "sales",
            }
        },
        {"$match": {"sales.0": {"$exists": True}}},
        {
            "$project": {
                "block": 1,
                "street": 1,
                "town": "$town.name",
                "lease_year": "$lease_commence_year",
                "distance": 1,
                **_block_medians("$sales"),
            }
        },
        *_filters_and_page(f, {"distance": 1, "_id": 1}),
    ]


def _block_fit(d: dict) -> BlockFit:
    return BlockFit(
        building_id=d["_id"],
        block=d["block"],
        street=d["street"],
        town=d["town"],
        n=d["n"],
        median_price=d["median_price"].to_decimal(),
        storey_sum=d["storey_sum"],
        area_median=d["area_median"].to_decimal(),
        lease_year=d.get("lease_year"),
        distance_m=round_metres(d["distance"]) if "distance" in d else None,
        latitude=coord(d["location"]["coordinates"][1]) if isinstance(d.get("location"), dict) else None,
        longitude=coord(d["location"]["coordinates"][0]) if isinstance(d.get("location"), dict) else None,
    )


class MongoExploreRepo:
    """Built around one Database (docs/conventions.md, section 3)."""

    def __init__(self, db: Database):
        self.db = db

    def latest_month(self) -> date | None:
        pipeline = [
            {"$sort": {"flat_type": 1, "txn_month": 1}},
            {"$group": {"_id": "$flat_type", "last": {"$last": "$txn_month"}}},
        ]
        months = [d["last"] for d in self.db.resale_transactions.aggregate(pipeline)]
        return max(months).date() if months else None

    def flat_type_ranges(self, window: Window) -> list[FlatTypeRange]:
        docs = list(self.db.resale_transactions.aggregate(ranges_pipeline(window)))
        docs.sort(key=lambda d: FLAT_TYPE_IDS[d["_id"]])  # the list order, as PostgreSQL orders by flat_type_id
        return [
            FlatTypeRange(
                flat_type=d["_id"],
                n=d["n"],
                area_median=d["area_median"].to_decimal(),
                price_low=d["price_low"].to_decimal(),
                price_high=d["price_high"].to_decimal(),
            )
            for d in docs
        ]

    def towns_fit(self, flat_type: str, budget: Decimal, window: Window) -> list[TownFit]:
        docs = self.db.resale_transactions.aggregate(towns_pipeline(flat_type, budget, window))
        return [
            TownFit(
                town=d["_id"],
                n=d["n"],
                median_price=d["median_price"].to_decimal(),
                within_budget=d["within_budget"],
            )
            for d in docs
        ]

    def baselines(self, flat_type: str, window: Window) -> list[TownBaseline]:
        docs = self.db.resale_transactions.aggregate(baselines_pipeline(flat_type, window))
        return [
            TownBaseline(
                town=d["_id"],
                n=d["n"],
                storey_sum=d["storey_sum"],
                lease_year=d.get("lease_year"),
                area_median=d["area_median"].to_decimal(),
            )
            for d in docs
        ]

    def blocks_fit(self, f: BlockFilter) -> BlockFitPage:
        if f.place is None:
            result = next(self.db.resale_transactions.aggregate(blocks_pipeline(f)))
        else:
            place = self.db.building_profiles.find_one(
                {"_id": f.place, "location": {"$type": "object"}}, {"location": 1}
            )
            if place is None:  # unknown or unlocated: nothing is within any distance of it, as in PostgreSQL
                return BlockFitPage(total=0, rows=[])
            result = next(self.db.building_profiles.aggregate(blocks_near_pipeline(f, place["location"])))
        total = result["total"][0]["n"] if result["total"] else 0
        return BlockFitPage(total=total, rows=[_block_fit(d) for d in result["rows"]])
