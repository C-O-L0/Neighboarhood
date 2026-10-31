"""The saved-block list and the save, edit-note and remove forms.

The buyer id always comes from the session account (set by app.services.guards.load_account for the current DB
mode), never from the request. CSRF is enforced for every POST by app.services.guards. Form fields are read as
plain strings and validated here, so a signed-out visitor is redirected to sign in whatever the form holds.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.repos.dto import Outcome, RolledBack
from app.services.repos import call_repo
from app.services.saved import (
    TOO_LONG,
    clean_note,
    notice_from,
    notice_key,
    parse_building_id,
    rolled_back_params,
)
from app.templating import templates

router = APIRouter()

RAW_MAX = 5000  # form fields longer than this are not even trimmed; the note limit is 500 characters


def _login(request: Request) -> RedirectResponse:
    return RedirectResponse(f"/login?db={request.state.db_mode}", status_code=303)


def _back(request: Request, back: str, building_id: int | None, params: dict[str, str]) -> RedirectResponse:
    path = f"/block/{building_id}" if back == "building" and building_id is not None else "/saved"
    query = urlencode({"db": request.state.db_mode, **params})
    return RedirectResponse(f"{path}?{query}", status_code=303)


def _invalid(request: Request, heading: str, message: str):
    return templates.TemplateResponse(request, "error.html", {"heading": heading, "message": message}, status_code=422)


def _read(request: Request, building_id: str, note: str | None = None):
    """Validated (building id, note) from the form fields, or an error response."""
    bid = parse_building_id(building_id)
    if bid is None:
        return _invalid(request, "Invalid block", "Choose a valid block.")
    if note is None:
        return bid, None
    try:
        return bid, clean_note(note[:RAW_MAX])
    except ValueError:
        return _invalid(request, "Note too long", TOO_LONG)


@router.get("/saved", response_class=HTMLResponse)
def saved_list(request: Request):
    if request.state.account is None:
        return _login(request)
    blocks = call_repo(request, "saved", "list", request.state.account.buyer_id, label="saved list")
    notice = notice_from(request.query_params, request.state.db_mode)
    return templates.TemplateResponse(request, "saved.html", {"blocks": blocks, "notice": notice})


@router.post("/saved")
def save_block(
    request: Request,
    building_id: str = Form(""),
    note: str = Form(""),
    simulate_failure: str = Form(""),
    back: str = Form("saved"),
):
    account = request.state.account
    if account is None:
        return _login(request)
    parsed = _read(request, building_id, note)
    if not isinstance(parsed, tuple):
        return parsed
    bid, clean = parsed
    result = call_repo(
        request, "saved", "save", account.buyer_id, bid, clean, simulate_failure == "1", label="save block"
    )
    if isinstance(result, RolledBack):
        return _back(request, back, bid, rolled_back_params(result))
    return _back(request, back, bid, {"notice": notice_key("save", result)})


@router.post("/saved/note")
def edit_note(
    request: Request,
    building_id: str = Form(""),
    note: str = Form(""),
    back: str = Form("saved"),
):
    account = request.state.account
    if account is None:
        return _login(request)
    parsed = _read(request, building_id, note)
    if not isinstance(parsed, tuple):
        return parsed
    bid, clean = parsed
    result = call_repo(request, "saved", "edit_note", account.buyer_id, bid, clean, label="edit note")
    return _back(request, back, bid, {"notice": notice_key("edit", result)})


@router.post("/saved/remove")
def remove_block(request: Request, building_id: str = Form(""), back: str = Form("saved")):
    account = request.state.account
    if account is None:
        return _login(request)
    parsed = _read(request, building_id)
    if not isinstance(parsed, tuple):
        return parsed
    bid, _ = parsed
    result: Outcome = call_repo(request, "saved", "remove", account.buyer_id, bid, label="remove block")
    return _back(request, back, bid, {"notice": notice_key("remove", result)})
