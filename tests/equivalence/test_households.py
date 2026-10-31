"""Households (task T11): both databases give the same outcomes and the same state for one script of writes.

The buyers are fresh in each database, so their ids differ; every id is replaced by a label (A, B, C) before
comparing, and times (saved_at, created_at) and household ids, which differ by design, are left out.
"""

import threading
from dataclasses import fields, is_dataclass, replace

import psycopg
import pytest

from app.repos.dto import AcceptResult, HouseholdOutcome, Outcome, SavedBlock
from app.repos.mongo.accounts import MongoAccountRepo
from app.repos.mongo.households import MongoHouseholdRepo
from app.repos.mongo.saved import MongoSavedRepo
from app.repos.pg.accounts import PgAccountRepo
from app.repos.pg.households import PgHouseholdRepo
from app.repos.pg.saved import PgSavedRepo
from tests.equivalence.compare import assert_equivalent

pytestmark = pytest.mark.integration

HO = HouseholdOutcome
ID_FIELDS = {"buyer_id", "saved_by", "note_by"}
DROPPED = {"saved_at", "created_at", "household_id"}


def norm(value, labels: dict[int, str]):
    """The value with buyer ids replaced by labels and per-database fields set to None."""
    if is_dataclass(value):
        changes = {}
        for f in fields(value):
            v = getattr(value, f.name)
            if f.name in DROPPED:
                changes[f.name] = None
            elif f.name in ID_FIELDS and v is not None:
                changes[f.name] = labels[v]
            else:
                changes[f.name] = norm(v, labels)
        return replace(value, **changes)
    if isinstance(value, list | tuple):
        return type(value)(norm(v, labels) for v in value)
    if isinstance(value, dict):
        return {k: norm(v, labels) for k, v in value.items()}
    return value


def blocks(conn, n):
    rows = conn.execute(
        "SELECT building_id FROM building WHERE in_property_dataset ORDER BY building_id LIMIT %s", (n,)
    ).fetchall()
    return [r[0] for r in rows]


class Side:
    """One database's repos and its three fresh buyers."""

    def __init__(self, households, saved, accounts, ids: list[int]):
        self.h, self.s, self.accounts = households, saved, accounts
        self.ids = dict(zip("ABC", ids, strict=True))
        self.emails = {k: accounts.by_id(v).email for k, v in self.ids.items()}
        self.labels = {v: k for k, v in self.ids.items()}

    def state(self):
        return {k: (self.h.household(i), self.h.invitations(i), self.s.list(i)) for k, i in self.ids.items()}


@pytest.fixture
def sides(pg_conn, mongo_db, new_buyers):
    made = [new_buyers() for _ in range(3)]
    pg = Side(PgHouseholdRepo(pg_conn), PgSavedRepo(pg_conn), PgAccountRepo(pg_conn), [p for p, _ in made])
    mongo = Side(
        MongoHouseholdRepo(mongo_db), MongoSavedRepo(mongo_db), MongoAccountRepo(mongo_db), [m for _, m in made]
    )
    return pg, mongo


def script(b: list[int]):
    """(description, function of a Side returning a result). b holds building ids."""
    return [
        ("A saves two blocks", lambda s: [s.s.save(s.ids["A"], b[0], None), s.s.save(s.ids["A"], b[1], "A's note")]),
        ("B saves two, one shared", lambda s: [s.s.save(s.ids["B"], b[1], "B's"), s.s.save(s.ids["B"], b[2], "B2")]),
        ("A invites C, any case", lambda s: s.h.invite(s.ids["A"], s.emails["C"].upper())),
        ("A invites B instead", lambda s: s.h.invite(s.ids["A"], s.emails["B"])),
        ("A invites themselves", lambda s: s.h.invite(s.ids["A"], s.emails["A"])),
        ("A invites nobody", lambda s: s.h.invite(s.ids["A"], "nobody-at-all@example.com")),
        ("C declines what A never sent", lambda s: s.h.decline(s.ids["C"], s.ids["A"])),
        ("B accepts what C never sent", lambda s: s.h.accept(s.ids["B"], s.ids["C"], True)),
        ("C invites B too", lambda s: s.h.invite(s.ids["C"], s.emails["B"])),
        ("B accepts A, merging", lambda s: s.h.accept(s.ids["B"], s.ids["A"], True)),
        ("B accepts A again", lambda s: s.h.accept(s.ids["B"], s.ids["A"], True)),
        ("B edits a note A saved", lambda s: s.s.edit_note(s.ids["B"], b[0], "B on A's block")),
        ("A clears a note", lambda s: s.s.edit_note(s.ids["A"], b[2], None)),
        ("A removes a block", lambda s: s.s.remove(s.ids["A"], b[1])),
        ("B saves a shared block again", lambda s: s.s.save(s.ids["B"], b[0], None)),
        ("B saves a new block", lambda s: s.s.save(s.ids["B"], b[3], "new")),
        ("C invites A", lambda s: s.h.invite(s.ids["C"], s.emails["A"])),
        ("A invites C", lambda s: s.h.invite(s.ids["A"], s.emails["C"])),
        ("C saves a block", lambda s: s.s.save(s.ids["C"], b[4], "C's")),
        ("C cancels nothing", lambda s: s.h.cancel(s.ids["C"])),
        ("B leaves", lambda s: s.h.leave(s.ids["B"])),
        ("B leaves again", lambda s: s.h.leave(s.ids["B"])),
        ("A saves a kept block", lambda s: s.s.save(s.ids["A"], b[0], None)),
        ("A invites C after B left", lambda s: s.h.invite(s.ids["A"], s.emails["C"])),
        ("A cancels", lambda s: s.h.cancel(s.ids["A"])),
        ("A invites C again", lambda s: s.h.invite(s.ids["A"], s.emails["C"])),
        ("C accepts without merging", lambda s: s.h.accept(s.ids["C"], s.ids["A"], False)),
        ("A leaves", lambda s: s.h.leave(s.ids["A"])),
    ]


