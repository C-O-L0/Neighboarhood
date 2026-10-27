"""Synthetic buyers and saved blocks, loaded identically into PostgreSQL and MongoDB.

200 buyers with ids 1 to 200 and about 1,000 saved blocks from a fixed seed:

  id 1     demo@neighboar.local   "Demo Buyer"   password from DEMO_BUYER_PASSWORD
  id 2     admin@neighboar.local  "Demo Admin"   password from DEMO_ADMIN_PASSWORD, is_admin
  3 - 200  buyerNNN@example.com                  random password, discarded

The random generator (seed 65) draws only names, saves, notes and time offsets, in a fixed
order, so the data is identical on every run. Passwords never come from it. Each password is
hashed once with argon2 and the same hash string goes to both databases.

Before any write, both databases are checked for a clash: an id from 1 to 200 holding a
different email, or a synthetic email held by a different id. A clash raises SynthCollision
and nothing is written anywhere. Buyers above id 200 (real registrations) are never touched.
Re-running replaces the 200 buyers and their saves and never moves an id sequence backwards.

Usage (both databases loaded, DEMO_* variables set; the etl service has them):
    docker compose run --rm etl python -m etl.synth_buyers
"""

from __future__ import annotations

import random
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
from argon2 import PasswordHasher
from pymongo import MongoClient
from pymongo.database import Database

from app.config import get_settings

SEED = 65
N_BUYERS = 200
MAX_SAVES = 10
DEMO_EMAIL = "demo@neighboar.local"
ADMIN_EMAIL = "admin@neighboar.local"
CREATED_BASE = datetime(2026, 10, 1, tzinfo=UTC)
SAVED_BASE = datetime(2026, 10, 15, tzinfo=UTC)
CREATED_SPREAD = 14 * 86_400  # whole seconds after CREATED_BASE
SAVED_SPREAD = 13 * 86_400  # whole seconds after SAVED_BASE
MIN_PASSWORD, MAX_PASSWORD = 10, 128

ADJECTIVES = [
    "Amber", "Brisk", "Calm", "Daring", "Eager", "Fair", "Gentle", "Hardy", "Ivory", "Jolly",
    "Keen", "Lively", "Mellow", "Noble", "Quiet", "Rapid", "Steady", "Tidy", "Vivid", "Witty",
]  # fmt: skip
NOUNS = [
    "Egret", "Heron", "Kingfisher", "Lark", "Myna", "Oriole", "Plover", "Robin", "Sparrow", "Tern",
    "Barbet", "Bulbul", "Coucal", "Drongo", "Falcon", "Kite", "Munia", "Pitta", "Shama", "Swift",
]  # fmt: skip
NOTES = [
    "Near the MRT",
    "Quiet corridor",
    "Check the lease start",
    "Good for the family",
    "Close to the market",
    "Ask about renovation",
    "High floor, nice view",
    "Compare with the next block",
    "Schools nearby",
    "Visit on the weekend",
    "Price looks high",
    "Parents live close by",
]


class SynthCollision(Exception):
    """A buyer id or email in a database clashes with the synthetic buyers."""


@dataclass(frozen=True)
class Save:
    building_id: int
    block: str
    street: str
    town: str
    note: str | None
    saved_at: datetime


@dataclass(frozen=True)
class Buyer:
    buyer_id: int
    email: str
    display_name: str
    is_admin: bool
    created_at: datetime
    password_hash: str
    saved: list[Save]


def synthetic_email(buyer_id: int) -> str:
    if buyer_id == 1:
        return DEMO_EMAIL
    if buyer_id == 2:
        return ADMIN_EMAIL
    return f"buyer{buyer_id:03d}@example.com"


EMAILS = {synthetic_email(i): i for i in range(1, N_BUYERS + 1)}


def check_demo_passwords(buyer_password: str, admin_password: str) -> None:
    """Raise ValueError (naming the variable, never the value) if a demo password is unusable."""
    for var, value in (("DEMO_BUYER_PASSWORD", buyer_password), ("DEMO_ADMIN_PASSWORD", admin_password)):
        if not value:
            raise ValueError(f"{var} is not set; add it to .env (see .env.example)")
        if value.startswith("change-me"):
            raise ValueError(f"{var} still holds the .env.example placeholder; choose your own password")
        if not MIN_PASSWORD <= len(value) <= MAX_PASSWORD:
            raise ValueError(f"{var} must be {MIN_PASSWORD} to {MAX_PASSWORD} characters")


