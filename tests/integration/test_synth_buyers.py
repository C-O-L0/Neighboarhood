"""etl/synth_buyers.py against both databases (needs the sample or full data loaded and the DEMO_* variables).

These tests re-run the script, which replaces buyers 1 to 200 and their saves in both databases;
the data they leave is equivalent to the data the pipeline loads (only the discarded random
passwords' hashes differ). Tests that break the data on purpose put it back in a `finally`.
"""

import psycopg
import pytest
from argon2 import PasswordHasher

from etl import synth_buyers
from etl.synth_buyers import SynthCollision

pytestmark = pytest.mark.integration


@pytest.fixture
def pg(settings):
    """The schema owner in autocommit mode, as etl/synth_buyers.py connects."""
    if not settings.pg_owner_password:
        pytest.skip("PG_OWNER_PASSWORD is not set; pass it to the test container")
    with psycopg.connect(settings.pg_dsn("neighboar_owner"), autocommit=True) as conn:
        yield conn


@pytest.fixture
def db(mongo_etl):
    return mongo_etl


def run(pg, db, demo_passwords):
    return synth_buyers.synthesise(pg, db, *demo_passwords)


def pg_accounts(pg, with_hash=False):
    cols = "buyer_id, email, display_name, is_admin, created_at"
    cols += ", password_hash, last_active_at" if with_hash else ""
    return set(pg.execute(f"SELECT {cols} FROM buyer WHERE buyer_id <= 200").fetchall())  # noqa: S608


def pg_saves(pg):
    rows = pg.execute("SELECT buyer_id, building_id, note, saved_at FROM saved_building WHERE buyer_id <= 200")
    return set(rows.fetchall())


def mongo_accounts(db, with_hash=False):
    out = set()
    for d in db.buyers.find({"_id": {"$lte": 200}}):
        row = (d["_id"], d["email"], d["display_name"], d["is_admin"], d["created_at"])
        out.add(row + ((d["password_hash"], d["last_active_at"]) if with_hash else ()))
    return out


def mongo_saves(db):
    return {
        (d["_id"], s["building_id"], s["note"], s["saved_at"])
        for d in db.buyers.find({"_id": {"$lte": 200}})
        for s in d["saved"]
    }


def state(pg, db):
    """Everything both databases hold for buyers 1 to 200, hashes and sequences included."""
    seq = pg.execute("SELECT last_value FROM buyer_buyer_id_seq").fetchone()[0]
    counter = db.counters.find_one({"_id": "buyer"})["seq"]
    return pg_accounts(pg, True), pg_saves(pg), mongo_accounts(db, True), mongo_saves(db), seq, counter


def test_two_hundred_buyers_and_identical_saves_in_both(pg, db, demo_passwords):
    ids = sorted(r[0] for r in pg_accounts(pg))
    assert ids == list(range(1, 201))
    assert sorted(r[0] for r in mongo_accounts(db)) == ids
    saves = pg_saves(pg)
    assert 700 <= len(saves) <= 1300
    assert saves == mongo_saves(db)
    # the extended-reference fields come from building_profiles, same as the PostgreSQL join
    for d in db.buyers.find({"_id": {"$lte": 200}}):
        for s in d["saved"]:
            profile = db.building_profiles.find_one({"_id": s["building_id"]})
            expected = (profile["block"], profile["street"], profile["town"]["name"])
            assert (s["block"], s["street"], s["town"]) == expected
    # the same hash string in both, and the demo accounts accept their configured passwords
    pg_h = {r[0]: r[5] for r in pg_accounts(pg, True)}
    mongo_h = {r[0]: r[5] for r in mongo_accounts(db, True)}
    assert pg_h == mongo_h
    hasher = PasswordHasher()
    assert hasher.verify(pg_h[1], demo_passwords[0]) and hasher.verify(pg_h[2], demo_passwords[1])
    assert pg_h[1].startswith("$argon2")
    admins = pg.execute("SELECT buyer_id FROM buyer WHERE is_admin AND buyer_id <= 200").fetchall()
    assert admins == [(2,)]
    assert {r[6] for r in pg_accounts(pg, True)} == {None} == {r[6] for r in mongo_accounts(db, True)}


def test_rerun_gives_identical_data(pg, db, demo_passwords):
    run(pg, db, demo_passwords)
    first = (pg_saves(pg), pg_accounts(pg), mongo_saves(db), mongo_accounts(db))
    run(pg, db, demo_passwords)
    second = (pg_saves(pg), pg_accounts(pg), mongo_saves(db), mongo_accounts(db))
    assert first == second
    assert first[0] == first[2] and len(first[0]) > 0
    assert first[1] == first[3] and len(first[1]) == 200


def test_main_runs_as_a_script(demo_passwords, pg, db):
    assert synth_buyers.main([]) == 0
    assert len(pg_accounts(pg)) == 200 == len(mongo_accounts(db))


# ---------------------------------------------------------------- safety


def pg_row(pg, buyer_id):
    return pg.execute("SELECT * FROM buyer WHERE buyer_id = %s", (buyer_id,)).fetchone()


