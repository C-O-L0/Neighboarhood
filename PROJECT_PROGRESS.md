# Project progress

Decisions and next actions only (README rule). Newest first.

## 2026-10-27 CI runner

- CI runs on `ubuntu-22.04`. MongoDB 8.3.11 will not start on Linux 6.19 or newer (SERVER-121912), which the `ubuntu-24.04` runners now use, so CI on main had failed since PR #6. The image pin is unchanged. Docker Desktop's WSL kernel (6.18 today) is affected the same way once it moves to 6.19: if MongoDB then fails locally, revisit the image pin.

## Review decisions (conflicts raised in a teammate's review)

- Lease year stays on `building`, derived from the sales: when a block's sales disagree, the year consistent with their remaining lease wins (24-month tolerance), exactly one candidate year must fit every valid sale date and remaining lease, otherwise cleaning stops for manual review. 37 TEBAN GDNS RD keeps 1966; its one 1981 sale is a suspected source error inferred from remaining-lease consistency.
- Building + month + price is not a key for a sale (318 identical rows). Edits and deletes target a `txn_id`, CRUD tests map runtime ids through an id map kept by the test, and dataset comparisons are multisets.
- After an admin write, MongoDB updates the block's `stats` and `recent_sales` in the same transaction; PostgreSQL refreshes `mv_town_month_stats` right after commit (a failed refresh leaves town figures stale until the next one). Tests and benchmarks settle both DBs first.
- Median stays the lower median (always an observed price), labelled "median (lower of the two middle prices when n is even)" in the UI and report.
- 82 MACPHERSON LANE stays a stub building in every feature; its unknown attributes show as "Unknown", `v_building_units` returns NULL and the Mongo profile has no `supply`.
- Sales carry `source` (`snapshot` or `admin`, no default) in both DBs; both DBs are reloaded from the pinned snapshot and settled before a formal benchmark.

## 2026-10-20 Phase 1 decisions

- Phase 1 schemas, loaders and ERD were built on `data/clean-etl` together with the cleaning step. The PostgreSQL and MongoDB owners review them and must be able to walk through both designs (README rule).
- Migration files are `001_schema`, `002_views`, `003_triggers`, `004_indexes`, `005_grants`. Lookup rows (town, flat_type, flat_model) are loaded by the ETL from the fixed lists in `etl/clean.py`, not seeded by a migration, so both databases use one definition. This replaces `002_lookups.sql` in BUILD_PLAN section 3.
- `hawker_centre.name` and `address` are `VARCHAR(100)` (longest values 80 and 75).
- Trigger (a) also rejects a sale with a non-resale flat type. `building` has a CHECK that property blocks carry all attributes and stubs carry none.
- MongoDB block stats come from one pipeline (`db/mongo/stats.py`): `$merge` for the bulk rebuild, a plain aggregate for one block inside a transaction. Medians match `percentile_disc(0.5)` exactly; `avg_psm` agrees within 0.01 (Mongo rounds half to even).
- MongoDB stores buyer emails lowercased; the validator rejects upper case.
- The ERD is 36 cm wide at natural size: put it on a landscape page in the report.
- Next: PostgreSQL and MongoDB owners review `data/clean-etl`; Phase 2 query work can start on the loaded sample.

## 2026-10-20 Phase 0 decisions

- `hawker_centre` is the ninth PostgreSQL table (needed for the Haversine food-access query). Listed as a deliberate change in docs/BUILD_PLAN.md section 15.
- MongoDB runs as a single-node replica set with a build-time keyfile (spike passed: auth under `--replSet`, multi-document transactions, `$geoNear` inside `$lookup`). No standalone fallback. `mongo/replset-spike` is closed; the setup is already on main via db/mongo/Dockerfile and db/migrate.py.

## 2026-10-18 Gate 0 decisions

- Scope follows the submitted progress report. Deviations are listed in docs/BUILD_PLAN.md section 15 and will be stated in the final report.
- `lease_commence_year` moves from `resale_transaction` to `building` (3NF: one value per block).
- 82 MACPHERSON LANE (3 sales, no property row) loads as a stub building so all 241,357 sales are in both DBs.
- Data snapshot: the three files downloaded on 26 Sep 2026 are the pinned snapshot (SHA-256 in etl/manifest.json).
- Image pins kept: postgres:18.6-bookworm, mongodb/mongodb-community-server:8.3.11-ubi8-slim, python:3.12.15-slim-bookworm.
- Repository to be made private by its owner.

## Phase 0 spike results (2026-10-18)

- The Mongo image accepts `MONGODB_INITDB_ROOT_*` (and the older `MONGO_INITDB_*`), ships `mongosh`, runs as uid 1000 `mongod`, and its entrypoint already adds `--bind_ip_all`.
- Single-node replica set with a build-time keyfile works with auth; root user is created under `--replSet`; multi-document transactions commit and abort correctly.
- `$geoNear` inside a `$lookup` sub-pipeline with `near: "$$loc"` works on 8.3.11.
- `$median` and `$percentile` accept only `method: "approximate"` on 8.3.11, so the exact median is computed with `$sortArray`.
