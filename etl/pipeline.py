"""Run the whole import in order: verify, clean, load PostgreSQL, load MongoDB.

Full snapshot (needs data/raw, see etl/download.py):
    docker compose run --rm etl
Committed sample (no download needed; what CI loads):
    docker compose run --rm etl python -m etl.pipeline --sample

Whenever either load runs, the 200 synthetic buyers and their saved blocks are written to
both databases afterwards (etl/synth_buyers.py), because each loader clears its database's
saves. DEMO_BUYER_PASSWORD and DEMO_ADMIN_PASSWORD must be set; they are checked first.

When both loads run, the pipeline ends by counting rows and documents in both
databases (etl/db_counts.py) and exits 1 if they disagree. The import report
(docs/import_report.md) is regenerated separately with `python -m etl.report`,
because it is a committed file; it shows those database counts when present.

--fraction 0.25 or 0.5 (benchmark scaling runs, task 4.1b) cleans the whole snapshot as usual, then keeps only the
sales whose ETL txn_id is divisible by 4 (0.25) or 2 (0.5). The filtered tables are written to a temporary folder,
which both loaders read and the database counts check uses; data/interim is never written and
docs/import_report.md is not touched. Buildings, supply, hawkers and buyers are unchanged. Reload the full snapshot
afterwards with a plain `docker compose run --rm etl`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from app.config import get_settings
from etl import clean, db_counts, download, load_mongo, load_pg, synth_buyers

ROOT = Path(__file__).resolve().parent.parent

# fraction of the sales kept -> the modulus on the ETL txn_id
FRACTIONS = {0.25: 4, 0.5: 2}


def filter_sales(interim: Path, fraction: float) -> int:
    """Keep the sales with txn_id % modulus == 0 in `interim` and make its clean_report.json agree.

    Only the temporary copy is touched: the sale table is rewritten, and rows_out.resale_transaction in the copy of
    clean_report.json becomes the filtered count (with the fraction recorded), so the database count check compares
    like with like. Returns the number of sales kept.
    """
    modulus = FRACTIONS[fraction]
    path = interim / "resale_transaction.parquet"
    table = pq.read_table(path)
    keep = [i for i, txn_id in enumerate(table["txn_id"].to_pylist()) if txn_id % modulus == 0]
    table = table.take(pa.array(keep, type=pa.int64()))
    pq.write_table(table, path)
    report_path = interim / "clean_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["rows_out"]["resale_transaction"] = table.num_rows
    report["fraction"] = fraction
    report_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return table.num_rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sample", action="store_true", help="load data/sample instead of the full snapshot")
    ap.add_argument("--skip-mongo", action="store_true")
    ap.add_argument("--skip-pg", action="store_true")
    ap.add_argument(
        "--fraction",
        type=float,
        choices=sorted(FRACTIONS),
        help="load only the sales with txn_id divisible by 4 (0.25) or 2 (0.5), from a temporary folder",
    )
    args = ap.parse_args(argv)
    started = time.perf_counter()

    s = get_settings()
    try:
        synth_buyers.check_demo_passwords(s.demo_buyer_password, s.demo_admin_password)
    except ValueError as exc:
        print(f"[pipeline] {exc}. Nothing was cleaned or loaded.", file=sys.stderr)
        return 1

    if args.sample:
        raw = ROOT / "data" / "sample"
    else:
        raw = ROOT / "data" / "raw"
        if not download.verify(download.load_manifest(), raw):
            print("Snapshot missing or changed. Run: python -m etl.download --from <folder>", file=sys.stderr)
            return 1
    if args.sample or args.fraction:
        # Container-local, so a read-only or root-owned data mount still works, and data/interim is never written.
        interim = Path(tempfile.mkdtemp(prefix="neighboar-fraction-" if args.fraction else "neighboar-interim-"))
    else:
        interim = ROOT / "data" / "interim"
    try:
        return load_all(args, raw, interim, started)
    finally:
        if args.fraction:
            shutil.rmtree(interim, ignore_errors=True)


def load_all(args: argparse.Namespace, raw: Path, interim: Path, started: float) -> int:
    print(f"== clean ({raw.relative_to(ROOT)})", flush=True)
    clean.main(["--raw-dir", str(raw), "--out", str(interim), "--cache-dir", str(ROOT / "data" / "cache")])
    if args.fraction:
        kept = filter_sales(interim, args.fraction)
        rule = f"txn_id % {FRACTIONS[args.fraction]} == 0"
        print(f"== fraction {args.fraction}: {kept:,} sales kept ({rule})", flush=True)
    if not args.skip_pg:
        print("== load PostgreSQL", flush=True)
        load_pg.main(["--interim", str(interim)])
    if not args.skip_mongo:
        print("== load MongoDB", flush=True)
        load_mongo.main(["--interim", str(interim)])
    if not (args.skip_pg and args.skip_mongo):
        print("== synthetic buyers", flush=True)
        status = synth_buyers.main([])
        if status:
            return status
    status = 0
    if not args.skip_pg and not args.skip_mongo:
        print("== database counts", flush=True)
        status = db_counts.main(["--interim", str(interim)])
    print(f"== done in {time.perf_counter() - started:.0f} s", flush=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
