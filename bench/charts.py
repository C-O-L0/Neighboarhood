"""Benchmark charts from the committed CSVs alone (task 4.2).

    docker compose run --rm bench python -m bench.charts bench/results/2026-11-full

Reads <folder>/f100/summary.csv, f100/throughput.csv, f100/index_on_off.csv and <folder>/scaling.csv, and writes
latency_by_case, throughput, scaling and index_on_off as PNG (300 dpi, 16 cm wide) and SVG into <folder>.

Where a chart needs one number per case and database from the three parameter sets, the median is the median of the
three per-set medians and the p95 whisker is the largest of the three per-set p95 values.

Rules: PostgreSQL #2a78d6, MongoDB #eb6834 in every chart; MongoDB bars are also hatched; one value axis per chart;
a legend on every chart; text in #0b0b0b or #52514e; a light grid on the value axis only; axis titles carry units;
the median is labelled (1 dp) at the end of each bar and nowhere else; a log axis only when the range exceeds 100x.
"""

from __future__ import annotations

import csv
import math
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "matplotlib"))  # the service user has no home

import matplotlib  # noqa: E402

matplotlib.use("Agg")  # non-interactive: no display in the container

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

PG_COLOUR = "#2a78d6"
MONGO_COLOUR = "#eb6834"
TEXT_STRONG = "#0b0b0b"
TEXT_MUTED = "#52514e"
GRID = "#e3e2de"
MONGO_HATCH = "//"
DB_NAMES = {"pg": "PostgreSQL", "mongo": "MongoDB"}
DB_COLOURS = {"pg": PG_COLOUR, "mongo": MONGO_COLOUR}
DB_ORDER = ("pg", "mongo")

DPI = 300
WIDTH_CM = 16
WIDTH_IN = WIDTH_CM / 2.54
LOG_RANGE = 100  # a log axis only when max / min exceeds this

CHARTS = ("latency_by_case", "throughput", "scaling", "index_on_off")


class ChartError(Exception):
    """An input file is missing or empty."""


# ------------------------------------------------------------------------------------------------- data


