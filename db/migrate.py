"""Idempotent database setup for both DBMSs. Safe to run on every start.

Steps, in order:
  1. MongoDB: initiate the single-node replica set (member host mongodb:27017)
     and wait until it is PRIMARY.
  2. PostgreSQL (as superuser): create or update the four roles with passwords
     from .env, make neighboar_owner own the database and schema, lock down
     PUBLIC, set default privileges.
  3. PostgreSQL (as neighboar_owner via SET ROLE): apply db/postgres/*.sql in
     name order, recording each file and its checksum in schema_migration.
  4. MongoDB (as root): create or update custom roles and users, then apply
     collection validators from db/mongo/validators/*.json.

Usage (inside Compose):  docker compose --profile tools run --rm migrate
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import psycopg
from psycopg import sql
from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError

from app.config import Settings, get_settings

ROOT = Path(__file__).resolve().parent
PG_DIR = ROOT / "postgres"
MONGO_VALIDATORS = ROOT / "mongo" / "validators"

PG_APP_ROLES = ("app_rw", "app_admin", "app_ro")
MONGO_MEMBER_HOST = "mongodb:27017"

# MongoDB custom roles. "" as collection means every non-system collection.
MONGO_ROLES = {
    "neighboar_read": [{"collection": "", "actions": ["find"]}],
    "neighboar_app_write": [
        {"collection": c, "actions": ["insert", "update", "remove"]} for c in ("buyers", "counters")
    ],
    "neighboar_admin_write": [
        {"collection": c, "actions": ["insert", "update", "remove"]}
        for c in ("resale_transactions", "building_profiles")
    ],
}
MONGO_USERS = {
    "etl": ["readWrite"],
    "app": ["neighboar_read", "neighboar_app_write"],
    "app_admin": ["neighboar_read", "neighboar_app_write", "neighboar_admin_write"],
}


def log(msg: str) -> None:
    print(f"[migrate] {msg}", flush=True)


def retry(what: str, fn, attempts: int = 30, delay: float = 2.0):
    for i in range(1, attempts + 1):
        try:
            return fn()
        except (psycopg.OperationalError, PyMongoError) as exc:
            if i == attempts:
                raise
            log(f"waiting for {what} ({exc.__class__.__name__}), retry {i}/{attempts}")
            time.sleep(delay)


# --------------------------------------------------------------------- MongoDB


def mongo_replica_set(s: Settings) -> None:
    client = MongoClient(s.mongo_uri("root", direct=True), serverSelectionTimeoutMS=5000)
    try:
        retry("MongoDB", lambda: client.admin.command("ping"))
        try:
            client.admin.command("replSetGetStatus")
            log("replica set already initiated")
        except OperationFailure as exc:
            if exc.code != 94:  # NotYetInitialized
                raise
            config = {"_id": s.mongo_replica_set, "members": [{"_id": 0, "host": MONGO_MEMBER_HOST}]}
            client.admin.command("replSetInitiate", config)
            log(f"replica set {s.mongo_replica_set} initiated with member {MONGO_MEMBER_HOST}")
        for _ in range(60):
            if client.admin.command("hello").get("isWritablePrimary"):
                log("replica set member is PRIMARY")
                return
            time.sleep(1)
        raise RuntimeError("replica set did not reach PRIMARY within 60 s")
    finally:
        client.close()


def mongo_roles_and_users(s: Settings) -> None:
    client = MongoClient(s.mongo_uri("root", direct=True), serverSelectionTimeoutMS=5000)
    try:
        db = client[s.mongo_db]
        existing_roles = {r["role"] for r in db.command("rolesInfo", 1)["roles"]}
        for role, privs in MONGO_ROLES.items():
            privileges = [
                {"resource": {"db": s.mongo_db, "collection": p["collection"]}, "actions": p["actions"]} for p in privs
            ]
            cmd = "updateRole" if role in existing_roles else "createRole"
            db.command(cmd, role, privileges=privileges, roles=[])
            log(f"mongo role {role}: {cmd}")

        passwords = {
            "etl": s.mongo_etl_password,
            "app": s.mongo_app_password,
            "app_admin": s.mongo_app_admin_password,
        }
        existing_users = {u["user"] for u in db.command("usersInfo", 1)["users"]}
        for user, roles in MONGO_USERS.items():
            if not passwords[user]:
                raise RuntimeError(f"password for MongoDB user {user} is empty; check .env")
            role_docs = [{"role": r, "db": s.mongo_db} for r in roles]
            cmd = "updateUser" if user in existing_users else "createUser"
            db.command(cmd, user, pwd=passwords[user], roles=role_docs)
            log(f"mongo user {user}: {cmd}")
    finally:
        client.close()


def mongo_validators(s: Settings) -> None:
    files = sorted(MONGO_VALIDATORS.glob("*.json")) if MONGO_VALIDATORS.exists() else []
    if not files:
        log("no Mongo validators yet")
        return
    client = MongoClient(s.mongo_uri("root", direct=True), serverSelectionTimeoutMS=5000)
    try:
        db = client[s.mongo_db]
        existing = set(db.list_collection_names())
        for f in files:
            name = f.stem
            schema = json.loads(f.read_text(encoding="utf-8"))
            opts = {"validator": {"$jsonSchema": schema}, "validationLevel": "strict", "validationAction": "error"}
            if name in existing:
                db.command("collMod", name, **opts)
                log(f"mongo validator {name}: updated")
            else:
                db.create_collection(name, **opts)
                log(f"mongo validator {name}: collection created")
    finally:
        client.close()


# ------------------------------------------------------------------ PostgreSQL


def pg_roles(s: Settings) -> None:
    passwords = {
        "neighboar_owner": s.pg_owner_password,
        "app_rw": s.pg_app_rw_password,
        "app_admin": s.pg_app_admin_password,
        "app_ro": s.pg_app_ro_password,
    }
    for role, pw in passwords.items():
        if not pw:
            raise RuntimeError(f"password for PostgreSQL role {role} is empty; check .env")

    conn = retry("PostgreSQL", lambda: psycopg.connect(s.pg_dsn(s.postgres_user), autocommit=True))
    with conn:
        for role, pw in passwords.items():
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            verb = "ALTER" if exists else "CREATE"
            conn.execute(
                sql.SQL("{} ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
                    sql.SQL(verb), sql.Identifier(role), sql.Literal(pw)
                )
            )
            log(f"pg role {role}: {'altered' if exists else 'created'}")

        db = sql.Identifier(s.postgres_db)
        owner = sql.Identifier("neighboar_owner")
        apps = sql.SQL(", ").join(sql.Identifier(r) for r in PG_APP_ROLES)
        for stmt in (
            sql.SQL("ALTER DATABASE {} OWNER TO {}").format(db, owner),
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(db),
            sql.SQL("REVOKE CONNECT ON DATABASE postgres FROM PUBLIC"),
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(db, apps),
        ):
            conn.execute(stmt)
        for stmt in (
            sql.SQL("ALTER SCHEMA public OWNER TO {}").format(owner),
            sql.SQL("REVOKE ALL ON SCHEMA public FROM PUBLIC"),
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(apps),
            sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT SELECT ON TABLES TO {}").format(
                owner, apps
            ),
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
            ).format(owner),
        ):
            conn.execute(stmt)
    log("pg database and schema ownership, PUBLIC revoked, default privileges set")


def pg_apply_sql(s: Settings) -> None:
    files = sorted(PG_DIR.glob("*.sql")) if PG_DIR.exists() else []
    with psycopg.connect(s.pg_dsn(s.postgres_user)) as conn:
        conn.execute("SET ROLE neighboar_owner")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migration (
                   filename   TEXT PRIMARY KEY,
                   sha256     CHAR(64) NOT NULL,
                   applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
        )
        applied = dict(conn.execute("SELECT filename, sha256 FROM schema_migration").fetchall())
        conn.commit()
        for f in files:
            text = f.read_text(encoding="utf-8")
            digest = hashlib.sha256(text.encode()).hexdigest()
            if f.name in applied:
                if applied[f.name] != digest:
                    raise RuntimeError(
                        f"{f.name} changed after it was applied; add a new migration file "
                        "or reset the database volume (docker compose down -v)"
                    )
                continue
            with conn.transaction():
                conn.execute(text)
                conn.execute("INSERT INTO schema_migration (filename, sha256) VALUES (%s, %s)", (f.name, digest))
            log(f"pg applied {f.name}")
    log(f"pg migrations up to date ({len(files)} files)")


# ------------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Set up roles, users and schemas in both databases.")
    ap.add_argument("--skip-mongo", action="store_true")
    ap.add_argument("--skip-pg", action="store_true")
    args = ap.parse_args(argv)
    s = get_settings()

    if not args.skip_mongo:
        mongo_replica_set(s)
    if not args.skip_pg:
        pg_roles(s)
        pg_apply_sql(s)
    if not args.skip_mongo:
        mongo_roles_and_users(s)
        mongo_validators(s)
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
