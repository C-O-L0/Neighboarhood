# Project progress

Decisions and next actions only (README rule). Newest first.

## 2026-10-30 UI overhaul, task T6 Home and Explore

- Home (`/`) is the guided start: one form, three numbered steps (flat type with its recent area and middle-80% price range, budget, optional place and radius), no script. The old block lookup is a side card. The "Not sure yet" option was dropped: every flat type already shows its size and price range.
- Explore (`/explore`): search chips, a headline (best town, or the closest towns when none fits, or the count near a place or in a town), town cards with the share of sales within budget, then blocks with explainer tags, lease left, distance and typical price, 20 per page, and "More filters" (town, lease left, floor area, radius).
- A typed place: one match is used, several are offered as "Which block did you mean?" (first 8), none returns to the form with a message. An unlocated place is reported and the results are not limited by distance; nothing within the radius offers the next radius.
- New filters: `money_short` ($620k, $1.23M), `flat_label`, `flat_adj`, `proper` (block numbers keep their case), `distance` (m below 1 km). `PlaceRepo.get` added in both databases.
- The block lookup's 422 now shows the home page, which loads its flat type ranges; the lookup itself still never runs on bad input (test narrowed to that).
- Full suite on the sample: 1609 passed, 0 skipped.

## 2026-10-30 UI overhaul, task T5 blocks that fit

- `blocks_fit(BlockFilter)` in both databases: blocks whose typical (lower median) price for the flat type over the recent window is at or under the budget, with optional town, place and radius, least lease left and least typical floor area. Pages of 20 with the total. Without a place, cheapest first; with a place, located blocks within the radius (the place itself included), nearest first; ties by building id.
- PostgreSQL returns the total and the page from one statement: the count row always exists and the page is `LEFT JOIN LATERAL`ed to it, so a page past the end still knows the total.
- MongoDB has two shapes. Without a place it groups the window's sales by block (type and month index, or town, type and month with a town). With a place it starts from `$geoNear` on `building_profiles` and `$lookup`s each block's sales by `building_id` (`building_month` index, no collection scan). `$facet` gives the total and the page.
- "Lease left" is whole years in the window's last year (`lease_start + 99 - year`); only the lease start year is known.
- Each row carries the block's typical storey band, floor area and lease start, which T3's tags compare with the town baseline.
- Full suite on the sample: 1584 passed, 0 skipped.

## 2026-10-30 UI overhaul, task T4 places and street abbreviations

- Street abbreviations: every stored street is in HDB's abbreviated form (checked against all 632 streets in `data/cache/block_coordinates.csv`), so the typed text is abbreviated before it reaches either database (`etl.streets.abbreviate_street`, the reverse of `ABBREVIATIONS`; "SAINT" becomes "ST."). Used by search, the home block lookup and the new place picker. "marine terrace" now finds MARINE TER. Not handled: punctuation ("st georges" still misses ST. GEORGE'S RD).
- New `places` feature in both databases: `find(block, street, limit)` for the picker (street contains the text, optional exact block, ordered by street then block number with 9 before 10) and `near(building_id, radius_m)` (located, not non-residential blocks within 1, 2 or 5 km, nearest first; None for an unknown or unlocated block). `app/services/places.py` reads "Blk 186 Bishan Street 13" as block and street.
- Distances use the food-access method in both: Haversine with R = 6378100 m in PostgreSQL and `$geoNear` in MongoDB, compared within 1 m. MongoDB uses its location 2dsphere index; PostgreSQL has no spatial index and computes the distance for every located building. That is fast at 13,358 buildings and is a benchmark case for T17.
- Full suite on the sample: 1565 passed, 0 skipped.

## 2026-10-30 UI overhaul, task T3 explainer baselines

- `baselines(flat_type, window)` in both databases: per town, the lower median storey band (as `storey_min + storey_max`, kept whole), lease start year and floor area of the window's sales of the flat type. Same `(flat_type, txn_month)` index as T2.
- Tag rules live in `app/services/explain.py`, in integer arithmetic: "Low/High floor" a third or more from the typical middle storey, "Shorter/Longer lease" 10 or more years apart, "Smaller/Larger than usual" 10% or more apart. No tags means typical for the town; no comparison at all when the town has fewer than 5 sales.
- Block age and lease are one factor, not two as USE_CASES R8 listed: the lease left at a sale is 99 years minus the block's age, so both tags would always appear together.
- Baselines are computed per request, not stored: one indexed aggregate per flat type, and nothing to keep current after admin writes.
- Full suite on the sample: 1520 passed, 0 skipped.

