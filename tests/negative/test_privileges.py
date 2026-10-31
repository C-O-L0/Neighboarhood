"""The privilege grid of every runtime identity in both databases, and real writes that must be refused (task 3.5b).

PostgreSQL: the privileges of app_rw, app_admin and app_ro over every table, view, materialised view, sequence and
function in `public`, and CREATE on the schema, compared with an allowlist written out below from
db/postgres/005_grants.sql, db/migrate.py and BUILD_PLAN section 4. An object that is not in the allowlist, an
allowlist entry for an object that no longer exists, and any extra or missing privilege all fail.

MongoDB: `connectionStatus` with `showPrivileges` for `app` and `app_admin`, compared with MONGO_ROLES in
db/migrate.py, plus the role names each user holds.

Then real attempted writes for a representative subset: PostgreSQL SQLSTATE 42501, MongoDB code 13.
"""

from __future__ import annotations

import psycopg
import pytest
from pymongo.errors import OperationFailure

from db.migrate import MONGO_ROLES

pytestmark = pytest.mark.integration

ROLES = ("app_rw", "app_admin", "app_ro")
NONE = frozenset()
READ = frozenset({"SELECT"})
DML = frozenset({"INSERT", "UPDATE", "DELETE"})

TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN")
SEQUENCE_PRIVILEGES = ("USAGE", "SELECT", "UPDATE")


def _readable(extra_rw: frozenset = NONE, extra_admin: frozenset = NONE) -> dict[str, frozenset]:
    return {"app_rw": READ | extra_rw, "app_admin": READ | extra_rw | extra_admin, "app_ro": READ}


# Relations: every table, view and materialised view the owner creates gives the three roles SELECT (default
# privileges in db/migrate.py). Accounts, saved blocks and households are writable by app_rw and app_admin; sales
# only by app_admin; the migration log is closed to all three (005_grants.sql, 006_households.sql).
RELATIONS: dict[str, dict[str, frozenset]] = {
    "town": _readable(),
    "flat_type": _readable(),
    "flat_model": _readable(),
    "building": _readable(),
    "building_flat_supply": _readable(),
    "hawker_centre": _readable(),
    "resale_transaction": _readable(extra_admin=DML),
    "buyer": _readable(extra_rw=DML),
    "saved_building": _readable(extra_rw=DML),
    "household": _readable(extra_rw=DML),
    "household_member": _readable(extra_rw=DML),
    "household_invitation": _readable(extra_rw=DML),
    "household_saved": _readable(extra_rw=DML),
    "schema_migration": {r: NONE for r in ROLES},
    "v_transaction_detail": _readable(),
    "v_building_units": _readable(),
    "v_town_year_stats": _readable(),
    "mv_town_month_stats": _readable(),
}

# Sequences: only the three that the app's own inserts draw from. Nobody writes building ids at runtime.
SEQUENCES: dict[str, dict[str, frozenset]] = {
    "building_building_id_seq": {r: NONE for r in ROLES},
    "buyer_buyer_id_seq": {"app_rw": frozenset({"USAGE"}), "app_admin": frozenset({"USAGE"}), "app_ro": NONE},
    "resale_transaction_txn_id_seq": {"app_rw": NONE, "app_admin": frozenset({"USAGE"}), "app_ro": NONE},
    "household_household_id_seq": {"app_rw": frozenset({"USAGE"}), "app_admin": frozenset({"USAGE"}), "app_ro": NONE},
}

# Functions: the refresh is for app_admin only (EXECUTE revoked from PUBLIC). The trigger functions are
# internal; a trigger needs no EXECUTE grant, so nobody has one.
FUNCTIONS: dict[str, dict[str, bool]] = {
    "refresh_town_stats": {"app_rw": False, "app_admin": True, "app_ro": False},
    "check_resale_transaction": {r: False for r in ROLES},
    "check_saved_building_cap": {r: False for r in ROLES},
    "check_household_saved_cap": {r: False for r in ROLES},
    "check_saved_building_owner": {r: False for r in ROLES},
}


# ------------------------------------------------------------------------------------------------ PostgreSQL


def _names(conn, kinds: tuple[str, ...]) -> set[str]:
    rows = conn.execute(
        """SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public' AND c.relkind = ANY(%s)""",
        (list(kinds),),
    )
    return {r[0] for r in rows}


def test_the_allowlists_cover_exactly_the_objects_in_public(pg_owner):
    """A new table, view, sequence or function must be added to the allowlist, with its expected privileges."""
    assert _names(pg_owner, ("r", "v", "m", "p", "f")) == set(RELATIONS)
    assert _names(pg_owner, ("S",)) == set(SEQUENCES)
    functions = {
        r[0]
        for r in pg_owner.execute(
            "SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'"
        )
    }
    assert functions == set(FUNCTIONS)


