"""Geocode HDB blocks once with OneMap and cache the result in the repo.

Targets: every residential block in the property dataset plus every block that
appears in the resale dataset (the union, so the stub block with no property
row is included). Output: data/cache/block_coordinates.csv.

The script is resumable: blocks already in the cache are skipped, so it can be
stopped and restarted. It must never run as part of app start-up or the demo.

Usage:
    python -m etl.geocode [--raw-dir data/raw] [--out data/cache/block_coordinates.csv]
                          [--rate 4] [--limit N]

Manual corrections go in data/cache/block_coordinates_overrides.csv with the
same columns; they replace cached rows when the ETL loads coordinates.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from etl.streets import expand_street, normalise_key, same_street

ONEMAP_URL = "https://www.onemap.gov.sg/api/common/elastic/search"
RESALE_FILE = "ResaleflatpricesbasedonregistrationdatefromJan2017onwards.csv"
PROPERTY_FILE = "HDBPropertyInformation.csv"

FIELDS = [
    "block",
    "street",
    "latitude",
    "longitude",
    "postal_code",
    "onemap_address",
    "match_status",
    "query",
]

# Singapore bounding box, used as a sanity check on every returned point.
LAT_RANGE = (1.15, 1.48)
LON_RANGE = (103.59, 104.10)


@dataclass(frozen=True)
class Block:
    block: str
    street: str


def target_blocks(raw_dir: Path) -> list[Block]:
    """Residential blocks plus every resold block, de-duplicated and sorted."""
    targets: set[Block] = set()
    with open(raw_dir / PROPERTY_FILE, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["residential"].strip().upper() == "Y":
                targets.add(Block(normalise_key(row["blk_no"]), normalise_key(row["street"])))
    with open(raw_dir / RESALE_FILE, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            targets.add(Block(normalise_key(row["block"]), normalise_key(row["street_name"])))
    return sorted(targets, key=lambda b: (b.street, b.block))


def load_done(out: Path) -> set[Block]:
    if not out.exists():
        return set()
    with open(out, newline="", encoding="utf-8") as f:
        return {Block(r["block"], r["street"]) for r in csv.DictReader(f)}


def in_singapore(lat: float, lon: float) -> bool:
    return LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]


def search(session: requests.Session, query: str) -> list[dict]:
    """One OneMap search call with simple retry on throttling or network errors."""
    params = {"searchVal": query, "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": 1}
    for attempt in range(5):
        try:
            resp = session.get(ONEMAP_URL, params=params, timeout=20)
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
            return resp.json().get("results", [])
        except (requests.RequestException, ValueError):
            time.sleep(2**attempt)
    raise RuntimeError(f"OneMap search failed after retries: {query!r}")


def pick(block: Block, results: list[dict]) -> tuple[dict | None, str]:
    """Choose the best result. Exact block + street wins; prefer a real postal code."""
    exact = [
        r
        for r in results
        if normalise_key(r.get("BLK_NO", "")) == block.block
        and same_street(block.street, r.get("ROAD_NAME", ""))
    ]
    if exact:
        exact.sort(key=lambda r: r.get("POSTAL", "NIL") in ("NIL", ""))
        return exact[0], "exact"
    blk_only = [r for r in results if normalise_key(r.get("BLK_NO", "")) == block.block]
    if len(blk_only) == 1:
        return blk_only[0], "block_only"
    return None, "none"


def geocode_one(session: requests.Session, block: Block) -> dict:
    queries = [f"{block.block} {block.street}"]
    expanded = f"{block.block} {expand_street(block.street)}"
    if expanded != queries[0]:
        queries.append(expanded)

    best: tuple[dict | None, str, str] = (None, "none", queries[0])
    for q in queries:
        chosen, status = pick(block, search(session, q))
        if chosen is not None:
            lat, lon = float(chosen["LATITUDE"]), float(chosen["LONGITUDE"])
            if not in_singapore(lat, lon):
                continue
            best = (chosen, status, q)
            if status == "exact":
                break

    chosen, status, q = best
    row = {"block": block.block, "street": block.street, "match_status": status, "query": q}
    if chosen is None:
        row.update(latitude="", longitude="", postal_code="", onemap_address="")
    else:
        row.update(
            latitude=f"{float(chosen['LATITUDE']):.6f}",
            longitude=f"{float(chosen['LONGITUDE']):.6f}",
            postal_code="" if chosen.get("POSTAL") in (None, "NIL") else chosen["POSTAL"],
            onemap_address=chosen.get("ADDRESS", ""),
        )
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    ap.add_argument("--out", type=Path, default=Path("data/cache/block_coordinates.csv"))
    ap.add_argument("--rate", type=float, default=4.0, help="max requests per second")
    ap.add_argument("--limit", type=int, default=0, help="stop after N blocks (0 = all)")
    args = ap.parse_args(argv)

    targets = target_blocks(args.raw_dir)
    done = load_done(args.out)
    todo = [b for b in targets if b not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"targets={len(targets)} cached={len(done)} to_geocode={len(todo)}", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    new_file = not args.out.exists()
    interval = 1.0 / args.rate
    session = requests.Session()
    counts: dict[str, int] = {}

    with open(args.out, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        if new_file:
            writer.writeheader()
        for i, block in enumerate(todo, 1):
            started = time.monotonic()
            row = geocode_one(session, block)
            writer.writerow(row)
            counts[row["match_status"]] = counts.get(row["match_status"], 0) + 1
            if i % 200 == 0:
                f.flush()
                print(f"{i}/{len(todo)} {counts}", flush=True)
            time.sleep(max(0.0, interval - (time.monotonic() - started)))

    print(f"done: {counts}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
