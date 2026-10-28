"""Shared test support for admin sale tests: record the state, then put both databases back exactly.

Admin tests record and delete sales (and sometimes delete a loaded sale), which the sample's other tests must not
see. `AdminCleanup` is used from a fixture and finished in its teardown, so a failed assertion still cleans up. It
connects as the schema owner (PostgreSQL) and the ETL user (MongoDB), the identities allowed to write everything.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from pymongo import MongoClient

from app.config import Settings
from app.repos.mongo.building import MongoBuildingRepo
from app.repos.pg.building import PgBuildingRepo
from db.mongo.stats import stats_for_building


class AdminCleanup:
    def __init__(self, settings: Settings):
        if not settings.pg_owner_password or not settings.mongo_etl_password:
            pytest.skip("PG_OWNER_PASSWORD and MONGO_ETL_PASSWORD are needed to clean up admin tests")
        self.owner = psycopg.connect(settings.pg_dsn("neighboar_owner"), autocommit=True)
        self.admin = psycopg.connect(settings.pg_dsn("app_admin"), autocommit=True)
        self.client = MongoClient(settings.mongo_uri("etl", direct=True), serverSelectionTimeoutMS=5000, tz_aware=True)
        self.etl = self.client[settings.mongo_db]
        self.pg_baseline = self._admin_ids_pg()
        self.mongo_baseline = self._admin_ids_mongo()
        self.txn_counter = self.etl.counters.find_one({"_id": "txn"})["seq"]
        self.watched: dict[int, tuple] = {}
        self.snapshots: list[tuple[dict, dict | None]] = []

    # -- state -----------------------------------------------------------------------------------------------

    def _admin_ids_pg(self) -> set[int]:
        rows = self.owner.execute("SELECT txn_id FROM resale_transaction WHERE source = 'admin'").fetchall()
        return {r[0] for r in rows}

    def _admin_ids_mongo(self) -> set[int]:
        return {d["_id"] for d in self.etl.resale_transactions.find({"source": "admin"}, {"_id": 1})}

    def building_dtos(self, building_id: int) -> tuple:
        return PgBuildingRepo(self.owner).get(building_id), MongoBuildingRepo(self.etl).get(building_id)

    def watch(self, building_id: int) -> None:
        """Remember a building's page data now; finish() asserts it is back exactly."""
        self.watched.setdefault(building_id, self.building_dtos(building_id))

    def capture_snapshot(self, txn_id: int) -> None:
        """Keep the full PostgreSQL row and MongoDB document of a loaded sale before a test deletes it."""
        with self.owner.cursor(row_factory=dict_row) as cur:
            row = cur.execute("SELECT * FROM resale_transaction WHERE txn_id = %s", (txn_id,)).fetchone()
        assert row is not None and row["source"] == "snapshot"
        doc = self.etl.resale_transactions.find_one({"_id": txn_id})
        assert doc is not None and doc["source"] == "snapshot"
        self.snapshots.append((row, doc))
        self.watch(row["building_id"])

    # -- teardown --------------------------------------------------------------------------------------------

    def finish(self) -> None:
        try:
            self._restore()
        finally:
            self.owner.close()
            self.admin.close()
            self.client.close()

    def _restore(self) -> None:
        touched = set(self.watched)
        for row, doc in self.snapshots:
            touched.add(row["building_id"])
            present = self.owner.execute("SELECT 1 FROM resale_transaction WHERE txn_id = %s", (row["txn_id"],))
            if present.fetchone() is None:
                columns = sql.SQL(", ").join(sql.Identifier(c) for c in row)
                marks = sql.SQL(", ").join(sql.Placeholder() for _ in row)
                insert = sql.SQL("INSERT INTO resale_transaction ({}) VALUES ({})").format(columns, marks)
                self.owner.execute(insert, list(row.values()))
            if self.etl.resale_transactions.count_documents({"_id": doc["_id"]}) == 0:
                self.etl.resale_transactions.insert_one(doc)

        extra_pg = self._admin_ids_pg() - self.pg_baseline
        if extra_pg:
            rows = self.owner.execute(
                "DELETE FROM resale_transaction WHERE txn_id = ANY(%s) RETURNING building_id", (list(extra_pg),)
            ).fetchall()
            touched |= {r[0] for r in rows}
        extra_mongo = self._admin_ids_mongo() - self.mongo_baseline
        if extra_mongo:
            touched |= {
                d["building_id"]
                for d in self.etl.resale_transactions.find({"_id": {"$in": list(extra_mongo)}}, {"building_id": 1})
            }
            self.etl.resale_transactions.delete_many({"_id": {"$in": list(extra_mongo)}})

        # Admin writes advance the counter for good; put it back so the loaded state (counter = max id) holds again.
        self.etl.counters.update_one({"_id": "txn"}, {"$set": {"seq": self.txn_counter}})

        # Recompute each touched block's stored stats with the same pipeline the transactions use.
        for building_id in touched:
            self.etl.building_profiles.update_one(
                {"_id": building_id}, {"$set": stats_for_building(self.etl, building_id)}
            )
        self.admin.execute("SELECT refresh_town_stats()")

        for building_id, (pg_before, mongo_before) in self.watched.items():
            pg_after, mongo_after = self.building_dtos(building_id)
            assert pg_after == pg_before, f"PostgreSQL building {building_id} is not back to its pre-test value"
            assert mongo_after == mongo_before, f"MongoDB building {building_id} is not back to its pre-test value"