@pytest.mark.parametrize("role", ROLES)
def test_postgres_table_privileges_match_the_allowlist(role, pg_owner):
    problems = []
    for table, expected in RELATIONS.items():
        held = {
            p
            for p in TABLE_PRIVILEGES
            if pg_owner.execute("SELECT has_table_privilege(%s, %s, %s)", (role, f"public.{table}", p)).fetchone()[0]
        }
        if held != expected[role]:
            problems.append(f"{table}: extra {sorted(held - expected[role])}, missing {sorted(expected[role] - held)}")
    assert not problems, f"{role}: " + "; ".join(problems)


@pytest.mark.parametrize("role", ROLES)
def test_postgres_sequence_privileges_match_the_allowlist(role, pg_owner):
    problems = []
    for seq, expected in SEQUENCES.items():
        held = {
            p
            for p in SEQUENCE_PRIVILEGES
            if pg_owner.execute("SELECT has_sequence_privilege(%s, %s, %s)", (role, f"public.{seq}", p)).fetchone()[0]
        }
        if held != expected[role]:
            problems.append(f"{seq}: extra {sorted(held - expected[role])}, missing {sorted(expected[role] - held)}")
    assert not problems, f"{role}: " + "; ".join(problems)


@pytest.mark.parametrize("role", ROLES)
def test_postgres_function_privileges_match_the_allowlist(role, pg_owner):
    held = {}
    for name in FUNCTIONS:
        oid = pg_owner.execute(
            "SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.proname = %s",
            (name,),
        ).fetchone()[0]
        held[name] = pg_owner.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, oid)).fetchone()[0]
    assert held == {name: allowed[role] for name, allowed in FUNCTIONS.items()}


@pytest.mark.parametrize("role", ROLES)
def test_postgres_schema_and_database_privileges(role, pg_owner):
    """USAGE but not CREATE on the schema; CONNECT but neither CREATE nor TEMPORARY on the database."""

    def has(query, *args):
        return pg_owner.execute(query, (role, *args)).fetchone()[0]

    assert has("SELECT has_schema_privilege(%s, 'public', 'USAGE')") is True
    assert has("SELECT has_schema_privilege(%s, 'public', 'CREATE')") is False
    assert has("SELECT has_database_privilege(%s, current_database(), 'CONNECT')") is True
    assert has("SELECT has_database_privilege(%s, current_database(), 'CREATE')") is False
    assert has("SELECT has_database_privilege(%s, current_database(), 'TEMPORARY')") is False


@pytest.mark.parametrize("role", ROLES)
def test_the_app_roles_are_plain_login_roles(role, pg_owner):
    row = pg_owner.execute(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolreplication, rolcanlogin "
        "FROM pg_roles WHERE rolname = %s",
        (role,),
    ).fetchone()
    assert row == (False, False, False, False, False, True)
    members = pg_owner.execute(
        "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = %s", (role,)
    ).fetchone()[0]
    assert members == 0, "a role that inherits from another role would add privileges the grid does not list"


# Real attempted writes. The ACL check comes before any constraint check, so each statement is refused with 42501
# before it could change anything (and the fixtures roll back in any case).
PG_DENIED = [
    ("app_rw", "DELETE FROM resale_transaction WHERE txn_id = -1"),
    ("app_rw", "UPDATE building SET block = block WHERE building_id = -1"),
    ("app_rw", "DELETE FROM schema_migration"),
    ("app_rw", "SELECT refresh_town_stats()"),
    ("app_rw", "SELECT nextval('resale_transaction_txn_id_seq')"),
    ("app_rw", "CREATE TABLE privilege_probe (x int)"),
    ("app_admin", "DELETE FROM building WHERE building_id = -1"),
    ("app_admin", "UPDATE town SET name = name WHERE town_id = -1"),
    ("app_admin", "DELETE FROM schema_migration"),
    ("app_admin", "SELECT nextval('building_building_id_seq')"),
    ("app_admin", "TRUNCATE saved_building"),
    ("app_admin", "CREATE TABLE privilege_probe (x int)"),
    ("app_ro", "DELETE FROM buyer WHERE buyer_id = -1"),
    ("app_ro", "UPDATE saved_building SET note = note WHERE buyer_id = -1"),
    ("app_ro", "INSERT INTO resale_transaction (txn_id) VALUES (-1)"),
    ("app_ro", "SELECT refresh_town_stats()"),
    ("app_ro", "SELECT nextval('buyer_buyer_id_seq')"),
    ("app_ro", "DELETE FROM household_saved WHERE household_id = -1"),
    ("app_ro", "INSERT INTO household_invitation (inviter_id, invitee_id) VALUES (-1, -2)"),
    ("app_ro", "SELECT nextval('household_household_id_seq')"),
    ("app_admin", "TRUNCATE household"),
    ("app_ro", "CREATE TABLE privilege_probe (x int)"),
]


@pytest.mark.parametrize("role,statement", PG_DENIED, ids=[f"{r}:{s[:40]}" for r, s in PG_DENIED])
def test_postgres_refuses_a_write_the_role_may_not_make(role, statement, pg_rw, pg_admin, pg_ro):
    conn = {"app_rw": pg_rw, "app_admin": pg_admin, "app_ro": pg_ro}[role]
    with pytest.raises(psycopg.Error) as caught:
        conn.execute(statement)
    assert caught.value.sqlstate == "42501", caught.value
    conn.rollback()


