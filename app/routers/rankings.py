"""Town rankings: median price rank per flat type and whether each town beats the island median."""

from __future__ import annotations

from itertools import groupby

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.services.rankings import FIELDS, YEARS, RankingQuery, error_messages
from app.services.repos import call_repo
from app.services.search import RESALE_TYPES
from app.templating import templates

router = APIRouter()

ECHO_LIMIT = 100  # longest value echoed back into the form


@router.get("/rankings", response_class=HTMLResponse)
def rankings(request: Request):
    form = {k: request.query_params.get(k, "")[:ECHO_LIMIT] for k in FIELDS}
    raw = {k: request.query_params.get(k, "") for k in FIELDS if k in request.query_params}
    context = {
        "f": form,
        "years": YEARS,
        "flat_types": RESALE_TYPES,
        "errors": [],
        "groups": None,
    }
    if not raw:  # first visit: the empty form, no error
        return templates.TemplateResponse(request, "rankings.html", context)
    try:
        query = RankingQuery.model_validate(raw)
    except ValidationError as exc:
        context["errors"] = error_messages(exc)
        return templates.TemplateResponse(request, "rankings.html", context, status_code=422)
    rows = call_repo(request, "rankings", "rank", query, label="rankings")
    context["groups"] = [(name, list(items)) for name, items in groupby(rows, key=lambda r: r.flat_type)]
    return templates.TemplateResponse(request, "rankings.html", context)
