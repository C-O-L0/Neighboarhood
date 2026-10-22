"""The hand-drawn ERD must list exactly the tables and columns of the live schema."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ERD = Path(__file__).resolve().parents[2] / "docs" / "diagrams" / "erd.dot"
NODE = re.compile(r"^  (\w+) \[label=<(.*?)>\];", re.S | re.M)
COLUMN = re.compile(r'<TD PORT="(\w+)"')


def erd_columns() -> dict[str, list[str]]:
    return {name: COLUMN.findall(body) for name, body in NODE.findall(ERD.read_text(encoding="utf-8"))}


def test_erd_matches_catalogue(pg_rw):
    rows = pg_rw.execute(
        """SELECT c.table_name, c.column_name
           FROM information_schema.columns c
           JOIN information_schema.tables t USING (table_schema, table_name)
           WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
           ORDER BY c.table_name, c.ordinal_position"""
    ).fetchall()
    live: dict[str, list[str]] = {}
    for table, column in rows:
        live.setdefault(table, []).append(column)
    live.pop("schema_migration", None)  # internal migration log, not part of the model

    drawn = erd_columns()
    assert sorted(drawn) == sorted(live), "tables differ between docs/diagrams/erd.dot and the database"
    for table, cols in live.items():
        assert drawn[table] == cols, f"{table}: ERD columns {drawn[table]} != database {cols}"
