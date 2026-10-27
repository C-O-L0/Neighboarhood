# Neighboar Build Plan (v3, after two review rounds)

Team 65, INF2003 Database Systems, Y2T1. Drafted Sun 18 Oct 2026. Final submission Sun 29 Nov 2026, 11:30 PM; target submit Fri 27 Nov.

Scope is the submitted progress report. Items marked **[Q#]** depend on an open question at Gate 0 and show the recommended option.

## 1. Starting point

- Repo `C-O-L0/Neighboarhood`: 3 commits (README brief + logo), nothing built. Week 7 of 13; six weeks remain.
- Data in hand: resale 241,357 rows (2017-01 to 2026-09), property 13,357 blocks (10,796 residential, 2,561 with zero dwelling units), hawkers 129 GeoJSON points (WGS84).
- Data facts that shape the schema (verified with pandas):
  - **67 resale prices are fractional** (for example 888,888.88). Prices are `NUMERIC(9,2)` in PostgreSQL and `Decimal128` in MongoDB, never INT or double.
  - `lease_commence_date` is a block attribute: 9,752 of 9,753 resold blocks have one value; the exception is 37 TEBAN GDNS RD (1966 and 1981). It moves to `building` (3NF). When a block's sales disagree, the year consistent with the sales' own remaining lease wins (exactly one candidate year must fit every valid sale date and remaining lease, otherwise cleaning stops for manual review), so Teban stores 1966 and the single 1981 sale is a suspected source error inferred from remaining-lease consistency (section 15).
  - `remaining_lease` is not derivable from lease year and month (13,548 of 200,244 block-month groups hold more than one value), so `remaining_lease_months` stays on the transaction.
  - The property file counts `studio_apartment_sold` and `other_room_rental`, which have no resale flat type, so `flat_type` is a 9-row superset.
  - 82 MACPHERSON LANE (3 sales) has no property row. 318 rows are exact duplicates and are kept (no unit number in the data).
  - 63 of 131 town and flat-type series have missing months, so "12 rows back" is not "12 months back".
  - Storey bands overshoot `max_floor` for 15,387 sales, so no `storey_max <= max_floor` rule.

## 2. Architecture

```
Browser (Jinja2 pages + vanilla JS, Chart.js vendored)
   │  HTTP, signed session cookie
FastAPI app (web)
   ├─ routers/   search, trends, building, saved, food, auth, admin (record a sale)
   ├─ services/  validation, db-mode dispatch, timing wrapper
   └─ repos/     one Protocol per feature ─► repos/pg/*    (psycopg 3 pools: app_rw, app_admin)
                                          └► repos/mongo/* (PyMongo: app, app_admin users)
PostgreSQL 18.6  ◄─ migrate + etl (one-off Compose services) ─►  MongoDB 8.3.11 (single-node replica set)
```

DB mode is a `?db=pg|mongo` parameter with a header toggle that sets a cookie. Repos return plain DTOs; the service layer times each call with `perf_counter_ns` around the call **and** full materialisation (`fetchall()` / `list(cursor)`), and the footer shows the figure.

## 3. Repository layout

```
compose.yaml          postgres, mongodb, migrate (runs on every up), web; etl in profile "tools"
Dockerfile            python:3.12.x-slim-bookworm (pinned), non-root user
db/mongo/Dockerfile   thin wrapper on the Mongo image: keyfile generated at build (`head -c 756 /dev/urandom | base64`), owned by the mongod UID, mode 400; nothing secret committed
.env.example          root and per-role passwords, SECRET_KEY, DSNs; no real values
.gitattributes        first commit: * text=auto, eol=lf for sh sql js py Dockerfile .env.example
requirements.txt + requirements.lock (pip-compile)
app/        main.py config.py db.py auth/ repos/{pg,mongo}/ services/ routers/ templates/ static/
db/postgres/  001_schema.sql 002_lookups.sql 003_views.sql 004_triggers.sql 005_indexes.sql 006_grants.sql
db/mongo/     validators/*.json  indexes.py  users.py
db/migrate.py     idempotent: replSetInitiate, roles and users (passwords from env), SQL files as neighboar_owner, Mongo validators
etl/          download.py manifest.json clean.py geocode.py synth_buyers.py load_pg.py load_mongo.py report.py
data/raw/          gitignored (full snapshot lands here)  [Q4]
data/sample/       committed, ~5k sales over 3 towns + their blocks + all hawkers
data/cache/block_coordinates.csv  committed geocode cache
scripts/      backup.sh restore.sh (pg_dump + mongodump), make_submission_zip.py
bench/        cases.yaml run.py verify.py results/
docs/         diagrams/  data_dictionary.md  user_manual.md  import_report.md  demo_script.md  references.md
tests/        unit/ integration/ equivalence/ negative/
.github/workflows/ci.yml   ruff, then `docker compose up -d --wait`, `compose run migrate`, sample load, pytest inside `web` (service containers cannot run a replica set)
PROJECT_PROGRESS.md        decisions, owners, PR log (README rule)
```

## 4. PostgreSQL schema (3NF, 9 tables) [Q2]

| Table | Columns and constraints |
|---|---|
| `town` | `town_id SMALLINT PK`, `code VARCHAR(3) UNIQUE NOT NULL`, `name VARCHAR(40) UNIQUE NOT NULL` (27 rows; TG/Tengah has no sales, UI handles empty towns) |
| `flat_type` | `flat_type_id SMALLINT PK`, `name VARCHAR(20) UNIQUE NOT NULL`, `is_resale_type BOOLEAN NOT NULL` (9 rows) |
| `flat_model` | `flat_model_id SMALLINT PK`, `name VARCHAR(30) UNIQUE NOT NULL` (21 rows) |
| `building` | `building_id INT GENERATED BY DEFAULT AS IDENTITY PK`, `town_id FK NOT NULL`, `block VARCHAR(5) NOT NULL`, `street VARCHAR(40) NOT NULL`, `UNIQUE(block, street)`, `year_completed`, `lease_commence_year`, `max_floor` (`CHECK 1..60`, all NULL-able for the stub), land-use booleans, `in_property_dataset BOOLEAN NOT NULL`, `latitude/longitude NUMERIC(9,6)` with both-or-neither and Singapore bounding-box CHECKs |
| `building_flat_supply` | `PK(building_id, flat_type_id)`, `sold_units >= 0`, `rental_units >= 0`, `CHECK (sold_units + rental_units > 0)` (ETL skips all-zero pairs), `ON DELETE CASCADE` from building |
| `resale_transaction` | `txn_id BIGINT GENERATED BY DEFAULT AS IDENTITY PK`, `building_id`, `flat_type_id`, `flat_model_id` FKs, `txn_month DATE CHECK (extract(day) = 1)`, `storey_min/max SMALLINT CHECK (1 <= min <= max <= 60)`, `floor_area_sqm NUMERIC(5,1) CHECK (> 0 AND < 400)`, `remaining_lease_months SMALLINT CHECK (0..1188)`, `resale_price NUMERIC(9,2) CHECK (> 0)`, `source VARCHAR(8) NOT NULL CHECK (source IN ('snapshot','admin'))` with no default (an insert must name it: the loader writes `snapshot`, admin routes write `admin`) |
| `buyer` | `buyer_id INT GENERATED BY DEFAULT AS IDENTITY PK`, `email VARCHAR(254) NOT NULL` + `UNIQUE INDEX ON lower(email)`, `password_hash TEXT NOT NULL`, `display_name VARCHAR(60)`, `is_admin BOOLEAN NOT NULL DEFAULT false`, `created_at TIMESTAMPTZ DEFAULT now()`, `last_active_at TIMESTAMPTZ` |
| `saved_building` | `PK(buyer_id, building_id)`, `note VARCHAR(500)`, `saved_at TIMESTAMPTZ DEFAULT now()`, cascade on buyer delete, restrict on building delete |
| `hawker_centre` | `hawker_id INT PK`, `name`, `address`, `postal_code CHAR(6)`, `status VARCHAR(30)`, `stall_count SMALLINT >= 0`, `latitude/longitude NUMERIC(9,6) NOT NULL` (needed for the committed Haversine query; no FK because proximity is computed, not stored) |

Stub building (82 MACPHERSON LANE): kept with `in_property_dataset = false`. It appears in search and building pages, can be saved, and joins food access if geocoded. Its unknown attributes (supply, units, completion year, max floor, land use) are shown as "Unknown", never 0 or No.

Identity columns are `BY DEFAULT` so `COPY` can load ETL ids, followed by `setval(seq, coalesce(max(id), 1), max(id) IS NOT NULL)` on every identity table.

Advanced objects:
- Views: `v_transaction_detail` (joins, `price_psm`, storey label), `v_building_units` (derived total units, NULL for a stub rather than 0; ETL asserts it equals the source `total_dwelling_units` for all 13,357 rows), `v_town_year_stats`.
- Materialised view `mv_town_month_stats(town_id, flat_type_id, month, n, median_price, avg_psm)` with a unique index. First population is a plain `REFRESH`; later refreshes are `CONCURRENTLY`, called through `refresh_town_stats()`: `SECURITY DEFINER`, `SET search_path = pg_catalog, public, pg_temp`, `REVOKE EXECUTE FROM PUBLIC`, `GRANT EXECUTE TO app_admin`.
- Analytics: town rankings with `RANK() OVER (PARTITION BY flat_type ...)`; YoY by self-join on `month - interval '12 months'` (correct with missing months); rolling 12-month median through a `LATERAL` subquery with `percentile_disc(0.5)` over the date range, computed only for the requested town and flat type (117 calls, not 15k; ordered-set aggregates cannot be window functions); correlated subquery for towns beating the island median for the same flat type and year.
- Triggers: (a) `BEFORE INSERT OR UPDATE`: reject a sale dated before `building.year_completed` (0 violations in source data, fires on admin writes); (b) cap saved buildings at 50 per buyer, taking `SELECT ... FOR UPDATE` on the buyer row first so concurrent saves cannot overshoot.
- Transaction demo: "save block with note" inserts `saved_building` and updates `buyer.last_active_at` atomically; a forced-failure path shows rollback.
- Indexes: `resale_transaction(flat_type_id, txn_month)`, `(building_id, txn_month DESC)`, `building(town_id)`, `(flat_type_id, resale_price, txn_id)` for price sorts (Mongo gets the same key shape, so the pair is like for like); `EXPLAIN (ANALYZE, BUFFERS)` before and after.
- Roles (created by `migrate` through `psycopg.sql.Literal`, passwords from `.env`, `ALTER ROLE ... PASSWORD` on reruns; SQL files applied after `SET ROLE neighboar_owner` so the owner owns every object and `ALTER DEFAULT PRIVILEGES FOR ROLE neighboar_owner` applies): `neighboar_owner` (owns schema; migrate and ETL), `app_rw` (SELECT; DML on `buyer`, `saved_building`), `app_admin` (as `app_rw` plus DML on `resale_transaction` and EXECUTE on the refresh function; used only by admin routes), `app_ro` (SELECT; bench only). `REVOKE ALL ON SCHEMA public FROM PUBLIC`, `GRANT USAGE` to app roles, `ALTER DEFAULT PRIVILEGES` for future objects.

## 5. MongoDB model

`_id` reuses the PostgreSQL keys for loaded data. Runtime-created buyers and sales take ids from a `counters` collection (`buyer` and `txn` sequences seeded to the PostgreSQL maximum; `findOneAndUpdate` with `$inc`). Runtime ids can diverge between the DBs. Building, month and price is not a key for a sale (318 identical rows exist), so: edits and deletes always target a `txn_id`; a scripted CRUD sequence run in both DBs records the id each DB returns in an id map kept by the test (no new production field); dataset comparisons are multisets (occurrence counts preserved, never sets); snapshot rows keep identical ETL ids in both DBs. Email stays a valid natural key for buyers.

| Collection | Shape | Indexes | Pattern |
|---|---|---|---|
| `building_profiles` | building fields, `town {code, name}`, `location` Point (omitted, not null, when not geocoded), `supply [{flat_type, sold, rental}]` (omitted for a stub, whose supply is unknown; the validator enforces this), `stats {txn_count, median_price, avg_psm, by_flat_type[], last_txn_month}`, `recent_sales` (latest 10) | `town.name`, `{block, street}` unique, `location` 2dsphere | converted: one-to-few embedded, subset pattern, computed pattern |
| `resale_transactions` | one document per sale; `building_id` plus denormalised `town`, `flat_type`, `flat_model`, `block`, `street`; `resale_price` Decimal128; `source` required, `snapshot` or `admin` | `{flat_type, txn_month}`, `{town, flat_type, txn_month}`, `{building_id, txn_month:-1}`, `{flat_type, resale_price, _id}` | converted; referencing because the 1:M is unbounded |
| `buyers` | account fields (incl. `is_admin`) with `email` stored lowercased, `saved [{building_id, block, street, town, note, saved_at}]` | `email` unique, `saved.building_id` | embedded M:N side with extended-reference snapshot; `maxItems: 50` |
| `hawker_centres` | kept as GeoJSON Feature (`type`, `geometry`, `properties`) as the report states | `geometry` 2dsphere | independent design |
| `counters` | `{_id: "buyer" or "txn", seq}` | | id allocation |

All collections get `$jsonSchema` validators (strict, error) before load; money fields are `bsonType: "decimal"` and the app converts `Decimal128` with `.to_decimal()`. Users (created by `migrate`): `etl` (readWrite), `app` (custom role: `find` on all; `insert/update/remove` on `buyers` and `counters`), `app_admin` (adds `insert/update/remove` on `resale_transactions` and `building_profiles`).

Mongo advanced work, matched to the SQL side:
- Recording or deleting a sale (admin) runs in `withTransaction`: write to `resale_transactions`, recompute that block's `stats` and `recent_sales` with a plain `aggregate` (`stats_for_building` in `db/mongo/stats.py` returns both), write them with `updateOne` on `building_profiles`; a forced-failure path shows rollback. (`$merge` is not allowed inside transactions.)
- `$merge` is shown separately as the bulk rebuild of all profile stats (ETL and an admin "rebuild stats" action): the counterpart of the MV refresh.
- Transactions need a replica set: Phase 0 spikes a single-node replica set (member host `mongodb:27017`, initiated by `migrate` via PyMongo with `directConnection=true`, root-user creation checked under `--replSet`). It gives transactions, not high availability, and the report says so. There is no standalone fallback.
- `$setWindowFields` with `range` windows in `unit: "month"` sorted on `txn_month`, for YoY (`[-12, -12]`) and rolling stats (handles missing months); the exact rolling median first `$group`s prices by month, then windows over month arrays with `$concatArrays` and `$sortArray` (keeps stages under 100 MB); `$rank` for rankings; "beats island median" via `$facet` (island and town medians in one pass) then `$filter`.
- `$geoNear` food access, plus `$lookup` from `building_profiles` with a `$geoNear` sub-pipeline (`near: "$$loc"`) to attach nearest hawkers to each block in a result; checked in the Phase 0 spike, fallback is one batched `$geoNear` per block.
- `explain("executionStats")` before and after each index, mirroring `EXPLAIN ANALYZE`.

## 6. Features and equivalence contract

| Feature | PostgreSQL | MongoDB | Equivalence check |
|---|---|---|---|
| Search and filter | parameterised join on `v_transaction_detail`, `LIMIT/OFFSET` | `$match`, `$sort`, `$skip/$limit` on `resale_transactions` | same `txn_id` list and order |
| Trends and rankings | MV, `RANK`, self-join YoY, `LATERAL` rolling median | `$group`, `$setWindowFields` | same rows; money within 0.01 |
| Building page | join building + supply + latest sales | one read of `building_profiles` | same fields |
| Saved-block CRUD | transaction, cascade, cap trigger | `$push` with `$ne` guard, positional `$set`, `$pull` | same state after a scripted sequence (ignoring `saved_at` and snapshot fields) |
| Admin: record and delete a sale (edit is stretch) | INSERT/DELETE, trigger (a), MV refresh through `refresh_town_stats()` after commit (takes seconds; said in the demo) | transaction with `updateOne` stats recompute; `$merge` rebuild | after settle, same sales (multiset; runtime ids matched through the test's id map) and same recomputed stats |
| Food access | Haversine on `hawker_centre` (R = 6378.1 km, MongoDB's radius) | `$geoNear` spherical (metres), status filter on `properties.STATUS` in its `query` | same ids within 1 m; same status filter (exclude "Under Construction"); ties broken by id |
| Integrity and security | CHECK, triggers, roles | `$jsonSchema`, custom roles | each bad write rejected by both |

Rules: every ordered query ends with the id as tie-break; median is `percentile_disc(0.5)` in SQL and element `floor((n-1)/2)` of a `$sortArray` in Mongo (Mongo `$median` is approximate-only; it appears in the bench as a labelled extra, and if 8.3 offers an exact method we use it). This lower median, always an observed price, is deliberate and applies to building stats, town stats, rankings and rolling medians; UI labels and the report call it "median (lower of the two middle prices when n is even)". Money is compared with absolute tolerance 0.01; coordinates rounded to 6 dp in both; `txn_month` is midnight UTC.

Derived statistics after an admin write:
- MongoDB: the sale write and that block's `stats` and `recent_sales` update are one transaction.
- PostgreSQL: building-level figures are computed live from the tables and views. `mv_town_month_stats` is refreshed through `refresh_town_stats()` right after the admin write commits, in the same request. If the refresh fails, the sale stays committed, the town figures show as stale and the next refresh repairs them.
- Equivalence tests and benchmarks first run a settle step (refresh the MV; MongoDB is already current), so both DBs are compared at the same logical state. Benchmarks time the refresh separately from the write.
- Before a formal benchmark, reload both DBs from the pinned snapshot with `docker compose run --rm etl`, then settle and verify counts and equivalent data before timing queries. Removing admin-created sales alone does not restore deleted or modified snapshot sales. Run this on dedicated benchmark data: reloading also clears saved buildings while retaining buyer accounts.

## 7. ETL

1. `migrate` (Compose one-shot): initiates the Mongo replica set and waits for PRIMARY; as PG superuser creates or updates roles; applies SQL files as `neighboar_owner`; creates or updates Mongo users (`updateUser` on reruns), collections and validators. Idempotent; the same command runs in CI.
2. `download.py`: fetches the pinned snapshot **[Q4]**, verifies SHA-256 against `manifest.json`. `--live` downloads current data.gov.sg files and only reports drift.
3. `clean.py`: type parsing (month, storey band, 3 remaining-lease formats, Y/N, area, prices as strings with 2 dp), town map (27, TG named by hand), lookups, key normalisation, join on block + street, stub building for 82 MACPHERSON LANE **[Q3]**, lease year per block derived from the resale sales (when they disagree, the year consistent with the sales' own remaining lease wins: 99-year lease, tolerance 24 months because HDB's remaining-lease figures drift up to 18 months from a January start; exactly one candidate year must fit every valid sale date and remaining lease, otherwise cleaning stops for manual review; anomaly logged with its evidence; sales whose remaining lease disagrees with their own lease year are counted, 1 in the snapshot), skip zero supply pairs, keep the 318 duplicates. Output parquet; every rule logs counts.
4. `geocode.py`: OneMap search for residential plus resold blocks (~10.8k), rate-limited and resumable, stores OneMap's returned address and a match flag, manual override file. Run once where OneMap is reachable **[Q5]**; result committed; never called at runtime.
5. `synth_buyers.py`: fixed seed, 200 buyers, about 1,000 saves, argon2 hashes, one documented demo account.
6. `load_pg.py`: `COPY` in one transaction as owner, `setval`, `ANALYZE`, first MV refresh.
7. `load_mongo.py`: `insert_many(ordered=False)` in 10k batches, indexes after load, counters seeded.
8. `report.py`: `docs/import_report.md` with rows in, rejected and why, unmatched, duplicates kept. The report also shows the PostgreSQL and MongoDB counts after load next to the cleaning output, taken by `etl/db_counts.py` at the end of the pipeline (the Phase 1 gate compares them).

## 8. Diagrams (instructor's ERD note)

- ERD written as Graphviz DOT in `docs/diagrams/erd.dot` with crow's-foot cardinality and optionality drawn by hand, so M:N junctions read clearly. A test compares the DOT's table and column list to the live catalog, so it cannot drift silently.
- Rendered to SVG and PNG at 300 dpi (`dot -Gdpi=300`), checked at 16 cm width. Same for `architecture.dot` and a Mongo document-model diagram. `python -m docs.diagrams.build` regenerates all three locally (no CI check, since Graphviz versions differ in output).

## 9. Security

- argon2id (`argon2-cffi`), verified in the thread pool; unknown emails verified against a dummy hash (no user enumeration).
- Signed session cookie holding only `buyer_id`, `max_age` 8 h, `HttpOnly`, `SameSite=Lax`; app refuses to start without `SECRET_KEY`.
- CSRF token on every state-changing form, compared with `hmac.compare_digest`; logout is POST.
- Login throttle keyed on email plus IP (Docker Desktop collapses client IPs).
- Pydantic validation on all inputs; parameterised SQL only; sort columns from an allowlist mapped to `psycopg.sql.Identifier`; Mongo filters built only from typed values; any text search `re.escape`d.
- Least-privilege users in both DBs as in sections 4 and 5; admin routes need `is_admin` and use the admin pool. Because `web` holds both credential sets, the split demonstrates least privilege rather than isolating a compromised app; the report says so.
- `.env` gitignored, `.env.example` committed; containers non-root; `web` published on `127.0.0.1:8000` only; DB ports not published (a `compose.override.yaml.example` exposes them on localhost for host-side tools).

## 10. Compose and reproducibility

- Postgres volume `pgdata:/var/lib/postgresql` (PG 18 layout; `PGDATA` left unset). Healthcheck `pg_isready -h 127.0.0.1`.
- Mongo root via `MONGODB_INITDB_ROOT_USERNAME/PASSWORD` (this image's names, confirmed in the Phase 0 smoke test). Healthcheck `mongosh --quiet --eval "db.adminCommand('ping').ok"` if mongosh ships in the slim image, else a TCP probe plus driver retries. `start_period: 40s`; `web`, `migrate`, `etl` use `depends_on: condition: service_healthy`.
- Memory parity: WiredTiger cache and `shared_buffers` both set from one `.env` value (default 512 MB, 1 GB for benchmark runs), `mem_limit` on both; user manual documents `.wslconfig` for 8 GB laptops and has notes for markers on macOS or Linux.
- Scripts baked into images; only data is bind-mounted, read-only.
- Tests run inside the `web` image so the locked environment is the one tested.

## 11. Tests

Unit (parsers, hashing, validators), integration (each repo on the sample in both DBs), equivalence (one parametrised suite over every comparison query; `bench/verify.py` repeats it on full data before timing), negative (every CHECK, trigger, validator and role denial). CI brings up the real Compose stack (`up -d --wait`), runs `migrate`, loads the sample, then pytest inside `web`. Playwright smoke script kept local (stretch).

## 12. Benchmark

Driver-level timing including materialisation, plus server-side figures (`EXPLAIN ANALYZE` execution time, `executionStats.executionTimeMillis`). Per case: 3 parameter sets, 5 warm-up, 50 timed runs, median and p95; warm cache only, stated as such. Pairs compared like for like: live vs live, MV vs `building_profiles` stats, embedded vs `$lookup`, index vs no index, exact vs approximate median. Throughput at 1, 4 and 8 clients with `multiprocessing`. Data-volume scaling at 25, 50 and 100% of sales for the scalability discussion.

## 13. Phases, owners, dates, acceptance

Owners map to the README's five roles **[Q1]**: Data (D), PostgreSQL (P), MongoDB (M), Integration (I), Interface/testing/evaluation (E). One GitHub Issue per branch with acceptance criteria; every PR names a non-author reviewer; decisions logged in `PROJECT_PROGRESS.md`.

| Phase | Dates | Branches (owner) | Done when |
|---|---|---|---|
| 0 Foundations and spikes | Mon 19 to Fri 23 Oct | `app/compose-skeleton` (I), `data/download-manifest` (D), `data/geocode-cache` (D), `mongo/replset-spike` (M) | every member runs `docker compose up --build`; `migrate` creates all roles and users; `/health` connects as `app_rw` and `app`; CI green; geocode run started; replica-set decision made |
| 1 Data and schemas | Sat 24 Oct to Sun 1 Nov | `data/clean-etl` (D), `sql/initial-schema` (P), `mongo/initial-model` (M), `docs/erd` (P) | gate: full clean and PG load from scratch with counts matching the import report and all constraints active; Mongo load may trail by 2 days; ERD and architecture SVG + 300 dpi PNG |
| 2 Search, trends, building page | Mon 2 to Sun 8 Nov | `app/db-mode-switch` (I), `sql/search-trends` (P), `mongo/search-trends` (M), `app/ui-search` (E) | equivalence suite green on sample and full data; UI shows timings; report sections 1 to 2 drafted |
| 3 Accounts, CRUD, admin sales, food access, security | Mon 9 to Sun 15 Nov | `app/auth` (I), `sql/saved-admin-crud` (P), `mongo/saved-admin-crud` (M), `sql/food-access` (D), `mongo/food-access` (D), `app/ui-crud` (E) | full CRUD in both modes; trigger, MV refresh, `$merge`, rollback demos work; negative tests green; app uses least-privilege users only; DB implementation sections drafted |
| 4 Benchmark and hardening | Mon 16 to Wed 18 Nov (feature freeze Wed 18) | `app/bench` (E), fixes (all) | results and charts committed; backup and restore scripts work; fresh clone on a clean Windows machine passes the user manual |
| 5 Report, slides, video, submission | Thu 19 to Fri 27 Nov | `docs/user-manual` (D), `docs/report-assets` (E) | report complete by 22 Nov; slides locked 23 Nov in the mandated order (Background, SQL + demo, NoSQL + demo + comparison, performance) with names on every slide; every member recorded by 25 Nov; rough cut under 9:30 on 26 Nov; zip (report PDF, PPTX, MP4, source with data snapshot, geocode cache, user manual) built by script and submitted 27 Nov |

Stretch, only after Phase 3 is green: admin edit of a sale, small value score (price trend plus food access), Playwright in CI, keyset pagination.

Presenters per video segment are named at the start of Phase 5. The final report states the team's use of AI assistance.

## 14. Risks

| Risk | Mitigation |
|---|---|
| Docker Hub, MongoDB downloads, OneMap and data.gov.sg blocked from the build sandbox **[Q5]** | allowlist the hosts, otherwise those steps run on a team member's Windows machine from ready-made commands; CI covers Mongo tests |
| Replica set with auth needs a keyfile, awkward on Windows | keyfile baked in the image; verified on every teammate's Windows machine before Phase 1 closes |
| Geocoding misses | returned address stored, match flag, overrides, match rate in report |
| Parallel teammate work | owners per branch, small PRs |
| Equivalence false failures | contract in section 6 |
| Week 13 clashes with other modules | freeze 18 Nov, submit 27 Nov |
| Public repo **[Q6]** | make private at Gate 0 |

## 15. Deliberate changes from the progress report (to state in the final report)

Nine tables, not eight (`hawker_centre`, needed for the committed Haversine query); `lease_commence_year` moved from `resale_transaction` to `building` (3NF), chosen by the sales' remaining lease where they disagree (37 TEBAN GDNS RD: the one sale recorded as 1981, in Jan 2025 with 40 years 01 month remaining, fits 1966 (about 40 years left), not 1981 (about 55), so 1981 is a suspected source error inferred from remaining-lease consistency; dataset-wide, 1 sale has a remaining lease that disagrees with its own lease year, that same row); prices `NUMERIC(9,2)` because of fractional source values; stub building for 82 MACPHERSON LANE so all 241,357 sales load in both DBs (the README had planned to keep them separate); 318 identical rows kept as distinct sales; `source` provenance marker on sales (`snapshot` or `admin`, no default) to distinguish imported and admin-created sales; formal benchmarks require a full snapshot reload; Mongo single-node replica set (transactions, not HA); admin sale recording added so the trigger, MV refresh and stats recompute run at runtime.
