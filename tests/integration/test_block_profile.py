"""The block profile (S3: UC4, UC6) through the running web app: summary, actions, lease check, map and states."""

import html
import json
import os
import re

import httpx
import pytest

from tests.integration.web import DEMO

BASE = os.environ.get("NEIGHBOAR_URL", "http://127.0.0.1:8000")
pytestmark = pytest.mark.integration

MAIN = re.compile(r'<main id="main" class="page">(.*?)</main>', re.S)
MAP = re.compile(r'<script type="application/json" id="map-data">(.*?)</script>', re.S)
MODES = ["pg", "mongo"]


def get(path, **kw):
    kw.setdefault("cookies", DEMO)
    return httpx.get(f"{BASE}{path}", timeout=30, **kw)


def main_of(resp):
    return re.sub(r"\s+", " ", MAIN.search(resp.text).group(1)).strip()


@pytest.fixture
def ids(pg_rw):
    def one(sql):
        row = pg_rw.execute(sql).fetchone()
        return None if row is None else row[0]

    # "Recent" is the 12 months up to the latest month: later than 12 months before it.
    return {
        # Located, with recent sales of a flat type the town has plenty of, so the headline has tags.
        "recent": one(
            """
            SELECT t.building_id FROM resale_transaction t JOIN building b USING (building_id)
            WHERE t.txn_month > (SELECT max(txn_month) FROM resale_transaction) - interval '12 months'
              AND b.latitude IS NOT NULL AND b.lease_commence_year IS NOT NULL
            GROUP BY t.building_id ORDER BY count(*) DESC, t.building_id LIMIT 1
            """
        ),
        "old_sales_only": one(
            """
            SELECT b.building_id FROM building b
            WHERE EXISTS (SELECT 1 FROM resale_transaction t WHERE t.building_id = b.building_id)
              AND NOT EXISTS (
                SELECT 1 FROM resale_transaction t WHERE t.building_id = b.building_id
                  AND t.txn_month > (SELECT max(txn_month) FROM resale_transaction) - interval '12 months')
            ORDER BY b.building_id LIMIT 1
            """
        ),
        "no_sales": one(
            "SELECT building_id FROM building b WHERE NOT EXISTS"
            " (SELECT 1 FROM resale_transaction t WHERE t.building_id = b.building_id) ORDER BY building_id LIMIT 1"
        ),
        "unlocated": one("SELECT building_id FROM building WHERE latitude IS NULL ORDER BY building_id LIMIT 1"),
        "teban": one("SELECT building_id FROM building WHERE block = '37' AND street = 'TEBAN GDNS RD'"),
    }


@pytest.mark.parametrize("db", MODES)
def test_summary_tags_and_actions_lead_the_page(ids, db):
    page = main_of(get(f"/block/{ids['recent']}?db={db}"))
    lede = re.search(r'<p class="lede">([^<]+)</p>', page).group(1)
    assert re.fullmatch(r"Completed \d{4}, about \d+ years of lease left, \d+ open hawker centres? within 1 km\.", lede)
    assert re.search(r'<p class="profile-tags"><span class="hint">[^<]+ sales here, compared with [^<]+:</span>', page)
    assert re.search(r'<a class="button secondary" href="/trends\?town=[^"&]+&amp;flat_type=[^"&]+">', page)
    assert 'href="/?flat_type=' in page and "place_text=" in page
    assert '<a class="button" href="#shortlist">Save to shortlist</a>' in page


def test_the_actions_lead_to_working_pages(ids):
    page = get(f"/block/{ids['recent']}").text
    for href in re.findall(r'<nav class="actions".*?</nav>', page, re.S)[0].split('href="')[1:]:
        href = html.unescape(href.split('"')[0])
        if href.startswith("/"):
            assert get(href).status_code == 200, href
    near = html.unescape(re.search(r'href="(/\?flat_type=[^"]+)"', page).group(1))
    place = re.search(r'id="place_text" name="place_text" value="([^"]+)"', get(near).text).group(1)
    sales = html.unescape(re.search(r'href="(/search\?[^"]+)">Show all (\d+)</a>', page).group(1))
    assert place and get(sales).status_code == 200


@pytest.mark.parametrize("db", MODES)
def test_the_map_has_the_block_and_its_nearest_open_centres(ids, db):
    page = get(f"/block/{ids['recent']}?db={db}").text
    data = json.loads(MAP.search(page).group(1))
    pins = data["pins"]
    assert pins[0]["chosen"] is True and "href" not in pins[0]
    assert [p.get("kind") for p in pins[1:]] == ["hawker"] * 5
    assert all(re.fullmatch(r"1\.\d{6}", p["lat"]) and re.fullmatch(r"10[34]\.\d{6}", p["lon"]) for p in pins)
    names = re.findall(r'<span class="hawker-name"><strong>([^<]+)</strong>', page)
    assert [html.unescape(n) for n in names] == [p["name"] for p in pins[1:]]
    assert "vendor/leaflet.js" in page and "map.js" in page


