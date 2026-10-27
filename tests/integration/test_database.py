"""Loaded data, integrity rules and least privilege, in both databases.

Every write here is rolled back (PostgreSQL) or deleted (MongoDB), so the tests
can run against a loaded database without changing it.
"""

from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest
from bson.decimal128 import Decimal128
from psycopg import errors, sql
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
    query = """
        SELECT building_id, count(*), percentile_disc(0.5) WITHIN GROUP (ORDER BY resale_price),
               round(avg(resale_price / floor_area_sqm), 2), max(txn_month),
               (array_agg(txn_id ORDER BY txn_month DESC, txn_id DESC))[1:10]
        FROM resale_transaction GROUP BY building_id"""
    expected = {r[0]: r[1:] for r in pg_rw.execute(query)}
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
        """SELECT count(*), count(*) FILTER (WHERE total_units IS NOT DISTINCT FROM sold_units + rental_units)
           FROM v_building_units"""
    ).fetchone()
    assert row[0] == row[1] > 0


def test_stub_units_are_unknown_not_zero(pg_rw, mongo_app):
    """A stub has no supply data: NULL units in PostgreSQL, no supply field in MongoDB.
    A real block without supply rows has 0 units and an empty supply array."""
    rows = pg_rw.execute(
        """SELECT b.in_property_dataset, u.sold_units, u.rental_units, u.total_units
           FROM building b JOIN v_building_units u USING (building_id)
           WHERE b.block = '82' AND b.street = 'MACPHERSON LANE'"""
    ).fetchall()
    assert rows == [(False, None, None, None)]
    stub = mongo_app.building_profiles.find_one({"block": "82", "street": "MACPHERSON LANE"})
    assert stub["in_property_dataset"] is False
    assert "supply" not in stub

    units = dict(pg_rw.execute("SELECT building_id, total_units FROM v_building_units").fetchall())
    for d in mongo_app.building_profiles.find({}, {"supply": 1}):
        mongo_total = sum(s["sold"] + s["rental"] for s in d["supply"]) if "supply" in d else None
        assert mongo_total == units[d["_id"]], d["_id"]


def test_loaded_sales_are_all_snapshot(pg_rw, mongo_app):
    assert pg_rw.execute("SELECT DISTINCT source FROM resale_transaction").fetchall() == [("snapshot",)]
    assert mongo_app.resale_transactions.distinct("source") == ["snapshot"]


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
        "source": "admin",
        **over,
    }
    cols = ["building_id", "flat_type_id", "flat_model_id", *v]
    conn.execute(
        sql.SQL("INSERT INTO resale_transaction ({}) VALUES ({})").format(
            sql.SQL(", ").join(map(sql.Identifier, cols)),
            sql.SQL(", ").join(sql.Placeholder() * len(cols)),
        ),
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
        {"resale_price": Decimal("-1")},
        {"floor_area_sqm": Decimal("0")},
        {"floor_area_sqm": Decimal("-1")},
        {"source": "other"},
    ],
)
def test_check_constraints_reject_bad_sales(pg_admin, bad):
    sale = _any_sale(pg_admin)
    with pytest.raises(errors.CheckViolation):
        _insert_sale(pg_admin, *sale, **bad)


def test_valid_sale_is_accepted_by_admin(pg_admin):
    _insert_sale(pg_admin, *_any_sale(pg_admin))  # rolled back by the fixture


def test_sale_without_source_is_rejected(pg_admin):
    """source has no default: an insert that forgets it fails instead of reading as snapshot."""
    b, ft, fm = _any_sale(pg_admin)
    with pytest.raises(errors.NotNullViolation):
        pg_admin.execute(
            """INSERT INTO resale_transaction (building_id, flat_type_id, flat_model_id, txn_month, storey_min,
                   storey_max, floor_area_sqm, remaining_lease_months, resale_price)
               VALUES (%s, %s, %s, '2025-01-01', 1, 3, 90.0, 800, 500000.00)""",
            (b, ft, fm),
        )


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
        {"resale_price": Decimal128("0")},
        {"resale_price": Decimal128("-1")},
        {"floor_area_sqm": Decimal128("0")},
        {"floor_area_sqm": Decimal128("-1")},
        {"floor_area_sqm": Decimal128("400")},
        {"storey_min": 5, "storey_max": 3},
        {"txn_month": datetime(2025, 1, 2, tzinfo=UTC)},
        {"txn_month": datetime(2025, 1, 1, 12, tzinfo=UTC)},
        {"txn_month": "2025-01-01"},
        {"flat_type": "STUDIO APARTMENT"},
        {"storey_max": 61},
        {"remaining_lease_months": -1},
        {"unexpected_field": 1},
        {"source": "other"},
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


