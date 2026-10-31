"""Health check: proves the app reaches both databases as its least-privilege users.

/api/health answers in JSON; the page that shows it to people is /demo (app/routers/behind.py).
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


def _check_postgres(request: Request) -> dict:
    started = time.perf_counter_ns()
    try:
        with request.app.state.dbs.pg_rw.connection(timeout=3) as conn:
            user, version = conn.execute("SELECT current_user, current_setting('server_version')").fetchone()
        return {"ok": True, "user": user, "version": version, "ms": (time.perf_counter_ns() - started) / 1e6}
    except Exception as exc:  # report, don't crash the health page
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}


def _check_mongo(request: Request) -> dict:
    started = time.perf_counter_ns()
    try:
        db = request.app.state.dbs.mongo()
        status = db.command("connectionStatus")
        users = [u["user"] for u in status["authInfo"]["authenticatedUsers"]]
        build = db.command("buildInfo")
        hello = db.command("hello")
        return {
            "ok": True,
            "user": users[0] if users else None,
            "version": build["version"],
            "replica_set": hello.get("setName"),
            "primary": hello.get("isWritablePrimary", False),
            "ms": (time.perf_counter_ns() - started) / 1e6,
        }
    except Exception as exc:
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}


def status(request: Request) -> dict:
    pg, mongo = _check_postgres(request), _check_mongo(request)
    return {"ok": pg["ok"] and mongo["ok"], "postgres": pg, "mongodb": mongo}


@router.get("/api/health")
def health_json(request: Request):
    result = status(request)
    return JSONResponse(result, status_code=200 if result["ok"] else 503)
