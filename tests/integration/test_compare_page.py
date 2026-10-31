"""Compare (S7) through the running web app: validation, both databases, the flat type, lease and selection."""

import os
import re

import httpx
import pytest

from tests.integration.test_saved_page import MODES, make_user  # noqa: F401 (a fixture)
from tests.integration.web import DEMO

BASE = os.environ.get("NEIGHBOAR_URL", "http://127.0.0.1:8000")
pytestmark = pytest.mark.integration

MAIN = re.compile(r'<main id="main" class="page">(.*?)</main>', re.S)
TIMING = re.compile(r"<li>([^<]+): [\d.]+ ms on (PostgreSQL|MongoDB) \(query and fetch\)</li>")


def get(path, **kw):
    kw.setdefault("cookies", DEMO)
    return httpx.get(f"{BASE}{path}", timeout=30, **kw)


def main_of(resp):
    return re.sub(r"\s+", " ", MAIN.search(resp.text).group(1)).strip()


def cells(page, label):
    row = re.search(rf'<tr> ?<th scope="row">{label}.*?</tr>', page)
    return re.findall(r"<td>(.*?)</td>", row.group(0))


@pytest.fixture
def ids(pg_rw):
    """Three located blocks with recent 4-room sales, one with none, and the stub (no completion year)."""
    with_sales = [r[0] for r in pg_rw.execute(
        """
        SELECT t.building_id FROM resale_transaction t JOIN building b USING (building_id)
        WHERE t.flat_type_id = (SELECT flat_type_id FROM flat_type WHERE name = '4 ROOM') AND b.latitude IS NOT NULL
          AND t.txn_month > (SELECT max(txn_month) FROM resale_transaction) - interval '12 months'
        GROUP BY t.building_id ORDER BY count(*) DESC, t.building_id LIMIT 3
        """
    ).fetchall()]  # fmt: skip
    without = pg_rw.execute(
        """
        SELECT b.building_id FROM building b WHERE NOT EXISTS (
            SELECT 1 FROM resale_transaction t WHERE t.building_id = b.building_id
              AND t.flat_type_id = (SELECT flat_type_id FROM flat_type WHERE name = '4 ROOM')
              AND t.txn_month > (SELECT max(txn_month) FROM resale_transaction) - interval '12 months')
        ORDER BY b.building_id LIMIT 1
        """
    ).fetchone()[0]
    stub = pg_rw.execute("SELECT building_id FROM building WHERE NOT in_property_dataset").fetchone()[0]
    pg_rw.rollback()
    return {"sales": with_sales, "without": without, "stub": stub}


def query(*ids, **extra):
    parts = [f"ids={i}" for i in ids] + [f"{k}={v}" for k, v in extra.items()]
    return "/compare?" + "&".join(parts)


@pytest.mark.parametrize("db", MODES)
def test_blocks_side_by_side_with_one_timed_query_each(ids, db):
    a, b, c = ids["sales"]
    resp = get(query(a, b, c, db=db))
    assert resp.status_code == 200
    page = main_of(resp)
    assert len(re.findall(r'<th scope="col"> <a href="/block/\d+">', page)) == 3
    labels = [t[0] for t in TIMING.findall(resp.text)]
    assert labels.count("block profile") == 3 and labels.count("recent prices") == 3
    assert labels.count("town baselines") == 1 and labels.count("food access") == 3
    assert "using 4 room sales" in page  # the most sold flat type across the three
    assert all("$" in cell for cell in cells(page, "Typical price"))


def test_both_databases_render_the_same_comparison(ids):
    a, b, _ = ids["sales"]
    for extra in ({}, {"flat_type": "5+ROOM", "at": "2050"}):
        pages = [main_of(get(query(a, b, ids["without"], ids["stub"], db=db, **extra))) for db in MODES]
        assert pages[0] == pages[1], extra


@pytest.mark.parametrize("db", MODES)
def test_a_block_without_the_flat_type_says_so_and_the_stub_shows_unknown(ids, db):
    page = main_of(get(query(ids["sales"][0], ids["without"], ids["stub"], flat_type="4+ROOM", db=db)))
    prices = cells(page, "Typical price")
    assert "No 4-room sales" in prices[1]
    assert cells(page, "Completed")[2] == "Unknown"


@pytest.mark.parametrize("db", MODES)
def test_the_lease_row_for_a_chosen_year(ids, db):
    a, b, _ = ids["sales"]
    page = main_of(get(query(a, b, at="2050", db=db)))
    assert '<th scope="row">Lease left in 2050</th>' in page
    assert all(re.fullmatch(r"About \d+ years|Run out|Unknown", c) for c in cells(page, "Lease left in 2050"))
    bad = main_of(get(query(a, b, at="1999", db=db)))
    assert "Year must be from" in bad and "Lease left in 1999" not in bad


@pytest.mark.parametrize("db", MODES)
def test_drop_links_keep_the_others_and_stop_at_two(ids, db):
    a, b, c = ids["sales"]
    page = get(query(a, b, c, flat_type="4+ROOM", db=db)).text
    assert f'href="/compare?ids={a}&amp;ids={c}&amp;flat_type=4+ROOM">Drop' in page
    assert ">Drop<" not in get(query(a, b, db=db)).text


@pytest.mark.parametrize(
    "q,status",
    [("/compare", 422), ("/compare?ids=1", 422), ("/compare?ids=1,2,3,4,5", 422), ("/compare?ids=1&ids=x", 422),
     ("/compare?ids=1&ids=2000000000", 404)],
)  # fmt: skip
def test_bad_selections_say_why(q, status):
    resp = get(q)
    assert resp.status_code == status and 'role="alert"' in resp.text and 'href="/shortlist"' in resp.text


@pytest.mark.parametrize("db", MODES)
def test_the_shortlist_offers_selection_once_there_are_two(db, make_user, ids):  # noqa: F811
    browser, _ = make_user(db)
    a, b, _ = ids["sales"]
    browser.post("/saved", db, building_id=str(a), note="")
    assert 'id="compare-form"' not in browser.http.get(f"/shortlist?db={db}").text
    browser.post("/saved", db, building_id=str(b), note="")
    page = browser.http.get(f"/shortlist?db={db}").text
    assert '<form method="get" action="/compare" id="compare-form"' in page
    assert len(re.findall(r'name="ids" value="\d+" form="compare-form"', page)) == 2