@pytest.mark.parametrize("change", [{"supply": []}, {"in_property_dataset": True}])
def test_mongo_profile_validator_keeps_stub_supply_unknown(mongo_app, mongo_admin, change):
    """A stub may not carry a supply array, and a real block must have one."""
    stub = mongo_app.building_profiles.find_one({"in_property_dataset": False})
    doc = {**stub, "_id": -5, "block": "ZZ1", **change}
    try:
        with pytest.raises(WriteError) as exc:
            mongo_admin.building_profiles.insert_one(doc)
        assert exc.value.code == VALIDATION_FAILED
    finally:
        mongo_admin.building_profiles.delete_one({"_id": -5})


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


@pytest.mark.parametrize("month", [datetime(2024, 2, 1, tzinfo=UTC), datetime(2025, 12, 1, tzinfo=UTC)])
def test_mongo_valid_sale_is_accepted(mongo_app, mongo_admin, month):
    doc = {**_valid_sale(mongo_app, -6), "source": "admin", "txn_month": month,
           "storey_min": 1, "storey_max": 1, "floor_area_sqm": Decimal128("399.9"),
           "resale_price": Decimal128("0.01")}
    try:
        mongo_admin.resale_transactions.insert_one(doc)
        assert mongo_admin.resale_transactions.find_one({"_id": -6}) is not None
    finally:
        mongo_admin.resale_transactions.delete_one({"_id": -6})


# ---------------------------------------------------------------------- validator parity: helpers


def _expect_rejected(coll, doc):
    try:
        with pytest.raises(WriteError) as exc:
            coll.insert_one(doc)
        assert exc.value.code == VALIDATION_FAILED
    finally:
        coll.delete_one({"_id": doc["_id"]})


def _expect_accepted(coll, doc):
    try:
        coll.insert_one(doc)
        assert coll.count_documents({"_id": doc["_id"]}) == 1
    finally:
        coll.delete_one({"_id": doc["_id"]})


def _last_sale_value(conn, column):
    return conn.execute(
        sql.SQL("SELECT {} FROM resale_transaction ORDER BY txn_id DESC LIMIT 1").format(sql.Identifier(column))
    ).fetchone()[0]


# ---------------------------------------------------------------------- validator parity: sales


def test_mongo_rejects_price_with_more_than_2dp_pg_would_round(pg_admin, mongo_app, mongo_admin):
    """MongoDB rejects 500000.005; PostgreSQL does not reject it, it stores 500000.01."""
    doc = {**_valid_sale(mongo_app, -7), "source": "admin", "resale_price": Decimal128("500000.005")}
    _expect_rejected(mongo_admin.resale_transactions, doc)
    _insert_sale(pg_admin, *_any_sale(pg_admin), resale_price=Decimal("500000.005"))
    assert _last_sale_value(pg_admin, "resale_price") == Decimal("500000.01")


def test_price_at_numeric_maximum_rejected_by_both(pg_admin, mongo_app, mongo_admin):
    """NUMERIC(9,2) tops out at 9999999.99; 10000000.00 overflows in PostgreSQL and fails the Mongo bound."""
    with pytest.raises(errors.NumericValueOutOfRange):
        _insert_sale(pg_admin, *_any_sale(pg_admin), resale_price=Decimal("10000000.00"))
    pg_admin.rollback()
    doc = {**_valid_sale(mongo_app, -8), "source": "admin", "resale_price": Decimal128("10000000.00")}
    _expect_rejected(mongo_admin.resale_transactions, doc)


def test_area_399_95_rejected_by_both(pg_admin, mongo_app, mongo_admin):
    """PostgreSQL rounds 399.95 to 400.0 and then the < 400 CHECK fails; MongoDB rejects the scale."""
    with pytest.raises(errors.CheckViolation):
        _insert_sale(pg_admin, *_any_sale(pg_admin), floor_area_sqm=Decimal("399.95"))
    pg_admin.rollback()
    doc = {**_valid_sale(mongo_app, -9), "source": "admin", "floor_area_sqm": Decimal128("399.95")}
    _expect_rejected(mongo_admin.resale_transactions, doc)


def test_mongo_rejects_area_with_more_than_1dp_pg_would_round(pg_admin, mongo_app, mongo_admin):
    """MongoDB rejects 85.25; PostgreSQL does not reject it, it stores 85.3."""
    doc = {**_valid_sale(mongo_app, -10), "source": "admin", "floor_area_sqm": Decimal128("85.25")}
    _expect_rejected(mongo_admin.resale_transactions, doc)
    _insert_sale(pg_admin, *_any_sale(pg_admin), floor_area_sqm=Decimal("85.25"))
    assert _last_sale_value(pg_admin, "floor_area_sqm") == Decimal("85.3")


