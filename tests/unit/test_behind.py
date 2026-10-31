"""Behind the scenes without a database: the capture store, the recorders and the benchmark summary."""

from datetime import UTC, datetime
from decimal import Decimal

from bson.decimal128 import Decimal128

from app.services.behind import (
    Call,
    Captures,
    MongoRecorder,
    PgRecorder,
    latest_benchmark,
    show_mongo,
    summary_mongo,
    summary_pg,
)


def test_captures_keep_one_page_per_session_and_drop_the_oldest():
    store = Captures(size=2)
    first = store.new_id()
    store.add("a", first, "/x", Call("one", "pg", 1.0))
    store.add("a", first, "/x", Call("two", "pg", 1.0))
    store.add("a", store.new_id(), "/y", Call("three", "pg", 1.0))  # a new request replaces the page
    assert store.last("a").path == "/y" and [c.label for c in store.last("a").calls] == ["three"]
    store.add("b", store.new_id(), "/b", Call("b", "mongo", 1.0))
    store.add("c", store.new_id(), "/c", Call("c", "mongo", 1.0))
    assert store.last("a") is None and store.last("b") and store.last("c")


class FakeConn:
    def __init__(self):
        self.ran = []

    def execute(self, query, params=None, **kwargs):
        self.ran.append((query, params))
        return "result"

    def transaction(self):
        return "tx"


def test_pg_recorder_records_then_runs_and_counts_its_own_time():
    conn = FakeConn()
    rec = PgRecorder(conn)
    assert rec.execute("SELECT 1 WHERE x = %s", [5]) == "result"
    assert rec.execute("UPDATE t SET a = 1", {"a": 1}) == "result"
    assert [(s.text, s.params, s.select) for s in rec.statements] == [
        ("SELECT 1 WHERE x = %s", [5], True),
        ("UPDATE t SET a = 1", {"a": 1}, False),
    ]
    assert conn.ran[0] == ("SELECT 1 WHERE x = %s", [5]) and rec.overhead_ns > 0
    assert rec.transaction() == "tx"  # anything else passes through


class FakeColl:
    name = "things"

    def find_one(self, *a, **k):
        return {"_id": 1}

    def aggregate(self, pipeline, **k):
        return iter([])

    def update_one(self, *a, **k):
        return "updated"


class FakeDb:
    client = "the client"

    def __getitem__(self, name):
        return FakeColl()


def test_mongo_recorder_records_reads_and_writes_and_passes_the_client_through():
    rec = MongoRecorder(FakeDb())
    assert rec.client == "the client"  # transactions need the real client
    rec.things.find_one({"_id": 1}, {"a": 1})
    rec.things.aggregate([{"$match": {}}], session=object())
    assert rec.things.update_one({"_id": 1}, {"$set": {"a": 2}}) == "updated"
    kinds = [c["kind"] for c in rec.calls]
    assert kinds == ["find", "aggregate", "update_one"]
    assert rec.calls[1]["options"] == {}  # the session is not kept
    assert summary_mongo(rec.calls[1]) == "aggregate on things, 1 stage"


def test_shown_text_handles_decimals_dates_and_long_sql():
    call = {"kind": "find", "collection": "c", "filter": {"p": Decimal128(Decimal("1.5")),
            "t": datetime(2026, 1, 1, tzinfo=UTC)}}  # fmt: skip
    shown = show_mongo(call)
    assert shown.startswith("db.c.find(") and '"$numberDecimal": "1.5"' in shown and "2026-01-01" in shown
    assert summary_pg("SELECT a\nFROM b\nWHERE c") == "SELECT a FROM b WHERE c (3 lines)"
    assert summary_pg("SELECT " + "x, " * 40).endswith("... (1 line)")


def test_latest_benchmark_takes_the_newest_run_and_its_chart_values(tmp_path):
    header = (
        "invocation,case,pair,db,param_set,part,n,rows,median_ms,p95_ms,min_ms,max_ms,server_ms,planning_ms,"
        "execution_ms\n"
    )
    rows = [
        "r,search,live,pg,a,call,50,1,2.0,1,1,1,1,1,1",
        "r,search,live,pg,b,call,50,1,4.0,1,1,1,1,1,1",
        "r,search,live,pg,c,call,50,1,3.0,1,1,1,1,1,1",
        "r,search,live,mongo,a,call,50,1,1.0,1,1,1,1,1,1",
        "r,search,live,mongo,b,refresh,50,1,99.0,1,1,1,1,1,1",
        "r,lookup,embedded,mongo,a,call,50,1,0.5,1,1,1,1,1,1",
    ]
    for name in ("2026-10-old", "2026-11-new"):
        (tmp_path / name / "f100").mkdir(parents=True)
        (tmp_path / name / "f100" / "summary.csv").write_text(header + "\n".join(rows) + "\n")
    (tmp_path / "2026-11-new" / "scaling.svg").write_text("<svg/>")
    bench = latest_benchmark(tmp_path)
    assert bench.name == "2026-11-new" and bench.charts == [("scaling", "Latency as the data grows")]
    by_case = {c.case: c for c in bench.cases}
    assert (by_case["search"].pg_ms, by_case["search"].mongo_ms) == (3.0, 1.0)  # refresh rows are not calls
    assert by_case["search"].faster == "MongoDB" and by_case["lookup"].faster is None
    assert latest_benchmark(tmp_path / "missing") is None
