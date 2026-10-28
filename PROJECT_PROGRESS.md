# Project progress

Decisions and next actions only (README rule). Newest first.

## Run state (paused 2026-10-28)

- Phase 3. Tasks 3.1a (PR #18), 3.1b (PR #19), 3.2 (PR #20), 3.3 (PR #22), 3.4 (PR #23) and 3.5a (PR #24) are merged; `main` is at the merge of PR #24. Phase 2 closed with PR #16 (task 2.5) and PR #17 (gate).
- No feature branch is in progress, and there are no open PRs.
- Approved specs are kept locally outside version control in `.claude/specs/`:
  - 3.3 admin sales: `p3_3_spec.md`, plus `phase3_notes.md`. That note says to quantize the admin sale price to 0.01 and the area to 0.1 before writing Decimal128.
  - 3.4 food access: `p3_4_spec.md`;
  - 3.5a integrity matrix and 3.5b privilege audit: `p3_5_spec.md`;
  - Phase 4 (4.1a, 4.1b, 4.2, 4.3, 4.4, revision 4): `p4_spec.md`.

  They are approved; do not re-plan or re-review them.
- The local `.env` has `DEMO_BUYER_PASSWORD` and `DEMO_ADMIN_PASSWORD` set; the databases hold the sample plus the synthetic buyers.
- Next action: create `app/privilege-audit` from `main` and send task 3.5b of `.claude/specs/p3_5_spec.md` to the implementer. Then run then the Phase 3 gate. The gate includes a full-data load that regenerates `docs/import_report.md` with buyer and saved-block counts. Then run Phase 4 and stop after its gate.

## 2026-10-28 Task 3.5b privilege audit

- The PostgreSQL grid is an allowlist written out in `tests/negative/test_privileges.py` from `005_grants.sql`, `db/migrate.py` and BUILD_PLAN section 4, not read back from the catalogue. It covers every table, view, materialised view, sequence and function in `public`, all eight table privileges, the three sequence privileges, EXECUTE, and USAGE and CREATE on the schema, for `app_rw`, `app_admin` and `app_ro`. A new object in `public` fails until it is listed, as does an extra or a missing privilege. The two trigger functions are listed with no EXECUTE for anyone, because a trigger needs no grant. The roles are also checked to be plain login roles with no membership, since an inherited role would add privileges the grid does not show.
- The MongoDB grid compares `connectionStatus` with `showPrivileges` against `MONGO_ROLES`, using the role names each user holds, which the test pins itself so a change to `MONGO_USERS` is caught. Real refused writes are PostgreSQL 42501 and MongoDB code 13. The MongoDB probes for `collMod`, `drop` and `createIndex` use a collection that does not exist (and a no-op `collMod` on the app's own collections), so a widened privilege could not damage data.
- Runtime identities are checked through a spy that replaces the four pools and clients on `Databases` and records each checkout. `ROUTES` in `tests/integration/test_runtime_identities.py` holds a request and a signed-in role for every route, keyed by method and template, and a test fails on a route without an entry or an entry without a route. Routes come from `iter_route_contexts(app.routes)` (FastAPI 0.143), so the OpenAPI and docs routes and the `/static` mount are in the table too. Ordinary routes may check out only `pg_rw` and `mongo_app`; admin routes must check out something and only `pg_admin` and `mongo_admin`. Sign-in and CSRF token fetches happen before the spy is installed.
- The web pools set `application_name = 'neighboar-web'` (`WEB_APPLICATION_NAME` in `app/db.py`, passed to `Settings.pg_dsn`); every other tool keeps `neighboar`, so `pg_stat_activity` separates the web sessions by role for the report.
- The section 9 file checks are unit tests (`tests/unit/test_section9.py`): `web` publishes only on `127.0.0.1`, `postgres` and `mongodb` publish nothing, `.env` is in `.gitignore` and `.dockerignore`, every `*_PASSWORD` and `SECRET_KEY` in `.env.example` starts with `change-me`, both Dockerfiles end with a non-root `USER` with no build step after it, and `create_app` refuses an empty key, one under 32 characters and one starting with `change-me`. The repo already satisfied all of these, so no Dockerfile or compose change was needed.
- The CI non-root step runs after the web app is up and reads the real and effective UID of PID 1 for `postgres`, `mongodb` and `web`, failing on 0 or on no value. The MongoDB image (UBI 8 slim) has no `grep` and no package manager, so `docker compose exec -T <svc> grep Uid /proc/1/status` fails there. The step runs `docker compose exec -T <svc> cat /proc/1/status | grep Uid` instead, with the filter on the host. The UIDs seen locally are 999 (`postgres`), 1000 (`mongodb`) and 10001 (`web`).

## 2026-10-28 Task 3.5a integrity matrix

- The rule list is read from the live catalogues, never from the SQL and JSON files: 107 PostgreSQL rules (constraints of type `c`, `f`, `n`, `p`, `u`, the one constraint-less unique index, the two triggers, and the `VARCHAR`/`NUMERIC` limits as `:type` rules) and 271 MongoDB rules (every `required`, `bsonType`, bound, length, pattern, enum, item count and `additionalProperties` at every level, and every `$expr` comparison keyed by its JSON path). `docs/integrity_matrix.md` has one row per pairing, and `tests/negative/test_matrix.py` fails on a rule without a row, a row for a rule that no longer exists, a listed test that does not exist, or a rule without a case whose id is its key. Node ids come from a separate `pytest --collect-only`, so the test works under `-k`.
- The matrix test needs the `etl` MongoDB user (`MONGO_ETL_PASSWORD`), because `app` and `app_admin` may not run `listCollections`. It reads only the catalogues, so it passes on an empty migrated database as well as on the sample; the other negative tests need the sample.
- Every case follows the baseline method (accept a baseline, change one value) and asserts the exact error: SQLSTATE plus `constraint_name` for `c`, `f`, `p`, `u` and the unique index; SQLSTATE plus table and column for NOT NULL; SQLSTATE alone for `:type`; message text for triggers. MongoDB cases assert code 121 and that `errInfo.details` names the failing path and keyword; for `$expr` rules they assert that only the expression failed. In PostgreSQL the whole case runs inside an outer transaction that is always rolled back, so a wrongly accepted bad write can never be committed.
- Where one PostgreSQL CHECK bundles clauses that MongoDB splits (`storey_band`, `building_property_attrs`, the range checks, `building_coords_both_or_neither`, trigger (a)), the PostgreSQL case id is `<key>#<clause>`, one per MongoDB clause, with the same bad value.
- Clause cases flip the value that makes the clause fail, so the failure is new relative to the accepted baseline: the two `anyOf` enum rules are tested by flipping `in_property_dataset` (a stub or property document always fails the other branch, so omitting a field would not isolate them), and PostgreSQL `building_property_attrs` has matching `#in_property_dataset_false` and `#stub_in_property_dataset_true` cases. MongoDB case ids may also carry `#<clause>`: the email pattern has `#no_at` and `#at_first` (the PostgreSQL `buyer_email_check` values) and `#upper`, which is MongoDB only, since PostgreSQL accepts mixed case and relies on `buyer_email_lower_key`.
- Three kinds of MongoDB rule cannot be broken by omission or by a normal value, and their cases say so: `required._id` (the driver supplies an ObjectId, so the case asserts the `_id` type or enum rule fails instead), the root `bsonType: object` (a document is always an object, so the case asserts the live rule), and the `not` in the stub branch (MongoDB reports only `child expression matched`, so the case asserts `not` at `anyOf.1`). Counter documents are tested inside a MongoDB transaction that is aborted, because their `_id` values are live.
- Rules with no counterpart are recorded as `app layer:` with the reason (trigger (a)'s completion year in the 3.3 Mongo transaction, foreign keys by lookups in the app, decimal scale by the 3.3 quantizing) or `none:` (lookup tables and derived statistics). The MongoDB unique indexes (`block_street_unique`, `email_unique`, `_id`) are not validator rules and so are not enumerated; `test_mongo_unique_index` covers them and the rows cite it. Earlier tests (PR #10, 3.2 and 3.3) are cited in the rows, not repeated.
- Fresh-database check, which leaves the working volumes alone: `docker compose -p neighboar-matrix run --rm migrate`, then the matrix test in that project with the repo `tests/` and `docs/` mounted, then `docker compose -p neighboar-matrix down -v`. The CI workflow needed no change: its test step already runs every test under `tests/` with the owner and etl passwords.

## 2026-10-28 Task 3.4 food access

- Distances: PostgreSQL computes Haversine in SQL with R = 6378100 m (MongoDB's radius) on `double precision` casts of the coordinates; MongoDB uses `$geoNear` spherical on `geometry`. Both sort and test the 1 km radius (inclusive) on the unrounded distance, and the DTO rounds to 0.1 m with one shared function (`round_metres`, half up). `distance_m` is the only tolerant field (`tol` 1 m, BUILD_PLAN section 6); ids, order and `within_1km` are exact.
- The status filter (`Under Construction`) sits in the `$geoNear` `query` and in the PostgreSQL `WHERE`, so a closed centre never counts towards the five nearest or the 1 km total. Order is distance, then hawker id (block view) and distance, then building id (town view). The data has no exact ties, so a test checks the statement text and the pipeline `$sort` stages.
- Block view: PostgreSQL is one statement (two lateral subqueries for the five nearest and the count). MongoDB reads the block's `location`, then runs one `$geoNear` into a `$facet` (`$geoNear` may not appear inside `$facet`). A block without coordinates returns `None` from both repos and the page shows "Unknown" without calling the food repo; the stub (82 MACPHERSON LANE) is geocoded and shows real results. The building page footer now has two timing lines for a geocoded block ("building page" and "food access").
- Town view (`GET /food?town=...`): PostgreSQL is one statement with two `CROSS JOIN LATERAL`s; MongoDB is one aggregate on `building_profiles` with two `$lookup` stages whose pipelines start with `$geoNear` (`near: "$$loc"`). The town is validated against the 27 names (422 otherwise). A "Food" link was added to the main menu.
- Equivalence cases `food_block` (every geocoded block on the sample, 326, including the stub; every 25th geocoded id plus the stub on full data) and `food_town` (all 5 sample towns with geocoded blocks; on full data 3 towns: fewest, median and most geocoded blocks) are in the registry.
- Measurement for the report: on the sample, the maximum PostgreSQL vs MongoDB difference over all 42,054 block and centre pairs is 5.9e-10 m (1 m is the limit). To be re-done at the Phase 3 gate on full data before the figure is quoted.

## 2026-10-28 Task 3.3 admin sales

- Admin routes read the signed-in account as `app_admin` too: `load_account` switches pools and clients for paths under `/admin`, so an admin request never touches `app_rw` or `app`. The routes get their connection from the dependencies `admin_pg` and `admin_mongo` (each yields only in its own DB mode); `rw_pg` and `app_mongo` are the normal pair. A signed-out visitor gets 403 on `/admin`, like a non-admin buyer. The flag counts only in the mode the buyer signed in under.
- A write redirects (303) to `/admin`, and the page shows the last action's message and its timings (write and refresh as separate lines). That last action is held in process, like the stale flag and the lock, so it is shared by admins and lost on restart.
- The month must fall in 1900 to 2100 (the building year bounds). Block and street are normalised with `normalise_key` and must match a building exactly; the lookup runs inside the lock, with the write.
- The list of admin-recorded sales is newest first by `txn_id`, at most 20, and each row has its own delete button; the delete form accepts any id.
- The refresh button exists in PostgreSQL mode only and the rebuild button in MongoDB mode only; the other mode gets 400. A rolled-back write is never followed by a refresh. A failed refresh keeps the earliest stale time; any successful refresh (after a write or from the button) clears it. A rolled-back or rejected write leaves the flag alone.
- Rejections: the PostgreSQL message of trigger (a) is shown as it is, and MongoDB builds the identical text (`sale in YYYY-MM predates block completion in YYYY`). A MongoDB validator rejection (code 121) shows a fixed message. The app rejects non-resale flat types before either database, so only direct writes reach trigger (a)'s flat-type rule and the `flat_type` enum.
- The rollback demo compares island sales, the block's sales and its median before and after. PostgreSQL derives the block figures live; MongoDB reads the profile's stored `stats`. Deleting a missing id with the demo ticked reports `not_found`, as without it.
- The MongoDB `txn` counter is bumped inside the transaction, so an abort or rejection leaves it unchanged. A committed admin write advances it for good, as the PostgreSQL sequence is advanced by rollbacks too. `test_database` compares the counter with the largest loaded id, so the admin test cleanup (`tests/admin_support.py`) restores the counter to its pre-test value. It does not restore the PostgreSQL sequence.
- Page and identity tests create the app in the test process (real databases) so they can patch the refresh to fail and swap the normal pool and client for tripwires. They need `PG_OWNER_PASSWORD` and `MONGO_ETL_PASSWORD`.
- Report note: `$merge` runs outside a transaction, so an admin write committing during a rebuild could be overwritten by stats from an older read. Record, delete and both rebuild actions therefore share one `threading.Lock` in the single uvicorn worker; a test holds a rebuild open and shows a write waits for it. With more than one worker the lock would not hold.

## 2026-10-28 Task 3.2 saved blocks

- Saving, editing a note and removing return a typed outcome, never an exception: `ok`, `already_saved`, `not_saved`, `no_such_building`, `cap_reached`. A save checks them in that order in both databases, so a buyer at 50 saves gets the same answer from each. PG checks the building and the existing row inside the transaction before the INSERT; trigger (b) stays the real cap guard. MongoDB reports a validator rejection (code 121) as `cap_reached` only when the buyer really holds 50 saves.
- `not_saved` changes nothing in either database, `last_active_at` included.
- The rollback demo (`simulate_failure`) runs only after the `no_such_building` and `already_saved` checks. PG raises before commit; MongoDB raises inside `with_transaction`. The page shows the counts seen inside the transaction (PG insert 1 and update 1, MongoDB matched 1). The normal MongoDB save stays a single atomic `update_one`.
- Page messages after a POST are keys from a fixed table plus integer counts, so no request text is echoed back. A signed-out POST redirects to sign-in before any form validation.
- The scripted equivalence sequence lives in `tests/equivalence/cases.py` but outside `CASES`, so the read-only comparisons and `bench/verify.py` never write.
- The building page's signed-out prompt is plain text rather than a link, so the page stays byte-identical in both modes.

## 2026-10-28 Task 3.1b accounts and sessions

- Each database is a complete account store. Registering, signing in and (from 3.2) saving blocks touch only the database selected for that request. The synthetic buyers exist in both with the same ids, but later registrations can get different ids in each.
- The session holds `{"uid": {"pg": id, "mongo": id}, "csrf": token}` and nothing else. This overrides BUILD_PLAN section 9's "holding only buyer_id": the CSRF token has to live in the session, and signing in under one mode does not sign the user in under the other. The cookie is signed, `HttpOnly`, `SameSite=Lax` and lasts 8 hours.
- Register redirects (303) to the sign-in page rather than signing the user in. A duplicate email gets 409, a failed sign-in 401 (the same message whether the email is unknown or the password is wrong, and an unknown email is still checked against a dummy hash), and a throttled sign-in 429.
- The throttle covers sign-in only. It counts 5 failures per (email, client IP) in a sliding 15-minute window, is per process (the app runs one uvicorn worker), and takes the IP from the connection. Behind Docker Desktop every client shares one IP, so in practice it limits per email.
- CSRF is one app-level dependency that runs before every route's own dependencies and form validation, so any POST without a valid token gets 403. A test lists every POST route, so routes added later are covered automatically. On FastAPI 0.143 the test walks `iter_route_contexts`, because `app.routes` no longer lists routes from included routers.
- If the signed-in account cannot be read (its database is down), the page treats the visitor as signed out for that request instead of failing, so `/health` still reports 503.

## 2026-10-28 Task 3.1a synthetic buyers

- `etl/synth_buyers.py` writes 200 buyers and their saved blocks into both databases with `random.Random(65)` (948 saves on the sample). The seed drives names, saves, notes and timestamps only; passwords never come from it. Ids 3 to 200 get random passwords that are discarded, and each hash goes to both databases unchanged.
- Demo accounts: `demo@neighboar.local` (id 1) and `admin@neighboar.local` (id 2, admin). Their passwords come from `DEMO_BUYER_PASSWORD` and `DEMO_ADMIN_PASSWORD` in each teammate's own `.env`. These reach the `etl` service and the test container only, never `web`. The pipeline stops before cleaning if either is missing, not 10 to 128 characters or starts with `change-me`.
- The script refuses to write (`SynthCollision`) if ids 1 to 200 hold a non-synthetic email or a synthetic email sits on another id. It checks both databases before writing to either. The PG buyer sequence and the Mongo `buyer` counter only move forward.
- The test for "a synthetic email on another id" first removes buyer 3 and its saves, then puts `buyer003@example.com` on id 300, and restores buyer 3 exactly afterwards. Both databases enforce unique emails (`buyer_email_lower_key` in PG, `email_unique` in MongoDB), so id 300 cannot hold that email while buyer 3 does.
- `docs/import_report.md` shows 0 buyers and 0 saved blocks until it is regenerated by the full-data load at the Phase 3 gate.

## 2026-10-28 Phase 2 gate passed

- On main 65524a1, the full clean and load of both databases took 23 s. Every count in "Database counts" matches: 241,357 sales and 13,358 buildings in each database. `docs/import_report.md` is unchanged.
- The equivalence suite passes on the full data: 65 passed, 3 skipped. The three skips are rankings facts that only hold on the sample (exact ties and a single-town case); the full-data rankings cases still run.
- The whole suite gives 452 passed and 5 skipped on the full data. After reloading the sample (2 s), it gives 457 passed with no skips.
- The rankings page test for a flat type with no sales now skips on the full data, which has sales of every resale flat type in every year from 2017 to 2026. It still asserts on the sample.
- Every page shows its per-request timing in both modes (integration tests check the footer).
- Deferred to Phase 5: BUILD_PLAN section 13 asks for report sections 1 and 2 at this gate, but the report is drafted in one pass in Phase 5.
- Next: Phase 3 (3.1a synthetic buyers, 3.1b auth, 3.2 saved blocks, 3.3 admin sales, 3.4 food access, 3.5a integrity matrix, 3.5b privilege audit).

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
  - 2.5 Interface (Chart.js, styling): merged (PR #16, `app/ui-search`).
  - Phase 2 gate: passed (see "Phase 2 gate passed").

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
