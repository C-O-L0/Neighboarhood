"""Accounts without a database: validation, the login throttle, sessions and CSRF on every POST route."""

import base64
import json
import re

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app.config import get_settings
from app.main import create_app
from app.services.auth import (
    LoginForm,
    LoginThrottle,
    RegisterForm,
    ensure_session,
    hash_password,
    session_uid,
    sign_in,
    verify_password,
)

SECRET = "unit-test-secret-" + "x" * 32
KEY = ("a@example.com", "10.0.0.1")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# ---------------------------------------------------------------- throttle


def test_five_failures_then_blocked():
    t = LoginThrottle(clock=Clock())
    for _ in range(4):
        t.record_failure(KEY)
        assert not t.blocked(KEY)
    t.record_failure(KEY)
    assert t.blocked(KEY)


def test_blocked_attempts_are_not_counted_as_failures():
    clock = Clock()
    t = LoginThrottle(clock=clock)
    for _ in range(5):
        t.record_failure(KEY)
    # Repeated blocked checks never extend the block: it ends 15 minutes after the failures.
    for _ in range(20):
        clock.now += 30
        assert t.blocked(KEY)
    clock.now = 1000.0 + 15 * 60 + 1
    assert not t.blocked(KEY)


def test_window_slides():
    clock = Clock()
    t = LoginThrottle(clock=clock)
    t.record_failure(KEY)  # at 1000
    clock.now += 14 * 60
    for _ in range(4):
        t.record_failure(KEY)
    assert t.blocked(KEY)
    clock.now += 61  # the first failure is now over 15 minutes old and drops out
    assert not t.blocked(KEY)
    t.record_failure(KEY)
    assert t.blocked(KEY)


def test_success_clears_the_key():
    t = LoginThrottle(clock=Clock())
    for _ in range(5):
        t.record_failure(KEY)
    t.clear(KEY)
    assert not t.blocked(KEY)


def test_keys_are_independent():
    t = LoginThrottle(clock=Clock())
    for _ in range(5):
        t.record_failure(KEY)
    assert not t.blocked(("a@example.com", "10.0.0.2"))
    assert not t.blocked(("b@example.com", "10.0.0.1"))


def test_expired_keys_are_pruned():
    clock = Clock()
    t = LoginThrottle(clock=clock)
    for i in range(100):
        t.record_failure((f"u{i}@example.com", "10.0.0.1"))
    assert len(t) == 100
    clock.now += 15 * 60 + 1
    assert not t.blocked(KEY)  # any check prunes
    assert len(t) == 0


# ---------------------------------------------------------------- validation and passwords


def test_email_is_trimmed_and_lower_cased():
    assert LoginForm(email="  Test@Example.COM ", password="x" * 10).email == "test@example.com"


@pytest.mark.parametrize("email", ["", "a@b", "a b@c.com", "@c.com", "a@@c.com", "a@c.", "x" * 250 + "@a.co"])
def test_bad_emails_rejected(email):
    with pytest.raises(ValueError):
        LoginForm(email=email, password="x" * 10)


@pytest.mark.parametrize("password", ["x" * 9, "x" * 129, "x" * 10_000])
def test_password_length_limits(password):
    with pytest.raises(ValueError):
        LoginForm(email="a@b.co", password=password)
    with pytest.raises(ValueError):
        RegisterForm(email="a@b.co", password=password)


def test_password_length_boundaries_accepted():
    assert LoginForm(email="a@b.co", password="x" * 10)
    assert LoginForm(email="a@b.co", password="x" * 128)


def test_display_name_is_optional_and_limited():
    assert RegisterForm(email="a@b.co", password="x" * 10).display_name is None
    assert RegisterForm(email="a@b.co", password="x" * 10, display_name="  ").display_name is None
    assert RegisterForm(email="a@b.co", password="x" * 10, display_name=" Sam ").display_name == "Sam"
    assert RegisterForm(email="a@b.co", password="x" * 10, display_name="n" * 60)
    with pytest.raises(ValueError):
        RegisterForm(email="a@b.co", password="x" * 10, display_name="n" * 61)


