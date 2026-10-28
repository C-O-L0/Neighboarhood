"""cases.yaml, the typed loader, the capture helpers and the fraction rule of the benchmark harness (task 4.1a).

These need no database; the integration tests in tests/integration/test_bench.py run the harness for real.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from bson.decimal128 import Decimal128

from bench import capture
from bench.cases import PARAM_SETS, load_cases, to_mongo, typed_arg
from bench.run import FRACTION_TOLERANCE, Refusal, check_fraction, import_report_sales, pg_index_names
from tests.equivalence.cases import CASES, Case

# ------------------------------------------------------------------------------------------------ cases.yaml


def test_every_case_has_exactly_three_parameter_sets():
    for case in load_cases():
        assert len(case.params) == PARAM_SETS, case.id
        assert len({p.label for p in case.params}) == len(case.params)
    rebuild = next(c for c in load_cases() if c.id == "stats_rebuild")
    assert [p.label for p in rebuild.params] == ["rep-1", "rep-2", "rep-3"]
    assert (rebuild.runs, rebuild.warmup) == (50, 5)


def test_the_cases_of_the_spec_are_all_there():
    ids = {c.id for c in load_cases()}
    assert ids == {
        "search_filtered", "search_type_month", "search_price_sort", "search_unfiltered", "trends_series",
        "rankings_year", "building_page", "building_recent_lookup", "town_stats_read", "food_town",
        "median_exact", "median_approx", "stats_rebuild", "admin_record_delete",
    }  # fmt: skip


def test_write_cases_are_flagged_and_the_defaults_are_50_and_5():
    cases = {c.id: c for c in load_cases()}
    assert {i for i, c in cases.items() if c.writes} == {"stats_rebuild", "admin_record_delete"}
    assert (cases["search_filtered"].runs, cases["search_filtered"].warmup) == (50, 5)
    assert (cases["admin_record_delete"].runs, cases["admin_record_delete"].warmup) == (50, 5)


def test_cases_that_run_on_one_database_only_are_the_bench_only_extras():
    one_sided = {c.id for c in load_cases() if c.pg is None or c.mongo is None}
    assert one_sided == {"building_recent_lookup", "median_approx"}


def test_pairs_follow_the_spec():
    pairs = {c.id: c.pair for c in load_cases()}
    assert pairs["building_page"] == "embedded_vs_join"
    assert pairs["building_recent_lookup"] == "embedded_vs_lookup"
    assert pairs["town_stats_read"] == pairs["stats_rebuild"] == "precomputed"
    assert pairs["median_exact"] == pairs["median_approx"] == "median"
    assert pairs["food_town"] == "geo"
    notes = {c.id: c.note for c in load_cases()}
    assert "not result-equivalent" in notes["town_stats_read"] and "not result-equivalent" in notes["stats_rebuild"]


def test_bench_only_sql_is_bound_never_string_built():
    for case in load_cases():
        for side in (case.pg, case.mongo):
            if side is not None and "sql" in side.spec:
                text = side.spec["sql"]
                assert "%(" in text and "{" not in text and "'" not in text, case.id
                assert set(side.spec["bind"]) <= set(case.params[0].args)


def test_prepared_statements_hold_typed_values():
    cases = {c.id: c for c in load_cases()}
    exact = cases["median_exact"]
    args = exact.params[0].args
    assert args["start"] == date(2022, 1, 1)
    pipeline = exact.mongo.spec["pipeline"]
    typed = to_mongo(pipeline, args)
    bounds = typed[0]["$match"]["txn_month"]
    assert bounds["$gte"] == datetime(2022, 1, 1, tzinfo=UTC) and bounds["$lt"] == datetime(2023, 1, 1, tzinfo=UTC)
    assert typed[0]["$match"]["flat_type"] == "4 ROOM"


# --------------------------------------------------------------------------------------------- typed values


def test_decimals_and_dates_are_typed_for_mongodb():
    assert typed_arg({"$decimal": "650000.00"}) == Decimal("650000.00")
    out = to_mongo({"price": {"$param": "p"}, "when": {"$param": "d"}, "lit": {"$decimal": "1.50"}}, {
        "p": Decimal("12.50"), "d": date(2024, 6, 1),
    })  # fmt: skip
    assert out["price"] == Decimal128("12.50")
    assert out["when"] == datetime(2024, 6, 1, tzinfo=UTC) and out["when"].tzinfo is UTC
    assert out["lit"] == Decimal128("1.50")


def test_a_param_marker_for_an_unknown_name_fails_loudly():
    with pytest.raises(KeyError):
        to_mongo({"x": {"$param": "missing"}}, {})


# ----------------------------------------------------------------------------------------------- registry


def test_the_equivalence_registry_has_a_writes_flag_that_defaults_to_false():
    assert Case.__dataclass_fields__["writes"].default is False
    assert all(c.writes is False for c in CASES)


# ------------------------------------------------------------------------------------------- fraction rule


def test_the_import_report_says_241357_sales():
    assert import_report_sales() == 241_357


def test_fraction_within_the_tolerance_passes():
    counts = {"snapshot_sale": {"pg": 241_357}}
    assert check_fraction(counts, 1.0) == 1.0
    counts = {"snapshot_sale": {"pg": 6_275}}
    assert check_fraction(counts, 0.026) == pytest.approx(0.026, abs=FRACTION_TOLERANCE)


def test_fraction_outside_the_tolerance_is_refused():
    with pytest.raises(Refusal, match="--expect-fraction"):
        check_fraction({"snapshot_sale": {"pg": 6_275}}, 1.0)
    with pytest.raises(Refusal):
        check_fraction({"snapshot_sale": {"pg": 241_357 // 4}}, 0.5)
    assert check_fraction({"snapshot_sale": {"pg": 241_357 // 4}}, 0.25) == pytest.approx(0.25, abs=0.001)


def test_the_postgresql_index_names_are_read_from_the_migration():
    names = pg_index_names()
    assert "resale_transaction_type_month_idx" in names and len(names) == 5


# --------------------------------------------------------------------------------------------- capture


class FakeCursor:
    def fetchall(self):
        return []


class FakeConn:
    def __init__(self):
        self.seen = []

    def execute(self, query, params=None, **kwargs):
        self.seen.append((query, params))
        return FakeCursor()

    def transaction(self):
        return "transaction"

    def as_string(self):  # pragma: no cover - not used
        return ""


def test_recording_connection_records_every_statement_with_its_params():
    conn = FakeConn()
    rec = capture.RecordingConnection(conn)
    rec.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    rec.execute("SELECT count(*) FROM t WHERE a = %s", [5])
    rec.execute("\nWITH x AS (SELECT 1) SELECT * FROM x WHERE b = %(b)s", {"b": 7})
    assert [s.select for s in rec.statements] == [False, True, True]
    assert rec.statements[1].params == [5] and rec.statements[2].params == {"b": 7}
    assert len(conn.seen) == 3  # all were passed on
    assert rec.transaction() == "transaction"


def test_recording_connection_refuses_cursors_so_nothing_slips_past():
    with pytest.raises(NotImplementedError):
        capture.RecordingConnection(FakeConn()).cursor()


def test_pg_server_time_is_planning_plus_execution_summed_over_the_explained_statements():
    a = capture.PgStatement(
        "SELECT 1", "SELECT 1", None, True, explained=True, plan={"Planning Time": 0.5, "Execution Time": 1.5}
    )
    b = capture.PgStatement(
        "SELECT 2", "SELECT 2", None, True, explained=True, plan={"Planning Time": 0.25, "Execution Time": 2.0}
    )
    c = capture.PgStatement("SET TRANSACTION READ ONLY", "SET TRANSACTION READ ONLY", None, False)
    assert capture.pg_server_time([c, a, b]) == {"server_ms": 4.25, "planning_ms": 0.75, "execution_ms": 3.5}
    assert capture.pg_server_time([c]) == {}


def test_mongo_server_time_prefers_the_top_level_execution_time():
    explain = {
        "executionStats": {"executionTimeMillis": 12},
        "stages": [{"$group": {}, "executionTimeMillisEstimate": 99}],
    }
    assert capture.mongo_call_time_ms(explain) == 12.0


def test_mongo_server_time_for_a_pipeline_is_the_largest_stage_estimate():
    explain = {"stages": [
        {"$cursor": {}, "executionTimeMillisEstimate": 3},
        {"$group": {}, "executionTimeMillisEstimate": 8},
        {"$sort": {}, "executionTimeMillisEstimate": 7},
    ]}  # fmt: skip
    assert capture.mongo_call_time_ms(explain) == 8.0
    assert capture.mongo_server_time([explain, {"executionStats": {"executionTimeMillis": 2}}]) == {
        "server_ms": 10.0, "planning_ms": None, "execution_ms": 10.0,
    }  # fmt: skip
    assert capture.mongo_call_time_ms({}) is None


class FakeCollection:
    name = "resale_transactions"

    def find(self, filter=None, projection=None, **kwargs):
        return FakeMongoCursor()

    def count_documents(self, filter, **kwargs):
        return 3

    def aggregate(self, pipeline, **kwargs):
        return iter([{"n": 1}])


class FakeMongoCursor:
    def sort(self, key_or_list, direction=None):
        return self

    def skip(self, n):
        return self

    def limit(self, n):
        return self

    def __iter__(self):
        return iter([{"_id": 1}])


class FakeDb:
    def __getitem__(self, name):
        return FakeCollection()


def test_recording_database_records_find_with_its_sort_skip_and_limit():
    proxy = capture.RecordingDatabase(FakeDb())
    coll = proxy.resale_transactions
    assert coll.count_documents({"town": "BISHAN"}) == 3
    docs = list(coll.find({"town": "BISHAN"}).sort([("txn_month", -1), ("_id", -1)]).skip(50).limit(50))
    assert docs == [{"_id": 1}]
    list(coll.aggregate([{"$match": {"a": 1}}], allowDiskUse=True))
    kinds = [c["kind"] for c in proxy.calls]
    assert kinds == ["count_documents", "find", "aggregate"]
    find = proxy.calls[1]
    assert find["sort"] == [["txn_month", -1], ["_id", -1]] and find["skip"] == 50 and find["limit"] == 50


def test_recording_collection_refuses_calls_it_does_not_record():
    with pytest.raises(AttributeError):
        capture.RecordingDatabase(FakeDb()).resale_transactions.delete_one({})


def test_explain_commands_carry_the_recorded_call():
    find = {"kind": "find", "collection": "c", "filter": {"a": 1}, "projection": None,
            "sort": [["x", -1], ["_id", -1]], "skip": 5, "limit": 50}  # fmt: skip
    assert capture.explain_command(find) == {
        "find": "c", "filter": {"a": 1}, "sort": {"x": -1, "_id": -1}, "skip": 5, "limit": 50,
    }  # fmt: skip
    count = capture.explain_command({"kind": "count_documents", "collection": "c", "filter": {"a": 1}})
    assert count["pipeline"] == [{"$match": {"a": 1}}, {"$group": {"_id": 1, "n": {"$sum": 1}}}]
    agg = capture.explain_command({"kind": "aggregate", "collection": "c", "pipeline": [{"$limit": 1}],
                                   "options": {"allowDiskUse": True}})  # fmt: skip
    assert agg == {"aggregate": "c", "pipeline": [{"$limit": 1}], "cursor": {}, "allowDiskUse": True}
