"""Price check comparables (task T9): both databases against the sales picked out in Python from the raw rows.

The registry case (test_cases.py) shows the two databases agree; these tests show what they agree on is right.
"""

import pytest

from app.repos.mongo.prices import MongoPriceRepo
from app.repos.pg.prices import PgPriceRepo
from app.services.prices import Level, Scope, window_ending
from tests.equivalence.cases import comparable_params

pytestmark = pytest.mark.integration


def raw_sales(pg_conn, window):
    return pg_conn.execute(
        """
        SELECT t.txn_id, t.txn_month, t.building_id, t.street, t.town, t.flat_type, t.storey_min,
               b.lease_commence_year
        FROM v_transaction_detail t JOIN building b USING (building_id)
        WHERE t.txn_month BETWEEN %s AND %s
        """,
        [window.start, window.end],
    ).fetchall()


def test_comparables_are_the_matching_sales_latest_first(pg_conn, mongo_db):
    params = comparable_params(pg_conn)
    window = window_ending(params[0]["end"])
    rows = raw_sales(pg_conn, window)
    pg, mongo = PgPriceRepo(pg_conn), MongoPriceRepo(mongo_db)
    levels_with_more = {level: 0 for level in Level}
    for p in params[:: max(1, len(params) // 300)]:
        scope = Scope(Level(p["level"]), p["building_id"], p["street"], p["town"], p["flat_type"], window, p["storey"])

        def matches(r, s=scope):
            if r[5] != s.flat_type or (s.storey_min is not None and r[6] != s.storey_min):
                return False
            if s.level == Level.BLOCK:
                return r[2] == s.building_id
            return r[4] == s.town and (s.level == Level.TOWN or r[3] == s.street)

        expected = sorted((r for r in rows if matches(r)), key=lambda r: (r[1], r[0]), reverse=True)
        for repo in (pg, mongo):
            got = repo.comparables(scope)
            assert [(c.txn_id, c.lease_year) for c in got] == [(r[0], r[7]) for r in expected], (repo, p)
        levels_with_more[scope.level] = max(levels_with_more[scope.level], len(expected))
    # Each level was exercised with real sales, and the town finds more than a block.
    assert 0 < levels_with_more[Level.BLOCK] < levels_with_more[Level.TOWN]


def test_the_street_level_keeps_to_the_town(pg_conn, mongo_db):
    """A street is matched within the block's town: the same street in another town finds nothing."""
    p = next(p for p in comparable_params(pg_conn) if p["level"] == "street" and p["storey"] is None)
    other = pg_conn.execute("SELECT name FROM town WHERE name <> %s ORDER BY name LIMIT 1", [p["town"]]).fetchone()[0]
    window = window_ending(p["end"])
    for repo in (PgPriceRepo(pg_conn), MongoPriceRepo(mongo_db)):
        right = Scope(Level.STREET, p["building_id"], p["street"], p["town"], p["flat_type"], window)
        wrong = Scope(Level.STREET, p["building_id"], p["street"], other, p["flat_type"], window)
        assert repo.comparables(right) and repo.comparables(wrong) == []
