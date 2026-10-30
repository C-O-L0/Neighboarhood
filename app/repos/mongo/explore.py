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

from app.repos.dto import FlatTypeRange, TownBaseline, TownFit
from app.services.explore import Window
from app.services.search import RESALE_TYPES
from etl.clean import FLAT_TYPE_IDS


def _at_percentile(values: str, n: str | dict, p: float) -> dict:
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
                "area_median": _at_percentile("$areas", "$n", 0.5),
                "price_low": _at_percentile("$prices", "$n", 0.1),
                "price_high": _at_percentile("$prices", "$n", 0.9),
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
        {"$project": {"n": 1, "within_budget": 1, "median_price": _at_percentile("$prices", "$n", 0.5)}},
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
                "storey_sum": _at_percentile("$storeys", "$n", 0.5),
                "lease_year": _at_percentile("$leases", {"$size": "$leases"}, 0.5),
                "area_median": _at_percentile("$areas", "$n", 0.5),
            }
        },
        {"$sort": {"_id": 1}},
    ]


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
