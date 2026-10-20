"""Load the cleaned tables (data/interim/*.parquet) into MongoDB.

Builds the document model from docs/BUILD_PLAN.md section 5 out of the same
parquet files PostgreSQL loads, so both databases hold the same data:

  building_profiles    one per block: town and supply embedded, location as a
                       GeoJSON Point (omitted when not geocoded), stats and the
                       latest 10 sales computed by $merge after the sales load
  resale_transactions  one per sale, referencing building_id, with town, block,
                       street, flat type and model denormalised for search
  hawker_centres       GeoJSON Features with NEA's property names
  counters             buyer and txn sequences seeded past the loaded ids

`_id` reuses the PostgreSQL keys. Money is Decimal128. Runs as the `etl` user.
Reloading replaces all snapshot documents; buyer accounts are kept, but their
saved blocks are cleared because building ids may change.

Usage (after `python -m etl.clean`):
    python -m etl.load_mongo [--interim data/interim]
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
from bson.decimal128 import Decimal128
from pymongo import InsertOne, MongoClient
from pymongo.database import Database

from app.config import get_settings
from db.mongo.indexes import create_indexes, drop_secondary_indexes
from db.mongo.stats import EMPTY_STATS, rebuild_all_stats

BATCH = 10_000
SNAPSHOT_COLLECTIONS = ["building_profiles", "resale_transactions", "hawker_centres"]
LAND_USE = ["residential", "commercial", "market_hawker", "miscellaneous", "multistorey_carpark", "precinct_pavilion"]


def log(msg: str) -> None:
    print(f"[load_mongo] {msg}", flush=True)


def rows(interim: Path, name: str) -> list[dict]:
    return pq.read_table(interim / f"{name}.parquet").to_pylist()


def dec(v: Decimal | None) -> Decimal128 | None:
    return None if v is None else Decimal128(v)


def month(d: date) -> datetime:
    """txn_month as midnight UTC, the convention both databases share."""
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def point(lat: Decimal | None, lon: Decimal | None) -> dict | None:
    if lat is None or lon is None:
        return None
    return {"type": "Point", "coordinates": [float(lon), float(lat)]}


def build_documents(interim: Path) -> dict[str, list[dict]]:
    towns = {t["town_id"]: t for t in rows(interim, "town")}
    flat_types = {f["flat_type_id"]: f["name"] for f in rows(interim, "flat_type")}
    flat_models = {f["flat_model_id"]: f["name"] for f in rows(interim, "flat_model")}

    supply: dict[int, list[dict]] = defaultdict(list)
    for s in rows(interim, "building_flat_supply"):
        supply[s["building_id"]].append(
            {"flat_type": flat_types[s["flat_type_id"]], "sold": s["sold_units"], "rental": s["rental_units"]}
        )

    buildings = rows(interim, "building")
    by_id = {b["building_id"]: b for b in buildings}
    profiles = []
    for b in buildings:
        t = towns[b["town_id"]]
        doc = {
            "_id": b["building_id"],
            "block": b["block"],
            "street": b["street"],
            "town": {"code": t["code"], "name": t["name"]},
            "year_completed": b["year_completed"],
            "lease_commence_year": b["lease_commence_year"],
            "max_floor": b["max_floor"],
            **{k: b[k] for k in LAND_USE},
            "in_property_dataset": b["in_property_dataset"],
            "supply": sorted(supply[b["building_id"]], key=lambda s: s["flat_type"]),
            "stats": EMPTY_STATS,
            "recent_sales": [],
        }
        loc = point(b["latitude"], b["longitude"])
        if loc:
            doc["location"] = loc
        profiles.append(doc)

    sales = []
    for r in rows(interim, "resale_transaction"):
        b = by_id[r["building_id"]]
        sales.append(
            {
                "_id": r["txn_id"],
                "building_id": r["building_id"],
                "town": towns[b["town_id"]]["name"],
                "block": b["block"],
                "street": b["street"],
                "flat_type": flat_types[r["flat_type_id"]],
                "flat_model": flat_models[r["flat_model_id"]],
                "txn_month": month(r["txn_month"]),
                "storey_min": r["storey_min"],
                "storey_max": r["storey_max"],
                "floor_area_sqm": dec(r["floor_area_sqm"]),
                "remaining_lease_months": r["remaining_lease_months"],
                "resale_price": dec(r["resale_price"]),
                "lease_commence_year": b["lease_commence_year"],
            }
        )

    hawkers = [
        {
            "_id": h["hawker_id"],
            "type": "Feature",
            "geometry": point(h["latitude"], h["longitude"]),
            "properties": {
                "OBJECTID": h["hawker_id"],
                "NAME": h["name"],
                "ADDRESS_MYENV": h["address"],
                "ADDRESSPOSTALCODE": h["postal_code"],
                "STATUS": h["status"],
                "NUMBER_OF_COOKED_FOOD_STALLS": h["stall_count"],
            },
        }
        for h in rows(interim, "hawker_centre")
    ]
    return {"building_profiles": profiles, "resale_transactions": sales, "hawker_centres": hawkers}


def insert_all(db: Database, name: str, docs: list[dict]) -> int:
    n = 0
    for i in range(0, len(docs), BATCH):
        result = db[name].bulk_write([InsertOne(d) for d in docs[i : i + BATCH]], ordered=False)
        n += result.inserted_count
    return n


def load(db: Database, interim: Path) -> dict[str, int]:
    missing = [t for t in ("town", "building", "resale_transaction") if not (interim / f"{t}.parquet").exists()]
    if missing:
        raise SystemExit(f"[load_mongo] missing {missing} in {interim}; run `python -m etl.clean` first")
    existing = set(db.list_collection_names())
    absent = [c for c in [*SNAPSHOT_COLLECTIONS, "buyers", "counters"] if c not in existing]
    if absent:
        raise SystemExit(f"[load_mongo] collections {absent} do not exist; run `python -m db.migrate` first")

    started = time.perf_counter()
    docs = build_documents(interim)
    log(f"built documents in {time.perf_counter() - started:.1f} s")

    drop_secondary_indexes(db, SNAPSHOT_COLLECTIONS)
    for name in SNAPSHOT_COLLECTIONS:
        db[name].delete_many({})
    db.buyers.update_many({}, {"$set": {"saved": []}})

    counts: dict[str, int] = {}
    for name in ["building_profiles", "resale_transactions", "hawker_centres"]:
        started = time.perf_counter()
        counts[name] = insert_all(db, name, docs[name])
        if counts[name] != len(docs[name]):
            raise RuntimeError(f"{name}: inserted {counts[name]} of {len(docs[name])}")
        log(f"{name:22} {counts[name]:>8,} docs  {time.perf_counter() - started:5.1f} s")

    started = time.perf_counter()
    names = create_indexes(db)
    log(f"indexes                {len(names):>8} built {time.perf_counter() - started:5.1f} s")

    started = time.perf_counter()
    with_sales = rebuild_all_stats(db)
    log(f"profile stats ($merge) {with_sales:>8,} blocks with sales  {time.perf_counter() - started:5.1f} s")

    max_txn = max((d["_id"] for d in docs["resale_transactions"]), default=0)
    max_buyer = next(iter(db.buyers.find({}, {"_id": 1}).sort("_id", -1).limit(1)), {"_id": 0})["_id"]
    for key, seq in (("txn", max_txn), ("buyer", max_buyer)):
        db.counters.replace_one({"_id": key}, {"_id": key, "seq": seq}, upsert=True)
    log(f"counters               txn={max_txn:,} buyer={max_buyer:,}")
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--interim", type=Path, default=Path("data/interim"))
    args = ap.parse_args(argv)

    s = get_settings()
    if not s.mongo_etl_password:
        raise SystemExit("[load_mongo] MONGO_ETL_PASSWORD is empty; check .env")
    started = time.perf_counter()
    client = MongoClient(s.mongo_uri("etl", direct=True), serverSelectionTimeoutMS=10000, tz_aware=True)
    try:
        counts = load(client[s.mongo_db], args.interim)
    finally:
        client.close()
    log(f"loaded {sum(counts.values()):,} documents in {time.perf_counter() - started:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
