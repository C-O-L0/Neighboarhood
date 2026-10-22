# Data dictionary

Every column the import produces, where it comes from and how it is cleaned. The cleaner (`etl/clean.py`) writes one parquet file per table to `data/interim/`; both loaders read those files, so this is the single definition for PostgreSQL and MongoDB. Row counts are for the pinned snapshot (`snapshot-2026-09`); `docs/import_report.md` has the current figures and every rule's count.

PostgreSQL types are the target types from `docs/BUILD_PLAN.md` section 4. The MongoDB document shapes built from these tables are in section 5 of the same file.

## Sources

| File | Publisher | Rows | Used for |
|---|---|---:|---|
| `ResaleflatpricesbasedonregistrationdatefromJan2017onwards.csv` | HDB via data.gov.sg | 241,357 | `resale_transaction`, building lease year, stub buildings |
| `HDBPropertyInformation.csv` | HDB via data.gov.sg | 13,357 | `building`, `building_flat_supply` |
| `HawkerCentresGEOJSON.geojson` | NEA via data.gov.sg | 129 | `hawker_centre` |
| `data/cache/block_coordinates.csv` (+ `_overrides.csv`) | OneMap search, run once by `etl/geocode.py` | ~10.8k | building latitude and longitude |

## Conventions

- **Join keys.** Block and street are upper-cased, trimmed and have repeated spaces collapsed (`normalise_key`, the same rule as `etl/streets.py`). Streets keep HDB's abbreviated form (`ANG MO KIO AVE 10`), which is what both source files use.
- **Ids are deterministic.** `building_id` follows (street, block) order; `txn_id` is the 1-based row number in the source file; `hawker_id` is the source `OBJECTID`; lookup ids follow the fixed lists in `etl/clean.py`. Re-running the cleaner on the same files gives the same ids, which the equivalence tests rely on.
- **Money and coordinates are exact decimals** (parquet `decimal128`), never floats. 67 source prices have cents (for example 888,888.88).
- **Months** are stored as the first day of the month.
- **Null** means "not known" (stub buildings, ungeocoded blocks), never zero.
- Every range check below mirrors a PostgreSQL CHECK constraint, so a bad row is reported by the cleaner instead of failing at `COPY`.

## town (27 rows)

| Column | Parquet | PostgreSQL | Source | Rule |
|---|---|---|---|---|
| `town_id` | int16 | `SMALLINT PK` | | position in the fixed list (alphabetical by name) |
| `code` | string | `VARCHAR(3) UNIQUE` | property `bldg_contract_town` | 27 codes; the cleaner fails on an unknown code |
| `name` | string | `VARCHAR(40) UNIQUE` | resale `town` | each code's name is checked against the resale town of every resold block (0 mismatches); `TG` = `TENGAH` is set by hand because it has no sales yet |

## flat_type (9 rows)

| Column | Parquet | PostgreSQL | Source | Rule |
|---|---|---|---|---|
| `flat_type_id` | int16 | `SMALLINT PK` | | position in the fixed list |
| `name` | string | `VARCHAR(20) UNIQUE` | resale `flat_type`, property supply column names | `1 ROOM` to `5 ROOM`, `EXECUTIVE`, `MULTI-GENERATION`, plus `STUDIO APARTMENT` and `OTHER` |
| `is_resale_type` | bool | `BOOLEAN` | | false for `STUDIO APARTMENT` and `OTHER`, which appear only in supply counts; a sale with either is rejected |

## flat_model (21 rows)

| Column | Parquet | PostgreSQL | Source | Rule |
|---|---|---|---|---|
| `flat_model_id` | int16 | `SMALLINT PK` | | position in the fixed list (alphabetical) |
| `name` | string | `VARCHAR(30) UNIQUE` | resale `flat_model` | source spelling kept (`Model A`, `3Gen`); a model not in the list is rejected and reported |

## building (13,358 rows)

One row per property-dataset block, plus a stub for each block that has sales but no property row (one: 82 MACPHERSON LANE, 3 sales). A stub (`in_property_dataset = false`) appears in search and building pages, can be saved and joins food access if geocoded; its unknown attributes (supply, units, completion year, max floor, land use) are shown as "Unknown", never 0 or No.