def test_sale_at_price_and_area_limits_accepted_by_both(pg_admin, mongo_app, mongo_admin):
    _insert_sale(pg_admin, *_any_sale(pg_admin), resale_price=Decimal("9999999.99"), floor_area_sqm=Decimal("399.9"))
    doc = {
        **_valid_sale(mongo_app, -11),
        "source": "admin",
        "resale_price": Decimal128("9999999.99"),
        "floor_area_sqm": Decimal128("399.9"),
    }
    _expect_accepted(mongo_admin.resale_transactions, doc)


# ---------------------------------------------------------------------- validator parity: buildings and hawkers

PROPERTY_ATTRS = {
    "year_completed": 2000,
    "max_floor": 10,
    "residential": True,
    "commercial": False,
    "market_hawker": False,
    "miscellaneous": False,
    "multistorey_carpark": False,
    "precinct_pavilion": False,
}
NO_ATTRS = dict.fromkeys(PROPERTY_ATTRS)
OUTSIDE_BOX = [
    {"lon": 103.58, "lat": 1.30},
    {"lon": 104.11, "lat": 1.30},
    {"lon": 103.80, "lat": 1.14},
    {"lon": 103.80, "lat": 1.49},
]


def _pg_building(conn, **over):
    """Insert a block as the owner (rolled back by the fixture); defaults to a full property block."""
    town_id = conn.execute("SELECT town_id FROM town ORDER BY town_id LIMIT 1").fetchone()[0]
    v = {
        "town_id": town_id,
        "block": "ZZ9",
        "street": "TEST STREET",
        "in_property_dataset": True,
        "lease_commence_year": 2000,
        **PROPERTY_ATTRS,
        **over,
    }
    conn.execute(
        sql.SQL("INSERT INTO building ({}) VALUES ({})").format(
            sql.SQL(", ").join(map(sql.Identifier, v)), sql.SQL(", ").join(sql.Placeholder() * len(v))
        ),
        list(v.values()),
    )


def _pg_stub(conn):
    _pg_building(conn, **{**NO_ATTRS, "in_property_dataset": False, "lease_commence_year": None})


def _profile(_id, *, stub=False, location=None, **over):
    doc = {
        "_id": _id,
        "block": "ZZ9",
        "street": "TEST STREET",
        "town": {"code": "ABC", "name": "TEST TOWN"},
        "in_property_dataset": not stub,
        "lease_commence_year": None if stub else 2000,
        **(NO_ATTRS if stub else PROPERTY_ATTRS),
        "stats": {"txn_count": 0, "median_price": None, "avg_psm": None, "by_flat_type": [], "last_txn_month": None},
        "recent_sales": [],
    }
    if not stub:
        doc["supply"] = []
    if location:
        doc["location"] = {"type": "Point", "coordinates": [location["lon"], location["lat"]]}
    return {**doc, **over}


def _pg_hawker(conn, lon, lat):
    conn.execute(
        """INSERT INTO hawker_centre (hawker_id, name, status, latitude, longitude)
           VALUES (999999, 'TEST HAWKER', 'Existing', %s, %s)""",
        (Decimal(str(lat)), Decimal(str(lon))),
    )


def _hawker(_id, lon, lat):
    return {
        "_id": _id,
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"OBJECTID": _id, "NAME": "TEST HAWKER", "STATUS": "Existing"},
    }


@pytest.mark.parametrize("attr", list(PROPERTY_ATTRS))
def test_property_block_with_null_attribute_rejected_by_both(pg_owner, mongo_etl, attr):
    """PostgreSQL building_property_attrs and the Mongo property branch both need every attribute."""
    with pytest.raises(errors.CheckViolation):
        _pg_building(pg_owner, **{attr: None})
    _expect_rejected(mongo_etl.building_profiles, _profile(-30, **{attr: None}))


def test_stub_with_year_completed_rejected_by_both(pg_owner, mongo_etl):
    with pytest.raises(errors.CheckViolation):
        _pg_building(pg_owner, **{**NO_ATTRS, "in_property_dataset": False, "year_completed": 1999})
    _expect_rejected(mongo_etl.building_profiles, _profile(-31, stub=True, year_completed=1999))


@pytest.mark.parametrize("field", ["year_completed", "lease_commence_year"])
@pytest.mark.parametrize("year", [1899, 2101])
def test_year_out_of_bounds_rejected_by_both(pg_owner, mongo_etl, field, year):
    with pytest.raises(errors.CheckViolation):
        _pg_building(pg_owner, **{field: year})
    _expect_rejected(mongo_etl.building_profiles, _profile(-32, **{field: year}))


