"""Interface through the running web app: navigation, charts, accessibility (task 2.5)."""

import json
import os
import re
from html.parser import HTMLParser

import httpx
import pytest

from app.services.trends import add_months
from tests.equivalence.cases import has_gap, trend_series
from tests.integration.web import DEMO

BASE = os.environ.get("NEIGHBOAR_URL", "http://127.0.0.1:8000")
pytestmark = pytest.mark.integration

TIMING = re.compile(r"<li>([^<]+): [\d.]+ ms on (PostgreSQL|MongoDB) \(query and fetch\)</li>")
TERM = 'href="/glossary#typical-price"'
NAV = ["/", "/price-check", "/search", "/trends", "/glossary"]
EXPLORE = "/explore?flat_type=4+ROOM&budget=650000"
PRICE_CHECK = "/price-check?place_text=265+Bishan+St+24&flat_type=4+ROOM&area=104&price=700%2C000"
JSON_TAG = re.compile(r'<script type="application/json" id="([\w-]+)">(.*?)</script>', re.S)


def get(path, **kw):
    kw.setdefault("cookies", DEMO)
    return httpx.get(f"{BASE}{path}", timeout=30, **kw)


def chart_json(resp, ident):
    found = {i: body for i, body in JSON_TAG.findall(resp.text)}
    return found[ident]


@pytest.fixture(scope="module")
def building_path():
    page = get("/search?db=pg").text
    return re.search(r'href="(/block/\d+)"', page).group(1)


@pytest.fixture
def gap_query(pg_rw):
    """The first series in (town, flat type) order with a gap after its first year (as task 2.3 AC2)."""
    for (town, flat_type), months in trend_series(pg_rw).items():
        have = set(months)
        if has_gap(months) and any(add_months(m, -12) not in have for m in months[12:]):
            return f"/trends?town={town.replace(' ', '+').replace('/', '%2F')}&flat_type={flat_type.replace(' ', '+')}"
    pytest.fail("the loaded data has no series with a gap after its first year")


def pages(building_path, gap_query):
    return [
        ("/", True),
        ("/explore?flat_type=4+ROOM&budget=650000", True),
        ("/search?town=BISHAN", True),
        (gap_query, True),
        (building_path, True),
        (PRICE_CHECK, True),
        ("/demo", False),
    ]


@pytest.mark.parametrize("db,name", [("pg", "PostgreSQL"), ("mongo", "MongoDB")])
def test_every_page_has_navigation_demo_bar_and_timing(db, name, building_path, gap_query):
    for path, queries in pages(building_path, gap_query):
        resp = get(path + ("&" if "?" in path else "?") + f"db={db}")
        assert resp.status_code == 200, path
        for target in NAV:
            assert f'href="{target}"' in resp.text, (path, target)
        assert f'<strong id="db-mode">{name}</strong>' in resp.text
        assert resp.text.count('aria-current="true"') == 1
        assert resp.text.count('aria-current="page"') <= 1
        assert 'href="/demo"' in resp.text
        if queries:
            assert TIMING.findall(resp.text), path


def test_without_demo_mode_there_is_no_switch_and_no_timing(building_path):
    for path in ("/", "/search?town=BISHAN", building_path):
        resp = get(path, cookies={})
        assert resp.status_code == 200, path
        assert 'id="db-mode"' not in resp.text and not TIMING.findall(resp.text), path
        assert 'action="/demo"' in resp.text and 'aria-pressed="false"' in resp.text, path


def test_current_menu_item_is_marked():
    assert re.search(r'<a href="/search" aria-current="page">Advanced search</a>', get("/search").text)
    assert re.search(r'<a href="/" aria-current="page">Find a home</a>', get("/").text)
    assert 'aria-current="page"' not in get("/glossary").text


