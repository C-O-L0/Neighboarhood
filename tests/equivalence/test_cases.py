"""Run every registered case on both databases and require equivalent DTOs."""

import psycopg
import pytest

from app.repos.mongo.building import MongoBuildingRepo
from app.repos.pg.building import PgBuildingRepo
from tests.equivalence.cases import CASES, is_sample
from tests.equivalence.compare import assert_equivalent

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_case_equivalent(case, pg_conn, mongo_db):
    params = case.params(pg_conn)
    assert params, "a case needs at least one parameter set"
    failures = []
    for p in params:
        pg, mongo = case.pg_call(pg_conn, p), case.mongo_call(mongo_db, p)
        try:
            assert_equivalent(pg, mongo, path=f"{case.name}{p}")
        except AssertionError as exc:
            failures.append(str(exc))
    assert not failures, f"{len(failures)} of {len(params)} differ; first: {failures[0]}"


def test_sampling_rule(pg_conn):
    count = pg_conn.execute("SELECT count(*) FROM building").fetchone()[0]
    ids = [p["building_id"] for p in CASES[0].params(pg_conn)]
    if is_sample(pg_conn):
        assert len(ids) == count
    else:
        assert len(ids) < count
        stub = pg_conn.execute("SELECT building_id FROM building WHERE NOT in_property_dataset").fetchall()
        teban = pg_conn.execute(
            "SELECT building_id FROM building WHERE block = '37' AND street = 'TEBAN GDNS RD'"
        ).fetchone()
        assert {r[0] for r in stub} <= set(ids) and teban[0] in ids


def test_missing_building_is_none_in_both(pg_conn, mongo_db):
    assert PgBuildingRepo(pg_conn).get(2_000_000_000) is None
    assert MongoBuildingRepo(mongo_db).get(2_000_000_000) is None


def test_settle_connection_is_autocommit_and_idle(settle):
    assert settle.autocommit is True
    assert settle.info.transaction_status == psycopg.pq.TransactionStatus.IDLE


def test_pg_building_repo_works_after_another_query_on_the_connection(pg_conn):
    pg_conn.execute("SELECT 1").fetchone()
    detail = PgBuildingRepo(pg_conn).get(1)
    assert detail is not None and detail.building_id == 1
    assert pg_conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
