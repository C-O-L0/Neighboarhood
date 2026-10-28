"""The sale filter of etl.pipeline --fraction and the plan signatures of bench.explain (no database needed)."""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bench.explain import mongo_plan_signature, pg_plan_signature, plan_uses_index
from etl import pipeline


def write_interim(folder, ids):
    table = pa.table({"txn_id": pa.array(ids, pa.int64()), "n": pa.array(ids, pa.int16())})
    pq.write_table(table, folder / "resale_transaction.parquet")
    report = {"rows_out": {"resale_transaction": len(ids), "building": 7}, "raw_dir": "data/sample"}
    (folder / "clean_report.json").write_text(json.dumps(report), encoding="utf-8")


@pytest.mark.parametrize(("fraction", "modulus"), [(0.25, 4), (0.5, 2)])
def test_filter_sales_keeps_the_txn_ids_divisible_by_the_modulus(tmp_path, fraction, modulus):
    ids = list(range(1, 41))
    write_interim(tmp_path, ids)
    kept = pipeline.filter_sales(tmp_path, fraction)
    expected = [i for i in ids if i % modulus == 0]
    table = pq.read_table(tmp_path / "resale_transaction.parquet")
    assert kept == len(expected) == table.num_rows
    assert table["txn_id"].to_pylist() == expected
    assert table.schema.field("n").type == pa.int16()


def test_filter_sales_rewrites_only_the_sale_count_and_records_the_fraction(tmp_path):
    write_interim(tmp_path, list(range(1, 41)))
    pipeline.filter_sales(tmp_path, 0.25)
    report = json.loads((tmp_path / "clean_report.json").read_text(encoding="utf-8"))
    assert report["rows_out"] == {"resale_transaction": 10, "building": 7}
    assert report["fraction"] == 0.25
    assert report["raw_dir"] == "data/sample"


def test_only_the_two_fractions_are_accepted():
    with pytest.raises(SystemExit):
        pipeline.main(["--fraction", "0.75"])


def test_pg_plan_signature_lists_nodes_and_index_names():
    plan = {
        "Plan": {
            "Node Type": "Limit",
            "Plans": [
                {"Node Type": "Index Scan", "Index Name": "some_idx"},
                {"Node Type": "Sort", "Plans": [{"Node Type": "Seq Scan"}]},
            ],
        }
    }
    assert pg_plan_signature(plan) == "Limit > Index Scan some_idx > Sort > Seq Scan"


def test_mongo_plan_signature_reads_find_and_aggregation_explains():
    ixscan = {"stage": "IXSCAN", "indexName": "type_price", "keyPattern": {"stage": "x"}}
    find = {"queryPlanner": {"winningPlan": {"stage": "LIMIT", "inputStage": {"stage": "FETCH", "inputStage": ixscan}}}}
    assert mongo_plan_signature(find) == "LIMIT > FETCH > IXSCAN type_price"
    cursor = {"$cursor": {"queryPlanner": {"winningPlan": {"stage": "COLLSCAN"}}}}
    assert mongo_plan_signature({"stages": [cursor, {"$group": {}}]}) == "COLLSCAN"
    plan = {"queryPlan": {"stage": "IXSCAN", "indexName": "a"}, "slotBasedPlan": {"stage": "x"}}
    sbe = {"queryPlanner": {"winningPlan": plan}}
    assert mongo_plan_signature(sbe) == "IXSCAN a"


def test_the_index_name_is_matched_as_a_whole_token():
    sig = "GROUP > FETCH > IXSCAN town_type_month | SORT > Bitmap Index Scan resale_transaction_type_month_idx"
    assert not plan_uses_index(sig, "type_month")
    assert plan_uses_index(sig, "town_type_month")
    assert plan_uses_index(sig, "resale_transaction_type_month_idx")
    assert plan_uses_index("GROUP > IXSCAN type_month", "type_month")


@pytest.fixture
def fake_pipeline(tmp_path, monkeypatch):
    """A tmp ROOT with a populated data/interim, a verified full snapshot, and recording stand-ins for the steps."""
    root = tmp_path / "root"
    interim = root / "data" / "interim"
    interim.mkdir(parents=True)
    (interim / "resale_transaction.parquet").write_bytes(b"keep me")
    (interim / "clean_report.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pipeline, "ROOT", root)
    settings = SimpleNamespace(demo_buyer_password="a", demo_admin_password="b")
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline.synth_buyers, "check_demo_passwords", lambda *a: None)
    monkeypatch.setattr(pipeline.synth_buyers, "main", lambda argv: 0)
    monkeypatch.setattr(pipeline.download, "load_manifest", lambda: {})
    monkeypatch.setattr(pipeline.download, "verify", lambda manifest, raw: True)
    seen = {}

    def record(name, fail=False):
        def step(argv):
            folder = Path(argv[argv.index("--out") + 1] if "--out" in argv else argv[argv.index("--interim") + 1])
            seen.setdefault(name, []).append(folder)
            assert folder.is_dir() or name == "clean"
            if name == "clean":
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "marker").write_text("x", encoding="utf-8")
            if fail:
                raise RuntimeError("load failed")
            return 0

        return step

    monkeypatch.setattr(pipeline.clean, "main", record("clean"))

    def filter_sales(folder, fraction):
        seen.setdefault("filter", []).append(folder)
        return 1

    monkeypatch.setattr(pipeline, "filter_sales", filter_sales)
    monkeypatch.setattr(pipeline.load_mongo, "main", record("mongo"))
    monkeypatch.setattr(pipeline.db_counts, "main", record("counts"))
    return SimpleNamespace(root=root, interim=interim, seen=seen, record=record, monkeypatch=monkeypatch)


def snapshot(folder):
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())}


def test_a_full_snapshot_fraction_run_never_touches_data_interim(fake_pipeline):
    f = fake_pipeline
    f.monkeypatch.setattr(pipeline.load_pg, "main", f.record("pg"))
    before = snapshot(f.interim)
    assert pipeline.main(["--fraction", "0.25"]) == 0
    folders = {name: set(paths) for name, paths in f.seen.items()}
    assert set(folders) == {"clean", "filter", "pg", "mongo", "counts"}
    used = {p for paths in folders.values() for p in paths}
    assert len(used) == 1
    (folder,) = used
    assert folder != f.interim and f.interim not in folder.parents
    assert Path(tempfile.gettempdir()) in folder.parents
    assert not folder.exists()
    assert snapshot(f.interim) == before


def test_the_temporary_folder_is_removed_when_a_load_raises(fake_pipeline):
    f = fake_pipeline
    f.monkeypatch.setattr(pipeline.load_pg, "main", f.record("pg", fail=True))
    before = snapshot(f.interim)
    with pytest.raises(RuntimeError, match="load failed"):
        pipeline.main(["--fraction", "0.5"])
    (folder,) = {p for paths in f.seen.values() for p in paths}
    assert Path(tempfile.gettempdir()) in folder.parents and folder != f.interim
    assert not folder.exists()
    assert snapshot(f.interim) == before


def test_a_plain_full_run_still_uses_data_interim(fake_pipeline):
    f = fake_pipeline
    f.monkeypatch.setattr(pipeline.load_pg, "main", f.record("pg"))
    assert pipeline.main([]) == 0
    assert f.seen["clean"] == [f.interim]
