# ruff: noqa: F811 - the fixtures imported from test_admin_page are used by name
"""Which database identities the running app uses (task 3.5b, BUILD_PLAN section 9 "least-privilege users").

(a) `Databases.open` builds pools and clients for PostgreSQL app_rw and app_admin and MongoDB app and app_admin, and
    nothing else.
(b) Every route in the app is exercised in both DB modes with a spy on the pools and clients: ordinary routes never
    check out the admin pool or client, and admin routes check out only those. A fixture table gives the request for
    each route; a test fails when a route has no entry, so a route added later is covered automatically.
(c) The web pools are tagged `application_name = 'neighboar-web'`.

The app is created inside the test process (as in test_admin_page.py), against the real databases.
"""

from __future__ import annotations

import dataclasses
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi.routing import iter_route_contexts
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import ConnectionPool
from pymongo import MongoClient

from app.config import Settings
from app.db import WEB_APPLICATION_NAME, Databases
from tests.integration.test_admin_page import (  # noqa: F401 - fixtures are used by name
    PASSWORD,
    Session,
    app,
    cleanup,
    make_user,
    plan,
    sale_form,
)
from tests.integration.test_auth import delete_buyer

pytestmark = pytest.mark.integration

MODES = ("pg", "mongo")
NORMAL = {"pg_rw", "mongo_app"}
ADMIN = {"pg_admin", "mongo_admin"}


# ----------------------------------------------------------------------------------------------------- (a), (c)


@pytest.fixture
def opened(settings: Settings):
    dbs = Databases.open(settings)
    try:
        yield dbs
    finally:
        dbs.close()


def mongo_user(client: MongoClient, settings: Settings) -> str:
    info = client[settings.mongo_db].command("connectionStatus")["authInfo"]
    users = [u["user"] for u in info["authenticatedUsers"]]
    assert len(users) == 1
    return users[0]


def test_databases_open_builds_only_the_four_runtime_identities(opened: Databases, settings: Settings):
    # Nothing but these pools and clients exists on the object, so no other identity is opened.
    held = {f.name: getattr(opened, f.name) for f in dataclasses.fields(opened)}
    pools = {k for k, v in held.items() if isinstance(v, ConnectionPool)}
    clients = {k for k, v in held.items() if isinstance(v, MongoClient)}
    assert pools == {"pg_rw", "pg_admin"}
    assert clients == {"mongo_app", "mongo_admin"}
    assert set(held) - pools - clients == {"mongo_db_name"}

    assert conninfo_to_dict(opened.pg_rw.conninfo)["user"] == "app_rw"
    assert conninfo_to_dict(opened.pg_admin.conninfo)["user"] == "app_admin"
    assert mongo_user(opened.mongo_app, settings) == "app"
    assert mongo_user(opened.mongo_admin, settings) == "app_admin"


def test_the_pools_really_connect_as_those_roles(opened: Databases):
    with opened.pg_rw.connection(timeout=5) as conn:
        assert conn.execute("SELECT current_user, session_user").fetchone() == ("app_rw", "app_rw")
    with opened.pg_admin.connection(timeout=5) as conn:
        assert conn.execute("SELECT current_user, session_user").fetchone() == ("app_admin", "app_admin")


def test_the_web_pools_are_tagged_neighboar_web(opened: Databases, pg_owner):
    assert WEB_APPLICATION_NAME == "neighboar-web"
    for pool in (opened.pg_rw, opened.pg_admin):
        assert conninfo_to_dict(pool.conninfo)["application_name"] == "neighboar-web"
    with opened.pg_rw.connection(timeout=5) as rw, opened.pg_admin.connection(timeout=5) as admin:
        assert rw.execute("SHOW application_name").fetchone()[0] == "neighboar-web"
        assert admin.execute("SHOW application_name").fetchone()[0] == "neighboar-web"
        # The evidence for the report: pg_stat_activity, read by the owner, shows the web sessions by role.
        rows = pg_owner.execute(
            "SELECT usename, count(*) FROM pg_stat_activity WHERE application_name = 'neighboar-web' "
            "GROUP BY usename ORDER BY usename"
        ).fetchall()
    assert {user for user, _ in rows} == {"app_admin", "app_rw"}


