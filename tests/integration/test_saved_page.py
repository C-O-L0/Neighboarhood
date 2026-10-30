"""Saved blocks through the running web app, in both DB modes (task 3.2)."""

import re
import secrets

import pytest

from tests.integration.test_auth import Browser, delete_buyer

pytestmark = pytest.mark.integration

MODES = ("pg", "mongo")
PASSWORD = "correct horse battery"
NOTICE = re.compile(r'<div class="(?:ok|bad) notice" role="status">(.*?)</div>', re.S)
ROUTES = ("/saved", "/saved/note", "/saved/remove")


def notice(resp):
    found = NOTICE.search(resp.text)
    return re.sub(r"\s+", " ", found.group(1)).strip() if found else None


@pytest.fixture
def blocks(pg_rw):
    rows = pg_rw.execute(
        "SELECT building_id FROM building WHERE in_property_dataset ORDER BY building_id LIMIT 3"
    ).fetchall()
    pg_rw.rollback()
    return [r[0] for r in rows]


def buyer_id(mode, email, pg_rw, mongo_app):
    if mode == "pg":
        row = pg_rw.execute("SELECT buyer_id FROM buyer WHERE lower(email) = %s", (email,)).fetchone()
        pg_rw.rollback()
        return row[0]
    return mongo_app.buyers.find_one({"email": email}, {"_id": 1})["_id"]


def saved_ids(mode, bid, pg_rw, mongo_app):
    if mode == "pg":
        rows = pg_rw.execute("SELECT building_id FROM saved_building WHERE buyer_id = %s ORDER BY 1", (bid,))
        ids = [r[0] for r in rows.fetchall()]
        pg_rw.rollback()
        return ids
    return sorted(s["building_id"] for s in mongo_app.buyers.find_one({"_id": bid})["saved"])


def last_active(mode, bid, pg_rw, mongo_app):
    if mode == "pg":
        value = pg_rw.execute("SELECT last_active_at FROM buyer WHERE buyer_id = %s", (bid,)).fetchone()[0]
        pg_rw.rollback()
        return value
    return mongo_app.buyers.find_one({"_id": bid})["last_active_at"]


@pytest.fixture
def make_user(pg_owner, mongo_etl):
    """Factory: register and sign in a fresh buyer in one mode. Deletes every buyer it made."""
    made = []

    def make(mode):
        browser = Browser()
        email = f"test-saved-{secrets.token_hex(6)}@example.com"
        assert browser.post("/register", mode, email=email, password=PASSWORD).status_code == 303
        assert browser.login(mode, email, PASSWORD).status_code == 303
        made.append((browser, email))
        return browser, email

    yield make
    for browser, email in made:
        browser.close()
        delete_buyer(pg_owner, mongo_etl, email)


def follow(browser, resp):
    assert resp.status_code == 303, resp.text[:300]
    return browser.http.get(resp.headers["location"])


@pytest.mark.parametrize("mode", MODES)
def test_signed_out_visitors_are_sent_to_sign_in(mode):
    browser = Browser()
    try:
        for path in ROUTES:
            resp = browser.post(path, mode, building_id="1", note="x")
            assert resp.status_code == 303 and resp.headers["location"] == f"/login?db={mode}"
        resp = browser.http.get(f"/saved?db={mode}")
        assert resp.status_code == 303 and resp.headers["location"] == f"/login?db={mode}"
        assert "Sign in (top right) to save this block." in browser.http.get(f"/building/1?db={mode}").text
    finally:
        browser.close()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("path", ROUTES)
def test_posts_without_a_csrf_token_get_403(mode, path, make_user, blocks):
    browser, _ = make_user(mode)
    resp = browser.http.post(f"{path}?db={mode}", data={"building_id": str(blocks[0])})
    assert resp.status_code == 403


