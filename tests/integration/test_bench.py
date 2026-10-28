"""The benchmark harness against the running stack (task 4.1a).

The harness runs on whatever is loaded (the sample in CI, the full data at a gate): --runs 3 --warmup 1, with
--expect-fraction taken from the loaded data. Every run
writes into a temporary results root (the hidden --results-root option), so nothing lands in bench/results. The
tests need the passwords of the benchmark identities (PG_APP_RO_PASSWORD, PG_APP_ADMIN_PASSWORD, MONGO_APP_PASSWORD,
MONGO_APP_ADMIN_PASSWORD, MONGO_ETL_PASSWORD) and skip without them; the CI test step and the README command already
pass PG_APP_RO_PASSWORD and MONGO_ETL_PASSWORD, and the web service supplies the rest.
"""

import csv
import json
import shutil
from types import SimpleNamespace

import pytest

from app.repos.mongo.building import MongoBuildingRepo
from app.repos.pg.building import PgBuildingRepo
from bench import capture, connect, verify
from bench import run as bench_run
from bench.cases import load_cases
from bench.stats import SUMMARY_COLUMNS, THROUGHPUT_COLUMNS
from etl import db_counts
from tests.equivalence.cases import CASES, Case

pytestmark = pytest.mark.integration

READ_CASES = [c for c in load_cases() if not c.writes]
WRITE_CASES = [c for c in load_cases() if c.writes]


@pytest.fixture(scope="session")
def bench_settings():
    s = connect.settings()
    needed = {
        "PG_APP_RO_PASSWORD": s.pg_app_ro_password,
        "PG_APP_ADMIN_PASSWORD": s.pg_app_admin_password,
        "MONGO_APP_PASSWORD": s.mongo_app_password,
        "MONGO_APP_ADMIN_PASSWORD": s.mongo_app_admin_password,
        "MONGO_ETL_PASSWORD": s.mongo_etl_password,
    }
    missing = [k for k, v in needed.items() if not v]
    if missing:
        pytest.skip(f"{', '.join(missing)} not set; pass them to the test container")
    return s


@pytest.fixture(scope="module")
def conns(bench_settings):
    client, mdb = connect.open_mongo("app", bench_settings)
    admin_client, admin_mdb = connect.open_mongo("app_admin", bench_settings)
    with (
        connect.open_pg("app_ro", bench_settings) as ro,
        connect.open_pg("app_ro", bench_settings) as ro2,
        connect.open_pg("app_admin", bench_settings) as admin,
    ):
        yield SimpleNamespace(ro=ro, ro2=ro2, admin=admin, mdb=mdb, admin_mdb=admin_mdb)
    client.close()
    admin_client.close()


@pytest.fixture(scope="module")
def fraction(conns):
    n = conns.ro.execute("SELECT count(*) FROM resale_transaction WHERE source = 'snapshot'").fetchone()[0]
    return f"{n / bench_run.import_report_sales():.4f}"


def run_bench(root, fraction, *args) -> int:
    return bench_run.main([*args, "--results-root", str(root), "--expect-fraction", fraction])


def admin_blocks() -> list[dict]:
    return [ps.args for c in WRITE_CASES if c.id == "admin_record_delete" for ps in c.params]


def state(conns) -> dict:
    """What a writes run must leave as it found it."""
    ids = [PgBuildingRepo(conns.ro).find_id(a["block"], a["street"]) for a in admin_blocks()]
    assert None not in ids
    return {
        "counts": db_counts.collect(conns.ro, conns.mdb),
        "pg_buildings": [PgBuildingRepo(conns.ro).get(i) for i in ids],
        "mongo_buildings": [MongoBuildingRepo(conns.mdb).get(i) for i in ids],
        "mv": conns.ro.execute(
            "SELECT town_id, flat_type_id, month, n, median_price, avg_psm FROM mv_town_month_stats"
            " ORDER BY town_id, flat_type_id, month"
        ).fetchall(),
        "txn_counter": conns.mdb.counters.find_one({"_id": "txn"}),
    }


