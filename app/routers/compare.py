"""Compare (S7): 2 to 4 blocks side by side for one flat type, with lease left now and at a chosen year.

Reached from the shortlist's selection (or any link with `ids`). Each block costs the queries its profile runs:
details, food access (when located) and recent sales by flat type; the town baselines for the tags are one query
for the chosen flat type, shared by every column.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.services.compare import (
    FALLBACK_TYPE,
    MAX_BLOCKS,
    MIN_BLOCKS,
    best,
    column,
    default_flat_type,
    parse_flat_type,
    parse_ids,
    parse_year,
)
from app.services.explain import Subject, tags
from app.services.explore import FEW_SALES, window_ending
from app.services.repos import call_repo
from app.services.search import RESALE_TYPES
from app.templating import templates

router = APIRouter()

TEXT_MAX = 100


def _link(ids: list[int], flat_type: str, at: int | None) -> str:
    extra = [("flat_type", flat_type)] + ([("at", at)] if at else [])
    return "/compare?" + urlencode([("ids", i) for i in ids] + extra)


def _problem(request: Request, message: str, status: int):
    context = {"message": message, "min_blocks": MIN_BLOCKS, "max_blocks": MAX_BLOCKS}
    return templates.TemplateResponse(request, "compare.html", {**context, "columns": None}, status_code=status)


@router.get("/compare", response_class=HTMLResponse)
def compare(request: Request):
    raw_ids = [v[:TEXT_MAX] for v in request.query_params.getlist("ids")]
    ids = parse_ids(raw_ids)
    if ids is None:
        return _problem(request, f"Choose {MIN_BLOCKS} to {MAX_BLOCKS} different blocks to compare.", 422)
    month = request.state.data_month
    now = (month or date.today()).year

    details = [call_repo(request, "building", "get", i, label="block profile") for i in ids]
    missing = [i for i, d in zip(ids, details, strict=True) if d is None]
    if missing:
        return _problem(request, "One of these blocks was not found. Go back to your shortlist and choose again.", 404)
    recent = [[] for _ in ids]
    window = None
    if month is not None:
        window = window_ending(month)
        recent = [call_repo(request, "explore", "block_recent", i, window, label="recent prices") for i in ids]

    asked = request.query_params.get("flat_type", "")[:TEXT_MAX]
    flat_type = parse_flat_type(asked) or default_flat_type(recent) or FALLBACK_TYPE
    raw_at = request.query_params.get("at", "")[:TEXT_MAX]
    at = parse_year(raw_at, now)
    baselines = {}
    if window is not None:
        found = call_repo(request, "explore", "baselines", flat_type, window, label="town baselines")
        baselines = {b.town: b for b in found}

    columns = []
    for b, rows in zip(details, recent, strict=True):
        food = call_repo(request, "food", "block", b.building_id, label="food access") if b.latitude else None
        mine = next((r for r in rows if r.flat_type == flat_type), None)
        found_tags = None
        if mine is not None:
            found_tags = tags(Subject(mine.storey_sum, b.lease_commence_year, mine.area_median), baselines.get(b.town))
        columns.append(column(b, food, mine, found_tags, now, at))

    context = {
        "columns": columns,
        "best": best(columns),
        "flat_type": flat_type,
        "flat_types": RESALE_TYPES,
        "ids": ids,
        "now": now,
        "at": at,
        "at_text": raw_at,
        "at_error": bool(raw_at.strip()) and at is None,
        "few_sales": FEW_SALES,
        "min_blocks": MIN_BLOCKS,
        "drop": {i: _link([j for j in ids if j != i], flat_type, at) for i in ids},
    }
    return templates.TemplateResponse(request, "compare.html", context)
