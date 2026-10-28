"""bench.scaling: three summary.csv files merged by the fraction in each env.json."""

import csv
import json

import pytest

from bench import scaling
from bench.stats import SUMMARY_COLUMNS


def make_folder(root, name, fraction, cases=("search_filtered",)):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "env.json").write_text(json.dumps({"fraction": fraction}), encoding="utf-8")
    with open(folder / "summary.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, SUMMARY_COLUMNS, lineterminator="\n")
        w.writeheader()
        for case in cases:
            for db in ("pg", "mongo"):
                w.writerow(
                    {"invocation": "i1", "case": case, "pair": "live", "db": db, "param_set": "p1", "part": "call",
                     "n": 50, "rows": 10, "median_ms": f"{fraction * 10:.3f}", "p95_ms": "9.0"}
                )  # fmt: skip
    return folder


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_merge_keys_by_env_fraction_not_folder_name(tmp_path):
    # The folder names are deliberately wrong: only env.json counts.
    make_folder(tmp_path, "f100", 0.50012)
    make_folder(tmp_path, "f50", 1.0, cases=("search_filtered", "trends_series"))
    make_folder(tmp_path, "f25", 0.2499, cases=("search_filtered",))
    assert scaling.main([str(tmp_path)]) == 0
    rows = read(tmp_path / "scaling.csv")
    assert list(rows[0]) == scaling.SCALING_COLUMNS
    by_fraction = {}
    for r in rows:
        by_fraction.setdefault(r["fraction"], set()).add(r["source"])
    assert by_fraction == {"0.25": {"f25"}, "0.5": {"f100"}, "1": {"f50"}}
    assert [r["fraction"] for r in rows] == sorted((r["fraction"] for r in rows), key=float)
    assert len(rows) == 2 + 2 + 4
    assert {r["median_ms"] for r in rows if r["fraction"] == "1"} == {"10.000"}


def test_rerun_rebuilds_scaling_csv_instead_of_appending(tmp_path):
    for name, f in (("a", 0.25), ("b", 0.5), ("c", 1.0)):
        make_folder(tmp_path, name, f)
    assert scaling.main([str(tmp_path)]) == 0
    assert scaling.main([str(tmp_path)]) == 0
    assert len(read(tmp_path / "scaling.csv")) == 6


def test_needs_three_distinct_fractions(tmp_path, capsys):
    make_folder(tmp_path, "a", 0.25)
    make_folder(tmp_path, "b", 0.5)
    assert scaling.main([str(tmp_path)]) == 1
    assert "need 3 fractions" in capsys.readouterr().err
    assert not (tmp_path / "scaling.csv").exists()


def test_two_folders_with_the_same_fraction_are_refused(tmp_path):
    make_folder(tmp_path, "a", 0.25)
    make_folder(tmp_path, "b", 0.25)
    make_folder(tmp_path, "c", 1.0)
    with pytest.raises(scaling.ScalingError, match="both have fraction"):
        scaling.merge(tmp_path)


def test_missing_fraction_in_env_json_is_an_error(tmp_path):
    for name, f in (("a", 0.25), ("b", 0.5), ("c", 1.0)):
        make_folder(tmp_path, name, f)
    (tmp_path / "c" / "env.json").write_text("{}", encoding="utf-8")
    with pytest.raises(scaling.ScalingError, match="fraction"):
        scaling.merge(tmp_path)


def test_folders_without_both_files_are_ignored(tmp_path):
    for name, f in (("a", 0.25), ("b", 0.5), ("c", 1.0)):
        make_folder(tmp_path, name, f)
    (tmp_path / "explain").mkdir()
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert len(scaling.merge(tmp_path)) == 6
