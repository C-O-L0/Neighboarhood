"""Block profile (S3), DB-mode switch, timing footer and block lookup through the running web app."""

import os
import re

import httpx
import pytest

from tests.integration.web import DEMO

BASE = os.environ.get("NEIGHBOAR_URL", "http://127.0.0.1:8000")
pytestmark = pytest.mark.integration

MAIN = re.compile(r'<main id="main" class="page">(.*?)</main>', re.S)
TIMING = re.compile(r"<li>([^<]+): [\d.]+ ms on (PostgreSQL|MongoDB) \(query and fetch\)</li>")


def get(path, **kw):
    kw.setdefault("cookies", DEMO)
    return httpx.get(f"{BASE}{path}", timeout=15, **kw)


def main_of(resp):
    return re.sub(r"\s+", " ", MAIN.search(resp.text).group(1)).strip()


@pytest.fixture
def ids(pg_rw):
    def one(sql):
        return pg_rw.execute(sql).fetchone()[0]

    return {
        "normal": one(
            "SELECT b.building_id FROM building b WHERE in_property_dataset AND EXISTS "
            "(SELECT 1 FROM building_flat_supply s WHERE s.building_id = b.building_id) "
            "AND (SELECT count(*) FROM resale_transaction t WHERE t.building_id = b.building_id) > 10 "
            "ORDER BY building_id LIMIT 1"
        ),
        "stub": one("SELECT building_id FROM building WHERE NOT in_property_dataset"),
        "zero_units": one(
            "SELECT building_id FROM building b WHERE in_property_dataset AND NOT EXISTS "
            "(SELECT 1 FROM building_flat_supply s WHERE s.building_id = b.building_id) ORDER BY building_id LIMIT 1"
        ),
        "teban": one("SELECT building_id FROM building WHERE block = '37' AND street = 'TEBAN GDNS RD'"),
    }


@pytest.mark.parametrize("kind", ["normal", "stub", "zero_units", "teban"])
def test_same_rendered_values_from_both_databases(ids, kind):
    pg = get(f"/block/{ids[kind]}?db=pg")
    mongo = get(f"/block/{ids[kind]}?db=mongo")
    assert pg.status_code == mongo.status_code == 200
    a, b = main_of(pg), main_of(mongo)
    assert a == b


def test_stub_shows_unknown_and_zero_units_shows_zero(ids):
    stub = main_of(get(f"/block/{ids['stub']}?db=pg"))
    assert "<dt>Completed</dt><dd>Unknown</dd>" in stub
    assert "<dt>Highest floor</dt><dd>Unknown</dd>" in stub
    assert "<dt>Total flats</dt><dd>Unknown</dd>" in stub
    assert "<dt>Land use</dt> <dd> Unknown" in stub
    assert "<dt>Flats</dt> <dd> Unknown" in stub
    zero = main_of(get(f"/block/{ids['zero_units']}?db=mongo"))
    assert "<dt>Total flats</dt><dd>0</dd>" in zero
    assert "<dt>Flats</dt> <dd> No flats" in zero


def test_teban_lease_year(ids):
    for db in ("pg", "mongo"):
        page = main_of(get(f"/block/{ids['teban']}?db={db}"))
        assert re.search(r"Lease start<a [^>]+>\?</a></dt><dd>1966</dd>", page)


def test_typical_price_is_labelled_and_links_to_the_glossary(ids):
    page = get(f"/block/{ids['normal']}").text
    assert "Typical price" in page and 'href="/glossary#typical-price"' in page
    assert "median*" not in page.lower()


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_unknown_building_is_404(db):
    resp = get(f"/block/2000000000?db={db}")
    assert resp.status_code == 404
    assert get(f"/block/99999999999?db={db}").status_code == 404


def test_invalid_db_is_400():
    assert get("/?db=xyz").status_code == 400
    assert get("/block/1?db=").status_code == 400


def test_valid_db_sets_the_cookie_with_its_attributes():
    resp = get("/?db=mongo")
    # every page also sets the session cookie (CSRF token), so pick this one out
    cookie = next(c for c in resp.headers.get_list("set-cookie") if c.startswith("db_mode=")).lower()
    assert cookie.startswith("db_mode=mongo")
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie
    assert "max-age=2592000" in cookie