def test_verify_password():
    stored = hash_password("correct horse battery")
    assert stored.startswith("$argon2id$")
    assert verify_password(stored, "correct horse battery")
    assert not verify_password(stored, "wrong horse battery")
    assert not verify_password(None, "correct horse battery")  # unknown email: dummy hash, always False


def test_sign_in_sets_only_this_mode_and_rotates_the_token():
    session = {}
    ensure_session(session)
    before = session["csrf"]
    sign_in(session, "pg", 7)
    assert session_uid(session, "pg") == 7
    assert session_uid(session, "mongo") is None
    assert session["csrf"] != before
    assert set(session) == {"uid", "csrf"}


# ---------------------------------------------------------------- CSRF on every POST route


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", SECRET)
    get_settings.cache_clear()
    try:
        yield create_app()
    finally:
        get_settings.cache_clear()


def cookie_for(session: dict) -> str:
    payload = base64.b64encode(json.dumps(session).encode())
    return TimestampSigner(SECRET).sign(payload).decode()


def post_routes(app):
    """Every POST path, with path parameters filled in. Included routers are expanded by FastAPI's own walker."""
    found = []
    for route in iter_route_contexts(app.routes):
        if route.methods and "POST" in route.methods:
            found.append(re.sub(r"\{[^}]+\}", "1", route.path))
    return sorted(found)


def test_the_enumeration_finds_the_auth_routes(app):
    assert {"/login", "/register", "/logout"} <= set(post_routes(app))


@pytest.mark.parametrize("case", ["no session", "no token in session", "wrong token"])
def test_every_post_route_rejects_a_missing_or_wrong_token(app, case):
    client = TestClient(app, follow_redirects=False)
    body = {"email": "a@example.com", "password": "x" * 12}
    if case == "no token in session":
        client.cookies.set("neighboar_session", cookie_for({"uid": {}}))
        body["csrf_token"] = "anything"
    elif case == "wrong token":
        client.cookies.set("neighboar_session", cookie_for({"uid": {}, "csrf": "right-token"}))
        body["csrf_token"] = "wrong-token"
    for path in post_routes(app):
        # The empty body also fails each route's own validation: the token check must come first.
        for data in (body, {}):
            resp = client.post(path, data=data)
            assert resp.status_code == 403, (case, path, data)


def test_a_matching_token_gets_past_the_guard(app):
    client = TestClient(app, follow_redirects=False)
    client.cookies.set("neighboar_session", cookie_for({"uid": {}, "csrf": "right-token"}))
    resp = client.post("/logout", data={"csrf_token": "right-token"})
    assert resp.status_code == 303


def test_logout_by_get_is_405(app):
    assert TestClient(app).get("/logout").status_code == 405


def test_get_gives_every_visitor_a_token(app):
    client = TestClient(app)
    resp = client.get("/login")
    assert resp.status_code == 200
    token = re.search(r'name="csrf_token" value="([^"]+)"', resp.text).group(1)
    assert len(token) >= 40
    assert token in client.get("/register").text  # stable across GETs


def test_long_passwords_are_rejected_before_hashing(app, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("hashed or verified an over-long password")

    monkeypatch.setattr("app.routers.auth.hash_password", boom)
    monkeypatch.setattr("app.routers.auth.verify_password", boom)
    client = TestClient(app, follow_redirects=False)
    client.cookies.set("neighboar_session", cookie_for({"uid": {}, "csrf": "t"}))
    for path in ("/login", "/register"):
        resp = client.post(path, data={"csrf_token": "t", "email": "a@example.com", "password": "p" * 10_000})
        assert resp.status_code == 422, path


@pytest.mark.parametrize("mode", ["pg", "mongo"])
def test_account_lookup_failure_does_not_break_health(app, monkeypatch, mode):
    """A signed-in session while the database is down: health still answers (503), never 500."""

    def down(*_a, **_k):
        raise RuntimeError("database unreachable")

    class Pool:
        def connection(self, **_k):
            down()

    class Dbs:
        pg_rw = Pool()

        def mongo(self, **_k):
            down()

    app.state.dbs = Dbs()
    client = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
    client.cookies.set("neighboar_session", cookie_for({"uid": {mode: 1}, "csrf": "t"}))
    resp = client.get(f"/api/health?db={mode}")
    assert resp.status_code in (200, 503)