def read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        raise ChartError(f"missing input {path}")
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ChartError(f"{path} has no rows")
    return rows


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def collapse(rows: Sequence[dict], keys: Sequence[str]) -> dict[tuple, tuple[float, float]]:
    """{keys: (median of the median_ms values, largest p95_ms)} over the rows sharing the keys."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(tuple(r[k] for k in keys), []).append(r)
    return {
        k: (_median([float(r["median_ms"]) for r in g]), max(float(r["p95_ms"]) for r in g)) for k, g in groups.items()
    }


def latency_data(rows: Sequence[dict]) -> tuple[list[str], dict[tuple, tuple[float, float]]]:
    """Case order as in the file; the refresh half of an admin write is not a latency of the case."""
    kept = [r for r in rows if r["part"] != "refresh"]
    cases = list(dict.fromkeys(r["case"] for r in kept))
    return cases, collapse(kept, ("case", "db"))


# ---------------------------------------------------------------------------------------------- styling


def _apply_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 7,
            "text.color": TEXT_STRONG,
            "axes.labelcolor": TEXT_STRONG,
            "axes.titlecolor": TEXT_STRONG,
            "axes.edgecolor": TEXT_MUTED,
            "xtick.color": TEXT_MUTED,
            "ytick.color": TEXT_MUTED,
            "axes.titlesize": 7.5,
            "axes.labelsize": 7,
            "legend.fontsize": 7,
            "legend.frameon": False,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "hatch.linewidth": 0.6,
            "svg.hashsalt": "neighboar",
            "svg.fonttype": "path",
            "figure.dpi": DPI,
        }
    )


def _style_axes(ax, value_axis: str) -> None:
    """Light grid on the value axis only, behind the data."""
    ax.set_axisbelow(True)
    ax.grid(False)
    ax.grid(True, axis=value_axis, color=GRID, linewidth=0.5)
    ax.tick_params(length=2, width=0.5)


def _hatched(db: str) -> str | None:
    return MONGO_HATCH if db == "mongo" else None


def _bar_style(db: str, light: bool = False) -> dict:
    colour = DB_COLOURS[db]
    return {
        "facecolor": colour,
        "edgecolor": "white" if db == "mongo" else colour,
        "hatch": _hatched(db),
        "alpha": 0.45 if light else 1.0,
        "linewidth": 0.0 if db == "pg" else 0.4,
    }


def figure_legend(fig, handles: Sequence, ncol: int) -> None:
    """One legend for the whole chart, below the plots so it never covers a bar, a line or a label."""
    fig.legend(handles=list(handles), loc="outside lower center", ncol=ncol)


def use_log(values: Sequence[float]) -> bool:
    positive = [v for v in values if v > 0]
    return bool(positive) and max(positive) / min(positive) > LOG_RANGE


def _tick_format(value: float, _pos=None) -> str:
    return f"{value:g}"


def _fmt1(value: float) -> str:
    return f"{value:,.1f}"


def _save(fig, folder: Path, name: str) -> list[Path]:
    out = []
    for ext, kwargs in (("png", {"dpi": DPI, "metadata": {"Software": None}}), ("svg", {"metadata": {"Date": None}})):
        path = folder / f"{name}.{ext}"
        fig.savefig(path, format=ext, **kwargs)  # no bbox_inches="tight": the width must stay at 16 cm
        out.append(path)
    plt.close(fig)
    return out


# ------------------------------------------------------------------------------------------------ charts


def latency_by_case(folder: Path) -> list[Path]:
    cases, agg = latency_data(read_csv(folder / "f100" / "summary.csv"))
    dbs = [d for d in DB_ORDER if any((c, d) in agg for c in cases)]
    values = [v for k, (m, p) in agg.items() for v in (m, p)]
    log = use_log(values)
    height = max(4.0, 0.62 * len(cases) + 2.0)
    fig, ax = plt.subplots(figsize=(WIDTH_IN, height / 2.54), layout="constrained")
    _style_axes(ax, "x")
    bar_h = 0.8 / len(dbs)
    top = max(values)
    for i, case in enumerate(cases):
        for j, db in enumerate(dbs):
            if (case, db) not in agg:
                continue
            med, p95 = agg[(case, db)]
            y = i + (j - (len(dbs) - 1) / 2) * bar_h
            ax.barh(y, med, height=bar_h * 0.92, zorder=2, **_bar_style(db))
            ax.plot([med, p95], [y, y], color=TEXT_MUTED, linewidth=0.6, zorder=3, solid_capstyle="butt")
            ax.plot([p95, p95], [y - bar_h * 0.25, y + bar_h * 0.25], color=TEXT_MUTED, linewidth=0.6, zorder=3)
            ax.text(
                max(med, p95) * (1.12 if log else 1) + (0 if log else top * 0.01),
                y,
                _fmt1(med),
                va="center",
                ha="left",
                fontsize=6,
                color=TEXT_STRONG,
            )
    ax.set_yticks(range(len(cases)), cases)
    ax.invert_yaxis()
    if log:
        ax.set_xscale("log")
        ax.set_xlim(left=min(v for v in values if v > 0) / 2, right=top * 6)
        ax.xaxis.set_major_formatter(FuncFormatter(_tick_format))
        ax.set_xlabel("Latency (ms), log scale")
    else:
        ax.set_xlim(0, top * 1.12)
        ax.set_xlabel("Latency (ms)")
    whisker = Line2D([], [], color=TEXT_MUTED, linewidth=0.6, label="p95 (whisker)")
    figure_legend(fig, [Patch(label=DB_NAMES[d], **_bar_style(d)) for d in dbs] + [whisker], ncol=len(dbs) + 1)
    ax.set_title("Median latency by case", loc="left")
    return _save(fig, folder, "latency_by_case")


def throughput(folder: Path) -> list[Path]:
    rows = read_csv(folder / "f100" / "throughput.csv")
    cases = list(dict.fromkeys(r["case"] for r in rows))
    fig, axes = plt.subplots(1, len(cases), figsize=(WIDTH_IN, 7.5 / 2.54), layout="constrained", squeeze=False)
    for ax, case in zip(axes[0], cases, strict=True):
        _style_axes(ax, "y")
        workers = sorted({int(r["workers"]) for r in rows if r["case"] == case})
        for db, marker, style in (("pg", "o", "-"), ("mongo", "s", "--")):
            pts = {int(r["workers"]): float(r["calls_per_s"]) for r in rows if r["case"] == case and r["db"] == db}
            if not pts:
                continue
            ax.plot(
                list(pts),
                list(pts.values()),
                color=DB_COLOURS[db],
                marker=marker,
                markersize=4,
                linestyle=style,
                linewidth=1.4,
                label=DB_NAMES[db],
            )
        ax.set_xticks(workers)
        ax.set_xlim(min(workers) - 0.5, max(workers) + 0.5)
        ax.margins(y=0.12)
        ax.set_ylim(bottom=0)
        ax.set_xlabel("Clients (worker processes)")
        ax.set_title(case, loc="left")
    for ax in axes[0]:
        ax.set_ylabel("Throughput (calls/s)")
    figure_legend(fig, axes[0][0].get_legend_handles_labels()[0], ncol=2)
    return _save(fig, folder, "throughput")


def scaling(folder: Path) -> list[Path]:
    rows = [{**r, "fraction": float(r["fraction"])} for r in read_csv(folder / "scaling.csv") if r["part"] != "refresh"]
    fractions = sorted({r["fraction"] for r in rows})
    cases = [c for c in dict.fromkeys(r["case"] for r in rows) if
             all(any(r["case"] == c and r["fraction"] == f for r in rows) for f in fractions)]  # fmt: skip
    if not cases:
        raise ChartError("scaling.csv has no case present at every fraction")
    agg = collapse(rows, ("case", "db", "fraction"))
    cols = 2 if len(cases) > 1 else 1
    nrows = math.ceil(len(cases) / cols)
    fig, axes = plt.subplots(
        nrows, cols, figsize=(WIDTH_IN, (5.2 * nrows + 1.4) / 2.54), layout="constrained", squeeze=False
    )
    flat = list(axes.flat)
    for ax, case in zip(flat, cases, strict=False):
        _style_axes(ax, "y")
        for db, marker, style in (("pg", "o", "-"), ("mongo", "s", "--")):
            pts = [(f, agg[(case, db, f)][0]) for f in fractions if (case, db, f) in agg]
            if not pts:
                continue
            ax.plot(
                [f * 100 for f, _ in pts],
                [m for _, m in pts],
                color=DB_COLOURS[db],
                marker=marker,
                markersize=4,
                linestyle=style,
                linewidth=1.4,
                label=DB_NAMES[db],
            )
        ax.set_xticks([f * 100 for f in fractions], [f"{f * 100:g}" for f in fractions])
        ax.margins(y=0.12)
        ax.set_ylim(bottom=0)
        ax.set_title(case, loc="left")
        ax.set_xlabel("Data loaded (% of sales)")
        ax.set_ylabel("Median latency (ms)")
    for ax in flat[len(cases) :]:
        ax.set_visible(False)
    figure_legend(fig, flat[0].get_legend_handles_labels()[0], ncol=2)
    return _save(fig, folder, "scaling")


def index_on_off(folder: Path) -> list[Path]:
    rows = read_csv(folder / "f100" / "index_on_off.csv")
    agg = collapse(rows, ("case", "db", "arm"))
    cases = list(dict.fromkeys(r["case"] for r in rows))
    fig, axes = plt.subplots(1, len(cases), figsize=(WIDTH_IN, 8.5 / 2.54), layout="constrained", squeeze=False)
    dbs = [d for d in DB_ORDER if any(k[1] == d for k in agg)]
    for ax, case in zip(axes[0], cases, strict=True):
        _style_axes(ax, "y")
        values = [agg[k][0] for k in agg if k[0] == case]
        log = use_log(values)
        top = max(values)
        width = 0.36
        for i, db in enumerate(dbs):
            for j, arm in enumerate(("on", "off")):
                if (case, db, arm) not in agg:
                    continue
                med = agg[(case, db, arm)][0]
                x = i + (j - 0.5) * width
                ax.bar(x, med, width=width * 0.92, zorder=2, **_bar_style(db, light=arm == "off"))
                ax.text(x, med * (1.1 if log else 1) + (0 if log else top * 0.015), _fmt1(med), ha="center",
                        va="bottom", fontsize=6, color=TEXT_STRONG)  # fmt: skip
        ax.set_xticks(range(len(dbs)), [DB_NAMES[d] for d in dbs])
        if log:
            ax.set_yscale("log")
            ax.set_ylim(bottom=min(v for v in values if v > 0) / 2, top=top * 4)
            ax.yaxis.set_major_formatter(FuncFormatter(_tick_format))
            ax.set_ylabel("Median latency (ms), log scale")
        else:
            ax.set_ylim(0, top * 1.15)
            ax.set_ylabel("Median latency (ms)")
        ax.set_title(case, loc="left")
    handles = []
    for db in dbs:
        handles.append(Patch(label=f"{DB_NAMES[db]}, index on", **_bar_style(db)))
        handles.append(Patch(label=f"{DB_NAMES[db]}, index off", **_bar_style(db, light=True)))
    figure_legend(fig, handles, ncol=len(handles))
    return _save(fig, folder, "index_on_off")


BUILDERS = {
    "latency_by_case": latency_by_case,
    "throughput": throughput,
    "scaling": scaling,
    "index_on_off": index_on_off,
}


def build_all(folder: Path) -> list[Path]:
    _apply_style()
    written = []
    for name in CHARTS:
        written += BUILDERS[name](folder)
    return written


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m bench.charts <results folder, e.g. bench/results/2026-11-full>", file=sys.stderr)
        return 2
    folder = Path(args[0])
    try:
        written = build_all(folder)
    except (ChartError, OSError, KeyError, ValueError) as exc:
        print(f"charts: {exc!r}" if isinstance(exc, KeyError) else f"charts: {exc}", file=sys.stderr)
        return 1
    for path in written:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
