"""Connections for the benchmark, set up as app/db.py and docs/conventions.md set them up.

PostgreSQL: psycopg with autocommit=True and the default prepare_threshold, so repos can open a real
transaction with conn.transaction(). MongoDB: PyMongo with tz_aware=True and the replica-set URI the app uses.
The settings are written to env.json (CONNECTION_SETTINGS).
"""

from __future__ import annotations

import psycopg
from pymongo import MongoClient
from pymongo.database import Database

from app.config import Settings, get_settings

APPLICATION_NAME = "neighboar-bench"

CONNECTION_SETTINGS = {
    "postgresql": {
        "driver": f"psycopg {psycopg.__version__}",
        "autocommit": True,
        "prepare_threshold": "default (5)",
        "application_name": APPLICATION_NAME,
    },
    "mongodb": {
        "driver": "pymongo",
        "tz_aware": True,
        "uri_style": "replica set, as app/db.py",
        "appname": APPLICATION_NAME,
        "serverSelectionTimeoutMS": 10000,
    },
}


def settings() -> Settings:
    return get_settings()


def open_pg(role: str, s: Settings | None = None) -> psycopg.Connection:
    """An autocommit connection as the given PostgreSQL role (timed reads use app_ro)."""
    s = s or settings()
    return psycopg.connect(s.pg_dsn(role, APPLICATION_NAME), autocommit=True)


def open_mongo(user: str, s: Settings | None = None) -> tuple[MongoClient, Database]:
    """A client and its database as the given MongoDB user (timed reads use app)."""
    s = s or settings()
    client = MongoClient(
        s.mongo_uri(user),
        serverSelectionTimeoutMS=CONNECTION_SETTINGS["mongodb"]["serverSelectionTimeoutMS"],
        tz_aware=True,
        appname=APPLICATION_NAME,
    )
    return client, client[s.mongo_db]


def password_values(s: Settings | None = None) -> list[str]:
    """Every configured secret, so tests can check none of them reaches a results file."""
    s = s or settings()
    values = [v for k, v in s.model_dump().items() if (k.endswith("_password") or k == "secret_key") and v]
    return sorted(set(values))
