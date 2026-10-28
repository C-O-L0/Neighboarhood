"""Admin sale writes: both databases give the same outcomes and, once settled, the same data (task 3.3)."""

import threading
import time
from collections import Counter
from dataclasses import replace
from datetime import date

import psycopg
import pytest
from pymongo.errors import WriteError

from app.repos.dto import SaleOutcome
from app.repos.mongo.admin import MongoAdminRepo
from app.repos.mongo.building import MongoBuildingRepo
from app.repos.mongo.search import MongoSearchRepo
from app.repos.mongo.trends import MongoTrendRepo
from app.repos.pg.admin import PgAdminRepo
from app.repos.pg.building import PgBuildingRepo
from app.repos.pg.search import PgSearchRepo
from app.repos.pg.trends import PgTrendRepo
from app.services.admin import AdminState, parse_sale_form, rebuild_block_stats, record_sale
from app.services.search import SearchQuery
from app.services.trends import TrendQuery
from db.mongo.stats import stats_for_building
from etl.clean import FLAT_TYPE_IDS
from tests.equivalence.cases import admin_plan, admin_sale
from tests.equivalence.compare import assert_equivalent

pytestmark = pytest.mark.integration


@pytest.fixture
def repos(settle, admin_mongo_db):
    return PgAdminRepo(settle), MongoAdminRepo(admin_mongo_db)


def translate_building(detail, idmap):
    return replace(detail, recent_sales=[replace(r, txn_id=idmap.get(r.txn_id, r.txn_id)) for r in detail.recent_sales])


def translate_rows(rows, idmap):
    return [replace(r, txn_id=idmap.get(r.txn_id, r.txn_id)) for r in rows]


def all_search_rows(repo, block, street):
    query = SearchQuery.model_validate({"block": block, "street": street})
    first = repo.search(query)
    rows = list(first.rows)
    for page in range(2, first.pages + 1):
        rows += repo.search(SearchQuery.model_validate({"block": block, "street": street, "page": str(page)})).rows
    return rows


def sales_multiset(pg_conn, mongo_db, building_ids):
    pg_rows = pg_conn.execute(
        "SELECT building_id, txn_month, flat_type, resale_price, source FROM v_transaction_detail"
        " WHERE building_id = ANY(%s)",
        (list(building_ids),),
    ).fetchall()
    mongo_rows = [
        (d["building_id"], d["txn_month"].date(), d["flat_type"], d["resale_price"].to_decimal(), d["source"])
        for d in mongo_db.resale_transactions.find({"building_id": {"$in": list(building_ids)}})
    ]
    return Counter(map(tuple, pg_rows)), Counter(mongo_rows)


def comparable(result):
    """The part of a SaleResult both databases must agree on (ids differ by design)."""
    return result.outcome, result.reason, result.rolled_back


def test_scripted_sequence_leaves_both_databases_equivalent(
    settle, pg_conn, mongo_db, admin_mongo_db, repos, admin_cleanup
):
    pg, mongo = repos
    plan = admin_plan(pg_conn)
    admin_cleanup.watch(plan.a_id)
    admin_cleanup.watch(plan.b_id)
    admin_cleanup.capture_snapshot(plan.snapshot_txn_id)
    idmap: dict[int, int] = {}

    def both(method, *args):
        a, b = getattr(pg, method)(*args), getattr(mongo, method)(*args)
        assert comparable(a) == comparable(b), (method, args, a, b)
        return a, b

    # A rolled-back insert consumes a PostgreSQL id but no MongoDB counter value, so the ids diverge from here on.
    counter_before = admin_mongo_db.counters.find_one({"_id": "txn"})["seq"]
    both("record", admin_sale(plan, plan.a_id, "111111.11"), True)
    assert admin_mongo_db.counters.find_one({"_id": "txn"})["seq"] == counter_before
    rejected_month = date(plan.a_year_completed - 1, 1, 1)
    a, b = both("record", admin_sale(plan, plan.a_id, "222222.22", month=rejected_month), False)
    assert a.outcome is SaleOutcome.REJECTED
    expected = f"sale in {rejected_month:%Y-%m} predates block completion in {plan.a_year_completed}"
    assert a.reason == b.reason == expected
    assert admin_mongo_db.counters.find_one({"_id": "txn"})["seq"] == counter_before

    sales = {}
    script = (("s1", plan.a_id, "612345.67"), ("s2", plan.a_id, "700000.00"), ("s3", plan.b_id, "505050.50"))
    for name, building, price in script:
        a, b = both("record", admin_sale(plan, building, price), False)
        assert a.outcome is SaleOutcome.OK and a.txn_id and b.txn_id
        idmap[a.txn_id] = b.txn_id
        sales[name] = a.txn_id
    assert admin_mongo_db.counters.find_one({"_id": "txn"})["seq"] == counter_before + 3

    a, b = pg.delete(sales["s2"]), mongo.delete(idmap[sales["s2"]])  # each database deletes its own id for s2
    assert comparable(a) == comparable(b) == (SaleOutcome.OK, None, False)
    both("delete", plan.snapshot_txn_id, False)
    a, _ = both("delete", 2**63 - 1, False)
    assert a.outcome is SaleOutcome.NOT_FOUND
    pg.refresh()  # settle

    for building_id in (plan.a_id, plan.b_id):
        pg_dto = translate_building(PgBuildingRepo(pg_conn).get(building_id), idmap)
        assert_equivalent(pg_dto, MongoBuildingRepo(mongo_db).get(building_id), path=f"building {building_id}")
    query = TrendQuery.model_validate({"town": plan.town, "flat_type": plan.flat_type})
    assert_equivalent(PgTrendRepo(pg_conn).trend(query), MongoTrendRepo(mongo_db).trend(query), path="trends")
    pg_set, mongo_set = sales_multiset(pg_conn, mongo_db, (plan.a_id, plan.b_id))
    assert pg_set == mongo_set
    assert sum(1 for k in pg_set if k[4] == "admin") == 2  # s1 and s3 remain
    block_street = {plan.a_id: (plan.a_block, plan.a_street), plan.b_id: (plan.b_block, plan.b_street)}
    for block, street in block_street.values():
        pg_rows = translate_rows(all_search_rows(PgSearchRepo(pg_conn), block, street), idmap)
        assert_equivalent(pg_rows, all_search_rows(MongoSearchRepo(mongo_db), block, street), path="search")
    rows = all_search_rows(PgSearchRepo(pg_conn), plan.a_block, plan.a_street)
    assert plan.snapshot_txn_id not in {r.txn_id for r in rows}


