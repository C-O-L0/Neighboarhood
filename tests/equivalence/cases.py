"""The single registry of PostgreSQL / MongoDB comparison cases.

Each feature registers a Case. `pg_call(conn, params)` and `mongo_call(db, params)` return DTOs;
`params(conn)` returns the parameter dicts to run, given a PostgreSQL connection so it can look
at the loaded data. The pytest files here iterate over CASES, and Phase 4's bench/verify.py will
iterate over the same registry on the full data.

Sampling rule: with at most SAMPLE_MAX buildings loaded (the sample), every case runs its full
parameter list; otherwise each feature returns the fixed full-data list it declares.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.repos.mongo.building import MongoBuildingRepo
from app.repos.pg.building import PgBuildingRepo

SAMPLE_MAX = 1000
FULL_DATA_STEP = 25  # building cases: every 25th building id in id order, plus the named blocks


@dataclass(frozen=True)
class Case:
    name: str
    pg_call: Callable[[Any, dict], Any]
    mongo_call: Callable[[Any, dict], Any]
    params: Callable[[Any], list[dict]]


def is_sample(conn) -> bool:
    return conn.execute("SELECT count(*) FROM building").fetchone()[0] <= SAMPLE_MAX


def building_params(conn) -> list[dict]:
    ids = [r[0] for r in conn.execute("SELECT building_id FROM building ORDER BY building_id").fetchall()]
    if len(ids) > SAMPLE_MAX:
        named = conn.execute(
            """
            SELECT building_id FROM building WHERE NOT in_property_dataset
            UNION
            SELECT building_id FROM building WHERE block = '37' AND street = 'TEBAN GDNS RD'
            UNION
            (SELECT building_id FROM building b
             WHERE in_property_dataset
               AND NOT EXISTS (SELECT 1 FROM building_flat_supply s WHERE s.building_id = b.building_id)
             ORDER BY building_id LIMIT 1)
            """
        ).fetchall()
        ids = sorted({*ids[::FULL_DATA_STEP], *(r[0] for r in named)})
    return [{"building_id": i} for i in ids]


CASES: list[Case] = [
    Case(
        name="building",
        pg_call=lambda conn, p: PgBuildingRepo(conn).get(p["building_id"]),
        mongo_call=lambda db, p: MongoBuildingRepo(db).get(p["building_id"]),
        params=building_params,
    ),
]
