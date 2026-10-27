# Project progress

Decisions and next actions only (README rule). Newest first.

## 2026-10-28 Task 2.5 interface

- Chart.js 4.4.7 is vendored as the npm package file `dist/chart.umd.js` (unmodified, licences alongside), served from `/static/vendor/`; no CDN at runtime. A test checks its SHA-256 against `app/static/vendor/README.md`.
- Chart data reaches JavaScript only as embedded JSON. Money is rounded half up to 2 dp and sent as a string, so the chart JSON is byte-identical in both DB modes.
- Wide tables scroll inside a `.table-wrap` element; tables themselves keep normal table layout.
- `ruff format --check` reports 7 files that predate this task; CI runs `ruff check` only, so they are left as they are.

## 2026-10-27 Task 2.4 rankings

- Towns are ranked within each flat type by median price only (`RANK()` in PG, `$rank` in MongoDB), so equal medians share a rank and the next rank skips; the town name is only the final tie-break for display order. "Above island median" means strictly greater.
- The approved spec expected three towns for 2017 `3 ROOM` on the sample; the sample has five (GEYLANG and JURONG EAST each have one building). The test asserts all five (BUKIT TIMAH 1, MARINE PARADE 1, BISHAN 3, GEYLANG 4, JURONG EAST 5), which still shows the shared first rank and the skip to 3.
- Report notes: the PG correlated subquery for the island median runs once per (flat type, town) row while `$facet` computes it once per flat type; with no flat type selected, neither database has an index starting with `txn_month`, so both scan.

## 2026-10-27 Task 2.3 trends

- Monthly figures and the previous-year median come from `mv_town_month_stats` (self-join on `month - interval '12 months'`); rolling 12-month figures from a `LATERAL` `percentile_disc(0.5)` over calendar months. MongoDB uses `$setWindowFields` month ranges `[-12, -12]` and `[-11, 0]` over months grouped first, with the lower median from `$sortArray`. A month with no sales 12 months earlier shows "No sales" for the previous-year median.
- Both bounds still see earlier sales: the previous-year median and the rolling figures of the first month in range include sales before `month_from`.
- PG resolves town and flat type to ids through `TOWN_IDS` and `FLAT_TYPE_IDS` (as search does) instead of joining the lookup tables.
- On full data the equivalence list is a fixed rule (the first three series with missing months, then every nth series, ten in all), not a hand-picked list.

## 2026-10-27 Task 2.2 search and filter

