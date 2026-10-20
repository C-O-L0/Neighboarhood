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
    assert "37 TEBAN GDNS RD" in md and "majority value 1966" in md and "1981 (1 sale)" in md
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
