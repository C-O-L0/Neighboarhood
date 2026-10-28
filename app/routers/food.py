"""Food access by town: each geocoded block with its nearest open hawker centre."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.services.food import FIELDS, FoodQuery, error_messages
from app.services.repos import call_repo
from app.templating import templates
from etl.clean import TOWNS

router = APIRouter()

ECHO_LIMIT = 100  # longest value echoed back into the form


@router.get("/food", response_class=HTMLResponse)
def food(request: Request):
    form = {k: request.query_params.get(k, "")[:ECHO_LIMIT] for k in FIELDS}
    raw = {k: request.query_params.get(k, "") for k in FIELDS if k in request.query_params}
    context = {"f": form, "towns": sorted(name for _, name in TOWNS), "errors": [], "blocks": None}
    if not raw:  # first visit: the empty form, no error
        return templates.TemplateResponse(request, "food.html", context)
    try:
        query = FoodQuery.model_validate(raw)
    except ValidationError as exc:
        context["errors"] = error_messages(exc)
        return templates.TemplateResponse(request, "food.html", context, status_code=422)
    context["blocks"] = call_repo(request, "food", "town", query.town, label="town food access")
    return templates.TemplateResponse(request, "food.html", context)