| Column | Parquet | PostgreSQL | Source | Rule |
|---|---|---|---|---|
| `building_id` | int32 | `INT IDENTITY PK` | | 1..n in (street, block) order |
| `block` | string | `VARCHAR(5)`, `UNIQUE(block, street)` | property `blk_no`, resale `block` | normalised key; longest is 4 characters |
| `street` | string | `VARCHAR(40)` | property `street`, resale `street_name` | normalised key; longest is 20 characters |
| `town_id` | int16 | `SMALLINT FK` | property `bldg_contract_town` | stubs take the town of their sales |
| `year_completed` | int16, null | `SMALLINT` | property `year_completed` | 1937 to 2026; null for stubs |
| `max_floor` | int16, null | `SMALLINT CHECK 1..60` | property `max_floor_lvl` | 1 to 50; null for stubs. Not used to bound storey bands (15,387 sales have a band above it) |
| `residential`, `commercial`, `market_hawker`, `miscellaneous`, `multistorey_carpark`, `precinct_pavilion` | bool, null | `BOOLEAN` | property `Y`/`N` columns | any other value fails the run; null for stubs |
| `in_property_dataset` | bool | `BOOLEAN NOT NULL` | | false only for stubs |
| `lease_commence_year` | int16, null | `SMALLINT` | resale `lease_commence_date` | block attribute (3NF), derived from the block's sales. When they disagree, the year consistent with the sales' own remaining lease wins (99-year lease from January, tolerance 24 months, because HDB's remaining-lease figures drift up to 18 months); the majority value is only a fallback. One block disagrees: 37 TEBAN GDNS RD (1966 x 11, 1981 x 1). The 1981 sale (Jan 2025, 40 years 01 month remaining) fits 1966 (about 40 years left), not 1981 (about 55), so 1966 is stored and the anomaly logged with its evidence. Null for blocks never resold |
| `latitude`, `longitude` | decimal(9,6), null | `NUMERIC(9,6)`, both-or-neither, Singapore bounding box | geocode cache, overrides win | null when the block is not in the cache or has no match. Bounding box: latitude 1.15 to 1.48, longitude 103.59 to 104.10 |

The property dataset's `total_dwelling_units` is not stored: it is derived from `building_flat_supply` (view `v_building_units`), and the cleaner asserts the two agree for every block. For a stub the view returns NULL units (unknown, not 0), and the MongoDB profile omits `supply` (the validator enforces this).

## building_flat_supply (20,570 rows)

Units per block and flat type, from the 12 property supply columns. A (block, flat type) pair with no units is skipped (99,643 pairs).

| Column | Parquet | PostgreSQL | Source | Rule |
|---|---|---|---|---|
| `building_id` | int32 | `INT FK`, `PK(building_id, flat_type_id)` | | |
| `flat_type_id` | int16 | `SMALLINT FK` | column name | `1room`..`5room`, `exec`, `multigen`, `studio_apartment`, `other_room` mapped to the flat types |
| `sold_units` | int32 | `INT CHECK >= 0` | `*_sold` columns | |
| `rental_units` | int32 | `INT CHECK >= 0` | `*_rental` columns | only 1-, 2-, 3-room and other have rental columns; the rest are 0 |

## resale_transaction (241,357 rows)

One row per sale, including 318 rows that are exact duplicates of another row (kept: the data has no unit number, so two identical sales in a month are possible).

| Column | Parquet | PostgreSQL | Source | Rule |
|---|---|---|---|---|
| `txn_id` | int64 | `BIGINT IDENTITY PK` | | source row number |
| `building_id` | int32 | `INT FK` | `block` + `street_name` | joined on the normalised key; every sale resolves (stub included) |
| `flat_type_id` | int16 | `SMALLINT FK` | `flat_type` | must be a resale type |
| `flat_model_id` | int16 | `SMALLINT FK` | `flat_model` | must be in the fixed list |
| `txn_month` | date32 | `DATE CHECK day = 1` | `month` (`YYYY-MM`) | first of the month; 2017-01 to 2026-09 |
| `storey_min`, `storey_max` | int16 | `SMALLINT CHECK 1 <= min <= max <= 60` | `storey_range` (`10 TO 12`) | 17 bands, highest `49 TO 51` |
| `floor_area_sqm` | decimal(5,1) | `NUMERIC(5,1) CHECK > 0 AND < 400` | `floor_area_sqm` | `44.00` becomes 44.0; a value that would lose a digit is rejected. Range 31.0 to 366.7 |
| `remaining_lease_months` | int16 | `SMALLINT CHECK 0..1188` | `remaining_lease` | three formats: `61 years 04 months` (202,025), `61 years 01 month` (19,528), `95 years` (19,804). Kept per sale because it is not derivable from the lease year and month. Also the evidence for the block's lease year; the cleaner reports sales more than 24 months from a 99-year lease starting in their own lease year (1 in the snapshot, the 37 TEBAN GDNS RD sale) and keeps them |
| `resale_price` | decimal(9,2) | `NUMERIC(9,2) CHECK > 0` | `resale_price` | exact; 67 have cents |
| `source` | not in parquet | `VARCHAR(8) NOT NULL CHECK (source IN ('snapshot','admin'))`, no default | | provenance: the loaders write `snapshot`, admin routes write `admin`; an insert must name it. MongoDB `resale_transactions.source` is required with the same two values. Admin rows are deleted before a benchmark; a deleted snapshot row comes back only with a reload |

Not stored per sale: `town` (from the building), `lease_commence_date` (moved to `building`).

## hawker_centre (129 rows)

| Column | Parquet | PostgreSQL | Source | Rule |
|---|---|---|---|---|
| `hawker_id` | int32 | `INT PK` | `OBJECTID` | unique, checked |
| `name` | string | text (longest 80 characters) | `NAME` | trimmed |
| `address` | string, null | text (longest 75 characters) | `ADDRESS_MYENV` | trimmed |
| `postal_code` | string, null | `CHAR(6)` | `ADDRESSPOSTALCODE` | kept only if exactly 6 digits (all 129 are) |
| `status` | string | `VARCHAR(30)` | `STATUS` | `Existing` 103, `Existing (new)` 16, `Under Construction` 6, `Existing (replacement)` 3, `Interim Centre` 1; food-access queries exclude `Under Construction` |
| `stall_count` | int16, null | `SMALLINT CHECK >= 0` | `NUMBER_OF_COOKED_FOOD_STALLS` | 0 to 226 |
| `latitude`, `longitude` | decimal(9,6) | `NUMERIC(9,6) NOT NULL` | GeoJSON point `[lon, lat]` | rounded to 6 dp, bounding-box checked. MongoDB keeps the original GeoJSON Feature shape |

## Files the cleaner writes

| File | Contents |
|---|---|
| `data/interim/<table>.parquet` | the seven tables above |
| `data/interim/clean_report.json` | rows in and out, and the count for every rule; input to `etl/report.py` |
| `data/interim/rejected_rows.csv` | source row number and reason for each rejected sale (empty for the snapshot) |

`data/interim/` is gitignored; regenerate with `python -m etl.clean`.