def test_other_tools_keep_the_neighboar_application_name(settings: Settings, pg_rw):
    assert conninfo_to_dict(settings.pg_dsn("app_rw"))["application_name"] == "neighboar"
    assert pg_rw.execute("SHOW application_name").fetchone()[0] == "neighboar"


# ---------------------------------------------------------------------------------------------------------- (b)


@dataclass
class Req:
    """One request: the path, query and form values (the CSRF token is added for a form)."""

    path: str
    query: dict | None = None
    form: dict | None = None


# "METHOD template" -> (who makes the request: "anon", "buyer" or "admin", function of the context -> Req). The keys
# are the app's route templates. The context has plan (a loaded block and a valid sale), the signed-in session's
# email and a fresh email for registration.
ROUTES: dict[str, tuple[str, Callable[[SimpleNamespace], Req]]] = {
    "GET /openapi.json": ("anon", lambda c: Req("/openapi.json")),
    "GET /docs": ("anon", lambda c: Req("/docs")),
    "GET /docs/oauth2-redirect": ("anon", lambda c: Req("/docs/oauth2-redirect")),
    "GET /redoc": ("anon", lambda c: Req("/redoc")),
    "MOUNT /static": ("anon", lambda c: Req("/static/app.css")),
    "GET /health": ("anon", lambda c: Req("/health")),
    "GET /api/health": ("anon", lambda c: Req("/api/health")),
    "GET /": ("buyer", lambda c: Req("/")),
    "GET /building/find": (
        "buyer",
        lambda c: Req("/building/find", {"block": c.plan.a_block, "street": c.plan.a_street}),
    ),
    "GET /building/{building_id:int}": ("buyer", lambda c: Req(f"/building/{c.plan.a_id}")),
    "GET /food": ("buyer", lambda c: Req("/food", {"town": "BISHAN"})),
    "GET /search": ("buyer", lambda c: Req("/search", {"town": "BISHAN"})),
    "GET /trends": ("buyer", lambda c: Req("/trends", {"town": "BISHAN", "flat_type": "4 ROOM"})),
    "GET /rankings": ("buyer", lambda c: Req("/rankings", {"year": "2024", "flat_type": "4 ROOM"})),
    "GET /register": ("anon", lambda c: Req("/register")),
    "POST /register": ("anon", lambda c: Req("/register", form={"email": c.new_email, "password": PASSWORD})),
    "GET /login": ("anon", lambda c: Req("/login")),
    "POST /login": ("buyer", lambda c: Req("/login", form={"email": c.email, "password": PASSWORD})),
    "POST /logout": ("buyer", lambda c: Req("/logout", form={})),
    "GET /account": ("buyer", lambda c: Req("/account")),
    "GET /saved": ("buyer", lambda c: Req("/saved")),
    "POST /saved": ("buyer", lambda c: Req("/saved", form={"building_id": str(c.plan.a_id), "note": "probe"})),
    "POST /saved/note": ("buyer", lambda c: Req("/saved/note", form={"building_id": str(c.plan.a_id), "note": "x"})),
    "POST /saved/remove": ("buyer", lambda c: Req("/saved/remove", form={"building_id": str(c.plan.a_id)})),
    "GET /glossary": ("anon", lambda c: Req("/glossary")),
    "POST /demo": ("anon", lambda c: Req("/demo", form={"on": "1", "next": "/"})),
    "GET /admin": ("admin", lambda c: Req("/admin")),
    "POST /admin/sales": ("admin", lambda c: Req("/admin/sales", form=sale_form(c.plan))),
    "POST /admin/sales/delete": ("admin", lambda c: Req("/admin/sales/delete", form={"txn_id": str(2**63 - 1)})),
    "POST /admin/refresh": ("admin", lambda c: Req("/admin/refresh", form={})),
    "POST /admin/rebuild-stats": ("admin", lambda c: Req("/admin/rebuild-stats", form={})),
}


