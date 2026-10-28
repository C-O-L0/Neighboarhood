"""Index on and off (task 4.1b) against the running stack, on the sample.

bench.explain drops an index in a rolled-back owner transaction (PostgreSQL) or drops and recreates it (MongoDB, as the
etl user). These tests run it with 1 run and no warm-up into a temporary results root and check that each index is
back exactly as it was afterwards, also when the timed step raises. They need PG_OWNER_PASSWORD and MONGO_ETL_PASSWORD
(the CI test step and the README command pass both) and skip without them.
"""

import csv
import json

import pytest

from bench import connect
from bench import explain as bench_explain
from bench.cases import load_cases
from bench.run import Results

pytestmark = pytest.mark.integration

CASE_IDS = list(bench_explain.INDEX_CASES)


@pytest.fixture(scope="module")
def handles():
    s = connect.settings()
    if not s.pg_owner_password or not s.mongo_etl_password:
        pytest.skip("PG_OWNER_PASSWORD and MONGO_ETL_PASSWORD are not set; pass them to the test container")
    client, etl_db = connect.open_mongo("etl", s)
    with connect.open_pg("neighboar_owner", s) as owner:
        yield owner, etl_db
    client.close()


def mongo_state(etl_db) -> dict:
    return {name: etl_db[name].index_information() for name in ("resale_transactions", "building_profiles")}


def run_cases(handles, tmp_path, ids=CASE_IDS, hook=None):
    owner, etl_db = handles
    res = Results(tmp_path / "idx")
    cases = [c for c in load_cases() if c.id in ids]
    rows = bench_explain.run_cases(owner, etl_db, cases, "test-inv", res, lambda _: None, 1, 0, hook)
    return res, rows


@pytest.fixture(scope="module")
def on_off(handles, tmp_path_factory):
    """One run of all three cases, with the index state before and after it."""
    owner, etl_db = handles
    before = (bench_explain.pg_index_defs(owner), mongo_state(etl_db))
    res, rows = run_cases(handles, tmp_path_factory.mktemp("idx"))
    after = (bench_explain.pg_index_defs(owner), mongo_state(etl_db))
    return res, rows, before, after


def by_key(rows):
    return {(r["case"], r["db"], r["param_set"], r["arm"]): r for r in rows}


def test_every_case_has_both_arms_for_both_databases_and_all_param_sets(on_off):
    _, rows, _, _ = on_off
    assert len(rows) == len(CASE_IDS) * 2 * 3 * 2
    assert {r["arm"] for r in rows} == {"on", "off"}
    assert {r["db"] for r in rows} == {"pg", "mongo"}
    assert all(r["n"] == 1 and r["plan"] for r in rows)


def test_the_plan_without_each_index_differs_in_both_databases(on_off):
    _, rows, _, _ = on_off
    keyed = by_key(rows)
    for case in CASE_IDS:
        for db in ("pg", "mongo"):
            labels = [ps.label for ps in load_param_sets(case)]
            plans = [(keyed[(case, db, x, "on")], keyed[(case, db, x, "off")]) for x in labels]
            changed = [on["plan"] != off["plan"] for on, off in plans]
            assert any(changed), f"{case} {db}: no parameter set changed its plan without the index"
            for on, off in plans:
                assert not off["index_in_plan"], f"{case} {db}: the dropped index is still in the plan"
                if on["plan"] != off["plan"]:
                    assert on["index_in_plan"], f"{case} {db}: plans differ but the index was not the one used"


def load_param_sets(case_id):
    return next(c for c in load_cases() if c.id == case_id).params


def test_the_indexes_are_identical_afterwards_in_both_databases(on_off):
    _, _, before, after = on_off
    assert before[0] == after[0]
    assert before[1] == after[1]
    assert {c.pg_index for c in bench_explain.INDEX_CASES.values()} <= set(before[0])


def test_the_plans_are_saved_with_and_without_the_index(on_off):
    res, _, _, _ = on_off
    for case in CASE_IDS:
        files = {p.name for p in (res.explain / case).iterdir()}
        for db in ("pg", "mongo"):
            for arm in ("on", "off"):
                label = load_param_sets(case)[0].label
                assert f"{db}-index-{arm}-{label}.json" in files
                assert f"{db}-index-{arm}-{label}.txt" in files


@pytest.mark.parametrize("db", ["pg", "mongo"])
def test_a_failure_in_the_timed_step_leaves_the_indexes_as_they_were(handles, tmp_path, db):
    owner, etl_db = handles
    before = (bench_explain.pg_index_defs(owner), mongo_state(etl_db))
    seen = []

    def hook(hook_db, arm):
        seen.append((hook_db, arm))
        if hook_db == db and arm == "off":
            raise bench_explain.ForcedFailure("forced")

    with pytest.raises(bench_explain.ForcedFailure):
        run_cases(handles, tmp_path, ["search_price_sort"], hook)
    assert (db, "off") in seen
    assert bench_explain.pg_index_defs(owner) == before[0]
    assert mongo_state(etl_db) == before[1]
    assert owner.info.transaction_status.name == "IDLE"


def test_main_writes_the_csv_and_an_env_entry_and_no_password(tmp_path):
    s = connect.settings()
    if not s.pg_owner_password or not s.mongo_etl_password:
        pytest.skip("PG_OWNER_PASSWORD and MONGO_ETL_PASSWORD are not set")
    rc = bench_explain.main(
        ["--cases", "median_exact", "--label", "idx", "--runs", "1", "--warmup", "0", "--results-root", str(tmp_path)]
    )
    assert rc == 0
    out = tmp_path / "idx"
    with open(out / "index_on_off.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == bench_explain.INDEX_COLUMNS
    assert {(r["db"], r["arm"]) for r in rows} == {(d, a) for d in ("pg", "mongo") for a in ("on", "off")}
    env = json.loads((out / "env.json").read_text(encoding="utf-8"))
    entry = env["invocations"][-1]
    assert entry["kind"] == "index_on_off"
    assert entry["identities"] == {"postgresql": "neighboar_owner", "mongodb": "etl"}
    secrets = connect.password_values(s)
    assert secrets
    for path in out.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            assert not [v for v in secrets if v in text], f"a password appears in {path.name}"


def test_main_returns_one_on_a_forced_failure_and_leaves_the_indexes(handles, tmp_path):
    owner, etl_db = handles
    before = (bench_explain.pg_index_defs(owner), mongo_state(etl_db))
    rc = bench_explain.main(
        [
            "--cases", "search_type_month", "--label", "idx", "--runs", "1", "--warmup", "0",
            "--results-root", str(tmp_path), "--force-failure",
        ]
    )  # fmt: skip
    assert rc == 1
    assert not (tmp_path / "idx" / "index_on_off.csv").exists()
    assert (bench_explain.pg_index_defs(owner), mongo_state(etl_db)) == before


def test_main_refuses_an_unknown_case(tmp_path):
    assert bench_explain.main(["--cases", "trends_series", "--results-root", str(tmp_path)]) == 2

