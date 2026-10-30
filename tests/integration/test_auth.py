"""Accounts through the running web app: register, sign in, sessions, throttle (task 3.1b)."""

import base64
import json
import os
import re
import secrets

import httpx
import pytest

from tests.integration.web import DEMO

BASE = os.environ.get("NEIGHBOAR_URL", "http://127.0.0.1:8000")
pytestmark = pytest.mark.integration

MODES = ("pg", "mongo")
CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')
ALERT = re.compile(r'<div class="bad" role="alert">(.*?)</div>', re.S)
DEMO_EMAIL = "demo@neighboar.local"


class Browser:
    """An httpx client with its own cookie jar that fetches a fresh CSRF token before each POST.

    It is in demo mode, so pages show the database switch and the timings.
    """

    def __init__(self):
        self.http = httpx.Client(base_url=BASE, cookies=DEMO, timeout=30, follow_redirects=False)

    def token(self, mode):
        page = self.http.get(f"/login?db={mode}")
        return CSRF.search(page.text).group(1)

    def post(self, path, mode, **data):
        data.setdefault("csrf_token", self.token(mode))
        return self.http.post(f"{path}?db={mode}", data=data)

    def login(self, mode, email, password):
        return self.post("/login", mode, email=email, password=password)

    def close(self):
        self.http.close()


@pytest.fixture(autouse=True)
def restore_synthetic_last_active(pg_owner, mongo_etl):
    """Signing in sets last_active_at; the synthetic buyers are loaded with it NULL, so put that back."""
    yield
    pg_owner.execute("UPDATE buyer SET last_active_at = NULL WHERE buyer_id <= 200")
    pg_owner.commit()
    mongo_etl.buyers.update_many({"_id": {"$lte": 200}}, {"$set": {"last_active_at": None}})


@pytest.fixture
def browser():
    b = Browser()
    yield b
    b.close()


def alert_text(resp):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", ALERT.search(resp.text).group(1))).strip()


def random_email(mixed=False):
    local = secrets.token_hex(6)
    return f"Test-{local}@Example.com" if mixed else f"test-{local}@example.com"


def pg_count(pg_rw, email):
    return pg_rw.execute("SELECT count(*) FROM buyer WHERE lower(email) = %s", (email.lower(),)).fetchone()[0]


def mongo_count(mongo_app, email):
    return mongo_app.buyers.count_documents({"email": email.lower()})


def delete_buyer(pg_owner, mongo_etl, email):
    pg_owner.execute("DELETE FROM buyer WHERE lower(email) = %s", (email.lower(),))
    pg_owner.commit()
    mongo_etl.buyers.delete_many({"email": email.lower()})


# ---------------------------------------------------------------- register


@pytest.mark.parametrize("mode", MODES)
def test_register_creates_the_buyer_in_that_database_only(browser, mode, pg_rw, pg_owner, mongo_app, mongo_etl):
    email = random_email(mixed=True)
    password = secrets.token_urlsafe(16)
    try:
        resp = browser.post("/register", mode, email=email, password=password, display_name="Test Person")
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/login")
        counts = {"pg": pg_count(pg_rw, email), "mongo": mongo_count(mongo_app, email)}
        assert counts[mode] == 1
        assert counts["mongo" if mode == "pg" else "pg"] == 0
        # The stored email is lower-case; the same address in lower case is a duplicate.
        again = browser.post("/register", mode, email=email.lower(), password=password)
        assert again.status_code == 409
        assert "already exists" in alert_text(again)
        assert {"pg": pg_count(pg_rw, email), "mongo": mongo_count(mongo_app, email)}[mode] == 1
        # The new account can sign in in its own mode only.
        assert browser.login(mode, email.upper(), password).status_code == 303
        other = "mongo" if mode == "pg" else "pg"
        assert browser.login(other, email, password).status_code == 401
    finally:
        delete_buyer(pg_owner, mongo_etl, email)


def test_register_stores_a_lower_case_email_and_an_argon2id_hash(browser, pg_rw, pg_owner, mongo_etl):
    email = random_email(mixed=True)
    try:
        resp = browser.post("/register", "pg", email=f"  {email}  ", password=secrets.token_urlsafe(16))
        assert resp.status_code == 303
        row = pg_rw.execute("SELECT email, password_hash FROM buyer WHERE lower(email) = %s", (email.lower(),))
        stored, pw_hash = row.fetchone()
        assert stored == email.lower()
        assert pw_hash.startswith("$argon2id$")
    finally:
        delete_buyer(pg_owner, mongo_etl, email)


@pytest.mark.parametrize("mode", MODES)
def test_register_rejects_bad_input(browser, mode):
    good = {"email": random_email(), "password": "p" * 12}
    for change in ({"email": "not-an-email"}, {"password": "short"}, {"display_name": "n" * 61}):
        resp = browser.post("/register", mode, **{**good, **change})
        assert resp.status_code == 422, change


