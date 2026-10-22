"""copy_table in etl/load_pg.py, with a fake cursor (no database needed)."""

from contextlib import contextmanager

import pyarrow as pa
import pyarrow.parquet as pq

from etl.load_pg import EXTRA_COLUMNS, copy_table


class FakeCursor:
    def __init__(self):
        self.stmt = None
        self.rows = []

    @contextmanager
    def copy(self, stmt):
        self.stmt = stmt
        yield self

    def write_row(self, row):
        self.rows.append(tuple(row))


def write(tmp_path):
    path = tmp_path / "t.parquet"
    pq.write_table(pa.table({"txn_id": [1, 2], "resale_price": [10, 20]}), path)
    return path


def test_copy_without_extra_columns(tmp_path):
    cur = FakeCursor()
    assert copy_table(cur, "t", write(tmp_path)) == 2
    assert cur.stmt.as_string(None) == 'COPY "t" ("txn_id", "resale_price") FROM STDIN'
    assert cur.rows == [(1, 10), (2, 20)]


def test_copy_appends_constant_columns(tmp_path):
    cur = FakeCursor()
    assert copy_table(cur, "t", write(tmp_path), {"source": "snapshot"}) == 2
    assert cur.stmt.as_string(None) == 'COPY "t" ("txn_id", "resale_price", "source") FROM STDIN'
    assert cur.rows == [(1, 10, "snapshot"), (2, 20, "snapshot")]


def test_sales_are_loaded_as_snapshot():
    assert EXTRA_COLUMNS == {"resale_transaction": {"source": "snapshot"}}
