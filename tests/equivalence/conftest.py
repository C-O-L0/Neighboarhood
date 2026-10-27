"""Fixtures for the equivalence tests: both repos are called directly as the app's own identities."""

import psycopg
import pytest
from pymongo import MongoClient

from app.config import get_settings


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def settle(settings):
    """Refresh the town-month MV before any comparison (BUILD_PLAN section 6, "settle step").

    Connects as app_admin in autocommit mode, as etl/load_pg.py does, so the refresh is
    committed and the connection is left idle. Yields that connection.
    """
    with psycopg.connect(settings.pg_dsn("app_admin"), autocommit=True) as conn:
        conn.execute("SELECT refresh_town_stats()")
        yield conn


@pytest.fixture(scope="session")
def pg_conn(settings, settle):
    """app_rw in autocommit mode, as the app's pool hands connections to repos."""
    with psycopg.connect(settings.pg_dsn("app_rw"), autocommit=True) as conn:
        yield conn


@pytest.fixture(scope="session")
def mongo_db(settings, settle):
    client = MongoClient(settings.mongo_uri("app", direct=True), serverSelectionTimeoutMS=5000, tz_aware=True)
    yield client[settings.mongo_db]
    client.close()
