"""Behind the scenes (S12, UC8) through the running web app: health, the last page's queries, plans, benchmark."""

import os
import re

import httpx
import pytest

from tests.integration.web import DEMO

BASE = os.environ.get("NEIGHBOAR_URL", "http://127.0.0.1:8000")
pytestmark = pytest.mark.integration

MODES = [("pg", "PostgreSQL"), ("mongo", "MongoDB")]


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE, cookies=DEMO, timeout=60) as c:
        yield c


@pytest.mark.parametrize("db,name", MODES)
def test_it_shows_what_the_last_page_ran_with_its_timings(client, db, name):
    page = client.get(f"/block/214?db={db}").text
    labels = re.findall(r"<li>([^<]+): [\d.]+ ms on", page)
    demo = client.get("/demo").text
    assert f'Behind <a href="/block/214">/block/214</a> in <strong>{name}</strong>' in demo
    assert re.findall(r'<p class="query-head"><strong>([^<]+)</strong>', demo) == labels
    if db == "pg":
        assert "SELECT b.building_id, b.block, b.street" in demo and "Parameters" in demo
    else:
        assert "find on building_profiles" in demo and "aggregate on resale_transactions" in demo
    assert "Plan" not in re.sub(r"Show the query plans", "", demo.split('id="bench-h"')[0])


@pytest.mark.parametrize("db,marker", [("pg", "Execution Time:"), ("mongo", "winning plan:")])
def test_plans_on_request(client, db, marker):
    client.get(f"/block/214?db={db}")
    demo = client.get("/demo?plans=1").text
    assert marker in demo and "Could not explain" not in demo


def test_opening_demo_keeps_the_page_it_describes_and_a_new_page_replaces_it(client):
    client.get("/block/214?db=pg")
    client.get("/demo")
    assert "/block/214</a>" in client.get("/demo").text
    client.get("/trends?town=BISHAN&flat_type=4+ROOM&db=pg")
    demo = client.get("/demo").text
    assert "/trends?town=BISHAN&amp;flat_type=4+ROOM</a>" in demo and "/block/214</a>" not in demo


def test_each_session_sees_only_its_own_page(client):
    client.get("/block/214?db=pg")
    with httpx.Client(base_url=BASE, cookies=DEMO, timeout=30) as other:
        assert "Open any page that looks something up" in other.get("/demo").text


def test_outside_demo_mode_nothing_is_recorded_or_shown():
    with httpx.Client(base_url=BASE, timeout=30) as plain:
        plain.get("/block/214")
        page = plain.get("/demo").text
    assert "Turn on demo mode in the footer" in page and "query-card" not in page


def test_health_and_the_benchmark(client):
    page = client.get("/demo").text
    assert page.count('<span class="badge fit">Up</span>') == 2
    assert "<code>app_rw</code>" in page and "<code>app</code>" in page and "rs0 (primary)" in page
    assert re.search(r"Run <strong>[\w-]+</strong> on the full data", page)
    assert "<code>trends_series</code>" in page
    for src in re.findall(r'<img src="(/demo/charts/\w+\.svg)"', page):
        resp = client.get(src)
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("image/svg+xml")
    assert client.get("/demo/charts/raw.svg").status_code == 404
    assert client.get("/demo/charts/..%2Fsummary.svg").status_code == 404


@pytest.mark.parametrize("old,new", [("/health", "/demo"), ("/rankings?year=2022", "/"), ("/food", "/")])
def test_retired_pages_redirect(client, old, new):
    resp = client.get(old, follow_redirects=False)
    assert resp.status_code == 308 and resp.headers["location"] == new


def test_the_demo_bar_links_here_and_the_menu_lost_rankings_and_food(client):
    page = client.get("/").text
    assert '<a class="demo-link" href="/demo">Behind the scenes</a>' in page
    assert 'href="/rankings"' not in page and 'href="/food"' not in page
