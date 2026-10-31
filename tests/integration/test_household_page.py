"""The shortlist (S6) and household (S8) pages through the running web app, in both DB modes (task T12)."""

import re

import pytest

from tests.integration.test_auth import Browser
from tests.integration.test_saved_page import (  # noqa: F401 (fixtures)
    MODES,
    blocks,
    follow,
    make_user,
    notice,
)

pytestmark = pytest.mark.integration

COULD_NOT = "We could not invite that email."
HOUSEHOLD_POSTS = (
    "/household/invite",
    "/household/cancel",
    "/household/accept",
    "/household/decline",
    "/household/leave",
)


def title(page) -> str:
    return re.search(r"<h1>([^<]+)</h1>", page.text).group(1)


def menu_count(page) -> int:
    return int(re.search(r'<span class="menu-count">(\d+)</span>', page.text).group(1))


def pair(make_user, mode, blocks):  # noqa: F811
    """Sam with two saved blocks and Alex with one shared and one new; returns their browsers and emails."""
    sam, sam_email = make_user(mode)
    alex, alex_email = make_user(mode)
    for b in blocks[:2]:
        assert sam.post("/saved", mode, building_id=str(b), note="from Sam").status_code == 303
    for b in blocks[1:3]:
        assert alex.post("/saved", mode, building_id=str(b), note="").status_code == 303
    return sam, sam_email, alex, alex_email


@pytest.mark.parametrize("mode", MODES)
def test_signed_out_pages_send_you_to_sign_in_and_back(mode, make_user):  # noqa: F811
    browser, email = make_user(mode)
    browser.http.post(f"/logout?db={mode}", data={"csrf_token": browser.token(mode)})
    for path in ("/household", "/shortlist"):
        resp = browser.http.get(f"{path}?db={mode}")
        assert resp.status_code == 303 and resp.headers["location"] == f"/login?next=%2F{path[1:]}&db={mode}"
    page = browser.http.get(f"/login?next=/household&db={mode}")
    assert '<input type="hidden" name="next" value="/household">' in page.text
    resp = browser.post("/login", mode, email=email, password="correct horse battery", next="/household")
    assert resp.status_code == 303 and resp.headers["location"] == f"/household?db={mode}"


@pytest.mark.parametrize(
    "next_value", ["//evil.example/x", "https://evil.example", "/\\evil", "/block/1?db=mongo&x=1", ""]
)
def test_sign_in_only_returns_to_a_local_path(make_user, next_value):  # noqa: F811
    browser, email = make_user("pg")
    browser.http.post("/logout?db=pg", data={"csrf_token": browser.token("pg")})
    resp = browser.post("/login", "pg", email=email, password="correct horse battery", next=next_value)
    location = resp.headers["location"]
    expected = "/block/1?x=1&db=pg" if next_value.startswith("/block") else "/account?db=pg"
    assert location == expected


@pytest.mark.parametrize("mode", MODES)
def test_invite_accept_with_merge_then_leave(mode, make_user, blocks):  # noqa: F811
    sam, sam_email, alex, alex_email = pair(make_user, mode, blocks)

    resp = sam.post("/household/invite", mode, email=alex_email.upper())
    page = follow(sam, resp)
    assert notice(page) == "Invitation sent." and "Cancel invitation" in page.text
    assert "Waiting for" in sam.http.get(f"/shortlist?db={mode}").text

    shortlist = alex.http.get(f"/shortlist?db={mode}")
    assert "invited you to share a shortlist" in shortlist.text and 'class="avatar-dot"' in shortlist.text
    assert "Your 2 saved blocks" in shortlist.text and title(shortlist) == "Your shortlist"
    inviter = re.search(r'name="inviter_id" value="(\d+)"', shortlist.text).group(1)
    resp = alex.post("/household/accept", mode, inviter_id=inviter, merge="1")
    page = follow(alex, resp)
    assert notice(page) == "You now share a shortlist. 1 block of yours was added."
    assert "You share a shortlist with" in page.text

    for who in (sam, alex):
        page = who.http.get(f"/shortlist?db={mode}")
        assert title(page) == "Our shortlist" and menu_count(page) == 3
        assert "by you" in page.text and "Note by" in page.text
    sam_view = sam.http.get(f"/shortlist?db={mode}").text
    assert re.search(r"saved \d{1,2} [A-Z][a-z]{2} \d{4} by (?!you)", sam_view)  # Alex's block names Alex

    resp = sam.post("/household/leave", mode)
    assert notice(follow(sam, resp)).startswith("You left the household.")
    assert menu_count(sam.http.get(f"/shortlist?db={mode}")) == 0
    kept = alex.http.get(f"/shortlist?db={mode}")
    assert title(kept) == "Your shortlist" and menu_count(kept) == 3


