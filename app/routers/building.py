"""Home page, block lookup and the building page."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services.repos import call_repo
from app.services.saved import notice_from
from app.templating import templates
from etl.clean import normalise_key

router = APIRouter()

MAX_BLOCK = 5  # building.block VARCHAR(5)
MAX_STREET = 40  # building.street VARCHAR(40)
MAX_ID = 2**31 - 1  # building.building_id is INT


def _home(request: Request, status: int = 200, **ctx):
    context = {"block": "", "street": "", "message": None, **ctx}
    return templates.TemplateResponse(request, "home.html", context, status_code=status)


def _not_found(request: Request, what: str):
    return templates.TemplateResponse(request, "not_found.html", {"what": what}, status_code=404)


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return _home(request)


@router.get("/building/find")
def find_building(request: Request, block: str = "", street: str = ""):
    nb, ns = normalise_key(block), normalise_key(street)
    if not 1 <= len(nb) <= MAX_BLOCK or not 1 <= len(ns) <= MAX_STREET:
        message = f"Enter a block of 1 to {MAX_BLOCK} characters and a street of 1 to {MAX_STREET} characters."
        return _home(request, 422, block=nb[:100], street=ns[:100], message=message)
    building_id = call_repo(request, "building", "find_id", nb, ns, label="block lookup")
    if building_id is None:
        return _not_found(request, f"Block {nb} {ns}")
    return RedirectResponse(f"/building/{building_id}", status_code=303)


@router.get("/building/{building_id:int}", response_class=HTMLResponse)
def building_page(request: Request, building_id: int):
    detail = None
    if 1 <= building_id <= MAX_ID:
        detail = call_repo(request, "building", "get", building_id, label="building page")
    if detail is None:
        return _not_found(request, f"Building {building_id}")
    food = None  # a block without coordinates has no food access ("Unknown" on the page)
    if detail.latitude is not None:
        food = call_repo(request, "food", "block", building_id, label="food access")
    saved = None
    if request.state.account is not None:
        blocks = call_repo(request, "saved", "list", request.state.account.buyer_id, label="saved list")
        saved = next((s for s in blocks if s.building_id == building_id), None)
    notice = notice_from(request.query_params, request.state.db_mode)
    context = {"b": detail, "food": food, "saved": saved, "notice": notice}
    return templates.TemplateResponse(request, "building.html", context)
