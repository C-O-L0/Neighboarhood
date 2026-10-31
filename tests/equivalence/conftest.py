"""Fixtures for the equivalence tests: both repos are called directly as the app's own identities."""

import secrets

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


@pytest.fixture(scope="session")
def owner_conn(settings):
    """The schema owner in autocommit mode, to delete test buyers (cascade removes their saves)."""
    if not settings.pg_owner_password:
        pytest.skip("PG_OWNER_PASSWORD is not set; pass it to the test container")
    with psycopg.connect(settings.pg_dsn("neighboar_owner"), autocommit=True) as conn:
        yield conn


@pytest.fixture(scope="session")
def etl_db(settings):
    """The ETL user, which may delete buyer documents the app user cannot."""
    if not settings.mongo_etl_password:
        pytest.skip("MONGO_ETL_PASSWORD is not set; pass it to the test container")
    client = MongoClient(settings.mongo_uri("etl", direct=True), serverSelectionTimeoutMS=5000, tz_aware=True)
    yield client[settings.mongo_db]
    client.close()


@pytest.fixture
def new_buyers(pg_conn, mongo_db, owner_conn, etl_db):
    """Factory for fresh test buyers (one per database, created through the account repos).

    Returns (pg_id, mongo_id). Every buyer made is deleted afterwards, so the synthetic buyers 1 to 200
    are never touched.
    """
    from app.repos.mongo.accounts import MongoAccountRepo
    from app.repos.pg.accounts import PgAccountRepo

    made: list[tuple[int, int]] = []

    def make() -> tuple[int, int]:
        email = f"test-saved-{secrets.token_hex(6)}@example.com"
        pg_id = PgAccountRepo(pg_conn).create(email, "not-a-real-hash", None)
        mongo_id = MongoAccountRepo(mongo_db).create(email, "not-a-real-hash", None)
        assert isinstance(pg_id, int) and isinstance(mongo_id, int)
        made.append((pg_id, mongo_id))
        return pg_id, mongo_id

    yield make
    pg_ids, mongo_ids = [p for p, _ in made], [m for _, m in made]
    # Their households too: PostgreSQL cascades members and invitations, but a household row outlives its members.
    owner_conn.execute(
        "DELETE FROM household WHERE household_id IN"
        " (SELECT household_id FROM household_member WHERE buyer_id = ANY(%s))",
        (pg_ids,),
    )
    owner_conn.execute("DELETE FROM buyer WHERE buyer_id = ANY(%s)", (pg_ids,))
    etl_db.households.delete_many({"members.buyer_id": {"$in": mongo_ids}})
    etl_db.invitations.delete_many({"$or": [{"_id": {"$in": mongo_ids}}, {"invitee_id": {"$in": mongo_ids}}]})
    etl_db.buyers.delete_many({"_id": {"$in": mongo_ids}})


@pytest.fixture(scope="session")
def admin_mongo_db(settings, settle):
    """The app_admin MongoDB client, as the admin routes use it (transactions need the replica set)."""
    client = MongoClient(settings.mongo_uri("app_admin", direct=True), serverSelectionTimeoutMS=5000, tz_aware=True)
    yield client[settings.mongo_db]
    client.close()


@pytest.fixture
def admin_cleanup(settings, settle):
    """Records the state before an admin test and puts both databases back after it, pass or fail."""
    from tests.admin_support import AdminCleanup

    cleanup = AdminCleanup(settings)
    yield cleanup
    cleanup.finish()
