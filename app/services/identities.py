"""Which database identity a request uses: the one place that picks a pool or client.

Normal routes use `app_rw` (PostgreSQL) and `app` (MongoDB). Admin routes use `app_admin` in both and never touch
the normal pair, not even to look up the signed-in account (app.services.guards.load_account follows the same
rule for paths under /admin). The `rw_pg`, `app_mongo`, `admin_pg` and `admin_mongo` functions are FastAPI
dependencies; each yields only for its own DB mode and None otherwise, so a request checks out one connection.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager

from fastapi import Request
from psycopg import Connection
from pymongo.database import Database

ADMIN_PREFIX = "/admin"


def is_admin_path(path: str) -> bool:
    return path == ADMIN_PREFIX or path.startswith(ADMIN_PREFIX + "/")


def pg_conn(request: Request, *, admin: bool = False) -> AbstractContextManager[Connection]:
    """A pooled autocommit connection as app_admin (admin=True) or app_rw."""
    dbs = request.app.state.dbs
    return (dbs.pg_admin if admin else dbs.pg_rw).connection(timeout=5)


def mongo_db(request: Request, *, admin: bool = False) -> Database:
    return request.app.state.dbs.mongo(admin=admin)


def rw_pg(request: Request) -> Iterator[Connection | None]:
    if request.state.db_mode != "pg":
        yield None
        return
    with pg_conn(request) as conn:
        yield conn


def app_mongo(request: Request) -> Database | None:
    return mongo_db(request) if request.state.db_mode == "mongo" else None


def admin_pg(request: Request) -> Iterator[Connection | None]:
    if request.state.db_mode != "pg":
        yield None
        return
    with pg_conn(request, admin=True) as conn:
        yield conn


def admin_mongo(request: Request) -> Database | None:
    return mongo_db(request, admin=True) if request.state.db_mode == "mongo" else None
