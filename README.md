# HDB Neighbourhood Explorer - Team Brief

![Neighboar](./images/Neighboar.png)

## What we are building

An HDB neighbourhood explorer for prospective buyers. It uses historical HDB resale transactions, HDB building information, and hawker-centre locations to let a user compare towns and individual buildings by flat type, time period, floor area, past resale prices, and food-amenity access.

Registered users can save individual buildings, add comparison notes, and revisit their saved list. Historical transactions are evidence of previous sales, not current property listings.

The first demonstrable query is: "For a selected flat type and period, how do historical resale prices compare across towns?"

## Why we are building it

This is an INF2003 database application, not a custom database engine. The project demonstrates how the same domain can be designed and queried with a relational DBMS and a document DBMS.

It gives us credible real-world data, meaningful CRUD through accounts and saved buildings, advanced aggregation queries, and a fair performance comparison using equivalent results and the same source data.

## Chosen technology

| Layer | Choice | Purpose |
| --- | --- | --- |
| Backend and GUI | Python 3.12, FastAPI, Jinja2 templates, vanilla JavaScript and CSS | A compact server-rendered web application with a graphical browser interface. It lets us keep database queries visible for learning. |
| Relational DBMS | PostgreSQL 18.6 | Tables, foreign keys, constraints, joins, views, indexes, and SQL analytics. |
| Document DBMS | MongoDB Community Server 8.3.11 | Document modelling, embedding, indexing, and aggregation pipelines. |
| Database clients | psycopg 3 and PyMongo | Direct Python access to PostgreSQL and MongoDB. |
| Runtime | Docker Compose | One reproducible local stack for the app and both databases. |
| Testing | pytest plus browser-level checks for key user flows | Verify imports, queries, CRUD, and user-visible behaviour. |
| Collaboration | GitHub repository, Issues, pull requests, and a shared project board | Version control, task ownership, review, and traceable decisions. |

Pinning the PostgreSQL and MongoDB container image versions avoids every teammate receiving a different database release. PostgreSQL 18.6 is the current stable supported release and `postgres:18.6-bookworm` is available as an official image. MongoDB's verified Community Server image provides `8.3.11-ubi8-slim`. [PostgreSQL documentation](https://www.postgresql.org/docs/18/), [PostgreSQL image tags](https://hub.docker.com/_/postgres/tags), [MongoDB image tags](https://hub.docker.com/r/mongodb/mongodb-community-server/tags)

## How we will build it

1. **Data pipeline** - download the official resale and property CSVs plus hawker-location data, retain a dated source manifest, clean values, map HDB property town codes to resale town names, and report unmatched addresses.
2. **PostgreSQL design** - the progress report committed eight tables in 3NF: `town`, `building`, `flat_type`, `flat_model`, `building_flat_supply`, `resale_transaction`, `buyer` and `saved_building`. The implementation has nine: it adds `hawker_centre`, needed for the Haversine food-access query. Town to building and building to transaction are 1:M, transaction to flat type and flat model are M:1, and `building_flat_supply` and `saved_building` resolve two M:N relationships.
3. **MongoDB design** (as committed in the progress report) - `building_profiles` (block details, recent sales and price statistics embedded for single-read pages), `resale_transactions` (full history), `buyers` (saved blocks embedded) and `hawker_centres` (GeoJSON with a 2dsphere index).
4. **Shared features** - implement equivalent search, filtering, aggregation, building detail, and saved-building operations. The UI can let reviewers select the PostgreSQL or MongoDB implementation for supported comparison queries.
5. **Advanced work** - add indexed trend/ranking queries, explain query plans, test data constraints, measure equivalent query latency and throughput, and report tradeoffs.

Proximity method: every residential block is geocoded once through OneMap (`etl/geocode.py`) and the result is committed in `data/cache/block_coordinates.csv`. The app never calls an external API at run time. Food access uses Haversine distance in PostgreSQL and `$geoNear` in MongoDB.

The detailed build plan, schemas, equivalence rules and timeline are in [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md).

The rules every Phase 2 feature follows (repository layout, DTO types, DB-mode switch, timing, equivalence tests, formatting filters) are in [`docs/conventions.md`](docs/conventions.md).

## Quick start

Needs Docker Desktop (Windows or macOS) or Docker Engine with the Compose plugin (Linux).

```bash
cp .env.example .env        # then replace every change-me value (Windows: copy .env.example .env)
docker compose up --build   # first run pulls images and builds, a few minutes
```

Open http://localhost:8000/health. Both databases should show as up, connected as `app_rw` (PostgreSQL) and `app` (MongoDB, replica set `rs0`).

## Docker Compose workflow

`compose.yaml` runs four services:

```text
postgres  PostgreSQL 18.6, named volume pgdata
mongodb   MongoDB 8.3.11 as a single-node replica set (needed for transactions), named volume mongodata
migrate   one-shot: initiates the replica set, creates least-privilege roles and users, applies schema files, then exits
web       FastAPI application at http://localhost:8000 (bound to localhost only), starts after migrate succeeds
etl       on demand only (profile "tools"): verifies the snapshot, cleans it, loads both databases
```

