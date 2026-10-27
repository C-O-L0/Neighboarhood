"""Search: PostgreSQL and MongoDB return the same total, the same rows and the same order."""

import pytest

from app.repos.mongo.search import MongoSearchRepo
from app.repos.pg.search import PgSearchRepo
from app.services.search import PAGE_SIZE, SearchQuery
from tests.equivalence.cases import (
    DUPLICATE_PAIR,
    SEARCH_PARAMS,
    TIED_AREA,
    TIED_PRICE,
    is_sample,
    search_edge_params,
    search_params,
)
from tests.equivalence.compare import assert_equivalent

pytestmark = pytest.mark.integration


def both(pg_conn, mongo_db, **params):
    q = SearchQuery.model_validate(params)
    return PgSearchRepo(pg_conn).search(q), MongoSearchRepo(mongo_db).search(q)


def test_at_least_twelve_parameter_sets_return_rows(pg_conn, mongo_db):
    assert len(search_params(pg_conn)) >= 12
    for p in search_params(pg_conn):
        pg, mongo = both(pg_conn, mongo_db, **p)
        assert pg.rows and mongo.rows, p
        assert_equivalent(pg, mongo, path=f"search{p}")


@pytest.mark.parametrize("p", SEARCH_PARAMS, ids=lambda p: "&".join(f"{k}={v}" for k, v in p.items()) or "all")
def test_each_parameter_set_is_equivalent(pg_conn, mongo_db, p):
    assert_equivalent(*both(pg_conn, mongo_db, **p))


@pytest.mark.parametrize("sort", ["price", "area"])
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_page_boundary_inside_tied_sort_values(pg_conn, mongo_db, sort, order):
    base = {**(TIED_PRICE if sort == "price" else TIED_AREA), "sort": sort, "order": order}
    p1 = both(pg_conn, mongo_db, **base)
    p2 = both(pg_conn, mongo_db, **base, page="2")
    key = "resale_price" if sort == "price" else "floor_area_sqm"
    for pg, mongo in (p1, p2):
        assert [r.txn_id for r in pg.rows] == [r.txn_id for r in mongo.rows]
        assert_equivalent(pg, mongo)
    # the boundary falls inside one run of identical values, and ids keep their direction across it
    assert len(p1[0].rows) == PAGE_SIZE and p2[0].rows
    assert getattr(p1[0].rows[-1], key) == getattr(p2[0].rows[0], key)
    ids = [r.txn_id for r in p1[0].rows + p2[0].rows]
    assert ids == sorted(ids, reverse=order == "desc")
    assert len(set(ids)) == len(ids)


def test_edge_cases_are_equivalent(pg_conn, mongo_db):
    for p in search_edge_params(pg_conn):
        assert_equivalent(*both(pg_conn, mongo_db, **p), path=f"search{p}")


def test_street_dot_is_literal(pg_conn, mongo_db):
    pg, mongo = both(pg_conn, mongo_db, street="ST.")
    if is_sample(pg_conn):
        assert pg.total == mongo.total == 0 and pg.rows == mongo.rows == []
    else:
        george = both(pg_conn, mongo_db, street="ST. GEORGE'S")
        assert george[0].total > 0
        assert_equivalent(*george)
        assert all("ST." in r.street for r in george[0].rows)
        assert_equivalent(pg, mongo)


@pytest.mark.parametrize("street", ["%", "_", ".*", "' OR 1=1 --", "\\"])
def test_injection_strings_match_literally(pg_conn, mongo_db, street):
    pg, mongo = both(pg_conn, mongo_db, street=street)
    assert_equivalent(pg, mongo)
    assert all(street.upper() in r.street for r in pg.rows)  # zero rows, or a literal match


def test_beyond_last_page_is_empty_with_the_true_total(pg_conn, mongo_db):
    everything = both(pg_conn, mongo_db)
    assert everything[0].total == everything[1].total > 0
    for pg, mongo in (both(pg_conn, mongo_db, page="10000"), both(pg_conn, mongo_db, town="BISHAN", page="10000")):
        assert pg.rows == mongo.rows == []
        assert pg.total == mongo.total > 0
        assert pg.page == mongo.page == 10000
        assert pg.pages == mongo.pages
    last = both(pg_conn, mongo_db, page=str(everything[0].pages))
    assert last[0].rows and len(last[0].rows) <= PAGE_SIZE
    assert both(pg_conn, mongo_db, page=str(everything[0].pages + 1))[0].rows == []


def test_exact_duplicates_are_kept_as_two_sales(pg_conn, mongo_db):
    # The sample keeps every sale of its towns, so the pair is the same on full data.
    pg, mongo = both(pg_conn, mongo_db, **DUPLICATE_PAIR)
    assert_equivalent(pg, mongo)
    pair = [r for r in pg.rows if str(r.resale_price) == "468000.00" and r.storey_min == 4]
    assert len(pair) == 2 and pair[0].txn_id != pair[1].txn_id
    assert [str(r.floor_area_sqm) for r in pair] == ["88.0", "88.0"]
    assert {r.flat_model for r in pair} == {"Improved"}
    assert [r.txn_id for r in pair] == sorted((r.txn_id for r in pair), reverse=True)  # month desc, then id desc
