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
def pg_ro(settings):
    if not settings.pg_app_ro_password:
        pytest.skip("PG_APP_RO_PASSWORD is not set; pass it to the test container")
    with psycopg.connect(settings.pg_dsn("app_ro")) as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def pg_owner(settings):
    """The schema owner, for rules the app roles cannot reach. Every write is rolled back."""
    if not settings.pg_owner_password:
        pytest.skip("PG_OWNER_PASSWORD is not set; pass it to the test container")
    with psycopg.connect(settings.pg_dsn("neighboar_owner")) as conn:
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


@pytest.fixture(scope="session")
def mongo_etl(settings):
    """The ETL user (readWrite), for collections the app users cannot write. Tests delete what they insert."""
    if not settings.mongo_etl_password:
        pytest.skip("MONGO_ETL_PASSWORD is not set; pass it to the test container")
    client, db = _mongo(settings, "etl")
    yield db
    client.close()


@pytest.fixture(scope="session")
def demo_passwords(settings):
    """(buyer, admin) demo passwords for etl/synth_buyers.py; skips when unset or unusable."""
    from etl.synth_buyers import check_demo_passwords

    pair = (settings.demo_buyer_password, settings.demo_admin_password)
    try:
        check_demo_passwords(*pair)
    except ValueError as exc:
        pytest.skip(f"{exc}; pass DEMO_BUYER_PASSWORD and DEMO_ADMIN_PASSWORD to the test container")
    return pair
