"""Import report renders every rule from a real cleaner run on the sample."""

import json
from pathlib import Path

from etl import clean, report

SAMPLE = Path(__file__).resolve().parents[2] / "data" / "sample"


def test_report_from_sample(tmp_path):
    tables, rep, rejected = clean.clean(SAMPLE, tmp_path / "no-cache")
    clean.write(tables, rep, rejected, tmp_path)
    loaded = json.loads((tmp_path / "clean_report.json").read_text(encoding="utf-8"))  # as report.py reads it
    md = report.render(loaded, rejected)

    assert md.startswith("# Import report")
    assert f"| `resale_transaction` | {len(tables['resale_transaction']):,} |" in md
    assert "82 MACPHERSON LANE (Geylang)" in md
    assert "37 TEBAN GDNS RD" in md and "1981 (1 sale)" in md
    assert "1966 stored on `building`, chosen by remaining lease" in md
    assert "including the 1 sale recorded as 1981, matches 1966" in md and "1981 matches 0 sales" in md
    assert "Sale whose remaining lease disagrees with its own lease commencement year" in md
    assert "accepted by cleaning" in md and " loaded" not in md
    assert "not database counts" in md
    assert "No geocode cache was present" in md
    assert "None. Every sale passed" in md


def test_report_lists_rejections_by_reason(tmp_path):
    tables, rep, rejected = clean.clean(SAMPLE, tmp_path / "no-cache")
    rep = json.loads(json.dumps(rep, default=str))
    rep["rejected"] = 2
    rejected = rejected.__class__(
        [{"source_row": 5, "reason": "flat_model 'Loft'"}, {"source_row": 9, "reason": "flat_model 'Pod'"}]
    )
    md = report.render(rep, rejected)
    assert "| flat_model '...' | 2 |" in md


def test_report_lease_fallback_wording(tmp_path):
    tables, rep, rejected = clean.clean(SAMPLE, tmp_path / "no-cache")
    rep = json.loads(json.dumps(rep, default=str))
    a = rep["lease_year_anomalies"][0]
    a["method"], a["chosen"] = "majority_fallback", 1966
    md = report.render(rep, rejected)
    assert "no value matches any sale's remaining lease" in md and "majority value 1966" in md


def _loaded_report(tmp_path):
    tables, rep, rejected = clean.clean(SAMPLE, tmp_path / "no-cache")
    return json.loads(json.dumps(rep, default=str)), rejected


def _same(n):
    return {"pg": n, "mongo": n}


def _db_counts_for(rep):
    ro, geo = rep["rows_out"], rep["coordinates"]["geocoded_buildings"]
    entities = {
        "town": {"pg": ro["town"], "mongo": 20},
        "flat_type": {"pg": ro["flat_type"], "mongo": 6},
        "flat_model": {"pg": ro["flat_model"], "mongo": 11},
        "building": _same(ro["building"]),
        "stub_building": _same(len(rep["stub_buildings"])),
        "geocoded_building": _same(geo),
        "building_flat_supply": _same(ro["building_flat_supply"]),
        "sale_all": _same(ro["resale_transaction"] + 2),  # two admin sales in both databases
        "snapshot_sale": _same(ro["resale_transaction"]),
        "hawker_centre": _same(ro["hawker_centre"]),
        "buyer": _same(0),
        "saved_building": _same(0),
    }
    return {"taken_at": "2026-10-27T01:02:03+00:00", "raw_dir": rep["raw_dir"], "rows_out": ro, "entities": entities}


def test_report_renders_database_counts(tmp_path):
    rep, rejected = _loaded_report(tmp_path)
    md = report.render(rep, rejected, _db_counts_for(rep))
    lines = md.splitlines()
    i = lines.index("| Entity | Cleaning output | PostgreSQL | MongoDB | Match |")
    assert lines[i - 2] == "## Database counts"
    assert md.index("## Tables produced") < md.index("## Database counts") < md.index("## Rejected rows")
    rows = lines[i + 2 : i + 14]
    assert [ln.split(" | ")[0].removeprefix("| ") for ln in rows] == [
        "town",
        "flat_type",
        "flat_model",
        "building",
        "stub building",
        "geocoded building",
        "building_flat_supply",
        "sale (all sources)",
        "snapshot sale",
        "hawker_centre",
        "buyer",
        "saved_building",
    ]
    assert all(ln.endswith("| yes |") for ln in rows)
    assert f"| town | {rep['rows_out']['town']} | {rep['rows_out']['town']} | embedded (20 distinct) | yes |" in md
    assert "| sale (all sources) | n/a |" in md and "| buyer | n/a | 0 | 0 | yes |" in md
    assert "2026-10-27T01:02:03+00:00" in md
    assert "not database counts" not in md


def test_report_database_count_mismatch_shows_no(tmp_path):
    rep, rejected = _loaded_report(tmp_path)
    counts = _db_counts_for(rep)
    counts["entities"]["hawker_centre"]["mongo"] += 1
    md = report.render(rep, rejected, counts)
    row = next(ln for ln in md.splitlines() if ln.startswith("| hawker_centre |"))
    assert row.endswith("| no |")
    assert sum(ln.endswith("| no |") for ln in md.splitlines()) == 1


def test_report_stale_database_counts_render_no_table(tmp_path):
    rep, rejected = _loaded_report(tmp_path)
    counts = _db_counts_for(rep)
    counts["rows_out"] = {**counts["rows_out"], "resale_transaction": counts["rows_out"]["resale_transaction"] + 1}
    md = report.render(rep, rejected, counts)
    assert "stale" in md and "etl.db_counts" in md
    assert "| Entity | Cleaning output |" not in md


def test_report_without_database_counts_is_unchanged(tmp_path):
    rep, rejected = _loaded_report(tmp_path)
    md = report.render(rep, rejected)
    assert md == report.render(rep, rejected, None)
    assert "Database counts" not in md and "not database counts" in md
