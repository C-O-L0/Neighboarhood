"""The admin pages through the app (in process, against the running databases), in both DB modes (task 3.3).

The app is created inside the test process so a test can patch the refresh to fail and swap the database pools for
tripwires; the databases are the real ones. Every sale a test records is removed by AdminCleanup afterwards.
"""

import re
import secrets
from types import SimpleNamespace

import psycopg
import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from app.main import create_app
from app.repos.pg.admin import PgAdminRepo
from app.services.identities import admin_mongo, admin_pg, app_mongo, rw_pg
from tests.admin_support import AdminCleanup
from tests.equivalence.cases import admin_plan
from tests.integration.test_auth import delete_buyer

pytestmark = pytest.mark.integration

MODES = ("pg", "mongo")
PASSWORD = "correct horse battery"
NOTICE = re.compile(r'<div class="(?:ok|bad) notice" role="status">(.*?)</div>', re.S)
CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')
ADMIN_POSTS = ("/admin/sales", "/admin/sales/delete", "/admin/refresh", "/admin/rebuild-stats")


@pytest.fixture(scope="module")
def app():
    application = create_app()
    with TestClient(application):  # runs the lifespan: opens the pools and clients
        yield application


class Session:
    """A browser: its own cookie jar, and a fresh CSRF token fetched before each POST."""

    def __init__(self, app):
        self.http = TestClient(app, follow_redirects=False)

    def token(self, mode, path="/login"):
        return CSRF.search(self.http.get(f"{path}?db={mode}").text).group(1)

    def post(self, path, mode, **data):
        data.setdefault("csrf_token", self.token(mode))
        return self.http.post(f"{path}?db={mode}", data=data)

    def get(self, path, mode):
        return self.http.get(f"{path}?db={mode}")

    def follow(self, resp):
        assert resp.status_code == 303, resp.text[:300]
        return self.http.get(resp.headers["location"])


def notice(resp):
    found = NOTICE.search(resp.text)
    return re.sub(r"\s+", " ", found.group(1)).strip() if found else None


@pytest.fixture
def make_user(app, pg_owner, mongo_etl):
    made = []

    def make(mode, admin=False):
        session, email = Session(app), f"test-admin-{secrets.token_hex(6)}@example.com"
        assert session.post("/register", mode, email=email, password=PASSWORD).status_code == 303
        if admin:
            if mode == "pg":
                pg_owner.execute("UPDATE buyer SET is_admin = true WHERE lower(email) = %s", (email,))
                pg_owner.commit()
            else:
                mongo_etl.buyers.update_one({"email": email}, {"$set": {"is_admin": True}})
        assert session.post("/login", mode, email=email, password=PASSWORD).status_code == 303
        session.email = email
        made.append(email)
        return session

    yield make
    for email in made:
        delete_buyer(pg_owner, mongo_etl, email)


@pytest.fixture
def cleanup(settings):
    c = AdminCleanup(settings)
    yield c
    c.finish()


@pytest.fixture
def plan(cleanup):
    p = admin_plan(cleanup.owner)
    cleanup.watch(p.a_id)
    return p


def sale_form(plan, **overrides):
    data = {
        "block": plan.a_block, "street": plan.a_street, "month": f"{plan.month:%Y-%m}",
        "flat_type": plan.flat_type, "flat_model": plan.flat_model, "storey_min": "3", "storey_max": "5",
        "floor_area_sqm": "93.5", "remaining_lease_months": "700", "resale_price": "612345.67",
    }  # fmt: skip
    return {**data, **overrides}


def state_of(mode, cleanup):
    """(sale count, Mongo txn counter) of the database a mode reads."""
    if mode == "pg":
        return cleanup.owner.execute("SELECT count(*) FROM resale_transaction").fetchone()[0], None
    return cleanup.etl.resale_transactions.count_documents({}), cleanup.etl.counters.find_one({"_id": "txn"})["seq"]


def building_page(session, mode, plan):
    """The block's page without the timing footer, which differs on every request."""
    return session.get(f"/building/{plan.a_id}", mode).text.split('<footer class="timings">')[0]


def recorded_id(resp):
    return int(re.search(r"Sale (\d+) recorded", notice(resp)).group(1))


# ------------------------------------------------------------------------------------------------ access


