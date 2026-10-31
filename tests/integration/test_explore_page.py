"""Home's guided start and the Explore page through the running web app (task T6)."""

import json
import os
import re
from html import unescape

import httpx
import pytest

from tests.integration.web import DEMO

BASE = os.environ.get("NEIGHBOAR_URL", "http://127.0.0.1:8000")
pytestmark = pytest.mark.integration

MAIN = re.compile(r'<main id="main" class="page">(.*?)</main>', re.S)
TIMING = re.compile(r"<li>([^<]+): [\d.]+ ms on (PostgreSQL|MongoDB) \(query and fetch\)</li>")
MODES = [("pg", "PostgreSQL"), ("mongo", "MongoDB")]
FIT = "/explore?flat_type=4+ROOM&budget=650%2C000"


def get(path, **kw):
    kw.setdefault("cookies", DEMO)
    return httpx.get(f"{BASE}{path}", timeout=30, **kw)


def main_of(resp) -> str:
    return re.sub(r"\s+", " ", unescape(MAIN.search(resp.text).group(1))).strip()


def text_of(resp) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", main_of(resp))).strip()


@pytest.fixture
def located_place(pg_rw):
    """A located block with 4-room sales, its block number and street."""
    return pg_rw.execute(
        """
        SELECT b.building_id, b.block, b.street FROM building b
        WHERE b.latitude IS NOT NULL AND EXISTS (
            SELECT 1 FROM resale_transaction t WHERE t.building_id = b.building_id AND t.flat_type_id = 4)
        ORDER BY b.building_id LIMIT 1
        """
    ).fetchone()


@pytest.mark.parametrize("db,name", MODES)
def test_home_offers_flat_types_with_their_recent_ranges(db, name):
    resp = get(f"/?db={db}")
    assert resp.status_code == 200
    page = text_of(resp)
    assert "How many rooms?" in page and "What is your budget?" in page and "Want to be near somewhere?" in page
    assert re.search(r"4 room About \d+ sqm · usually \$\d+k to \$[\d.]+[kM]", page)
    assert TIMING.findall(resp.text) == [("flat type ranges", name)]
    assert 'action="/explore"' in resp.text and 'action="/block/find"' in resp.text


def test_home_keeps_the_values_it_is_given():
    page = get("/?flat_type=5+ROOM&budget=900%2C000&place_text=17+marine+ter&radius=5000").text
    assert re.search(r'value="5 ROOM" checked', page)
    assert 'value="900,000"' in page and 'value="17 marine ter"' in page
    assert re.search(r'id="radius-5000" name="radius" value="5000" checked', page)


@pytest.mark.parametrize("db,name", MODES)
def test_explore_names_the_best_town_and_lists_blocks(db, name):
    resp = get(f"{FIT}&db={db}")
    assert resp.status_code == 200
    page = text_of(resp)
    assert re.search(
        r"fits your budget best \d+% of its 4-room sales in the last 12 months were at or under \$650,000", page
    )
    assert re.search(r"\d+ blocks? had a typical price within budget", page)
    assert "Towns" in page and "Blocks within budget" in page
    assert [t[0] for t in TIMING.findall(resp.text)] == ["towns that fit", "town baselines", "blocks that fit"]
    assert {t[1] for t in TIMING.findall(resp.text)} == {name}
    assert re.search(r'<li class="block-row">', resp.text)


def test_explore_is_the_same_from_both_databases(located_place):
    for path in (
        FIT,
        f"{FIT}&town=BISHAN&min_lease=50",
        f"{FIT}&place={located_place[0]}&radius=5000",
        f"{FIT}&page=2",
    ):
        pg, mongo = get(f"{path}&db=pg"), get(f"{path}&db=mongo")
        assert pg.status_code == mongo.status_code == 200, path
        same = [re.sub(r'name="db" value="\w+"', 'name="db"', main_of(r)) for r in (pg, mongo)]
        assert same[0] == same[1], path


def test_towns_link_to_their_blocks_and_mark_the_chosen_one():
    page = get(f"{FIT}&town=BISHAN").text
    assert re.search(r'class="town-card selected" href="/explore\?[^"]*town=BISHAN', page)
    assert re.search(r"\d+ blocks? in Bishan fits? your budget", text_of(get(f"{FIT}&town=BISHAN")))


def test_a_budget_below_every_town_names_the_closest():
    page = text_of(get("/explore?flat_type=4+ROOM&budget=300000"))
    assert "No town's typical 4-room price is within $300,000" in page
    assert re.search(r"Closest: [A-Za-z ]+ \(\$[\d,]+ over\)", page)


def test_one_matching_place_limits_blocks_to_its_radius(located_place):
    building_id, block, street = located_place
    resp = get(f"{FIT}&place_text={block}+{street.replace(' ', '+')}&radius=5000")
    page = text_of(resp)
    assert resp.status_code == 200
    assert re.search(r"within 5 km of " + re.escape(block), page, re.I)
    assert "Nearest first" in page and "Towns" not in page.split("Blocks within budget")[0].split("fit")[0]
    assert f'name="place" value="{building_id}"' in resp.text