- `etl/clean.py` now defines `TOWN_IDS`, `FLAT_TYPE_IDS` and `FLAT_MODEL_IDS` (name to id, the 1-based list positions) and asserts each has one entry per list item. `lookups()`, the cleaning step and the search repos all use them, so the app maps form values to the same ids the ETL loads. Re-running the sample pipeline left `docs/import_report.md` and every count unchanged.
- PG search filters on the id columns of `v_transaction_detail` so it can use the same index shapes as MongoDB. Street search is a `LIKE` with `%`, `_` and `\` escaped in PG and an escaped, case-sensitive regex in MongoDB.
- Sample timings (6,275 sales, unfiltered, median of 14 warm runs): count PG 1.36 ms, MongoDB 0.98 ms; first page PG 3.47 ms, MongoDB 3.30 ms. These are indicative only; the Phase 4 benchmark gives the formal figures.

## Blocked, needs Ahsanul

- No GitHub Release `snapshot-2026-09` exists, so `python -m etl.download --release` (the manifest's default tag) cannot fetch the snapshot; only `--from DIR` works. Publishing the three source files as a release would put them on the repository, which is still public (next point). Decide whether to create the release; until then the user manual and the fresh-clone test use `--from` with the files from the submission zip.
- The repository is still public, although Gate 0 decided it would be made private by its owner. Changing visibility is a repository setting, so it is left to you.
- The Phase 4 fresh-clone test can only run on the development machine; the clean Windows machine BUILD_PLAN section 13 asks for needs a teammate.

## 2026-10-27 Phase 2 plan

- Phase 2 is split by feature instead of BUILD_PLAN section 13's per-database branches, so each PR carries both database sides and its own equivalence tests. The PostgreSQL and MongoDB owners review their side of each feature PR (README rule).
- The conventions every feature follows (repo layout, DTO types, DB-mode switch, timing, equivalence harness, formatting filters) are in `docs/conventions.md`.
- The header's DB-mode links reuse the request path only when it fully matches a strict path allowlist (no empty segments, no backslash or control characters), otherwise they point at `/`. Two review rounds found `//host` and `/\host` paths turning the links into off-site URLs; the allowlist closes the whole class.
- Task list:
  - 2.1 DB-mode switch, timing, repo pattern, equivalence harness and building page: merged (PR #12, `app/db-mode-switch`).
  - 2.2 Search and filter: merged (PR #13, `app/search`).
  - 2.3 Trends: merged (PR #14, `app/trends`).
  - 2.4 Rankings: merged (PR #15, `app/rankings`).
  - 2.5 Interface (Chart.js, styling): `app/ui-search`.
  - Phase 2 gate: not started.

## 2026-10-27 Phase 1 gate passed

- From empty volumes on main 7fcfdec: `up` and `migrate` succeeded, and the full clean and load of both databases took 26 s. PostgreSQL holds 275,471 rows and MongoDB 254,844 documents. Every compared count matches: the 12 rows in `docs/import_report.md`, "Database counts". Lookups are checked against cleaning in PostgreSQL only.
- Active in PostgreSQL:
  - all 88 constraints are validated, and both triggers are enabled;
  - `app_rw`, `app_admin` and `app_ro` hold only their section 4 privileges;
  - only `app_admin` can execute `refresh_town_stats()`, which is SECURITY DEFINER with a fixed search path;
  - `neighboar_owner` owns every object.
- Active in MongoDB:
  - all five collections have strict validators;
  - the `etl`, `app` and `app_admin` users hold their section 5 roles;
  - all indexes are built.
- 159 tests pass on the full data as well as on the sample.
- The ERD, architecture and MongoDB model diagrams are in `docs/diagrams/` as SVG and PNG.
  - The PNGs are rendered with `dot -Gdpi=300`; for example, the ERD is 4,284 px wide, which is 36.3 cm at 300 dpi.
  - The PNG files carry no DPI metadata, so set the width by hand when inserting them into the report.
- Next: Phase 2 (DB-mode switch with timing, search and filter, trends and rankings, building page, equivalence suite).

## 2026-10-27 Phase 1 review fixes

- Commit 10d8943 changed only a comment in the applied `001_schema.sql`, so any database migrated before 22 Oct now fails `migrate` with "changed after it was applied". It is kept as is, because reverting would break databases migrated after 22 Oct instead. Teammates with an older volume run `docker compose down -v` and reload (no user data exists yet). Later schema changes go in a new numbered file.
- Trigger (a)'s resale-flat-type rule is already enforced in MongoDB by the `flat_type` enum. Its completion-year rule needs the building's `year_completed`, which a validator cannot read, so the Phase 3 Mongo admin transaction enforces it and the Phase 3 negative tests cover both databases.
- PostgreSQL rounds a price with more than 2 dp and an area with more than 1 dp silently, while MongoDB now rejects them. Parity therefore relies on the app: admin sale input must reject a price with more than 2 dp and an area with more than 1 dp before either database sees it (Pydantic, section 9), covered by Phase 3 negative tests.
- The Mongo validators now mirror the PostgreSQL CHECKs for the price maximum, area and price scale, property-block attributes, year bounds and building and hawker coordinates.

## 2026-10-27 Import report database counts

- The import report now also shows PostgreSQL and MongoDB counts after load, taken by `etl/db_counts.py` at the end of the pipeline, so the Phase 1 gate can show both databases hold the same snapshot. This overrides BUILD_PLAN section 7 step 8, which limited the report to cleaning counts.
- Counts are plain row and document counts. Lookups are compared in PostgreSQL only (MongoDB embeds them), and "sale (all sources)" is compared between the two databases only, so admin-recorded sales never break a match; the report marks counts taken for a different cleaning run as stale.

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