# ---------------------------------------------------------------- login


@pytest.mark.parametrize("mode", MODES)
def test_demo_account_signs_in(browser, mode, demo_passwords):
    resp = browser.login(mode, DEMO_EMAIL, demo_passwords[0])
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/account?db={mode}"
    page = browser.http.get(f"/account?db={mode}")
    assert page.status_code == 200
    name = "PostgreSQL" if mode == "pg" else "MongoDB"
    assert DEMO_EMAIL in page.text
    assert "Signed in as" in page.text and f'<th scope="row">Database</th><td>{name}</td>' in page.text
    assert "Last active" in page.text and "Created" in page.text


@pytest.mark.parametrize("mode", MODES)
def test_wrong_password_and_unknown_email_look_the_same(browser, mode, demo_passwords):
    wrong = browser.login(mode, DEMO_EMAIL, "definitely-not-the-password")
    unknown = browser.login(mode, random_email(), "definitely-not-the-password")
    assert wrong.status_code == unknown.status_code == 401
    assert alert_text(wrong) == alert_text(unknown) == "Email or password is incorrect."
    # a success clears the demo key so reruns never accumulate failures
    assert browser.login(mode, DEMO_EMAIL, demo_passwords[0]).status_code == 303


def test_signing_in_under_one_mode_does_not_sign_in_under_the_other(browser, demo_passwords):
    assert browser.login("pg", DEMO_EMAIL, demo_passwords[0]).status_code == 303
    assert browser.http.get("/account?db=pg").status_code == 200
    other = browser.http.get("/account?db=mongo")
    assert other.status_code == 303
    assert other.headers["location"] == "/login?db=mongo"
    assert "Sign in" in browser.http.get("/?db=mongo").text
    assert "Signed in as" not in browser.http.get("/?db=mongo").text
    assert "Signed in as" in browser.http.get("/?db=pg").text


@pytest.mark.parametrize("mode", MODES)
def test_account_page_redirects_when_signed_out(mode):
    resp = httpx.get(f"{BASE}/account?db={mode}", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/login?db={mode}"


def test_logout_clears_the_session(browser, demo_passwords):
    browser.login("pg", DEMO_EMAIL, demo_passwords[0])
    assert browser.http.get("/account?db=pg").status_code == 200
    assert browser.http.get("/logout").status_code == 405
    assert browser.http.get("/account?db=pg").status_code == 200  # GET did not sign out
    assert browser.post("/logout", "pg").status_code == 303
    assert browser.http.get("/account?db=pg").status_code == 303


@pytest.mark.parametrize("path", ["/login", "/register"])
@pytest.mark.parametrize("mode", MODES)
def test_ten_thousand_character_password_is_422(browser, path, mode):
    resp = browser.post(path, mode, email=random_email(), password="p" * 10_000)
    assert resp.status_code == 422


# ---------------------------------------------------------------- session cookie


def test_session_cookie_attributes_and_contents(demo_passwords):
    resp = httpx.post(f"{BASE}/login?db=pg", data={}, follow_redirects=False)
    assert resp.status_code == 403  # no session, no token
    client = httpx.Client(base_url=BASE, follow_redirects=False)
    token = CSRF.search(client.get("/login?db=pg").text).group(1)
    resp = client.post(
        "/login?db=pg", data={"csrf_token": token, "email": DEMO_EMAIL, "password": demo_passwords[0]}
    )
    assert resp.status_code == 303
    header = next(h for h in resp.headers.get_list("set-cookie") if h.startswith("neighboar_session="))
    low = header.lower()
    assert "httponly" in low
    assert "samesite=lax" in low
    assert "max-age=28800" in low
    value = header.split(";")[0].split("=", 1)[1]
    payload = json.loads(base64.b64decode(value.split(".")[0] + "=="))
    assert set(payload) == {"uid", "csrf"}
    assert set(payload["uid"]) == {"pg"}
    assert isinstance(payload["uid"]["pg"], int)
    assert payload["csrf"] != token  # rotated at sign-in
    blob = base64.b64decode(value.split(".")[0] + "==").decode()
    assert DEMO_EMAIL not in blob and "argon2" not in blob and demo_passwords[0] not in blob
    client.close()


# ---------------------------------------------------------------- throttle


@pytest.mark.parametrize("mode", MODES)
def test_sixth_failed_sign_in_is_throttled(browser, mode):
    email = random_email()  # fresh per run: the web process keeps throttle state between runs
    statuses = [browser.login(mode, email, "wrong-password-123").status_code for _ in range(6)]
    assert statuses == [401] * 5 + [429]
    # 429 answers are not failures and still do not verify
    assert browser.login(mode, email, "wrong-password-123").status_code == 429
