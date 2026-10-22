"""Clean the pinned snapshot into load-ready tables (one parquet file per table).

Input:  data/raw (or data/sample) with the three source files from etl/manifest.json.
Output: data/interim/<table>.parquet for town, flat_type, flat_model, building,
        building_flat_supply, resale_transaction and hawker_centre, plus
        data/interim/clean_report.json with a count for every rule applied.

Both loaders (PostgreSQL and MongoDB) read these files, so type parsing and
key normalisation happen once, here. Ids are deterministic: buildings are
numbered in (street, block) order, sales in source-file order, hawker centres
keep the source OBJECTID, and the lookup tables use the fixed lists below.

Coordinates are joined from data/cache/block_coordinates.csv (plus the optional
overrides file) when the geocode cache exists; otherwise they stay null.

Usage:
    python -m etl.clean [--raw-dir data/raw] [--out data/interim] [--cache-dir data/cache]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

RESALE_FILE = "ResaleflatpricesbasedonregistrationdatefromJan2017onwards.csv"
PROPERTY_FILE = "HDBPropertyInformation.csv"
HAWKER_FILE = "HawkerCentresGEOJSON.geojson"
CACHE_FILE = "block_coordinates.csv"
OVERRIDES_FILE = "block_coordinates_overrides.csv"

# Property dataset town codes -> resale town names. 27 towns; TG (Tengah) has no
# resale sales yet, so its name is set by hand. Verified against the data on load.
TOWNS: list[tuple[str, str]] = [
    ("AMK", "ANG MO KIO"),
    ("BD", "BEDOK"),
    ("BH", "BISHAN"),
    ("BB", "BUKIT BATOK"),
    ("BM", "BUKIT MERAH"),
    ("BP", "BUKIT PANJANG"),
    ("BT", "BUKIT TIMAH"),
    ("CT", "CENTRAL AREA"),
    ("CCK", "CHOA CHU KANG"),
    ("CL", "CLEMENTI"),
    ("GL", "GEYLANG"),
    ("HG", "HOUGANG"),
    ("JE", "JURONG EAST"),
    ("JW", "JURONG WEST"),
    ("KWN", "KALLANG/WHAMPOA"),
    ("MP", "MARINE PARADE"),
    ("PRC", "PASIR RIS"),
    ("PG", "PUNGGOL"),
    ("QT", "QUEENSTOWN"),
    ("SB", "SEMBAWANG"),
    ("SK", "SENGKANG"),
    ("SGN", "SERANGOON"),
    ("TAP", "TAMPINES"),
    ("TG", "TENGAH"),
    ("TP", "TOA PAYOH"),
    ("WL", "WOODLANDS"),
    ("YS", "YISHUN"),
]

# 9 flat types: the 7 resale types plus two that only appear in the property supply counts.
FLAT_TYPES: list[tuple[str, bool]] = [
    ("1 ROOM", True),
    ("2 ROOM", True),
    ("3 ROOM", True),
    ("4 ROOM", True),
    ("5 ROOM", True),
    ("EXECUTIVE", True),
    ("MULTI-GENERATION", True),
    ("STUDIO APARTMENT", False),
    ("OTHER", False),
]

# The 21 flat models in the snapshot, as spelled in the source. A new model in a
# later download is rejected and reported rather than silently added.
FLAT_MODELS: list[str] = [
    "2-room",
    "3Gen",
    "Adjoined flat",
    "Apartment",
    "DBSS",
    "Improved",
    "Improved-Maisonette",
    "Maisonette",
    "Model A",
    "Model A-Maisonette",
    "Model A2",
    "Multi Generation",
    "New Generation",
    "Premium Apartment",
    "Premium Apartment Loft",
    "Premium Maisonette",
    "Simplified",
    "Standard",
    "Terrace",
    "Type S1",
    "Type S2",
]

# Property supply columns -> (flat type, "sold" or "rental").
SUPPLY_COLUMNS: dict[str, tuple[str, str]] = {
    "1room_sold": ("1 ROOM", "sold"),
    "2room_sold": ("2 ROOM", "sold"),
    "3room_sold": ("3 ROOM", "sold"),
    "4room_sold": ("4 ROOM", "sold"),
    "5room_sold": ("5 ROOM", "sold"),
    "exec_sold": ("EXECUTIVE", "sold"),
    "multigen_sold": ("MULTI-GENERATION", "sold"),
    "studio_apartment_sold": ("STUDIO APARTMENT", "sold"),
    "1room_rental": ("1 ROOM", "rental"),
    "2room_rental": ("2 ROOM", "rental"),
    "3room_rental": ("3 ROOM", "rental"),
    "other_room_rental": ("OTHER", "rental"),
}

LAND_USE = ["residential", "commercial", "market_hawker", "miscellaneous", "multistorey_carpark", "precinct_pavilion"]

# Mirrors of the PostgreSQL CHECK constraints, so bad rows are reported here, not at COPY time.
STOREY_MAX = 60
AREA_MAX = Decimal("400")
LEASE_MONTHS_MAX = 1188

# HDB leases run 99 years. A sale's remaining lease should match a lease starting in
# its block's commencement year. The start month is unknown (up to 11 months) and
# HDB's own figures drift by a few more months, the largest honest gap in the
# snapshot being 18 months, so anything within two years counts as consistent.
LEASE_YEARS = 99
LEASE_TOLERANCE_MONTHS = 24
LAT_RANGE = (Decimal("1.15"), Decimal("1.48"))
LON_RANGE = (Decimal("103.59"), Decimal("104.10"))

_LEASE = re.compile(r"^(\d+) years?(?: (\d+) months?)?$")
_STOREY = re.compile(r"^(\d+) TO (\d+)$")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_SPACES = re.compile(r"\s+")


# ---------------------------------------------------------------------- parsers


def normalise_key(text: str) -> str:
    """Upper-case, trim and collapse whitespace. Same rule as etl/streets.py, used for every join key."""
    return _SPACES.sub(" ", str(text).strip().upper())


def parse_remaining_lease(text: str) -> tuple[int, str]:
    """'61 years 04 months', '61 years 1 month' or '61 years' -> (months, format label)."""
    m = _LEASE.match(normalise_key(text).lower())
    if not m:
        raise ValueError(f"remaining_lease {text!r}")
    years, months = int(m.group(1)), int(m.group(2) or 0)
    if months > 11:
        raise ValueError(f"remaining_lease {text!r}")
    label = "years only" if m.group(2) is None else ("years and 1 month" if months == 1 else "years and months")
    return years * 12 + months, label


def parse_storey(text: str) -> tuple[int, int]:
    m = _STOREY.match(normalise_key(text))
    if not m:
        raise ValueError(f"storey_range {text!r}")
    lo, hi = int(m.group(1)), int(m.group(2))
    if not 1 <= lo <= hi <= STOREY_MAX:
        raise ValueError(f"storey_range {text!r} outside 1..{STOREY_MAX}")
    return lo, hi


def parse_month(text: str) -> pd.Timestamp:
    m = _MONTH.match(text.strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValueError(f"month {text!r}")
    return pd.Timestamp(int(m.group(1)), int(m.group(2)), 1)


def parse_decimal(text: str, places: int, what: str) -> Decimal:
    """Exact decimal with `places` digits after the point. Refuses values that would lose digits."""
    try:
        value = Decimal(text.strip())
    except InvalidOperation as exc:
        raise ValueError(f"{what} {text!r}") from exc
    quantum = Decimal(1).scaleb(-places)
    rounded = value.quantize(quantum)
    if rounded != value:
        raise ValueError(f"{what} {text!r} has more than {places} decimal places")
    return rounded


def parse_flag(text: str) -> bool:
    t = normalise_key(text)
    if t not in ("Y", "N"):
        raise ValueError(f"flag {text!r}")
    return t == "Y"


def parse_coord(value, rng: tuple[Decimal, Decimal]) -> Decimal | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() == "":
        return None
    d = Decimal(str(value).strip()).quantize(Decimal("0.000001"))
    if not rng[0] <= d <= rng[1]:
        raise ValueError(f"coordinate {value!r} outside Singapore")
    return d


# ---------------------------------------------------------------------- lease year


def lease_deviation(lease_year: int, sale_month: pd.Timestamp, remaining_months: int) -> int:
    """Months between a sale's reported remaining lease and a 99-year lease from January of lease_year."""
    implied = (lease_year + LEASE_YEARS - sale_month.year) * 12 - (sale_month.month - 1)
    return abs(implied - remaining_months)


