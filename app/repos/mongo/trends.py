"""Price trends from MongoDB: one aggregate on resale_transactions.

$group by month, then $setWindowFields with month range windows: [-12, -12] for the previous
year and [-11, 0] for the rolling 12 calendar months. Medians sort the price arrays and take
element floor((n-1)/2); $median is not used (approximate only on 8.3).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from pymongo.database import Database

from app.repos.dto import TrendPoint
from app.services.trends import TrendQuery, add_months


def _midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _median(prices, n) -> dict:
    return {
        "$arrayElemAt": [
            {"$sortArray": {"input": prices, "sortBy": 1}},
            {"$floor": {"$divide": [{"$subtract": [n, 1]}, 2]}},
        ]
    }


def _concat(field: str) -> dict:
    """Flatten the window's array of price arrays."""
    return {"$reduce": {"input": field, "initialValue": [], "in": {"$concatArrays": ["$$value", "$$this"]}}}


def build_pipeline(q: TrendQuery) -> list[dict]:
    """Pipeline built only from the validated, typed query values."""
    match: dict = {"town": q.town, "flat_type": q.flat_type}
    bounds: dict = {}
    if q.month_from is not None:
        match["txn_month"] = {"$gte": _midnight(add_months(q.month_from, -12))}  # reach back a year
        bounds["$gte"] = _midnight(q.month_from)
    if q.month_to is not None:
        match.setdefault("txn_month", {})["$lte"] = _midnight(q.month_to)
        bounds["$lte"] = _midnight(q.month_to)
    prev = {"range": [-12, -12], "unit": "month"}
    roll = {"range": [-11, 0], "unit": "month"}
    return [
        {"$match": match},
        {
            "$group": {
                "_id": "$txn_month",
                "n": {"$sum": 1},
                "prices": {"$push": "$resale_price"},
                "sum_psm": {"$sum": {"$divide": ["$resale_price", "$floor_area_sqm"]}},
            }
        },
        {"$set": {"txn_month": "$_id"}},
        {
            "$setWindowFields": {
                "sortBy": {"txn_month": 1},
                "output": {
                    "prev_n": {"$sum": "$n", "window": prev},
                    "prev_prices": {"$push": "$prices", "window": prev},
                    "roll_n": {"$sum": "$n", "window": roll},
                    "roll_prices": {"$push": "$prices", "window": roll},
                },
            }
        },
        *([{"$match": {"txn_month": bounds}}] if bounds else []),
        {
            "$project": {
                "_id": 0,
                "month": "$txn_month",
                "n": 1,
                "median_price": _median("$prices", "$n"),
                "avg_psm": {"$round": [{"$divide": ["$sum_psm", "$n"]}, 2]},
                "prev_year_median": {
                    "$cond": [{"$gt": ["$prev_n", 0]}, _median(_concat("$prev_prices"), "$prev_n"), None]
                },
                "rolling_n": "$roll_n",
                "rolling_median": _median(_concat("$roll_prices"), "$roll_n"),
            }
        },
        {"$sort": {"month": 1}},
    ]


def _dec(value) -> Decimal | None:
    return None if value is None else value.to_decimal()


class MongoTrendRepo:
    """Built around one Database (docs/conventions.md, section 3)."""

    def __init__(self, db: Database):
        self.db = db

    def trend(self, query: TrendQuery) -> list[TrendPoint]:
        docs = list(self.db.resale_transactions.aggregate(build_pipeline(query)))
        return [
            TrendPoint(
                month=d["month"].date(),
                n=d["n"],
                median_price=_dec(d["median_price"]),
                avg_psm=_dec(d["avg_psm"]),
                prev_year_median=_dec(d["prev_year_median"]),
                rolling_n=d["rolling_n"],
                rolling_median=_dec(d["rolling_median"]),
            )
            for d in docs
        ]
