"""Explore (task T2): both databases against figures worked out in Python from the raw sales.

The registry cases (test_cases.py) show the two databases agree; these tests show what they agree on is right.
"""

import math
from collections import defaultdict
from decimal import Decimal

import pytest

from app.repos.dto import BlockFitPage
from app.repos.mongo.explore import MongoExploreRepo
from app.repos.pg.explore import PgExploreRepo
from app.services.explore import PAGE_SIZE, BlockFilter, BlocksQuery, Window, block_filter, window_ending
from app.services.food import EARTH_RADIUS_M
from app.services.search import RESALE_TYPES
from app.services.trends import add_months
from tests.equivalence.cases import block_fit_params
from tests.equivalence.compare import assert_equivalent

pytestmark = pytest.mark.integration


def repos(pg_conn, mongo_db):
    return PgExploreRepo(pg_conn), MongoExploreRepo(mongo_db)


def sales(pg_conn, window: Window) -> list[tuple]:
    """(flat type, town, price, area, storey sum, lease year) of every sale in the window, read with plain SQL."""
    return pg_conn.execute(
        """
        SELECT flat_type, town, resale_price, floor_area_sqm, storey_min + storey_max, lease_commence_year
        FROM v_transaction_detail
        WHERE txn_month BETWEEN %s AND %s
        """,
        [window.start, window.end],
    ).fetchall()


def at_percentile(values: list, p: float):
    """percentile_disc by its definition: the sorted value at position ceil(p * n), counting from 1."""
    ordered = sorted(values)
    return ordered[max(math.ceil(p * len(ordered)), 1) - 1]


@pytest.fixture(scope="module")
def latest(pg_conn):
    return pg_conn.execute("SELECT max(txn_month) FROM resale_transaction").fetchone()[0]


def test_latest_month_is_the_newest_sale_in_both(pg_conn, mongo_db, latest):
    pg, mongo = repos(pg_conn, mongo_db)
    assert pg.latest_month() == mongo.latest_month() == latest
    assert latest.day == 1


@pytest.mark.parametrize("years_back", [0, 2])
def test_ranges_match_the_raw_sales(pg_conn, mongo_db, latest, years_back):
    window = window_ending(add_months(latest, -12 * years_back))
    by_type = defaultdict(lambda: ([], []))
    for flat_type, _, price, area, *_ in sales(pg_conn, window):
        by_type[flat_type][0].append(price)
        by_type[flat_type][1].append(area)
    expected = [
        (t, len(prices), at_percentile(areas, 0.5), at_percentile(prices, 0.1), at_percentile(prices, 0.9))
        for t in RESALE_TYPES
        if t in by_type
        for prices, areas in [by_type[t]]
    ]
    pg, mongo = repos(pg_conn, mongo_db)
    for rows in (pg.flat_type_ranges(window), mongo.flat_type_ranges(window)):
        got = [(r.flat_type, r.n, r.area_median, r.price_low, r.price_high) for r in rows]
        assert got == expected
    assert expected


def test_towns_match_the_raw_sales_for_every_type_and_budget(pg_conn, mongo_db, latest):
    window = window_ending(latest)
    rows = sales(pg_conn, window)
    pg, mongo = repos(pg_conn, mongo_db)
    checked = 0
    for flat_type in RESALE_TYPES:
        prices_by_town = defaultdict(list)
        for t, town, price, *_ in rows:
            if t == flat_type:
                prices_by_town[town].append(price)
        for budget in (Decimal("400000"), Decimal("650000"), Decimal("900000")):
            expected = sorted(
                (
                    (town, len(p), at_percentile(p, 0.5), sum(1 for x in p if x <= budget))
                    for town, p in prices_by_town.items()
                ),
                key=lambda r: (r[2], r[0]),
            )
            for repo in (pg, mongo):
                got = [
                    (r.town, r.n, r.median_price, r.within_budget) for r in repo.towns_fit(flat_type, budget, window)
                ]
                assert got == expected, (repo.__class__.__name__, flat_type, budget)
            checked += bool(expected)
    assert checked