def test_several_matching_places_are_offered_as_a_choice():
    resp = get(f"{FIT}&place_text=st")
    assert resp.status_code == 200
    assert "Which block did you mean?" in text_of(resp)
    assert re.search(r'href="/explore\?[^"]*place=\d+', resp.text)


@pytest.mark.parametrize(
    "query,message",
    [
        ("flat_type=4+ROOM&budget=abc", "Budget must be an amount in dollars such as 650,000."),
        ("budget=650000", "Flat type is required."),
        ("flat_type=4+ROOM&budget=650000&place_text=no+such+street+anywhere", "No block matches"),
        ("flat_type=4+ROOM&budget=650000&place=2000000000", "Choose the place from the list of blocks."),
        ("flat_type=4+ROOM&budget=650000&radius=3000", "Distance must be 1, 2 or 5 km."),
    ],
)
def test_bad_input_returns_to_the_form_with_a_message(query, message):
    resp = get(f"/explore?{query}")
    assert resp.status_code == 422
    assert message in text_of(resp)
    assert 'action="/explore"' in resp.text


def test_an_unlocated_place_is_reported(pg_rw):
    row = pg_rw.execute(
        "SELECT building_id, block, street FROM building WHERE latitude IS NULL ORDER BY 1 LIMIT 1"
    ).fetchone()
    resp = get(f"{FIT}&place={row[0]}")
    assert resp.status_code == 200
    assert "We do not have a location for" in text_of(resp)


def test_pages_keep_every_filter():
    page = get(f"{FIT}&min_lease=50").text
    nxt = re.search(r'<a rel="next" href="([^"]+)"', page)
    if nxt:
        href = unescape(nxt.group(1))
        assert "flat_type=4+ROOM" in href and "budget=650%2C000" in href and "min_lease=50" in href and "page=2" in href


def test_nothing_near_offers_a_larger_radius(pg_rw):
    """A place whose nearest priced 4-room block is beyond 1 km, if the data has one."""
    row = pg_rw.execute(
        """
        SELECT o.building_id FROM building o
        WHERE o.latitude IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM building b JOIN resale_transaction t ON t.building_id = b.building_id AND t.flat_type_id = 4
            WHERE b.latitude IS NOT NULL
              AND abs(b.latitude - o.latitude) < 0.02 AND abs(b.longitude - o.longitude) < 0.02)
        ORDER BY 1 LIMIT 1
        """
    ).fetchone()
    if row is None:
        pytest.skip("every located block has 4-room sales nearby")
    page = text_of(get(f"/explore?flat_type=4+ROOM&budget=10000000&place={row[0]}&radius=1000"))
    assert "No block within 1 km" in page and "Try 2 km" in page


# ---------------------------------------------------------------- the map (T7)

MAP_JSON = re.compile(r'<script type="application/json" id="map-data">(.*?)</script>', re.S)


def map_of(resp) -> dict | None:
    m = MAP_JSON.search(resp.text)
    return None if m is None else json.loads(m.group(1))


def test_the_map_has_a_pin_for_every_located_block_on_the_page(pg_rw):
    resp = get(FIT)
    data = map_of(resp)
    ids = [
        int(i)
        for i in re.findall(r'<li class="block-row">\s*<div class="block-name">\s*<a href="/block/(\d+)"', resp.text)
    ]
    located = {r[0] for r in pg_rw.execute("SELECT building_id FROM building WHERE latitude IS NOT NULL").fetchall()}
    assert [int(p["href"].rsplit("/", 1)[1]) for p in data["pins"]] == [i for i in ids if i in located]
    assert data["place"] is None
    pin = data["pins"][0]
    assert set(pin) == {"lat", "lon", "label", "name", "detail", "href", "chosen"}
    assert re.fullmatch(r"\d\.\d{6}", pin["lat"]) and re.fullmatch(r"10\d\.\d{6}", pin["lon"])
    assert re.fullmatch(r"\$\d+k|\$[\d.]+M", pin["label"])
    assert 'data-map data-source="map-data"' in resp.text and "vendor/leaflet.js" in resp.text and "map.js" in resp.text


def test_the_map_is_the_same_from_both_databases(located_place):
    for path in (FIT, f"{FIT}&place={located_place[0]}&radius=2000", f"{FIT}&page=2"):
        assert map_of(get(f"{path}&db=pg")) == map_of(get(f"{path}&db=mongo")), path


def test_a_place_puts_its_area_on_the_map(located_place, pg_rw):
    data = map_of(get(f"{FIT}&place={located_place[0]}&radius=5000"))
    lat, lon = pg_rw.execute(
        "SELECT latitude, longitude FROM building WHERE building_id = %s", [located_place[0]]
    ).fetchone()
    assert data["place"] == {"lat": str(lat), "lon": str(lon), "radius_m": 5000}
    assert sum(p["chosen"] for p in data["pins"]) <= 1


def test_no_blocks_and_no_place_means_no_map():
    resp = get("/explore?flat_type=4+ROOM&budget=1000")
    assert map_of(resp) is None and "leaflet" not in resp.text


def test_map_scripts_and_styles_are_served_from_the_site():
    for path in ("/static/vendor/leaflet.js", "/static/vendor/leaflet.css", "/static/map.js"):
        assert get(path).status_code == 200, path