## 2026-10-30 UI overhaul, task T2 towns that fit

- New `explore` feature in both databases: `latest_month`, `flat_type_ranges` (count, typical floor area, 10th and 90th percentile price per resale flat type) and `towns_fit` (per town: count, typical price, sales at or under the budget; cheapest first). Every query filters on `(flat_type, txn_month)` and uses that index in both databases; the latest month is one index probe per flat type (PostgreSQL `LATERAL` backward scan, MongoDB `DISTINCT_SCAN`).
- "Recent" means the 12 registration months ending at the latest month in the data. `app/services/explore.py` works out that window once and passes both dates to each database. Percentiles in MongoDB pick the element PostgreSQL's `percentile_disc` picks, position `ceil(p * n)` with `p` a double.
- The footer shows "Sales registered up to <month>" from the current database; the lookup runs for GET pages only and is not timed, like the account lookup.
- Rules for the pages (T6): "few sales" is fewer than 5; the best fit is the fitting town with the highest share of sales within budget (ties: lower typical price, then name); with no fit, the 3 towns least over budget. Budgets accept commas and a dollar sign.
- Full suite on the sample: 1498 passed, 0 skipped.

## 2026-10-30 UI overhaul, task T1 page shell

- The UI is being rebuilt around buyer use cases (find where we can afford, check a price, judge a block, shortlist and compare as a household); the task list runs T1 to T18. T1 is the shared shell.
- Demo mode replaces the always-visible database switch: the footer toggle (`POST /demo`) sets a `demo` cookie, and only then does the demo bar show the PostgreSQL/MongoDB switch, the page timings and the health link. Without demo mode the `db_mode` cookie is ignored, so buyers always use PostgreSQL; `?db=` in a link still selects the database. Writes stay per database (no dual writes).
- "Typical price" replaces "median*" and its footnote on every page and chart, with a glossary link; `/glossary` defines it as the lower middle price. `docs/conventions.md` sections 6, 11, 12 and the new 14 record the rules.
- Styles are rebuilt on tokens with the self-hosted Figtree font (OFL, hash checked). `money` drops ".00" from whole amounts.
- `ruff format` is now applied to the whole tree and checked in CI. `httpx2` is installed for Starlette's test client, which removes its deprecation warning.
- Full suite on the sample: 1461 passed, 0 skipped.

## 2026-10-29 Phase 4 gate passed

