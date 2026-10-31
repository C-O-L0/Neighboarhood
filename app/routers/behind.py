"""Behind the scenes (S12, UC8): the health of both databases, what the last page ran, and the benchmark.

The page itself queries nothing through call_repo, so opening it never replaces the page it describes. With
?plans=1 it runs EXPLAIN on that page's reads: PostgreSQL EXPLAIN (ANALYZE, BUFFERS) inside a read-only
transaction that is rolled back, MongoDB explain with executionStats, as the benchmark does (bench/capture.py).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app.routers.health import status
from app.services.behind import (
    CHARTS,
    is_read,
    latest_benchmark,
    show_mongo,
    show_params,
    summary_mongo,
    summary_pg,
)
from app.services.identities import mongo_db, pg_conn
from app.templating import templates
from bench.capture import explain_mongo, explain_pg, render_mongo_explain, render_pg_plan

router = APIRouter()


def _explain(request: Request, page) -> dict[int, list[str]]:
    """Rendered plans per call index. A failure is shown in place of the plan, never raised."""
    plans: dict[int, list[str]] = {}
    for i, call in enumerate(page.calls):
        try:
            if call.mode == "pg":
                with pg_conn(request) as conn:
                    explain_pg(conn, call.pg)
                plans[i] = [render_pg_plan(st.plan) for st in call.pg if st.plan]
            else:
                reads = [c for c in call.mongo if is_read(c)]
                explains = explain_mongo(mongo_db(request), reads)
                plans[i] = [render_mongo_explain(c, e) for c, e in zip(reads, explains, strict=True)]
        except Exception as exc:  # a plan is a nicety; the page must still render
            plans[i] = [f"Could not explain: {exc.__class__.__name__}"]
    return plans


@router.get("/demo", response_class=HTMLResponse)
def behind(request: Request):
    health = status(request)
    page = request.app.state.captures.last(request.session.get("csrf", "")) if request.state.demo else None
    want_plans = request.query_params.get("plans") == "1"
    context = {
        "health": health,
        "page": page,
        "plans": _explain(request, page) if page and want_plans else {},
        "want_plans": want_plans,
        "bench": latest_benchmark(),
        "show_mongo": show_mongo,
        "show_params": show_params,
        "summary_pg": summary_pg,
        "summary_mongo": summary_mongo,
    }
    return templates.TemplateResponse(request, "behind.html", context, status_code=200 if health["ok"] else 503)


@router.get("/demo/charts/{name}.svg")
def chart(name: str):
    bench = latest_benchmark()
    if name not in CHARTS or bench is None or not (bench.folder / f"{name}.svg").is_file():
        raise HTTPException(status_code=404)
    return FileResponse(bench.folder / f"{name}.svg", media_type="image/svg+xml")


# Retired pages (T15): their questions are answered by the buyer pages, so old links land there.
@router.get("/health")
def old_health():
    return RedirectResponse("/demo", status_code=308)


@router.get("/rankings")
def old_rankings():
    return RedirectResponse("/", status_code=308)


@router.get("/food")
def old_food():
    return RedirectResponse("/", status_code=308)