@pytest.mark.parametrize("mode", MODES)
def test_signed_out_visitors_and_ordinary_buyers_get_403_on_every_admin_route(mode, app, make_user):
    ordinary, signed_out = make_user(mode), Session(app)
    for session in (ordinary, signed_out):
        assert session.get("/admin", mode).status_code == 403
        for path in ADMIN_POSTS:
            assert session.post(path, mode, txn_id="1").status_code == 403, path
    admin = make_user(mode, admin=True)
    page = admin.get("/admin", mode)
    assert page.status_code == 200 and "Record a sale" in page.text
    assert f'href="/admin?db={mode}"' in page.text and "/admin?" not in ordinary.get("/saved", mode).text


@pytest.mark.parametrize("mode", MODES)
def test_the_admin_flag_of_the_other_database_does_not_count(mode, make_user):
    admin = make_user(mode, admin=True)
    other = "mongo" if mode == "pg" else "pg"
    assert admin.get("/admin", mode).status_code == 200
    assert admin.get("/admin", other).status_code == 403  # signed in under one mode only


# ---------------------------------------------------------------------------------------------- validation


BAD_FORMS = {
    "3 dp price": {"resale_price": "612345.678"},
    "2 dp area": {"floor_area_sqm": "93.55"},
    "area 400": {"floor_area_sqm": "400"},
    "price 10,000,000": {"resale_price": "10000000"},
    "price NaN": {"resale_price": "NaN"},
    "price Infinity": {"resale_price": "Infinity"},
    "price 1E+30": {"resale_price": "1E+30"},
    "area NaN": {"floor_area_sqm": "NaN"},
    "price zero": {"resale_price": "0"},
    "storey min above max": {"storey_min": "6", "storey_max": "5"},
    "lease 1189": {"remaining_lease_months": "1189"},
    "storey 61": {"storey_max": "61"},
    "non-resale type": {"flat_type": "STUDIO APARTMENT"},
    "unknown model": {"flat_model": "Palace"},
    "bad month": {"month": "2024-13"},
    "blank block": {"block": "   "},
}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", BAD_FORMS)
def test_invalid_sales_get_422_and_write_nothing(mode, name, make_user, plan, cleanup):
    admin = make_user(mode, admin=True)
    before = state_of(mode, cleanup)
    resp = admin.post("/admin/sales", mode, **sale_form(plan, **BAD_FORMS[name]))
    assert resp.status_code == 422 and "Nothing was written." in resp.text
    assert state_of(mode, cleanup) == before


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("raw", ["0", str(2**63), "abc", "-1", "", "1.5", "1e3"])
def test_invalid_delete_ids_get_422_and_write_nothing(mode, raw, make_user, cleanup):
    admin = make_user(mode, admin=True)
    before = state_of(mode, cleanup)
    assert admin.post("/admin/sales/delete", mode, txn_id=raw).status_code == 422
    assert state_of(mode, cleanup) == before


@pytest.mark.parametrize("mode", MODES)
def test_deleting_a_sale_that_does_not_exist_is_not_found(mode, make_user, cleanup):
    admin = make_user(mode, admin=True)
    before = state_of(mode, cleanup)
    resp = admin.post("/admin/sales/delete", mode, txn_id=str(2**63 - 1))
    assert notice(admin.follow(resp)) == "No sale has that id."
    assert state_of(mode, cleanup) == before


# --------------------------------------------------------------------------------------------- the flows


@pytest.mark.parametrize("mode", MODES)
def test_record_list_and_delete_through_the_pages(mode, make_user, plan, cleanup):
    admin = make_user(mode, admin=True)
    before = state_of(mode, cleanup)

    page = admin.follow(admin.post("/admin/sales", mode, **sale_form(plan, street=plan.a_street.lower())))
    txn_id = recorded_id(page)
    assert "record sale (write):" in page.text
    assert ("town figures refresh:" in page.text) == (mode == "pg")  # PostgreSQL times the refresh on its own
    assert f"<td>{txn_id}</td>" in page.text and "$612,345.67" in page.text
    assert state_of(mode, cleanup)[0] == before[0] + 1
    assert "$612,345.67" in building_page(admin, mode, plan)  # the block's recent sales include the new sale

    page = admin.follow(admin.post("/admin/sales/delete", mode, txn_id=str(txn_id)))
    assert notice(page).startswith(f"Sale {txn_id} deleted.")
    assert f"<td>{txn_id}</td>" not in page.text
    assert state_of(mode, cleanup)[0] == before[0]


