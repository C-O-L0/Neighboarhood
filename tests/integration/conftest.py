"""Connections for database-level integration tests, as the app's own least-privilege users.

These need the stack running with data loaded (CI loads data/sample). From the host,
copy compose.override.yaml.example to compose.override.yaml and set PG_HOST=localhost
MONGO_HOST=localhost.
"""

import psycopg
import pytest
from pymongo import MongoClient

from app.config import get_settings


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture
def pg_rw(settings):
    with psycopg.connect(settings.pg_dsn("app_rw")) as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def pg_admin(settings):
    with psycopg.connect(settings.pg_dsn("app_admin")) as conn:
        yield conn
        conn.rollback()


def _mongo(settings, user):
    client = MongoClient(settings.mongo_uri(user, direct=True), serverSelectionTimeoutMS=5000, tz_aware=True)
    return client, client[settings.mongo_db]


@pytest.fixture(scope="session")
def mongo_app(settings):
    client, db = _mongo(settings, "app")
    yield db
    client.close()


@pytest.fixture(scope="session")
def mongo_admin_client(settings):
    client, _ = _mongo(settings, "app_admin")
    yield client
    client.close()


@pytest.fixture(scope="session")
def mongo_admin(mongo_admin_client, settings):
    return mongo_admin_client[settings.mongo_db]