def test_the_permitted_writes_are_not_refused(pg_rw, pg_admin):
    """The other side of the grid: a statement each writer may run reaches the table (no rows match)."""
    assert pg_rw.execute("DELETE FROM saved_building WHERE buyer_id = -1").rowcount == 0
    assert pg_rw.execute("UPDATE buyer SET is_admin = is_admin WHERE buyer_id = -1").rowcount == 0
    assert pg_admin.execute("DELETE FROM resale_transaction WHERE txn_id = -1").rowcount == 0
    assert pg_rw.execute("DELETE FROM household_member WHERE buyer_id = -1").rowcount == 0
    assert pg_rw.execute("UPDATE household_saved SET note = note WHERE household_id = -1").rowcount == 0
    pg_rw.rollback()
    pg_admin.rollback()


# --------------------------------------------------------------------------------------------------- MongoDB

MONGO_USER_ROLES = {
    "app": {"neighboar_read", "neighboar_app_write"},
    "app_admin": {"neighboar_read", "neighboar_app_write", "neighboar_admin_write"},
}


def _expected_mongo(user: str) -> dict[str, frozenset]:
    """collection -> actions, from MONGO_ROLES for the roles the user holds ('' is every non-system collection)."""
    grants: dict[str, set] = {}
    for role in MONGO_USER_ROLES[user]:
        for privilege in MONGO_ROLES[role]:
            grants.setdefault(privilege["collection"], set()).update(privilege["actions"])
    return {collection: frozenset(actions) for collection, actions in grants.items()}


def _held_mongo(db, name: str) -> tuple[set, dict[str, frozenset]]:
    info = db.command("connectionStatus", showPrivileges=True)["authInfo"]
    assert [u["user"] for u in info["authenticatedUsers"]] == [name]
    roles = {r["role"] for r in info["authenticatedUserRoles"]}
    held: dict[str, set] = {}
    for privilege in info["authenticatedUserPrivileges"]:
        resource = privilege["resource"]
        assert resource["db"] == db.name and "collection" in resource, resource  # nothing on the cluster or other dbs
        held.setdefault(resource["collection"], set()).update(privilege["actions"])
    return roles, {collection: frozenset(actions) for collection, actions in held.items()}


@pytest.mark.parametrize("user", ["app", "app_admin"])
def test_mongo_privileges_match_the_roles_in_migrate(user, mongo_app, mongo_admin):
    db = {"app": mongo_app, "app_admin": mongo_admin}[user]
    roles, held = _held_mongo(db, user)
    assert roles == MONGO_USER_ROLES[user]
    assert held == _expected_mongo(user)


def test_the_two_mongo_users_differ_only_by_the_admin_write_role():
    app, admin = _expected_mongo("app"), _expected_mongo("app_admin")
    assert set(admin) - set(app) == {"resale_transactions", "building_profiles"}
    assert all(admin[c] == app[c] for c in app)


def _refused(operation):
    with pytest.raises(OperationFailure) as caught:
        operation()
    assert caught.value.code == 13, caught.value  # Unauthorized


# collection -> the identities that may not write it (the app user writes buyers and counters; the admin adds
# sales and block profiles; neither writes the hawker centres).
MONGO_DENIED = [
    ("app", "resale_transactions"),
    ("app", "building_profiles"),
    ("app", "hawker_centres"),
    ("app_admin", "hawker_centres"),
]


@pytest.mark.parametrize("user,collection", MONGO_DENIED, ids=[f"{u}:{c}" for u, c in MONGO_DENIED])
def test_mongo_refuses_insert_update_and_remove_where_denied(user, collection, mongo_app, mongo_admin):
    coll = {"app": mongo_app, "app_admin": mongo_admin}[user][collection]
    _refused(lambda: coll.insert_one({"_id": -1}))
    _refused(lambda: coll.update_one({"_id": -1}, {"$set": {"probe": 1}}))
    _refused(lambda: coll.delete_one({"_id": -1}))


@pytest.mark.parametrize("user", ["app", "app_admin"])
def test_mongo_refuses_collmod_drop_createindex_and_other_databases(user, mongo_app, mongo_admin):
    db = {"app": mongo_app, "app_admin": mongo_admin}[user]
    # Collections the user can write are still protected from schema changes. The probes are harmless if a
    # privilege were ever widened: a no-op collMod, and a collection name that does not exist.
    for collection in ("buyers", "counters"):
        _refused(lambda collection=collection: db.command("collMod", collection))
    _refused(lambda: db.command("collMod", "privilege_probe", validator={}))
    _refused(lambda: db["privilege_probe"].drop())
    _refused(lambda: db["privilege_probe"].create_index("probe"))
    _refused(lambda: db.client.drop_database("privilege_probe_db"))
    for other in ("privilege_probe_db", "admin", "config"):
        _refused(lambda other=other: db.client[other]["probe"].insert_one({"_id": -1}))
