"""Database counts against the loaded sample (no left-over admin rows)."""

import pytest

from etl import db_counts

pytestmark = pytest.mark.integration


def test_collect_agrees_across_databases(pg_rw, mongo_app):
    counts = db_counts.collect(pg_rw, mongo_app)
    assert set(counts) == {e for e, _, _ in db_counts.ENTITIES}
    for entity, _, mode in db_counts.ENTITIES:
        if mode != "pg_cleaning":
            assert counts[entity]["pg"] == counts[entity]["mongo"], entity
    assert counts["town"]["pg"] > 0 and counts["building"]["pg"] > 0


def test_supply_count_includes_the_stub_without_supply(pg_rw, mongo_app):
    counts = db_counts.collect(pg_rw, mongo_app)
    stubs = mongo_app["building_profiles"].count_documents({"in_property_dataset": False, "supply": {"$exists": False}})
    assert counts["stub_building"]["pg"] >= 1 and stubs >= 1
    with pg_rw.cursor() as cur:
        cur.execute("SELECT count(*) FROM building_flat_supply")
        assert cur.fetchone()[0] == counts["building_flat_supply"]["pg"] == counts["building_flat_supply"]["mongo"]