def route_keys(application) -> set[str]:
    """"METHOD path" for every route; included routers are expanded (FastAPI 0.143 app.routes no longer lists them)."""
    keys = set()
    for context in iter_route_contexts(application.routes):
        methods = sorted(m for m in (context.methods or ()) if m != "HEAD") or ["MOUNT"]
        keys.update(f"{method} {context.path}" for method in methods)
    return keys


def test_every_route_has_a_request_in_the_table(app):
    found = route_keys(app)
    assert found >= {"GET /health", "POST /admin/sales", "GET /building/{building_id:int}", "MOUNT /static"}
    missing = sorted(found - set(ROUTES))
    stale = sorted(set(ROUTES) - found)
    assert not missing, f"add a request for these routes to ROUTES in {__file__}: {missing}"
    assert not stale, f"these ROUTES entries match no route: {stale}"


class Spy:
    """Stands in for a pool or client, records each checkout and passes it on to the real one."""

    def __init__(self, real, name: str, log: list[str]):
        self._real, self._name, self._log = real, name, log

    def connection(self, *args, **kwargs):
        self._log.append(self._name)
        return self._real.connection(*args, **kwargs)

    def getconn(self, *args, **kwargs):
        self._log.append(self._name)
        return self._real.getconn(*args, **kwargs)

    def __getitem__(self, name):
        self._log.append(self._name)
        return self._real[name]

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture
def stray_buyers(pg_owner, mongo_etl):
    """Emails registered by the requests under test; removed afterwards from both databases."""
    emails = []
    yield emails
    for email in emails:
        delete_buyer(pg_owner, mongo_etl, email)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("key", sorted(ROUTES))
def test_route_checks_out_only_its_own_identities(
    key, mode, app, make_user, plan, cleanup, stray_buyers, monkeypatch
):
    new_email = f"test-identity-{secrets.token_hex(6)}@example.com"
    stray_buyers.append(new_email)
    role, build = ROUTES[key]
    # Signing in and fetching a CSRF token use app_rw, so they happen before the spy is installed.
    session = Session(app) if role == "anon" else make_user(mode, admin=role == "admin")
    req = build(SimpleNamespace(plan=plan, new_email=new_email, email=getattr(session, "email", "")))
    form = None if req.form is None else {**req.form, "csrf_token": session.token(mode)}
    method = key.split(" ", 1)[0]
    method = "GET" if method == "MOUNT" else method

    log: list[str] = []
    dbs = app.state.dbs
    for name in ("pg_rw", "pg_admin", "mongo_app", "mongo_admin"):
        monkeypatch.setattr(dbs, name, Spy(getattr(dbs, name), name, log))
    response = session.http.request(method, req.path, params={**(req.query or {}), "db": mode}, data=form)

    assert response.status_code < 500, response.text[:300]
    used = set(log)
    if role == "admin":
        assert response.status_code != 403, "the admin session was refused"
        assert used, "an admin route must reach the database as app_admin / admin"
        assert used <= ADMIN, f"{key} ({mode}) checked out {sorted(used - ADMIN)}"
    else:
        assert used <= NORMAL, f"{key} ({mode}) checked out {sorted(used - NORMAL)}"
        if role == "buyer":
            assert used, "a signed-in route looks the account up with app_rw / app"


def test_the_spy_sees_a_checkout(app, monkeypatch):
    """The spy is not blind: the health route checks out both normal identities."""
    log: list[str] = []
    dbs = app.state.dbs
    for name in ("pg_rw", "pg_admin", "mongo_app", "mongo_admin"):
        monkeypatch.setattr(dbs, name, Spy(getattr(dbs, name), name, log))
    assert Session(app).http.get("/api/health").status_code == 200
    assert set(log) == {"pg_rw", "mongo_app"}