@pytest.mark.parametrize("mode", MODES)
def test_unknown_blocks_and_early_months_are_messages_not_errors(mode, make_user, plan, cleanup):
    admin = make_user(mode, admin=True)
    before = state_of(mode, cleanup)
    resp = admin.post("/admin/sales", mode, **sale_form(plan, block="9999"))
    assert notice(admin.follow(resp)) == "There is no such block."
    early = f"{plan.a_year_completed - 1}-01"
    resp = admin.post("/admin/sales", mode, **sale_form(plan, month=early))
    expected = f"The sale was rejected: sale in {early} predates block completion in {plan.a_year_completed}."
    assert notice(admin.follow(resp)) == expected
    assert state_of(mode, cleanup) == before


@pytest.mark.parametrize("mode", MODES)
def test_the_rollback_demos_leave_the_counts_and_the_block_unchanged(mode, make_user, plan, cleanup):
    admin = make_user(mode, admin=True)
    before = state_of(mode, cleanup)
    building_before = building_page(admin, mode, plan)

    page = admin.follow(admin.post("/admin/sales", mode, **sale_form(plan), simulate_failure="1"))
    text = notice(page)
    assert text.startswith("Rolled back on purpose: the sale was written, then failed before commit")
    assert f"Before: {before[0]:,} sales on the island" in text and f"After: {before[0]:,} sales on the island" in text
    assert "town figures refresh" not in page.text  # a rolled-back write is not refreshed
    assert state_of(mode, cleanup) == before
    assert building_page(admin, mode, plan) == building_before

    created = recorded_id(admin.follow(admin.post("/admin/sales", mode, **sale_form(plan))))
    with_sale = building_page(admin, mode, plan)
    counts = state_of(mode, cleanup)
    page = admin.follow(admin.post("/admin/sales/delete", mode, txn_id=str(created), simulate_failure="1"))
    assert notice(page).startswith("Rolled back on purpose: the sale was deleted, then failed before commit")
    assert state_of(mode, cleanup) == counts
    assert building_page(admin, mode, plan) == with_sale
    assert f"<td>{created}</td>" in page.text
    admin.follow(admin.post("/admin/sales/delete", mode, txn_id=str(created)))
    assert building_page(admin, mode, plan) == building_before


def test_the_rebuild_button_runs_the_merge_and_reports_its_timing(make_user, plan, cleanup):
    admin = make_user("mongo", admin=True)
    page = admin.follow(admin.post("/admin/rebuild-stats", "mongo"))
    assert re.fullmatch(r"Statistics rebuilt for [\d,]+ blocks with sales\.", notice(page))
    assert "stats rebuild ($merge):" in page.text
    assert admin.post("/admin/refresh", "mongo").status_code == 400  # the refresh belongs to PostgreSQL mode


def test_the_refresh_button_refreshes_and_reports_its_timing(make_user):
    admin = make_user("pg", admin=True)
    page = admin.follow(admin.post("/admin/refresh", "pg"))
    assert notice(page) == "Town figures refreshed." and "town figures refresh:" in page.text
    assert admin.post("/admin/rebuild-stats", "pg").status_code == 400


# --------------------------------------------------------------------------------------- the stale path


def mv_count(cleanup, plan):
    row = cleanup.owner.execute(
        "SELECT n FROM mv_town_month_stats m JOIN town t USING (town_id) JOIN flat_type f USING (flat_type_id)"
        " WHERE t.name = %s AND f.name = %s AND m.month = %s",
        (plan.town, plan.flat_type, plan.month),
    ).fetchone()
    return row[0]


def test_a_failed_refresh_keeps_the_sale_marks_the_figures_stale_and_the_next_refresh_repairs_them(
    monkeypatch, make_user, plan, cleanup, app
):
    admin = make_user("pg", admin=True)
    trends = f"/trends?town={plan.town.replace(' ', '+')}&flat_type={plan.flat_type.replace(' ', '+')}"
    stale = "Town figures are stale since"
    assert stale not in admin.http.get(f"{trends}&db=pg").text
    n_before = mv_count(cleanup, plan)

    def broken(self):
        raise psycopg.OperationalError("refresh failed on purpose")

    with monkeypatch.context() as patched:
        patched.setattr(PgAdminRepo, "refresh", broken)
        page = admin.follow(admin.post("/admin/sales", "pg", **sale_form(plan)))
    assert "town figures are stale until the next successful refresh" in notice(page)
    assert stale in page.text and app.state.admin.stale_since is not None
    txn_id = int(re.search(r"Sale (\d+) recorded", notice(page)).group(1))
    assert cleanup.owner.execute("SELECT 1 FROM resale_transaction WHERE txn_id = %s", (txn_id,)).fetchone()
    assert mv_count(cleanup, plan) == n_before  # the view still lacks the committed sale

    assert stale in admin.http.get(f"{trends}&db=pg").text  # the trends page in PostgreSQL mode warns
    assert stale not in admin.http.get(f"{trends}&db=mongo").text  # MongoDB figures are not affected

    page = admin.follow(admin.post("/admin/refresh", "pg"))
    assert notice(page) == "Town figures refreshed."
    assert stale not in page.text and app.state.admin.stale_since is None
    assert stale not in admin.http.get(f"{trends}&db=pg").text
    assert mv_count(cleanup, plan) == n_before + 1


