"""Fixtures for the negative tests: the identities of tests/integration/conftest.py, plus loaded ids to build on."""

import pytest

# Re-used as they are: the owner and etl identities (and the app ones) are defined once, in the integration tests.
from tests.integration.conftest import (  # noqa: F401
    mongo_admin,
    mongo_admin_client,
    mongo_app,
    mongo_etl,
    pg_admin,
    pg_owner,
    pg_ro,
    pg_rw,
    settings,
)


@pytest.fixture
def loaded(pg_owner):  # noqa: F811 - the imported fixture is used by name
    """Ids of loaded rows that negative tests build on (read-only queries; the sample is enough)."""
    one = pg_owner.execute

    def first(query):
        row = one(query).fetchone()
        assert row is not None, "load the sample first (python -m etl.pipeline --sample)"
        return row[0]

    return {
        "town": first("SELECT town_id FROM town ORDER BY town_id LIMIT 1"),
        "resale_types": [
            r[0] for r in one("SELECT flat_type_id FROM flat_type WHERE is_resale_type ORDER BY 1 LIMIT 2")
        ],
        "other_type": first("SELECT flat_type_id FROM flat_type WHERE NOT is_resale_type ORDER BY 1 LIMIT 1"),
        "model": first("SELECT flat_model_id FROM flat_model ORDER BY 1 LIMIT 1"),
        # A property block completed well before 2025, so a 2025 sale passes trigger (a).
        "early_block": first("SELECT building_id FROM building WHERE year_completed <= 2000 ORDER BY 1 LIMIT 1"),
        "early_year": first(
            "SELECT year_completed FROM building WHERE year_completed <= 2000 ORDER BY building_id LIMIT 1"
        ),
        "blocks": [r[0] for r in one("SELECT building_id FROM building ORDER BY 1 LIMIT 52")],
    }