def test_in_demo_mode_the_cookie_decides_the_mode_and_default_is_pg(ids):
    path = f"/block/{ids['normal']}"
    assert {m[1] for m in TIMING.findall(get(path).text)} == {"PostgreSQL"}
    with httpx.Client(base_url=BASE, cookies={**DEMO, "db_mode": "mongo"}, timeout=15) as client:
        assert {m[1] for m in TIMING.findall(client.get(path).text)} == {"MongoDB"}
        # the query parameter wins over the cookie
        assert {m[1] for m in TIMING.findall(client.get(path + "?db=pg").text)} == {"PostgreSQL"}
    with httpx.Client(base_url=BASE, cookies={**DEMO, "db_mode": "bogus"}, timeout=15) as client:
        assert {m[1] for m in TIMING.findall(client.get(path).text)} == {"PostgreSQL"}


@pytest.mark.parametrize("db,name", [("pg", "PostgreSQL"), ("mongo", "MongoDB")])
def test_demo_bar_has_one_timing_line_per_repo_call_naming_the_database(ids, db, name):
    page = get(f"/block/{ids['normal']}?db={db}").text
    timings = TIMING.findall(page)
    assert timings[:3] == [("block profile", name), ("food access", name), ("recent prices", name)]
    rows = len(re.findall(r"<tr><td", page.split('<table class="recent">')[1].split("</table>")[0]))
    assert timings[3:] == [("town baselines", name)] * rows  # one baseline per flat type sold recently


def test_header_links_keep_other_parameters_and_encode_them():
    page = get("/block/find?block=1+2&street=A%26B&db=pg").text  # 422 page still has the header
    assert 'href="/block/find?block=1+2&amp;street=A%26B&amp;db=mongo"' in page
    assert 'href="/block/find?block=1+2&amp;street=A%26B&amp;db=pg"' in page


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_find_redirects_to_the_building(ids, db):
    resp = get(f"/block/find?block=37&street=teban++gdns rd&db={db}", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/block/{ids['teban']}"


def test_the_old_building_paths_redirect_to_the_block_keeping_the_query(ids):
    resp = get(f"/building/{ids['teban']}?db=mongo&at=2040", follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["location"] == f"/block/{ids['teban']}?db=mongo&at=2040"
    resp = get("/building/find?block=37&street=teban+gdns+rd", follow_redirects=False)
    assert resp.status_code == 308 and resp.headers["location"] == "/block/find?block=37&street=teban+gdns+rd"
    found = get("/building/find?block=37&street=teban+gdns+rd", follow_redirects=True)
    assert found.url.path == f"/block/{ids['teban']}"


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_find_not_found_is_404(db):
    resp = get(f"/block/find?block=99999&street=NO SUCH ROAD&db={db}", follow_redirects=False)
    assert resp.status_code == 404
    assert "not found" in resp.text.lower()
    assert len(TIMING.findall(resp.text)) == 1


@pytest.mark.parametrize(
    "query",
    [
        "block=&street=X",
        "block=37&street=",
        "block=123456&street=X",
        "block=1&street=" + "A" * 41,
        "",
        "block=%20&street=X",
    ],
)
def test_find_rejects_bad_input_with_422_and_no_lookup(query):
    resp = get(f"/block/find?{query}", follow_redirects=False)
    assert resp.status_code == 422
    # The home page it returns to loads its flat type ranges; the block lookup itself never runs.
    assert [t[0] for t in TIMING.findall(resp.text)] == ["flat type ranges"]


def test_home_page_has_the_lookup_form():
    resp = get("/")
    assert resp.status_code == 200
    assert 'action="/block/find"' in resp.text


def test_health_still_works():
    assert get("/api/health").status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "//evil.example/x?db=zz",
        "/%2F%2Fevil.example?db=zz",
        "/%5Cevil.example?db=zz",
        "/%5C%5Cevil.example?db=zz",
        "/%09/evil.example?db=zz",
    ],
)
def test_toggle_links_stay_on_this_origin(path):
    # httpx would normalise the path, so send the request line as written
    resp = httpx.get(httpx.URL(BASE).copy_with(raw_path=path.encode()), headers={"Cookie": "demo=1"}, timeout=15)
    assert resp.status_code == 400
    hrefs = re.findall(r'<nav class="mode".*?</nav>', resp.text, re.S)[0]
    links = re.findall(r'href="([^"]*)"', hrefs)
    assert len(links) == 2
    assert all(re.match(r"/(?![/\\])", h) for h in links), links


def test_toggle_links_keep_a_normal_path(ids):
    page = get(f"/block/{ids['normal']}?db=pg").text
    assert f'href="/block/{ids["normal"]}?db=mongo"' in page
    assert f'href="/block/{ids["normal"]}?db=pg"' in page


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_find_accepts_full_street_words(ids, db):
    resp = get(f"/block/find?block=17&street=marine+terrace&db={db}", follow_redirects=False)
    assert resp.status_code == 303 and re.fullmatch(r"/block/\d+", resp.headers["location"])
