"""Search and filter over resale sales."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.services.repos import call_repo
from app.services.search import FIELDS, ORDERS, RESALE_TYPES, SORTS, SearchQuery, error_messages
from app.templating import templates
from etl.clean import FLAT_MODELS, TOWNS

router = APIRouter()

ECHO_LIMIT = 100  # longest value echoed back into the form


def _link(request: Request, form: dict[str, str], page: int) -> str:
    """Link to another page of the same search: every filter and the DB mode are kept."""
    params = [(k, v) for k, v in form.items() if v.strip() and k not in ("page", "db")]
    params.append(("page", str(page)))
    params.append(("db", request.state.db_mode))
    return f"/search?{urlencode(params)}"


@router.get("/search", response_class=HTMLResponse)
def search(request: Request):
    form = {k: request.query_params.get(k, "")[:ECHO_LIMIT] for k in FIELDS}
    raw = {k: request.query_params.get(k, "") for k in FIELDS if k in request.query_params}
    context = {
        "f": form,
        "towns": sorted(name for _, name in TOWNS),
        "flat_types": RESALE_TYPES,
        "flat_models": sorted(FLAT_MODELS),
        "sorts": list(SORTS),
        "orders": ORDERS,
        "errors": [],
        "result": None,
    }
    try:
        query = SearchQuery.model_validate(raw)
    except ValidationError as exc:
        context["errors"] = error_messages(exc)
        return templates.TemplateResponse(request, "search.html", context, status_code=422)
    result = call_repo(request, "search", "search", query, label="search")
    context["result"] = result
    context["first"] = (result.page - 1) * 50 + 1
    context["prev_link"] = _link(request, form, min(result.page - 1, result.pages)) if result.page > 1 else None
    context["next_link"] = _link(request, form, result.page + 1) if result.page < result.pages else None
    return templates.TemplateResponse(request, "search.html", context)
