"""Chart data: money as two-decimal strings, months as YYYY-MM, gaps as null."""

import json
from datetime import date
from decimal import Decimal

from app.repos.dto import TownRank, TrendPoint
from app.services.charts import money_text, rankings_chart, trends_chart


def point(month, median, rolling="400000", n=3):
    return TrendPoint(
        month=month, n=n, median_price=Decimal(median), avg_psm=Decimal("5000.00"),
        prev_year_median=None, rolling_n=10, rolling_median=Decimal(rolling),
    )


def rank(town, median, island="500000"):
    return TownRank(
        flat_type="4 ROOM", rank=1, town=town, n=5, median_price=Decimal(median),
        avg_psm=Decimal("5000.00"), island_median=Decimal(island), beats_island=True,
    )


def test_both_decimal_scales_give_the_same_text():
    assert money_text(Decimal("500000")) == money_text(Decimal("500000.00")) == "500000.00"
    assert money_text(Decimal("500000.005")) == "500000.01"
    assert money_text(None) is None


def test_trends_chart_fills_missing_months_with_null_and_is_json():
    data = trends_chart([point(date(2023, 11, 1), "500000"), point(date(2024, 2, 1), "510000.00", n=2)])
    assert data["months"] == ["2023-11", "2023-12", "2024-01", "2024-02"]
    assert data["median"] == ["500000.00", None, None, "510000.00"]
    assert data["rolling_median"] == ["400000.00", None, None, "400000.00"]
    assert data["n"] == [3, None, None, 2]
    json.dumps(data)


def test_trends_chart_for_no_points_is_empty():
    assert trends_chart([]) == {"months": [], "median": [], "rolling_median": [], "n": []}


def test_rankings_chart_has_one_element_per_flat_type_in_rank_order():
    four = [rank("BISHAN", "600000"), rank("BEDOK", "500000.00")]
    groups = [("4 ROOM", four), ("5 ROOM", [rank("BISHAN", "700000")])]
    data = rankings_chart(groups)
    assert [d["flat_type"] for d in data] == ["4 ROOM", "5 ROOM"]
    assert data[0]["towns"] == ["BISHAN", "BEDOK"]
    assert data[0]["median"] == ["600000.00", "500000.00"]
    assert data[0]["island_median"] == "500000.00"
    json.dumps(data)