def test_scripted_households_give_the_same_outcomes_and_state(sides, pg_conn):
    pg, mongo = sides
    results = []
    for n, (what, step) in enumerate(script(blocks(pg_conn, 5))):
        a, b = step(pg), step(mongo)
        assert_equivalent(norm(a, pg.labels), norm(b, mongo.labels), path=f"step {n} {what}")
        assert_equivalent(norm(pg.state(), pg.labels), norm(mongo.state(), mongo.labels), path=f"after {n} {what}")
        results.append((what, a))
    got = dict(results)
    assert got["A invites themselves"] == HO.SELF and got["A invites nobody"] == HO.NOT_FOUND
    assert got["C declines what A never sent"] == got["B accepts what C never sent"].outcome == HO.NO_INVITATION
    assert got["B accepts A, merging"] == AcceptResult(HO.OK, merged=1, left_out=0)  # b1 was A's already
    assert got["B accepts A again"].outcome == HO.NO_INVITATION  # accepting removed it
    assert got["C invites A"] == HO.PARTNER_IN_HOUSEHOLD and got["A invites C"] == HO.IN_HOUSEHOLD
    assert got["B leaves again"] == HO.NOT_IN_HOUSEHOLD and got["C cancels nothing"] == HO.NO_INVITATION
    assert got["C accepts without merging"] == AcceptResult(HO.OK, merged=0, left_out=1)
    assert got["B saves a shared block again"] == Outcome.ALREADY_SAVED
    assert got["A saves a kept block"] == Outcome.ALREADY_SAVED  # A kept the shortlist when B left
    assert got["A invites C after B left"] == HO.OK


def test_shared_blocks_record_who_saved_them_and_who_wrote_the_note(sides, pg_conn):
    b = blocks(pg_conn, 4)
    for side in sides:
        a, p = side.ids["A"], side.ids["B"]
        side.s.save(a, b[0], "from A")
        side.s.save(p, b[1], "from B")
        side.s.save(p, b[2], None)
        assert side.h.invite(a, side.emails["B"]) == HO.OK
        assert side.h.accept(p, a, True).outcome == HO.OK
        side.s.edit_note(p, b[0], "B wrote over it")
        side.s.save(a, b[3], None)
        listed = {s.building_id: (s.saved_by, s.note_by, s.note) for s in side.s.list(a)}
        assert listed == {
            b[0]: (a, p, "B wrote over it"),
            b[1]: (p, p, "from B"),
            b[2]: (p, None, None),
            b[3]: (a, None, None),
        }
        assert side.s.list(a) == side.s.list(p)
        assert [m.seat for m in side.h.household(p).members] == [1, 2]
        assert side.h.household(a).saved_count == 4


def test_leaving_gives_the_partner_the_shortlist_without_authors(sides, pg_conn):
    b = blocks(pg_conn, 2)
    for side in sides:
        a, p = side.ids["A"], side.ids["B"]
        side.s.save(a, b[0], "kept")
        side.h.invite(a, side.emails["B"])
        side.h.accept(p, a, True)
        side.s.save(p, b[1], "also kept")
        assert side.h.leave(a) == HO.OK
        assert side.s.list(a) == [] and side.h.household(a) is None and side.h.household(p) is None
        kept = side.s.list(p)
        assert [(s.building_id, s.note, s.saved_by, s.note_by) for s in kept] == [
            (b[0], "kept", None, None),
            (b[1], "also kept", None, None),
        ]
        assert side.s.save(a, b[0], None) == Outcome.OK  # the leaver starts again on their own


