"""Search page through the running web app, and the id dicts the PostgreSQL filters rely on."""

import os
import re
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from psycopg import sql

from etl.clean import FLAT_MODEL_IDS, FLAT_TYPE_IDS, TOWN_IDS
from tests.integration.web import DEMO

BASE = os.environ.get("NEIGHBOAR_URL", "http://127.0.0.1:8000")
pytestmark = pytest.mark.integration

MAIN = re.compile(r'<main id="main" class="page">(.*?)</main>', re.S)
TIMING = re.compile(r"<li>([^<]+): [\d.]+ ms on (PostgreSQL|MongoDB) \(query and fetch\)</li>")
ROWS = re.compile(r"<tbody>(.*?)</tbody>", re.S)


def get(path, **kw):
    kw.setdefault("cookies", DEMO)
    return httpx.get(f"{BASE}{path}", timeout=30, **kw)


def main_of(resp):
    return re.sub(r"\s+", " ", MAIN.search(resp.text).group(1)).strip()


def test_id_dicts_equal_the_loaded_lookup_tables(pg_rw):
    for table, key, ids in (
        ("town", "town_id", TOWN_IDS),
        ("flat_type", "flat_type_id", FLAT_TYPE_IDS),
        ("flat_model", "flat_model_id", FLAT_MODEL_IDS),
    ):
        query = sql.SQL("SELECT name, {} FROM {}").format(sql.Identifier(key), sql.Identifier(table))
        loaded = dict(pg_rw.execute(query).fetchall())
        assert loaded == ids, table


@pytest.mark.parametrize(
    "query",
    [
        "month_from=2024-3",
        "month_from=2024-13",
        "month_from=24-03",
        "month_from=abc",
        "month_from=2024-05&month_to=2024-04",
        "price_min=-1",
        "price_min=1.234",
        "price_min=abc",
        "price_min=1e5",
        "price_min=10000000",
        "price_min=600000&price_max=500000",
        "area_min=0",
        "area_min=90.55",
        "area_min=90.05",
        "area_max=400",
        "area_min=100&area_max=90",
        "town=ATLANTIS",
        "town=bishan",
        "flat_type=9 ROOM",
        "flat_type=STUDIO APARTMENT",
        "flat_model=Castle",
        "street=" + "A" * 41,
        "block=123456",
        "street=A%00B",
        "sort=resale_price",
        "sort=price;DROP TABLE x",
        "order=sideways",
        "page=0",
        "page=10001",
        "page=-1",
        "page=abc",
        "page=1.5",
    ],
)
@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_bad_input_is_422_with_a_message_and_no_query(query, db):
    resp = get(f"/search?{query}&db={db}")
    assert resp.status_code == 422
    assert 'role="alert"' in resp.text and "<li>" in main_of(resp)
    assert "<form" in resp.text
    assert not TIMING.findall(resp.text)


def test_error_messages_name_the_field():
    page = main_of(get("/search?price_min=-1&area_min=0&month_from=x&page=0"))
    for text in ("Minimum price", "Minimum area", "From month", "Page must be"):
        assert text in page


def test_submitted_values_are_echoed_and_escaped():
    page = get('/search?street="><script>alert(1)</script>&town=ATLANTIS').text
    assert "<script>alert(1)</script>" not in page


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_blank_fields_equal_no_fields(db):
    blank = get(
        f"/search?town=&flat_type=&flat_model=&month_from=&month_to=&price_min=&price_max="
        f"&area_min=&area_max=&street=&block=&sort=&order=&page=&db={db}"
    )
    none = get(f"/search?db={db}")
    assert blank.status_code == none.status_code == 200
    assert ROWS.search(blank.text).group(1) == ROWS.search(none.text).group(1)
    a, b = get("/search?town=&street=&price_min="), get("/search")
    assert ROWS.search(a.text).group(1) == ROWS.search(b.text).group(1)
    count = re.compile(r"\d+ sales, page 1 of \d+")
    assert count.search(a.text).group(0) == count.search(b.text).group(0)


def test_whitespace_only_street_and_block_apply_no_filter():
    plain = get("/search?db=pg")
    spaced = get("/search?street=%20%20&block=%20&db=pg")
    assert spaced.status_code == 200
    assert ROWS.search(spaced.text).group(1) == ROWS.search(plain.text).group(1)


@pytest.mark.parametrize("db,name", [("pg", "PostgreSQL"), ("mongo", "MongoDB")])
def test_search_shows_results_and_timing_footer(db, name):
    resp = get(f"/search?town=BISHAN&db={db}")
    assert resp.status_code == 200
    assert TIMING.findall(resp.text) == [("search", name)]
    assert re.search(r"[\d,]+ sales, page 1 of \d+", resp.text)


def test_both_databases_render_the_same_results():
    q = "/search?town=BISHAN&flat_type=4+ROOM&sort=price&order=asc&page=2"
    pg, mongo = get(q + "&db=pg"), get(q + "&db=mongo")
    assert ROWS.search(pg.text).group(1) == ROWS.search(mongo.text).group(1)
    count = re.compile(r"[\d,]+ sales, page 2 of \d+")
    assert count.search(pg.text).group(0) == count.search(mongo.text).group(0)


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_row_formatting(db):
    page = main_of(get(f"/search?street=QUEEN%27S&db={db}"))
    assert re.search(r"\$\d{1,3}(,\d{3})+\.\d{2}", page)
    assert re.search(r"\d\d TO \d\d", page)
    assert re.search(r"\d+ years?( \d\d months?)?", page)
    assert re.search(r'<a href="/building/\d+[^"]*">QUEEN&#39;S RD</a>', page)


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_pagination_links_keep_filters_and_db_mode(db):
    resp = get(f"/search?town=BISHAN&price_min=300000&sort=price&order=asc&street=&db={db}")
    nxt = re.search(r'<a rel="next" href="([^"]+)"', resp.text)
    assert nxt and not re.search(r'rel="prev"', resp.text)
    href = nxt.group(1).replace("&amp;", "&")
    qs = parse_qs(urlparse(href).query)
    assert qs == {
        "town": ["BISHAN"],
        "price_min": ["300000"],
        "sort": ["price"],
        "order": ["asc"],
        "page": ["2"],
        "db": [db],
    }
    page2 = get(href)
    assert page2.status_code == 200 and "page 2 of" in page2.text
    prev = re.search(r'<a rel="prev" href="([^"]+)"', page2.text)
    assert prev and parse_qs(urlparse(prev.group(1).replace("&amp;", "&")).query)["page"] == ["1"]


def test_last_page_has_no_next_and_beyond_it_is_empty():
    resp = get("/search?town=BUKIT+TIMAH&page=10000&db=pg")
    assert resp.status_code == 200
    assert "No sales match." in resp.text and 'rel="next"' not in resp.text
    assert re.search(r"[\d,]+ sales, page 10000 of \d+", resp.text)
    assert 'rel="prev"' in resp.text


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_duplicate_pair_appears_twice(db):
    q = "block=18&street=MARINE+TER&month_from=2017-02&month_to=2017-02&flat_type=4+ROOM"
    page = main_of(get(f"/search?{q}&db={db}"))
    assert page.count("$468,000") >= 2
    assert page.count("04 TO 06") >= 2
    assert page.count("88.0") >= 2


@pytest.mark.parametrize("street", ["%", "_", ".*", "' OR 1=1 --"])
@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_injection_strings_are_not_errors(street, db):
    resp = get("/search", params={"street": street, "db": db})
    assert resp.status_code == 200
    assert "No sales match." in resp.text
