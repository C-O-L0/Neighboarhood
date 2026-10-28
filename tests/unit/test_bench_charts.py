"""bench.charts: the four charts from a small synthetic results tree, written only under tmp_path."""

import csv
import struct

import pytest

from bench import charts
from bench.stats import SUMMARY_COLUMNS, THROUGHPUT_COLUMNS

INDEX_COLUMNS = ["invocation", "case", "db", "param_set", "arm", "index", "n", "rows", "median_ms", "p95_ms"]
LIVE = ("search_filtered", "trends_series", "rankings_year", "building_page")


def write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, columns, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def summary_row(case, db, med, p95, part="call", ps="p1", **extra):
    return {"invocation": "i1", "case": case, "pair": "live", "db": db, "param_set": ps, "part": part, "n": 50,
            "rows": 10, "median_ms": med, "p95_ms": p95, **extra}  # fmt: skip


def build_tree(root, wide=False, fractions=(0.25, 0.5, 1.0)):
    """A results tree shaped like the real one: f100 files plus scaling.csv."""
    f100 = root / "f100"
    rows = []
    for i, case in enumerate(LIVE):
        for db, base in (("pg", 2.0), ("mongo", 3.0)):
            for k, ps in enumerate(("p1", "p2", "p3")):
                rows.append(summary_row(case, db, base + i + k * 0.1, base + i + 1.5, ps=ps))
    rows.append(summary_row("building_recent_lookup", "mongo", 400.0 if wide else 5.0, 450.0 if wide else 6.0))
    rows.append(summary_row("admin_record_delete", "pg", 3.0, 4.0, part="write"))
    rows.append(summary_row("admin_record_delete", "pg", 1.0, 2.0, part="refresh"))
    write_csv(f100 / "summary.csv", SUMMARY_COLUMNS, rows)
    tp = [
        {"invocation": "i3", "case": case, "db": db, "workers": w, "seconds": 30, "calls": 1000,
         "calls_per_s": rate * w, "median_ms": 1.0, "p95_ms": 2.0}
        for case in ("search_filtered", "building_page")
        for db, rate in (("pg", 100.0), ("mongo", 80.0))
        for w in (1, 4, 8)
    ]  # fmt: skip
    write_csv(f100 / "throughput.csv", THROUGHPUT_COLUMNS, tp)
    idx = [
        {"invocation": "i4", "case": case, "db": db, "param_set": ps, "arm": arm, "index": "ix", "n": 50, "rows": 5,
         "median_ms": (2.0 if arm == "on" else 9.0) * (1 if db == "pg" else 1.5), "p95_ms": 12.0}
        for case in ("search_price_sort", "search_type_month", "median_exact")
        for db in ("pg", "mongo")
        for ps in ("p1", "p2")
        for arm in ("on", "off")
    ]  # fmt: skip
    write_csv(f100 / "index_on_off.csv", INDEX_COLUMNS, idx)
    sc = []
    for f in fractions:
        for case in LIVE[:3]:
            for db in ("pg", "mongo"):
                sc.append({"fraction": f"{f:g}", "source": "x", **summary_row(case, db, 1 + 4 * f, 9.0)})
    sc.append({"fraction": "1", "source": "f100", **summary_row("only_full", "pg", 1.0, 2.0)})
    write_csv(root / "scaling.csv", ["fraction", "source", *SUMMARY_COLUMNS], sc)
    return root