def test_a_sale_exactly_at_the_budget_counts_as_within(pg_conn, mongo_db, latest):
    window = window_ending(latest)
    flat_type, price = pg_conn.execute(
        """
        SELECT flat_type, resale_price FROM v_transaction_detail
        WHERE txn_month BETWEEN %s AND %s ORDER BY txn_id LIMIT 1
        """,
        [window.start, window.end],
    ).fetchone()
    pg, mongo = repos(pg_conn, mongo_db)
    for repo in (pg, mongo):
        at = sum(t.within_budget for t in repo.towns_fit(flat_type, price, window))
        below = sum(t.within_budget for t in repo.towns_fit(flat_type, price - Decimal("0.01"), window))
        assert at > below


def test_window_ends_are_inclusive(pg_conn, mongo_db, latest):
    """A one-month-wide window still finds the sales of that month, in both databases."""
    window = Window(start=latest, end=latest)
    pg, mongo = repos(pg_conn, mongo_db)
    expected = pg_conn.execute("SELECT count(*) FROM resale_transaction WHERE txn_month = %s", [latest]).fetchone()[0]
    for repo in (pg, mongo):
        assert sum(r.n for r in repo.flat_type_ranges(window)) == expected > 0


def test_a_window_with_no_sales_is_empty_in_both(pg_conn, mongo_db, latest):
    window = Window(start=add_months(latest, 1), end=add_months(latest, 12))
    pg, mongo = repos(pg_conn, mongo_db)
    assert pg.flat_type_ranges(window) == mongo.flat_type_ranges(window) == []
    assert (
        pg.towns_fit("4 ROOM", Decimal("650000"), window) == mongo.towns_fit("4 ROOM", Decimal("650000"), window) == []
    )


def test_results_are_typed(pg_conn, mongo_db, latest):
    window = window_ending(latest)
    pg, mongo = repos(pg_conn, mongo_db)
    for repo in (pg, mongo):
        for r in repo.flat_type_ranges(window):
            assert isinstance(r.n, int) and r.n >= 1
            assert all(isinstance(v, Decimal) for v in (r.area_median, r.price_low, r.price_high))
            assert r.price_low <= r.price_high
        for t in repo.towns_fit("4 ROOM", Decimal("650000"), window):
            assert isinstance(t.median_price, Decimal) and 0 <= t.within_budget <= t.n
    assert_equivalent(pg.flat_type_ranges(window), mongo.flat_type_ranges(window))


@pytest.mark.parametrize("years_back", [0, 2])
def test_baselines_match_the_raw_sales(pg_conn, mongo_db, latest, years_back):
    window = window_ending(add_months(latest, -12 * years_back))
    rows = sales(pg_conn, window)
    pg, mongo = repos(pg_conn, mongo_db)
    checked = 0
    for flat_type in RESALE_TYPES:
        by_town = defaultdict(list)
        for t, town, _, area, storey_sum, lease in rows:
            if t == flat_type:
                by_town[town].append((storey_sum, lease, area))
        expected = [
            (
                town,
                len(v),
                at_percentile([s for s, _, _ in v], 0.5),
                at_percentile([y for _, y, _ in v if y is not None], 0.5),
                at_percentile([a for _, _, a in v], 0.5),
            )
            for town, v in sorted(by_town.items())
        ]
        for repo in (pg, mongo):
            got = [(b.town, b.n, b.storey_sum, b.lease_year, b.area_median) for b in repo.baselines(flat_type, window)]
            assert got == expected, (repo.__class__.__name__, flat_type)
        checked += bool(expected)
    assert checked


# ---------------------------------------------------------------- blocks that fit (T5)


