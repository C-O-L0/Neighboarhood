"""Block lookup and the block profile (S3: UC4, UC6). Home, where the lookup form sits, is in app/routers/explore.py.

The profile was the building page at /building/{id}; the old paths redirect, so saved links keep working.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.repos.dto import BlockFoodAccess, BuildingDetail
from app.routers.explore import render_home
from app.services.block import CPF_LEASE_URL, FIELDS, TEXT_MAX, headline, lease_check, lease_left, summary
from app.services.explain import Subject, tags
from app.services.explore import FEW_SALES, window_ending
from app.services.maps import ATTRIBUTION, MAX_ZOOM, TILE_URL
from app.services.repos import call_repo
from app.services.saved import notice_from
from app.templating import distance, proper, templates
from etl.clean import normalise_key
from etl.streets import abbreviate_street

router = APIRouter()

MAX_BLOCK = 5  # building.block VARCHAR(5)
MAX_STREET = 40  # building.street VARCHAR(40)
MAX_ID = 2**31 - 1  # building.building_id is INT


def _not_found(request: Request, what: str):
    return templates.TemplateResponse(request, "not_found.html", {"what": what}, status_code=404)


def _moved(request: Request, path: str) -> RedirectResponse:
    query = request.url.query
    return RedirectResponse(path + (f"?{query}" if query else ""), status_code=308)


@router.get("/block/find")
def find_block(request: Request, block: str = "", street: str = ""):
    nb, ns = normalise_key(block), abbreviate_street(street)  # "17 marine terrace" finds 17 MARINE TER
    if not 1 <= len(nb) <= MAX_BLOCK or not 1 <= len(ns) <= MAX_STREET:
        message = f"Enter a block of 1 to {MAX_BLOCK} characters and a street of 1 to {MAX_STREET} characters."
        return render_home(request, 422, block=nb[:100], street=ns[:100], message=message)
    building_id = call_repo(request, "building", "find_id", nb, ns, label="block lookup")
    if building_id is None:
        return _not_found(request, f"Block {nb} {ns}")
    return RedirectResponse(f"/block/{building_id}", status_code=303)


@router.get("/building/find")
def old_find(request: Request):
    return _moved(request, "/block/find")


@router.get("/building/{building_id:int}")
def old_profile(request: Request, building_id: int):
    return _moved(request, f"/block/{building_id}")


def map_data(b: BuildingDetail, food: BlockFoodAccess | None) -> dict | None:
    """The block and its nearest open hawker centres as map pins; None for a block without coordinates.

    Coordinates go out as strings so the JSON is the same from both databases (docs/conventions.md, 2).
    """
    if b.latitude is None:
        return None
    pins = [
        {
            "lat": str(b.latitude),
            "lon": str(b.longitude),
            "label": f"Blk {b.block}",
            "name": f"{b.block} {proper(b.street)}",
            "detail": "This block",
            "chosen": True,
        }
    ]
    for h in food.nearest if food else []:
        pins.append(
            {
                "lat": str(h.latitude),
                "lon": str(h.longitude),
                "label": distance(h.distance_m),
                "name": h.name,
                "detail": f"Hawker centre · {distance(h.distance_m)} away",
                "kind": "hawker",
            }
        )
    return {"pins": pins, "place": None}


@router.get("/block/{building_id:int}", response_class=HTMLResponse)
def block_profile(request: Request, building_id: int):
    detail = None
    if 1 <= building_id <= MAX_ID:
        detail = call_repo(request, "building", "get", building_id, label="block profile")
    if detail is None:
        return _not_found(request, f"Block {building_id}")
    b = detail
    food = None  # a block without coordinates has no food access (UC4 A2)
    if b.latitude is not None:
        food = call_repo(request, "food", "block", building_id, label="food access")

    month = request.state.data_month
    now = (month or date.today()).year
    recent, rows = [], []
    if month is not None:
        window = window_ending(month)
        recent = call_repo(request, "explore", "block_recent", building_id, window, label="recent prices")
        for r in recent:
            baselines = call_repo(request, "explore", "baselines", r.flat_type, window, label="town baselines")
            baseline = next((t for t in baselines if t.town == b.town), None)
            found = tags(Subject(r.storey_sum, b.lease_commence_year, r.area_median), baseline)
            rows.append({"r": r, "tags": found})
    top = headline(recent)
    top_tags = next((row["tags"] for row in rows if row["r"] is top), None)

    saved = None
    if request.state.account is not None:
        blocks = call_repo(request, "saved", "list", request.state.account.buyer_id, label="saved list")
        saved = next((s for s in blocks if s.building_id == building_id), None)

    lease_values = {k: request.query_params.get(k, "")[:TEXT_MAX] for k in FIELDS}
    check, lease_errors = None, []
    if b.lease_commence_year is not None:
        check, lease_errors = lease_check(lease_values, b.lease_commence_year, now)

    trend_query = {"town": b.town, **({"flat_type": top.flat_type} if top else {})}
    near_query = {**({"flat_type": top.flat_type} if top else {}), "place_text": f"{b.block} {proper(b.street)}"}
    context = {
        "b": b,
        "food": food,
        "saved": saved,
        "notice": notice_from(request.query_params, request.state.db_mode),
        "summary": summary(b, food, now),
        "now": now,
        "lease_now": None if b.lease_commence_year is None else lease_left(b.lease_commence_year, now),
        "recent": rows,
        "top": top,
        "top_tags": top_tags,
        "few_sales": FEW_SALES,
        "check": check,
        "lease_errors": lease_errors,
        "lease_values": lease_values,
        "cpf_url": CPF_LEASE_URL,
        "price_link": "/price-check?"
        + urlencode({"place": building_id, **({"flat_type": top.flat_type} if top else {})}),
        "trend_link": "/trends?" + urlencode(trend_query),
        "near_link": "/?" + urlencode(near_query),
        "sales_link": "/search?" + urlencode({"town": b.town, "block": b.block, "street": b.street}),
        "map": map_data(b, food),
        "tile_url": TILE_URL,
        "attribution": ATTRIBUTION,
        "max_zoom": MAX_ZOOM,
    }
    return templates.TemplateResponse(request, "block.html", context)