- Phase 4 merged as 4.1a (PR #27), 4.1b (PR #28), 4.2 (PR #29), 4.3 (PR #30) and 4.4 (PR #31). The checks ran on main df34822.
- Results and charts: `bench/results/2026-11-full/` holds f100, f50, f25, `scaling.csv`, `RESULTS.md` and the four charts. `bench.charts`, run on a copy of the four CSVs alone, produced all eight files byte-identical to the committed blobs. On a Windows checkout with `core.autocrlf=true` the working-tree SVGs are CRLF, so compare against `git show HEAD:<path>`, not the checked-out file.
- Backup and restore: `scripts/check_backup_roundtrip.sh` on the sample (6,275 sales) exited 0. The fingerprint changed after the mutation and was equal after the restore. A changed byte in `postgres.dump` and a changed `db/postgres/*.sql` hash were both refused with nothing changed, and the script removed its three backup folders. The full suite afterwards gave 1432 passed. The `dbtools` image reports `pg_dump` and `pg_restore` 18.6 and `mongodump` and `mongorestore` 100.20.0. A build with a wrong checksum failing, and no secret in process arguments or `manifest.json`, were shown during 4.3.
- Fresh clone: the 4.4 test on the development machine passed (sections 2 to 12 as written, full-data counts matching, 1424 passed and 8 skipped in the fresh stack, working stack image ids and volumes unchanged). Not done: the run on a clean Windows machine that BUILD_PLAN section 13 asks for. It stays under "Blocked, needs Ahsanul".
- Working stack: the full clean and load from the pinned snapshot took 36 s, and every count in "Database counts" of `docs/import_report.md` matches (PostgreSQL 275,471 rows, MongoDB 254,844 documents, 200 buyers and 1,002 saved blocks in each). The full suite the CI way on the full data gave 1424 passed and 8 skipped. The skips are sample-only facts: two food, three rankings, two rankings page, and `test_pipeline_fraction.py`. ruff 0.17.1 is clean, and CI is green on main.
- Least privilege and section 9: the privilege grid, the route identity spy and the section 9 file checks are in that suite and passed. In CI on main, PID 1 runs as 999 (`postgres`), 1000 (`mongodb`) and 10001 (`web`).
- Deferred: the clean-Windows-machine run; the untested macOS, Linux and arm64 `dbtools` paths (including the likely Linux permission problem writing `data/interim/` as UID 10001) and manual sections 1 and 13, which were not exercised; the report, slides, demo script, finished manual and submission zip (Phase 5; the zip must exclude `backups/`); and the follow-up in the 4.4 entry on passwords in the `Settings` repr.
- Next: Phase 5 drafts, on Ahsanul's go.

## 2026-10-29 Task 4.4 user manual draft and fresh-clone test

- `docs/user_manual.md` is the draft (Phase 5 completes it with screenshots). Every command is for Git Bash and needs no host Python. It starts the stack with `docker compose up -d --build --wait web` rather than the foreground `docker compose up --build`, so the same terminal carries on. The snapshot is verified with `docker compose run --rm etl python -m etl.download` (no options checks `data/raw` against the manifest); `--from` must name a folder inside `data/`, as the `etl` service mounts only `./data`.
- `compose.yaml` tags the app, mongo and dbtools images `${NEIGHBOAR_IMAGE_TAG:-local}`, and `bench/run.sh` follows the same variable, so a second checkout can build without replacing the working images. The default names are unchanged.
- Fresh-clone test on the development machine (not the clean Windows machine section 13 asks for, which stays with the team): working stack stopped with its volumes kept; `git clone` of this branch from GitHub into a scratch folder; `COMPOSE_PROJECT_NAME=neighboar-fresh`, `NEIGHBOAR_IMAGE_TAG=fresh`, images built with `--no-cache`. Sections 2 to 12 ran as written: `.env` with 12 values from the manual's Docker command; health page with both databases up; the snapshot files copied from the working `data/raw` as the unzip step, three `OK`; full load in 37 s with every count in "Database counts" of `docs/import_report.md` matching; every page 200 in both modes, signed-out `/admin` 403 and `/saved` 303, the buyer signed in with `/admin` 403, the admin with `/admin` 200; backup and restore on the full data (fingerprint matched); the short benchmark (verify OK, 1,517 checks); stop and restart. Then `down -v` of the `neighboar-fresh` project only and `docker image rm` of the three `:fresh` tags. The working stack's image ids and volumes were the same before and after.
- The first full-data run of the suite in the fresh stack gave 1420 passed, 8 skipped, 4 failed. Three `tests/integration/test_bench.py` tests assumed the sample (fraction below 1); they now take an expected fraction that is wrong for whatever is loaded (`wrong_fraction`), so they hold on the sample, the fractions and the full data. `test_case_equivalent[accounts]` failed because manual step 8 signs in before step 9: a sign-in in PostgreSQL mode sets `last_active_at` in PostgreSQL only, by design of per-mode writes. The manual now reloads the data as the first command of section 9. After both fixes the suite in the fresh stack gave 1424 passed and 8 skipped, run as section 9 now reads. The skips are facts that hold only on the sample (two food, three rankings, two rankings page) and `test_pipeline_fraction.py`.
- `tests/integration/test_pipeline_fraction.py` skips, before touching any data, when the loaded snapshot sales are 0.1 or more of 241,357. On the full data it used to fail and its `finally` replaced the full data with the sample. The bench tests' `sample` fixture now also runs the bench read and write cases on the full data inside the suite, which makes a full-data run longer.
- Not exercised: section 1 (Docker memory and `.wslconfig`; the machine was already set up), section 13 (troubleshooting; none of the conditions occurred) and section 14 (macOS and Linux, no machine). The Linux notes say they are untested, including a likely permission problem writing `data/interim/` as UID 10001, and the arm64 `dbtools` build is marked untested.
- Follow-up, not fixed here: a failing test that prints the app `Settings` object shows the demo passwords in clear in the pytest output (seen in the fresh stack's local log, which used throwaway values and was deleted). CI generates throwaway secrets, so its logs leak nothing that matters, but a failing run on a team member's machine would print their real demo passwords to the terminal; the password fields should be excluded from the repr.

## 2026-10-28 Task 4.3 backup and restore

- Scripts: `scripts/backup.sh` and `scripts/restore.sh` (POSIX `sh`, run from Git Bash on Windows), and `scripts/check_backup_roundtrip.sh`, which backs up, changes both databases (`db/roundtrip_mutate.py`: a saved block deleted, an admin sale recorded in both, one profile's stats altered), restores, compares the fingerprint, and checks both refusals (one changed byte in a dump file, a different `db/postgres/*.sql` set). It deletes only the backup folders it made; `--keep` leaves them and says where.
- Tools image: new service `dbtools` (profile `tools`, `db/tools/Dockerfile`) on `postgres:18.6-bookworm` with MongoDB Database Tools 100.20.0 (the latest on `fastdl.mongodb.org`). The SHA-256 of each archive is in the Dockerfile and checked during the build; a wrong value fails the build. The image ends as `postgres` and the service runs as the host user (`HOST_UID`, `HOST_GID`).
- Deviation from the spec: MongoDB publishes no Debian 12 build for arm64 (every Debian archive is x86_64 only), so arm64 uses the Ubuntu 22.04 arm64 build. The Go binaries need glibc 2.35 or older and Debian 12 has 2.36. The arm64 path was not run, as the machine is x86_64; the pinned checksum was verified against the downloaded file.
- Secrets: PostgreSQL gets `PGPASSWORD` from the container environment. MongoDB gets the password from a `--config` YAML written inside the container at mode 600 and removed by `trap` on any exit; the user name, host and database are on the command line, as they are not secrets. The host scripts never read `.env`. `ps` inside the containers during a backup and a restore showed no secret.
- `db.state` (run in the `etl` service as the owner and the ETL user) is the state fingerprint. It reports a NULL ACL as the default ACL of the object, because a restore turns the explicit owner-only ACL of `schema_migration` back into NULL (same meaning). It compares constraints by type and validity, not by deparsed text, because `pg_dump` rewrites a CHECK on a `varchar` column as `ANY (ARRAY[...])`. Sequence positions are part of the fingerprint. Both normalisations were needed after the first round trip showed those two false differences.
- `restore.sh` verifies checksums and the schema file set before any change, stops `web` only after the confirmation, and starts it only after the fingerprint matches; on failure `web` stays stopped and the script says so. `backup.sh` refuses while `web` runs unless `--stop-web`, and removes a half-written backup folder.
- The Phase 5 submission zip script must exclude `backups/`: a backup is a full copy of both databases, with account hashes.
- Verified on the sample before the PR: `scripts/check_backup_roundtrip.sh` exited 0 (fingerprint changed by the mutation, equal after the restore, both refusals fired and left the fingerprint unchanged); a build with a wrong `TOOLS_SHA256_AMD64` failed at `sha256sum --check` and produced no image; the image prints `pg_dump` 18.6 and `mongodump` 100.20.0; no password or secret key appeared in 2,110 sampled process command lines across the containers during the run, in the script output or in `manifest.json`; the full suite the CI way gave 1432 passed; ruff 0.17.1 is clean.
- CI is unchanged: `db.state` is covered by `tests/integration/test_db_state.py` in the existing full-suite step; the round trip needs Docker on the host and stays a manual check (orchestrator repeats it on the sample).

## 2026-10-28 Task 4.2 formal benchmark

- The formal run followed `bench/README.md` steps 0 to 5 with `web` stopped, at commit 09cbc0e: `f100/env.json` shows `shared_buffers` 1GB, WiredTiger cache 1 GB (read from the running container), 241,357 sales in both databases, fraction 1, `bench.verify` ok (1,517 checks) for reads, writes and throughput, and four invocations. `f50` and `f25` hold the 120,678 and 60,339 sale loads (the real fractions, 0.499998 and 0.249999, are in each `env.json`). Every read case has 3 sets x 50 runs per database where it runs; `admin_record_delete` and `stats_rebuild` the same.
- Results and the four charts (`latency_by_case`, `throughput`, `scaling`, `index_on_off`, PNG and SVG) are in `bench/results/2026-11-full/`; `RESULTS.md` is generated by a local script from the CSVs and `env.json`, so it has no hand-typed numbers. The charts regenerate byte-identically from the four CSVs alone. No change to `bench/charts.py` was needed.
- Headline facts the report depends on, as chart values (median of the three set medians): MongoDB is lower in `search_filtered` (1.81 ms against PostgreSQL 5.05 ms, helped by its extra `{town, flat_type, txn_month}` index), `search_price_sort`, `trends_series`, `rankings_year` and `building_page`; PostgreSQL is lower in `town_stats_read`, `stats_rebuild` (165.01 ms against 2,355.57 ms, different grain), `food_town` (51.24 ms against 296.42 ms) and `median_exact`; `search_type_month` and `search_unfiltered` are close. Throughput at 8 clients: MongoDB 2,480.9 calls/s against 922.1 for `search_filtered`, and 14,802.9 against 5,862.6 for `building_page`. In `admin_record_delete` the PostgreSQL write is 5.11 ms and its separate refresh 164.42 ms; MongoDB's write (which includes the stats recomputation) is 11.57 ms.
- Index off: every case is slower in both databases. Without the `(flat_type, month)` index MongoDB falls back to `type_price` in all three sets, PostgreSQL does so only in part for `search_type_month`: set `4room-2022-q1` names no non-key index in either statement, `5room-2023-h2` names the price index in the count statement only (its page statement names none), and `3room-2017-h1` names it in both. The plan tables in `RESULTS.md` section 4 give every set.
- Deviations: none from the procedure. The MongoDB server time is whole milliseconds and 0 for the sub-millisecond cases, as the README notes. `RESULTS.md` states warm cache only, one machine on Docker Desktop for Windows, the extra Mongo index, the unequal read identities and the grain of `town_stats_read` and `stats_rebuild`.

## 2026-10-28 Task 4.2 benchmark tooling

- `bench/README.md` holds the formal procedure (steps 1 to 6) as Git Bash commands. The label allowlist now accepts one optional slash (`2026-11-full/f100`, each part starting with a letter or digit), in both `bench.run` and `bench.explain`; the results folder is created with parents. `.gitignore` excludes `bench/results/**/tmp/` so the scratch rule also covers the nested labels.
- `bench.scaling` takes the fraction from each folder's `env.json` (rounded to two decimals), never from the folder name, requires exactly three different fractions and rebuilds `scaling.csv` (summary columns plus `fraction` and `source`).
- `bench.charts` reads only the committed CSVs. Per case and database it plots the median of the three parameter sets' medians, with the largest p95 as the whisker; the `refresh` half of `admin_record_delete` is left out; `scaling.png` shows only cases present at all three fractions. The log axis is used only above a 100x range and then says "log scale". One legend per chart sits below the plots, so it never covers a bar or a label. Line charts (throughput, scaling) carry no value labels, because the labelling rule is for bars; MongoDB lines are dashed with square markers so they also read apart in greyscale.
- The `bench` service runs as `HOST_UID`/`HOST_GID` (default 1000), so `bench.scaling` and `bench.charts` are run after `export HOST_UID=$(id -u) HOST_GID=$(id -g)`; otherwise the container cannot write into the results folder created by `run.sh`.
- `matplotlib` 3.11.2 was added to `requirements.in` and compiled into `requirements.lock` with the lock's own command (`uv pip compile --python-version 3.12 --python-platform linux`), run inside `neighboar-app:local` with uv installed there; existing pins did not change.
- `bench.explain` writes no `fraction` to `env.json`; the read invocation of the same label does, so run the explain step after the reads, as the procedure does.

## 2026-10-28 Task 4.1b index on and off, and fractions

- `bench/explain.py` runs `search_price_sort`, `search_type_month` and `median_exact` with and without their index, one method for both arms, each database compared only with itself. Run it with `sh bench/run.sh explain --label NAME` (the wrapper now picks the module from a leading `explain`). It writes `index_on_off.csv` (a plan signature and an `index_in_plan` flag per arm, matching the index name as a whole token so `type_month` is not `town_type_month`) and `explain/<case>/{pg,mongo}-index-{on,off}-<param set>.{json,txt}`, and adds an `index_on_off` entry to `env.json`.
- PostgreSQL arms run as `neighboar_owner`, MongoDB arms as `etl`; the bench service already held both passwords. Each PG arm is one transaction that always ends in a rollback: optional `DROP INDEX`, warm-up and timed runs of the captured SELECTs (the last statement's row count is recorded), then `EXPLAIN (ANALYZE, BUFFERS)` of each. Both arms of a case cover all three parameter sets, so an index is dropped once per case and database. `capture.explain_pg` gained `in_transaction=True` for this, because its own read-only wrapper cannot run after queries in an open transaction.
- Before and after every PG arm the `pg_get_indexdef` of every public index is compared; MongoDB compares `index_information()` of the collection with the capture and recreates the dropped index from the captured definition in a `finally`. A mismatch raises. The hidden `--force-failure` option raises in the timed step of every off arm; `main` then exits 1 and the indexes are as they were.
- Dropping the `(flat_type_id, txn_month)` index does not leave PostgreSQL without an index: the planner falls back to the `(flat_type_id, resale_price, txn_id)` index for `search_type_month` and `median_exact`, and MongoDB does the same with `type_price`. The plan still differs and the dropped index is absent from it, which is what the check asserts; `RESULTS.md` should say the off arm still has a neighbouring index. For `search_price_sort` PostgreSQL's page statement turns into a sort over `type_month`-driven rows, MongoDB's into `SORT` over `type_month`.
- `etl.pipeline --fraction 0.25|0.5` cleans the full snapshot, keeps the sales with `txn_id % 4 == 0` or `% 2 == 0`, and always writes to a fresh temporary folder (removed afterwards), also for the full snapshot, so `data/interim` is never written. The copy of `clean_report.json` gets the filtered `rows_out.resale_transaction` and a `fraction` key; `db_counts` reads that copy and writes its json there. Cleaning, the buyers, the saved blocks, stats and the MV are untouched. Checked on the host: the full snapshot with `--fraction 0.25` (no `--sample`, `./data` mounted) exited 0 with 60,339 sales in both databases and equal counts, and the `sha256sum` of every file in `data/interim` (10 files) and of `docs/import_report.md` was the same before and after; the sample was then reloaded (6,275 sales, 200 buyers, 948 saved blocks). A unit test covers the same path with the steps stubbed: the full-snapshot run uses a temporary folder under the system temp directory, never `data/interim`, removes it afterwards and also when a load raises.
- `bench.run` still divides by the 241,357 in the import report, so on the sample a fraction load never reads as 0.25. The test passes the computed value (kept sales over 241,357), shows the default (1) and the full sample's own value both refuse, and the real `--expect-fraction 0.25` is for the full snapshot in 4.2.
- The fraction test runs the pipeline as a subprocess of the web test container, which already holds the owner, ETL and demo passwords in CI, and reloads the full sample in a `finally`. The `--sample` path already used a temporary folder before this task, so the integration test says nothing about `data/interim`; the unit test and the host check above are what cover it. The image has no `data/interim`.

## 2026-10-28 Task 4.1a benchmark harness

- The harness lives in `bench/`: `run.py` (checks, timing, results), `verify.py`, `capture.py`, `stats.py`, `connect.py`, `cases.yaml` and `run.sh`, plus three small modules the spec did not list: `cases.py` (the typed YAML loader), `calls.py` (the repo wrappers and the bench-only statements) and `throughput.py` (the spawn workers). `Case` in `tests/equivalence/cases.py` has a `writes` flag, false for every registered case, and `bench.verify` skips any case that sets it.
- Test-only hooks are hidden command-line options: `--force-mismatch` (verify compares one result against nothing), `--throughput-seconds` and `--throughput-warmup` (short throughput), and `--results-root` (tests write to a temporary folder, never `bench/results`). A run made with a hook records it under `test_hooks` in its `env.json` entry.
- Timed reads run as PG `app_ro` and Mongo `app`; the runner reads `current_user` and `connectionStatus` on the timed connections themselves and refuses to time as anyone else. `app` has no `listIndexes` or `createIndex`, so the index check and the idempotent `create_indexes(INDEXES)` use the ETL Mongo user; write cases use PG `app_admin` and Mongo `app_admin`. The bench service has no `app_rw` credential.
- Capture, then warm-up, then timed runs, then EXPLAIN. The capture call goes through the recorders and is untimed; the warm-up and timed calls use bare connections (each raw timing records its connection type); the EXPLAINs run last on a second `app_ro` connection, each SELECT inside a read-only transaction that is rolled back. MongoDB `executionTimeMillis` has 1 ms resolution, which `README.txt` in each results folder says next to the server-time rule.
- The summary has a `part` column: `call` for reads, and `write` and `refresh` for an admin write. One PG run is record plus delete (the two spans summed as `write`) and then one `refresh_town_stats()` (`refresh`); MongoDB has no refresh. The median is the usual one (mean of the two middle timings when n is even), unlike the lower median used for prices; p95 is nearest rank.
- `stats_rebuild` takes no parameters, so its three sets are repetitions (`rep-1` to `rep-3`) with the default 50 runs and 5 warm-up. Every case has exactly three sets and the loader refuses anything else. Sets in BISHAN, MARINE PARADE and BUKIT TIMAH also return rows on the sample; the TAMPINES sets return nothing there, and the sample runs accept that.
- Each `env.json` invocation entry has `verify: {ok, checks}`. A writes run repairs an interrupted rebuild by comparing the profiles with `stats.txn_count > 0` against the distinct `building_id` count in `resale_transactions` (`rebuild_all_stats` first resets every profile to empty stats, so a missing `stats` field is not the failure mode).
- A writes run records the state first (db_counts, the three affected building pages in both databases, every `mv_town_month_stats` row), settles afterwards and fails if any of it changed. It also puts the MongoDB `txn` counter back, because `test_database` compares it with the largest loaded id. The PostgreSQL sequence cannot be put back and is not.
- The fraction is PG snapshot sales over the `Sales: N read` figure in `docs/import_report.md`, so admin sales do not move it. The sample is 0.026. `--expect-fraction` defaults to 1 and the label plays no part.
- `run.sh` also passes `BENCH_MEM_LIMITS` (the `mem_limit` of the postgres and mongodb containers from `docker inspect`) beyond the four host facts the spec names; `BENCH_IMAGES` is a JSON object of the three image ids. Tests read the PG `shared_buffers` from the server and the Mongo cache from `BENCH_MONGO_CACHE`, and the value is `null` when `run.sh` did not set it.
- The bench integration tests need `PG_APP_RO_PASSWORD` and `MONGO_ETL_PASSWORD`, which the CI test step and the README command already pass; the web service supplies the admin and app passwords. No CI or README change was needed, and the tests skip cleanly if one of the five passwords is missing.

## 2026-10-28 Phase 3 gate passed

- On main 4da5671, the full clean and load of both databases took 30 s. Every count in "Database counts" matches, now including 200 buyers and 1,002 saved blocks in each database. `docs/import_report.md` was regenerated with `python -m etl.report`; only the buyer and saved-block rows and the timestamp changed. The `etl` service mounts only `./data`, so the report is regenerated with `./docs` mounted as well (`docker compose run --rm -v ./docs:/srv/neighboar/docs etl python -m etl.report`).
- The whole suite gives 1273 passed and 7 skipped on the full data. The skips are facts that only hold on the sample (two food cases with sample building ids, three rankings cases, two rankings page cases). This includes full CRUD for saved blocks and admin sales in both modes, trigger (a), the MV refresh and stale flag, the `$merge` rebuild, all rollback demos, the integrity matrix and every negative test, the privilege grids and the route identity spy.
- Food access on the full data: over all 10,797 geocoded blocks and 1,328,031 block and open-centre pairs, the largest difference between the PostgreSQL Haversine and MongoDB `$geoNear` is 6.7e-10 m (building 5293, centre 119473), so the 1 m tolerance holds with a wide margin.
- CI shows PID 1 runs as UID 999 (`postgres`), 1000 (`mongodb`) and 10001 (`web`). An HTTP smoke check of every page in both modes gave the expected codes (200 for pages, 422 for an unknown town, 403 for a signed-out `/admin`, 303 to sign-in for `/saved`).
- Deferred to Phase 5: BUILD_PLAN section 13 asks for the database implementation sections at this gate, but the report is drafted in one pass in Phase 5 (as at the Phase 2 gate). Stretch items are not started; the orchestrator prompt allows them only on Ahsanul's go at this checkpoint, and the run instructions go straight to Phase 4.
- Next: Phase 4 (4.1a bench harness, 4.1b index on and off and fractions, 4.2 formal benchmark, 4.3 backup and restore, 4.4 user manual draft and fresh-clone test on the development machine). The Phase 4 gate: results and charts committed, backup and restore scripts work, and the user manual passes a fresh clone.

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

- No GitHub Release `snapshot-2026-09` exists, so `python -m etl.download --release` (the manifest's default tag) cannot fetch the snapshot; only `--from DIR` works. Publishing the three source files as a release would put them on the repository, which is still public (next point). Decide whether to create the release; until then the user manual and the fresh-clone test copy the three files from the submission zip into `data/raw/` and verify them with `python -m etl.download` (no options); `--from DIR` is the alternative.
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
