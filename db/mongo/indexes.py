"""Secondary indexes for the MongoDB collections.

Same key shapes as db/postgres/004_indexes.sql, so the benchmark compares like
with like. Created by etl/load_mongo.py after the bulk insert (building an index
once over loaded data is faster than maintaining it row by row).
"""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING, GEOSPHERE, IndexModel
from pymongo.database import Database

INDEXES: dict[str, list[IndexModel]] = {
    "building_profiles": [
        IndexModel([("block", ASCENDING), ("street", ASCENDING)], name="block_street_unique", unique=True),
        IndexModel([("town.name", ASCENDING)], name="town_name"),
        IndexModel([("location", GEOSPHERE)], name="location_2dsphere"),
    ],
    "resale_transactions": [
        IndexModel([("flat_type", ASCENDING), ("txn_month", ASCENDING)], name="type_month"),
        IndexModel([("town", ASCENDING), ("flat_type", ASCENDING), ("txn_month", ASCENDING)], name="town_type_month"),
        IndexModel([("building_id", ASCENDING), ("txn_month", DESCENDING)], name="building_month"),
        IndexModel([("flat_type", ASCENDING), ("resale_price", ASCENDING), ("_id", ASCENDING)], name="type_price"),
    ],
    "buyers": [
        IndexModel([("email", ASCENDING)], name="email_unique", unique=True),
        IndexModel([("saved.building_id", ASCENDING)], name="saved_building"),
    ],
    "hawker_centres": [
        IndexModel([("geometry", GEOSPHERE)], name="geometry_2dsphere"),
    ],
    # Households are created at run time, not loaded, so db/migrate.py creates these (APP_COLLECTIONS).
    "households": [
        # Multikey and unique: no buyer id can appear in two households, the MongoDB side of PostgreSQL's
        # household_member primary key. (It does not stop one id appearing twice in one document; the
        # seats and the app's checks do.)
        IndexModel([("members.buyer_id", ASCENDING)], name="member_unique", unique=True),
        IndexModel([("saved.building_id", ASCENDING)], name="saved_building"),
    ],
    "invitations": [
        IndexModel([("invitee_id", ASCENDING)], name="invitee"),
    ],
}
APP_COLLECTIONS = ["households", "invitations"]


def create_indexes(db: Database, collections: list[str] | None = None) -> list[str]:
    created = []
    for name, models in INDEXES.items():
        if collections is None or name in collections:
            created += db[name].create_indexes(models)
    return created


def drop_secondary_indexes(db: Database, collections: list[str]) -> None:
    """Drop everything except _id, before a bulk reload."""
    for name in collections:
        db[name].drop_indexes()