def plan(building_rows: list[tuple[int, str, str, str]]) -> list[dict]:
    """Everything the generator draws, with no passwords: one dict per buyer in id order.

    `building_rows` are (building_id, block, street, town) sorted by building_id.
    """
    rng = random.Random(SEED)
    ids = [r[0] for r in building_rows]
    by_id = {r[0]: r for r in building_rows}
    out = []
    for buyer_id in range(1, N_BUYERS + 1):
        created = CREATED_BASE + timedelta(seconds=rng.randrange(CREATED_SPREAD))
        if buyer_id == 1:
            name, admin = "Demo Buyer", False
        elif buyer_id == 2:
            name, admin = "Demo Admin", True
        else:
            name, admin = f"{rng.choice(ADJECTIVES)} {rng.choice(NOUNS)}", False
        picked = sorted(rng.sample(ids, rng.randint(0, MAX_SAVES)))
        saves = []
        for building_id in picked:
            note = rng.choice(NOTES) if rng.random() < 0.5 else None
            saved_at = SAVED_BASE + timedelta(seconds=rng.randrange(SAVED_SPREAD))
            _, block, street, town = by_id[building_id]
            saves.append(Save(building_id, block, street, town, note, saved_at))
        out.append(
            {
                "buyer_id": buyer_id,
                "email": synthetic_email(buyer_id),
                "display_name": name,
                "is_admin": admin,
                "created_at": created,
                "saved": saves,
            }
        )
    return out


def build(building_rows: list[tuple[int, str, str, str]], buyer_password: str, admin_password: str) -> list[Buyer]:
    check_demo_passwords(buyer_password, admin_password)
    hasher = PasswordHasher()
    buyers = []
    for p in plan(building_rows):
        if p["buyer_id"] == 1:
            password = buyer_password
        elif p["buyer_id"] == 2:
            password = admin_password
        else:
            password = secrets.token_urlsafe(32)
        buyers.append(Buyer(password_hash=hasher.hash(password), **p))
    return buyers


# ---------------------------------------------------------------- reading and checking


def pg_buildings(conn: psycopg.Connection) -> list[tuple[int, str, str, str]]:
    rows = conn.execute(
        """
        SELECT b.building_id, b.block, b.street, tw.name
        FROM building b JOIN town tw ON tw.town_id = b.town_id
        ORDER BY b.building_id
        """
    ).fetchall()
    return [tuple(r) for r in rows]


def mongo_buildings(db: Database) -> list[tuple[int, str, str, str]]:
    docs = db.building_profiles.find({}, {"block": 1, "street": 1, "town.name": 1}).sort("_id", 1)
    return [(d["_id"], d["block"], d["street"], d["town"]["name"]) for d in docs]


def require_same_buildings(pg_rows: list, mongo_rows: list) -> None:
    pg_ids, mongo_ids = [r[0] for r in pg_rows], [r[0] for r in mongo_rows]
    if pg_ids != mongo_ids:
        only_pg, only_mongo = set(pg_ids) - set(mongo_ids), set(mongo_ids) - set(pg_ids)
        raise SystemExit(
            f"[synth_buyers] the databases hold different buildings ({len(pg_ids)} in PostgreSQL, "
            f"{len(mongo_ids)} in MongoDB; {len(only_pg)} only in PostgreSQL, {len(only_mongo)} only in MongoDB). "
            "Reload both with `python -m etl.pipeline` first."
        )


def find_collision(rows: list[tuple[int, str]], database: str) -> None:
    """Raise SynthCollision for the first (buyer_id, email) row that clashes with the synthetic buyers."""
    for buyer_id, email in sorted(rows):
        if 1 <= buyer_id <= N_BUYERS and email != synthetic_email(buyer_id):
            raise SynthCollision(
                f"{database}: buyer {buyer_id} has email {email!r}, "
                f"expected {synthetic_email(buyer_id)!r}; nothing was written"
            )
        owner = EMAILS.get(email.lower())
        if owner is not None and owner != buyer_id:
            raise SynthCollision(
                f"{database}: email {email!r} belongs to buyer {buyer_id}, "
                f"but the synthetic buyer with it has id {owner}; nothing was written"
            )


def pg_buyer_rows(conn: psycopg.Connection) -> list[tuple[int, str]]:
    return [
        (r[0], r[1])
        for r in conn.execute(
            "SELECT buyer_id, email FROM buyer WHERE buyer_id BETWEEN 1 AND %s OR lower(email) = ANY(%s)",
            (N_BUYERS, list(EMAILS)),
        ).fetchall()
    ]


