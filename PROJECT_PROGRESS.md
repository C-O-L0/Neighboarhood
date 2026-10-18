# Project progress

Decisions and next actions only (README rule). Newest first.

## 2026-10-18 Gate 0 decisions

- Scope follows the submitted progress report. Deviations are listed in docs/BUILD_PLAN.md section 15 and will be stated in the final report.
- `lease_commence_year` moves from `resale_transaction` to `building` (3NF: one value per block).
- 82 MACPHERSON LANE (3 sales, no property row) loads as a stub building so all 241,357 sales are in both DBs.
- Data snapshot: the three files downloaded on 26 Sep 2026 are the pinned snapshot (SHA-256 in etl/manifest.json).
- Image pins kept: postgres:18.6-bookworm, mongodb/mongodb-community-server:8.3.11-ubi8-slim, python:3.12.15-slim-bookworm.
- Repository to be made private by its owner.
- Open: `hawker_centre` as a ninth PostgreSQL table for the Haversine query (needed before Phase 1 schema work).

## Phase 0 spike results (2026-10-18)

- The Mongo image accepts `MONGODB_INITDB_ROOT_*` (and the older `MONGO_INITDB_*`), ships `mongosh`, runs as uid 1000 `mongod`, and its entrypoint already adds `--bind_ip_all`.
- Single-node replica set with a build-time keyfile works with auth; root user is created under `--replSet`; multi-document transactions commit and abort correctly.
- `$geoNear` inside a `$lookup` sub-pipeline with `near: "$$loc"` works on 8.3.11.
- `$median` and `$percentile` accept only `method: "approximate"` on 8.3.11, so the exact median is computed with `$sortArray`.