def test_the_map_is_the_same_from_both_databases(ids):
    pg, mongo = (json.loads(MAP.search(get(f"/block/{ids['recent']}?db={db}").text).group(1)) for db in MODES)
    assert pg == mongo


@pytest.mark.parametrize("db", MODES)
def test_a_block_without_coordinates_says_so_and_has_no_map(ids, db):
    resp = get(f"/block/{ids['unlocated']}?db={db}")
    assert resp.status_code == 200
    page = main_of(resp)
    assert "Location not available, so there is no map or food access for this block." in page
    assert "location not available, so no food access figures." in page.lower()
    assert not MAP.search(resp.text) and "leaflet.js" not in resp.text


@pytest.mark.parametrize("db", MODES)
def test_a_block_with_no_sales_still_shows_the_rest(ids, db):
    if ids["no_sales"] is None:
        pytest.skip("every loaded block has a sale")
    page = main_of(get(f"/block/{ids['no_sales']}?db={db}"))
    assert "No resale sales recorded for this block." in page
    assert "Latest sales" not in page and "At a glance" in page and "Food nearby" in page
    assert 'class="profile-tags"' not in page


@pytest.mark.parametrize("db", MODES)
def test_a_block_with_only_older_sales_names_the_last_month(ids, db):
    page = main_of(get(f"/block/{ids['old_sales_only']}?db={db}"))
    assert re.search(r"No resale sales recorded in the last 12 months\. The latest was in [A-Z][a-z]{2} \d{4}\.", page)
    assert "Latest sales" in page


@pytest.mark.parametrize("db", MODES)
def test_an_unknown_block_is_a_404_with_a_way_back(db):
    resp = get(f"/block/2000000000?db={db}")
    assert resp.status_code == 404
    assert 'href="/"' in resp.text and 'href="/search"' in resp.text


@pytest.mark.parametrize("db", MODES)
def test_the_lease_check_answers_on_the_page(ids, db):
    base = f"/block/{ids['teban']}?db={db}"  # lease start 1966
    page = main_of(get(f"{base}&at=2040&min_left=30"))
    assert "In 2040, about <strong>25 years</strong> of lease will be left." in page
    assert '<span class="warn">That is below your 30 years.</span>' in page
    page = main_of(get(f"{base}&age=30&at_age=40&min_left=20"))
    assert "at age 40, about <strong>" in page and "That is at least your 20 years." in page
    page = main_of(get(f"{base}&at=2070"))
    assert "In 2070, the lease will have run out." in page


@pytest.mark.parametrize("db", MODES)
def test_a_bad_lease_check_keeps_the_page_and_says_why(ids, db):
    resp = get(f"/block/{ids['teban']}?db={db}&at=1990&min_left=abc")
    assert resp.status_code == 200
    page = main_of(resp)
    assert 'role="alert"' in page and "Year must be from" in page and "Least lease left must be" in page
    assert 'value="1990"' in page and 'value="abc"' in page
    assert '<p class="lease-result" aria-live="polite"></p>' in page


def test_lease_check_input_is_escaped_and_cut(ids):
    page = get(f"/block/{ids['teban']}", params={"at": '"><script>x</script>'}).text
    assert "<script>x</script>" not in page and 'value="&#34;&gt;&lt;s"' in page


def test_the_lease_check_links_to_the_official_cpf_page(ids):
    page = get(f"/block/{ids['teban']}").text
    assert 'href="https://www.cpf.gov.sg/member/home-ownership/using-your-cpf-to-buy-a-home"' in page
    assert "lease.js" in page


@pytest.mark.parametrize("db", MODES)
def test_recent_prices_have_tags_against_the_town(ids, db):
    page = main_of(get(f"/block/{ids['recent']}?db={db}"))
    table = page.split('<table class="recent">')[1].split("</table>")[0]
    assert "<th>Against " in table and "Typical price" in table
    assert re.search(r'<span class="tags">|Too few town sales to compare', table)


def test_signed_out_buyers_are_asked_to_sign_in_to_save(ids):
    page = main_of(get(f"/block/{ids['recent']}"))
    assert re.search(r'<a href="/login">Sign in</a> to save this block to your shortlist', page)


def test_the_rollback_demonstration_is_only_offered_in_demo_mode(ids):
    # Signed out, the save form is not shown at all; the demo checkbox lives inside it (test_saved_page.py).
    page = get(f"/block/{ids['recent']}", cookies={}).text
    assert "Simulate failure" not in page
