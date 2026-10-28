"""Change both databases so that a restore has something to undo (used by scripts/check_backup_roundtrip.sh).

    python -m db.roundtrip_mutate

Through the application's own identities and repositories it
  - deletes one saved block (PostgreSQL saved_building as app_rw; the embedded entry in buyers as app),
  - records one admin sale in each database (app_admin, then the town-figures refresh in PostgreSQL),
  - alters one block profile's stats in MongoDB (app_admin).
Prints one line per change. Test helper: run it only on a database you are prepared to restore.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal

import psycopg
from pymongo import MongoClient

from app.config import get_settings
from app.repos.dto import NewSale, SaleOutcome
from app.repos.mongo.admin import MongoAdminRepo
from app.repos.pg.admin import PgAdminRepo


def main() -> int:
    s = get_settings()
    mongo_rw = MongoClient(s.mongo_uri("app"), serverSelectionTimeoutMS=10000, tz_aware=True)
    mongo_admin = MongoClient(s.mongo_uri("app_admin"), serverSelectionTimeoutMS=10000, tz_aware=True)
    try:
        with (
            psycopg.connect(s.pg_dsn("app_rw"), autocommit=True) as rw,
            psycopg.connect(s.pg_dsn("app_admin"), autocommit=True) as admin,
        ):
            # 1. one saved block, gone from both databases
            buyer_id, building_id = rw.execute(
                "SELECT buyer_id, building_id FROM saved_building ORDER BY buyer_id, building_id LIMIT 1"
            ).fetchone()
            rw.execute("DELETE FROM saved_building WHERE buyer_id = %s AND building_id = %s", (buyer_id, building_id))
            pulled = mongo_rw[s.mongo_db].buyers.update_one(
                {"_id": buyer_id}, {"$pull": {"saved": {"building_id": building_id}}}
            )
            if pulled.modified_count != 1:
                raise SystemExit("[mutate] the saved block was not found in MongoDB")
            print(f"[mutate] deleted saved block {building_id} of buyer {buyer_id} in both databases")

            # 2. one admin sale in each database
            target = admin.execute(
                "SELECT building_id FROM building WHERE year_completed IS NOT NULL AND year_completed <= 2000 "
                "ORDER BY building_id LIMIT 1"
            ).fetchone()[0]
            sale = NewSale(
                building_id=target,
                txn_month=date(2024, 6, 1),
                flat_type="4 ROOM",
                flat_model="Improved",
                storey_min=4,
                storey_max=6,
                floor_area_sqm=Decimal("90.0"),
                remaining_lease_months=700,
                resale_price=Decimal("123456.78"),
            )
            pg_repo = PgAdminRepo(admin)
            outcome = pg_repo.record(sale).outcome
            pg_repo.refresh()
            mongo_outcome = MongoAdminRepo(mongo_admin[s.mongo_db]).record(sale).outcome
            if (outcome, mongo_outcome) != (SaleOutcome.OK, SaleOutcome.OK):
                raise SystemExit(f"[mutate] the admin sale was not recorded: {outcome}, {mongo_outcome}")
            print(f"[mutate] recorded one admin sale for block {target} in both databases")

        # 3. one profile's stats
        profiles = mongo_admin[s.mongo_db].building_profiles
        doc = profiles.find_one({"_id": {"$ne": target}, "stats.txn_count": {"$gt": 0}}, {"_id": 1})
        profiles.update_one({"_id": doc["_id"]}, {"$inc": {"stats.txn_count": 1}})
        print(f"[mutate] changed the stats of profile {doc['_id']} in MongoDB")
    finally:
        mongo_rw.close()
        mongo_admin.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
