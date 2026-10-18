"""FastAPI entry point. Run with: uvicorn app.main:create_app --factory"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import Databases
from app.routers import health

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE / "templates")


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

    app = FastAPI(title="Neighboar", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="neighboar_session",
        max_age=8 * 3600,
        same_site="lax",
        https_only=False,  # local demo over http://localhost
    )
    app.state.templates = templates
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
    app.include_router(health.router)
    return app
