"""Demo mode on and off (the footer toggle). Demo mode shows the database switch and the query timings.

Turning it off also forgets the chosen database and drops ?db= from the page the browser returns to, so the
visitor is back on PostgreSQL. CSRF is enforced for this POST by app.services.guards like every other.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse

from app.services.dbmode import COOKIE, COOKIE_MAX_AGE, DEMO_COOKIE, safe_return_path

router = APIRouter()

NEXT_MAX = 2000


def return_to(next_url: str, keep_db: bool) -> str:
    """The local page to go back to. Anything with a scheme or host, or an unusual path, becomes "/"."""
    parts = urlsplit(next_url[:NEXT_MAX])
    if parts.scheme or parts.netloc:
        return "/"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if keep_db or k != "db"]
    return safe_return_path(parts.path, urlencode(query))


@router.post("/demo")
def toggle_demo(on: str = Form(""), next_url: str = Form("/", alias="next")) -> RedirectResponse:
    turn_on = on == "1"
    response = RedirectResponse(return_to(next_url, keep_db=turn_on), status_code=303)
    if turn_on:
        response.set_cookie(DEMO_COOKIE, "1", max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax")
    else:
        response.delete_cookie(DEMO_COOKIE, path="/", httponly=True, samesite="lax")
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax")
    return response
