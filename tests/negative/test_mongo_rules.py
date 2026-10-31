"""One negative test per MongoDB validator rule (task 3.5a).

Each case inserts a baseline document that the test first shows is accepted, then changes exactly one value (or
removes one field) and expects code 121. For `$jsonSchema` rules, `errInfo.details` must name the failing
property and keyword (read back as the same path the rule key uses); for `$expr` rules MongoDB reports only
`operatorName: "$expr"` for the whole expression, so the test asserts that and that no schema rule failed too.
Writes use the identity that could otherwise write the collection (etl, app_admin or app) and are deleted
afterwards; the counters are changed inside a transaction that is never committed. Case ids are the rule keys of
tests/negative/rules.py.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest
from bson import Decimal128 as D
from pymongo.errors import DuplicateKeyError, WriteError

from tests.negative.rules import mongo_rules

pytestmark = pytest.mark.integration

J = "$jsonSchema"
STAMP = datetime(2025, 1, 1, tzinfo=UTC)
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
UNREACHABLE = "unreachable"  # a rule no write can break: the document is always an object

IDENTITY = {
    "building_profiles": "mongo_etl",
    "hawker_centres": "mongo_etl",
    "resale_transactions": "mongo_admin",
    "buyers": "mongo_app",
    "counters": "mongo_app",
    "households": "mongo_app",
    "invitations": "mongo_app",
}


# ------------------------------------------------------------------------------------------------ baselines


def _sales_stats():
    return {"txn_count": 1, "median_price": D("500000.00"), "avg_psm": D("5555.56")}


def property_block():
    return {
        "_id": -9101,
        "block": "ZZ9",
        "street": "TEST STREET",
        "town": {"code": "ZZZ", "name": "TEST TOWN"},
        "lease_commence_year": 2000,
        "in_property_dataset": True,
        **ATTRS,
        "location": {"type": "Point", "coordinates": [103.8, 1.35]},
        "supply": [{"flat_type": "4 ROOM", "sold": 10, "rental": 0}],
        "stats": {
            **_sales_stats(),
            "by_flat_type": [{"flat_type": "4 ROOM", **_sales_stats()} | {"txn_count": 1}],
            "last_txn_month": STAMP,
        },
        "recent_sales": [
            {
                "txn_id": 1,
                "txn_month": STAMP,
                "flat_type": "4 ROOM",
                "flat_model": "Improved",
                "storey_min": 1,
                "storey_max": 3,
                "floor_area_sqm": D("90.0"),
                "resale_price": D("500000.00"),
            }
        ],
    }


def stub_block():
    doc = property_block()
    doc.pop("supply")
    doc.update(dict.fromkeys(ATTRS), in_property_dataset=False, lease_commence_year=None)
    return doc


def hawker():
    return {
        "_id": -9201,
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [103.85, 1.30]},
        "properties": {
            "OBJECTID": 9201,
            "NAME": "TEST HAWKER",
            "ADDRESS_MYENV": "1 TEST RD",
            "ADDRESSPOSTALCODE": "123456",
            "STATUS": "Existing",
            "NUMBER_OF_COOKED_FOOD_STALLS": 10,
        },
    }


def sale():
    return {
        "_id": -9301,
        "building_id": 1,
        "town": "TEST TOWN",
        "block": "ZZ9",
        "street": "TEST STREET",
        "flat_type": "4 ROOM",
        "flat_model": "Improved",
        "txn_month": STAMP,
        "storey_min": 1,
        "storey_max": 3,
        "floor_area_sqm": D("90.0"),
        "remaining_lease_months": 800,
        "resale_price": D("500000.00"),
        "lease_commence_year": 2000,
        "source": "admin",
    }


def buyer():
    return {
        "_id": -9401,
        "email": "neg@example.com",
        "password_hash": "x",
        "display_name": "Neg",
        "is_admin": False,
        "created_at": STAMP,
        "last_active_at": None,
        "saved": [{"building_id": 1, "block": "1", "street": "X", "town": "Y", "note": "n", "saved_at": STAMP}],
    }


def counter():
    return {"_id": "txn", "seq": 0}


def household():
    # Member ids no real buyer has, so the unique index on members.buyer_id never meets a live household.
    return {
        "_id": -9501,
        "created_at": STAMP,
        "members": [
            {"buyer_id": -9601, "seat": 1, "joined_at": STAMP},
            {"buyer_id": -9602, "seat": 2, "joined_at": STAMP},
        ],
        "saved": [
            {"building_id": 1, "block": "1", "street": "X", "town": "Y", "note": "n", "note_by": -9601,
             "saved_by": -9601, "saved_at": STAMP}
        ],
    }  # fmt: skip


def invitation():
    return {"_id": -9701, "invitee_id": -9702, "created_at": STAMP}


BASES = {
    "P": property_block, "S": stub_block, "H": hawker, "T": sale, "B": buyer, "C": counter, "HH": household,
    "IV": invitation,
}  # fmt: skip

# ------------------------------------------------------------------------------------------------ cases
# key -> (collection, baseline, operations, expected observed keys). Operations are ("set", dotted.path, value or
# function of the document) and ("del", dotted.path); a numeric path segment is a list index.

CASES: dict = {}


def add(coll, base, suffix, *ops, expect=None):
    key = f"mongo:{coll}:{suffix}"
    assert key not in CASES, key
    CASES[key] = (coll, base, ops, expect)


def put_(path, value):
    return ("set", path, value)


def del_(path):
    return ("del", path)


def sub(path, name):
    return f"{path}.{name}" if path else name


def jpath(path):
    """The key path of an object at the dotted `path`: a list index becomes `items`."""
    parts = ["items" if part.isdigit() else part for part in path.split(".")] if path else []
    return ".".join([J, *parts])


def props(coll, base, path, *, required=(), extra=True, bson=None, maxlen=None, minimum=None, maximum=None):
    """Standard rules of an object at `path`: required fields, no extra field, types, lengths and bounds.

    bson: {name: wrong value}; maxlen: {name: (too long value)}; minimum/maximum: {name: out-of-range value}.
    """
    here = jpath(path)
    bson, maxlen, minimum, maximum = bson or {}, maxlen or {}, minimum or {}, maximum or {}
    for name in required:
        add(coll, base, f"{here}:required.{name}", del_(sub(path, name)))
    if extra:
        add(coll, base, f"{here}:additionalProperties", put_(sub(path, "zzz"), 1))
    for name, bad in bson.items():
        add(coll, base, f"{here}.{name}:bsonType", put_(sub(path, name), bad))
    for name, bad in maxlen.items():
        add(coll, base, f"{here}.{name}:maxLength", put_(sub(path, name), bad))
    for name, bad in minimum.items():
        add(coll, base, f"{here}.{name}:minimum", put_(sub(path, name), bad))
    for name, bad in maximum.items():
        add(coll, base, f"{here}.{name}:maximum", put_(sub(path, name), bad))


X = "x"
LONG = {n: "X" * (n + 1) for n in (3, 5, 20, 30, 40, 60, 100, 254, 500)}

# ---- building_profiles
BP = "building_profiles"
add(BP, "P", f"{J}:required._id", del_("_id"), expect=f"{J}._id:bsonType")  # the driver supplies an ObjectId
add(BP, "P", f"{J}:bsonType", expect=UNREACHABLE)
props(
    BP,
    "P",
    "",
    required=("block", "street", "town", "in_property_dataset", "stats", "recent_sales"),
    bson={
        "_id": X,
        "block": 1,
        "street": 1,
        "in_property_dataset": X,
        "year_completed": X,
        "lease_commence_year": X,
        "max_floor": X,
        "residential": X,
        "commercial": X,
        "market_hawker": X,
        "miscellaneous": X,
        "multistorey_carpark": X,
        "precinct_pavilion": X,
        "town": X,
        "location": X,
        "supply": X,
        "stats": X,
        "recent_sales": X,
    },
    maxlen={"block": LONG[5], "street": LONG[40]},
    minimum={"year_completed": 1899, "lease_commence_year": 1899, "max_floor": 0},
    maximum={"year_completed": 2101, "lease_commence_year": 2101, "max_floor": 61},
)
props(
    BP, "P", "town", required=("code", "name"), bson={"code": 1, "name": 1}, maxlen={"code": LONG[3], "name": LONG[40]}
)
props(BP, "P", "location", required=("type", "coordinates"), bson={"coordinates": X})
add(BP, "P", f"{J}.location.type:enum", put_("location.type", "LineString"))
add(BP, "P", f"{J}.location.coordinates:minItems", put_("location.coordinates", [103.8]))
add(BP, "P", f"{J}.location.coordinates:maxItems", put_("location.coordinates", [103.8, 1.35, 0.0]))
add(BP, "P", f"{J}.location.coordinates.items:bsonType", put_("location.coordinates.0", X))
props(
    BP,
    "P",
    "supply.0",
    required=("flat_type", "sold", "rental"),
    bson={"flat_type": 1, "sold": X, "rental": X},
    maxlen={"flat_type": LONG[20]},
    minimum={"sold": -1, "rental": -1},
)
add(BP, "P", f"{J}.supply.items:bsonType", put_("supply", [1]))
props(
    BP,
    "P",
    "stats",
    required=("txn_count", "median_price", "avg_psm", "by_flat_type", "last_txn_month"),
    bson={"txn_count": X, "median_price": X, "avg_psm": X, "by_flat_type": X, "last_txn_month": X},
    minimum={"txn_count": -1},
)
props(
    BP,
    "P",
    "stats.by_flat_type.0",
    required=("flat_type", "txn_count", "median_price", "avg_psm"),
    bson={"flat_type": 1, "txn_count": X, "median_price": X, "avg_psm": X},
    maxlen={"flat_type": LONG[20]},
    minimum={"txn_count": 0},
)
add(BP, "P", f"{J}.stats.by_flat_type.items:bsonType", put_("stats.by_flat_type", [1]))
props(
    BP,
    "P",
    "recent_sales.0",
    required=(
        "txn_id",
        "txn_month",
        "flat_type",
        "flat_model",
        "storey_min",
        "storey_max",
        "floor_area_sqm",
        "resale_price",
    ),
    bson={
        "txn_id": X,
        "txn_month": X,
        "flat_type": 1,
        "flat_model": 1,
        "storey_min": X,
        "storey_max": X,
        "floor_area_sqm": X,
        "resale_price": X,
    },
    maxlen={"flat_type": LONG[20], "flat_model": LONG[30]},
    minimum={"storey_min": 0, "storey_max": 0},
    maximum={"storey_min": 61, "storey_max": 61},
)
add(BP, "P", f"{J}.recent_sales.items:bsonType", put_("recent_sales", [1]))
add(BP, "P", f"{J}.recent_sales:maxItems", put_("recent_sales", lambda d: d["recent_sales"] * 11))
# The two branches of anyOf: a block in the property dataset, and a stub.
for _name in ("supply", *ATTRS):
    add(BP, "P", f"{J}.anyOf.0:required.{_name}", del_(_name))
for _name in ATTRS:
    add(BP, "P", f"{J}.anyOf.0.{_name}:bsonType", put_(_name, None))
    add(BP, "S", f"{J}.anyOf.1.{_name}:bsonType", put_(_name, ATTRS[_name]))
add(BP, "P", f"{J}.anyOf.0.in_property_dataset:enum", put_("in_property_dataset", False))
add(BP, "S", f"{J}.anyOf.1.in_property_dataset:enum", put_("in_property_dataset", True))
add(BP, "S", f"{J}.anyOf.1.not:required.supply", put_("supply", []), expect=f"{J}.anyOf.1:not")
for _i, _point in enumerate(([103.58, 1.35], [104.11, 1.35], [103.8, 1.14], [103.8, 1.49])):
    _op = ("$gte", "$lte", "$gte", "$lte")[_i]
    add(BP, "P", f"$expr.$cond.2.$and.{_i}.{_op}", put_("location.coordinates", _point))

# ---- hawker_centres
HK = "hawker_centres"
add(HK, "H", f"{J}:required._id", del_("_id"), expect=f"{J}._id:bsonType")
add(HK, "H", f"{J}:bsonType", expect=UNREACHABLE)
props(HK, "H", "", required=("type", "geometry", "properties"), bson={"_id": X, "geometry": X, "properties": X})
add(HK, "H", f"{J}.type:enum", put_("type", "Other"))
props(HK, "H", "geometry", required=("type", "coordinates"), bson={"coordinates": X})
add(HK, "H", f"{J}.geometry.type:enum", put_("geometry.type", "LineString"))
add(HK, "H", f"{J}.geometry.coordinates:minItems", put_("geometry.coordinates", [103.85]))
add(HK, "H", f"{J}.geometry.coordinates:maxItems", put_("geometry.coordinates", [103.85, 1.3, 0.0]))
add(HK, "H", f"{J}.geometry.coordinates.items:bsonType", put_("geometry.coordinates.0", X))
props(
    HK,
    "H",
    "properties",
    required=("OBJECTID", "NAME", "STATUS"),
    extra=False,
    bson={
        "OBJECTID": X,
        "NAME": 1,
        "ADDRESS_MYENV": 1,
        "ADDRESSPOSTALCODE": 1,
        "STATUS": 1,
        "NUMBER_OF_COOKED_FOOD_STALLS": X,
    },
    maxlen={"NAME": LONG[100], "ADDRESS_MYENV": LONG[100], "STATUS": LONG[30]},
    minimum={"NUMBER_OF_COOKED_FOOD_STALLS": -1},
)
add(HK, "H", f"{J}.properties.ADDRESSPOSTALCODE:pattern", put_("properties.ADDRESSPOSTALCODE", "12345"))
for _i, _point in enumerate(([103.58, 1.30], [104.11, 1.30], [103.85, 1.14], [103.85, 1.49])):
    add(HK, "H", f"$expr.$and.{_i}.{('$gte', '$lte', '$gte', '$lte')[_i]}", put_("geometry.coordinates", _point))

# ---- resale_transactions
TX = "resale_transactions"
add(TX, "T", f"{J}:required._id", del_("_id"), expect=f"{J}._id:bsonType")
add(TX, "T", f"{J}:bsonType", expect=UNREACHABLE)
props(
    TX,
    "T",
    "",
    required=(
        "building_id",
        "town",
        "block",
        "street",
        "flat_type",
        "flat_model",
        "txn_month",
        "storey_min",
        "storey_max",
        "floor_area_sqm",
        "remaining_lease_months",
        "resale_price",
        "source",
    ),
    bson={
        "_id": X,
        "building_id": X,
        "town": 1,
        "block": 1,
        "street": 1,
        "flat_model": 1,
        "txn_month": X,
        "storey_min": X,
        "storey_max": X,
        "floor_area_sqm": 90,
        "remaining_lease_months": X,
        "resale_price": 5,
        "lease_commence_year": X,
    },
    maxlen={"town": LONG[40], "block": LONG[5], "street": LONG[40], "flat_model": LONG[30]},
    minimum={"storey_min": 0, "storey_max": 0, "remaining_lease_months": -1},
    maximum={"storey_min": 61, "storey_max": 61, "remaining_lease_months": 1189},
)
add(TX, "T", f"{J}.flat_type:enum", put_("flat_type", "STUDIO APARTMENT"))
add(TX, "T", f"{J}.source:enum", put_("source", "other"))
_exprs = [
    ("$gt", {"resale_price": D("0")}),
    ("$lt", {"resale_price": D("10000000.00")}),
    ("$eq", {"resale_price": D("500000.005")}),
    ("$eq", {"floor_area_sqm": D("85.25")}),
    ("$gt", {"floor_area_sqm": D("0")}),
    ("$lt", {"floor_area_sqm": D("400")}),
    ("$lte", {"storey_min": 5}),
]
for _i, (_op, _change) in enumerate(_exprs):
    add(TX, "T", f"$expr.$and.{_i}.{_op}", *[put_(k, v) for k, v in _change.items()])
add(TX, "T", "$expr.$and.7.$cond.1.$eq", put_("txn_month", datetime(2025, 1, 2, tzinfo=UTC)))

# ---- buyers
BY = "buyers"
add(BY, "B", f"{J}:required._id", del_("_id"), expect=f"{J}._id:bsonType")
add(BY, "B", f"{J}:bsonType", expect=UNREACHABLE)
props(
    BY,
    "B",
    "",
    required=("email", "password_hash", "is_admin", "created_at", "saved"),
    bson={
        "_id": X,
        "email": 1,
        "password_hash": 1,
        "display_name": 1,
        "is_admin": X,
        "created_at": X,
        "last_active_at": X,
        "saved": X,
    },
    maxlen={"display_name": LONG[60]},
)
add(BY, "B", f"{J}.email:maxLength", put_("email", "a" * 249 + "@x.com"))
add(BY, "B", f"{J}.email:pattern#upper", put_("email", "Upper@example.com"))  # MongoDB only: PostgreSQL accepts it
add(BY, "B", f"{J}.email:pattern#no_at", put_("email", "nope.example.com"))
add(BY, "B", f"{J}.email:pattern#at_first", put_("email", "@example.com"))
add(BY, "B", f"{J}.saved:maxItems", put_("saved", lambda d: d["saved"] * 51))
add(BY, "B", f"{J}.saved.items:bsonType", put_("saved", [1]))
props(
    BY,
    "B",
    "saved.0",
    required=("building_id", "block", "street", "town", "saved_at"),
    bson={"building_id": X, "block": 1, "street": 1, "town": 1, "note": 1, "saved_at": X},
    maxlen={"block": LONG[5], "street": LONG[40], "town": LONG[40], "note": LONG[500]},
)

add(BY, "B", f"{J}.household_id:bsonType", put_("household_id", X))

# ---- households (T11)
HO = "households"
add(HO, "HH", f"{J}:required._id", del_("_id"), expect=f"{J}._id:bsonType")
add(HO, "HH", f"{J}:bsonType", expect=UNREACHABLE)
props(
    HO,
    "HH",
    "",
    required=("created_at", "members", "saved"),
    bson={"_id": X, "created_at": X, "members": X, "saved": X},
)
add(HO, "HH", f"{J}.members:minItems", put_("members", lambda d: d["members"][:1]))
add(
    HO,
    "HH",
    f"{J}.members:maxItems",
    put_("members", lambda d: [*d["members"], {**d["members"][0], "buyer_id": -9603}]),
)
add(HO, "HH", f"{J}.members.items:bsonType", put_("members", [1, 2]))
props(HO, "HH", "members.0", required=("buyer_id", "seat", "joined_at"), bson={"buyer_id": X, "joined_at": X})
add(HO, "HH", f"{J}.members.items.seat:enum", put_("members.0.seat", 3))
add(HO, "HH", f"{J}.saved:maxItems", put_("saved", lambda d: [{**d["saved"][0], "building_id": i} for i in range(51)]))
add(HO, "HH", f"{J}.saved.items:bsonType", put_("saved", [1]))
props(
    HO,
    "HH",
    "saved.0",
    required=("building_id", "block", "street", "town", "saved_at"),
    bson={
        "building_id": X, "block": 1, "street": 1, "town": 1, "note": 1, "note_by": X, "saved_by": X, "saved_at": X,
    },
    maxlen={"block": LONG[5], "street": LONG[40], "town": LONG[40], "note": LONG[500]},
)  # fmt: skip

# ---- invitations (T11)
IV = "invitations"
add(IV, "IV", f"{J}:required._id", del_("_id"), expect=f"{J}._id:bsonType")
add(IV, "IV", f"{J}:bsonType", expect=UNREACHABLE)
props(IV, "IV", "", required=("invitee_id", "created_at"), bson={"_id": X, "invitee_id": X, "created_at": X})

# ---- counters
CT = "counters"
add(CT, "C", f"{J}:required._id", del_("_id"), expect=f"{J}._id:enum")
add(CT, "C", f"{J}:bsonType", expect=UNREACHABLE)
add(CT, "C", f"{J}:required.seq", del_("seq"))
add(CT, "C", f"{J}:additionalProperties", put_("zzz", 1))
add(CT, "C", f"{J}._id:enum", put_("_id", "other"))
add(CT, "C", f"{J}.seq:bsonType", put_("seq", X))
add(CT, "C", f"{J}.seq:minimum", put_("seq", -1))


# ------------------------------------------------------------------------------------------------ runner


def apply(doc, ops):
    for op, path, *value in ops:
        *parents, last = path.split(".")
        node = doc
        for part in parents:
            node = node[int(part)] if isinstance(node, list) else node[part]
        index = int(last) if isinstance(node, list) else last
        if op == "del":
            del node[index]
        else:
            v = value[0](doc) if callable(value[0]) else value[0]
            node[index] = v
    return doc


def observed(details: dict) -> set[str]:
    """The rule keys (without the collection) that errInfo.details reports as failed."""
    found: set[str] = set()

    def walk(rules, path):
        for r in rules:
            op = r["operatorName"]
            if op == "properties":
                for p in r["propertiesNotSatisfied"]:
                    walk(p["details"], f"{path}.{p['propertyName']}")
            elif op == "items":
                walk(r["details"], f"{path}.items")
            elif op == "anyOf":
                for s in r["schemasNotSatisfied"]:
                    walk(s["details"], f"{path}.anyOf.{s['index']}")
            elif op == "required":
                found.update(f"{path}:required.{name}" for name in r["missingProperties"])
            else:  # a `not` is reported only as "child expression matched", without naming the field
                found.add(f"{path}:{op}")

    clauses = [c["details"] for c in details["clausesNotSatisfied"]] if details["operatorName"] == "$and" else [details]
    for clause in clauses:
        if clause["operatorName"] == "$jsonSchema":
            walk(clause["schemaRulesNotSatisfied"], J)
        else:
            found.add(clause["operatorName"])  # $expr
    return found


def check_rejection(err: WriteError, suffix: str, expect: str | None) -> None:
    assert err.code == 121
    found = observed(err.details["errInfo"]["details"])
    if suffix.startswith("$expr"):
        assert found == {"$expr"}, found  # only the expression failed, and it is reported as a whole
    else:
        assert (expect or suffix) in found, found


@pytest.fixture(scope="module")
def live_validators(settings):
    from pymongo import MongoClient

    if not settings.mongo_etl_password:
        pytest.skip("MONGO_ETL_PASSWORD is not set; pass it to the test container")
    client = MongoClient(settings.mongo_uri("etl", direct=True), serverSelectionTimeoutMS=5000)
    try:
        db = client[settings.mongo_db]
        yield (
            {i["name"]: i["options"]["validator"] for i in db.list_collections() if "validator" in i["options"]},
            (mongo_rules(db)),
        )
    finally:
        client.close()


@pytest.mark.parametrize("key", list(CASES), ids=list(CASES))
def test_mongo_rule(key, request, live_validators):
    validators, _ = live_validators
    coll_name, base, ops, expect = CASES[key]
    suffix = key.split(":", 2)[2].split("#")[0]
    if expect == UNREACHABLE:
        # A document is always an object, so nothing can break this; assert the live rule is still there.
        assert validators[coll_name][J]["bsonType"] == "object"
        return
    db = request.getfixturevalue(IDENTITY[coll_name])
    coll = db[coll_name]
    good = BASES[base]()
    bad = apply(BASES[base](), ops)
    if coll_name == "counters":
        return run_counter(coll, good, bad, suffix, expect)
    ids = [good["_id"]]
    try:
        coll.delete_many({"_id": {"$in": ids}})
        coll.insert_one(copy.deepcopy(good))  # the baseline is accepted
        assert coll.count_documents({"_id": good["_id"]}) == 1
        coll.delete_one({"_id": good["_id"]})
        with pytest.raises(WriteError) as raised:  # one value changed
            coll.insert_one(bad)
        check_rejection(raised.value, suffix, expect)
    finally:
        coll.delete_many({"_id": {"$in": [*ids, bad.get("_id", good["_id"])]}})


def run_counter(coll, good, bad, suffix, expect):
    """The counters hold live ids, so every write is made inside a transaction that is aborted."""
    with coll.database.client.start_session() as session:
        session.start_transaction()
        try:
            coll.delete_many({}, session=session)
            coll.insert_one(copy.deepcopy(good), session=session)  # the baseline is accepted
            coll.delete_one({"_id": good["_id"]}, session=session)
            with pytest.raises(WriteError) as raised:
                coll.insert_one(bad, session=session)
            check_rejection(raised.value, suffix, expect)
        finally:
            session.abort_transaction()
    assert coll.count_documents({}) >= 1


def test_every_rule_has_a_case(live_validators):
    _, rules = live_validators
    covered = {k.split("#")[0] for k in CASES}
    assert covered == set(rules), {
        "no case": sorted(set(rules) - covered),
        "stale case": sorted(covered - set(rules)),
    }


# ------------------------------------------------------------------------------------------------ unique indexes
# Not validator rules, so they are not in CASES, but they are the MongoDB side of the PostgreSQL unique keys.

UNIQUE = {
    "building_profiles.block_street_unique": ("building_profiles", "P", [put_("_id", -9102)], "block_street_unique"),
    "buyers.email_unique": ("buyers", "B", [put_("_id", -9402)], "email_unique"),
    "building_profiles._id_": ("building_profiles", "P", [put_("block", "ZZ8")], "_id_"),
    "hawker_centres._id_": ("hawker_centres", "H", [put_("properties.OBJECTID", 9202)], "_id_"),
    "resale_transactions._id_": ("resale_transactions", "T", [put_("block", "ZZ8")], "_id_"),
    "buyers._id_": ("buyers", "B", [put_("email", "neg2@example.com")], "_id_"),
    "households.member_unique": ("households", "HH", [put_("_id", -9502)], "member_unique"),
    "households._id_": (
        "households", "HH", [put_("members.0.buyer_id", -9603), put_("members.1.buyer_id", -9604)], "_id_",
    ),
    "invitations._id_": ("invitations", "IV", [put_("invitee_id", -9703)], "_id_"),
}  # fmt: skip


@pytest.mark.parametrize("name", list(UNIQUE), ids=list(UNIQUE))
def test_mongo_unique_index(name, request):
    coll_name, base, ops, index = UNIQUE[name]
    coll = request.getfixturevalue(IDENTITY[coll_name])[coll_name]
    good, bad = BASES[base](), apply(BASES[base](), ops)
    try:
        coll.delete_many({"_id": {"$in": [good["_id"], bad["_id"]]}})
        coll.insert_one(good)  # the baseline is accepted
        with pytest.raises(DuplicateKeyError) as raised:  # one value repeated
            coll.insert_one(bad)
        assert raised.value.code == 11000 and f"index: {index} " in str(raised.value)
    finally:
        coll.delete_many({"_id": {"$in": [good["_id"], bad["_id"]]}})
