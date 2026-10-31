"""The household page (S8, UC10): invite a partner, answer an invitation, or leave.

The buyer id always comes from the session account, never from the form. Every write redirects back here with a
fixed notice key (app/services/households.py). CSRF is enforced for every POST by app.services.guards.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services.auth import login_link
from app.services.households import CAP, accepted_params, clean_email, notice_from, notice_key, parse_buyer_id
from app.services.repos import call_repo
from app.templating import templates

router = APIRouter()

RAW_MAX = 1000  # form fields longer than this are refused before any trimming


def _login(request: Request) -> RedirectResponse:
    return RedirectResponse(f"{login_link('/household')}&db={request.state.db_mode}", status_code=303)


def _back(request: Request, params: dict[str, str]) -> RedirectResponse:
    return RedirectResponse(f"/household?{urlencode({'db': request.state.db_mode, **params})}", status_code=303)


@router.get("/household", response_class=HTMLResponse)
def household_page(request: Request):
    account = request.state.account
    if account is None:
        return _login(request)
    me = account.buyer_id
    household = call_repo(request, "households", "household", me, label="household")
    invitations = call_repo(request, "households", "invitations", me, label="invitations")
    own = 0
    if household is None and invitations.received:
        own = len(call_repo(request, "saved", "list", me, label="saved list"))
    context = {
        "household": household,
        "partner": next((m for m in household.members if m.buyer_id != me), None) if household else None,
        "invitations": invitations,
        "own": own,
        "cap": CAP,
        "notice": notice_from(request.query_params),
    }
    return templates.TemplateResponse(request, "household.html", context)


@router.post("/household/invite")
def invite(request: Request, email: str = Form("")):
    if request.state.account is None:
        return _login(request)
    cleaned = clean_email(email) if len(email) <= RAW_MAX else None
    if cleaned is None:
        return _back(request, {"notice": notice_key("invite", None)})
    outcome = call_repo(request, "households", "invite", request.state.account.buyer_id, cleaned, label="invite")
    return _back(request, {"notice": notice_key("invite", outcome)})


@router.post("/household/cancel")
def cancel(request: Request):
    if request.state.account is None:
        return _login(request)
    outcome = call_repo(request, "households", "cancel", request.state.account.buyer_id, label="cancel invitation")
    return _back(request, {"notice": notice_key("cancel", outcome)})


@router.post("/household/accept")
def accept(request: Request, inviter_id: str = Form(""), merge: str = Form("")):
    if request.state.account is None:
        return _login(request)
    inviter = parse_buyer_id(inviter_id[:RAW_MAX])
    if inviter is None:
        return _back(request, {"notice": notice_key("input", None)})
    me = request.state.account.buyer_id
    result = call_repo(request, "households", "accept", me, inviter, merge == "1", label="accept invitation")
    return _back(request, accepted_params(result))


@router.post("/household/decline")
def decline(request: Request, inviter_id: str = Form("")):
    if request.state.account is None:
        return _login(request)
    inviter = parse_buyer_id(inviter_id[:RAW_MAX])
    if inviter is None:
        return _back(request, {"notice": notice_key("input", None)})
    me = request.state.account.buyer_id
    outcome = call_repo(request, "households", "decline", me, inviter, label="decline invitation")
    return _back(request, {"notice": notice_key("decline", outcome)})


@router.post("/household/leave")
def leave(request: Request):
    if request.state.account is None:
        return _login(request)
    outcome = call_repo(request, "households", "leave", request.state.account.buyer_id, label="leave household")
    return _back(request, {"notice": notice_key("leave", outcome)})
