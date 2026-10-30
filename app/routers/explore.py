"""Home (the guided start) and Explore (towns and blocks that fit): UC1 and UC9.

Home asks for a flat type, a budget and, optionally, a place to live near. Explore answers with the towns that
fit, the blocks that fit and their explainer tags. Every figure comes from the recent window ending at the data
month (request.state.data_month). A typed place is matched to blocks first: one match is used, several are
offered as a choice, none is reported on the form.
"""

from __future__ import annotations

from math import ceil
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.services.explain import Subject, tags
from app.services.explore import (
    FEW_SALES,
    PAGE_SIZE,
    BlocksQuery,
    best_fit,
    block_filter,
    closest,
    error_messages,
    lease_left_years,
    window_ending,
)
from app.services.maps import ATTRIBUTION, MAX_ZOOM, TILE_URL
from app.services.places import DEFAULT_RADIUS_M, RADII_M, SUGGESTIONS, parse_place
from app.services.repos import call_repo
from app.services.search import RESALE_TYPES
from app.templating import money, money_short, proper, templates
from etl.clean import TOWN_IDS

router = APIRouter()

# Query parameters Explore reads; anything else in the URL is ignored. place_text is what the buyer typed.
PARAMS = ("flat_type", "budget", "place_text", "place", "radius", "town", "min_lease", "min_area", "page")
TEXT_MAX = 100  # longer values are cut before they are echoed back into the form


def _values(request: Request) -> dict[str, str]:
    """The Explore parameters as given, blanks dropped, each cut to TEXT_MAX characters."""
    q = request.query_params
    return {k: q[k][:TEXT_MAX] for k in PARAMS if q.get(k, "").strip()}


def render_home(request: Request, status: int = 200, *, values: dict | None = None, errors=None, **lookup):
    """The guided start, with the flat type ranges of the recent window. lookup carries the block lookup's
    block, street and message (app/routers/building.py)."""
    month = request.state.data_month
    ranges = []
    if month is not None:
        ranges = call_repo(request, "explore", "flat_type_ranges", window_ending(month), label="flat type ranges")
    context = {
        "values": values or {},
        "errors": errors or [],
        "ranges": {r.flat_type: r for r in ranges},
        "flat_types": RESALE_TYPES,
        "few_sales": FEW_SALES,
        "radii": RADII_M,
        "default_radius": DEFAULT_RADIUS_M,
        "block": "",
        "street": "",
        "message": None,
        **lookup,
    }
    return templates.TemplateResponse(request, "home.html", context, status_code=status)


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return render_home(request, values=_values(request))


def map_data(rows, place, radius_m: int) -> dict | None:
    """The page's located blocks as map pins, and the chosen place with its radius. None when nothing is located.

    Coordinates go out as strings so the JSON is the same from both databases (docs/conventions.md, 2).
    """
    pins = [
        {
            "lat": str(b.latitude),
            "lon": str(b.longitude),
            "label": money_short(b.median_price),
            "name": f"{b.block} {proper(b.street)}",
            "detail": f"{money(b.median_price)} typical · {b.n} sale{'' if b.n == 1 else 's'}",
            "href": f"/building/{b.building_id}",
            "chosen": place is not None and b.building_id == place.building_id,
        }
        for b in rows
        if b.latitude is not None
    ]
    centre = None
    if place is not None and place.located:
        centre = {"lat": str(place.latitude), "lon": str(place.longitude), "radius_m": radius_m}
    if not pins and centre is None:
        return None
    return {"pins": pins, "place": centre}


def _link(values: dict, **changes) -> str:
    """/explore with the current values, some changed; a change to None removes that value."""
    merged = {**values, **changes}
    return "/explore?" + urlencode([(k, merged[k]) for k in PARAMS if merged.get(k) is not None])


@router.get("/explore", response_class=HTMLResponse)
def explore(request: Request):
    values = _values(request)
    month = request.state.data_month
    if month is None:
        return render_home(
            request, 503, values=values, errors=["No sales are loaded yet, so there is nothing to compare."]
        )
    try:
        query = BlocksQuery.model_validate({k: v for k, v in values.items() if k != "place_text"})
    except ValidationError as exc:
        return render_home(request, 422, values=values, errors=error_messages(exc))

    # The place: an id from a previous choice wins; otherwise the typed text is matched to blocks.
    place, choices = None, []
    if query.place is not None:
        place = call_repo(request, "places", "get", query.place, label="place")
        if place is None:
            return render_home(request, 422, values=values, errors=["Choose the place from the list of blocks."])
    elif "place_text" in values:
        parsed = parse_place(values["place_text"])
        if parsed is None:
            return render_home(
                request, 422, values=values, errors=["Place must be a block and street, such as 186 Bishan St 13."]
            )
        matches = call_repo(request, "places", "find", parsed.block, parsed.street, SUGGESTIONS, label="place lookup")
        if not matches:
            message = f"No block matches “{values['place_text']}”. Try the block number and street name."
            return render_home(request, 422, values=values, errors=[message])
        if len(matches) == 1:
            place = matches[0]
        else:
            choices = matches
    if place is not None and place.located:
        query = query.model_copy(update={"place": place.building_id})
        values = {**values, "place": str(place.building_id)}

    window = window_ending(month)
    towns = []
    if query.place is None:
        towns = call_repo(
            request, "explore", "towns_fit", query.flat_type, query.budget, window, label="towns that fit"
        )
    baselines = call_repo(request, "explore", "baselines", query.flat_type, window, label="town baselines")
    by_town = {b.town: b for b in baselines}
    page = call_repo(request, "explore", "blocks_fit", block_filter(query, window), label="blocks that fit")
    blocks = [
        {
            "b": b,
            "tags": tags(Subject(b.storey_sum, b.lease_year, b.area_median), by_town.get(b.town)),
            "lease_left": None if b.lease_year is None else lease_left_years(b.lease_year, window.end),
        }
        for b in page.rows
    ]
    pages = max(1, ceil(page.total / PAGE_SIZE))
    larger = next((r for r in RADII_M if r > query.radius), None)
    context = {
        "q": query,
        "values": values,
        "window": window,
        "place": place,
        "choices": choices,
        "towns": towns,
        "best": best_fit(towns, query.budget),
        "closest": closest(towns, query.budget),
        "blocks": blocks,
        "total": page.total,
        "pages": pages,
        "few_sales": FEW_SALES,
        "suggestions": SUGGESTIONS,
        "radii": RADII_M,
        "larger_radius": larger,
        "all_towns": sorted(TOWN_IDS),
        "link": lambda **changes: _link(values, **changes),
        "map": map_data(page.rows, place if place is not None and place.located else None, query.radius),
        "tile_url": TILE_URL,
        "attribution": ATTRIBUTION,
        "max_zoom": MAX_ZOOM,
        "change_link": "/?"
        + urlencode({k: v for k, v in values.items() if k in ("flat_type", "budget", "place_text", "radius")}),
    }
    return templates.TemplateResponse(request, "explore.html", context)
