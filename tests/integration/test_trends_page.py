"""Town trend (S5) through the running web app: validation, summary, both databases, formatting."""

import os
import re

import httpx
import pytest

from tests.integration.web import DEMO

BASE = os.environ.get("NEIGHBOAR_URL", "http://127.0.0.1:8000")
pytestmark = pytest.mark.integration

MAIN = re.compile(r'<main id="main" class="page">(.*?)</main>', re.S)
TIMING = re.compile(r"<li>([^<]+): [\d.]+ ms on (PostgreSQL|MongoDB) \(query and fetch\)</li>")
ROWS = re.compile(r"<tbody>(.*?)</tbody>", re.S)
SERIES = "town=BISHAN&flat_type=4+ROOM"


def get(path, **kw):
    kw.setdefault("cookies", DEMO)
    return httpx.get(f"{BASE}{path}", timeout=30, **kw)


def main_of(resp):
    return re.sub(r"\s+", " ", MAIN.search(resp.text).group(1)).strip()


@pytest.mark.parametrize(
    "query,message",
    [
        ("flat_type=4+ROOM", "Town is required."),
        ("town=BISHAN", "Flat type is required."),
        ("town=&flat_type=", "Town is required."),
        ("town=%20&flat_type=4+ROOM", "Town is required."),
        ("town=ATLANTIS&flat_type=4+ROOM", "Town must be one of the listed towns."),
        ("town=bishan&flat_type=4+ROOM", "Town must be one of the listed towns."),
        ("town=BISHAN&flat_type=9+ROOM", "Flat type must be one of the listed flat types."),
        ("town=BISHAN&flat_type=STUDIO+APARTMENT", "Flat type must be one of the listed flat types."),
        (f"{SERIES}&month_from=2024-3", "From month must look like 2024-03."),
        (f"{SERIES}&month_from=2024-13", "From month must look like 2024-03."),
        (f"{SERIES}&month_to=abc", "To month must look like 2024-03."),
        (f"{SERIES}&month_from=2024-05&month_to=2024-04", "From month must not be after to month."),
    ],
)
@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_bad_input_is_422_with_a_message_and_no_query(query, message, db):
    resp = get(f"/trends?{query}&db={db}")
    assert resp.status_code == 422
    assert 'role="alert"' in resp.text and message in main_of(resp)
    assert "<form" in resp.text
    assert not TIMING.findall(resp.text)


def test_first_visit_shows_the_empty_form():
    resp = get("/trends")
    assert resp.status_code == 200
    assert "<form" in resp.text and 'role="alert"' not in resp.text and "<table" not in resp.text
    assert not TIMING.findall(resp.text)


def test_submitted_values_are_echoed_and_escaped():
    page = get('/trends?town="><script>alert(1)</script>&flat_type=4+ROOM').text
    assert "<script>alert(1)</script>" not in page


@pytest.mark.parametrize("db,name", [("pg", "PostgreSQL"), ("mongo", "MongoDB")])
def test_trends_shows_table_labels_and_timings(db, name):
    resp = get(f"/trends?{SERIES}&db={db}")
    assert resp.status_code == 200
    assert TIMING.findall(resp.text) == [("trends", name)]
    page = main_of(resp)
    assert "Typical price" in page and "12-month typical price" in page and "<th>A year earlier</th>" in page
    assert 'href="/glossary#typical-price"' in page and "median*" not in page.lower()
    assert re.search(r'<td class="nowrap">[A-Z][a-z]{2} \d{4}</td>', page)
    assert re.search(r"<td>\$\d{1,3}(,\d{3})+</td>", page)
    assert re.search(r"-?\d+\.\d%", page)  # a YoY percentage


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_blank_bounds_equal_no_bounds(db):
    blank = get(f"/trends?{SERIES}&month_from=&month_to=&db={db}")
    none = get(f"/trends?{SERIES}&db={db}")
    assert blank.status_code == none.status_code == 200
    assert ROWS.search(blank.text).group(1) == ROWS.search(none.text).group(1)


def test_both_databases_render_the_same_table():
    q = f"/trends?{SERIES}&month_from=2020-06&month_to=2023-02"
    pg, mongo = get(q + "&db=pg"), get(q + "&db=mongo")
    assert ROWS.search(pg.text).group(1) == ROWS.search(mongo.text).group(1)
    first = re.search(r"<tr><td[^>]*>([^<]+)</td>", ROWS.search(pg.text).group(1)).group(1)
    assert first == "Jun 2020"


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_range_without_sales_says_so(db):
    page = main_of(get(f"/trends?{SERIES}&month_from=2999-01&db={db}"))
    assert "No 4-room sales in Bishan in this period." in page and "<table" not in page
    assert 'id="summary-h"' not in page


SUMMARY = re.compile(r'<h2 id="summary-h" class="verdict-line">([^<]+)</h2>')


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_a_summary_sentence_leads_the_results(db):
    resp = get(f"/trends?{SERIES}&db={db}")
    sentence = SUMMARY.search(resp.text).group(1)
    assert re.fullmatch(
        r"4 room prices in Bishan are (up \d+\.\d%|down \d+\.\d%|unchanged) on a year earlier\.", sentence
    )
    page = main_of(resp)
    assert re.search(r"over the 12 months to [A-Z][a-z]{2} \d{4}: \$[\d,]+, against \$[\d,]+ for the 12 months", page)
    assert page.index('id="summary-h"') < page.index("<canvas")


def test_the_summary_is_the_same_from_both_databases():
    for query in (SERIES, "town=BUKIT+TIMAH&flat_type=5+ROOM", f"{SERIES}&month_to=2022-06"):
        pg, mongo = (main_of(get(f"/trends?{query}&db={db}")).split("<figure")[0] for db in ("pg", "mongo"))
        assert pg == mongo, query


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_without_a_year_earlier_in_range_there_is_no_change(db):
    resp = get(f"/trends?{SERIES}&month_from=2026-01&db={db}")
    sentence = SUMMARY.search(resp.text).group(1)
    assert sentence.startswith("The typical 4-room price in Bishan over the 12 months to ")
    assert re.search(r"No year-on-year change: [A-Z][a-z]{2} 2025 is before the range shown\.", main_of(resp))


def test_the_form_says_all_months_and_the_table_starts_collapsed():
    page = get(f"/trends?{SERIES}").text
    assert page.count('placeholder="All months"') == 2
    assert re.search(
        r'<details class="more">\s*<summary>Show the monthly table \(\d+ months with sales\)</summary>', page
    )
    assert '<details class="more" open' not in page


def test_the_legend_draws_the_rolling_line_dashed():
    page = get(f"/trends?{SERIES}").text
    assert 'class="legend-rolling"' in page and 'class="legend-median"' in page and 'class="legend-sales"' in page
    css = get("/static/app.css").text
    assert re.search(r"\.legend-rolling \{[^}]*stroke-dasharray", css)
    js = get("/static/charts.js").text
    assert "legend: { display: false }" in js and "borderDash: [6, 3]" in js
