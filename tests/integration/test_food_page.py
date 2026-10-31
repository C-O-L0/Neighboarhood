"""Food access through the running web app: the block profile panel (task 3.4), and the retired /food page."""

import os
import re

import httpx
import pytest

from tests.integration.web import DEMO

BASE = os.environ.get("NEIGHBOAR_URL", "http://127.0.0.1:8000")
pytestmark = pytest.mark.integration

MAIN = re.compile(r'<main id="main" class="page">(.*?)</main>', re.S)
TIMING = re.compile(r"<li>([^<]+): [\d.]+ ms on (PostgreSQL|MongoDB) \(query and fetch\)</li>")
PANEL = re.compile(r'<h2 id="food-h">Food nearby</h2>(.*?)</section>', re.S)
MODES = [("pg", "PostgreSQL"), ("mongo", "MongoDB")]


def get(path, **kw):
    kw.setdefault("cookies", DEMO)
    return httpx.get(f"{BASE}{path}", timeout=30, **kw)


def main_of(resp):
    return re.sub(r"\s+", " ", MAIN.search(resp.text).group(1)).strip()


def panel_of(resp):
    return re.sub(r"\s+", " ", PANEL.search(resp.text).group(1)).strip()


@pytest.fixture
def ids(pg_rw):
    def one(sql):
        return pg_rw.execute(sql).fetchone()[0]

    return {
        "geocoded": one("SELECT building_id FROM building WHERE latitude IS NOT NULL ORDER BY building_id LIMIT 1"),
        "ungeocoded": one("SELECT building_id FROM building WHERE latitude IS NULL ORDER BY building_id LIMIT 1"),
        "stub": one("SELECT building_id FROM building WHERE NOT in_property_dataset"),
    }


@pytest.mark.parametrize("db,name", MODES)
def test_building_page_shows_the_five_nearest_and_the_count(ids, db, name):
    resp = get(f"/block/{ids['geocoded']}?db={db}")
    assert resp.status_code == 200
    assert TIMING.findall(resp.text)[:2] == [("block profile", name), ("food access", name)]
    panel = panel_of(resp)
    assert "open hawker" in panel and "within 1 km" in panel
    assert len(re.findall(r"<li>", panel)) == 5
    assert "Under Construction" not in panel


@pytest.mark.parametrize("db,name", MODES)
def test_stub_has_real_food_access(ids, db, name):
    panel = panel_of(get(f"/block/{ids['stub']}?db={db}"))
    assert "Unknown" not in panel.split("<ol")[0]
    assert len(re.findall(r"<li>", panel)) == 5


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_ungeocoded_block_shows_unknown_without_a_database_error(ids, db):
    resp = get(f"/block/{ids['ungeocoded']}?db={db}")
    assert resp.status_code == 200
    assert panel_of(resp) == '<p class="empty">Location not available, so food access is not known.</p>'
    assert "food access" not in [t[0] for t in TIMING.findall(resp.text)]


def test_panel_is_the_same_in_both_modes(ids):
    for kind in ("geocoded", "stub", "ungeocoded"):
        assert panel_of(get(f"/block/{ids[kind]}?db=pg")) == panel_of(get(f"/block/{ids[kind]}?db=mongo"))


def test_unavailable_address_renders_as_unknown(pg_rw):
    row = pg_rw.execute(
        "SELECT b.building_id FROM building b CROSS JOIN LATERAL ("
        " SELECT h.address FROM hawker_centre h WHERE h.status <> 'Under Construction'"
        " ORDER BY 2 * 6378100 * asin(sqrt(sin(radians(h.latitude - b.latitude) / 2) ^ 2 + cos(radians(b.latitude))"
        " * cos(radians(h.latitude)) * sin(radians(h.longitude - b.longitude) / 2) ^ 2)), h.hawker_id LIMIT 1"
        ") n WHERE b.latitude IS NOT NULL AND n.address IS NULL ORDER BY b.building_id LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("no geocoded block has an address-less centre as its nearest")
    for db in ("pg", "mongo"):
        assert '<span class="hint">Unknown' in panel_of(get(f"/block/{row[0]}?db={db}"))


def test_the_retired_food_by_town_page_redirects_home():
    # The town view was a query showcase; buyers get food access on each block profile. Its repo method stays
    # (tests/equivalence/test_food.py and the benchmark's food_town case).
    resp = get("/food?town=BISHAN", follow_redirects=False)
    assert resp.status_code == 308 and resp.headers["location"] == "/"
