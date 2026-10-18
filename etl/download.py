"""Fetch and verify the pinned data snapshot (see etl/manifest.json).

The three source files are not committed. They come from one of:

  --from DIR        copy them from a local folder (e.g. the team's shared drive
                    or an unzipped submission), then verify
  --release [TAG]   download the GitHub Release asset set with the gh CLI
                    (repo must be accessible to your gh login), then verify
  (no option)       verify files already in data/raw

  --check-live      compare row counts on data.gov.sg today with the snapshot
                    (reports drift only; never replaces the snapshot)

Every path ends with a SHA-256 check against the manifest, so a load can only
ever run on the exact files the report describes.

Usage: python -m etl.download [--from DIR | --release [TAG]] [--check-live]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = Path(__file__).resolve().parent / "manifest.json"
RAW = ROOT / "data" / "raw"
DATASTORE = "https://data.gov.sg/api/action/datastore_search"


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(manifest: dict, raw: Path = RAW) -> bool:
    ok = True
    for entry in manifest["files"]:
        path = raw / entry["name"]
        if not path.exists():
            print(f"MISSING  {entry['name']}")
            ok = False
            continue
        digest = sha256(path)
        if digest != entry["sha256"]:
            print(f"MISMATCH {entry['name']}\n  expected {entry['sha256']}\n  got      {digest}")
            ok = False
        else:
            print(f"OK       {entry['name']} ({entry['rows']:,} rows)")
    return ok


def copy_from(manifest: dict, src: Path, raw: Path = RAW) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    for entry in manifest["files"]:
        source = src / entry["name"]
        if not source.exists():
            raise SystemExit(f"{source} not found")
        shutil.copy2(source, raw / entry["name"])
        print(f"copied   {entry['name']}")


def download_release(manifest: dict, tag: str, raw: Path = RAW) -> None:
    if shutil.which("gh") is None:
        raise SystemExit("gh CLI not found; install it or use --from DIR")
    raw.mkdir(parents=True, exist_ok=True)
    cmd = ["gh", "release", "download", tag, "--dir", str(raw), "--clobber"]
    for entry in manifest["files"]:
        cmd += ["--pattern", entry["name"]]
    subprocess.run(cmd, check=True, cwd=ROOT)


def check_live(manifest: dict) -> None:
    """Row counts from the data.gov.sg datastore API (CSV datasets only)."""
    for entry in manifest["files"]:
        if not entry["name"].endswith(".csv"):
            print(f"skip     {entry['name']} (GeoJSON is not in the datastore API)")
            continue
        resp = requests.get(DATASTORE, params={"resource_id": entry["dataset_id"], "limit": 1}, timeout=30)
        resp.raise_for_status()
        total = resp.json()["result"]["total"]
        drift = total - entry["rows"]
        print(f"live     {entry['name']}: {total:,} rows today vs {entry['rows']:,} in snapshot ({drift:+,})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch and verify the pinned data snapshot.")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--from", dest="src", type=Path, help="copy files from this folder")
    src.add_argument("--release", nargs="?", const="", help="download from a GitHub Release (default: manifest tag)")
    ap.add_argument("--check-live", action="store_true", help="report row-count drift on data.gov.sg")
    args = ap.parse_args(argv)

    manifest = load_manifest()
    if args.src:
        copy_from(manifest, args.src)
    elif args.release is not None:
        download_release(manifest, args.release or manifest["snapshot"])

    if args.check_live:
        check_live(manifest)

    if not verify(manifest):
        print("\nSnapshot incomplete or changed. Fetch it with --from DIR or --release.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
