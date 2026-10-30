# Phase 2 conventions

Rules for every feature that the web app serves from either database. They come from task 2.1 (DB-mode switch and building page) and are what tasks 2.2 to 2.5 build on. Section numbers in brackets refer to `docs/BUILD_PLAN.md`.

## 1. Layout (section 3)

- `app/repos/dto.py`: frozen dataclasses returned by both repos.
- `app/repos/protocols.py`: one `Protocol` per feature.
- `app/repos/pg/<feature>.py` and `app/repos/mongo/<feature>.py`: the two implementations.
- `app/services/`: DB-mode resolution (`dbmode.py`), timing and repo dispatch (`repos.py`).
- `app/routers/<feature>.py` and `app/templates/<feature>.html`.
- `tests/equivalence/` (with `__init__.py`): the equivalence harness.

A new feature adds its DTOs, its Protocol, its two repos, one entry in `REPOS` in `app/services/repos.py`, and one `Case` in `tests/equivalence/cases.py`.

## 2. Types in DTOs

- Money and areas are `Decimal` (MongoDB `Decimal128.to_decimal()`).
- Months are `datetime.date` on day 1 (MongoDB midnight-UTC datetimes converted with `.date()`).
- Coordinates are `Decimal` with exactly 6 dp: MongoDB floats via `Decimal(repr(x)).quantize(Decimal("0.000001"))`, PostgreSQL `NUMERIC(9,6)` as is.
- Never `float` in a DTO.

## 3. Repos

- Repos materialise everything they return (`fetchall()`, `list(cursor)`) and do no I/O after returning.
- A PostgreSQL repo is built around one connection and a MongoDB repo around one `Database`. Repos never open pools or clients.
- A PostgreSQL repo that issues more than one statement to build one result runs them in one read-only REPEATABLE READ transaction: `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY` as the first statement inside `conn.transaction()`. One page then sees one snapshot, as a single MongoDB document read does.
- Repo connections are always passed in idle and in autocommit mode, so `conn.transaction()` issues a real `BEGIN`. The `app_rw` and `app_admin` pools in `app/db.py` are created with `kwargs={"autocommit": True}`, and every test fixture that hands a connection to a repo connects with `autocommit=True`.

## 4. Text ordering

Every SQL `ORDER BY` (and window `ORDER BY`) on a text column uses `COLLATE "C"`. This matches MongoDB's and Python's code-point order; the PostgreSQL database uses `en_US.utf8`, under which for example `Model A2` sorts before `Model A-Maisonette`. MongoDB repos that return embedded arrays in a defined order sort them in Python with the same key rather than relying on `$push` order.

## 5. Timing (section 2)

The service layer times only the repo call with `time.perf_counter_ns()`. Connection checkout from the pool is outside the timed span. It records `Timing(label, db, ms)` on `request.state.timings`. The two lookups that serve the page frame rather than the page, the signed-in account and the data month in the footer (`app/services/guards.py`), are not timed. In demo mode (section 6) the demo bar lists each timing as "<label>: <ms> ms on PostgreSQL|MongoDB (query and fetch)", after the page total.

## 6. DB mode

- `?db=pg|mongo` selects the database. Any other value is a 400.
- Demo mode is on while the `demo` cookie is `1`. It is set and cleared by the footer toggle, `POST /demo` (CSRF as every POST). Turning it off also clears `db_mode` and drops `?db=` from the page the browser returns to. The return path is checked like the mode links below; anything with a scheme or host goes to `/`.
- If the parameter is absent, the `db_mode` cookie decides in demo mode when it holds a valid value; otherwise the mode is `pg`. A buyer who never turns demo mode on always uses PostgreSQL.
- A request with a valid `?db=` sets `db_mode` on the response (`HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age` 30 days). The `demo` cookie has the same attributes.
- Only in demo mode, the demo bar under the header shows the current mode, two links to the current path and query with `db=pg` and `db=mongo` (other query parameters kept and URL-encoded), the page's timings and a link to the health page.
- The `db_mode` cookie is a display preference, not account state, so switching by link needs no CSRF token.

## 7. Least privilege

User-facing routes use the `app_rw` pool and the MongoDB `app` user only (`app/db.py`), never admin identities.

## 8. Security

- Parameterised SQL only.
- Identifiers come only from allowlists, through `psycopg.sql.Identifier`.
- MongoDB filters are built only from typed, validated values (Pydantic or path converters), never from raw request dicts.
- Text inputs have length limits matching the column sizes.

## 9. Lookups for forms

Town names, resale flat types and flat models come from the fixed lists in `etl/clean.py` (`TOWNS`, `FLAT_TYPES` with `is_resale_type`, `FLAT_MODELS`), the single definition both databases are loaded from (PROJECT_PROGRESS, 2026-10-20). Forms make no database call for lookups. Block and street input is normalised with `etl.clean.normalise_key`.

## 10. Equivalence tests

Tests in `tests/equivalence/` are marked `pytest.mark.integration` and call both repos directly with `app_rw` and MongoDB `app` connections.

