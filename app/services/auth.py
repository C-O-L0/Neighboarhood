"""Accounts: input validation, password hashing, the login throttle, and session and CSRF helpers.

The session holds {"uid": {"pg": int, "mongo": int}, "csrf": str} and nothing else. Each database has its own
accounts (BUILD_PLAN section 5), so signing in under one mode does not sign the user in under the other.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from collections.abc import Callable, MutableMapping
from typing import Any
from urllib.parse import parse_qsl, urlencode

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from pydantic import BaseModel, ValidationError, field_validator

from app.services.dbmode import MODES

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_MIN, PASSWORD_MAX = 10, 128
NAME_MAX = 60

THROTTLE_LIMIT = 5
THROTTLE_WINDOW = 15 * 60.0  # seconds

hasher = PasswordHasher()
# Verified against when the email is unknown, so the time taken does not reveal whether it exists.
_DUMMY_HASH = hasher.hash("neighboar-dummy-password")


# ---------------------------------------------------------------- validation


def _check_password(value: str) -> str:
    if not PASSWORD_MIN <= len(value) <= PASSWORD_MAX:
        raise ValueError(f"The password must be {PASSWORD_MIN} to {PASSWORD_MAX} characters.")
    return value


class _Credentials(BaseModel):
    email: str
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, value: Any) -> str:
        email = str(value).strip().lower()
        if not 3 <= len(email) <= 254 or not EMAIL_RE.fullmatch(email):
            raise ValueError("Enter a valid email address (3 to 254 characters).")
        return email

    @field_validator("password", mode="before")
    @classmethod
    def _password(cls, value: Any) -> str:
        return _check_password(str(value))


class LoginForm(_Credentials):
    pass


class RegisterForm(_Credentials):
    display_name: str | None = None

    @field_validator("display_name", mode="before")
    @classmethod
    def _name(cls, value: Any) -> str | None:
        name = None if value is None else str(value).strip()
        if not name:
            return None
        if len(name) > NAME_MAX:
            raise ValueError(f"The display name must be at most {NAME_MAX} characters.")
        return name


def error_messages(exc: ValidationError) -> list[str]:
    return [e["msg"].removeprefix("Value error, ") for e in exc.errors()]


# ---------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    return hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    """True when the password matches. With no stored hash (unknown email) a dummy hash is checked instead."""
    try:
        hasher.verify(stored_hash or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return stored_hash is not None


# ---------------------------------------------------------------- throttle


class LoginThrottle:
    """Sliding-window limit on failed sign-ins per (email, client IP).

    After `limit` failures inside `window` seconds the key is blocked until the oldest failure leaves the window.
    State is per process (one uvicorn worker), so it resets on restart and is not shared between workers.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        limit: int = THROTTLE_LIMIT,
        window: float = THROTTLE_WINDOW,
    ):
        self.clock, self.limit, self.window = clock, limit, window
        self._failures: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self) -> None:
        cutoff = self.clock() - self.window
        for key in list(self._failures):
            kept = [t for t in self._failures[key] if t > cutoff]
            if kept:
                self._failures[key] = kept
            else:
                del self._failures[key]

    def blocked(self, key: tuple[str, str]) -> bool:
        with self._lock:
            self._prune()
            return len(self._failures.get(key, ())) >= self.limit

    def record_failure(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._failures.setdefault(key, []).append(self.clock())

    def clear(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def __len__(self) -> int:
        return len(self._failures)


# ---------------------------------------------------------------- session and CSRF


def new_token() -> str:
    return secrets.token_urlsafe(32)


def ensure_session(session: MutableMapping[str, Any]) -> None:
    """Give the session (anonymous or not) a CSRF token and a uid map if it lacks them."""
    if not isinstance(session.get("csrf"), str):
        session["csrf"] = new_token()
    if not isinstance(session.get("uid"), dict):
        session["uid"] = {}


def session_uid(session: MutableMapping[str, Any], mode: str) -> int | None:
    uid = session.get("uid")
    value = uid.get(mode) if isinstance(uid, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def sign_in(session: MutableMapping[str, Any], mode: str, buyer_id: int) -> None:
    """Set the uid for this mode only, and rotate the CSRF token."""
    assert mode in MODES
    ensure_session(session)
    session["uid"][mode] = buyer_id
    session["csrf"] = new_token()


NEXT_MAX = 300  # a return path longer than this is ignored
_NEXT_PATH = re.compile(r"/(?!/)[A-Za-z0-9._~-]*(/[A-Za-z0-9._~-]+)*")  # the same rule as dbmode._SAFE_PATH


def return_path(raw: str | None) -> str | None:
    """Where to send a buyer after signing in (UC5 E1, UC4 A3): a local path of plain segments with an optional
    query, the `db` parameter dropped (the redirect sets it). None for anything else, such as //host or a URL."""
    if not raw or len(raw) > NEXT_MAX or not raw.isprintable():
        return None
    path, _, query = raw.partition("?")
    if not _NEXT_PATH.fullmatch(path):
        return None
    kept = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k != "db"]
    return f"{path}?{urlencode(kept)}" if kept else path


def login_link(path: str, query: str = "") -> str:
    """/login with this page as the place to return to."""
    target = f"{path}?{query}" if query else path
    return "/login?" + urlencode({"next": target})
