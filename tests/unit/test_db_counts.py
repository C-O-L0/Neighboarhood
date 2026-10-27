"""Database counts: comparison rules and exit status, without a database."""

import contextlib
import json

from etl import db_counts

CLEAN = {
    "raw_dir": "data/sample",
    "rows_out": {
        "town": 27,
        "flat_type": 9,
        "flat_model": 21,
        "building": 40,
        "building_flat_supply": 100,
        "resale_transaction": 300,
        "hawker_centre": 5,
    },
    "stub_buildings": [{"block": "82", "street": "MACPHERSON LANE"}],
    "coordinates": {"geocoded_buildings": 35},
}


def matching_counts() -> dict[str, dict]:
    return {
        "town": {"pg": 27, "mongo": 20},
        "flat_type": {"pg": 9, "mongo": 6},
        "flat_model": {"pg": 21, "mongo": 11},
        "building": {"pg": 40, "mongo": 40},
        "stub_building": {"pg": 1, "mongo": 1},
        "geocoded_building": {"pg": 35, "mongo": 35},
        "building_flat_supply": {"pg": 100, "mongo": 100},
        "sale_all": {"pg": 300, "mongo": 300},
        "snapshot_sale": {"pg": 300, "mongo": 300},
        "hawker_centre": {"pg": 5, "mongo": 5},
        "buyer": {"pg": 0, "mongo": 0},
        "saved_building": {"pg": 0, "mongo": 0},
    }


def test_compare_all_match():
    rows = db_counts.compare(matching_counts(), CLEAN)
    assert [r["entity"] for r in rows] == [e for e, _, _ in db_counts.ENTITIES]
    assert all(r["match"] for r in rows)
    assert next(r for r in rows if r["entity"] == "stub_building")["cleaning"] == 1
    assert next(r for r in rows if r["entity"] == "sale_all")["cleaning"] is None


def test_compare_admin_sales_do_not_break_a_match():
    counts = matching_counts()
    counts["sale_all"] = {"pg": 303, "mongo": 303}  # three admin sales in both databases
    assert all(r["match"] for r in db_counts.compare(counts, CLEAN))


def test_compare_flags_each_kind_of_difference():
    counts = matching_counts()
    counts["building_flat_supply"]["mongo"] = 99  # all three compared
    counts["town"]["pg"] = 26  # PostgreSQL against cleaning
    counts["buyer"]["mongo"] = 1  # PostgreSQL against MongoDB
    bad = {r["entity"] for r in db_counts.compare(counts, CLEAN) if not r["match"]}
    assert bad == {"building_flat_supply", "town", "buyer"}


def test_main_exits_one_and_names_the_entity(tmp_path, monkeypatch, capsys):
    (tmp_path / "clean_report.json").write_text(json.dumps(CLEAN), encoding="utf-8")
    counts = matching_counts()
    counts["hawker_centre"]["mongo"] = 4
    monkeypatch.setattr(db_counts, "_open", lambda: contextlib.nullcontext((None, None)))
    monkeypatch.setattr(db_counts, "collect", lambda pg, mdb: counts)

    assert db_counts.main(["--interim", str(tmp_path)]) == 1
    assert "hawker_centre" in capsys.readouterr().err
    saved = json.loads((tmp_path / "db_counts.json").read_text(encoding="utf-8"))
    assert saved["rows_out"] == CLEAN["rows_out"] and saved["entities"]["hawker_centre"] == {"pg": 5, "mongo": 4}


def test_main_exits_zero_when_all_match(tmp_path, monkeypatch, capsys):
    (tmp_path / "clean_report.json").write_text(json.dumps(CLEAN), encoding="utf-8")
    monkeypatch.setattr(db_counts, "_open", lambda: contextlib.nullcontext((None, None)))
    monkeypatch.setattr(db_counts, "collect", lambda pg, mdb: matching_counts())

    assert db_counts.main(["--interim", str(tmp_path)]) == 0
    assert "embedded (20 distinct)" in capsys.readouterr().out
