"""The price check (S4, UC2) through the running web app: form, block choice, levels, verdict, states and saving."""

import html
import os
import re
from urllib.parse import urlencode

import httpx
import pytest

from tests.integration.test_saved_page import make_user  # noqa: F401 (a fixture)
from tests.integration.web import DEMO

BASE = os.environ.get("NEIGHBOAR_URL", "http://127.0.0.1:8000")
pytestmark = pytest.mark.integration

MAIN = re.compile(r'<main id="main" class="page">(.*?)</main>', re.S)
TIMING = re.compile(r"<li>([^<]+): [\d.]+ ms on (PostgreSQL|MongoDB) \(query and fetch\)</li>")
MODES = ["pg", "mongo"]


def get(path, **kw):
    kw.setdefault("cookies", DEMO)
    return httpx.get(f"{BASE}{path}", timeout=30, **kw)


def check(db="pg", **values):
    return get("/price-check?" + urlencode({**values, "db": db}))


def main_of(resp):
    return re.sub(r"\s+", " ", MAIN.search(resp.text).group(1)).strip()


def verdict_of(resp):
    return re.search(r'<h2 id="verdict-h" class="verdict-line">([^<]+)</h2>', resp.text).group(1)


@pytest.fixture
def blocks(pg_rw):
    """Blocks with many, few and no 4-room sales in the price check window, by their typed name."""
    rows = pg_rw.execute(
        """
        WITH w AS (SELECT max(txn_month) - interval '23 months' AS start FROM resale_transaction)
        SELECT b.building_id, b.block || ' ' || b.street,
               count(*) FILTER (WHERE ft.name = '4 ROOM' AND t.txn_month >= w.start) AS n
        FROM building b CROSS JOIN w
        LEFT JOIN resale_transaction t ON t.building_id = b.building_id
        LEFT JOIN flat_type ft ON ft.flat_type_id = t.flat_type_id
        GROUP BY b.building_id, b.block, b.street
        ORDER BY n DESC, b.building_id
        """
    ).fetchall()
    many = next(r for r in rows if r[2] >= 5)
    few = next(r for r in rows if 0 < r[2] < 5)
    return {"many": many, "few": few}


def test_the_menu_links_to_the_price_check():
    page = get("/").text
    assert '<a href="/price-check">Check a price</a>' in page and 'class="side-card" href="/price-check"' in page


@pytest.mark.parametrize("db", MODES)
def test_the_form_opens_empty_without_queries(db):
    resp = get(f"/price-check?db={db}")
    assert resp.status_code == 200
    assert 'name="place_text"' in resp.text and 'role="alert"' not in resp.text and not TIMING.findall(resp.text)
    assert '<option value="49-51">49 TO 51</option>' in resp.text


@pytest.mark.parametrize("db", MODES)
def test_the_block_profile_opens_it_prefilled(blocks, db):
    profile = get(f"/block/{blocks['many'][0]}?db={db}").text
    link = html.unescape(re.search(r'href="(/price-check\?[^"]+)">Check a price</a>', profile).group(1))
    resp = get(link)
    assert resp.status_code == 200 and 'role="alert"' not in resp.text
    assert f'name="place" value="{blocks["many"][0]}"' in resp.text
    assert re.search(r'name="place_text" value="[^"]+"', resp.text)
    assert [t[0] for t in TIMING.findall(resp.text)] == ["block"]


@pytest.mark.parametrize("db", MODES)
def test_enough_sales_in_the_block_give_a_verdict_on_the_block(blocks, db):
    resp = check(db, place_text=blocks["many"][1], flat_type="4 ROOM", area="95", price="1,000,000")
    assert resp.status_code == 200
    assert re.fullmatch(
        r"This asking price is (higher|lower) than (all \d+|\d+% of) similar sales in [^.]+\.", verdict_of(resp)
    )
    page = main_of(resp)
    assert "Not enough sales" not in page and "across all storeys" in page
    assert [t[0] for t in TIMING.findall(resp.text)] == ["block lookup", "comparables (block)"]
    assert "<th>Block</th>" not in page and "Save with this price" not in page  # signed out
    assert re.search(r'<a href="/login\?next=%2Fprice-check%3F[^"]+">Sign in</a> to save', page)


@pytest.mark.parametrize("db", MODES)
def test_few_sales_in_the_block_widen_and_say_so(blocks, db):
    resp = check(db, place_text=blocks["few"][1], flat_type="4 ROOM", area="95", price="600000")
    page = main_of(resp)
    labels = [t[0] for t in TIMING.findall(resp.text)]
    assert labels[:2] == ["block lookup", "comparables (block)"] and len(labels) >= 3
    assert re.search(r"Not enough sales in [^(]+ \([1-4]\)", page)
    assert "<th>Block</th>" in page


