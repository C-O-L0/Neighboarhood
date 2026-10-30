"""Places from MongoDB: the block picker and blocks within a radius, on building_profiles.

The picker matches the street with an escaped regex and sorts on the block number's length so 9 comes before
10, as PostgreSQL does. Nearby blocks use $geoNear on the location 2dsphere index (spherical, metres, radius
6378.1 km); $geoNear orders by distance only, so a $sort on (distance, _id) settles ties as PostgreSQL does.
"""

from __future__ import annotations

import re

from pymongo.database import Database

from app.repos.dto import NearBlock, Place
from app.services.food import round_metres


def find_pipeline(block: str | None, street: str, limit: int) -> list[dict]:
    """Built only from validated, typed values (docs/conventions.md, 8): the street is regex-escaped."""
    match: dict = {"street": {"$regex": re.escape(street)}}
    if block is not None:
        match["block"] = block
    return [
        {"$match": match},
        {"$set": {"block_len": {"$strLenBytes": "$block"}}},
        {"$sort": {"street": 1, "block_len": 1, "block": 1, "_id": 1}},
        {"$limit": limit},
        {
            "$project": {
                "block": 1,
                "street": 1,
                "town": "$town.name",
                "located": {"$eq": [{"$type": "$location"}, "object"]},
            }
        },
    ]


class MongoPlaceRepo:
    """Built around one Database (docs/conventions.md, section 3)."""

    def __init__(self, db: Database):
        self.db = db

    def find(self, block: str | None, street: str, limit: int) -> list[Place]:
        docs = self.db.building_profiles.aggregate(find_pipeline(block, street, limit))
        return [Place(d["_id"], d["block"], d["street"], d["town"], d["located"]) for d in docs]

    def near(self, building_id: int, radius_m: int) -> list[NearBlock] | None:
        origin = self.db.building_profiles.find_one(
            {"_id": building_id, "location": {"$type": "object"}}, {"location": 1}
        )
        if origin is None:
            return None
        pipeline = [
            {
                "$geoNear": {
                    "near": origin["location"],
                    "key": "location",
                    "distanceField": "d",
                    "spherical": True,
                    "maxDistance": radius_m,
                    "query": {"_id": {"$ne": building_id}, "residential": {"$ne": False}},
                }
            },
            {"$sort": {"d": 1, "_id": 1}},
            {"$project": {"block": 1, "street": 1, "town": "$town.name", "d": 1}},
        ]
        docs = self.db.building_profiles.aggregate(pipeline)
        return [NearBlock(d["_id"], d["block"], d["street"], d["town"], round_metres(d["d"])) for d in docs]
