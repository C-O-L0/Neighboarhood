"""Per-block statistics embedded in building_profiles (computed pattern + subset pattern).

One aggregation over resale_transactions produces, for each block:
  stats         txn_count, median_price, avg_psm, by_flat_type[], last_txn_month
  recent_sales  the latest 10 sales (subset pattern)

The rules match PostgreSQL so the two databases agree (docs/BUILD_PLAN.md section 6):
  median   element floor((n-1)/2) of the ascending prices, the same value as
           percentile_disc(0.5); $median is approximate-only on 8.3, so it is not used
  avg_psm  mean of price / floor area, rounded to 2 dp
  order    newest month first, then highest txn id, for recent_sales

Two ways to apply it:
  rebuild_all_stats   $merge into building_profiles: the bulk rebuild after a load,
                      the counterpart of REFRESH MATERIALIZED VIEW
  stats_for_building  plain aggregate for one block, used inside a transaction when
                      an admin records or deletes a sale ($merge is not allowed there)
"""

from __future__ import annotations

from pymongo.client_session import ClientSession
from pymongo.database import Database

RECENT = 10


def _median(arr: str, n: str) -> dict:
    return {
        "$arrayElemAt": [
            {"$sortArray": {"input": arr, "sortBy": 1}},
            {"$floor": {"$divide": [{"$subtract": [n, 1]}, 2]}},
        ]
    }


def stats_pipeline(match: dict | None = None) -> list[dict]:
    psm = {"$divide": ["$resale_price", "$floor_area_sqm"]}
    sale = {
        "txn_id": "$_id",
        "txn_month": "$txn_month",
        "flat_type": "$flat_type",
        "flat_model": "$flat_model",
        "storey_min": "$storey_min",
        "storey_max": "$storey_max",
        "floor_area_sqm": "$floor_area_sqm",
        "resale_price": "$resale_price",
    }
    return [
        *([{"$match": match}] if match else []),
        # 1. per block and flat type
        {
            "$group": {
                "_id": {"b": "$building_id", "ft": "$flat_type"},
                "n": {"$sum": 1},
                "prices": {"$push": "$resale_price"},
                "sum_psm": {"$sum": psm},
                "last": {"$max": "$txn_month"},
                "recent": {"$topN": {"n": RECENT, "sortBy": {"txn_month": -1, "_id": -1}, "output": sale}},
            }
        },
        {"$sort": {"_id.b": 1, "_id.ft": 1}},
        # 2. per block
        {
            "$group": {
                "_id": "$_id.b",
                "txn_count": {"$sum": "$n"},
                "sum_psm": {"$sum": "$sum_psm"},
                "prices": {"$push": "$prices"},
                "last_txn_month": {"$max": "$last"},
                "recent": {"$push": "$recent"},
                "by_flat_type": {
                    "$push": {
                        "flat_type": "$_id.ft",
                        "txn_count": "$n",
                        "median_price": _median("$prices", "$n"),
                        "avg_psm": {"$round": [{"$divide": ["$sum_psm", "$n"]}, 2]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 1,
                "stats": {
                    "txn_count": "$txn_count",
                    "median_price": _median(
                        {
                            "$reduce": {
                                "input": "$prices",
                                "initialValue": [],
                                "in": {"$concatArrays": ["$$value", "$$this"]},
                            }
                        },
                        "$txn_count",
                    ),
                    "avg_psm": {"$round": [{"$divide": ["$sum_psm", "$txn_count"]}, 2]},
                    "by_flat_type": "$by_flat_type",
                    "last_txn_month": "$last_txn_month",
                },
                "recent_sales": {
                    "$slice": [
                        {
                            "$sortArray": {
                                "input": {
                                    "$reduce": {
                                        "input": "$recent",
                                        "initialValue": [],
                                        "in": {"$concatArrays": ["$$value", "$$this"]},
                                    }
                                },
                                "sortBy": {"txn_month": -1, "txn_id": -1},
                            }
                        },
                        RECENT,
                    ]
                },
            }
        },
    ]


EMPTY_STATS = {"txn_count": 0, "median_price": None, "avg_psm": None, "by_flat_type": [], "last_txn_month": None}


def rebuild_all_stats(db: Database) -> int:
    """Recompute every block's stats with $merge. Blocks with no sales are reset to empty."""
    db.building_profiles.update_many({}, {"$set": {"stats": EMPTY_STATS, "recent_sales": []}})
    db.resale_transactions.aggregate(
        [
            *stats_pipeline(),
            {
                "$merge": {
                    "into": "building_profiles",
                    "on": "_id",
                    "whenMatched": [{"$set": {"stats": "$$new.stats", "recent_sales": "$$new.recent_sales"}}],
                    "whenNotMatched": "discard",
                }
            },
        ],
        allowDiskUse=True,
    )
    return db.building_profiles.count_documents({"stats.txn_count": {"$gt": 0}})


def stats_for_building(db: Database, building_id: int, session: ClientSession | None = None) -> dict:
    """{"stats": ..., "recent_sales": ...} for one block, for an updateOne inside a transaction."""
    docs = list(db.resale_transactions.aggregate(stats_pipeline({"building_id": building_id}), session=session))
    if not docs:
        return {"stats": EMPTY_STATS, "recent_sales": []}
    return {"stats": docs[0]["stats"], "recent_sales": docs[0]["recent_sales"]}