- `tests/equivalence/cases.py` is the single registry of comparison cases. Each feature registers `Case(name, pg_call, mongo_call, params)`. The calls take (connection or database, params) and return DTOs; `params(conn)` returns the parameter dicts to run. The pytest files iterate over the registry, and Phase 4's `bench/verify.py` will iterate over the same registry on full data.
- Sampling rule: when the loaded data has at most 1,000 buildings (the sample), every case runs its full parameter list. Otherwise each feature's case list is the fixed full-data list it declares (buildings: every 25th building id in id order, plus the stub, 37 TEBAN GDNS RD and one zero-unit property block).
- A session-scoped `settle` fixture in `tests/equivalence/conftest.py` connects as `app_admin` with `psycopg.connect(settings.pg_dsn("app_admin"), autocommit=True)`, as `etl/load_pg.py` does for its refresh, and calls `refresh_town_stats()` before any equivalence test runs (section 6, "settle step"). Every equivalence test depends on it.
- `tests/equivalence/compare.py` provides `assert_equivalent(pg, mongo)`. It is recursive over dataclasses, lists (order matters), dicts and scalars. Values must be exactly equal, including `None` and every `Decimal`, except dataclass fields declared with `field(metadata={"tol": Decimal("0.01")})`, which may differ by at most 0.01. Only rounded aggregates where PostgreSQL rounds half away from zero and MongoDB `$round` rounds half to even carry that metadata (today `avg_psm`). Medians are exact observed prices and are never tolerant. A failure names the field path.

## 11. Formatting filters

Registered once in `app/templating.py` and used by every page; there is no other formatting code.

| Filter | Example |
| --- | --- |
| `money` | `money(Decimal("1234.5"))` is `$1,234.50`; a whole amount drops the cents: `money(Decimal("-12500"))` is `-$12,500`; `money(None)` is `Unknown` |
| `month` | `month(date(2024, 1, 1))` is `Jan 2024` |
| `lease` | `lease(733)` is `61 years 01 month`; `lease(732)` is `61 years`; `lease(735)` is `61 years 03 months`; `lease(12)` is `1 year`; `lease(13)` is `1 year 01 month` |
| `storey` | `storey(7, 9)` is `07 TO 09` |
| `pct` | `pct(Decimal("-3.4"))` is `-3.4%` (one decimal place, half up); `pct(None)` is empty |
| `unknown` | `unknown(None)` is `Unknown`; any other value is returned unchanged |
| `stamp` | `stamp(datetime(2026, 10, 28, 14, 3, tzinfo=UTC))` is `2026-10-28 14:03 UTC`; `stamp(None)` is `Never` |

`lease` follows the source's own wording: whole years have no month part, a single month is singular, and a single year is singular.

## 12. Labels

A median is labelled "typical price" (decided 2026-10-30, replacing "median*" and its footnote). Its first use in a table header or summary carries the glossary link (`ui.term('Typical price', 'typical-price')`), and the glossary defines it as the lower of the two middle prices when n is even (section 6). Derived labels follow: "12-month typical price", "Island typical price", "Typical price a year earlier".

## 13. Accounts, sessions and CSRF (task 3.1b)

- Each database holds its own accounts. A request reads and writes accounts only in the database chosen for it, and signing in under one mode does not sign the user in under the other. The signed session holds `{"uid": {"pg": int, "mongo": int}, "csrf": str}` and nothing else.
- CSRF is enforced by an app-level dependency (`app/services/guards.py`), so every POST route, including later ones, needs the form field `csrf_token` and gets a 403 otherwise, before its own validation runs. Templates read the token from the `csrf_token` context variable; every `<form method="post">` includes it as a hidden field.
- The login throttle (5 failures per lower-cased email and client IP in a sliding 15 minutes, then HTTP 429) is held in memory per process. The app runs one uvicorn worker, so the state is lost on restart and is not shared if workers are added.
- Passwords are 10 to 128 characters, checked before hashing, and hashed with argon2id (`argon2.PasswordHasher()`).

## 14. Interface

- Styles live in `app/static/app.css`: colour, radius and spacing tokens on `:root` first, then base elements, layout and components. Pages use the tokens, never their own colours.
- The font is Figtree, self-hosted from `app/static/vendor/figtree-latin.woff2` (Latin subset, SIL Open Font License); no font or script loads from another site.
- Every page has the same header (brand, main menu with `aria-current="page"` on the current item, account area) and footer (glossary link, demo toggle). The account menu is a `<details>` element, so it needs no script.
- Shared pieces are Jinja macros in `app/templates/_ui.html`, imported as `ui`. A component is added there by the first task that uses it, not ahead of its first user.
- Plain language first: buttons and links name the action, jargon gets a glossary link (`ui.term`), and the glossary at `/glossary` has one anchored entry per term.
- Months and storey bands in tables do not wrap (`class="nowrap"`). Controls are at least 44 px tall, except compact buttons inside tables (36 px).
