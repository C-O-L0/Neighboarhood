"""Food access from MongoDB: $geoNear on hawker_centres.

Block view: one $geoNear (status in its query, spherical, no maxDistance) feeding a $facet that takes the
5 nearest and counts those within 1000 m ($geoNear may not appear inside $facet). Town view: one aggregate
on building_profiles whose $lookup stages run a $geoNear sub-pipeline with near: "$$loc" (nearest one, then
the count within 1000 m). Ties break on _id (the hawker id, the building id).
"""

from __future__ import annotations

from pymongo.database import Database

from app.repos.dto import BlockFoodAccess, HawkerDistance
from app.repos.mongo.building import coord
from app.services.food import EXCLUDED_STATUS, NEAREST_COUNT, RADIUS_M, round_metres

_OPEN = {"properties.STATUS": {"$ne": EXCLUDED_STATUS}}


def _geo_near(near, **extra) -> dict:
    return {
        "$geoNear": {"near": near, "distanceField": "d", "spherical": True, "key": "geometry", "query": _OPEN, **extra}
    }


def block_pipeline(point: dict) -> list[dict]:
    return [
        _geo_near(point),
        {
            "$facet": {
                "nearest": [{"$sort": {"d": 1, "_id": 1}}, {"$limit": NEAREST_COUNT}],
                "within": [{"$match": {"d": {"$lte": RADIUS_M}}}, {"$count": "n"}],
            }
        },
        {"$project": {"nearest": 1, "within_1km": {"$ifNull": [{"$first": "$within.n"}, 0]}}},
    ]


def town_pipeline(town: str) -> list[dict]:
    return [
        {"$match": {"town.name": town, "location": {"$ne": None}}},
        {
            "$lookup": {
                "from": "hawker_centres",
                "let": {"loc": "$location"},
                "pipeline": [_geo_near("$$loc"), {"$sort": {"d": 1, "_id": 1}}, {"$limit": 1}],
                "as": "nearest",
            }
        },
        {
            "$lookup": {
                "from": "hawker_centres",
                "let": {"loc": "$location"},
                "pipeline": [_geo_near("$$loc", maxDistance=RADIUS_M), {"$count": "n"}],
                "as": "c",
            }
        },
        {"$set": {"dist": {"$first": "$nearest.d"}, "within_1km": {"$ifNull": [{"$first": "$c.n"}, 0]}}},
        {"$sort": {"dist": 1, "_id": 1}},
    ]


def _hawker(doc: dict) -> HawkerDistance:
    p = doc["properties"]
    return HawkerDistance(
        hawker_id=doc["_id"],
        name=p["NAME"],
        address=p["ADDRESS_MYENV"],
        status=p["STATUS"],
        stall_count=p["NUMBER_OF_COOKED_FOOD_STALLS"],
        latitude=coord(doc["geometry"]["coordinates"][1]),
        longitude=coord(doc["geometry"]["coordinates"][0]),
        distance_m=round_metres(doc["d"]),
    )


class MongoFoodRepo:
    """Built around one Database (docs/conventions.md, section 3)."""

    def __init__(self, db: Database):
        self.db = db

    def block(self, building_id: int) -> BlockFoodAccess | None:
        """The block's 5 nearest open centres and the number within 1 km; None if it has no coordinates."""
        profile = self.db.building_profiles.find_one({"_id": building_id}, {"block": 1, "street": 1, "location": 1})
        if profile is None or profile.get("location") is None:
            return None
        doc = next(iter(self.db.hawker_centres.aggregate(block_pipeline(profile["location"]))), None)
        nearest = [_hawker(d) for d in doc["nearest"]] if doc else []
        return BlockFoodAccess(
            profile["_id"], profile["block"], profile["street"], nearest, doc["within_1km"] if doc else 0
        )

    def town(self, town: str) -> list[BlockFoodAccess]:
        """Every geocoded block of the town with its nearest open centre, nearest first then building id."""
        docs = self.db.building_profiles.aggregate(town_pipeline(town))
        return [
            BlockFoodAccess(d["_id"], d["block"], d["street"], [_hawker(d["nearest"][0])], d["within_1km"])
            for d in docs
            if d["nearest"]
        ]
