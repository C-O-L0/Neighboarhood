# HDB Neighbourhood Explorer - Team Brief

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
2. **PostgreSQL design** - implement `town`, `building`, `resale_transaction`, `buyer`, and `saved_building`. A building has many transactions. Buyers and buildings have a many-to-many relationship through `saved_building`.
3. **MongoDB design** - build a `building_profiles` collection with building metadata and embedded transactions, plus buyer documents with saved-building entries. Preserve the three unmatched transactions separately so town-level analysis remains complete.
4. **Shared features** - implement equivalent search, filtering, aggregation, building detail, and saved-building operations. The UI can let reviewers select the PostgreSQL or MongoDB implementation for supported comparison queries.
5. **Advanced work** - add indexed trend/ranking queries, explain query plans, test data constraints, measure equivalent query latency and throughput, and report tradeoffs.

The resale and property datasets are the validated core for the first prototype. Hawker-centre locations are the third planned dataset for amenity features. We must still validate its coordinate fields and determine a defensible proximity method before promising a specific "nearby hawker" calculation.

## Docker Compose workflow

Use Docker Compose from the start. The eventual `compose.yaml` will run three services:

```text
web       FastAPI application, exposed at http://localhost:8000
postgres  PostgreSQL, persistent named volume
mongodb   MongoDB, persistent named volume
```

Compose gives the services a private network, lets the application use service names such as `postgres` and `mongodb`, and starts the full stack with one command:

```bash
docker compose up --build
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