def test_chart_script_is_served_from_the_site():
    resp = get("/static/vendor/chart.umd.js")
    assert resp.status_code == 200 and resp.content.startswith(b"/*!\n * Chart.js v4.4.7")
    assert get("/static/charts.js").status_code == 200


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_trends_chart_json_covers_every_month_with_null_for_gaps(db, gap_query):
    resp = get(f"{gap_query}&db={db}")
    data = json.loads(chart_json(resp, "trends-data"))
    assert set(data) == {"months", "median", "rolling_median", "n"}
    rows = re.findall(r'<tr><td class="nowrap">([A-Z][a-z]{2} \d{4})</td>', resp.text)
    first, last = rows[0], rows[-1]
    names = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    index = {m: i for i, m in enumerate(names)}

    def pos(label):
        mon, year = label.split()
        return int(year) * 12 + index[mon]

    span = pos(last) - pos(first) + 1
    assert span > len(rows)  # the series has a gap
    assert all(len(data[k]) == span for k in data)
    missing = [i for i, v in enumerate(data["median"]) if v is None]
    assert len(missing) == span - len(rows) > 0
    for k in ("rolling_median", "n"):
        assert [i for i, v in enumerate(data[k]) if v is None] == missing
    assert re.fullmatch(r"\d{4}-\d{2}", data["months"][0])
    assert re.fullmatch(r"\d+\.\d{2}", next(v for v in data["median"] if v))


def test_trends_chart_json_is_identical_in_both_databases(gap_query):
    pg, mongo = get(f"{gap_query}&db=pg"), get(f"{gap_query}&db=mongo")
    assert chart_json(pg, "trends-data") == chart_json(mongo, "trends-data")


class Access(HTMLParser):
    def __init__(self):
        super().__init__()
        self.labels, self.fields, self.problems = set(), [], []
        self.in_table = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "label" and a.get("for"):
            self.labels.add(a["for"])
        if tag in ("input", "select", "textarea") and a.get("type") != "hidden":
            self.fields.append(a.get("id"))
        if tag == "table":
            self.in_table = {"caption": False}
            self.tables = getattr(self, "tables", [])
            self.tables.append(self.in_table)
        if tag == "caption" and self.in_table is not None:
            self.in_table["caption"] = True
        if tag == "canvas" and not a.get("aria-label"):
            self.problems.append("canvas without aria-label")


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_accessibility_labels_captions_and_canvas(db, building_path, gap_query):
    paths = [p for p, _ in pages(building_path, gap_query)]
    paths += ["/trends", "/block/find?block=1&street=x"]
    for path in paths:
        resp = get(path + ("&" if "?" in path else "?") + f"db={db}")
        scan = Access()
        scan.feed(resp.text)
        for ident in scan.fields:
            assert ident and ident in scan.labels, (path, ident)
        assert all(t["caption"] for t in getattr(scan, "tables", [])), path
        assert scan.problems == [], path


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_wide_tables_are_wrapped_for_scrolling(db, building_path, gap_query):
    for path, css in (
        ("/search?town=BISHAN", "sales"),
        (gap_query, "trends"),
        (building_path, "sales"),
    ):
        text = get(f"{path}{'&' if '?' in path else '?'}db={db}").text
        wrapped = re.findall(rf'<div class="table-wrap">\s*<table class="{css}">.*?</table>\s*</div>', text, re.S)
        assert len(wrapped) == text.count(f'<table class="{css}">') >= 1, path


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_pages_with_a_typical_price_link_it_to_the_glossary(db, building_path, gap_query):
    for path in (gap_query, EXPLORE, building_path):
        text = get(f"{path}{'&' if '?' in path else '?'}db={db}").text
        assert "Typical price" in text and TERM in text, path
        assert "median*" not in text.lower(), path


def test_glossary_has_an_entry_for_every_linked_term(building_path, gap_query):
    glossary = get("/glossary").text
    for path in (gap_query, EXPLORE, building_path):
        for anchor in re.findall(r'href="/glossary#([\w-]+)"', get(path).text):
            assert f'<dt id="{anchor}">' in glossary, (path, anchor)


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_footer_shows_the_latest_registration_month(db, pg_rw, building_path):
    latest = pg_rw.execute("SELECT max(txn_month) FROM resale_transaction").fetchone()[0]
    label = f"Sales registered up to {latest:%b %Y}"
    for path in ("/", "/glossary", building_path):
        assert label in get(f"{path}?db={db}").text, path
    assert "Sales registered up to" not in get("/api/health").text