def read_csv(path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_jsonl(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def sample(conns, fraction, tmp_path_factory):
    """A reads run and then a writes run into one label, with the state before and after."""
    root = tmp_path_factory.mktemp("results")
    before = state(conns)
    reads = run_bench(root, fraction, "--label", "sample", "--runs", "3", "--warmup", "1")
    writes = run_bench(root, fraction, "--label", "sample", "--include-writes", "--runs", "3", "--warmup", "1")
    after = state(conns)
    return SimpleNamespace(root=root, dir=root / "sample", reads=reads, writes=writes, before=before, after=after)


@pytest.fixture(scope="module")
def throughput(fraction, tmp_path_factory, bench_settings):
    root = tmp_path_factory.mktemp("throughput")
    rc = run_bench(
        root, fraction, "--label", "tp", "--throughput", "--throughput-seconds", "1", "--throughput-warmup", "0"
    )
    return SimpleNamespace(root=root, dir=root / "tp", rc=rc)


# ------------------------------------------------------------------------------------------ verify (criterion 1)


def probe_case(events: list, name: str, writes: bool) -> Case:
    def call(target, params):
        events.append(name)
        return 1

    return Case(name=name, pg_call=call, mongo_call=call, params=lambda conn: [{}], writes=writes)


def test_verify_settles_first_and_runs_the_read_registry_only(conns, monkeypatch):
    events: list = []
    real_settle = verify.settle

    def spy(admin_conn):
        events.append("settle")
        real_settle(admin_conn)

    monkeypatch.setattr(verify, "settle", spy)
    cases = [probe_case(events, "reader", False), probe_case(events, "writer", True)]
    result = verify.run_verify(conns.ro, conns.mdb, conns.admin, cases=cases, log=lambda m: None)
    assert result.ok, result.failures
    assert events[0] == "settle" and events.count("settle") == 1
    assert "writer" not in events, "a case with writes=True was called"
    assert events.count("reader") == 2  # one call on each side
    assert result.checked == 1 + 3 + 3  # the probe, then the 3 median_exact and 3 building_recent_lookup checks


def test_the_real_registry_has_no_write_cases_and_verify_passes_on_it(conns):
    assert all(c.writes is False for c in CASES)
    result = verify.run_verify(conns.ro, conns.mdb, conns.admin, log=lambda m: None)
    assert result.ok, result.failures[:3]


def test_a_forced_mismatch_fails_verify(conns):
    result = verify.run_verify(conns.ro, conns.mdb, conns.admin, force_mismatch=True, log=lambda m: None)
    assert not result.ok and len(result.failures) == 1


def test_verify_main_exits_non_zero_on_a_forced_mismatch(bench_settings):
    assert verify.main(["--force-mismatch"]) == 1
    assert verify.main([]) == 0


def test_bench_run_refuses_to_time_when_verify_fails(fraction, tmp_path, bench_settings):
    rc = run_bench(tmp_path, fraction, "--label", "forced", "--runs", "1", "--warmup", "0", "--force-mismatch")
    assert rc == 1
    assert not (tmp_path / "forced").exists(), "nothing may be written when verification fails"


def test_bench_only_check_catches_a_median_difference(conns):
    cases = load_cases()
    verify.resolve_building_ids(cases, conns.ro)
    exact = next(c for c in cases if c.id == "median_exact")
    result = verify.VerifyResult()
    pg_side = exact.pg

    class Off:
        def prepare(self, args):
            inner = pg_side.prepare(args)
            return lambda conn: None if inner(conn) is None else inner(conn) + 1

    object.__setattr__(exact, "pg", Off())
    verify.verify_bench_only(conns.ro, conns.mdb, cases, result)
    assert any(f.startswith("median_exact") for f in result.failures)


# ------------------------------------------------------------------------------------- the sample run (criterion 2)


def test_reads_and_writes_runs_complete(sample):
    assert sample.reads == 0 and sample.writes == 0


def test_summary_has_the_documented_columns_and_every_read_case(sample):
    path = sample.dir / "summary.csv"
    assert path.read_text(encoding="utf-8").splitlines()[0] == ",".join(SUMMARY_COLUMNS)
    rows = read_csv(path)
    for case in READ_CASES:
        for db, side in (("pg", case.pg), ("mongo", case.mongo)):
            got = [r for r in rows if r["case"] == case.id and r["db"] == db]
            if side is None:
                assert not got, f"{case.id} has no {db} side"
                continue
            assert {r["param_set"] for r in got} == {p.label for p in case.params}, (case.id, db)
            for r in got:
                assert r["n"] == "3" and r["part"] == "call" and r["pair"] == case.pair
                assert float(r["min_ms"]) <= float(r["median_ms"]) <= float(r["p95_ms"]) <= float(r["max_ms"])
                assert r["rows"] != "" and int(r["rows"]) >= 0
                assert r["server_ms"] != "", f"{case.id} {db} {r['param_set']} has no server time"
                if db == "pg":
                    assert float(r["server_ms"]) == pytest.approx(
                        float(r["planning_ms"]) + float(r["execution_ms"]), abs=1e-3
                    )


def test_summary_records_the_grain_of_the_precomputed_pair(sample):
    rows = {
        (r["db"], r["param_set"]): r for r in read_csv(sample.dir / "summary.csv") if r["case"] == "town_stats_read"
    }
    assert int(rows["pg", "bishan-4room"]["rows"]) > 0 and int(rows["mongo", "bishan-4room"]["rows"]) > 0
    assert rows["pg", "bishan-4room"]["rows"] != rows["mongo", "bishan-4room"]["rows"]  # months against blocks


def test_raw_jsonl_holds_every_timed_call(sample):
    raw = read_jsonl(sample.dir / "raw.jsonl")
    summary = read_csv(sample.dir / "summary.csv")
    assert len(raw) == sum(int(r["n"]) for r in summary)
    keys = {"invocation", "case", "db", "param_set", "part", "run", "ms", "rows", "conn"}
    assert all(set(r) == keys and r["ms"] > 0 for r in raw)


def test_a_reads_run_then_a_writes_run_keep_both_sets_of_rows(sample):
    env = json.loads((sample.dir / "env.json").read_text(encoding="utf-8"))
    assert [i["kind"] for i in env["invocations"]] == ["reads", "writes"]
    reads_id, writes_id = (i["id"] for i in env["invocations"])
    assert reads_id != writes_id and all(i["utc_end"] for i in env["invocations"])
    summary = read_csv(sample.dir / "summary.csv")
    by_inv = {inv: {r["case"] for r in summary if r["invocation"] == inv} for inv in (reads_id, writes_id)}
    assert by_inv[reads_id] == {c.id for c in READ_CASES}
    assert by_inv[writes_id] == {c.id for c in WRITE_CASES}
    assert {r["invocation"] for r in read_jsonl(sample.dir / "raw.jsonl")} == {reads_id, writes_id}
    assert summary[0]["invocation"] == reads_id  # the reads rows come first, still there after the writes run


def test_write_cases_time_the_write_and_the_refresh_separately(sample):
    rows = [r for r in read_csv(sample.dir / "summary.csv") if r["case"] == "admin_record_delete"]
    parts = {(r["db"], r["part"]) for r in rows}
    assert parts == {("pg", "write"), ("pg", "refresh"), ("mongo", "write")}
    assert all(r["n"] == "3" and r["server_ms"] == "" for r in rows), "write cases have client timing only"
    assert {r["db"] for r in read_csv(sample.dir / "summary.csv") if r["case"] == "stats_rebuild"} == {"pg", "mongo"}


def test_a_writes_run_leaves_counts_buildings_views_and_the_counter_unchanged(sample):
    assert sample.after["counts"] == sample.before["counts"]
    assert sample.after["pg_buildings"] == sample.before["pg_buildings"]
    assert sample.after["mongo_buildings"] == sample.before["mongo_buildings"]
    assert sample.after["mv"] == sample.before["mv"]
    assert sample.after["txn_counter"] == sample.before["txn_counter"]


def test_env_json_records_the_environment(sample, conns):
    env = json.loads((sample.dir / "env.json").read_text(encoding="utf-8"))
    assert env["sale_counts"]["pg"] == env["sale_counts"]["mongo"] > 0
    assert 0 < env["fraction"] <= 1
    assert env["connections"]["postgresql"]["autocommit"] is True
    for entry in env["invocations"]:
        assert entry["verify"]["ok"] is True and entry["verify"]["checks"] > 0
    assert env["connections"]["mongodb"]["tz_aware"] is True
    assert env["cpu_count"] and env["utc_start"] <= env["utc_end"]
    assert env["postgresql"]["version"] and env["mongodb"]["version"]
    readme = (sample.dir / "README.txt").read_text(encoding="utf-8")
    assert readme.startswith("Server time per call:")
    assert "Warm cache only" in readme and "executionTimeMillis" in readme and "Planning Time" in readme
    assert "not symmetric" in readme


# ----------------------------------------------------------------------------------------- throughput (criterion 2)


def test_throughput_writes_its_csv(throughput):
    assert throughput.rc == 0
    path = throughput.dir / "throughput.csv"
    assert path.read_text(encoding="utf-8").splitlines()[0] == ",".join(THROUGHPUT_COLUMNS)
    rows = read_csv(path)
    assert len(rows) == 2 * 2 * 3
    assert {(r["case"], r["db"], r["workers"]) for r in rows} == {
        (c, d, w) for c in ("search_filtered", "building_page") for d in ("pg", "mongo") for w in ("1", "4", "8")
    }
    assert all(int(r["calls"]) > 0 and float(r["calls_per_s"]) > 0 and float(r["median_ms"]) > 0 for r in rows)
    env = json.loads((throughput.dir / "env.json").read_text(encoding="utf-8"))
    assert [i["kind"] for i in env["invocations"]] == ["throughput"]
    assert env["invocations"][0]["test_hooks"] == ["short_throughput"], "a hooked run must say so"


# ----------------------------------------------------------------------------------------- passwords (criterion 3)


def test_no_password_appears_in_any_result_file(sample, throughput, bench_settings):
    secrets = [v for v in connect.password_values(bench_settings) if len(v) >= 8]
    assert secrets, "no password long enough to search for"
    checked = 0
    for root in (sample.dir, throughput.dir):
        for path in root.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                checked += 1
                for value in secrets:
                    assert value not in text, f"a password appears in {path.relative_to(root)}"
    assert checked > 50
    names = {p.name for p in sample.dir.iterdir()}
    assert {"raw.jsonl", "summary.csv", "env.json", "explain", "README.txt"} <= names


# ---------------------------------------------------------------------------------------- identities (criterion 4)


def test_timed_reads_run_as_app_ro_and_mongo_app(sample, conns):
    assert conns.ro.execute("SELECT current_user").fetchone()[0] == "app_ro"
    status = conns.mdb.command("connectionStatus")
    assert [u["user"] for u in status["authInfo"]["authenticatedUsers"]] == ["app"]
    env = json.loads((sample.dir / "env.json").read_text(encoding="utf-8"))
    assert env["identities"] == {"postgresql": "app_ro", "mongodb": "app"}


def test_the_runner_refuses_to_time_as_the_wrong_identity(bench_settings, conns):
    handles = bench_run.Handles(conns.admin, conns.ro2, conns.admin, conns.mdb, conns.admin_mdb, None)
    with pytest.raises(bench_run.Refusal, match="app_ro"):
        bench_run.identities(handles)


def test_every_recorded_select_was_explained_with_its_params(sample):
    seen = 0
    for path in (sample.dir / "explain").rglob("pg-*.json"):
        for st in json.loads(path.read_text(encoding="utf-8")):
            if st["select"]:
                seen += 1
                assert st["explained"] is True and st["explain_params"] == st["params"], path.name
                assert st["plan"]["Planning Time"] >= 0 and st["plan"]["Execution Time"] >= 0
            else:
                assert st["explained"] is False and st["plan"] is None
    assert seen >= len([c for c in READ_CASES if c.pg]) * 3
    assert (sample.dir / "explain" / "search_filtered" / "pg-bishan-4room-2019-2021.txt").exists()
    # The search repo runs SET TRANSACTION, a count and a page: one recorded control statement, two explained.
    doc = json.loads(
        (sample.dir / "explain/search_filtered/pg-bishan-4room-2019-2021.json").read_text(encoding="utf-8")
    )
    assert [s["select"] for s in doc] == [False, True, True]


@pytest.mark.parametrize("case_id", ["search_filtered", "trends_series", "rankings_year", "building_page", "food_town"])
def test_explaining_a_capture_uses_the_params_of_each_statement(case_id, conns):
    case = next(c for c in READ_CASES if c.id == case_id)
    verify.resolve_building_ids([case], conns.ro)
    statements, result = capture.capture_pg(case.pg.prepare(case.params[0].args), conns.ro)
    assert result is not None or case_id == "building_page"
    capture.explain_pg(conns.ro2, statements)
    selects = [s for s in statements if s.select]
    assert selects and all(s.explained and s.explain_params == s.params and s.plan for s in selects)
    assert not any(s.explained for s in statements if not s.select)
    assert conns.ro2.info.transaction_status.name == "IDLE", "the explain transaction must be rolled back"
    total = capture.pg_server_time(statements)
    assert total["server_ms"] == pytest.approx(total["planning_ms"] + total["execution_ms"])


def test_mongo_explain_time_exists_for_every_read_case(conns):
    for case in READ_CASES:
        if case.mongo is None:
            continue
        verify.resolve_building_ids([case], conns.ro)
        calls, _ = capture.capture_mongo(case.mongo.prepare(case.params[0].args), conns.mdb)
        assert calls, case.id
        assert capture.mongo_server_time(capture.explain_mongo(conns.mdb, calls)), case.id


def test_timed_calls_run_on_bare_connections(conns, monkeypatch, tmp_path):
    """The recorder wraps the one capture call only; the warm-up and timed calls never touch it."""
    pg_calls, mongo_calls = [], []
    real_execute = capture.RecordingConnection.execute
    real_count = capture.RecordingCollection.count_documents
    real_find = capture.RecordingCollection.find

    def spy_execute(self, *a, **k):
        pg_calls.append(a[0])
        return real_execute(self, *a, **k)

    def spy_count(self, *a, **k):
        mongo_calls.append("count")
        return real_count(self, *a, **k)

    def spy_find(self, *a, **k):
        mongo_calls.append("find")
        return real_find(self, *a, **k)

    monkeypatch.setattr(capture.RecordingConnection, "execute", spy_execute)
    monkeypatch.setattr(capture.RecordingCollection, "count_documents", spy_count)
    monkeypatch.setattr(capture.RecordingCollection, "find", spy_find)
    case = next(c for c in READ_CASES if c.id == "search_filtered")
    handles = bench_run.Handles(conns.ro, conns.ro2, None, conns.mdb, None, None)
    res = bench_run.Results(tmp_path / "bare")
    res.dir.mkdir()
    bench_run.time_reads(case, handles, "inv", res, lambda m: None, runs=4, warmup=2)
    sets = len(case.params)
    assert len(pg_calls) == 3 * sets, "the recorder saw more than one capture call per set (SET, count, page)"
    assert mongo_calls == ["count", "find"] * sets
    raw = read_jsonl(res.raw)
    assert len(raw) == 4 * sets * 2
    assert {r["conn"] for r in raw if r["db"] == "pg"} == {"psycopg.Connection"}
    assert not any("Recording" in r["conn"] for r in raw)
    assert all(r["conn"].endswith("Database") and "Recording" not in r["conn"] for r in raw if r["db"] == "mongo")


# ----------------------------------------------------------------------------------------------- cache (5)


def test_env_cache_values_come_from_the_server_and_the_wrapper_not_from_env(sample, conns, monkeypatch):
    shown = conns.ro.execute("SHOW shared_buffers").fetchone()[0]
    env = json.loads((sample.dir / "env.json").read_text(encoding="utf-8"))
    assert env["postgresql"]["shared_buffers"] == shown
    monkeypatch.setenv("PG_SHARED_BUFFERS", "1")  # .env says something else: it must play no part
    monkeypatch.setenv("MONGO_CACHE_GB", "99")
    monkeypatch.setenv("BENCH_MONGO_CACHE", "0.75")
    facts = bench_run.server_facts(conns.ro, conns.mdb)
    assert facts["postgresql"]["shared_buffers"] == shown
    assert facts["mongodb"]["wiredtiger_cache_gb"] == "0.75"  # the value run.sh read from the running server
    monkeypatch.delenv("BENCH_MONGO_CACHE")
    assert bench_run.server_facts(conns.ro, conns.mdb)["mongodb"]["wiredtiger_cache_gb"] is None


# ------------------------------------------------------------------------------------------------- refusals


def wrong_fraction(fraction) -> str:
    """An expectation that is wrong for whatever is loaded (the sample, a fraction or the full data)."""
    return "1" if float(fraction) < 0.5 else "0.25"


def test_a_wrong_expected_fraction_refuses_and_writes_nothing(fraction, tmp_path, bench_settings):
    rc = bench_run.main(
        ["--label", "wrong", "--results-root", str(tmp_path), "--expect-fraction", wrong_fraction(fraction)]
    )
    assert rc == 2 and not (tmp_path / "wrong").exists()


def test_the_label_plays_no_part_in_the_fraction_check(fraction, tmp_path, bench_settings):
    rc = bench_run.main(
        ["--label", "full-data", "--results-root", str(tmp_path), "--expect-fraction", wrong_fraction(fraction)]
    )
    assert rc == 2


def test_a_second_reads_run_refuses_unless_replace_and_replace_keeps_the_writes(sample, fraction, tmp_path):
    root = tmp_path / "copy"
    shutil.copytree(sample.root, root)
    again = ("--label", "sample", "--cases", "building_page", "--runs", "2", "--warmup", "0")
    assert run_bench(root, fraction, *again) == 2
    assert run_bench(root, fraction, *again, "--replace") == 0
    env = json.loads((root / "sample" / "env.json").read_text(encoding="utf-8"))
    assert [i["kind"] for i in env["invocations"]] == ["writes", "reads"]
    summary = read_csv(root / "sample" / "summary.csv")
    assert {r["case"] for r in summary if r["invocation"] == env["invocations"][1]["id"]} == {"building_page"}
    assert {r["case"] for r in summary if r["invocation"] == env["invocations"][0]["id"]} == {c.id for c in WRITE_CASES}
    assert {r["invocation"] for r in read_jsonl(root / "sample" / "raw.jsonl")} == {i["id"] for i in env["invocations"]}


def test_unknown_cases_and_write_cases_without_the_flag_are_refused(fraction, tmp_path, bench_settings):
    assert run_bench(tmp_path, fraction, "--label", "u", "--cases", "no_such_case") == 2
    assert run_bench(tmp_path, fraction, "--label", "u", "--cases", "admin_record_delete") == 2
    assert run_bench(tmp_path, fraction, "--label", "u", "--include-writes", "--cases", "building_page") == 2
    assert run_bench(tmp_path, fraction, "--label", "u", "--include-writes", "--throughput") == 2
    assert run_bench(tmp_path, fraction, "--label", "../escape") == 2
    assert not (tmp_path / "u").exists()


def test_a_writes_run_repairs_a_profile_reset_to_empty_stats(conns, fraction, tmp_path):
    """An interrupted rebuild leaves stats present but empty; the writes run must notice and rebuild first."""
    from db.mongo.stats import EMPTY_STATS

    profiles = conns.admin_mdb.building_profiles
    original = profiles.find_one({"stats.txn_count": {"$gt": 0}})
    assert original is not None
    try:
        profiles.update_one({"_id": original["_id"]}, {"$set": {"stats": EMPTY_STATS, "recent_sales": []}})
        rc = run_bench(
            tmp_path, fraction, "--label", "repair", "--include-writes", "--cases", "admin_record_delete",
            "--runs", "1", "--warmup", "0",
        )  # fmt: skip
        assert rc == 0
        repaired = profiles.find_one({"_id": original["_id"]})
        assert repaired["stats"] == original["stats"] and repaired["recent_sales"] == original["recent_sales"]
    finally:
        profiles.update_one(
            {"_id": original["_id"]}, {"$set": {"stats": original["stats"], "recent_sales": original["recent_sales"]}}
        )
