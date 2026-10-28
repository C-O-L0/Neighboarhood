"""Interface through the running web app: navigation, charts, accessibility (task 2.5)."""

import json
import os
import re
from html.parser import HTMLParser

import httpx
import pytest

from app.services.trends import add_months
from tests.equivalence.cases import has_gap, trend_series

BASE = os.environ.get("NEIGHBOAR_URL", "http://127.0.0.1:8000")
pytestmark = pytest.mark.integration

TIMING = re.compile(r"<li>([^<]+): [\d.]+ ms on (PostgreSQL|MongoDB) \(query and fetch\)</li>")
FOOTNOTE = "* median: the lower of the two middle prices when n is even"
NAV = ["/", "/search", "/trends", "/rankings", "/food", "/health"]
JSON_TAG = re.compile(r'<script type="application/json" id="([\w-]+)">(.*?)</script>', re.S)


def get(path, **kw):
    return httpx.get(f"{BASE}{path}", timeout=30, **kw)


def chart_json(resp, ident):
    found = {i: body for i, body in JSON_TAG.findall(resp.text)}
    return found[ident]


@pytest.fixture(scope="module")
def building_path():
    page = get("/search?db=pg").text
    return re.search(r'href="(/building/\d+)"', page).group(1)


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
        ("/", False),
        ("/search?town=BISHAN", True),
        (gap_query, True),
        ("/rankings?year=2022", True),
        ("/food?town=BISHAN", True),
        (building_path, True),
        ("/health", False),
    ]


@pytest.mark.parametrize("db,name", [("pg", "PostgreSQL"), ("mongo", "MongoDB")])
def test_every_page_has_navigation_toggle_and_timing(db, name, building_path, gap_query):
    for path, queries in pages(building_path, gap_query):
        resp = get(path + ("&" if "?" in path else "?") + f"db={db}")
        assert resp.status_code == 200, path
        for target in NAV:
            assert f'<a href="{target}">' in resp.text, (path, target)
        assert f'<strong id="db-mode">{name}</strong>' in resp.text
        assert resp.text.count('aria-current="true"') == 1
        if queries:
            assert TIMING.findall(resp.text), path


def test_chart_script_is_served_from_the_site():
    resp = get("/static/vendor/chart.umd.js")
    assert resp.status_code == 200 and resp.content.startswith(b"/*!\n * Chart.js v4.4.7")
    assert get("/static/charts.js").status_code == 200


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_trends_chart_json_covers_every_month_with_null_for_gaps(db, gap_query):
    resp = get(f"{gap_query}&db={db}")
    data = json.loads(chart_json(resp, "trends-data"))
    assert set(data) == {"months", "median", "rolling_median", "n"}
    rows = re.findall(r"<tr><td>([A-Z][a-z]{2} \d{4})</td>", resp.text)
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


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_rankings_chart_json_has_one_element_per_table(db):
    resp = get(f"/rankings?year=2022&db={db}")
    data = json.loads(chart_json(resp, "rankings-data"))
    tables = re.findall(r'<table class="rankings">(.*?)</table>', resp.text, re.S)
    assert len(data) == len(tables) >= 2
    for element, table in zip(data, tables, strict=True):
        assert set(element) == {"flat_type", "towns", "median", "island_median"}
        assert len(element["towns"]) == len(element["median"])
        assert element["towns"] == re.findall(r"<tr><td>\d+</td><td>([^<]+)</td>", table)
    assert resp.text.count("<canvas") == len(data)


def test_rankings_chart_json_is_identical_in_both_databases():
    pg, mongo = get("/rankings?year=2022&db=pg"), get("/rankings?year=2022&db=mongo")
    assert chart_json(pg, "rankings-data") == chart_json(mongo, "rankings-data")


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
    paths += ["/trends", "/rankings", "/building/find?block=1&street=x"]
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
        ("/rankings?year=2022", "rankings"),
        (building_path, "sales"),
    ):
        text = get(f"{path}{'&' if '?' in path else '?'}db={db}").text
        wrapped = re.findall(rf'<div class="table-wrap">\s*<table class="{css}">.*?</table>\s*</div>', text, re.S)
        assert len(wrapped) == text.count(f'<table class="{css}">') >= 1, path


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_pages_with_a_median_show_the_footnote(db, building_path, gap_query):
    for path in (gap_query, "/rankings?year=2022", building_path):
        text = get(f"{path}{'&' if '?' in path else '?'}db={db}").text
        if "median*" in text:
            assert FOOTNOTE in text, path
    assert FOOTNOTE in get(f"{gap_query}&db={db}").text
    assert FOOTNOTE in get(f"/rankings?year=2022&db={db}").text
