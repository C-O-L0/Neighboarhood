"""Town rankings from MongoDB: one aggregate on resale_transactions.

$facet computes the town medians and the island medians in one pass over the matched sales;
each town row picks its island median with $filter and $arrayElemAt, and $setWindowFields
with $rank ranks the towns within each flat type. Medians sort the price arrays and take
element floor((n-1)/2); $median is not used (approximate only on 8.3).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pymongo.database import Database

from app.repos.dto import TownRank
from app.services.rankings import RankingQuery


def _median(prices, n) -> dict:
    return {
        "$arrayElemAt": [
            {"$sortArray": {"input": prices, "sortBy": 1}},
            {"$floor": {"$divide": [{"$subtract": [n, 1]}, 2]}},
        ]
    }


def build_pipeline(q: RankingQuery) -> list[dict]:
    """Pipeline built only from the validated, typed query values."""
    match: dict = {
        "txn_month": {
            "$gte": datetime(q.year, 1, 1, tzinfo=UTC),
            "$lt": datetime(q.year + 1, 1, 1, tzinfo=UTC),
        }
    }
    if q.flat_type is not None:
        match["flat_type"] = q.flat_type
    return [
        {"$match": match},
        {
            "$facet": {
                "towns": [
                    {
                        "$group": {
                            "_id": {"flat_type": "$flat_type", "town": "$town"},
                            "n": {"$sum": 1},
                            "prices": {"$push": "$resale_price"},
                            "sum_psm": {"$sum": {"$divide": ["$resale_price", "$floor_area_sqm"]}},
                        }
                    },
                    {
                        "$project": {
                            "n": 1,
                            "median_price": _median("$prices", "$n"),
                            "avg_psm": {"$round": [{"$divide": ["$sum_psm", "$n"]}, 2]},
                        }
                    },
                ],
                "island": [
                    {"$group": {"_id": "$flat_type", "n": {"$sum": 1}, "prices": {"$push": "$resale_price"}}},
                    {"$project": {"median_price": _median("$prices", "$n")}},
                ],
            }
        },
        {"$unwind": "$towns"},
        {
            "$set": {
                "island_row": {
                    "$arrayElemAt": [
                        {
                            "$filter": {
                                "input": "$island",
                                "as": "i",
                                "cond": {"$eq": ["$$i._id", "$towns._id.flat_type"]},
                            }
                        },
                        0,
                    ]
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "flat_type": "$towns._id.flat_type",
                "town": "$towns._id.town",
                "n": "$towns.n",
                "median_price": "$towns.median_price",
                "avg_psm": "$towns.avg_psm",
                "island_median": "$island_row.median_price",
                "beats_island": {"$gt": ["$towns.median_price", "$island_row.median_price"]},
            }
        },
        {
            "$setWindowFields": {
                "partitionBy": "$flat_type",
                "sortBy": {"median_price": -1},
                "output": {"rank": {"$rank": {}}},
            }
        },
        {"$sort": {"flat_type": 1, "rank": 1, "town": 1}},
    ]


class MongoRankingRepo:
    """Built around one Database (docs/conventions.md, section 3)."""

    def __init__(self, db: Database):
        self.db = db

    def rank(self, query: RankingQuery) -> list[TownRank]:
        docs = list(self.db.resale_transactions.aggregate(build_pipeline(query)))
        return [
            TownRank(
                flat_type=d["flat_type"],
                rank=d["rank"],
                town=d["town"],
                n=d["n"],
                median_price=d["median_price"].to_decimal(),
                avg_psm=d["avg_psm"].to_decimal(),
                island_median=d["island_median"].to_decimal(),
                beats_island=d["beats_island"],
            )
            for d in docs
        ]
