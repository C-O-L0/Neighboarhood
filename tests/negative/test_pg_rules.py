"""One negative test per PostgreSQL write rule (task 3.5a).

Every case follows the baseline method: it first shows that a row is accepted, then changes exactly one value
(or, for the cap trigger, the one thing that makes the row the 51st), so the error can only come from the rule
under test. Each write uses the identity that could otherwise write the table (app_rw, app_admin or the schema
owner) and is rolled back. Case ids are the rule keys of tests/negative/rules.py; a rule with several clauses
that MongoDB splits has one case per clause, `<key>#<clause>`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from psycopg import sql

from tests.negative.rules import pg_rules

pytestmark = pytest.mark.integration

STAMP = datetime(2026, 1, 1, tzinfo=UTC)
ATTRS = {
    "year_completed": 1999,
    "max_floor": 5,
    "residential": True,
    "commercial": True,
    "market_hawker": True,
    "miscellaneous": True,
    "multistorey_carpark": True,
    "precinct_pavilion": True,
}
NO_ATTRS = dict.fromkeys(ATTRS)
MISSING = 999999999

IDENTITY = {
    "town": "pg_owner",
    "flat_type": "pg_owner",
    "flat_model": "pg_owner",
    "building": "pg_owner",
    "building_flat_supply": "pg_owner",
    "hawker_centre": "pg_owner",
    "resale_transaction": "pg_admin",
    "buyer": "pg_rw",
    "saved_building": "pg_rw",
    "household": "pg_rw",
    "household_member": "pg_rw",
    "household_invitation": "pg_rw",
    "household_saved": "pg_rw",
}


@dataclass
class Spec:
    table: str
    good: dict
    bad: dict = field(default_factory=dict)
    before: list[tuple[str, dict]] = field(default_factory=list)
    sqlstate: str | None = None  # for :type rules, which have no constraint or column to name
    message: str | None = None  # for triggers, which have no constraint name


def put(conn, table: str, values: dict) -> None:
    conn.execute(
        sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, values)),
            sql.SQL(", ").join(sql.Placeholder() * len(values)),
        ),
        list(values.values()),
    )


# ------------------------------------------------------------------------------------------------ rows


def town(**o):
    return {"town_id": 9001, "code": "ZZA", "name": "TEST TOWN A", **o}


def town_b(**o):
    return {"town_id": 9002, "code": "ZZB", "name": "TEST TOWN B", **o}


def flat_type(**o):
    return {"flat_type_id": 9001, "name": "TEST TYPE A", "is_resale_type": True, **o}


def flat_type_b(**o):
    return {"flat_type_id": 9002, "name": "TEST TYPE B", "is_resale_type": True, **o}


def flat_model(**o):
    return {"flat_model_id": 9001, "name": "Test model A", **o}


def flat_model_b(**o):
    return {"flat_model_id": 9002, "name": "Test model B", **o}


def block(c, **o):
    return {
        "building_id": 9000001,
        "town_id": c["town"],
        "block": "9001",
        "street": "TEST STREET",
        "lease_commence_year": 2000,
        "in_property_dataset": True,
        "latitude": Decimal("1.35"),
        "longitude": Decimal("103.80"),
        **ATTRS,
        **o,
    }


def block_b(c, **o):
    return block(c, building_id=9000002, block="9002", **o)


def stub(c, **o):
    return block(c, **{**NO_ATTRS, "in_property_dataset": False, "lease_commence_year": None, **o})


def supply(c, **o):
    return {"building_id": 9000001, "flat_type_id": c["resale_types"][0], "sold_units": 10, "rental_units": 0, **o}


def sale(c, **o):
    return {
        "txn_id": 900000001,
        "building_id": c["early_block"],
        "flat_type_id": c["resale_types"][0],
        "flat_model_id": c["model"],
        "txn_month": date(2025, 1, 1),
        "storey_min": 1,
        "storey_max": 3,
        "floor_area_sqm": Decimal("90.0"),
        "remaining_lease_months": 800,
        "resale_price": Decimal("500000.00"),
        "source": "admin",
        **o,
    }


def buyer(_c=None, **o):
    return {
        "buyer_id": 900001,
        "email": "neg-a@example.com",
        "password_hash": "x",
        "display_name": "Neg A",
        "is_admin": False,
        "created_at": STAMP,
        **o,
    }


def buyer_b(_c=None, **o):
    return buyer(buyer_id=900002, email="neg-b@example.com", **o)


def saved(c, **o):
    return {"buyer_id": 900001, "building_id": c["blocks"][0], "note": "n", "saved_at": STAMP, **o}


def buyer_c(_c=None, **o):
    return buyer(buyer_id=900003, email="neg-c@example.com", **o)


def home(_c=None, **o):
    return {"household_id": 900101, "created_at": STAMP, **o}


def home_b(_c=None, **o):
    return home(household_id=900102, **o)


def member(_c=None, **o):
    return {"buyer_id": 900001, "household_id": 900101, "seat": 1, "joined_at": STAMP, **o}


def member_b(_c=None, **o):
    return member(buyer_id=900002, seat=2, **o)


def invitation(_c=None, **o):
    return {"inviter_id": 900001, "invitee_id": 900002, "created_at": STAMP, **o}


def shared(c, **o):
    return {
        "household_id": 900101, "building_id": c["blocks"][0], "note": "n", "note_by": 900001, "saved_by": 900001,
        "saved_at": STAMP, **o,
    }  # fmt: skip


def hawker(_c=None, **o):
    return {
        "hawker_id": 900001,
        "name": "TEST HAWKER",
        "address": "1 TEST RD",
        "postal_code": "123456",
        "status": "Existing",
        "stall_count": 10,
        "latitude": Decimal("1.30"),
        "longitude": Decimal("103.85"),
        **o,
    }


def hawker_b(_c=None, **o):
    return hawker(hawker_id=900002, **o)


# ------------------------------------------------------------------------------------------------ cases
# key -> function of the loaded ids returning a Spec. The key before `#` must be a live rule.

CASES: dict = {}


def case(key, builder):
    assert key not in CASES, key
    CASES[key] = builder


def spec(table, good, bad=None, before=None, **kw):
    return lambda c: Spec(
        table,
        good(c) if callable(good) else good,
        bad(c) if callable(bad) else (bad or {}),
        [(t, r(c) if callable(r) else r) for t, r in (before or [])],
        **kw,
    )


# lookups
case("pg:town.town_pkey", spec("town", town_b(), {"town_id": 9001}, [("town", town())]))
case("pg:town.town_code_key", spec("town", town_b(), {"code": "ZZA"}, [("town", town())]))
case("pg:town.town_name_key", spec("town", town_b(), {"name": "TEST TOWN A"}, [("town", town())]))
for _col in ("town_id", "code", "name"):
    case(f"pg:town.town_{_col}_not_null", spec("town", town(), {_col: None}))
case("pg:town.code:type", spec("town", town(), {"code": "ZZZZ"}, sqlstate="22001"))
case("pg:town.name:type", spec("town", town(), {"name": "X" * 41}, sqlstate="22001"))

case(
    "pg:flat_type.flat_type_pkey",
    spec("flat_type", flat_type_b(), {"flat_type_id": 9001}, [("flat_type", flat_type())]),
)
case(
    "pg:flat_type.flat_type_name_key",
    spec("flat_type", flat_type_b(), {"name": "TEST TYPE A"}, [("flat_type", flat_type())]),
)
for _col in ("flat_type_id", "name", "is_resale_type"):
    case(f"pg:flat_type.flat_type_{_col}_not_null", spec("flat_type", flat_type(), {_col: None}))
case("pg:flat_type.name:type", spec("flat_type", flat_type(), {"name": "X" * 21}, sqlstate="22001"))

case(
    "pg:flat_model.flat_model_pkey",
    spec("flat_model", flat_model_b(), {"flat_model_id": 9001}, [("flat_model", flat_model())]),
)
case(
    "pg:flat_model.flat_model_name_key",
    spec("flat_model", flat_model_b(), {"name": "Test model A"}, [("flat_model", flat_model())]),
)
for _col in ("flat_model_id", "name"):
    case(f"pg:flat_model.flat_model_{_col}_not_null", spec("flat_model", flat_model(), {_col: None}))
case("pg:flat_model.name:type", spec("flat_model", flat_model(), {"name": "X" * 31}, sqlstate="22001"))

# building
case("pg:building.building_pkey", spec("building", block_b, {"building_id": 9000001}, [("building", block)]))
case("pg:building.building_block_street_key", spec("building", block_b, {"block": "9001"}, [("building", block)]))
for _col in ("building_id", "town_id", "block", "street", "in_property_dataset"):
    case(f"pg:building.building_{_col}_not_null", spec("building", block, {_col: None}))
case("pg:building.building_town_id_fkey", spec("building", block, lambda c: {"town_id": 32000}))
case("pg:building.building_year_completed_check#minimum", spec("building", block, {"year_completed": 1899}))
case("pg:building.building_year_completed_check#maximum", spec("building", block, {"year_completed": 2101}))
case("pg:building.building_lease_commence_year_check#minimum", spec("building", block, {"lease_commence_year": 1899}))
case("pg:building.building_lease_commence_year_check#maximum", spec("building", block, {"lease_commence_year": 2101}))
case("pg:building.building_max_floor_check#minimum", spec("building", block, {"max_floor": 0}))
case("pg:building.building_max_floor_check#maximum", spec("building", block, {"max_floor": 61}))
case("pg:building.building_latitude_check#minimum", spec("building", block, {"latitude": Decimal("1.14")}))
case("pg:building.building_latitude_check#maximum", spec("building", block, {"latitude": Decimal("1.49")}))
case("pg:building.building_longitude_check#minimum", spec("building", block, {"longitude": Decimal("103.58")}))
case("pg:building.building_longitude_check#maximum", spec("building", block, {"longitude": Decimal("104.11")}))
case("pg:building.building_coords_both_or_neither#latitude_null", spec("building", block, {"latitude": None}))
case("pg:building.building_coords_both_or_neither#longitude_null", spec("building", block, {"longitude": None}))
for _attr, _value in ATTRS.items():
    case(f"pg:building.building_property_attrs#{_attr}_null", spec("building", block, {_attr: None}))
    case(f"pg:building.building_property_attrs#stub_{_attr}", spec("building", stub, {_attr: _value}))
case(
    "pg:building.building_property_attrs#in_property_dataset_false",
    spec("building", block, {"in_property_dataset": False}),
)
case(
    "pg:building.building_property_attrs#stub_in_property_dataset_true",
    spec("building", stub, {"in_property_dataset": True}),
)
case("pg:building.block:type", spec("building", block, {"block": "123456"}, sqlstate="22001"))
case("pg:building.street:type", spec("building", block, {"street": "X" * 41}, sqlstate="22001"))
case("pg:building.latitude:type", spec("building", block, {"latitude": Decimal("1000")}, sqlstate="22003"))
case("pg:building.longitude:type", spec("building", block, {"longitude": Decimal("1000")}, sqlstate="22003"))

# supply
_parent = [("building", block)]
case(
    "pg:building_flat_supply.building_flat_supply_pkey",
    spec(
        "building_flat_supply",
        lambda c: supply(c, flat_type_id=c["resale_types"][1]),
        lambda c: {"flat_type_id": c["resale_types"][0]},
        [*_parent, ("building_flat_supply", supply)],
    ),
)
case(
    "pg:building_flat_supply.building_flat_supply_building_id_fkey",
    spec("building_flat_supply", supply, {"building_id": MISSING}, _parent),
)
case(
    "pg:building_flat_supply.building_flat_supply_flat_type_id_fkey",
    spec("building_flat_supply", supply, {"flat_type_id": 32000}, _parent),
)
for _col in ("building_id", "flat_type_id", "sold_units", "rental_units"):
    case(
        f"pg:building_flat_supply.building_flat_supply_{_col}_not_null",
        spec("building_flat_supply", supply, {_col: None}, _parent),
    )
case(
    "pg:building_flat_supply.building_flat_supply_sold_units_check",
    spec("building_flat_supply", supply, {"sold_units": -1}, _parent),
)
case(
    "pg:building_flat_supply.building_flat_supply_rental_units_check",
    spec("building_flat_supply", supply, {"rental_units": -1}, _parent),
)
case("pg:building_flat_supply.supply_not_empty", spec("building_flat_supply", supply, {"sold_units": 0}, _parent))

# sales
R = "pg:resale_transaction."
case(
    R + "resale_transaction_pkey",
    spec(
        "resale_transaction", lambda c: sale(c, txn_id=900000002), {"txn_id": 900000001}, [("resale_transaction", sale)]
    ),
)
for _col in (
    "txn_id",
    "building_id",
    "flat_type_id",
    "flat_model_id",
    "txn_month",
    "storey_min",
    "storey_max",
    "floor_area_sqm",
    "remaining_lease_months",
    "resale_price",
    "source",
):
    case(f"{R}resale_transaction_{_col}_not_null", spec("resale_transaction", sale, {_col: None}))
case(R + "resale_transaction_building_id_fkey", spec("resale_transaction", sale, {"building_id": MISSING}))
case(R + "resale_transaction_flat_type_id_fkey", spec("resale_transaction", sale, {"flat_type_id": 32000}))
case(R + "resale_transaction_flat_model_id_fkey", spec("resale_transaction", sale, {"flat_model_id": 32000}))
case(R + "resale_transaction_txn_month_check", spec("resale_transaction", sale, {"txn_month": date(2025, 1, 2)}))
case(
    R + "resale_transaction_floor_area_sqm_check#zero",
    spec("resale_transaction", sale, {"floor_area_sqm": Decimal("0")}),
)
case(
    R + "resale_transaction_floor_area_sqm_check#400",
    spec("resale_transaction", sale, {"floor_area_sqm": Decimal("400.0")}),
)
case(
    R + "resale_transaction_remaining_lease_months_check#minimum",
    spec("resale_transaction", sale, {"remaining_lease_months": -1}),
)
case(
    R + "resale_transaction_remaining_lease_months_check#maximum",
    spec("resale_transaction", sale, {"remaining_lease_months": 1189}),
)
case(R + "resale_transaction_resale_price_check", spec("resale_transaction", sale, {"resale_price": Decimal("0")}))
case(R + "resale_transaction_source_check", spec("resale_transaction", sale, {"source": "other"}))
case(R + "storey_band#storey_min:minimum", spec("resale_transaction", sale, {"storey_min": 0}))
case(R + "storey_band#storey_min:maximum", spec("resale_transaction", sale, {"storey_min": 61}))
case(R + "storey_band#storey_max:minimum", spec("resale_transaction", sale, {"storey_max": 0}))
case(R + "storey_band#storey_max:maximum", spec("resale_transaction", sale, {"storey_max": 61}))
case(R + "storey_band#min_above_max", spec("resale_transaction", sale, {"storey_min": 5}))
case(
    R + "resale_transaction_check#flat_type",
    spec(
        "resale_transaction",
        sale,
        lambda c: {"flat_type_id": c["other_type"]},
        message=r"not sold on the resale market",
    ),
)
case(
    R + "resale_transaction_check#completion_year",
    spec(
        "resale_transaction",
        sale,
        lambda c: {"txn_month": date(c["early_year"] - 1, 12, 1)},
        message=r"predates block completion",
    ),
)
case(
    R + "floor_area_sqm:type",
    spec("resale_transaction", sale, {"floor_area_sqm": Decimal("10000.0")}, sqlstate="22003"),
)
case(
    R + "resale_price:type",
    spec("resale_transaction", sale, {"resale_price": Decimal("10000000.00")}, sqlstate="22003"),
)
case(R + "source:type", spec("resale_transaction", sale, {"source": "abcdefghi"}, sqlstate="22001"))

# accounts
case("pg:buyer.buyer_pkey", spec("buyer", buyer_b, {"buyer_id": 900001}, [("buyer", buyer)]))
case("pg:buyer.buyer_email_lower_key", spec("buyer", buyer_b, {"email": "NEG-A@EXAMPLE.COM"}, [("buyer", buyer)]))
for _col in ("buyer_id", "email", "password_hash", "is_admin", "created_at"):
    case(f"pg:buyer.buyer_{_col}_not_null", spec("buyer", buyer, {_col: None}))
case("pg:buyer.buyer_email_check#no_at", spec("buyer", buyer, {"email": "nope.example.com"}))
case("pg:buyer.buyer_email_check#at_first", spec("buyer", buyer, {"email": "@example.com"}))
case("pg:buyer.display_name:type", spec("buyer", buyer, {"display_name": "X" * 61}, sqlstate="22001"))
case("pg:buyer.email:type", spec("buyer", buyer, {"email": "a@" + "x" * 253}, sqlstate="22001"))

_saver = [("buyer", buyer)]
S = "pg:saved_building."
case(
    S + "saved_building_pkey",
    spec(
        "saved_building",
        lambda c: saved(c, building_id=c["blocks"][1]),
        lambda c: {"building_id": c["blocks"][0]},
        [*_saver, ("saved_building", saved)],
    ),
)
case(S + "saved_building_buyer_id_fkey", spec("saved_building", saved, {"buyer_id": MISSING}, _saver))
case(S + "saved_building_building_id_fkey", spec("saved_building", saved, {"building_id": MISSING}, _saver))
for _col in ("buyer_id", "building_id", "saved_at"):
    case(f"{S}saved_building_{_col}_not_null", spec("saved_building", saved, {_col: None}, _saver))
case(S + "note:type", spec("saved_building", saved, {"note": "x" * 501}, _saver, sqlstate="22001"))
case(S + "saved_building_cap#insert", None)  # custom runner below
case(S + "saved_building_cap#update_buyer_id", None)  # custom runner below
case(
    S + "saved_building_owner",
    spec(
        "saved_building",
        lambda c: saved(c, buyer_id=900002),
        {"buyer_id": 900001},
        [("buyer", buyer), ("buyer", buyer_b), ("household", home), ("household_member", member)],
        message=r"is in a household",
    ),
)

# ---- households (T11)
HH = "pg:household."
case(HH + "household_pkey", spec("household", home_b, {"household_id": 900101}, [("household", home)]))
for _col in ("household_id", "created_at"):
    case(f"{HH}household_{_col}_not_null", spec("household", home, {_col: None}))

HM = "pg:household_member."
_two = [("buyer", buyer), ("buyer", buyer_b), ("household", home)]
case(
    HM + "household_member_pkey",
    spec("household_member", member_b, {"buyer_id": 900001}, [*_two, ("household_member", member)]),
)
case(
    HM + "household_member_household_id_seat_key",
    spec("household_member", member_b, {"seat": 1}, [*_two, ("household_member", member)]),
)
case(HM + "household_member_seat_check", spec("household_member", member, {"seat": 3}, _two))
case(HM + "household_member_buyer_id_fkey", spec("household_member", member, {"buyer_id": MISSING}, _two))
case(HM + "household_member_household_id_fkey", spec("household_member", member, {"household_id": MISSING}, _two))
for _col in ("buyer_id", "household_id", "seat", "joined_at"):
    case(f"{HM}household_member_{_col}_not_null", spec("household_member", member, {_col: None}, _two))

HI = "pg:household_invitation."
_three = [("buyer", buyer), ("buyer", buyer_b), ("buyer", buyer_c)]
case(
    HI + "household_invitation_pkey",
    spec(
        "household_invitation",
        invitation(inviter_id=900003),
        {"inviter_id": 900001},
        [*_three, ("household_invitation", invitation)],
    ),
)
case(HI + "household_invitation_check", spec("household_invitation", invitation, {"invitee_id": 900001}, _three))
for _col in ("inviter_id", "invitee_id"):
    case(f"{HI}household_invitation_{_col}_fkey", spec("household_invitation", invitation, {_col: MISSING}, _three))
for _col in ("inviter_id", "invitee_id", "created_at"):
    case(f"{HI}household_invitation_{_col}_not_null", spec("household_invitation", invitation, {_col: None}, _three))

HS = "pg:household_saved."
_home = [("buyer", buyer), ("household", home)]
case(
    HS + "household_saved_pkey",
    spec(
        "household_saved",
        lambda c: shared(c, building_id=c["blocks"][1]),
        shared,
        [*_home, ("household_saved", shared)],
    ),
)
for _col in ("household_id", "building_id", "note_by", "saved_by"):
    case(f"{HS}household_saved_{_col}_fkey", spec("household_saved", shared, {_col: MISSING}, _home))
for _col in ("household_id", "building_id", "saved_at"):
    case(f"{HS}household_saved_{_col}_not_null", spec("household_saved", shared, {_col: None}, _home))
case(HS + "note:type", spec("household_saved", shared, {"note": "x" * 501}, _home, sqlstate="22001"))
case(HS + "household_saved_cap#insert", None)  # custom runner below
case(HS + "household_saved_cap#update_household_id", None)  # custom runner below

# hawker centres
H = "pg:hawker_centre."
case(H + "hawker_centre_pkey", spec("hawker_centre", hawker_b, {"hawker_id": 900001}, [("hawker_centre", hawker)]))
for _col in ("hawker_id", "name", "status", "latitude", "longitude"):
    case(f"{H}hawker_centre_{_col}_not_null", spec("hawker_centre", hawker, {_col: None}))
case(H + "hawker_centre_postal_code_check", spec("hawker_centre", hawker, {"postal_code": "12345A"}))
case(H + "hawker_centre_stall_count_check", spec("hawker_centre", hawker, {"stall_count": -1}))
case(H + "hawker_centre_latitude_check#minimum", spec("hawker_centre", hawker, {"latitude": Decimal("1.14")}))
case(H + "hawker_centre_latitude_check#maximum", spec("hawker_centre", hawker, {"latitude": Decimal("1.49")}))
case(H + "hawker_centre_longitude_check#minimum", spec("hawker_centre", hawker, {"longitude": Decimal("103.58")}))
case(H + "hawker_centre_longitude_check#maximum", spec("hawker_centre", hawker, {"longitude": Decimal("104.11")}))
case(H + "name:type", spec("hawker_centre", hawker, {"name": "X" * 101}, sqlstate="22001"))
case(H + "address:type", spec("hawker_centre", hawker, {"address": "X" * 101}, sqlstate="22001"))
case(H + "postal_code:type", spec("hawker_centre", hawker, {"postal_code": "1234567"}, sqlstate="22001"))
case(H + "status:type", spec("hawker_centre", hawker, {"status": "X" * 31}, sqlstate="22001"))
case(H + "latitude:type", spec("hawker_centre", hawker, {"latitude": Decimal("1000")}, sqlstate="22003"))
case(H + "longitude:type", spec("hawker_centre", hawker, {"longitude": Decimal("1000")}, sqlstate="22003"))


# ------------------------------------------------------------------------------------------------ runner


@pytest.fixture(scope="module")
def live_pg_rules(settings):
    with psycopg.connect(settings.pg_dsn("app_rw")) as conn:
        return pg_rules(conn)


def expect_error(err: psycopg.Error, rule: dict, spec_: Spec) -> None:
    """Assert the exact SQLSTATE, and the constraint, or table and column, the rule must report."""
    kind, diag = rule["kind"], err.diag
    if kind == "c":
        assert (err.sqlstate, diag.constraint_name) == ("23514", rule["name"])
    elif kind == "f":
        assert (err.sqlstate, diag.constraint_name) == ("23503", rule["name"])
    elif kind in ("p", "u", "index"):
        assert (err.sqlstate, diag.constraint_name) == ("23505", rule["name"])
    elif kind == "n":
        assert err.sqlstate == "23502"
        assert (diag.table_name, diag.column_name) == (rule["table"], rule["column"])
    elif kind == "trigger":
        assert err.sqlstate == "23514"
        assert spec_.message and re.search(spec_.message, diag.message_primary)
    else:  # type: overflow reports no table, column or constraint
        assert spec_.sqlstate and err.sqlstate == spec_.sqlstate
        assert diag.constraint_name is None


def run(conn, rule: dict, spec_: Spec) -> None:
    # The outer block always rolls back, so a bad write that is wrongly accepted is never committed.
    with conn.transaction(force_rollback=True):
        for table, row in spec_.before:
            put(conn, table, row)
        with conn.transaction(force_rollback=True):  # the baseline is accepted
            put(conn, spec_.table, spec_.good)
        with pytest.raises(psycopg.Error) as raised:  # one value changed
            with conn.transaction():
                put(conn, spec_.table, {**spec_.good, **spec_.bad})
    expect_error(raised.value, rule, spec_)


@pytest.mark.parametrize("key", list(CASES), ids=list(CASES))
def test_pg_rule(key, request, live_pg_rules, loaded):
    rule = live_pg_rules[key.split("#")[0]]
    conn = request.getfixturevalue(IDENTITY[rule["table"]])
    if key.startswith(S + "saved_building_cap"):
        return run_cap(conn, rule, key.split("#")[1], loaded)
    if key.startswith(HS + "household_saved_cap"):
        return run_household_cap(conn, rule, key.split("#")[1], loaded)
    run(conn, rule, CASES[key](loaded))


def run_cap(conn, rule: dict, variant: str, c: dict) -> None:
    """Trigger (b): the 51st saved block is refused, by an insert and by moving a row to a full buyer."""
    put(conn, "buyer", buyer())
    for b in c["blocks"][:49]:
        put(conn, "saved_building", saved(c, building_id=b))
    full = saved(c, building_id=c["blocks"][49])
    with conn.transaction(force_rollback=True):  # the 50th save is accepted
        put(conn, "saved_building", full)
    put(conn, "saved_building", full)
    message = r"already has 50 saved blocks"
    if variant == "insert":
        with pytest.raises(psycopg.Error) as raised:
            with conn.transaction():
                put(conn, "saved_building", saved(c, building_id=c["blocks"][50]))
    else:
        put(conn, "buyer", buyer_b())
        put(conn, "buyer", buyer(buyer_id=900003, email="neg-c@example.com"))
        moved = saved(c, buyer_id=900002, building_id=c["blocks"][50])
        put(conn, "saved_building", moved)
        move = "UPDATE saved_building SET buyer_id = %s WHERE buyer_id = 900002"
        with conn.transaction(force_rollback=True):  # an empty buyer takes the row
            conn.execute(move, (900003,))
        with pytest.raises(psycopg.Error) as raised:  # the full buyer cannot
            with conn.transaction():
                conn.execute(move, (900001,))
    expect_error(raised.value, rule, Spec("saved_building", {}, message=message))


def run_household_cap(conn, rule: dict, variant: str, c: dict) -> None:
    """Trigger (c): the 51st shared block is refused, by an insert and by moving a row to a full household."""
    put(conn, "buyer", buyer())
    put(conn, "household", home())
    for b in c["blocks"][:49]:
        put(conn, "household_saved", shared(c, building_id=b))
    full = shared(c, building_id=c["blocks"][49])
    with conn.transaction(force_rollback=True):  # the 50th is accepted
        put(conn, "household_saved", full)
    put(conn, "household_saved", full)
    if variant == "insert":
        with pytest.raises(psycopg.Error) as raised:
            with conn.transaction():
                put(conn, "household_saved", shared(c, building_id=c["blocks"][50]))
    else:
        put(conn, "household", home_b())
        put(conn, "household", home(household_id=900103))
        put(conn, "household_saved", shared(c, household_id=900102, building_id=c["blocks"][50]))
        move = "UPDATE household_saved SET household_id = %s WHERE household_id = 900102"
        with conn.transaction(force_rollback=True):  # an empty household takes the row
            conn.execute(move, (900103,))
        with pytest.raises(psycopg.Error) as raised:  # the full one cannot
            with conn.transaction():
                conn.execute(move, (900101,))
    expect_error(raised.value, rule, Spec("household_saved", {}, message=r"already has 50 saved blocks"))


def test_every_rule_has_a_case(live_pg_rules):
    covered = {key.split("#")[0] for key in CASES}
    assert covered == set(live_pg_rules), {
        "no case": sorted(set(live_pg_rules) - covered),
        "stale case": sorted(covered - set(live_pg_rules)),
    }
