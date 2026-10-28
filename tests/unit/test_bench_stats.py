"""Median, nearest-rank p95 and the summary writer of the benchmark harness (task 4.1a)."""

import csv

import pytest

from bench.stats import (
    SUMMARY_COLUMNS,
    THROUGHPUT_COLUMNS,
    median,
    p95,
    percentile_nearest_rank,
    summarise,
    write_rows,
    write_summary,
)

# ------------------------------------------------------------------------------------------------ median


def test_median_of_an_odd_count_is_the_middle_value():
    assert median([5.0, 1.0, 3.0]) == 3.0


def test_median_of_an_even_count_is_the_mean_of_the_two_middle_values():
    assert median([4.0, 1.0, 2.0, 3.0]) == 2.5


def test_median_of_one_value():
    assert median([7.5]) == 7.5


def test_median_ignores_input_order_and_does_not_change_it():
    values = [9.0, 1.0, 5.0, 3.0, 7.0]
    assert median(values) == 5.0
    assert values == [9.0, 1.0, 5.0, 3.0, 7.0]


def test_median_of_nothing_is_an_error():
    with pytest.raises(ValueError):
        median([])


# ------------------------------------------------------------------------------------------------ p95


def test_p95_of_100_values_is_the_95th_smallest():
    assert p95([float(i) for i in range(1, 101)]) == 95.0


def test_p95_of_50_runs_is_the_48th_smallest():
    # ceil(0.95 * 50) = 48
    assert p95([float(i) for i in range(1, 51)]) == 48.0


def test_p95_of_20_values_is_the_19th_smallest():
    assert p95([float(i) for i in range(1, 21)]) == 19.0


def test_p95_of_three_runs_is_the_largest():
    assert p95([2.0, 9.0, 4.0]) == 9.0


def test_p95_of_one_value_is_that_value():
    assert p95([3.25]) == 3.25


def test_p95_is_a_value_that_was_measured():
    values = [1.1, 2.7, 9.3, 4.4, 5.8, 0.2, 7.7]
    assert p95(values) in values


def test_nearest_rank_other_percentiles():
    values = [15.0, 20.0, 35.0, 40.0, 50.0]
    assert percentile_nearest_rank(values, 30) == 20.0  # ceil(1.5) = 2
    assert percentile_nearest_rank(values, 40) == 20.0  # ceil(2.0) = 2
    assert percentile_nearest_rank(values, 50) == 35.0  # ceil(2.5) = 3
    assert percentile_nearest_rank(values, 100) == 50.0


def test_percentile_rejects_nothing_and_bad_percentiles():
    with pytest.raises(ValueError):
        p95([])
    for bad in (0, -1, 101):
        with pytest.raises(ValueError):
            percentile_nearest_rank([1.0], bad)


# ------------------------------------------------------------------------------------------ summary writer


def rows_of(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_summarise_computes_the_statistics():
    row = summarise(
        "inv1", "search_filtered", "live", "pg", "set-a", [3.0, 1.0, 2.0, 4.0], 50,
        server={"server_ms": 1.5, "planning_ms": 0.5, "execution_ms": 1.0},
    )  # fmt: skip
    assert row["n"] == 4 and row["rows"] == 50 and row["part"] == "call"
    assert row["median_ms"] == 2.5 and row["p95_ms"] == 4.0 and row["min_ms"] == 1.0 and row["max_ms"] == 4.0
    assert (row["server_ms"], row["planning_ms"], row["execution_ms"]) == (1.5, 0.5, 1.0)


def test_summary_file_has_the_documented_columns_and_values(tmp_path):
    path = tmp_path / "summary.csv"
    rows = [
        summarise("inv1", "c1", "live", "pg", "a", [1.0, 2.0, 3.0], 5, server={"server_ms": 2.0}),
        summarise("inv1", "c1", "live", "mongo", "a", [4.0, 5.0, 6.0], 5),
    ]
    assert write_summary(path, rows) == 2
    assert path.read_text(encoding="utf-8").splitlines()[0] == ",".join(SUMMARY_COLUMNS)
    back = rows_of(path)
    assert [r["db"] for r in back] == ["pg", "mongo"]
    assert back[0]["median_ms"] == "2.0000" and back[0]["p95_ms"] == "3.0000" and back[0]["server_ms"] == "2.0000"
    assert back[1]["server_ms"] == "" and back[1]["planning_ms"] == "" and back[1]["execution_ms"] == ""
    assert back[0]["n"] == "3" and back[0]["rows"] == "5" and back[0]["invocation"] == "inv1"


def test_a_second_invocation_appends_without_a_second_header(tmp_path):
    path = tmp_path / "summary.csv"
    write_summary(path, [summarise("inv1", "c1", "live", "pg", "a", [1.0], 1)])
    write_summary(path, [summarise("inv2", "c2", "write", "pg", "b", [2.0], 1, part="write")])
    text = path.read_text(encoding="utf-8").splitlines()
    assert text.count(",".join(SUMMARY_COLUMNS)) == 1 and len(text) == 3
    assert [r["invocation"] for r in rows_of(path)] == ["inv1", "inv2"]
    assert rows_of(path)[1]["part"] == "write"


def test_lines_end_with_lf_only(tmp_path):
    path = tmp_path / "summary.csv"
    write_summary(path, [summarise("inv1", "c1", "live", "pg", "a", [1.0], 1)])
    assert b"\r" not in path.read_bytes()


def test_unknown_columns_are_refused(tmp_path):
    with pytest.raises(ValueError, match="unknown column"):
        write_rows(tmp_path / "x.csv", ["a"], [{"a": 1, "b": 2}])


def test_throughput_columns_are_written(tmp_path):
    path = tmp_path / "throughput.csv"
    row = dict.fromkeys(THROUGHPUT_COLUMNS, 1) | {"case": "search_filtered", "db": "pg"}
    write_rows(path, THROUGHPUT_COLUMNS, [row])
    assert path.read_text(encoding="utf-8").splitlines()[0] == ",".join(THROUGHPUT_COLUMNS)
