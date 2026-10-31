"""Load the cleaned tables (data/interim/*.parquet) into PostgreSQL.

Runs as neighboar_owner in one transaction: empty the snapshot tables, COPY every
table in foreign-key order, reset the identity sequences past the loaded ids,
refresh the town-month materialised view and ANALYZE. If anything fails, the
database is left exactly as it was.

Reloading replaces all snapshot data. saved_building and household_saved rows reference
buildings, so they are emptied too; buyer accounts and households are kept (re-run
etl/synth_buyers.py for saves).

Usage (after `python -m etl.clean`):
    python -m etl.load_pg [--interim data/interim]
Inside Compose:
    docker compose run --rm etl python -m etl.load_pg
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import psycopg
import pyarrow.parquet as pq
from psycopg import sql

from app.config import get_settings

# Foreign-key order: parents before children.
TABLES = [
    "town",
    "flat_type",
    "flat_model",
    "building",
    "building_flat_supply",
    "resale_transaction",
    "hawker_centre",
]

# Constant columns the parquet files do not carry, appended to every COPY row.
# source has no default, so the ETL states that these sales are the snapshot.
EXTRA_COLUMNS: dict[str, dict[str, object]] = {"resale_transaction": {"source": "snapshot"}}

# Tables whose ids come from an identity sequence that must move past the loaded ids.
IDENTITY = {"building": "building_id", "resale_transaction": "txn_id", "buyer": "buyer_id"}


def log(msg: str) -> None:
    print(f"[load_pg] {msg}", flush=True)


def copy_table(cur: psycopg.Cursor, name: str, path: Path, extra: dict[str, object] | None = None) -> int:
    """COPY one parquet file into `name`; `extra` adds constant columns to every row."""
    table = pq.read_table(path)
    extra = extra or {}
    cols = [*table.column_names, *extra]
    stmt = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(name), sql.SQL(", ").join(sql.Identifier(c) for c in cols)
    )
    n = 0
    with cur.copy(stmt) as copy:
        for batch in table.to_batches(max_chunksize=20_000):
            columns = [batch.column(i).to_pylist() for i in range(batch.num_columns)]
            for row in zip(*columns, strict=True):
                copy.write_row((*row, *extra.values()))
                n += 1
    return n


def load(conn: psycopg.Connection, interim: Path) -> dict[str, int]:
    missing = [t for t in TABLES if not (interim / f"{t}.parquet").exists()]
    if missing:
        raise SystemExit(f"[load_pg] missing {missing} in {interim}; run `python -m etl.clean` first")

    counts: dict[str, int] = {}
    with conn.transaction(), conn.cursor() as cur:
        # Serialise with migrate and with another load.
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('neighboar_load_pg'))")
        cur.execute(
            sql.SQL("TRUNCATE {}, saved_building, household_saved").format(
                sql.SQL(", ").join(sql.Identifier(t) for t in TABLES)
            )
        )
        # resale_transaction.source is not in the parquet and has no default, so
        # EXTRA_COLUMNS sets it to 'snapshot' on every loaded sale.
        for name in TABLES:
            started = time.perf_counter()
            counts[name] = copy_table(cur, name, interim / f"{name}.parquet", EXTRA_COLUMNS.get(name))
            log(f"{name:22} {counts[name]:>8,} rows  {time.perf_counter() - started:5.1f} s")

        for table, col in IDENTITY.items():
            cur.execute(
                sql.SQL(
                    "SELECT setval(pg_get_serial_sequence({t}, {c}), coalesce(max({ci}), 1), max({ci}) IS NOT NULL) "
                    "FROM {ti}"
                ).format(t=sql.Literal(table), c=sql.Literal(col), ci=sql.Identifier(col), ti=sql.Identifier(table))
            )

        started = time.perf_counter()
        cur.execute("SELECT refresh_town_stats()")
        cur.execute("SELECT count(*) FROM mv_town_month_stats")
        log(f"mv_town_month_stats    {cur.fetchone()[0]:>8,} rows  {time.perf_counter() - started:5.1f} s")

        # Every loaded row is in the database, and the derived unit counts match the source.
        for name in TABLES:
            cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(name)))
            got = cur.fetchone()[0]
            if got != counts[name]:
                raise RuntimeError(f"{name}: wrote {counts[name]} rows but table has {got}")

    conn.execute("ANALYZE")
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--interim", type=Path, default=Path("data/interim"))
    args = ap.parse_args(argv)

    s = get_settings()
    if not s.pg_owner_password:
        raise SystemExit("[load_pg] PG_OWNER_PASSWORD is empty; check .env")
    started = time.perf_counter()
    with psycopg.connect(s.pg_dsn("neighboar_owner"), autocommit=True) as conn:
        counts = load(conn, args.interim)
    log(f"loaded {sum(counts.values()):,} rows in {time.perf_counter() - started:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