def png_width_and_dpi(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width = struct.unpack(">I", data[16:20])[0]
    pos = 8
    dpi = None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        if kind == b"pHYs":
            ppu_x = struct.unpack(">I", data[pos + 8 : pos + 12])[0]
            dpi = ppu_x * 0.0254
        pos += 12 + length
    return width, dpi


@pytest.fixture
def figures(monkeypatch):
    """Keep each figure open so a test can inspect it; the real _save still writes the files."""
    kept = {}
    real_save = charts._save

    def save(fig, folder, name):
        kept[name] = fig
        out = real_save(fig, folder, name)
        return out

    monkeypatch.setattr(charts, "_save", save)
    yield kept
    charts.plt.close("all")


def test_all_four_charts_are_written_as_png_and_svg(tmp_path):
    build_tree(tmp_path)
    assert charts.main([str(tmp_path)]) == 0
    for name in charts.CHARTS:
        png, svg = tmp_path / f"{name}.png", tmp_path / f"{name}.svg"
        width, dpi = png_width_and_dpi(png)
        assert abs(width - 16 / 2.54 * 300) <= 2  # about 1890 px
        assert dpi is not None and abs(dpi - 300) < 1
        assert svg.read_text(encoding="utf-8").lstrip().startswith(("<?xml", "<svg"))
        assert "<svg" in svg.read_text(encoding="utf-8")


def test_series_colours_and_mongo_hatch_in_the_svg(tmp_path):
    build_tree(tmp_path)
    charts.build_all(tmp_path)
    for name in charts.CHARTS:
        text = (tmp_path / f"{name}.svg").read_text(encoding="utf-8")
        assert "#2a78d6" in text and "#eb6834" in text
    assert "<pattern" in (tmp_path / "latency_by_case.svg").read_text(encoding="utf-8")


def test_latency_bars_mongo_hatched_pg_plain_and_one_legend(tmp_path, figures):
    build_tree(tmp_path)
    charts.build_all(tmp_path)
    ax = figures["latency_by_case"].axes[0]
    bars = [p for p in ax.patches if p.get_width() > 0]
    assert bars
    for p in bars:
        colour = charts.matplotlib.colors.to_hex(p.get_facecolor())
        if colour == charts.MONGO_COLOUR:
            assert p.get_hatch() == "//"
        else:
            assert colour == charts.PG_COLOUR and not p.get_hatch()
    legend = figures["latency_by_case"].legends[0]
    assert legend is not None
    assert {t.get_text() for t in legend.get_texts()} >= {"PostgreSQL", "MongoDB"}
    assert len(figures["latency_by_case"].axes) == 1  # one value axis, no twin


def test_latency_aggregates_param_sets_and_drops_the_refresh_half(tmp_path, figures):
    build_tree(tmp_path)
    charts.build_all(tmp_path)
    ax = figures["latency_by_case"].axes[0]
    labels = [t.get_text() for t in ax.get_yticklabels()]
    assert labels[0] == "search_filtered"
    assert "admin_record_delete" in labels
    # search_filtered, pg: medians 2.0, 2.1, 2.2 -> 2.1 labelled with one decimal; p95 is the largest (3.5)
    texts = [t.get_text() for t in ax.texts]
    assert "2.1" in texts
    cases, agg = charts.latency_data(charts.read_csv(tmp_path / "f100" / "summary.csv"))
    assert agg[("search_filtered", "pg")] == (pytest.approx(2.1), pytest.approx(3.5))
    assert agg[("admin_record_delete", "pg")] == (3.0, 4.0)  # refresh row (1.0) excluded


def test_log_axis_only_when_the_range_exceeds_100x(tmp_path, figures):
    build_tree(tmp_path, wide=False)
    charts.build_all(tmp_path)
    ax = figures["latency_by_case"].axes[0]
    assert ax.get_xscale() == "linear"
    assert "log scale" not in ax.get_xlabel()
    assert "(ms)" in ax.get_xlabel()
    charts.plt.close("all")

    wide = tmp_path / "wide"
    build_tree(wide, wide=True)
    charts.build_all(wide)
    ax = figures["latency_by_case"].axes[0]
    assert ax.get_xscale() == "log"
    assert "log scale" in ax.get_xlabel()


def test_use_log_boundary():
    assert not charts.use_log([1.0, 100.0])
    assert charts.use_log([1.0, 100.1])
    assert not charts.use_log([])


def test_text_is_never_a_series_colour_and_labels_have_one_decimal(tmp_path, figures):
    build_tree(tmp_path)
    charts.build_all(tmp_path)
    allowed = {charts.TEXT_STRONG, charts.TEXT_MUTED}
    for name, fig in figures.items():
        texts = list(fig.findobj(charts.matplotlib.text.Text))
        for t in texts:
            if t.get_text().strip():
                assert charts.matplotlib.colors.to_hex(t.get_color()) in allowed, (name, t.get_text())
    for ax in figures["latency_by_case"].axes + figures["index_on_off"].axes:
        for t in ax.texts:
            assert t.get_text().count(".") == 1 and len(t.get_text().split(".")[1]) == 1


def test_line_charts_have_one_line_per_db_with_markers_and_no_value_labels(tmp_path, figures):
    build_tree(tmp_path)
    charts.build_all(tmp_path)
    for name, panels in (("throughput", 2), ("scaling", 3)):
        axes = [a for a in figures[name].axes if a.get_visible()]
        assert len(axes) == panels
        for ax in axes:
            lines = ax.get_lines()
            assert {charts.matplotlib.colors.to_hex(ln.get_color()) for ln in lines} == {
                charts.PG_COLOUR,
                charts.MONGO_COLOUR,
            }
            assert all(ln.get_marker() not in ("", "None", None) for ln in lines)
            assert not ax.texts
            assert "(" in ax.get_ylabel()   # units
    assert all(figures[n].legends for n in charts.CHARTS)  # one legend on every chart
    assert "calls/s" in figures["throughput"].axes[0].get_ylabel()


def test_scaling_uses_only_cases_present_at_every_fraction(tmp_path, figures):
    build_tree(tmp_path)
    charts.build_all(tmp_path)
    titles = [a.get_title(loc="left") for a in figures["scaling"].axes if a.get_visible()]
    assert titles == list(LIVE[:3])
    xs = figures["scaling"].axes[0].get_lines()[0].get_xdata()
    assert list(xs) == [25, 50, 100]


def test_index_chart_has_on_and_off_bars_per_db_with_matching_colour(tmp_path, figures):
    build_tree(tmp_path)
    charts.build_all(tmp_path)
    fig = figures["index_on_off"]
    assert len(fig.axes) == 3
    ax = fig.axes[0]
    bars = ax.patches
    assert len(bars) == 4
    hatched = [b for b in bars if b.get_hatch()]
    assert len(hatched) == 2
    assert all(charts.matplotlib.colors.to_hex(b.get_facecolor()[:3]) == charts.MONGO_COLOUR for b in hatched)
    heights = sorted(round(b.get_height(), 1) for b in bars)
    assert heights == [2.0, 3.0, 9.0, 13.5]  # pg on/off, mongo on/off


def test_grid_is_on_the_value_axis_only(tmp_path, figures):
    build_tree(tmp_path)
    charts.build_all(tmp_path)
    ax = figures["latency_by_case"].axes[0]
    assert any(g.get_visible() for g in ax.get_xgridlines())
    assert not any(g.get_visible() for g in ax.get_ygridlines())
    ax = figures["throughput"].axes[0]
    assert any(g.get_visible() for g in ax.get_ygridlines())
    assert not any(g.get_visible() for g in ax.get_xgridlines())


def test_missing_input_is_reported_not_a_traceback(tmp_path, capsys):
    build_tree(tmp_path)
    (tmp_path / "f100" / "throughput.csv").unlink()
    assert charts.main([str(tmp_path)]) == 1
    assert "throughput.csv" in capsys.readouterr().err


def test_headless_backend():
    assert charts.matplotlib.get_backend().lower() == "agg"