@pytest.mark.parametrize("pt", OUTSIDE_BOX)
def test_building_coordinates_outside_box_rejected_by_both(pg_owner, mongo_etl, pt):
    with pytest.raises(errors.CheckViolation):
        _pg_building(pg_owner, latitude=Decimal(str(pt["lat"])), longitude=Decimal(str(pt["lon"])))
    _expect_rejected(mongo_etl.building_profiles, _profile(-33, location=pt))


@pytest.mark.parametrize("pt", OUTSIDE_BOX)
def test_hawker_coordinates_outside_box_rejected_by_both(pg_owner, mongo_etl, pt):
    with pytest.raises(errors.CheckViolation):
        _pg_hawker(pg_owner, pt["lon"], pt["lat"])
    _expect_rejected(mongo_etl.hawker_centres, _hawker(-34, pt["lon"], pt["lat"]))


@pytest.mark.parametrize(
    "case",
    [{"year": 1900, "lon": 103.59, "lat": 1.15}, {"year": 2100, "lon": 104.10, "lat": 1.48}],
    ids=["lower-bounds", "upper-bounds"],
)
def test_values_exactly_on_bounds_accepted_by_both(pg_owner, mongo_etl, case):
    """BETWEEN is inclusive in PostgreSQL, so the Mongo bounds are inclusive too."""
    pt = {"lon": case["lon"], "lat": case["lat"]}
    _pg_building(
        pg_owner,
        year_completed=case["year"],
        lease_commence_year=case["year"],
        latitude=Decimal(str(pt["lat"])),
        longitude=Decimal(str(pt["lon"])),
    )
    pg_owner.rollback()
    _pg_hawker(pg_owner, pt["lon"], pt["lat"])
    doc = _profile(-35, location=pt, year_completed=case["year"], lease_commence_year=case["year"])
    _expect_accepted(mongo_etl.building_profiles, doc)
    _expect_accepted(mongo_etl.hawker_centres, _hawker(-36, pt["lon"], pt["lat"]))


def test_valid_property_profile_accepted_by_both(pg_owner, mongo_etl):
    _pg_building(pg_owner, latitude=Decimal("1.35"), longitude=Decimal("103.80"))
    _expect_accepted(mongo_etl.building_profiles, _profile(-37, location={"lon": 103.80, "lat": 1.35}))


def test_valid_stub_accepted_by_both(pg_owner, mongo_etl):
    _pg_stub(pg_owner)
    _expect_accepted(mongo_etl.building_profiles, _profile(-38, stub=True))


def test_valid_non_geocoded_profile_accepted_by_both(pg_owner, mongo_etl):
    """No location is allowed: the coordinate bounds must not reject a block that was never geocoded."""
    _pg_building(pg_owner)  # latitude and longitude both NULL
    _expect_accepted(mongo_etl.building_profiles, _profile(-39))


def test_valid_hawker_accepted_by_both(pg_owner, mongo_etl):
    _pg_hawker(pg_owner, 103.85, 1.30)
    _expect_accepted(mongo_etl.hawker_centres, _hawker(-40, 103.85, 1.30))


# ---------------------------------------------------------------------- least privilege: read-only roles


def test_app_ro_cannot_write_any_table(pg_ro):
    tables = pg_ro.execute(
        """SELECT c.relname, (SELECT a.attname FROM pg_attribute a
                              WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                              ORDER BY a.attnum LIMIT 1)
           FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY c.relname"""
    ).fetchall()
    assert len(tables) >= 9
    for table, col in tables:
        t, c = sql.Identifier(table), sql.Identifier(col)
        for stmt in (
            sql.SQL("INSERT INTO {} DEFAULT VALUES").format(t),
            sql.SQL("UPDATE {} SET {} = {}").format(t, c, c),
            sql.SQL("DELETE FROM {}").format(t),
        ):
            with pytest.raises(errors.InsufficientPrivilege):
                pg_ro.execute(stmt)
            pg_ro.rollback()


def test_app_ro_cannot_refresh_town_stats(pg_ro):
    with pytest.raises(errors.InsufficientPrivilege):
        pg_ro.execute("SELECT refresh_town_stats()")


def test_mongo_app_user_cannot_insert_buildings_or_hawkers(mongo_app):
    with pytest.raises(OperationFailure) as exc:
        mongo_app.building_profiles.insert_one(_profile(-41))
    assert exc.value.code == UNAUTHORIZED
    with pytest.raises(OperationFailure) as exc:
        mongo_app.hawker_centres.insert_one(_hawker(-42, 103.85, 1.30))
    assert exc.value.code == UNAUTHORIZED
