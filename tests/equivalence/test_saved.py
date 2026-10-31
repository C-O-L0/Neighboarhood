"""Saved-block writes: both databases give the same outcomes and the same state (task 3.2)."""

import threading
import time
from dataclasses import replace

import psycopg
import pytest
from pymongo.errors import WriteError

from app.repos.dto import Outcome, RolledBack
from app.repos.mongo.accounts import MongoAccountRepo
from app.repos.mongo.saved import MongoSavedRepo
from app.repos.pg import saved as pg_saved
from app.repos.pg.accounts import PgAccountRepo
from app.repos.pg.saved import PgSavedRepo
from tests.equivalence.cases import MISSING_BUILDING, SAVE_CAP, run_step, saved_script
from tests.equivalence.compare import assert_equivalent

pytestmark = pytest.mark.integration


def state(repo, buyer_id):
    """The buyer's saved list without saved_at, which differs by design between the databases."""
    return [replace(s, saved_at=None) for s in repo.list(buyer_id)]


def plain_ids(conn, count):
    rows = conn.execute(
        "SELECT building_id FROM building WHERE in_property_dataset ORDER BY building_id LIMIT %s", (count,)
    ).fetchall()
    return [r[0] for r in rows]


def test_scripted_sequence_gives_the_same_outcomes_and_state(pg_conn, mongo_db, new_buyers):
    pg_id, mongo_id = new_buyers()
    pg, mongo = PgSavedRepo(pg_conn), MongoSavedRepo(mongo_db)
    steps = saved_script(pg_conn)
    outcomes = []
    for n, step in enumerate(steps):
        a, b = run_step(pg, pg_id, step), run_step(mongo, mongo_id, step)
        assert_equivalent(a, b, path=f"step {n} {step}")
        assert_equivalent(state(pg, pg_id), state(mongo, mongo_id), path=f"state after step {n} {step}")
        outcomes.append(a)
    # The script reaches every outcome, in the order the spec requires.
    assert {
        Outcome.OK,
        Outcome.ALREADY_SAVED,
        Outcome.NOT_SAVED,
        Outcome.NO_SUCH_BUILDING,
        Outcome.CAP_REACHED,
    } == set(outcomes)
    assert outcomes[-3:] == [Outcome.CAP_REACHED, Outcome.ALREADY_SAVED, Outcome.NO_SUCH_BUILDING]
    final_pg, final_mongo = pg.list(pg_id), mongo.list(mongo_id)
    assert len(final_pg) == SAVE_CAP
    assert_equivalent(state(pg, pg_id), state(mongo, mongo_id))
    assert any(s.note == "now with a note" for s in final_pg)
    assert [s.saved_at.tzinfo is not None for s in final_pg + final_mongo] == [True] * (2 * SAVE_CAP)
    assert PgAccountRepo(pg_conn).by_id(pg_id).last_active_at is not None
    assert MongoAccountRepo(mongo_db).by_id(mongo_id).last_active_at is not None


def test_not_saved_and_failed_saves_do_not_touch_last_active_at(pg_conn, mongo_db, new_buyers):
    pg_id, mongo_id = new_buyers()
    pg, mongo = PgSavedRepo(pg_conn), MongoSavedRepo(mongo_db)
    block = plain_ids(pg_conn, 1)[0]
    for repo, buyer_id, accounts in (
        (pg, pg_id, PgAccountRepo(pg_conn)),
        (mongo, mongo_id, MongoAccountRepo(mongo_db)),
    ):
        assert repo.edit_note(buyer_id, block, "x") == Outcome.NOT_SAVED
        assert repo.remove(buyer_id, block) == Outcome.NOT_SAVED
        assert repo.save(buyer_id, MISSING_BUILDING, None) == Outcome.NO_SUCH_BUILDING
        assert accounts.by_id(buyer_id).last_active_at is None
        assert repo.list(buyer_id) == []
        assert repo.save(buyer_id, block, None) == Outcome.OK
        assert accounts.by_id(buyer_id).last_active_at is not None


def test_a_note_of_500_characters_is_stored_and_blank_is_none(pg_conn, mongo_db, new_buyers):
    pg_id, mongo_id = new_buyers()
    pg, mongo = PgSavedRepo(pg_conn), MongoSavedRepo(mongo_db)
    first, second = plain_ids(pg_conn, 2)
    for repo, buyer_id in ((pg, pg_id), (mongo, mongo_id)):
        assert repo.save(buyer_id, first, "x" * 500) == Outcome.OK
        assert repo.save(buyer_id, second, None) == Outcome.OK
    assert_equivalent(state(pg, pg_id), state(mongo, mongo_id))
    assert [len(s.note or "") for s in pg.list(pg_id)] == [500, 0]
    assert pg.list(pg_id)[1].note is None