def test_other_email_on_a_synthetic_id_stops_both_writes(pg, db, demo_passwords):
    # PostgreSQL clashes, MongoDB must stay untouched; then the other way round.
    original_pg = pg.execute("SELECT email FROM buyer WHERE buyer_id = 150").fetchone()[0]
    before = state(pg, db)
    pg.execute("UPDATE buyer SET email = 'someone.else@example.org' WHERE buyer_id = 150")
    try:
        with pytest.raises(SynthCollision, match=r"150.*someone\.else@example\.org"):
            run(pg, db, demo_passwords)
        assert mongo_accounts(db, True) == before[2] and mongo_saves(db) == before[3]
        assert pg_saves(pg) == before[1]
    finally:
        pg.execute("UPDATE buyer SET email = %s WHERE buyer_id = 150", (original_pg,))
    assert state(pg, db) == before

    db.buyers.update_one({"_id": 150}, {"$set": {"email": "someone.else@example.org"}})
    try:
        with pytest.raises(SynthCollision, match=r"150.*someone\.else@example\.org"):
            run(pg, db, demo_passwords)
        assert pg_accounts(pg, True) == before[0] and pg_saves(pg) == before[1]
        assert pg.execute("SELECT last_value FROM buyer_buyer_id_seq").fetchone()[0] == before[4]
    finally:
        db.buyers.update_one({"_id": 150}, {"$set": {"email": original_pg}})
    assert state(pg, db) == before


def test_synthetic_email_on_another_id_stops_both_writes(pg, db, demo_passwords):
    # A unique email index means buyer 3 must step aside before id 300 can hold its address.
    before = state(pg, db)
    email = synth_buyers.synthetic_email(3)
    pg_buyer, pg_saved = pg_row(pg, 3), pg.execute("SELECT * FROM saved_building WHERE buyer_id = 3").fetchall()
    pg.execute("DELETE FROM buyer WHERE buyer_id = 3")
    pg.execute("INSERT INTO buyer (buyer_id, email, password_hash) VALUES (300, %s, 'x')", (email,))
    try:
        with pytest.raises(SynthCollision, match=r"buyer003@example.com.*buyer 300"):
            run(pg, db, demo_passwords)
        assert mongo_accounts(db, True) == before[2] and mongo_saves(db) == before[3]
    finally:
        pg.execute("DELETE FROM buyer WHERE buyer_id = 300")
        pg.execute("INSERT INTO buyer VALUES (%s, %s, %s, %s, %s, %s, %s)", pg_buyer)
        for row in pg_saved:
            pg.execute("INSERT INTO saved_building VALUES (%s, %s, %s, %s)", row)
    assert state(pg, db) == before

    mongo_buyer = db.buyers.find_one({"_id": 3})
    db.buyers.delete_one({"_id": 3})
    db.buyers.insert_one({**mongo_buyer, "_id": 300, "saved": []})
    try:
        with pytest.raises(SynthCollision, match=r"buyer003@example.com.*buyer 300"):
            run(pg, db, demo_passwords)
        assert pg_accounts(pg, True) == before[0] and pg_saves(pg) == before[1]
    finally:
        db.buyers.delete_one({"_id": 300})
        db.buyers.insert_one(mongo_buyer)
    assert state(pg, db) == before


# ---------------------------------------------------------------- monotonic ids


def test_ids_never_move_backwards(pg, db, demo_passwords):
    seq_before = pg.execute("SELECT last_value, is_called FROM buyer_buyer_id_seq").fetchone()
    counter_before = db.counters.find_one({"_id": "buyer"})["seq"]
    try:
        pg.execute("SELECT setval('buyer_buyer_id_seq', 250)")
        db.counters.update_one({"_id": "buyer"}, {"$set": {"seq": 250}})
        # fifty registrations come and go: ids 201 to 250 existed, id 250 is inserted and deleted
        pg.execute("INSERT INTO buyer (buyer_id, email, password_hash) VALUES (250, 'late@example.org', 'x')")
        pg.execute("DELETE FROM buyer WHERE buyer_id = 250")
        db.buyers.insert_one(
            {"_id": 250, "email": "late@example.org", "password_hash": "x", "is_admin": False,
             "created_at": synth_buyers.CREATED_BASE, "saved": []}
        )  # fmt: skip
        db.buyers.delete_one({"_id": 250})

        run(pg, db, demo_passwords)

        assert pg.execute("SELECT last_value FROM buyer_buyer_id_seq").fetchone()[0] >= 250
        assert db.counters.find_one({"_id": "buyer"})["seq"] >= 250
    finally:
        pg.execute("DELETE FROM buyer WHERE buyer_id = 250")
        db.buyers.delete_one({"_id": 250})
        pg.execute("SELECT setval('buyer_buyer_id_seq', %s, %s)", seq_before)
        db.counters.update_one({"_id": "buyer"}, {"$set": {"seq": counter_before}})


def test_buyers_above_200_are_never_written(pg, db, demo_passwords):
    pg.execute("INSERT INTO buyer (buyer_id, email, password_hash) VALUES (201, 'real.user@example.org', 'x')")
    db.buyers.insert_one(
        {"_id": 201, "email": "real.user@example.org", "password_hash": "x", "is_admin": False,
         "created_at": synth_buyers.CREATED_BASE, "saved": []}
    )  # fmt: skip
    try:
        run(pg, db, demo_passwords)
        assert pg_row(pg, 201)[1:4] == ("real.user@example.org", "x", None)
        assert db.buyers.find_one({"_id": 201})["password_hash"] == "x"
    finally:
        pg.execute("DELETE FROM buyer WHERE buyer_id = 201")
        db.buyers.delete_one({"_id": 201})
