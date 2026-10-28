"""The integrity matrix (docs/integrity_matrix.md) covers every write rule in the live catalogues (task 3.5a).

The rules come from a freshly read catalogue of each database (tests/negative/rules.py), not from the SQL or JSON
files, so a rule added to either database without a matrix row fails here. This test needs only the schema, so it
passes on a freshly migrated database as well as on the loaded sample.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.negative.rules import mongo_rules, pg_rules

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs" / "integrity_matrix.md"
TICKS = re.compile(r"`([^`]+)`")


def parse_matrix(text: str) -> list[dict]:
    """Rows of the table: description, the pg: and mongo: keys, the whole cells, and the test node ids."""
    rows = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 4 or cells[0] == "Rule" or set(cells[0]) <= {"-", " ", ":"}:
            continue
        ticks = [TICKS.findall(cell) for cell in cells]
        rows.append(
            {
                "rule": cells[0],
                "pg_cell": cells[1],
                "mongo_cell": cells[2],
                "pg": [t for t in ticks[1] if t.startswith("pg:")],
                "mongo": [t for t in ticks[2] if t.startswith("mongo:")],
                "nodes": [t for t in ticks[3] if "::" in t],
            }
        )
    return rows


def collected_node_ids() -> set[str]:
    """Every test node id under tests/, from a separate `pytest --collect-only`, so `-k` or `-m` here do not matter."""
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-o", "addopts=", "tests/"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    return {line.strip() for line in done.stdout.splitlines() if "::" in line}


@pytest.fixture(scope="module")
def live_rules(settings):
    import psycopg
    from pymongo import MongoClient

    if not settings.mongo_etl_password:
        pytest.skip("MONGO_ETL_PASSWORD is not set; pass it to the test container")
    with psycopg.connect(settings.pg_dsn("app_rw")) as conn:
        found = set(pg_rules(conn))
    client = MongoClient(settings.mongo_uri("etl", direct=True), serverSelectionTimeoutMS=5000)
    try:
        found |= set(mongo_rules(client[settings.mongo_db]))
    finally:
        client.close()
    return found


@pytest.fixture(scope="module")
def rows():
    return parse_matrix(MATRIX.read_text(encoding="utf-8"))


def test_every_live_rule_has_exactly_one_row_and_no_row_names_a_missing_rule(live_rules, rows):
    listed = [key for row in rows for key in (*row["pg"], *row["mongo"])]
    duplicated = sorted({key for key in listed if listed.count(key) > 1})
    assert not duplicated, f"rules in more than one row: {duplicated}"
    assert not sorted(live_rules - set(listed)), f"rules with no row: {sorted(live_rules - set(listed))}"
    assert not sorted(set(listed) - live_rules), (
        f"rows for rules that no longer exist: {sorted(set(listed) - live_rules)}"
    )


def test_every_row_says_why_a_side_has_no_rule(rows):
    for row in rows:
        assert row["nodes"], f"no test node id: {row['rule']}"
        for side, keys in (("pg_cell", row["pg"]), ("mongo_cell", row["mongo"])):
            if not keys:
                assert re.match(r"(app layer|none): \S", row[side], re.I), f"{row['rule']}: {side} needs a reason"


def test_every_listed_node_id_exists_and_every_rule_maps_to_its_own_case(rows):
    nodes = collected_node_ids()
    for row in rows:
        missing = [n for n in row["nodes"] if n not in nodes]
        assert not missing, f"{row['rule']}: no such test: {missing}"
        for key in (*row["pg"], *row["mongo"]):
            own = [n for n in row["nodes"] if f"[{key}]" in n or f"[{key}#" in n]
            assert own, f"{row['rule']}: no case with the id {key}"


def test_the_matrix_uses_no_em_dashes():
    assert "—" not in MATRIX.read_text(encoding="utf-8")