def block_truth(pg_conn, f: BlockFilter) -> tuple[int, list[tuple]]:
    """Total and first page worked out in Python from the raw sales and buildings."""
    sales_rows = pg_conn.execute(
        """
        SELECT t.building_id, t.resale_price, t.storey_min + t.storey_max, t.floor_area_sqm
        FROM v_transaction_detail t
        WHERE t.flat_type = %s AND t.txn_month BETWEEN %s AND %s
        """,
        [f.flat_type, f.window.start, f.window.end],
    ).fetchall()
    buildings = {
        r[0]: r
        for r in pg_conn.execute(
            "SELECT b.building_id, b.block, b.street, tw.name, b.lease_commence_year, b.latitude, b.longitude"
            " FROM building b JOIN town tw USING (town_id)"
        ).fetchall()
    }
    per_block = defaultdict(list)
    for bid, price, storey_sum, area in sales_rows:
        per_block[bid].append((price, storey_sum, area))
    origin = buildings.get(f.place) if f.place is not None else None
    out = []
    for bid, s in per_block.items():
        b = buildings[bid]
        price = at_percentile([x[0] for x in s], 0.5)
        storey = at_percentile([x[1] for x in s], 0.5)
        area = at_percentile([x[2] for x in s], 0.5)
        if price > f.budget or (f.town is not None and b[3] != f.town):
            continue
        if f.min_lease_year is not None and (b[4] is None or b[4] < f.min_lease_year):
            continue
        if f.min_area is not None and area < f.min_area:
            continue
        distance = None
        if f.place is not None:
            if origin is None or origin[5] is None or b[5] is None:
                continue
            distance = haversine(origin[5], origin[6], b[5], b[6])
            if distance > f.radius_m:
                continue
        out.append((bid, b[1], b[2], b[3], len(s), price, storey, area, b[4], distance))
    key = (lambda r: (r[9], r[0])) if f.place is not None else (lambda r: (r[5], r[0]))
    out.sort(key=key)
    return len(out), out[: f.offset + PAGE_SIZE][f.offset :]


def haversine(a_lat, a_lon, b_lat, b_lon) -> float:
    la, lb = math.radians(float(a_lat)), math.radians(float(b_lat))
    dlat, dlon = lb - la, math.radians(float(b_lon)) - math.radians(float(a_lon))
    h = math.sin(dlat / 2) ** 2 + math.cos(la) * math.cos(lb) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def test_blocks_fit_match_the_raw_sales(pg_conn, mongo_db, latest):
    pg, mongo = repos(pg_conn, mongo_db)
    checked = 0
    for p in block_fit_params(pg_conn):
        q = BlocksQuery.model_validate({k: v for k, v in p.items() if k != "end"})
        f = block_filter(q, window_ending(p["end"]))
        total, page = block_truth(pg_conn, f)
        for repo in (pg, mongo):
            got = repo.blocks_fit(f)
            assert got.total == total, (repo.__class__.__name__, p)
            rows = [
                (
                    b.building_id,
                    b.block,
                    b.street,
                    b.town,
                    b.n,
                    b.median_price,
                    b.storey_sum,
                    b.area_median,
                    b.lease_year,
                )
                for b in got.rows
            ]
            assert rows == [r[:9] for r in page], (repo.__class__.__name__, p)
            for b, r in zip(got.rows, page, strict=True):
                assert (b.distance_m is None) == (r[9] is None)
                if r[9] is not None:
                    assert abs(b.distance_m - Decimal(repr(r[9]))) <= 1
        checked += bool(page)
    assert checked


def test_a_page_past_the_end_keeps_the_total(pg_conn, mongo_db, latest):
    q = BlocksQuery.model_validate({"flat_type": "4 ROOM", "budget": "2000000", "page": "9999"})
    f = block_filter(q, window_ending(latest))
    pg, mongo = repos(pg_conn, mongo_db)
    a, b = pg.blocks_fit(f), mongo.blocks_fit(f)
    assert a.rows == b.rows == [] and a.total == b.total > 0


def test_an_unlocated_place_has_no_blocks_near_it(pg_conn, mongo_db, latest):
    unlocated = pg_conn.execute("SELECT min(building_id) FROM building WHERE latitude IS NULL").fetchone()[0]
    for place in (unlocated, 2_000_000_000):
        q = BlocksQuery.model_validate({"flat_type": "4 ROOM", "budget": "2000000", "place": str(place)})
        f = block_filter(q, window_ending(latest))
        for repo in repos(pg_conn, mongo_db):
            assert repo.blocks_fit(f) == BlockFitPage(total=0, rows=[])
