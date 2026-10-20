"""Run the whole import in order: verify, clean, load PostgreSQL, load MongoDB.

Full snapshot (needs data/raw, see etl/download.py):
    docker compose run --rm etl
Committed sample (no download needed; what CI loads):
    docker compose run --rm etl python -m etl.pipeline --sample

The import report (docs/import_report.md) is regenerated separately with
`python -m etl.report`, because it is a committed file.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

from etl import clean, download, load_mongo, load_pg

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sample", action="store_true", help="load data/sample instead of the full snapshot")
    ap.add_argument("--skip-mongo", action="store_true")
    ap.add_argument("--skip-pg", action="store_true")
    args = ap.parse_args(argv)
    started = time.perf_counter()

    if args.sample:
        raw = ROOT / "data" / "sample"
        # Container-local, so a read-only or root-owned data mount still works.
        interim = Path(tempfile.mkdtemp(prefix="neighboar-interim-"))
    else:
        raw = ROOT / "data" / "raw"
        interim = ROOT / "data" / "interim"
        if not download.verify(download.load_manifest(), raw):
            print("Snapshot missing or changed. Run: python -m etl.download --from <folder>", file=sys.stderr)
            return 1

    print(f"== clean ({raw.relative_to(ROOT)})", flush=True)
    clean.main(["--raw-dir", str(raw), "--out", str(interim), "--cache-dir", str(ROOT / "data" / "cache")])
    if not args.skip_pg:
        print("== load PostgreSQL", flush=True)
        load_pg.main(["--interim", str(interim)])
    if not args.skip_mongo:
        print("== load MongoDB", flush=True)
        load_mongo.main(["--interim", str(interim)])
    print(f"== done in {time.perf_counter() - started:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
