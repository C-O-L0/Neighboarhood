"""App-level dependencies: CSRF protection and the signed-in account.

Both run for every route, before the route's own parameters (forms, path, query) are validated, so a missing
or wrong CSRF token is always a 403 whatever else is wrong with the request.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import HTTPException, Request

from app.services.auth import ensure_session, session_uid
from app.services.identities import is_admin_path, mongo_db, pg_conn
from app.services.repos import REPOS

log = logging.getLogger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Routes that never render a page footer, so they skip the data-month lookup.
NOT_PAGES = ("/api/", "/docs", "/redoc", "/openapi.json")


async def csrf_guard(request: Request) -> None:
    """GET: make sure the session has a CSRF token. Anything else: require a matching `csrf_token` form field."""
    session = request.session
    if request.method in SAFE_METHODS:
        ensure_session(session)
        return
    expected = session.get("csrf")
    try:
        supplied = (await request.form()).get("csrf_token")
    except Exception:  # a malformed body is treated like a missing token
        supplied = None
    if (
        not isinstance(expected, str)
        or not isinstance(supplied, str)
        or not hmac.compare_digest(supplied.encode(), expected.encode())
    ):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token")


def load_account(request: Request) -> None:
    """Set request.state.account to the account signed in under this request's DB mode, or None, and
    request.state.shortlist to its ShortlistSummary (the count in the menu and the invitation badge).

    These lookups are not timed or listed in the page footer: they serve the header, not the page's own query.
    """
    request.state.account = None
    request.state.shortlist = None
    mode = getattr(request.state, "db_mode", None)
    uid = session_uid(request.session, mode) if mode else None
    if uid is None:
        return
    try:
        # Admin routes never touch app_rw or app, so the account behind them is read as app_admin.
        admin = is_admin_path(request.url.path)
        pg_cls, mongo_cls = REPOS["accounts"]
        pg_households, mongo_households = REPOS["households"]
        summary = None
        if mode == "pg":
            with pg_conn(request, admin=admin) as conn:
                account = pg_cls(conn).by_id(uid)
                if account is not None and request.method in SAFE_METHODS:
                    summary = pg_households(conn).summary(uid)
        else:
            db = mongo_db(request, admin=admin)
            account = mongo_cls(db).by_id(uid)
            if account is not None and request.method in SAFE_METHODS:
                summary = mongo_households(db).summary(uid)
    except Exception as exc:
        # A database outage must not break every page (health reports it as 503). Keep the uid so the
        # user is still signed in when the database returns; log the error type only, never the session.
        log.warning("account lookup failed for mode %s: %s", mode, type(exc).__name__)
        return
    if account is None:  # the account was deleted since sign-in
        del request.session["uid"][mode]
    request.state.account = account
    request.state.shortlist = summary


def load_data_month(request: Request) -> None:
    """Set request.state.data_month to the latest registration month in this request's database, or None.

    Pages show it in the footer ("Sales registered up to ...") and the explore pages end their recent window on
    it. Like the account lookup it serves the page frame, so it is not timed. Only GET pages need it.
    """
    request.state.data_month = None
    mode = getattr(request.state, "db_mode", None)
    if mode is None or request.method not in SAFE_METHODS or request.url.path.startswith(NOT_PAGES):
        return
    try:
        admin = is_admin_path(request.url.path)
        pg_cls, mongo_cls = REPOS["explore"]
        if mode == "pg":
            with pg_conn(request, admin=admin) as conn:
                request.state.data_month = pg_cls(conn).latest_month()
        else:
            request.state.data_month = mongo_cls(mongo_db(request, admin=admin)).latest_month()
    except Exception as exc:  # a database outage must not break every page
        log.warning("data month lookup failed for mode %s: %s", mode, type(exc).__name__)


def require_admin(request: Request) -> None:
    """403 unless the account signed in under this request's DB mode is an administrator (signed out included)."""
    account = getattr(request.state, "account", None)
    if account is None or not account.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
