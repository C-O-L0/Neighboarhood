"""Compare's input rules, default flat type and row leaders (app/services/compare.py)."""

from decimal import Decimal

import pytest

from app.repos.dto import BlockFoodAccess, BuildingDetail, BuildingStats, FlatTypeRecent
from app.services.compare import best, column, default_flat_type, parse_flat_type, parse_ids, parse_year


@pytest.mark.parametrize(
    "raw,expected",
    [
        (["1", "2"], [1, 2]),
        (["3,1", "2"], [3, 1, 2]),
        (["1", "1", "2"], [1, 2]),  # a repeat counts once
        (["1"], None),
        (["1", "1"], None),
        (["1", "2", "3", "4", "5"], None),
        (["1", "x"], None),
        (["1", "0"], None),
        (["1", "2147483648"], None),
        (["1", "", "2"], [1, 2]),
        ([], None),
    ],
)
def test_parse_ids(raw, expected):
    assert parse_ids(raw) == expected


def test_parse_flat_type_and_year():
    assert parse_flat_type("4 ROOM") == "4 ROOM" and parse_flat_type("4 room") is None
    assert parse_year("2040", 2026) == 2040 and parse_year("2025", 2026) is None
    assert parse_year("2147", 2026) is None and parse_year("20x0", 2026) is None


def recent(flat_type, n, price="500000", psm="5000.40"):
    return FlatTypeRecent(flat_type, n, Decimal(price), Decimal(psm), 10, Decimal("90"))


def test_default_flat_type_is_the_most_sold_across_blocks():
    rows = [[recent("4 ROOM", 3), recent("5 ROOM", 4)], [recent("4 ROOM", 2)], []]
    assert default_flat_type(rows) == "4 ROOM"
    assert default_flat_type([[recent("5 ROOM", 2)], [recent("4 ROOM", 2)]]) == "4 ROOM"  # tie: list order
    assert default_flat_type([[], []]) is None


def block(i, lease_year=1990):
    return BuildingDetail(
        building_id=i, block=str(i), street="X ST", town="BISHAN", town_code="BS", in_property_dataset=True,
        year_completed=1989, lease_commence_year=lease_year, max_floor=10, land_use=None, latitude=None,
        longitude=None, supply=None, total_units=None, stats=BuildingStats(0, None, None, None, []), recent_sales=[],
    )  # fmt: skip


def food(i, within):
    return BlockFoodAccess(i, str(i), "X ST", [], within)


def test_leaders_per_row_and_none_when_all_equal_or_alone():
    cols = [
        column(block(1, 1990), food(1, 2), recent("4 ROOM", 5, "600000", "6000.49"), None, 2026, 2040),
        column(block(2, 2000), food(2, 2), recent("4 ROOM", 5, "550000", "6000.50"), None, 2026, None),
        column(block(3, 2000), None, None, None, 2026, None),
    ]
    marks = best(cols)
    assert marks["price"] == {2}
    assert marks["psm"] == {1}  # $6,000 against $6,001 as the page shows them
    assert marks["lease"] == {2, 3}
    assert marks["food"] == set()  # 2 and 2: nobody leads
    assert cols[0].lease_now == 63 and cols[0].lease_then == 49 and cols[1].lease_then is None
    assert best(cols[2:])["lease"] == set()
