"""Repo dispatch: pick the database for the request, time only the repo call, record the timing."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

from fastapi import Request

from app.repos.mongo.accounts import MongoAccountRepo
from app.repos.mongo.admin import MongoAdminRepo
from app.repos.mongo.building import MongoBuildingRepo
from app.repos.mongo.explore import MongoExploreRepo
from app.repos.mongo.food import MongoFoodRepo
from app.repos.mongo.households import MongoHouseholdRepo
from app.repos.mongo.places import MongoPlaceRepo
from app.repos.mongo.prices import MongoPriceRepo
from app.repos.mongo.rankings import MongoRankingRepo
from app.repos.mongo.saved import MongoSavedRepo
from app.repos.mongo.search import MongoSearchRepo
from app.repos.mongo.trends import MongoTrendRepo
from app.repos.pg.accounts import PgAccountRepo
from app.repos.pg.admin import PgAdminRepo
from app.repos.pg.building import PgBuildingRepo
from app.repos.pg.explore import PgExploreRepo
from app.repos.pg.food import PgFoodRepo
from app.repos.pg.households import PgHouseholdRepo
from app.repos.pg.places import PgPlaceRepo
from app.repos.pg.prices import PgPriceRepo
from app.repos.pg.rankings import PgRankingRepo
from app.repos.pg.saved import PgSavedRepo
from app.repos.pg.search import PgSearchRepo
from app.repos.pg.trends import PgTrendRepo
from app.services.behind import Call, MongoRecorder, PgRecorder
from app.services.dbmode import Timing
from app.services.identities import mongo_db, pg_conn

# feature -> (PostgreSQL repo class, MongoDB repo class); later features register here.
REPOS = {
    "building": (PgBuildingRepo, MongoBuildingRepo),
    "search": (PgSearchRepo, MongoSearchRepo),
    "trends": (PgTrendRepo, MongoTrendRepo),
    "rankings": (PgRankingRepo, MongoRankingRepo),
    "accounts": (PgAccountRepo, MongoAccountRepo),
    "saved": (PgSavedRepo, MongoSavedRepo),
    "households": (PgHouseholdRepo, MongoHouseholdRepo),
    "admin": (PgAdminRepo, MongoAdminRepo),
    "food": (PgFoodRepo, MongoFoodRepo),
    "explore": (PgExploreRepo, MongoExploreRepo),
    "places": (PgPlaceRepo, MongoPlaceRepo),
    "prices": (PgPriceRepo, MongoPriceRepo),
}


def call_repo(request: Request, feature: str, method: str, *args: Any, label: str | None = None) -> Any:
    """Run repo.method(*args) on the database chosen for this request, as app_rw / app (admin routes use identities.py).

    The pool checkout is outside the timed span; the span covers the repo call only,
    and repos materialise their results, so it includes the fetch.
    """
    mode = request.state.db_mode
    pg_cls, mongo_cls = REPOS[feature]
    record = getattr(request.state, "demo", False)  # behind the scenes is a demo-mode view (app/services/behind.py)
    recorder = None
    if mode == "pg":
        with pg_conn(request) as conn:
            recorder = PgRecorder(conn) if record else None
            fn = getattr(pg_cls(recorder or conn), method)
            started = time.perf_counter_ns()
            result = fn(*args)
            elapsed = time.perf_counter_ns() - started
    else:
        db = mongo_db(request)
        recorder = MongoRecorder(db) if record else None
        fn = getattr(mongo_cls(recorder or db), method)
        started = time.perf_counter_ns()
        result = fn(*args)
        elapsed = time.perf_counter_ns() - started
    name = label or f"{feature}.{method}"
    if recorder is not None:
        elapsed -= recorder.overhead_ns  # the recorder's own bookkeeping is not the database's time
        statements = recorder.statements if mode == "pg" else []
        calls = recorder.calls if mode == "mongo" else []
        session = request.session.get("csrf", "")
        call = Call(name, mode, elapsed / 1e6, statements, calls)
        captures = request.app.state.captures
        if not hasattr(request.state, "capture_id"):
            request.state.capture_id = captures.new_id()
        captures.add(session, request.state.capture_id, here(request), call)
    request.state.timings.append(Timing(name, mode, elapsed / 1e6))
    return result


def here(request: Request) -> str:
    """The page's path and query, without db (the mode is shown separately)."""
    query = urlencode([(k, v) for k, v in request.query_params.multi_items() if k != "db"])
    return request.url.path + (f"?{query}" if query else "")
