"""Repo dispatch: pick the database for the request, time only the repo call, record the timing."""

from __future__ import annotations

import time
from typing import Any

from fastapi import Request

from app.repos.mongo.accounts import MongoAccountRepo
from app.repos.mongo.building import MongoBuildingRepo
from app.repos.mongo.rankings import MongoRankingRepo
from app.repos.mongo.saved import MongoSavedRepo
from app.repos.mongo.search import MongoSearchRepo
from app.repos.mongo.trends import MongoTrendRepo
from app.repos.pg.accounts import PgAccountRepo
from app.repos.pg.building import PgBuildingRepo
from app.repos.pg.rankings import PgRankingRepo
from app.repos.pg.saved import PgSavedRepo
from app.repos.pg.search import PgSearchRepo
from app.repos.pg.trends import PgTrendRepo
from app.services.dbmode import Timing

# feature -> (PostgreSQL repo class, MongoDB repo class); later features register here.
REPOS = {
    "building": (PgBuildingRepo, MongoBuildingRepo),
    "search": (PgSearchRepo, MongoSearchRepo),
    "trends": (PgTrendRepo, MongoTrendRepo),
    "rankings": (PgRankingRepo, MongoRankingRepo),
    "accounts": (PgAccountRepo, MongoAccountRepo),
    "saved": (PgSavedRepo, MongoSavedRepo),
}


def call_repo(request: Request, feature: str, method: str, *args: Any, label: str | None = None) -> Any:
    """Run repo.method(*args) on the database chosen for this request.

    The pool checkout is outside the timed span; the span covers the repo call only,
    and repos materialise their results, so it includes the fetch.
    """
    mode = request.state.db_mode
    dbs = request.app.state.dbs
    pg_cls, mongo_cls = REPOS[feature]
    if mode == "pg":
        with dbs.pg_rw.connection(timeout=5) as conn:
            fn = getattr(pg_cls(conn), method)
            started = time.perf_counter_ns()
            result = fn(*args)
            elapsed = time.perf_counter_ns() - started
    else:
        fn = getattr(mongo_cls(dbs.mongo()), method)
        started = time.perf_counter_ns()
        result = fn(*args)
        elapsed = time.perf_counter_ns() - started
    request.state.timings.append(Timing(label or f"{feature}.{method}", mode, elapsed / 1e6))
    return result
