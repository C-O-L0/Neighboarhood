"""Loaded data, integrity rules and least privilege, in both databases.

Every write here is rolled back (PostgreSQL) or deleted (MongoDB), so the tests
can run against a loaded database without changing it.
"""

from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest
from bson.decimal128 import Decimal128
from psycopg import errors
from pymongo.errors import OperationFailure, WriteError

pytestmark = pytest.mark.integration

UNAUTHORIZED = 13
VALIDATION_FAILED = 121


# ---------------------------------------------------------------------- loaded data


def test_both_databases_hold_the_same_snapshot(pg_rw, mongo_app):
    pairs = {
        "building": "building_profiles",
        "resale_transaction": "resale_transactions",
        "hawker_centre": "hawker_centres",
    }
    for table, coll in pairs.items():
        n = pg_rw.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608 - fixed table names
        assert n > 0, f"{table} is empty; load data first (etl.pipeline --sample)"
        assert mongo_app[coll].count_documents({}) == n, coll
    assert pg_rw.execute("SELECT count(*) FROM mv_town_month_stats").fetchone()[0] > 0
    assert (
        mongo_app.counters.find_one({"_id": "txn"})["seq"]
        == pg_rw.execute("SELECT max(txn_id) FROM resale_transaction").fetchone()[0]
    )


def test_block_stats_agree(pg_rw, mongo_app):
    """Embedded Mongo stats equal the SQL aggregates (median exact, avg_psm within 0.01)."""
    sql = """
        SELECT building_id, count(*), percentile_disc(0.5) WITHIN GROUP (ORDER BY resale_price),
               round(avg(resale_price / floor_area_sqm), 2), max(txn_month),
               (array_agg(txn_id ORDER BY txn_month DESC, txn_id DESC))[1:10]
        FROM resale_transaction GROUP BY building_id"""
    expected = {r[0]: r[1:] for r in pg_rw.execute(sql)}
    docs = list(
        mongo_app.building_profiles.find({"stats.txn_count": {"$gt": 0}}, {"stats": 1, "recent_sales.txn_id": 1})
    )
    assert len(docs) == len(expected)
    for d in docs:
        n, median, psm, last, recent = expected[d["_id"]]
        st = d["stats"]
        assert st["txn_count"] == n
        assert st["median_price"].to_decimal() == median
        assert abs(st["avg_psm"].to_decimal() - psm) <= Decimal("0.01")
        assert st["last_txn_month"].date() == last
        assert [s["txn_id"] for s in d["recent_sales"]] == recent


def test_derived_units_view(pg_rw):
    row = pg_rw.execute(
        "SELECT count(*), count(*) FILTER (WHERE total_units = sold_units + rental_units) FROM v_building_units"
    ).fetchone()
    assert row[0] == row[1] > 0


# ---------------------------------------------------------------------- PostgreSQL rules


def _any_sale(conn):
    return conn.execute(
        """SELECT t.building_id, t.flat_type_id, t.flat_model_id FROM resale_transaction t
           JOIN building b USING (building_id) WHERE b.year_completed IS NOT NULL LIMIT 1"""
    ).fetchone()


