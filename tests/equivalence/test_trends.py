"""Trends: PostgreSQL and MongoDB return the same monthly series, YoY lookups and rolling medians."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

import pytest

from app.repos.dto import TrendPoint
from app.repos.mongo.trends import MongoTrendRepo
from app.repos.pg.trends import PgTrendRepo
from app.services.trends import TrendQuery, add_months
from tests.equivalence.cases import has_gap, select_full_pairs, trend_params, trend_series
from tests.equivalence.compare import assert_equivalent

pytestmark = pytest.mark.integration


def both(pg_conn, mongo_db, **params):
    q = TrendQuery.model_validate(params)
    return PgTrendRepo(pg_conn).trend(q), MongoTrendRepo(mongo_db).trend(q)


def raw_sales(pg_conn, town, flat_type) -> dict[date, list[Decimal]]:
    """Every sale price of the series by month, straight from the base table."""
    rows = pg_conn.execute(
        """
        SELECT t.txn_month, t.resale_price
        FROM resale_transaction t
        JOIN building b USING (building_id)
        JOIN town tw ON tw.town_id = b.town_id
        JOIN flat_type ft ON ft.flat_type_id = t.flat_type_id
        WHERE tw.name = %s AND ft.name = %s
        """,
        (town, flat_type),
    ).fetchall()
    by_month = defaultdict(list)
    for month, price in rows:
        by_month[month].append(price)
    return by_month


def lower_median(prices: list[Decimal]) -> Decimal:
    ordered = sorted(prices)
    return ordered[(len(ordered) - 1) // 2]


def window(sales: dict[date, list[Decimal]], month: date, first: int, last: int) -> list[Decimal]:
    """Prices of the calendar months month+first .. month+last (a missing month contributes nothing)."""
    return [p for k in range(first, last + 1) for p in sales.get(add_months(month, k), [])]


def gap_series(pg_conn):
    """First series, in (town, flat type) order, with a month whose prior-year month has no sales."""
    for key, months in trend_series(pg_conn).items():
        have = set(months)
        if has_gap(months) and any(add_months(m, -12) not in have for m in months[12:]):
            return key, months
    pytest.fail("the loaded data has no series with a gap after its first year")


def test_every_case_parameter_set_is_equivalent(pg_conn, mongo_db):
    params = trend_params(pg_conn)
    assert len(params) >= 2
    assert any("month_from" in p for p in params) and any("month_from" not in p for p in params)
    for p in params:
        pg, mongo = both(pg_conn, mongo_db, **p)
        assert pg, p
        assert_equivalent(pg, mongo, path=f"trends{p}")


def test_the_sample_covers_every_pair(pg_conn):
    series = trend_series(pg_conn)
    if len(series) <= 40:
        covered = {(p["town"], p["flat_type"]) for p in trend_params(pg_conn)}
        assert covered == set(series)
    else:
        pairs = {(p["town"], p["flat_type"]) for p in trend_params(pg_conn)}
        assert len(pairs) == 10
        assert sum(has_gap(series[k]) for k in pairs) >= 3


def test_full_data_selection_rule_on_synthetic_keys():
    keys = sorted((f"T{i:02d}", "4 ROOM") for i in range(131))
    gappy = {keys[5], keys[40], keys[41], keys[90], keys[130]}
    chosen = select_full_pairs(keys, gappy)
    assert len(chosen) == len(set(chosen)) == 10 and chosen == sorted(chosen)
    assert {keys[5], keys[40], keys[41]} <= set(chosen)  # first three gappy, in order
    assert keys[90] not in chosen or keys[90] in keys[::13]
    assert set(chosen) - {keys[5], keys[40], keys[41]} <= set(keys[::13])
    # no gappy pairs, and fewer stride pairs than needed: still exactly 10
    assert len(set(select_full_pairs(keys[:41], set()))) == 10
    assert len(set(select_full_pairs(keys, set()))) == 10


def test_points_are_ascending_and_typed(pg_conn, mongo_db):
    for rows in both(pg_conn, mongo_db, town="BISHAN", flat_type="4 ROOM"):
        assert [p.month for p in rows] == sorted({p.month for p in rows})
        for p in rows:
            assert isinstance(p, TrendPoint) and p.month.day == 1
            for value in (p.median_price, p.avg_psm, p.rolling_median):
                assert isinstance(value, Decimal)
            assert p.prev_year_median is None or isinstance(p.prev_year_median, Decimal)
            assert p.n >= 1 and p.rolling_n >= p.n


def test_missing_months_give_no_previous_year_and_a_calendar_rolling_window(pg_conn, mongo_db):
    (town, flat_type), months = gap_series(pg_conn)
    sales = raw_sales(pg_conn, town, flat_type)
    for rows in both(pg_conn, mongo_db, town=town, flat_type=flat_type):
        assert [p.month for p in rows] == months
        assert any(p.prev_year_median is None and p.month != months[0] for p in rows)
        for p in rows:
            earlier = sales.get(add_months(p.month, -12))
            assert p.prev_year_median == (lower_median(earlier) if earlier else None), p.month
            in_window = window(sales, p.month, -11, 0)
            assert p.rolling_n == len(in_window), p.month
            assert p.rolling_median == lower_median(in_window), p.month
    # the window counts calendar months, not rows: some window has an interior month without sales
    have = set(months)
    assert any(add_months(m, -k) > months[0] and add_months(m, -k) not in have for m in months for k in range(1, 12))


def test_bounded_range_keeps_history_before_month_from(pg_conn, mongo_db):
    (town, flat_type), months = next(((k, m) for k, m in trend_series(pg_conn).items() if len(m) > 14), (None, None))
    assert town, "a series with more than 14 months is needed"
    full_pg, full_mongo = both(pg_conn, mongo_db, town=town, flat_type=flat_type)
    # a start month that has a previous-year month and earlier months in its rolling window
    start = next(p for p in full_pg if p.prev_year_median is not None and p.rolling_n > p.n)
    end = full_pg[-2].month
    q = {"town": town, "flat_type": flat_type, "month_from": f"{start.month:%Y-%m}"}
    for rows, full in zip(both(pg_conn, mongo_db, **q), (full_pg, full_mongo), strict=True):
        assert rows[0].month == start.month
        assert rows[0].prev_year_median is not None
        assert rows[0].rolling_n > rows[0].n  # includes sales before month_from
        assert rows == [p for p in full if p.month >= start.month]
    q["month_to"] = f"{end:%Y-%m}"
    for rows in both(pg_conn, mongo_db, **q):
        assert rows[0].month == start.month and rows[-1].month == end
    assert_equivalent(*both(pg_conn, mongo_db, **q))


def test_month_to_is_the_last_returned_month(pg_conn, mongo_db):
    for (town, flat_type), months in trend_series(pg_conn).items():
        if len(months) < 3:
            continue
        target = months[len(months) // 2]
        for rows in both(pg_conn, mongo_db, town=town, flat_type=flat_type, month_to=f"{target:%Y-%m}"):
            assert rows[-1].month == target
        break
    else:
        pytest.fail("no series with three months")


def test_month_bounds_without_sales_return_nothing_or_trim(pg_conn, mongo_db):
    pg, mongo = both(pg_conn, mongo_db, town="BISHAN", flat_type="4 ROOM", month_from="1990-01", month_to="1990-12")
    assert pg == mongo == []
    pg, mongo = both(pg_conn, mongo_db, town="BISHAN", flat_type="4 ROOM", month_from="2999-01")
    assert pg == mongo == []


def test_hand_checked_month(pg_conn, mongo_db):
    series = trend_series(pg_conn)
    for (town, flat_type), months in series.items():
        sales = raw_sales(pg_conn, town, flat_type)
        # an even number of sales in the month and in the 12-month window shows the lower-median rule
        pick = next(
            (
                m
                for m in months
                if len(sales[m]) % 2 == 0 and len(window(sales, m, -11, 0)) % 2 == 0 and len(set(sales[m])) > 1
            ),
            None,
        )
        if pick:
            break
    else:
        pytest.fail("no month with an even count and distinct prices")
    month_prices = sorted(sales[pick])
    window_prices = sorted(window(sales, pick, -11, 0))
    for rows in both(pg_conn, mongo_db, town=town, flat_type=flat_type):
        point = next(p for p in rows if p.month == pick)
        assert point.n == len(month_prices)
        assert point.median_price == month_prices[len(month_prices) // 2 - 1]
        assert point.rolling_n == len(window_prices)
        assert point.rolling_median == window_prices[len(window_prices) // 2 - 1]
        psm = sum(p / a for p, a in _prices_and_areas(pg_conn, town, flat_type, pick)) / len(month_prices)
        assert abs(point.avg_psm - psm) <= Decimal("0.01")


def _prices_and_areas(pg_conn, town, flat_type, month):
    return pg_conn.execute(
        """
        SELECT t.resale_price, t.floor_area_sqm
        FROM resale_transaction t
        JOIN building b USING (building_id)
        JOIN town tw ON tw.town_id = b.town_id
        JOIN flat_type ft ON ft.flat_type_id = t.flat_type_id
        WHERE tw.name = %s AND ft.name = %s AND t.txn_month = %s
        """,
        (town, flat_type, month),
    ).fetchall()


def test_yoy_properties():
    def point(prev):
        return TrendPoint(date(2024, 5, 1), 3, Decimal("550000"), Decimal("6000.00"), prev, 30, Decimal("540000"))

    up = point(Decimal("500000"))
    assert up.yoy_change == Decimal("50000") and up.yoy_pct == Decimal("10.0")
    down = point(Decimal("600000"))
    assert down.yoy_change == Decimal("-50000") and down.yoy_pct == Decimal("-8.3")
    half = TrendPoint(date(2024, 5, 1), 1, Decimal("100.05"), Decimal("1.00"), Decimal("100"), 1, Decimal("1"))
    assert half.yoy_pct == Decimal("0.1")  # 0.05 rounds half up
    none = point(None)
    assert none.yoy_change is None and none.yoy_pct is None


def test_add_months():
    assert add_months(date(2024, 1, 1), -12) == date(2023, 1, 1)
    assert add_months(date(2024, 3, 1), -11) == date(2023, 4, 1)
    assert add_months(date(2024, 12, 1), 1) == date(2025, 1, 1)
    assert add_months(date(2024, 1, 1), -1) == date(2023, 12, 1)
