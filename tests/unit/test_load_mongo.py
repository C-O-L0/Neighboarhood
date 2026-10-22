"""Document shapes built by etl/load_mongo.py (no database needed)."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from bson.decimal128 import Decimal128

from etl.load_mongo import LAND_USE, profile_document, sale_document

VALIDATORS = Path(__file__).resolve().parents[2] / "db" / "mongo" / "validators"
TOWN = {"town_id": 11, "code": "GL", "name": "GEYLANG"}


def building(**over) -> dict:
    b = {
        "building_id": 1,
        "block": "1",
        "street": "BEACH RD",
        "town_id": 11,
        "year_completed": 1970,
        "max_floor": 12,
        **{k: False for k in LAND_USE},
        "residential": True,
        "in_property_dataset": True,
        "lease_commence_year": 1969,
        "latitude": Decimal("1.300000"),
        "longitude": Decimal("103.850000"),
    }
    return {**b, **over}


STUB = building(
    building_id=6969,
    block="82",
    street="MACPHERSON LANE",
    year_completed=None,
    max_floor=None,
    **{k: None for k in LAND_USE},
    in_property_dataset=False,
    lease_commence_year=1968,
)


def test_real_block_keeps_supply_sorted():
    supply = [{"flat_type": "4 ROOM", "sold": 10, "rental": 0}, {"flat_type": "3 ROOM", "sold": 5, "rental": 2}]
    doc = profile_document(building(), TOWN, supply)
    assert doc["in_property_dataset"] is True
    assert [s["flat_type"] for s in doc["supply"]] == ["3 ROOM", "4 ROOM"]
    assert doc["location"] == {"type": "Point", "coordinates": [103.85, 1.3]}


def test_real_block_without_supply_rows_has_zero_units():
    assert profile_document(building(), TOWN, [])["supply"] == []


def test_stub_supply_is_omitted_not_empty():
    doc = profile_document(STUB, TOWN, [])
    assert doc["in_property_dataset"] is False
    assert "supply" not in doc
    assert doc["max_floor"] is None and all(doc[k] is None for k in LAND_USE)


def test_location_omitted_when_not_geocoded():
    assert "location" not in profile_document(building(latitude=None, longitude=None), TOWN, [])


def test_sale_document_is_marked_snapshot():
    r = {
        "txn_id": 7,
        "building_id": 1,
        "flat_type_id": 3,
        "flat_model_id": 1,
        "txn_month": date(2024, 5, 1),
        "storey_min": 4,
        "storey_max": 6,
        "floor_area_sqm": Decimal("67.0"),
        "remaining_lease_months": 540,
        "resale_price": Decimal("350000.00"),
    }
    doc = sale_document(r, building(), "GEYLANG", "3 ROOM", "IMPROVED")
    assert doc["source"] == "snapshot"
    assert doc["txn_month"] == datetime(2024, 5, 1, tzinfo=UTC)
    assert doc["resale_price"] == Decimal128("350000.00")
    assert doc["lease_commence_year"] == 1969


def test_documents_use_only_validator_fields():
    """Every field the loader writes is declared, and every required field is written."""
    sale_schema = json.loads((VALIDATORS / "resale_transactions.json").read_text(encoding="utf-8"))
    assert sale_schema["properties"]["source"] == {"enum": ["snapshot", "admin"]}
    assert "source" in sale_schema["required"]

    profile_schema = json.loads((VALIDATORS / "building_profiles.json").read_text(encoding="utf-8"))
    for doc in (profile_document(building(), TOWN, []), profile_document(STUB, TOWN, [])):
        assert set(doc) <= set(profile_schema["properties"])
        assert set(profile_schema["required"]) <= set(doc)
    assert "supply" not in profile_schema["required"]