def choose_lease_year(values: dict[int, int], sales: list[tuple[pd.Timestamp, int]]) -> tuple[int, str, dict]:
    """Pick a block's lease commencement year when its sales disagree.

    `values` counts each reported year; `sales` is (month, remaining lease months)
    for every sale on the block. Exactly one candidate must fit every sale's
    remaining lease within the tolerance; otherwise stop for
    manual review instead of silently collapsing potentially different leases.
    Returns (year, method, evidence per candidate).
    """
    evidence = {}
    for year in values:
        devs = [lease_deviation(year, month, months) for month, months in sales]
        evidence[year] = {
            "consistent_sales": sum(d <= LEASE_TOLERANCE_MONTHS for d in devs),
            "max_deviation_months": max(devs, default=None),
        }
    candidates = [year for year, e in evidence.items() if e["consistent_sales"] == len(sales)]
    if not sales or len(candidates) != 1:
        raise ValueError(f"lease years require manual review: candidates={values}, evidence={evidence}")
    return candidates[0], "remaining_lease", evidence


# ---------------------------------------------------------------------- tables


def lookups() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    town = pd.DataFrame(
        [(i, code, name) for i, (code, name) in enumerate(TOWNS, 1)], columns=["town_id", "code", "name"]
    )
    flat_type = pd.DataFrame(
        [(i, name, resale) for i, (name, resale) in enumerate(FLAT_TYPES, 1)],
        columns=["flat_type_id", "name", "is_resale_type"],
    )
    flat_model = pd.DataFrame([(i, name) for i, name in enumerate(FLAT_MODELS, 1)], columns=["flat_model_id", "name"])
    return town, flat_type, flat_model