def test_the_form_quantizes_so_mongodb_keeps_the_scale_of_postgresql(repos, pg_conn, admin_mongo_db, admin_cleanup):
    pg, mongo = repos
    plan = admin_plan(pg_conn)
    admin_cleanup.watch(plan.a_id)
    form, errors = parse_sale_form(
        {
            "block": plan.a_block, "street": plan.a_street.lower(), "month": f"{plan.month:%Y-%m}",
            "flat_type": plan.flat_type, "flat_model": plan.flat_model, "storey_min": "1", "storey_max": "2",
            "floor_area_sqm": "100", "remaining_lease_months": "0", "resale_price": "700000.5",
        }
    )  # fmt: skip
    assert errors == [] and (form.block, form.street) == (plan.a_block, plan.a_street)
    sale = form.new_sale(plan.a_id)
    pg_result, mongo_result = pg.record(sale), mongo.record(sale)
    doc = admin_mongo_db.resale_transactions.find_one({"_id": mongo_result.txn_id})
    assert (str(doc["resale_price"]), str(doc["floor_area_sqm"])) == ("700000.50", "100.0")
    row = pg_conn.execute(
        "SELECT resale_price::text, floor_area_sqm::text FROM resale_transaction WHERE txn_id = %s", (pg_result.txn_id,)
    ).fetchone()
    assert row == ("700000.50", "100.0")
    assert doc["town"] == plan.town and doc["source"] == "admin"


def test_trigger_a_rejects_a_non_resale_type_written_directly_in_both_databases(
    settle, admin_mongo_db, pg_conn, repos, admin_cleanup
):
    plan = admin_plan(pg_conn)
    total_pg = pg_conn.execute("SELECT count(*) FROM resale_transaction").fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation), settle.transaction():
        settle.execute(
            "INSERT INTO resale_transaction (building_id, flat_type_id, flat_model_id, txn_month, storey_min,"
            " storey_max, floor_area_sqm, remaining_lease_months, resale_price, source)"
            " VALUES (%s, %s, 1, %s, 1, 2, 90.0, 600, 500000.00, 'admin')",
            (plan.a_id, FLAT_TYPE_IDS["STUDIO APARTMENT"], plan.month),
        )
    assert pg_conn.execute("SELECT count(*) FROM resale_transaction").fetchone()[0] == total_pg

    template = admin_mongo_db.resale_transactions.find_one({"_id": plan.snapshot_txn_id})
    bad = {**template, "_id": 2**40, "flat_type": "STUDIO APARTMENT", "source": "admin"}
    with pytest.raises(WriteError) as raised:
        admin_mongo_db.resale_transactions.insert_one(bad)
    assert raised.value.code == 121 and "flat_type" in str(raised.value.details)
    assert admin_mongo_db.resale_transactions.count_documents({"_id": 2**40}) == 0


