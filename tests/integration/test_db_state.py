"""The state fingerprint against the live databases (task 4.3).

Reads as the owner and the ETL user, so both passwords must reach the test container (the CI command does). The
round trip through a real backup is scripts/check_backup_roundtrip.sh.
"""

from __future__ import annotations

import json

import pytest

from app.config import get_settings
from db.state import differences, dumps, fingerprint

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def state():
    s = get_settings()
    if not (s.pg_owner_password and s.mongo_etl_password):
        pytest.skip("PG_OWNER_PASSWORD and MONGO_ETL_PASSWORD are not set; pass them to the test container")
    return fingerprint()


def test_the_fingerprint_covers_every_table_and_collection(state):
    pg, mongo = state["postgres"], state["mongo"]
    assert {"resale_transaction", "building", "buyer", "saved_building", "mv_town_month_stats"} <= set(pg["tables"])
    assert all(t["rows"] >= 0 and len(t["md5"] or "x" * 32) == 32 for t in pg["tables"].values())
    assert {"resale_transactions", "building_profiles", "buyers", "counters", "hawker_centres"} <= set(mongo)
    for name, coll in mongo.items():
        assert coll["count"] >= 0 and len(coll["sha256"]) == 64, name
        assert "_id_" in coll["indexes"], name
    assert "validator" in mongo["resale_transactions"]["options"]
    assert mongo["resale_transactions"]["options"]["validationAction"] == "error"


def test_the_fingerprint_records_owners_grants_and_function_flags(state):
    pg = state["postgres"]
    assert set(pg["owners"].values()) == {"neighboar_owner"}
    assert pg["privileges"]["table schema_migration"] == ["neighboar_owner=arwdDxtm/neighboar_owner"]
    assert any(line.startswith("neighboar_owner public r:") for line in pg["default_acl"])
    refresh = pg["functions"]["refresh_town_stats()"]
    assert refresh["prosecdef"] is True
    assert refresh["proconfig"] == ["search_path=pg_catalog, public, pg_temp"]
    assert pg["triggers"] and set(pg["triggers"].values()) == {"O"}
    assert pg["constraints"] and all(c["validated"] for c in pg["constraints"].values())


def test_the_fingerprint_is_repeatable_and_matches_the_json_round_trip(state):
    again = fingerprint()
    assert differences(json.loads(dumps(state)), json.loads(dumps(again))) == []


def test_the_fingerprint_holds_no_secret(state):
    text = dumps(state)
    s = get_settings()
    for key, value in s.model_dump().items():
        if (key.endswith("_password") or key == "secret_key") and value:
            assert value not in text, key