def mongo_buyer_rows(db: Database) -> list[tuple[int, str]]:
    docs = db.buyers.find(
        {"$or": [{"_id": {"$gte": 1, "$lte": N_BUYERS}}, {"email": {"$in": list(EMAILS)}}]}, {"email": 1}
    )
    return [(d["_id"], d["email"]) for d in docs]


# ---------------------------------------------------------------- writing

_UPSERT = """
INSERT INTO buyer (buyer_id, email, password_hash, display_name, is_admin, created_at, last_active_at)
VALUES (%s, %s, %s, %s, %s, %s, NULL)
ON CONFLICT (buyer_id) DO UPDATE SET
    email = EXCLUDED.email,
    password_hash = EXCLUDED.password_hash,
    display_name = EXCLUDED.display_name,
    is_admin = EXCLUDED.is_admin,
    created_at = EXCLUDED.created_at,
    last_active_at = NULL
"""

_SETVAL = """
SELECT setval(
    pg_get_serial_sequence('buyer', 'buyer_id'),
    GREATEST((SELECT last_value FROM buyer_buyer_id_seq), (SELECT max(buyer_id) FROM buyer)),
    true
)
"""


def write_pg(conn: psycopg.Connection, buyers: list[Buyer]) -> None:
    ids = [b.buyer_id for b in buyers]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            _UPSERT, [(b.buyer_id, b.email, b.password_hash, b.display_name, b.is_admin, b.created_at) for b in buyers]
        )
        cur.execute("DELETE FROM saved_building WHERE buyer_id = ANY(%s)", (ids,))
        cur.executemany(
            "INSERT INTO saved_building (buyer_id, building_id, note, saved_at) VALUES (%s, %s, %s, %s)",
            [(b.buyer_id, s.building_id, s.note, s.saved_at) for b in buyers for s in b.saved],
        )
        cur.execute(_SETVAL)


def write_mongo(db: Database, buyers: list[Buyer]) -> None:
    for b in buyers:
        doc = {
            "_id": b.buyer_id,
            "email": b.email,
            "password_hash": b.password_hash,
            "display_name": b.display_name,
            "is_admin": b.is_admin,
            "created_at": b.created_at,
            "last_active_at": None,
            "saved": [
                {
                    "building_id": s.building_id,
                    "block": s.block,
                    "street": s.street,
                    "town": s.town,
                    "note": s.note,
                    "saved_at": s.saved_at,
                }
                for s in b.saved
            ],
        }
        db.buyers.replace_one({"_id": b.buyer_id}, doc, upsert=True)
    top = next(iter(db.buyers.find({}, {"_id": 1}).sort("_id", -1).limit(1)))["_id"]
    # $max never lowers the counter, even when registrations have moved it past the highest id.
    db.counters.update_one({"_id": "buyer"}, {"$max": {"seq": top}}, upsert=True)


def synthesise(
    pg: psycopg.Connection, db: Database, buyer_password: str, admin_password: str
) -> tuple[int, int]:
    """Check both databases, then write the same buyers to both. Returns (buyers, saves)."""
    check_demo_passwords(buyer_password, admin_password)
    pg_rows, mongo_rows = pg_buildings(pg), mongo_buildings(db)
    require_same_buildings(pg_rows, mongo_rows)
    find_collision(pg_buyer_rows(pg), "PostgreSQL")
    find_collision(mongo_buyer_rows(db), "MongoDB")
    buyers = build(pg_rows, buyer_password, admin_password)
    write_pg(pg, buyers)
    write_mongo(db, buyers)
    return len(buyers), sum(len(b.saved) for b in buyers)


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    try:
        check_demo_passwords(s.demo_buyer_password, s.demo_admin_password)
    except ValueError as exc:
        print(f"[synth_buyers] {exc}", file=sys.stderr)
        return 1
    if not s.pg_owner_password or not s.mongo_etl_password:
        print("[synth_buyers] PG_OWNER_PASSWORD and MONGO_ETL_PASSWORD must be set; check .env", file=sys.stderr)
        return 1
    client = MongoClient(s.mongo_uri("etl", direct=True), serverSelectionTimeoutMS=10000, tz_aware=True)
    try:
        with psycopg.connect(s.pg_dsn("neighboar_owner"), autocommit=True) as pg:
            n_buyers, n_saves = synthesise(pg, client[s.mongo_db], s.demo_buyer_password, s.demo_admin_password)
    except SynthCollision as exc:
        print(f"[synth_buyers] {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()
    print(f"[synth_buyers] wrote {n_buyers} buyers and {n_saves} saved blocks to both databases", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
