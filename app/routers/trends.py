"""Town trend (S5, UC3): a summary sentence, the monthly and rolling 12-month typical price, and the sales count."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.services.charts import trends_chart
from app.services.explore import FEW_SALES
from app.services.repos import call_repo
from app.services.search import RESALE_TYPES
from app.services.trends import FIELDS, TrendQuery, error_messages, summarise
from app.templating import templates
from etl.clean import TOWNS

router = APIRouter()

ECHO_LIMIT = 100  # longest value echoed back into the form


@router.get("/trends", response_class=HTMLResponse)
def trends(request: Request):
    form = {k: request.query_params.get(k, "")[:ECHO_LIMIT] for k in FIELDS}
    raw = {k: request.query_params.get(k, "") for k in FIELDS if k in request.query_params}
    context = {
        "f": form,
        "towns": sorted(name for _, name in TOWNS),
        "flat_types": RESALE_TYPES,
        "errors": [],
        "points": None,
        "few_sales": FEW_SALES,
        # Set after a failed refresh of the town-month view; only PostgreSQL reads that view.
        "stale_since": request.app.state.admin.stale_since if request.state.db_mode == "pg" else None,
    }
    if not raw:  # first visit: the empty form, no error
        return templates.TemplateResponse(request, "trends.html", context)
    try:
        query = TrendQuery.model_validate(raw)
    except ValidationError as exc:
        context["errors"] = error_messages(exc)
        return templates.TemplateResponse(request, "trends.html", context, status_code=422)
    context["points"] = call_repo(request, "trends", "trend", query, label="trends")
    context["chart"] = trends_chart(context["points"])
    context["summary"] = summarise(context["points"])
    context["q"] = query
    return templates.TemplateResponse(request, "trends.html", context)