def test_a_later_successful_write_also_clears_the_flag(monkeypatch, make_user, plan, cleanup, app):
    admin = make_user("pg", admin=True)
    with monkeypatch.context() as patched:
        patched.setattr(PgAdminRepo, "refresh", lambda self: (_ for _ in ()).throw(psycopg.OperationalError("x")))
        admin.post("/admin/sales", "pg", **sale_form(plan))
    assert app.state.admin.stale_since is not None
    admin.post("/admin/sales", "pg", **sale_form(plan, resale_price="612345.68"))
    assert app.state.admin.stale_since is None


# ---------------------------------------------------------------------------------------------- identity


def stub_request(app, mode):
    return SimpleNamespace(app=app, state=SimpleNamespace(db_mode=mode))


def pg_user(dependency, app):
    generator = dependency(stub_request(app, "pg"))
    try:
        return next(generator).execute("SELECT current_user").fetchone()[0]
    finally:
        generator.close()


def mongo_user(db):
    status = db.command("connectionStatus")["authInfo"]["authenticatedUsers"]
    return status[0]["user"]


def test_admin_dependencies_resolve_to_app_admin_and_normal_ones_to_app_rw_and_app(app):
    assert pg_user(admin_pg, app) == "app_admin"
    assert pg_user(rw_pg, app) == "app_rw"
    assert mongo_user(admin_mongo(stub_request(app, "mongo"))) == "app_admin"
    assert mongo_user(app_mongo(stub_request(app, "mongo"))) == "app"
    # Each yields for its own mode only, so a request checks out one connection.
    assert admin_mongo(stub_request(app, "pg")) is None and app_mongo(stub_request(app, "pg")) is None
    assert next(admin_pg(stub_request(app, "mongo")), "nothing") is None


def test_every_admin_route_depends_on_the_admin_identities_and_the_admin_check(app):
    from app.services.guards import require_admin

    found = {}
    for context in iter_route_contexts(app.routes):
        if context.path and context.path.startswith("/admin"):
            calls = {d.call for d in context.dependant.dependencies}
            found[context.path] = calls
    assert set(found) == {"/admin", *ADMIN_POSTS}
    for path, calls in found.items():
        assert {admin_pg, admin_mongo, require_admin} <= calls, path
        assert not ({rw_pg, app_mongo} & calls), path


class Tripwire:
    """Stands in for a pool or client the admin routes must never touch."""

    def connection(self, *args, **kwargs):
        raise AssertionError("an admin route used the normal (app_rw) pool")

    def __getitem__(self, name):
        raise AssertionError("an admin route used the normal (app) MongoDB client")

    def close(self):
        pass


@pytest.mark.parametrize("mode", MODES)
def test_admin_routes_never_touch_the_normal_pool_or_client(mode, app, make_user, plan, cleanup, monkeypatch):
    admin = make_user(mode, admin=True)
    token = admin.token(mode, "/admin")  # the sign-in page would itself use app_rw
    dbs = app.state.dbs
    with monkeypatch.context() as patched:
        patched.setattr(dbs, "pg_rw", Tripwire())
        patched.setattr(dbs, "mongo_app", Tripwire())
        assert admin.get("/admin", mode).status_code == 200
        post = lambda path, **data: admin.post(path, mode, csrf_token=token, **data)  # noqa: E731
        assert post("/admin/sales", **sale_form(plan, resale_price="1.234")).status_code == 422
        txn_id = recorded_id(admin.follow(post("/admin/sales", **sale_form(plan))))
        assert admin.follow(post("/admin/sales/delete", txn_id=str(txn_id), simulate_failure="1")).status_code == 200
        assert admin.follow(post("/admin/sales/delete", txn_id=str(txn_id))).status_code == 200
        assert admin.follow(post("/admin/refresh" if mode == "pg" else "/admin/rebuild-stats")).status_code == 200
        # Control: the tripwire does trip for a normal page, so the checks above mean something.
        with pytest.raises(AssertionError, match="normal"):
            admin.get("/search", mode)