def test_merging_stops_at_the_cap_and_counts_what_was_left_out(sides, pg_conn):
    b = blocks(pg_conn, 55)
    pg, mongo = sides
    for side in sides:
        a, p = side.ids["A"], side.ids["B"]
        for building in b[:45]:
            assert side.s.save(a, building, None) == Outcome.OK
        for building in b[40:55]:  # 5 already shared, 10 new
            assert side.s.save(p, building, None) == Outcome.OK
        side.h.invite(a, side.emails["B"])
        assert side.h.accept(p, a, True) == AcceptResult(HO.OK, merged=5, left_out=5)
        assert side.h.household(a).saved_count == 50
        assert side.s.save(a, b[0], None) == Outcome.ALREADY_SAVED
        assert side.s.save(p, b[54], None) == Outcome.CAP_REACHED
    # The oldest of the partner's new blocks went in first; the two databases chose the same ones.
    assert [s.building_id for s in pg.s.list(pg.ids["A"])] == [s.building_id for s in mongo.s.list(mongo.ids["A"])]


def test_a_member_reads_the_household_and_invitations_the_same_way(sides):
    pg, mongo = sides
    for side in sides:
        side.h.invite(side.ids["A"], side.emails["C"])
        side.h.invite(side.ids["B"], side.emails["C"])
    for key in "ABC":
        assert_equivalent(
            norm(pg.h.invitations(pg.ids[key]), pg.labels), norm(mongo.h.invitations(mongo.ids[key]), mongo.labels)
        )
    received = pg.h.invitations(pg.ids["C"]).received
    assert [i.inviter.buyer_id for i in received] == [pg.ids["A"], pg.ids["B"]]  # oldest first


def test_household_writes_touch_only_the_acting_buyer(sides):
    for side in sides:
        a, p = side.ids["A"], side.ids["B"]

        def active(key, side=side):
            return side.accounts.by_id(side.ids[key]).last_active_at is not None

        assert side.h.invite(a, side.emails["B"]) == HO.OK
        assert (active("A"), active("B")) == (True, False)
        assert side.h.decline(p, side.ids["C"]) == HO.NO_INVITATION and not active("B")  # nothing changed
        assert side.h.accept(p, a, False).outcome == HO.OK
        assert active("B") and not active("C")


def _concurrent(fn_a, fn_b):
    results, start = [None, None], threading.Barrier(2)

    def run(i, fn):
        start.wait()
        results[i] = fn()

    threads = [threading.Thread(target=run, args=(0, fn_a)), threading.Thread(target=run, args=(1, fn_b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_two_invitations_accepted_at_once_make_one_household_in_postgresql(settings, sides):
    pg, _ = sides
    a, b, c = pg.ids["A"], pg.ids["B"], pg.ids["C"]
    pg.h.invite(a, pg.emails["B"])
    pg.h.invite(c, pg.emails["B"])

    def accept(inviter):
        with psycopg.connect(settings.pg_dsn("app_rw"), autocommit=True) as conn:
            return PgHouseholdRepo(conn).accept(b, inviter, True).outcome

    outcomes = sorted(_concurrent(lambda: accept(a), lambda: accept(c)))
    assert outcomes == sorted([HO.OK, HO.NO_INVITATION])  # the winner's accept removed the other invitation
    assert len(pg.h.household(b).members) == 2


def test_two_invitations_accepted_at_once_make_one_household_in_mongodb(sides):
    _, mongo = sides
    a, b, c = mongo.ids["A"], mongo.ids["B"], mongo.ids["C"]
    mongo.h.invite(a, mongo.emails["B"])
    mongo.h.invite(c, mongo.emails["B"])
    outcomes = sorted(
        _concurrent(lambda: mongo.h.accept(b, a, True).outcome, lambda: mongo.h.accept(b, c, True).outcome)
    )
    assert outcomes == sorted([HO.OK, HO.NO_INVITATION])
    assert len(mongo.h.household(b).members) == 2
    assert mongo.h.household(a) is None or mongo.h.household(c) is None


def test_a_member_cannot_have_personal_saves_in_postgresql(sides, pg_conn, owner_conn):
    pg, _ = sides
    a, p = pg.ids["A"], pg.ids["B"]
    pg.h.invite(a, pg.emails["B"])
    pg.h.accept(p, a, True)
    with pytest.raises(psycopg.errors.CheckViolation, match="is in a household"):
        with owner_conn.transaction():
            owner_conn.execute(
                "INSERT INTO saved_building (buyer_id, building_id) VALUES (%s, %s)", (a, blocks(pg_conn, 1)[0])
            )
    assert isinstance(pg.s.list(a), list) and all(isinstance(s, SavedBlock) for s in pg.s.list(a))
