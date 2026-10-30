"""Demo mode without a database: which database a request uses, the toggle and where it sends the browser."""

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config import get_settings
from app.main import create_app
from app.routers.demo import return_to
from app.services.dbmode import resolve_mode
from tests.unit.test_auth import SECRET, cookie_for


def request(query: str = "", cookies: dict | None = None) -> Request:
    header = "; ".join(f"{k}={v}" for k, v in (cookies or {}).items())
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": query.encode(),
        "headers": [(b"cookie", header.encode())] if header else [],
    }
    return Request(scope)


@pytest.mark.parametrize(
    "query,cookies,expected",
    [
        ("", {}, "pg"),
        ("", {"db_mode": "mongo"}, "pg"),  # without demo mode the cookie is ignored
        ("", {"demo": "1", "db_mode": "mongo"}, "mongo"),
        ("", {"demo": "1", "db_mode": "bogus"}, "pg"),
        ("", {"demo": "yes", "db_mode": "mongo"}, "pg"),  # only "1" turns demo mode on
        ("db=mongo", {}, "mongo"),  # a link that names the database always wins
        ("db=pg", {"demo": "1", "db_mode": "mongo"}, "pg"),
    ],
)
def test_resolve_mode(query, cookies, expected):
    assert resolve_mode(request(query, cookies)) == expected


@pytest.mark.parametrize(
    "next_url,keep_db,expected",
    [
        ("/search?town=BISHAN&db=mongo", True, "/search?town=BISHAN&db=mongo"),
        ("/search?town=BISHAN&db=mongo", False, "/search?town=BISHAN"),
        ("/trends?town=ANG+MO+KIO", True, "/trends?town=ANG+MO+KIO"),
        ("/", False, "/"),
        ("", False, "/"),
        ("//evil.example/x", True, "/"),
        ("https://evil.example/x", True, "/"),
        ("/\\evil.example", True, "/"),
        ("javascript:alert(1)", True, "/"),
        ("/a//b?db=pg", False, "/"),
    ],
)
def test_return_to_stays_on_this_site(next_url, keep_db, expected):
    assert return_to(next_url, keep_db) == expected


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", SECRET)
    get_settings.cache_clear()
    try:
        client = TestClient(create_app(), follow_redirects=False)
        client.cookies.set("neighboar_session", cookie_for({"uid": {}, "csrf": "t"}))
        yield client
    finally:
        get_settings.cache_clear()


def set_cookies(resp) -> dict[str, str]:
    return {h.split("=", 1)[0]: h.lower() for h in resp.headers.get_list("set-cookie")}


def test_turning_demo_mode_on_sets_the_cookie_and_keeps_the_page(client):
    resp = client.post("/demo", data={"csrf_token": "t", "on": "1", "next": "/search?town=BISHAN&db=mongo"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/search?town=BISHAN&db=mongo"
    cookie = set_cookies(resp)["demo"]
    assert cookie.startswith("demo=1;") and "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie


def test_turning_demo_mode_off_clears_both_cookies_and_the_db_parameter(client):
    resp = client.post("/demo", data={"csrf_token": "t", "on": "0", "next": "/search?town=BISHAN&db=mongo"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/search?town=BISHAN"
    cookies = set_cookies(resp)
    assert "max-age=0" in cookies["demo"] and "max-age=0" in cookies["db_mode"]


def test_the_toggle_needs_the_csrf_token(client):
    assert client.post("/demo", data={"on": "1", "next": "/"}).status_code == 403


def test_glossary_needs_no_database(client):
    resp = client.get("/glossary")
    assert resp.status_code == 200
    assert '<dt id="typical-price">Typical price</dt>' in resp.text
    assert 'id="db-mode"' not in resp.text  # no demo bar without demo mode