@pytest.mark.parametrize("mode", MODES)
def test_accept_without_merging_drops_your_own_blocks(mode, make_user, blocks):  # noqa: F811
    sam, _, alex, alex_email = pair(make_user, mode, blocks)
    sam.post("/household/invite", mode, email=alex_email)
    inviter = re.search(r'name="inviter_id" value="(\d+)"', alex.http.get(f"/household?db={mode}").text).group(1)
    page = follow(alex, alex.post("/household/accept", mode, inviter_id=inviter, merge="0"))
    assert notice(page) == "You now share a shortlist. 1 block of yours was left out."
    assert menu_count(alex.http.get(f"/shortlist?db={mode}")) == 2


@pytest.mark.parametrize("mode", MODES)
def test_unknown_email_and_taken_partner_get_the_same_message(mode, make_user, blocks):  # noqa: F811
    sam, sam_email, alex, alex_email = pair(make_user, mode, blocks)
    third, _ = make_user(mode)
    assert notice(follow(third, third.post("/household/invite", mode, email="nobody-here@example.com"))).startswith(
        COULD_NOT
    )
    sam.post("/household/invite", mode, email=alex_email)
    inviter = re.search(r'name="inviter_id" value="(\d+)"', alex.http.get(f"/household?db={mode}").text).group(1)
    alex.post("/household/accept", mode, inviter_id=inviter, merge="1")
    taken = notice(follow(third, third.post("/household/invite", mode, email=sam_email)))
    assert taken.startswith(COULD_NOT)
    assert notice(follow(third, third.post("/household/invite", mode, email="not an email"))).startswith("Enter")
    own = re.search(r"test-saved-[0-9a-f]+@example.com", third.http.get(f"/account?db={mode}").text).group(0)
    assert notice(follow(third, third.post("/household/invite", mode, email=own))).startswith("That is your own")


@pytest.mark.parametrize("mode", MODES)
def test_decline_and_cancel(mode, make_user, blocks):  # noqa: F811
    sam, _, alex, alex_email = pair(make_user, mode, blocks)
    sam.post("/household/invite", mode, email=alex_email)
    assert notice(follow(sam, sam.post("/household/cancel", mode))) == "Invitation cancelled."
    assert notice(follow(sam, sam.post("/household/cancel", mode))) == "There is no invitation to cancel."
    assert "invited you" not in alex.http.get(f"/shortlist?db={mode}").text
    sam.post("/household/invite", mode, email=alex_email)
    inviter = re.search(r'name="inviter_id" value="(\d+)"', alex.http.get(f"/household?db={mode}").text).group(1)
    assert notice(follow(alex, alex.post("/household/decline", mode, inviter_id=inviter))) == "Invitation declined."
    assert notice(follow(alex, alex.post("/household/accept", mode, inviter_id=inviter))) == (
        "That invitation is no longer open."
    )
    assert notice(follow(alex, alex.post("/household/accept", mode, inviter_id="x"))) == (
        "Choose an invitation from the list."
    )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("path", HOUSEHOLD_POSTS)
def test_household_posts_without_a_csrf_token_get_403(mode, path, make_user):  # noqa: F811
    browser, _ = make_user(mode)
    assert browser.http.post(f"{path}?db={mode}", data={"inviter_id": "1", "email": "a@b.c"}).status_code == 403


@pytest.mark.parametrize("mode,name", [("pg", "PostgreSQL"), ("mongo", "MongoDB")])
def test_demo_mode_explains_that_each_database_has_its_own(mode, name, make_user):  # noqa: F811
    browser, _ = make_user(mode)
    for path, what in (("/shortlist", "shortlist"), ("/household", "household")):
        assert f"This is the {what} in {name}." in browser.http.get(f"{path}?db={mode}").text
    browser.http.cookies.delete("demo")
    assert "This is the shortlist in" not in browser.http.get("/shortlist").text


def test_the_old_saved_page_redirects():
    browser = Browser()
    try:
        resp = browser.http.get("/saved?db=mongo&notice=save_ok")
        assert resp.status_code == 308 and resp.headers["location"] == "/shortlist?db=mongo&notice=save_ok"
    finally:
        browser.close()
