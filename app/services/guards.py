"""App-level dependencies: CSRF protection and the signed-in account.

Both run for every route, before the route's own parameters (forms, path, query) are validated, so a missing
or wrong CSRF token is always a 403 whatever else is wrong with the request.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import HTTPException, Request

from app.services.auth import ensure_session, session_uid
from app.services.repos import REPOS

log = logging.getLogger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


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
    """Set request.state.account to the account signed in under this request's DB mode, or None.

    This lookup is not timed or listed in the page footer: it serves the header, not the page's own query.
    """
    request.state.account = None
    mode = getattr(request.state, "db_mode", None)
    uid = session_uid(request.session, mode) if mode else None
    if uid is None:
        return
    try:
        dbs = request.app.state.dbs
        pg_cls, mongo_cls = REPOS["accounts"]
        if mode == "pg":
            with dbs.pg_rw.connection(timeout=5) as conn:
                account = pg_cls(conn).by_id(uid)
        else:
            account = mongo_cls(dbs.mongo()).by_id(uid)
    except Exception as exc:
        # A database outage must not break every page (health reports it as 503). Keep the uid so the
        # user is still signed in when the database returns; log the error type only, never the session.
        log.warning("account lookup failed for mode %s: %s", mode, type(exc).__name__)
        return
    if account is None:  # the account was deleted since sign-in
        del request.session["uid"][mode]
    request.state.account = account