def _insert_sale(conn, building_id, flat_type_id, flat_model_id, **over):
    v = {
        "txn_month": "2025-01-01",
        "storey_min": 1,
        "storey_max": 3,
        "floor_area_sqm": Decimal("90.0"),
        "remaining_lease_months": 800,
        "resale_price": Decimal("500000.00"),
        **over,
    }
    conn.execute(
        """INSERT INTO resale_transaction (building_id, flat_type_id, flat_model_id, txn_month, storey_min,
               storey_max, floor_area_sqm, remaining_lease_months, resale_price)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (building_id, flat_type_id, flat_model_id, *v.values()),
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"resale_price": Decimal("0")},
        {"txn_month": "2025-01-02"},
        {"storey_min": 5, "storey_max": 3},
        {"storey_max": 61},
        {"floor_area_sqm": Decimal("400.0")},
        {"remaining_lease_months": 1189},
    ],
)
def test_check_constraints_reject_bad_sales(pg_admin, bad):
    sale = _any_sale(pg_admin)
    with pytest.raises(errors.CheckViolation):
        _insert_sale(pg_admin, *sale, **bad)


def test_valid_sale_is_accepted_by_admin(pg_admin):
    _insert_sale(pg_admin, *_any_sale(pg_admin))  # rolled back by the fixture


def test_trigger_rejects_sale_before_completion(pg_admin):
    b, ft, fm, year = pg_admin.execute(
        """SELECT t.building_id, t.flat_type_id, t.flat_model_id, b.year_completed FROM resale_transaction t
           JOIN building b USING (building_id) ORDER BY b.year_completed DESC NULLS LAST LIMIT 1"""
    ).fetchone()
    with pytest.raises(errors.CheckViolation, match="predates block completion"):
        _insert_sale(pg_admin, b, ft, fm, txn_month=f"{year - 1}-06-01")


def test_trigger_rejects_non_resale_flat_type(pg_admin):
    b, _, fm = _any_sale(pg_admin)
    studio = pg_admin.execute("SELECT flat_type_id FROM flat_type WHERE NOT is_resale_type LIMIT 1").fetchone()[0]
    with pytest.raises(errors.CheckViolation, match="not sold on the resale market"):
        _insert_sale(pg_admin, b, studio, fm)


def test_saved_blocks_capped_at_50(pg_rw):
    buyer = pg_rw.execute(
        "INSERT INTO buyer (email, password_hash) VALUES ('cap-test@example.com', 'x') RETURNING buyer_id"
    ).fetchone()[0]
    ids = [r[0] for r in pg_rw.execute("SELECT building_id FROM building ORDER BY building_id LIMIT 51")]
    for b in ids[:50]:
        pg_rw.execute("INSERT INTO saved_building (buyer_id, building_id) VALUES (%s, %s)", (buyer, b))
    with pytest.raises(errors.CheckViolation, match="already has 50"):
        pg_rw.execute("INSERT INTO saved_building (buyer_id, building_id) VALUES (%s, %s)", (buyer, ids[50]))


def test_email_unique_ignoring_case(pg_rw):
    pg_rw.execute("INSERT INTO buyer (email, password_hash) VALUES ('Case@Example.com', 'x')")
    with pytest.raises(errors.UniqueViolation):
        pg_rw.execute("INSERT INTO buyer (email, password_hash) VALUES ('case@example.com', 'x')")


def test_deleting_buyer_cascades_to_saved_blocks(pg_rw):
    buyer = pg_rw.execute(
        "INSERT INTO buyer (email, password_hash) VALUES ('fk-test@example.com', 'x') RETURNING buyer_id"
    ).fetchone()[0]
    pg_rw.execute("INSERT INTO saved_building (buyer_id, building_id) VALUES (%s, 1)", (buyer,))
    pg_rw.execute("DELETE FROM buyer WHERE buyer_id = %s", (buyer,))
    assert pg_rw.execute("SELECT count(*) FROM saved_building WHERE buyer_id = %s", (buyer,)).fetchone()[0] == 0


# ---------------------------------------------------------------------- PostgreSQL privileges


@pytest.mark.parametrize(
    "stmt",
    [
        "DELETE FROM resale_transaction WHERE txn_id = 1",
        "UPDATE building SET max_floor = 10 WHERE building_id = 1",
        "DELETE FROM hawker_centre",
        "SELECT refresh_town_stats()",
        "SELECT * FROM schema_migration",
        "CREATE TABLE intruder (x int)",
    ],
)
def test_app_rw_is_least_privilege(pg_rw, stmt):
    with pytest.raises(errors.InsufficientPrivilege):
        pg_rw.execute(stmt)


def test_app_admin_can_refresh_but_not_change_buildings(pg_admin):
    pg_admin.execute("SELECT refresh_town_stats()")
    with pytest.raises(errors.InsufficientPrivilege):
        pg_admin.execute("DELETE FROM building WHERE building_id = 1")


def test_app_roles_cannot_connect_to_postgres_db(settings):
    dsn = settings.pg_dsn("app_rw").replace(f"dbname={settings.postgres_db}", "dbname=postgres")
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(dsn).close()


# ---------------------------------------------------------------------- MongoDB rules and privileges


def _valid_sale(mongo_app, _id):
    s = mongo_app.resale_transactions.find_one({}, {"_id": 0})
    return {**s, "_id": _id}


def test_mongo_app_user_cannot_write_sales(mongo_app):
    with pytest.raises(OperationFailure) as exc:
        mongo_app.resale_transactions.insert_one(_valid_sale(mongo_app, -1))
    assert exc.value.code == UNAUTHORIZED


@pytest.mark.parametrize(
    "bad",
    [
        {"resale_price": 500000.0},  # double, not Decimal128
        {"flat_type": "STUDIO APARTMENT"},
        {"storey_max": 61},
        {"remaining_lease_months": -1},
        {"unexpected_field": 1},
    ],
)
def test_mongo_validator_rejects_bad_sales(mongo_app, mongo_admin, bad):
    doc = {**_valid_sale(mongo_app, -2), **bad}
    try:
        with pytest.raises(WriteError) as exc:
            mongo_admin.resale_transactions.insert_one(doc)
        assert exc.value.code == VALIDATION_FAILED
    finally:
        mongo_admin.resale_transactions.delete_one({"_id": -2})


@pytest.mark.parametrize(
    "bad",
    [
        {"email": "Upper@Example.com"},
        {
            "saved": [
                {"building_id": i, "block": "1", "street": "X", "town": "Y", "saved_at": datetime.now(UTC)}
                for i in range(51)
            ]
        },
    ],
)
def test_mongo_buyer_validator(mongo_app, bad):
    doc = {
        "_id": -3,
        "email": "ok@example.com",
        "password_hash": "x",
        "is_admin": False,
        "created_at": datetime.now(UTC),
        "saved": [],
        **bad,
    }
    try:
        with pytest.raises(WriteError) as exc:
            mongo_app.buyers.insert_one(doc)
        assert exc.value.code == VALIDATION_FAILED
    finally:
        mongo_app.buyers.delete_one({"_id": -3})


def test_mongo_transaction_rolls_back(mongo_app, mongo_admin, mongo_admin_client):
    doc = _valid_sale(mongo_app, -4)
    doc["resale_price"] = Decimal128("123456.78")

    class Boom(Exception):
        pass

    with mongo_admin_client.start_session() as session:
        with pytest.raises(Boom):
            with session.start_transaction():
                mongo_admin.resale_transactions.insert_one(doc, session=session)
                assert mongo_admin.resale_transactions.count_documents({"_id": -4}, session=session) == 1
                raise Boom
    assert mongo_admin.resale_transactions.count_documents({"_id": -4}) == 0