Compose gives the services a private network, lets the application use service names such as `postgres` and `mongodb`, and starts the full stack with one command:

```bash
docker compose up --build
```

### Loading the data

The databases start empty. Load them once (and again after `docker compose down -v`):

```bash
# Full snapshot: put the three source files in data/raw first
python -m etl.download --from <folder with the 26 Sep 2026 files>
docker compose run --rm etl

# Or the committed ~6k-sale sample (no download needed; this is what CI loads)
docker compose run --rm etl python -m etl.pipeline --sample
```

The full load takes about a minute: PostgreSQL gets 275,471 rows, MongoDB 254,844 documents, both from the same cleaned files in `data/interim/`. `python -m etl.pipeline` ends by counting rows and documents in both databases (`etl/db_counts.py`) and `docs/import_report.md` shows those counts next to the cleaning output (rows accepted by cleaning). Schema files are in `db/postgres/`, MongoDB validators in `db/mongo/validators/`, column definitions in `docs/data_dictionary.md`, diagrams in `docs/diagrams/`.

Every load also writes 200 synthetic buyers (ids 1 to 200) and about 1,000 saved blocks to both databases (`etl/synth_buyers.py`, fixed seed, so the same data every time). Add two lines to your `.env` first, each a password of your own choosing of 10 to 128 characters that does not start with `change-me`:

```text
DEMO_BUYER_PASSWORD=...
DEMO_ADMIN_PASSWORD=...
```

They set the passwords of the two documented accounts, `demo@neighboar.local` (a normal buyer) and `admin@neighboar.local` (an administrator). The other 198 buyers (`buyer003@example.com` to `buyer200@example.com`) have random passwords that are thrown away, so nobody can log in as them. The pipeline stops before cleaning if either variable is missing or too short. If an account of yours already uses one of these ids or emails, the script stops with a message and writes nothing.

To run the whole test suite against the running stack (the demo passwords are passed to the test container only, never to `web`):

```bash
set -a; . ./.env; set +a
docker compose run --rm --no-deps -e NEIGHBOAR_URL=http://web:8000 -e PG_APP_RO_PASSWORD -e PG_OWNER_PASSWORD -e MONGO_ETL_PASSWORD -e DEMO_BUYER_PASSWORD -e DEMO_ADMIN_PASSWORD web python -m pytest -q -m "integration or not integration"
```

Use named volumes so normal restarts retain local database data. `docker compose down` removes containers but retains named volumes. Only `docker compose down -v` deletes local database data.

Commit `compose.yaml`, the Dockerfile, `.env.example`, schema migrations, import scripts, and dependency lock files. Do not commit `.env`, database volumes, generated downloads, or passwords. Each teammate copies `.env.example` to `.env` and uses their own local development credentials. Docker recommends Compose for managing a multi-service application and supports health checks and named volumes for reliable startup and persistence. [Docker Compose quickstart](https://docs.docker.com/compose/gettingstarted/)

## Suggested five-person split

| Role | Main responsibility | Shared review responsibility |
| --- | --- | --- |
| 1. Data and quality | Source download, cleaning, town-code mapping, import reports, data dictionary | Review all schema assumptions and reproduce import from a clean environment |
| 2. PostgreSQL | ER diagram, SQL migrations, constraints, indexes, views, SQL queries | Explain relational model and review equivalent MongoDB results |
| 3. MongoDB | Document schema, import, indexes, aggregation pipelines, CRUD documents | Explain embedding decisions and review equivalent PostgreSQL results |
| 4. Application integration | FastAPI routes, authentication, database-mode query services, error handling | Review API contracts and keep SQL/Mongo results comparable |
| 5. Interface, testing, and evaluation | Browser UI, CRUD flows, test plan, performance harness, report evidence | Run acceptance tests and coordinate report, slides, and user manual evidence |

Roles are ownership areas, not silos. Each owner should write a short walkthrough and pair with one teammate for review. Everyone should be able to explain both database designs before the presentation.

## Collaboration rules

- Create one GitHub Issue per concrete task. Assign one owner and describe acceptance criteria before coding.
- Work in a short-lived branch named like `data/town-mapping` or `sql/initial-schema`.
- Open a pull request into `main`; at least one teammate reviews it before merging. Keep commits focused and describe the tested behaviour in the pull request.
- Never hand-edit a running database as the only way to change its schema or seed data. Use committed migrations and import scripts so every teammate can reproduce the same state.
- Use a weekly 30-minute meeting: review completed work, blockers, upcoming tasks, and evidence needed for the report. Record only decisions and next actions in `PROJECT_PROGRESS.md`.
- Keep raw official data out of ordinary commits. Use a documented download script and a small committed sample dataset for automated tests. Include the full data snapshot or retrieval instructions in the final source-code submission as required by the assignment.

## Immediate team decisions

1. Create the shared GitHub repository and invite all members.
2. Everyone installs Docker Desktop or Docker Engine with the Compose plugin, then confirms `docker compose version` works. Docker Desktop is Docker's recommended Compose installation path. [Docker Compose installation](https://docs.docker.com/compose/install/)
3. Agree on the initial role owners and schedule a short walkthrough of this brief.
4. Build the Compose skeleton before writing database-specific application code.