def read_raw(raw_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    resale = pd.read_csv(raw_dir / RESALE_FILE, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    prop = pd.read_csv(raw_dir / PROPERTY_FILE, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    hawkers = json.loads((raw_dir / HAWKER_FILE).read_text(encoding="utf-8-sig"))
    return resale, prop, hawkers


def read_coordinates(cache_dir: Path) -> tuple[dict[tuple[str, str], tuple], dict]:
    """(block, street) -> (lat, lon) from the geocode cache, overrides replacing cached rows."""
    info = {"cache_present": False, "overrides": 0}
    coords: dict[tuple[str, str], tuple] = {}
    for name in (CACHE_FILE, OVERRIDES_FILE):
        path = cache_dir / name
        if not path.exists():
            continue
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        if name == CACHE_FILE:
            info["cache_present"] = True
            info["cache_rows"] = len(df)
            info["cache_status"] = df["match_status"].value_counts().to_dict()
        else:
            info["overrides"] = len(df)
        for r in df.itertuples(index=False):
            key = (normalise_key(r.block), normalise_key(r.street))
            if r.latitude and r.longitude:
                coords[key] = (r.latitude, r.longitude)
            elif name == OVERRIDES_FILE:
                coords.pop(key, None)  # an override with blank coordinates removes a bad match
    return coords, info


def clean(raw_dir: Path, cache_dir: Path) -> tuple[dict[str, pd.DataFrame], dict, pd.DataFrame]:
    report: dict = {"raw_dir": str(raw_dir)}
    town, flat_type, flat_model = lookups()
    town_by_code = dict(zip(town.code, town.town_id, strict=True))
    town_by_name = dict(zip(town.name, town.town_id, strict=True))
    ft_by_name = dict(zip(flat_type.name, flat_type.flat_type_id, strict=True))
    fm_by_name = dict(zip(flat_model.name, flat_model.flat_model_id, strict=True))

    resale, prop, hawker_geo = read_raw(raw_dir)
    report["rows_in"] = {"resale": len(resale), "property": len(prop), "hawker": len(hawker_geo["features"])}

    # ----------------------------------------------------------- property blocks
    prop["block"] = prop["blk_no"].map(normalise_key)
    prop["street_key"] = prop["street"].map(normalise_key)
    dup = prop.duplicated(["block", "street_key"]).sum()
    if dup:
        raise ValueError(f"{dup} duplicate (block, street) keys in the property dataset")
    unknown_codes = sorted(set(prop["bldg_contract_town"].map(normalise_key)) - set(town_by_code))
    if unknown_codes:
        raise ValueError(f"unknown town codes in property dataset: {unknown_codes}")

    # ------------------------------------------------------------- resale rows
    resale["block"] = resale["block"].map(normalise_key)
    resale["street_key"] = resale["street_name"].map(normalise_key)
    resale["town_key"] = resale["town"].map(normalise_key)
    report["duplicates_kept"] = int(resale.duplicated(subset=list(resale.columns[:11])).sum())

    # Town names in the resale file must all be known, and each property town code
    # must map to the resale town of its blocks.
    unknown_towns = sorted(set(resale["town_key"]) - set(town_by_name))
    if unknown_towns:
        raise ValueError(f"unknown resale towns: {unknown_towns}")
    joined = resale[["block", "street_key", "town_key"]].merge(
        prop[["block", "street_key", "bldg_contract_town"]], on=["block", "street_key"], how="inner"
    )
    joined["code_town"] = joined["bldg_contract_town"].map(lambda c: TOWNS[town_by_code[normalise_key(c)] - 1][1])
    mismatch = joined[joined["code_town"] != joined["town_key"]]
    report["town_code_mismatches"] = int(len(mismatch))
    if len(mismatch):
        raise ValueError(f"{len(mismatch)} resale rows disagree with their block's town code")

    # ------------------------------------------------------------- buildings
    resold_keys = resale.drop_duplicates(["block", "street_key"])[["block", "street_key", "street_name", "town_key"]]
    prop_keys = set(zip(prop["block"], prop["street_key"], strict=True))
    stubs = resold_keys[
        [(b, s) not in prop_keys for b, s in zip(resold_keys.block, resold_keys.street_key, strict=True)]
    ]

    rows = []
    for r in prop.itertuples(index=False):
        rows.append(
            {
                "block": r.block,
                "street": r.street_key,
                "town_id": town_by_code[normalise_key(r.bldg_contract_town)],
                "year_completed": int(r.year_completed),
                "max_floor": int(r.max_floor_lvl),
                **{c: parse_flag(getattr(r, c)) for c in LAND_USE},
                "in_property_dataset": True,
                "total_dwelling_units": int(r.total_dwelling_units),
            }
        )
    for r in stubs.itertuples(index=False):
        rows.append(
            {
                "block": r.block,
                "street": r.street_key,
                "town_id": town_by_name[r.town_key],
                "year_completed": None,
                "max_floor": None,
                **dict.fromkeys(LAND_USE),
                "in_property_dataset": False,
                "total_dwelling_units": None,
            }
        )
    building = pd.DataFrame(rows).sort_values(["street", "block"], ignore_index=True)
    building.insert(0, "building_id", range(1, len(building) + 1))
    bad_floor = building["max_floor"].dropna()
    if not bad_floor.between(1, STOREY_MAX).all():
        raise ValueError("max_floor outside 1..60")
    report["stub_buildings"] = [
        {
            "block": r.block,
            "street": r.street_key,
            "town": r.town_key,
            "sales": int(((resale.block == r.block) & (resale.street_key == r.street_key)).sum()),
        }
        for r in stubs.itertuples(index=False)
    ]
    bid = {(b, s): i for i, b, s in zip(building.building_id, building.block, building.street, strict=True)}

    # Lease commencement is a block attribute. When a block's sales disagree on it,
    # each sale's remaining lease is the evidence: the year it fits is kept (see
    # choose_lease_year). Disagreements are logged with that evidence.
    sale_lease: list[tuple[pd.Timestamp, int] | None] = []
    for month, remaining in zip(resale["month"], resale["remaining_lease"], strict=True):
        try:
            sale_lease.append((parse_month(month), parse_remaining_lease(remaining)[0]))
        except ValueError:
            sale_lease.append(None)  # rejected below, with its reason
    report["lease_remaining_tolerance_months"] = LEASE_TOLERANCE_MONTHS
    report["lease_remaining_inconsistent"] = sum(
        sl is not None and lease_deviation(int(year), *sl) > LEASE_TOLERANCE_MONTHS
        for year, sl in zip(resale["lease_commence_date"], sale_lease, strict=True)
    )

    lease_counts = resale.groupby(["block", "street_key"])["lease_commence_date"].value_counts()
    block_rows = resale.groupby(["block", "street_key"]).indices
    lease_choice: dict[tuple[str, str], int] = {}
    anomalies = []
    for key, counts in lease_counts.groupby(level=[0, 1]):
        values = {int(v): int(n) for (_, _, v), n in counts.items()}
        if len(values) == 1:
            lease_choice[key] = next(iter(values))
            continue
        sales = [sale_lease[i] for i in block_rows[key] if sale_lease[i] is not None]
        try:
            chosen, method, evidence = choose_lease_year(values, sales)
        except ValueError as exc:
            raise ValueError(f"{key[0]} {key[1]}: {exc}") from exc
        lease_choice[key] = chosen
        anomalies.append(
            {
                "block": key[0],
                "street": key[1],
                "values": values,
                "chosen": chosen,
                "method": method,
                "evidence": evidence,
            }
        )
    report["lease_year_anomalies"] = anomalies
    building["lease_commence_year"] = [
        lease_choice.get((b, s)) for b, s in zip(building.block, building.street, strict=True)
    ]

    # Coordinates from the geocode cache.
    coords, cinfo = read_coordinates(cache_dir)
    lat, lon = [], []
    for b, s in zip(building.block, building.street, strict=True):
        c = coords.get((b, s))
        lat.append(parse_coord(c[0], LAT_RANGE) if c else None)
        lon.append(parse_coord(c[1], LON_RANGE) if c else None)
    building["latitude"], building["longitude"] = lat, lon
    has = building["latitude"].notna()
    need = building["residential"].eq(True) | building["building_id"].isin(
        {bid[k] for k in zip(resale.block, resale.street_key, strict=True)}
    )
    cinfo["geocoded_buildings"] = int(has.sum())
    cinfo["target_buildings"] = int(need.sum())
    cinfo["target_buildings_geocoded"] = int((has & need).sum())
    report["coordinates"] = cinfo

    # ------------------------------------------------------------- supply
    supply_rows = []
    skipped_zero = 0
    for r in prop.to_dict("records"):  # dicts, because itertuples renames columns that start with a digit
        b = bid[(r["block"], r["street_key"])]
        per_type: dict[str, dict[str, int]] = {}
        for col, (ft, kind) in SUPPLY_COLUMNS.items():
            per_type.setdefault(ft, {"sold": 0, "rental": 0})[kind] += int(r[col])
        total = 0
        for ft, v in per_type.items():
            total += v["sold"] + v["rental"]
            if v["sold"] + v["rental"] == 0:
                skipped_zero += 1
                continue
            supply_rows.append((b, ft_by_name[ft], v["sold"], v["rental"]))
        if total != int(r["total_dwelling_units"]):
            raise ValueError(
                f"supply for {r['block']} {r['street_key']} sums to {total}, not {r['total_dwelling_units']}"
            )
    supply = pd.DataFrame(supply_rows, columns=["building_id", "flat_type_id", "sold_units", "rental_units"])
    report["supply_zero_pairs_skipped"] = skipped_zero
    report["supply_total_matches_dwelling_units"] = True

    # ------------------------------------------------------------- transactions
    txn_rows, rejected = [], []
    lease_formats: dict[str, int] = {}
    fractional_prices = 0
    for i, r in enumerate(resale.itertuples(index=False), 1):
        try:
            month = parse_month(r.month)
            lo, hi = parse_storey(r.storey_range)
            area = parse_decimal(r.floor_area_sqm, 1, "floor_area_sqm")
            if not Decimal(0) < area < AREA_MAX:
                raise ValueError(f"floor_area_sqm {area} outside (0, 400)")
            months, fmt = parse_remaining_lease(r.remaining_lease)
            if not 0 <= months <= LEASE_MONTHS_MAX:
                raise ValueError(f"remaining lease {months} months outside 0..{LEASE_MONTHS_MAX}")
            price = parse_decimal(r.resale_price, 2, "resale_price")
            if price <= 0:
                raise ValueError(f"resale_price {price}")
            ft = normalise_key(r.flat_type)
            if ft not in ft_by_name or not FLAT_TYPES[ft_by_name[ft] - 1][1]:
                raise ValueError(f"flat_type {r.flat_type!r}")
            if r.flat_model.strip() not in fm_by_name:
                raise ValueError(f"flat_model {r.flat_model!r}")
        except ValueError as exc:
            rejected.append({"source_row": i, "reason": str(exc)})
            continue
        lease_formats[fmt] = lease_formats.get(fmt, 0) + 1
        fractional_prices += price != price.to_integral_value()
        txn_rows.append(
            (
                i,
                bid[(r.block, r.street_key)],
                ft_by_name[ft],
                fm_by_name[r.flat_model.strip()],
                month,
                lo,
                hi,
                area,
                months,
                price,
            )
        )
    txn = pd.DataFrame(
        txn_rows,
        columns=[
            "txn_id",
            "building_id",
            "flat_type_id",
            "flat_model_id",
            "txn_month",
            "storey_min",
            "storey_max",
            "floor_area_sqm",
            "remaining_lease_months",
            "resale_price",
        ],
    )
    report["remaining_lease_formats"] = lease_formats
    report["fractional_prices"] = int(fractional_prices)
    report["rejected"] = len(rejected)

    # A sale may not predate its block's completion year (trigger (a) in PostgreSQL).
    yc = txn["building_id"].map(building.set_index("building_id")["year_completed"])
    early = (yc.notna() & (txn["txn_month"].dt.year < yc)).sum()
    report["sales_before_completion"] = int(early)
    if early:
        raise ValueError(f"{early} sales predate their block's completion year")

    # ------------------------------------------------------------- hawker centres
    hrows = []
    for f in hawker_geo["features"]:
        p = f["properties"]
        lon_v, lat_v = f["geometry"]["coordinates"][:2]
        stalls = p.get("NUMBER_OF_COOKED_FOOD_STALLS")
        postal = str(p.get("ADDRESSPOSTALCODE") or "").strip()
        hrows.append(
            {
                "hawker_id": int(p["OBJECTID"]),
                "name": str(p["NAME"]).strip(),
                "address": str(p.get("ADDRESS_MYENV") or "").strip() or None,
                "postal_code": postal if re.fullmatch(r"\d{6}", postal) else None,
                "status": str(p.get("STATUS") or "").strip() or None,
                "stall_count": None if stalls in (None, "") else int(float(stalls)),
                "latitude": parse_coord(lat_v, LAT_RANGE),
                "longitude": parse_coord(lon_v, LON_RANGE),
            }
        )
    hawker = pd.DataFrame(hrows).sort_values("hawker_id", ignore_index=True)
    if not hawker["hawker_id"].is_unique:
        raise ValueError("duplicate hawker OBJECTID")
    report["hawker_status"] = hawker["status"].value_counts().to_dict()

    building = building.drop(columns=["total_dwelling_units"])
    tables = {
        "town": town,
        "flat_type": flat_type,
        "flat_model": flat_model,
        "building": building,
        "building_flat_supply": supply,
        "resale_transaction": txn,
        "hawker_centre": hawker,
    }
    report["rows_out"] = {k: len(v) for k, v in tables.items()}
    return tables, report, pd.DataFrame(rejected, columns=["source_row", "reason"])


# ---------------------------------------------------------------------- output

SCHEMAS: dict[str, pa.Schema] = {
    "town": pa.schema([("town_id", pa.int16()), ("code", pa.string()), ("name", pa.string())]),
    "flat_type": pa.schema([("flat_type_id", pa.int16()), ("name", pa.string()), ("is_resale_type", pa.bool_())]),
    "flat_model": pa.schema([("flat_model_id", pa.int16()), ("name", pa.string())]),
    "building": pa.schema(
        [
            ("building_id", pa.int32()),
            ("block", pa.string()),
            ("street", pa.string()),
            ("town_id", pa.int16()),
            ("year_completed", pa.int16()),
            ("max_floor", pa.int16()),
            *[(c, pa.bool_()) for c in LAND_USE],
            ("in_property_dataset", pa.bool_()),
            ("lease_commence_year", pa.int16()),
            ("latitude", pa.decimal128(9, 6)),
            ("longitude", pa.decimal128(9, 6)),
        ]
    ),
    "building_flat_supply": pa.schema(
        [
            ("building_id", pa.int32()),
            ("flat_type_id", pa.int16()),
            ("sold_units", pa.int32()),
            ("rental_units", pa.int32()),
        ]
    ),
    "resale_transaction": pa.schema(
        [
            ("txn_id", pa.int64()),
            ("building_id", pa.int32()),
            ("flat_type_id", pa.int16()),
            ("flat_model_id", pa.int16()),
            ("txn_month", pa.date32()),
            ("storey_min", pa.int16()),
            ("storey_max", pa.int16()),
            ("floor_area_sqm", pa.decimal128(5, 1)),
            ("remaining_lease_months", pa.int16()),
            ("resale_price", pa.decimal128(9, 2)),
        ]
    ),
    "hawker_centre": pa.schema(
        [
            ("hawker_id", pa.int32()),
            ("name", pa.string()),
            ("address", pa.string()),
            ("postal_code", pa.string()),
            ("status", pa.string()),
            ("stall_count", pa.int16()),
            ("latitude", pa.decimal128(9, 6)),
            ("longitude", pa.decimal128(9, 6)),
        ]
    ),
}


def to_arrow(name: str, df: pd.DataFrame) -> pa.Table:
    schema = SCHEMAS[name]
    arrays = []
    for field in schema:
        col = df[field.name]
        if pa.types.is_date32(field.type):
            values = [None if pd.isna(v) else v.date() for v in col]
        else:
            values = [None if v is None or (not isinstance(v, (str, Decimal)) and pd.isna(v)) else v for v in col]
            if pa.types.is_integer(field.type):
                values = [None if v is None else int(v) for v in values]
            elif pa.types.is_boolean(field.type):
                values = [None if v is None else bool(v) for v in values]
        arrays.append(pa.array(values, type=field.type))
    return pa.Table.from_arrays(arrays, schema=schema)


def write(tables: dict[str, pd.DataFrame], report: dict, rejected: pd.DataFrame, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        pq.write_table(to_arrow(name, df), out / f"{name}.parquet")
    rejected.to_csv(out / "rejected_rows.csv", index=False)
    (out / "clean_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    ap.add_argument("--out", type=Path, default=Path("data/interim"))
    ap.add_argument("--cache-dir", type=Path, default=Path("data/cache"))
    args = ap.parse_args(argv)

    tables, report, rejected = clean(args.raw_dir, args.cache_dir)
    write(tables, report, rejected, args.out)

    for name, n in report["rows_out"].items():
        print(f"{name:22} {n:>8,}")
    c = report["coordinates"]
    print(
        f"rejected {report['rejected']}, duplicates kept {report['duplicates_kept']}, "
        f"fractional prices {report['fractional_prices']}, stubs {len(report['stub_buildings'])}, "
        f"lease anomalies {len(report['lease_year_anomalies'])}, "
        f"remaining lease inconsistent {report['lease_remaining_inconsistent']}"
    )
    if c["cache_present"]:
        print(f"coordinates: {c['target_buildings_geocoded']:,} of {c['target_buildings']:,} target blocks")
    else:
        print("coordinates: no geocode cache found, latitude/longitude left null")
    print(f"wrote {args.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
