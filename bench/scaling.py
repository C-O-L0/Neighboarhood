"""Merge the summary.csv of each data-volume fraction into one scaling.csv (task 4.2).

    python -m bench.scaling bench/results/2026-11-full

Every direct sub-folder of the given folder that holds a summary.csv and an env.json is one fraction. The fraction is
read from that folder's env.json (the value `bench.run` computed from the loaded sale count), rounded to two decimals,
never taken from the folder name. The output has the summary columns plus `fraction` and `source` (the folder name),
sorted by fraction and keeping each summary's own row order. Exactly three different fractions are required.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from bench.stats import SUMMARY_COLUMNS, write_rows

SCALING_COLUMNS = ["fraction", "source", *SUMMARY_COLUMNS]
EXPECTED_FRACTIONS = 3


class ScalingError(Exception):
    """The results folder is not a set of three fractions."""


def folder_fraction(folder: Path) -> float:
    env = json.loads((folder / "env.json").read_text(encoding="utf-8"))
    value = env.get("fraction")
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ScalingError(f"{folder / 'env.json'} has no numeric 'fraction'")
    return round(float(value), 2)


def merge(root: Path) -> list[dict]:
    folders = sorted(p for p in root.iterdir() if (p / "summary.csv").is_file() and (p / "env.json").is_file())
    by_fraction: dict[float, Path] = {}
    for folder in folders:
        fraction = folder_fraction(folder)
        if fraction in by_fraction:
            raise ScalingError(f"{folder.name} and {by_fraction[fraction].name} both have fraction {fraction}")
        by_fraction[fraction] = folder
    if len(by_fraction) != EXPECTED_FRACTIONS:
        found = ", ".join(f"{f} ({p.name})" for f, p in sorted(by_fraction.items())) or "none"
        raise ScalingError(f"need {EXPECTED_FRACTIONS} fractions under {root}, found: {found}")
    rows = []
    for fraction in sorted(by_fraction):
        folder = by_fraction[fraction]
        with open(folder / "summary.csv", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                rows.append({"fraction": f"{fraction:g}", "source": folder.name, **row})
    return rows


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m bench.scaling <results folder, e.g. bench/results/2026-11-full>", file=sys.stderr)
        return 2
    root = Path(args[0])
    try:
        rows = merge(root)
    except (ScalingError, OSError, ValueError) as exc:
        print(f"scaling: {exc}", file=sys.stderr)
        return 1
    target = root / "scaling.csv"
    target.unlink(missing_ok=True)  # write_rows appends; scaling.csv is always rebuilt whole
    count = write_rows(target, SCALING_COLUMNS, rows)
    print(f"wrote {target} ({count} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
