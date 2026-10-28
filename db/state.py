"""State fingerprint of both databases, used by the backup and restore scripts (task 4.3).

    python -m db.state                       print the fingerprint as one line of JSON
    python -m db.state --settle              run refresh_town_stats() as the owner, then print the fingerprint
    python -m db.state --compare FILE|-      compare the live state with the "fingerprint" in a backup manifest
                                             (a path, or - for standard input); exit 1 and list every difference

Run in the etl service, which holds the owner and ETL identities. It reads the PostgreSQL catalogs and tables as
neighboar_owner and the MongoDB collections as the etl user, and writes nothing except under --settle. The output
holds counts, hashes, object names, owners and privileges. It never holds a password, a connection string or a row.

PostgreSQL part: per table, materialised view and the migration log, the row count and
md5(string_agg(t::text, ',' ORDER BY primary key, else unique index, else the row text)); sequence positions;
the owner and privileges of every schema object; pg_default_acl; function flags (SECURITY DEFINER, proconfig);
constraint type and validity; trigger state; index definitions. A NULL ACL is reported as the default ACL for
the object (the owner holds everything), so it equals an explicit all-to-owner ACL. Constraint definitions are
not compared as text, because pg_dump may deparse an expression differently (ANY (ARRAY[...]) forms).

MongoDB part: per collection the count, a SHA-256 of the documents sorted by _id (canonical extended JSON), the
collection options (validator, validationLevel, validationAction) and index_information().
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import psycopg
from bson import json_util
from psycopg import sql
from pymongo import MongoClient
from pymongo.database import Database

from app.config import get_settings

RELKIND = {"r": "table", "m": "materialized view", "v": "view", "S": "sequence", "p": "partitioned table"}
MAX_DIFFERENCES = 40


def _jsonable(value: Any) -> Any:
    """BSON types to plain JSON through canonical extended JSON (types such as Decimal128 stay distinguishable)."""
    return json.loads(json_util.dumps(value, json_options=json_util.CANONICAL_JSON_OPTIONS))


# ------------------------------------------------------------------------------------------------ PostgreSQL


def _order_by(conn: psycopg.Connection, oid: int) -> sql.Composable:
    """Primary key columns, else the columns of the first unique index, else the whole row as text."""
    row = conn.execute(
        """WITH ix AS (SELECT i.indrelid, i.indkey
                         FROM pg_index i
                        WHERE i.indrelid = %s AND i.indisvalid AND i.indpred IS NULL AND i.indexprs IS NULL
                          AND (i.indisprimary OR i.indisunique)
                        ORDER BY i.indisprimary DESC, i.indexrelid
                        LIMIT 1)
           SELECT array_agg(a.attname ORDER BY k.ord)
             FROM ix
            CROSS JOIN LATERAL unnest(ix.indkey::int2[]) WITH ORDINALITY AS k(attnum, ord)
             JOIN pg_attribute a ON a.attrelid = ix.indrelid AND a.attnum = k.attnum""",
        (oid,),
    ).fetchone()
    if row and row[0]:
        return sql.SQL(", ").join(sql.SQL("t.{}").format(sql.Identifier(c)) for c in row[0])
    return sql.SQL("t::text")


def postgres_fingerprint(conn: psycopg.Connection) -> dict[str, Any]:
    out: dict[str, Any] = {}

    relations = conn.execute(
        """SELECT c.oid, c.relname, c.relkind, c.relowner::regrole::text,
                  ARRAY(SELECT x::text
                          FROM unnest(coalesce(c.relacl,
                                               acldefault((CASE c.relkind WHEN 'S' THEN 's' ELSE 'r' END)::"char",
                                                          c.relowner))) x
                         ORDER BY 1)
             FROM pg_class c
            WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'm', 'v', 'S', 'p')
            ORDER BY c.relname"""
    ).fetchall()

    tables: dict[str, Any] = {}
    sequences: dict[str, Any] = {}
    owners: dict[str, str] = {}
    privileges: dict[str, list[str]] = {}
    for oid, name, kind, owner, acl in relations:
        label = f"{RELKIND[kind]} {name}"
        owners[label] = owner
        privileges[label] = acl
        if kind in ("r", "m", "p"):
            query = sql.SQL("SELECT count(*), md5(string_agg(t::text, ',' ORDER BY {order})) FROM {tbl} t").format(
                order=_order_by(conn, oid), tbl=sql.Identifier("public", name)
            )
            count, digest = conn.execute(query).fetchone()
            tables[name] = {"rows": count, "md5": digest}
        elif kind == "S":
            last_value, is_called = conn.execute(
                sql.SQL("SELECT last_value, is_called FROM {}").format(sql.Identifier("public", name))
            ).fetchone()
            sequences[name] = {"last_value": last_value, "is_called": is_called}
    out["tables"] = tables
    out["sequences"] = sequences

    functions: dict[str, Any] = {}
    for sig, owner, acl, secdef, config, src_md5 in conn.execute(
        """SELECT p.oid::regprocedure::text, p.proowner::regrole::text,
                  ARRAY(SELECT x::text
                          FROM unnest(coalesce(p.proacl, acldefault('f'::"char", p.proowner))) x
                         ORDER BY 1),
                  p.prosecdef, p.proconfig, md5(p.prosrc)
             FROM pg_proc p
            WHERE p.pronamespace = 'public'::regnamespace
            ORDER BY 1"""
    ):
        owners[f"function {sig}"] = owner
        privileges[f"function {sig}"] = acl
        functions[sig] = {"prosecdef": secdef, "proconfig": config, "prosrc_md5": src_md5}
    out["functions"] = functions

    schema_owner, schema_acl = conn.execute(
        """SELECT nspowner::regrole::text,
                  ARRAY(SELECT x::text FROM unnest(coalesce(nspacl, acldefault('n'::"char", nspowner))) x ORDER BY 1)
             FROM pg_namespace WHERE nspname = 'public'"""
    ).fetchone()
    owners["schema public"] = schema_owner
    privileges["schema public"] = schema_acl
    db_owner, db_acl = conn.execute(
        """SELECT datdba::regrole::text,
                  ARRAY(SELECT x::text FROM unnest(coalesce(datacl, acldefault('d'::"char", datdba))) x ORDER BY 1)
             FROM pg_database WHERE datname = current_database()"""
    ).fetchone()
    owners["database"] = db_owner
    privileges["database"] = db_acl
    out["owners"] = owners
    out["privileges"] = privileges

    out["default_acl"] = sorted(
        f"{role} {schema} {kind}: {','.join(acl)}"
        for role, schema, kind, acl in conn.execute(
            """SELECT d.defaclrole::regrole::text,
                      CASE WHEN d.defaclnamespace = 0 THEN '*' ELSE d.defaclnamespace::regnamespace::text END,
                      d.defaclobjtype::text,
                      ARRAY(SELECT x::text FROM unnest(d.defaclacl) x ORDER BY 1)
                 FROM pg_default_acl d"""
        )
    )

    out["constraints"] = {
        f"{table}.{name}": {"type": kind, "validated": validated}
        for table, name, kind, validated in conn.execute(
            """SELECT conrelid::regclass::text, conname, contype::text, convalidated
                 FROM pg_constraint WHERE connamespace = 'public'::regnamespace AND conrelid <> 0"""
        )
    }
    out["triggers"] = {
        f"{table}.{name}": enabled
        for table, name, enabled in conn.execute(
            "SELECT tgrelid::regclass::text, tgname, tgenabled::text FROM pg_trigger WHERE NOT tgisinternal"
        )
    }
    out["indexes"] = dict(
        conn.execute("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'").fetchall()
    )
    return out


# ---------------------------------------------------------------------------------------------------- MongoDB


def mongo_fingerprint(db: Database) -> dict[str, Any]:
    options = {c["name"]: c.get("options", {}) for c in db.list_collections()}
    out: dict[str, Any] = {}
    for name in sorted(n for n in options if not n.startswith("system.")):
        coll = db[name]
        digest = hashlib.sha256()
        count = 0
        for doc in coll.find().sort("_id", 1).batch_size(5000):
            digest.update(json_util.dumps(doc, json_options=json_util.CANONICAL_JSON_OPTIONS).encode())
            digest.update(b"\n")
            count += 1
        out[name] = {
            "count": count,
            "sha256": digest.hexdigest(),
            "options": _jsonable(options[name]),
            "indexes": _jsonable(coll.index_information()),
        }
    return out


# ------------------------------------------------------------------------------------------------- the whole


def fingerprint(settle: bool = False) -> dict[str, Any]:
    s = get_settings()
    if not s.pg_owner_password or not s.mongo_etl_password:
        raise SystemExit("[state] PG_OWNER_PASSWORD and MONGO_ETL_PASSWORD must be set")
    client = MongoClient(s.mongo_uri("etl", direct=True), serverSelectionTimeoutMS=10000)
    try:
        with psycopg.connect(s.pg_dsn("neighboar_owner", "neighboar-state"), autocommit=True) as conn:
            if settle:
                conn.execute("SELECT refresh_town_stats()")
            # One snapshot, so the tables and the catalogs agree with each other.
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                pg = postgres_fingerprint(conn)
        mongo = mongo_fingerprint(client[s.mongo_db])
    finally:
        client.close()
    return {"postgres": pg, "mongo": mongo}


def differences(expected: Any, actual: Any, path: str = "") -> list[str]:
    """Every path at which two JSON values differ. Values are hashes, counts and names, never secrets."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        found: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            here = f"{path}/{key}"
            if key not in expected:
                found.append(f"{here}: not in the backup")
            elif key not in actual:
                found.append(f"{here}: missing now")
            else:
                found += differences(expected[key], actual[key], here)
        return found
    if expected != actual:
        return [f"{path}: backup {json.dumps(expected)[:120]} now {json.dumps(actual)[:120]}"]
    return []


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--settle", action="store_true", help="run refresh_town_stats() first")
    ap.add_argument("--compare", metavar="FILE", help="a backup manifest.json, or - for standard input")
    args = ap.parse_args(argv)

    live = fingerprint(settle=args.settle)
    if not args.compare:
        print(dumps(live))
        return 0

    text = sys.stdin.read() if args.compare == "-" else Path(args.compare).read_text(encoding="utf-8")
    expected = json.loads(text)["fingerprint"]
    # Round-trip the live value through JSON so both sides have the same types.
    found = differences(expected, json.loads(dumps(live)))
    if found:
        print(f"[state] the databases differ from the backup in {len(found)} place(s):", file=sys.stderr)
        for line in found[:MAX_DIFFERENCES]:
            print(f"  {line}", file=sys.stderr)
        if len(found) > MAX_DIFFERENCES:
            print(f"  ... and {len(found) - MAX_DIFFERENCES} more", file=sys.stderr)
        return 1
    print("[state] the databases match the backup")
    return 0


if __name__ == "__main__":
    sys.exit(main())
