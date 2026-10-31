"""Price check (S4, UC2): is an asking price fair against comparable past sales?

The buyer names a block (typed, or a block id from the block profile or a previous choice), the flat type, an
optional storey band, the floor area and the asking price. The form opens empty or prefilled; the check runs
once an area or a price is submitted. Comparables widen from the block to its street to its town until there
are enough (app/services/prices.py); every level tried is one timed repo call.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.repos.dto import Place
from app.services.explain import Subject, tags
from app.services.places import SUGGESTIONS, parse_place
from app.services.prices import (
    MIN_COMPARABLES,
    SHOWN,
    STOREY_BANDS,
    Level,
    PriceQuery,
    Scope,
    baseline,
    error_messages,
    storey_value,
    verdict,
    window_ending,
)
from app.services.repos import call_repo
from app.services.search import RESALE_TYPES
from app.templating import flat_label, money, proper, storey, templates

router = APIRouter()

TEXT_MAX = 100  # longer values are cut before they are echoed back into the form
PARAMS = ("place_text", "place", "flat_type", "storey", "area", "price")


def place_label(p: Place) -> str:
    return f"{p.block} {proper(p.street)}"


def _values(request: Request) -> dict[str, str]:
    q = request.query_params
    return {k: q[k][:TEXT_MAX] for k in PARAMS if q.get(k, "").strip()}


def _page(request: Request, status: int = 200, **context):
    base = {
        "values": {},
        "errors": [],
        "choices": [],
        "place": None,
        "result": None,
        "flat_types": RESALE_TYPES,
        "storeys": [(storey_value(b), storey(*b)) for b in STOREY_BANDS],
        "suggestions": SUGGESTIONS,
    }
    return templates.TemplateResponse(request, "price_check.html", {**base, **context}, status_code=status)


def _link(values: dict, **changes) -> str:
    merged = {**values, **changes}
    return "/price-check?" + urlencode([(k, merged[k]) for k in PARAMS if merged.get(k) is not None])


def _resolve(request: Request, query: PriceQuery, values: dict) -> tuple[Place | None, list[Place], str | None]:
    """The block the buyer means: (place, [], None), several to choose from ([...]), or an error message.

    A block id is used while the text still shows that block; once the buyer edits the text, the text wins.
    """
    if query.place is not None:
        place = call_repo(request, "places", "get", query.place, label="block")
        if place is not None and (query.place_text is None or query.place_text.strip() == place_label(place)):
            return place, [], None
        if place is None and query.place_text is None:
            return None, [], "Choose the block from the list."
    parsed = parse_place(query.place_text or "")
    if parsed is None:
        return None, [], "Block and street must be like 186 Bishan St 13."
    if parsed.block is None:
        choices = call_repo(request, "places", "find", None, parsed.street, SUGGESTIONS, label="block lookup")
        if choices:
            return None, choices, None
        return None, [], f"No street matches “{values.get('place_text', '')}”. Add the block number and street."
    matches = call_repo(request, "places", "find", parsed.block, parsed.street, SUGGESTIONS, label="block lookup")
    if len(matches) == 1:
        return matches[0], [], None
    if matches:
        return None, matches, None
    similar = call_repo(request, "places", "find", None, parsed.street, SUGGESTIONS, label="similar blocks")
    if similar:
        return (
            None,
            similar,
            f"No block {parsed.block} on a street like “{parsed.street.title()}”. Did you mean one of these?",
        )
    return None, [], f"No block matches “{values.get('place_text', '')}”. Try the block number and street name."


@router.get("/price-check", response_class=HTMLResponse)
def price_check(request: Request):
    values = _values(request)
    submitted = "area" in request.query_params or "price" in request.query_params
    if not submitted:
        # Opened from a block profile: show the block in the form; nothing is checked yet.
        place = None
        if values.get("place", "").isdigit() and len(values["place"]) <= 10:
            place = call_repo(request, "places", "get", int(values["place"]), label="block")
        if place is not None:
            values = {**values, "place_text": place_label(place)}
        return _page(request, values=values, place=place)

    try:
        query = PriceQuery.model_validate(values)
    except ValidationError as exc:
        return _page(request, 422, values=values, errors=error_messages(exc))
    month = request.state.data_month
    if month is None:
        return _page(request, 503, values=values, errors=["No sales are loaded yet, so there is nothing to compare."])

    place, choices, message = _resolve(request, query, values)
    if place is None:
        status = 200 if choices and message is None else 422
        links = [{"p": c, "href": _link(values, place=str(c.building_id), place_text=place_label(c))} for c in choices]
        return _page(request, status, values=values, errors=[message] if message else [], choices=links)
    values = {**values, "place": str(place.building_id), "place_text": place_label(place)}

    window = window_ending(month)
    storey_min = query.storey[0] if query.storey else None
    tried: list[tuple[Level, int]] = []
    sales, level = [], None
    for lvl in Level:
        scope = Scope(lvl, place.building_id, place.street, place.town, query.flat_type, window, storey_min)
        sales = call_repo(request, "prices", "comparables", scope, label=f"comparables ({lvl})")
        tried.append((lvl, len(sales)))
        if len(sales) >= MIN_COMPARABLES:
            level = lvl
            break
    if level is None and sales:
        level = Level.TOWN  # fewer than enough even in the town: use them, flagged as few

    result = None
    if level is not None:
        subject = Subject(sum(query.storey) if query.storey else None, place.lease_year, query.area)
        result = {
            "level": level,
            "tried": tried,
            "verdict": verdict(sales, query.area, query.price),
            "tags": tags(subject, baseline(sales, place.town)),
            "sales": sales[:SHOWN],
            "few": len(sales) < MIN_COMPARABLES,
        }
    note = " · ".join(
        part
        for part in (
            f"Asking {money(query.price)}",
            flat_label(query.flat_type),
            storey(*query.storey) if query.storey else None,
            f"{query.area} sqm",
        )
        if part
    )
    saved = None
    if request.state.account is not None:
        blocks = call_repo(request, "saved", "list", request.state.account.buyer_id, label="saved list")
        saved = next((s for s in blocks if s.building_id == place.building_id), None)
    return _page(
        request,
        values=values,
        place=place,
        q=query,
        window=window,
        result=result,
        tried=tried,
        note=note,
        saved=saved,
        trend_link="/trends?" + urlencode({"town": place.town, "flat_type": query.flat_type}),
    )
