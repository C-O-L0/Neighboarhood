"""Rankings page through the running web app: validation, both databases, formatting."""

import os
import re

import httpx
import pytest

from tests.equivalence.cases import is_sample
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


@pytest.mark.parametrize(
    "query,message",
    [
        ("flat_type=4+ROOM", "Year is required."),
        ("year=", "Year is required."),
        ("year=%20&flat_type=", "Year is required."),
        ("year=2016", "Year must be a whole number from 2017 to 2026."),
        ("year=2027", "Year must be a whole number from 2017 to 2026."),
        ("year=abc", "Year must be a whole number from 2017 to 2026."),
        ("year=2022.5", "Year must be a whole number from 2017 to 2026."),
        ("year=2022&flat_type=9+ROOM", "Flat type must be one of the listed flat types."),
        ("year=2022&flat_type=STUDIO+APARTMENT", "Flat type must be one of the listed flat types."),
        ("year=2022&flat_type=4+room", "Flat type must be one of the listed flat types."),
    ],
)
@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_bad_input_is_422_with_a_message_and_no_query(query, message, db):
    resp = get(f"/rankings?{query}&db={db}")
    assert resp.status_code == 422
    assert 'role="alert"' in resp.text and message in main_of(resp)
    assert "<form" in resp.text
    assert not TIMING.findall(resp.text)


def test_first_visit_shows_the_empty_form():
    resp = get("/rankings")
    assert resp.status_code == 200
    assert "<form" in resp.text and 'role="alert"' not in resp.text and "<table" not in resp.text
    assert not TIMING.findall(resp.text)


def test_submitted_values_are_echoed_and_escaped():
    page = get('/rankings?year="><script>alert(1)</script>&flat_type=4+ROOM').text
    assert "<script>alert(1)</script>" not in page


@pytest.mark.parametrize("db,name", [("pg", "PostgreSQL"), ("mongo", "MongoDB")])
def test_rankings_shows_tables_labels_and_timings(db, name):
    resp = get(f"/rankings?year=2022&db={db}")
    assert resp.status_code == 200
    assert TIMING.findall(resp.text) == [("rankings", name)]
    page = main_of(resp)
    assert "Typical price" in page and "Island typical price" in page and "Above island typical price" in page
    assert 'href="/glossary#typical-price"' in page and "median*" not in page.lower()
    assert page.count("<table") >= 2  # one table per flat type
    assert re.search(r"\$\d{1,3}(,\d{3})+\.\d{2}", page)
    assert "<td>Yes</td>" in page and "<td>No</td>" in page


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_blank_flat_type_means_all_types(db):
    blank = get(f"/rankings?year=2022&flat_type=&db={db}")
    none = get(f"/rankings?year=2022&db={db}")
    assert blank.status_code == none.status_code == 200
    assert ROWS.findall(blank.text) == ROWS.findall(none.text)


def test_both_databases_render_the_same_tables():
    q = "/rankings?year=2022"
    pg, mongo = get(q + "&db=pg"), get(q + "&db=mongo")
    assert ROWS.findall(pg.text) == ROWS.findall(mongo.text) != []


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_single_flat_type_has_one_table(db):
    page = main_of(get(f"/rankings?year=2022&flat_type=5+ROOM&db={db}"))
    assert page.count("<table") == 1 and "5 ROOM in 2022" in page


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_flat_type_without_sales_says_so(db, pg_rw):
    if not is_sample(pg_rw):
        pytest.skip("every resale flat type has sales in every year on the full data")
    page = main_of(get(f"/rankings?year=2022&flat_type=1+ROOM&db={db}"))
    assert "No sales match." in page and "<table" not in page
