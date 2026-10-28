"""Admin pages: record and delete a sale, rebuild the derived statistics, and the rollback demos.

Every route needs a signed-in administrator (`require_admin`, 403 otherwise) and uses only the admin database
identities (app.services.identities: `app_admin` in both databases). CSRF is enforced for every POST by
app.services.guards. Writes go through app.services.admin, which serialises them under one lock.
"""

from __future__ import annotations

import time
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from psycopg import Connection
from pymongo.database import Database

from app.services import admin as service
from app.services.admin import FIELDS, LIST_LIMIT, AdminState
from app.services.dbmode import Timing
from app.services.guards import require_admin
from app.services.identities import admin_mongo, admin_pg
from app.services.repos import REPOS
from app.services.search import RESALE_TYPES
from app.templating import templates
from etl.clean import FLAT_MODELS

PgConn = Annotated[Connection | None, Depends(admin_pg)]
MongoDb = Annotated[Database | None, Depends(admin_mongo)]

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

ECHO_LIMIT = 100  # longest value echoed back into the form
RAW_MAX = 200  # form fields longer than this are cut before validation; every valid value is far shorter


def _repo(pg: Connection | None, mongo: Database | None):
    pg_cls, mongo_cls = REPOS["admin"]
    return pg_cls(pg) if pg is not None else mongo_cls(mongo)


def _state(request: Request) -> AdminState:
    return request.app.state.admin


def _back(request: Request) -> RedirectResponse:
    return RedirectResponse(f"/admin?{urlencode({'db': request.state.db_mode})}", status_code=303)


def _page(request: Request, repo, status: int = 200, errors=None, delete_errors=None, form=None):
    started = time.perf_counter_ns()
    sales = repo.admin_sales(LIST_LIMIT)
    elapsed = (time.perf_counter_ns() - started) / 1e6
    request.state.timings.append(Timing("admin sales list", request.state.db_mode, elapsed))
    context = {
        "sales": sales,
        "errors": errors or [],
        "delete_errors": delete_errors or [],
        "f": form or {},
        "flat_types": RESALE_TYPES,
        "flat_models": sorted(FLAT_MODELS),
        "last": _state(request).last,
        "stale_since": _state(request).stale_since,
        "list_limit": LIST_LIMIT,
    }
    return templates.TemplateResponse(request, "admin.html", context, status_code=status)


def _message(request: Request, heading: str, message: str, status: int):
    context = {"heading": heading, "message": message}
    return templates.TemplateResponse(request, "error.html", context, status_code=status)


@router.get("", response_class=HTMLResponse)
def admin_home(request: Request, pg: PgConn, mongo: MongoDb):
    return _page(request, _repo(pg, mongo))


@router.post("/sales")
def record_sale(
    request: Request,
    pg: PgConn,
    mongo: MongoDb,
    block: str = Form(""),
    street: str = Form(""),
    month: str = Form(""),
    flat_type: str = Form(""),
    flat_model: str = Form(""),
    storey_min: str = Form(""),
    storey_max: str = Form(""),
    floor_area_sqm: str = Form(""),
    remaining_lease_months: str = Form(""),
    resale_price: str = Form(""),
    simulate_failure: str = Form(""),
):
    raw = {
        "block": block, "street": street, "month": month, "flat_type": flat_type, "flat_model": flat_model,
        "storey_min": storey_min, "storey_max": storey_max, "floor_area_sqm": floor_area_sqm,
        "remaining_lease_months": remaining_lease_months, "resale_price": resale_price,
    }  # fmt: skip
    raw = {k: v[:RAW_MAX] for k, v in raw.items()}
    form, errors = service.parse_sale_form(raw)
    repo = _repo(pg, mongo)
    if form is None:  # nothing has reached either database
        echo = {k: raw[k][:ECHO_LIMIT] for k in FIELDS}
        return _page(request, repo, status=422, errors=errors, form=echo)
    service.record_sale(_state(request), request.state.db_mode, repo, form, simulate_failure == "1")
    return _back(request)


@router.post("/sales/delete")
def delete_sale(
    request: Request,
    pg: PgConn,
    mongo: MongoDb,
    txn_id: str = Form(""),
    simulate_failure: str = Form(""),
):
    repo = _repo(pg, mongo)
    parsed = service.parse_txn_id(txn_id[:RAW_MAX])
    if parsed is None:
        message = f"The sale id must be a whole number from 1 to {service.MAX_TXN_ID:,}."
        return _page(request, repo, status=422, delete_errors=[message])
    service.delete_sale(_state(request), request.state.db_mode, repo, parsed, simulate_failure == "1")
    return _back(request)


@router.post("/refresh")
def refresh(request: Request, pg: PgConn, mongo: MongoDb):
    if request.state.db_mode != "pg":
        return _message(request, "Not available", "The town figures refresh applies to PostgreSQL mode.", 400)
    service.refresh_town_figures(_state(request), _repo(pg, mongo))
    return _back(request)


@router.post("/rebuild-stats")
def rebuild_stats(request: Request, pg: PgConn, mongo: MongoDb):
    if request.state.db_mode != "mongo":
        return _message(request, "Not available", "The block statistics rebuild applies to MongoDB mode.", 400)
    service.rebuild_block_stats(_state(request), _repo(pg, mongo))
    return _back(request)
