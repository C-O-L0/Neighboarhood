"""Count rows and documents per entity in both databases after a load.

Puts the cleaning output (clean_report.json) next to the PostgreSQL and MongoDB
counts, so a reader can see both databases hold the same snapshot. Reads as the
application's own users (PostgreSQL app_rw, MongoDB app); no extra roles needed.
Writes <interim>/db_counts.json, which etl/report.py renders as "Database counts".

Counts are plain row and document counts (a multiset: the 318 identical sales
count as 318). Sales recorded through the app change "sale (all sources)" in
both databases but never "snapshot sale".

Usage (after both loads):
    python -m etl.db_counts [--interim data/interim]
Exits 1 and names the entity when a compared count differs.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from psycopg import sql

if TYPE_CHECKING:
    import psycopg
    import pymongo.database

# (entity, label, compared). "pg_cleaning": PostgreSQL against the cleaning output only
# (MongoDB embeds the value). "all": all three agree. "pg_mongo": no cleaning figure.
ENTITIES: list[tuple[str, str, str]] = [
    ("town", "town", "pg_cleaning"),
    ("flat_type", "flat_type", "pg_cleaning"),
    ("flat_model", "flat_model", "pg_cleaning"),
    ("building", "building", "all"),
    ("stub_building", "stub building", "all"),
    ("geocoded_building", "geocoded building", "all"),
    ("building_flat_supply", "building_flat_supply", "all"),
    ("sale_all", "sale (all sources)", "pg_mongo"),
    ("snapshot_sale", "snapshot sale", "all"),
    ("hawker_centre", "hawker_centre", "all"),
    ("buyer", "buyer", "pg_mongo"),
    ("saved_building", "saved_building", "pg_mongo"),
]

# Fixed table names and WHERE clauses: nothing here is built from input.
PG_TABLES = {
    "town": "town",
    "flat_type": "flat_type",
    "flat_model": "flat_model",
    "building": "building",
    "stub_building": "building",
    "geocoded_building": "building",
    "building_flat_supply": "building_flat_supply",
    "sale_all": "resale_transaction",
    "snapshot_sale": "resale_transaction",
    "hawker_centre": "hawker_centre",
    "buyer": "buyer",
    "saved_building": "saved_building",
}
PG_WHERE = {
    "stub_building": sql.SQL(" WHERE NOT in_property_dataset"),
    "geocoded_building": sql.SQL(" WHERE latitude IS NOT NULL"),
    "snapshot_sale": sql.SQL(" WHERE source = 'snapshot'"),
}


def _array_total(coll, field: str) -> int:
    """Sum of an embedded array's length over a collection; a missing array counts as 0."""
    pipeline = [{"$group": {"_id": None, "n": {"$sum": {"$size": {"$ifNull": [f"${field}", []]}}}}}]
    out = list(coll.aggregate(pipeline))
    return int(out[0]["n"]) if out else 0


def collect(pg: psycopg.Connection, mdb: pymongo.database.Database) -> dict[str, dict]:
    """Return {entity: {"pg": int, "mongo": int}} for every entity in ENTITIES."""
    counts: dict[str, dict] = {}
    with pg.cursor() as cur:
        for entity, _, _ in ENTITIES:
            query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(PG_TABLES[entity]))
            if entity in PG_WHERE:
                query = query + PG_WHERE[entity]
            cur.execute(query)
            counts[entity] = {"pg": int(cur.fetchone()[0])}
    profiles, sales = mdb["building_profiles"], mdb["resale_transactions"]
    counts["town"]["mongo"] = len(profiles.distinct("town.code"))
    counts["flat_type"]["mongo"] = len(sales.distinct("flat_type"))
    counts["flat_model"]["mongo"] = len(sales.distinct("flat_model"))
    counts["building"]["mongo"] = profiles.count_documents({})
    counts["stub_building"]["mongo"] = profiles.count_documents({"in_property_dataset": False})
    counts["geocoded_building"]["mongo"] = profiles.count_documents({"location": {"$exists": True}})
    counts["building_flat_supply"]["mongo"] = _array_total(profiles, "supply")
    counts["sale_all"]["mongo"] = sales.count_documents({})
    counts["snapshot_sale"]["mongo"] = sales.count_documents({"source": "snapshot"})
    counts["hawker_centre"]["mongo"] = mdb["hawker_centres"].count_documents({})
    counts["buyer"]["mongo"] = mdb["buyers"].count_documents({})
    counts["saved_building"]["mongo"] = _array_total(mdb["buyers"], "saved")
    return counts


