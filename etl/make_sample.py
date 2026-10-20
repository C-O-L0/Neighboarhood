"""Build the committed sample dataset in data/sample from the pinned snapshot.

The sample has the same three files, columns and encoding as data/raw, so the
whole pipeline (clean, load, tests) runs on it unchanged:

- every sale in three towns (Bishan, Bukit Timah, Marine Parade), every month,
- the edge-case blocks the cleaner has rules for: 82 MACPHERSON LANE (no
  property row, loads as a stub) and 37 TEBAN GDNS RD (two lease years),
- every property row in those towns plus the Teban block, including blocks
  with no dwelling units,
- all hawker centres.

Rows keep their source order, and the CSVs are rewritten byte for byte from the
source lines, so the sample is deterministic and reviewable in a diff.

Usage:
    python -m etl.make_sample [--raw-dir data/raw] [--out data/sample]
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

from etl.clean import HAWKER_FILE, PROPERTY_FILE, RESALE_FILE, TOWNS, normalise_key

SAMPLE_TOWNS = ("BISHAN", "BUKIT TIMAH", "MARINE PARADE")
EDGE_BLOCKS = {("82", "MACPHERSON LANE"), ("37", "TEBAN GDNS RD")}


def _filter_lines(src: Path, dst: Path, keep) -> int:
    """Copy the header and every line whose parsed row passes `keep`, preserving the source bytes."""
    with open(src, encoding="utf-8-sig", newline="") as f:
        lines = f.read().splitlines(keepends=True)
    header = next(csv.reader([lines[0]]))
    kept = [lines[0]]
    for line in lines[1:]:
        row = dict(zip(header, next(csv.reader([line])), strict=True))
        if keep(row):
            kept.append(line)
    with open(dst, "w", encoding="utf-8", newline="") as f:
        f.writelines(kept)
    return len(kept) - 1


def build(raw: Path, out: Path) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    codes = {code for code, name in TOWNS if name in SAMPLE_TOWNS}

    def keep_sale(r: dict) -> bool:
        key = (normalise_key(r["block"]), normalise_key(r["street_name"]))
        return normalise_key(r["town"]) in SAMPLE_TOWNS or key in EDGE_BLOCKS

    def keep_block(r: dict) -> bool:
        key = (normalise_key(r["blk_no"]), normalise_key(r["street"]))
        return normalise_key(r["bldg_contract_town"]) in codes or key in EDGE_BLOCKS

    counts = {
        "resale": _filter_lines(raw / RESALE_FILE, out / RESALE_FILE, keep_sale),
        "property": _filter_lines(raw / PROPERTY_FILE, out / PROPERTY_FILE, keep_block),
    }
    shutil.copyfile(raw / HAWKER_FILE, out / HAWKER_FILE)
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    ap.add_argument("--out", type=Path, default=Path("data/sample"))
    args = ap.parse_args(argv)
    counts = build(args.raw_dir, args.out)
    print(f"sample: {counts['resale']:,} sales, {counts['property']:,} blocks, all hawker centres -> {args.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