def test_the_forced_failure_changes_nothing_in_either_database(repos, pg_conn, mongo_db, admin_mongo_db, admin_cleanup):
    pg, mongo = repos
    plan = admin_plan(pg_conn)
    admin_cleanup.watch(plan.a_id)
    for repo, building_repo in ((pg, PgBuildingRepo(pg_conn)), (mongo, MongoBuildingRepo(mongo_db))):
        counter = admin_mongo_db.counters.find_one({"_id": "txn"})["seq"]
        page_before = building_repo.get(plan.a_id)
        result = repo.record(admin_sale(plan, plan.a_id, "333333.33"), True)
        assert result.rolled_back and result.before == result.after
        assert building_repo.get(plan.a_id) == page_before
        if repo is mongo:  # an aborted attempt does not consume an id (a PostgreSQL rollback does)
            assert admin_mongo_db.counters.find_one({"_id": "txn"})["seq"] == counter

        created = repo.record(admin_sale(plan, plan.a_id, "333333.34"))
        page_with_sale = building_repo.get(plan.a_id)
        assert page_with_sale != page_before
        demo = repo.delete(created.txn_id, True)
        assert demo.rolled_back and demo.before == demo.after
        assert building_repo.get(plan.a_id) == page_with_sale
        assert repo.delete(created.txn_id).outcome is SaleOutcome.OK
        assert building_repo.get(plan.a_id) == page_before
        assert repo.delete(created.txn_id, True).outcome is SaleOutcome.NOT_FOUND  # gone: the demo reports not_found


def test_a_missing_block_and_a_missing_sale_give_typed_outcomes(repos, pg_conn):
    pg, mongo = repos
    plan = admin_plan(pg_conn)
    for repo in repos:
        assert repo.find_building("NOPE", "NO SUCH ST") is None
        assert repo.find_building(plan.a_block, plan.a_street).building_id == plan.a_id
        assert repo.record(admin_sale(plan, 2_000_000_000, "400000.00")).outcome is SaleOutcome.NO_SUCH_BUILDING
        assert repo.delete(2**63 - 1).outcome is SaleOutcome.NOT_FOUND


def test_a_merge_rebuild_matches_the_in_transaction_stats(repos, pg_conn, mongo_db, admin_cleanup):
    _, mongo = repos
    plan = admin_plan(pg_conn)
    admin_cleanup.watch(plan.a_id)
    reader = MongoBuildingRepo(mongo_db)
    first = mongo.record(admin_sale(plan, plan.a_id, "444444.44"))
    second = mongo.record(admin_sale(plan, plan.a_id, "555555.55"))
    assert mongo.delete(first.txn_id).outcome is SaleOutcome.OK
    transactional = reader.get(plan.a_id)
    assert {s.txn_id for s in transactional.recent_sales} >= {second.txn_id}
    assert mongo.rebuild_stats() > 0
    assert reader.get(plan.a_id) == transactional  # no drift between the two ways of computing stats
    assert mongo.delete(second.txn_id).outcome is SaleOutcome.OK
    transactional = reader.get(plan.a_id)
    mongo.rebuild_stats()
    assert reader.get(plan.a_id) == transactional


def test_a_rebuild_and_an_admin_write_never_overlap(repos, pg_conn, admin_mongo_db, admin_cleanup, monkeypatch):
    _, mongo = repos
    plan = admin_plan(pg_conn)
    admin_cleanup.watch(plan.a_id)
    state = AdminState()
    started, release = threading.Event(), threading.Event()
    order: list[str] = []
    real_rebuild = mongo.rebuild_stats

    def slow_rebuild():
        started.set()
        assert release.wait(30)
        blocks = real_rebuild()
        order.append("rebuild finished")
        return blocks

    monkeypatch.setattr(mongo, "rebuild_stats", slow_rebuild)
    form, errors = parse_sale_form(
        {
            "block": plan.a_block, "street": plan.a_street, "month": f"{plan.month:%Y-%m}", "flat_type": plan.flat_type,
            "flat_model": plan.flat_model, "storey_min": "1", "storey_max": "2", "floor_area_sqm": "90.0",
            "remaining_lease_months": "600", "resale_price": "666666.66",
        }
    )  # fmt: skip
    assert errors == []
    before = admin_mongo_db.resale_transactions.count_documents({"source": "admin"})

    def write():
        action = record_sale(state, "mongo", mongo, form, False)
        order.append("write finished")
        return action

    rebuild_thread = threading.Thread(target=lambda: rebuild_block_stats(state, mongo))
    write_thread = threading.Thread(target=write)
    rebuild_thread.start()
    assert started.wait(30)
    write_thread.start()
    time.sleep(0.5)
    assert write_thread.is_alive() and state.lock.locked()  # the write is waiting for the lock
    assert admin_mongo_db.resale_transactions.count_documents({"source": "admin"}) == before  # and has written nothing
    release.set()
    rebuild_thread.join(60)
    write_thread.join(60)
    assert order == ["rebuild finished", "write finished"]
    assert admin_mongo_db.resale_transactions.count_documents({"source": "admin"}) == before + 1
    stored = admin_mongo_db.building_profiles.find_one({"_id": plan.a_id}, {"stats": 1, "recent_sales": 1})
    assert {"stats": stored["stats"], "recent_sales": stored["recent_sales"]} == stats_for_building(
        admin_mongo_db, plan.a_id
    )