def cleaning_figure(entity: str, clean_report: dict) -> int | None:
    """The cleaning output for an entity, or None where cleaning has no matching figure."""
    rows_out = clean_report["rows_out"]
    if entity == "stub_building":
        return len(clean_report["stub_buildings"])
    if entity == "geocoded_building":
        return int(clean_report["coordinates"]["geocoded_buildings"])
    if entity == "snapshot_sale":
        return int(rows_out["resale_transaction"])
    if entity in ("sale_all", "buyer", "saved_building"):
        return None
    return int(rows_out[entity])


def compare(counts: dict[str, dict], clean_report: dict) -> list[dict]:
    """One row per entity: cleaning, pg, mongo and whether the compared pairs agree."""
    rows = []
    for entity, label, mode in ENTITIES:
        pg, mongo = counts[entity]["pg"], counts[entity]["mongo"]
        cleaning = cleaning_figure(entity, clean_report)
        if mode == "pg_cleaning":
            match = pg == cleaning
        elif mode == "all":
            match = pg == mongo == cleaning
        else:
            match = pg == mongo
        rows.append(
            {
                "entity": entity,
                "label": label,
                "mode": mode,
                "cleaning": cleaning,
                "pg": pg,
                "mongo": mongo,
                "match": match,
            }
        )
    return rows


@contextmanager
def _open() -> Iterator[tuple[psycopg.Connection, pymongo.database.Database]]:
    import psycopg
    from pymongo import MongoClient

    from app.config import get_settings

    s = get_settings()
    client = MongoClient(s.mongo_uri("app", direct=True), serverSelectionTimeoutMS=10000, tz_aware=True)
    try:
        with psycopg.connect(s.pg_dsn("app_rw")) as pg:
            yield pg, client[s.mongo_db]
    finally:
        client.close()


def cell(mode: str, side: str, value: int | None) -> str:
    """Text for one count; shared with etl/report.py."""
    if value is None:
        return "n/a"
    if side == "mongo" and mode == "pg_cleaning":
        return f"embedded ({value} distinct)"
    return f"{value:,}"


def format_table(rows: list[dict]) -> str:
    head = ("entity", "cleaning", "postgresql", "mongodb", "match")
    body = [
        (
            r["label"],
            cell(r["mode"], "cleaning", r["cleaning"]),
            cell(r["mode"], "pg", r["pg"]),
            cell(r["mode"], "mongo", r["mongo"]),
            "yes" if r["match"] else "NO",
        )
        for r in rows
    ]
    widths = [max(len(x[i]) for x in [head, *body]) for i in range(5)]
    return "\n".join("  ".join(v.ljust(w) for v, w in zip(x, widths, strict=True)).rstrip() for x in [head, *body])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--interim", type=Path, default=Path("data/interim"))
    args = ap.parse_args(argv)

    clean_report = json.loads((args.interim / "clean_report.json").read_text(encoding="utf-8"))
    with _open() as (pg, mdb):
        counts = collect(pg, mdb)
    rows = compare(counts, clean_report)
    out = {
        "taken_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "raw_dir": clean_report["raw_dir"],
        "rows_out": clean_report["rows_out"],
        "entities": {e: {"pg": c["pg"], "mongo": c["mongo"]} for e, c in counts.items()},
    }
    (args.interim / "db_counts.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(format_table(rows), flush=True)
    bad = [r["label"] for r in rows if not r["match"]]
    if bad:
        print(f"Database counts differ for: {', '.join(bad)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