def fill_to(repo, buyer_id, ids):
    for building_id in ids:
        assert repo.save(buyer_id, building_id, None) == Outcome.OK


def race(calls):
    """Run the callables on separate threads released together; return their results in order."""
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def run(i):
        barrier.wait()
        try:
            results[i] = calls[i]()
        except Exception as exc:  # reported by the assertions below
            results[i] = exc

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(calls))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results


ROUNDS = 3


def wait_for_lock(watcher, pid, timeout=10.0):
    """Poll pg_stat_activity until the backend `pid` is waiting on a heavyweight lock."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = watcher.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", (pid,)).fetchone()
        if row is not None and row[0] == "Lock":
            return
        time.sleep(0.02)
    pytest.fail(f"backend {pid} never waited on a lock within {timeout} s")


def test_trigger_b_serialises_an_interleaved_save_in_postgresql(settings, pg_conn, new_buyers):
    """Deterministic interleave: c1 holds trigger (b)'s buyer-row lock uncommitted while c2 tries to save.

    c2 must block on the lock, then see c1's committed 50th row and get cap_reached. Without the FOR UPDATE it
    would count 49 while c1 is uncommitted and insert a 51st row.
    """
    pg_id, _ = new_buyers()
    ids = plain_ids(pg_conn, SAVE_CAP + 1)
    repo = PgSavedRepo(pg_conn)
    fill_to(repo, pg_id, ids[: SAVE_CAP - 1])
    first, second = ids[SAVE_CAP - 1], ids[SAVE_CAP]
    dsn = settings.pg_dsn("app_rw")
    with (
        psycopg.connect(dsn, autocommit=True) as c1,
        psycopg.connect(dsn, autocommit=True) as c2,
        psycopg.connect(dsn, autocommit=True) as watcher,
    ):
        results = []
        thread = threading.Thread(target=lambda: results.append(PgSavedRepo(c2).save(pg_id, second, None)))
        try:
            with c1.transaction():
                c1.execute(pg_saved._P_INSERT, (pg_id, first, None))  # trigger (b) now holds the buyer-row lock
                c1.execute(pg_saved._TOUCH, (pg_id,))
                thread.start()
                wait_for_lock(watcher, c2.info.backend_pid)
                assert thread.is_alive() and results == []  # c2 is blocked, not finished
        finally:
            thread.join(timeout=30)
        assert not thread.is_alive()
    assert results == [Outcome.CAP_REACHED]
    assert len(repo.list(pg_id)) == SAVE_CAP
    assert {s.building_id for s in repo.list(pg_id)} >= {first}
    assert second not in {s.building_id for s in repo.list(pg_id)}


def test_the_cap_holds_under_concurrent_saves_in_postgresql(settings, pg_conn, new_buyers):
    pg_id, _ = new_buyers()
    ids = plain_ids(pg_conn, SAVE_CAP + 1 + ROUNDS)
    repo = PgSavedRepo(pg_conn)
    fill_to(repo, pg_id, ids[: SAVE_CAP - 1])
    with (
        psycopg.connect(settings.pg_dsn("app_rw"), autocommit=True) as c1,
        psycopg.connect(settings.pg_dsn("app_rw"), autocommit=True) as c2,
    ):
        for n in range(ROUNDS):
            first, second = ids[SAVE_CAP - 1 + n], ids[SAVE_CAP + n]
            results = race(
                [
                    lambda f=first: PgSavedRepo(c1).save(pg_id, f, None),
                    lambda s=second: PgSavedRepo(c2).save(pg_id, s, None),
                ]
            )
            assert sorted(results, key=str) == [Outcome.CAP_REACHED, Outcome.OK], results
            assert len(repo.list(pg_id)) == SAVE_CAP
            winner = next(s for s in repo.list(pg_id) if s.building_id in (first, second))
            assert repo.remove(pg_id, winner.building_id) == Outcome.OK  # back to 49 for the next round


def test_the_cap_holds_under_concurrent_saves_in_mongodb(mongo_db, pg_conn, new_buyers):
    _, mongo_id = new_buyers()
    ids = plain_ids(pg_conn, SAVE_CAP + 1 + ROUNDS)
    repo = MongoSavedRepo(mongo_db)
    fill_to(repo, mongo_id, ids[: SAVE_CAP - 1])
    for n in range(ROUNDS):
        first, second = ids[SAVE_CAP - 1 + n], ids[SAVE_CAP + n]
        results = race(
            [
                lambda f=first: MongoSavedRepo(mongo_db).save(mongo_id, f, None),
                lambda s=second: MongoSavedRepo(mongo_db).save(mongo_id, s, None),
            ]
        )
        assert sorted(results, key=str) == [Outcome.CAP_REACHED, Outcome.OK], results
        assert len(repo.list(mongo_id)) == SAVE_CAP
        winner = next(s for s in repo.list(mongo_id) if s.building_id in (first, second))
        assert repo.remove(mongo_id, winner.building_id) == Outcome.OK


def test_the_forced_failure_rolls_back_in_both_databases(pg_conn, mongo_db, new_buyers):
    pg_id, mongo_id = new_buyers()
    first, second = plain_ids(pg_conn, 2)
    cases = (
        (PgSavedRepo(pg_conn), pg_id, PgAccountRepo(pg_conn), RolledBack(insert=1, update=1)),
        (MongoSavedRepo(mongo_db), mongo_id, MongoAccountRepo(mongo_db), RolledBack(matched=1)),
    )
    for repo, buyer_id, accounts, expected in cases:
        assert repo.save(buyer_id, first, "kept") == Outcome.OK  # gives last_active_at a value to compare
        list_before = repo.list(buyer_id)
        active_before = accounts.by_id(buyer_id).last_active_at
        assert active_before is not None
        assert repo.save(buyer_id, second, "demo", True) == expected
        assert repo.list(buyer_id) == list_before
        assert accounts.by_id(buyer_id).last_active_at == active_before
        assert repo.save(buyer_id, second, "demo") == Outcome.OK  # the same block now saves normally
        assert [s.building_id for s in repo.list(buyer_id)] == [first, second]
        # Outcomes that are not a save still win over the demo.
        assert repo.save(buyer_id, second, None, True) == Outcome.ALREADY_SAVED
        assert repo.save(buyer_id, MISSING_BUILDING, None, True) == Outcome.NO_SUCH_BUILDING


def test_postgresql_rejects_a_note_over_500_characters(pg_conn, new_buyers):
    pg_id, _ = new_buyers()
    block = plain_ids(pg_conn, 1)[0]
    with pytest.raises(psycopg.errors.StringDataRightTruncation), pg_conn.transaction():
        pg_conn.execute(
            "INSERT INTO saved_building (buyer_id, building_id, note) VALUES (%s, %s, %s)", (pg_id, block, "x" * 501)
        )
    assert PgSavedRepo(pg_conn).list(pg_id) == []


def test_mongodb_validator_rejects_a_note_over_500_characters_and_a_51st_save(pg_conn, mongo_db, new_buyers):
    _, mongo_id = new_buyers()
    block = plain_ids(pg_conn, 1)[0]
    entry = {"building_id": block, "block": "1", "street": "X", "town": "Y", "note": "x" * 501}
    entry["saved_at"] = MongoAccountRepo(mongo_db).by_id(mongo_id).created_at
    with pytest.raises(WriteError) as too_long:
        mongo_db.buyers.update_one({"_id": mongo_id}, {"$push": {"saved": entry}})
    assert too_long.value.code == 121
    # Bypassing the repo's $ne guard and its count: the validator alone stops the 51st element.
    ids = plain_ids(pg_conn, SAVE_CAP + 1)
    repo = MongoSavedRepo(mongo_db)
    fill_to(repo, mongo_id, ids[:SAVE_CAP])
    raw = {**entry, "building_id": ids[SAVE_CAP], "note": None}
    with pytest.raises(WriteError) as over_cap:
        mongo_db.buyers.update_one({"_id": mongo_id}, {"$push": {"saved": raw}})
    assert over_cap.value.code == 121
    assert len(repo.list(mongo_id)) == SAVE_CAP


def test_trigger_b_stops_a_51st_row_written_directly(pg_conn, new_buyers):
    pg_id, _ = new_buyers()
    ids = plain_ids(pg_conn, SAVE_CAP + 1)
    fill_to(PgSavedRepo(pg_conn), pg_id, ids[:SAVE_CAP])
    with pytest.raises(psycopg.errors.CheckViolation), pg_conn.transaction():
        pg_conn.execute("INSERT INTO saved_building (buyer_id, building_id) VALUES (%s, %s)", (pg_id, ids[SAVE_CAP]))