def test_both_databases_give_the_same_page(blocks):
    for name in ("many", "few"):
        for storey in ("", "04-06"):
            values = {"place_text": blocks[name][1], "flat_type": "4 ROOM", "storey": storey, "area": "95.5"}
            pages = [main_of(check(db, **values, price="$750,000")) for db in MODES]
            assert pages[0] == pages[1], (name, storey)


@pytest.mark.parametrize("db", MODES)
def test_no_comparables_anywhere_points_to_the_trend(blocks, db):
    resp = check(db, place_text=blocks["many"][1], flat_type="MULTI-GENERATION", storey="49-51", area="160", price="1")
    assert resp.status_code == 422  # the price is below the minimum: checked first
    resp = check(db, place_text=blocks["many"][1], flat_type="MULTI-GENERATION", storey="49-51", area="160",
                 price="1000000")  # fmt: skip
    page = main_of(resp)
    assert "so a fair comparison is not possible" in page and 'href="/trends?town=' in page
    assert "try across all storeys" in page and 'id="verdict-h"' not in page


@pytest.mark.parametrize("db", MODES)
def test_a_street_without_a_block_number_offers_a_choice(db):
    resp = check(db, place_text="bishan st 2", flat_type="4 ROOM", area="95", price="700000")
    assert resp.status_code == 200 and "Which block did you mean?" in resp.text
    first = html.unescape(re.search(r'<li><a href="(/price-check\?[^"]+)">', resp.text).group(1))
    assert "place=" in first and "area=95" in first and "price=700000" in first
    chosen = get(first)
    assert 'id="verdict-h"' in chosen.text or "fair comparison is not possible" in chosen.text
    assert [t[0] for t in TIMING.findall(chosen.text)][0] == "block"


@pytest.mark.parametrize("db", MODES)
def test_an_unknown_block_suggests_blocks_on_similar_streets(db):
    resp = check(db, place_text="9999 bishan street 22", flat_type="4 ROOM", area="95", price="700000")
    assert resp.status_code == 422
    page = main_of(resp)
    assert "No block 9999 on a street like" in page and "Bishan St 22" in page
    resp = check(db, place_text="1 no such road", flat_type="4 ROOM", area="95", price="700000")
    assert resp.status_code == 422 and "No block matches" in main_of(resp)


@pytest.mark.parametrize("db", MODES)
def test_edited_text_wins_over_an_earlier_choice(blocks, db):
    resp = check(db, place=str(blocks["many"][0]), place_text="9999 nowhere rd", flat_type="4 ROOM", area="95",
                 price="700000")  # fmt: skip
    assert resp.status_code == 422 and "No block matches" in main_of(resp)


@pytest.mark.parametrize("db", MODES)
def test_bad_input_is_422_with_every_message_and_no_query(db):
    resp = check(db, place_text="", flat_type="", area="abc", price="lots")
    assert resp.status_code == 422
    page = main_of(resp)
    for message in (
        "Flat type is required.",
        "Floor area must be a number of square metres",
        "Asking price must be an amount in dollars",
    ):
        assert message in page
    assert 'value="abc"' in page and 'value="lots"' in page
    assert not TIMING.findall(resp.text)


def test_input_is_escaped(blocks):
    page = check(place_text='"><script>x</script>', flat_type="4 ROOM", area="95", price="700000").text
    assert "<script>x</script>" not in page


@pytest.mark.parametrize("db", MODES)
def test_a_signed_in_buyer_can_save_with_the_price(blocks, db, make_user):  # noqa: F811 (the imported fixture)
    browser, _ = make_user(db)
    query = urlencode({"place_text": blocks["many"][1], "flat_type": "4 ROOM", "storey": "04-06", "area": "95",
                       "price": "700000", "db": db})  # fmt: skip
    page = browser.http.get(f"/price-check?{query}").text
    note = html.unescape(re.search(r'<textarea id="note" name="note"[^>]*>([^<]*)</textarea>', page).group(1))
    assert note == "Asking $700,000 · 4 room · 04 TO 06 · 95 sqm"
    resp = browser.post("/saved", db, building_id=str(blocks["many"][0]), note=note, back="building")
    assert resp.headers["location"].startswith(f"/block/{blocks['many'][0]}?")
    assert "is already on your shortlist" in browser.http.get(f"/price-check?{query}").text
