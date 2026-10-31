"""The town trend's summary (app/services/trends.py): rolling figures, a year apart."""

from datetime import date
from decimal import Decimal

from app.repos.dto import TrendPoint
from app.services.trends import summarise


def point(month: date, rolling: str, n: int = 10) -> TrendPoint:
    return TrendPoint(month, 3, Decimal("1"), Decimal("1"), None, n, Decimal(rolling))


def test_an_empty_series_has_no_summary():
    assert summarise([]) is None


def test_the_summary_compares_the_last_rolling_figure_with_a_year_before():
    points = [
        point(date(2025, 9, 1), "600000"),
        point(date(2026, 3, 1), "620000"),
        point(date(2026, 9, 1), "636000", 40),
    ]
    s = summarise(points)
    assert (s.month, s.median, s.n, s.earlier) == (date(2026, 9, 1), Decimal("636000"), 40, Decimal("600000"))
    assert s.change_pct == Decimal("6.0") and s.year_before == date(2025, 9, 1)


def test_a_fall_is_negative_and_rounds_half_up():
    s = summarise([point(date(2025, 1, 1), "800000"), point(date(2026, 1, 1), "799600")])
    assert s.change_pct == Decimal("-0.1")  # -0.05% rounds half up, away from zero


def test_no_sales_a_year_before_means_no_change():
    s = summarise([point(date(2025, 8, 1), "600000"), point(date(2026, 9, 1), "650000")])
    assert s.earlier is None and s.change_pct is None