@pytest.mark.parametrize("mode", MODES)
def test_save_edit_and_remove_through_the_pages(mode, make_user, blocks, pg_rw, mongo_app):
    browser, email = make_user(mode)
    bid = buyer_id(mode, email, pg_rw, mongo_app)
    page = browser.http.get(f"/building/{blocks[0]}?db={mode}")
    assert "Save this block" in page.text and "Simulate failure (rollback demonstration)" in page.text

    resp = browser.post("/saved", mode, building_id=str(blocks[0]), note="  near the MRT  ", back="building")
    assert resp.headers["location"].startswith(f"/building/{blocks[0]}?")
    page = follow(browser, resp)
    assert notice(page) == "Block saved."
    assert "near the MRT" in page.text and "Update note" in page.text and "Remove from saved" in page.text
    assert "saved list: " in page.text  # the timing footer

    resp = browser.post("/saved", mode, building_id=str(blocks[0]), note="", back="building")
    assert notice(follow(browser, resp)) == "That block is already in your saved list."
    resp = browser.post("/saved", mode, building_id="2000000000", note="")
    assert notice(follow(browser, resp)) == "There is no such block."

    page = browser.http.get(f"/saved?db={mode}")
    assert page.status_code == 200 and "near the MRT" in page.text and f"/building/{blocks[0]}?db={mode}" in page.text
    assert "saved list: " in page.text

    resp = browser.post("/saved/note", mode, building_id=str(blocks[0]), note="new note")
    assert notice(follow(browser, resp)) == "Note updated."
    assert "new note" in browser.http.get(f"/saved?db={mode}").text
    resp = browser.post("/saved/note", mode, building_id=str(blocks[1]), note="x")
    assert notice(follow(browser, resp)) == "That block is not in your saved list."

    resp = browser.post("/saved/remove", mode, building_id=str(blocks[0]))
    assert notice(follow(browser, resp)) == "Block removed from your saved list."
    resp = browser.post("/saved/remove", mode, building_id=str(blocks[0]))
    assert notice(follow(browser, resp)) == "That block is not in your saved list."
    assert saved_ids(mode, bid, pg_rw, mongo_app) == []


@pytest.mark.parametrize("mode", MODES)
def test_a_note_over_500_characters_is_rejected_with_422(mode, make_user, blocks, pg_rw, mongo_app):
    browser, email = make_user(mode)
    bid = buyer_id(mode, email, pg_rw, mongo_app)
    resp = browser.post("/saved", mode, building_id=str(blocks[0]), note="x" * 501)
    assert resp.status_code == 422 and "at most 500 characters" in resp.text
    assert saved_ids(mode, bid, pg_rw, mongo_app) == []
    assert browser.post("/saved", mode, building_id=str(blocks[0]), note="x" * 500).status_code == 303
    assert browser.post("/saved/note", mode, building_id=str(blocks[0]), note="y" * 501).status_code == 422
    assert browser.post("/saved", mode, building_id="abc", note="").status_code == 422
    assert saved_ids(mode, bid, pg_rw, mongo_app) == [blocks[0]]


@pytest.mark.parametrize("mode", MODES)
def test_the_rollback_demo_changes_nothing(mode, make_user, blocks, pg_rw, mongo_app):
    browser, email = make_user(mode)
    bid = buyer_id(mode, email, pg_rw, mongo_app)
    browser.post("/saved", mode, building_id=str(blocks[0]), note="kept")
    ids_before = saved_ids(mode, bid, pg_rw, mongo_app)
    active_before = last_active(mode, bid, pg_rw, mongo_app)

    resp = browser.post(
        "/saved", mode, building_id=str(blocks[1]), note="demo", simulate_failure="1", back="building"
    )
    text = notice(follow(browser, resp))
    if mode == "pg":
        assert text.startswith("Rolled back: the transaction inserted 1 row into saved_building and updated 1 row")
    else:
        assert text.startswith("Rolled back: the transaction matched 1 buyer document")
    assert saved_ids(mode, bid, pg_rw, mongo_app) == ids_before == [blocks[0]]
    assert last_active(mode, bid, pg_rw, mongo_app) == active_before
    assert 'value="demo"' not in browser.http.get(f"/saved?db={mode}").text  # the rolled-back note

    resp = browser.post("/saved", mode, building_id=str(blocks[1]), note="demo")
    assert notice(follow(browser, resp)) == "Block saved."
    assert saved_ids(mode, bid, pg_rw, mongo_app) == [blocks[0], blocks[1]]
    assert 'value="demo"' in browser.http.get(f"/saved?db={mode}").text


@pytest.mark.parametrize("mode", MODES)
def test_the_buyer_comes_only_from_the_session(mode, make_user, blocks, pg_rw, mongo_app):
    me, my_email = make_user(mode)
    _, other_email = make_user(mode)
    mine, other = (buyer_id(mode, e, pg_rw, mongo_app) for e in (my_email, other_email))
    for path in ("/saved", "/saved/note", "/saved/remove"):
        me.post(path, mode, building_id=str(blocks[0]), note="x", buyer_id=str(other), uid=str(other))
    assert saved_ids(mode, other, pg_rw, mongo_app) == []
    assert saved_ids(mode, mine, pg_rw, mongo_app) == []  # remove and note found nothing to change
    me.post("/saved", mode, building_id=str(blocks[0]), note="x", buyer_id=str(other))
    assert saved_ids(mode, mine, pg_rw, mongo_app) == [blocks[0]]
    assert saved_ids(mode, other, pg_rw, mongo_app) == []
