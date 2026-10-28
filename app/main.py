"""FastAPI entry point. Run with: uvicorn app.main:create_app --factory"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import Databases
from app.routers import admin, auth, building, health, rankings, saved, search, trends
from app.services.admin import AdminState
from app.services.auth import LoginThrottle
from app.services.dbmode import COOKIE, COOKIE_MAX_AGE, MODES, requested_mode, resolve_mode
from app.services.guards import csrf_guard, load_account
from app.templating import templates

BASE = Path(__file__).resolve().parent


def create_app() -> FastAPI:
    settings = get_settings()
    if len(settings.secret_key) < 32 or settings.secret_key.startswith("change-me"):
        raise RuntimeError("SECRET_KEY must be set to a random value of at least 32 characters (see .env.example)")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.dbs = Databases.open(settings)
        try:
            yield
        finally:
            app.state.dbs.close()

    app = FastAPI(title="Neighboar", lifespan=lifespan, dependencies=[Depends(csrf_guard), Depends(load_account)])
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="neighboar_session",
        max_age=8 * 3600,
        same_site="lax",
        https_only=False,  # local demo over http://localhost
    )
    app.state.templates = templates
    app.state.throttle = LoginThrottle()  # per process; see docs
    app.state.admin = AdminState()  # per process: the admin write lock and the stale-figures flag

    @app.middleware("http")
    async def db_mode(request: Request, call_next):
        """Resolve the database for this request, reject a bad ?db=, and remember a valid one in a cookie."""
        request.state.timings = []
        param = requested_mode(request)
        request.state.db_mode = resolve_mode(request)
        if param is not None and param not in MODES:
            return templates.TemplateResponse(
                request,
                "error.html",
                {"heading": "Unknown database", "message": "The db parameter must be pg or mongo."},
                status_code=400,
            )
        response = await call_next(request)
        if param is not None:
            response.set_cookie(
                COOKIE, param, max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax"
            )
        return response

    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
    app.include_router(health.router)
    app.include_router(building.router)
    app.include_router(search.router)
    app.include_router(trends.router)
    app.include_router(rankings.router)
    app.include_router(auth.router)
    app.include_router(saved.router)
    app.include_router(admin.router)
    return app
