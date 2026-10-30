"""Explainer tags without a database: each factor's thresholds, the fixed order and when nothing is said."""

from decimal import Decimal

import pytest

from app.repos.dto import TownBaseline
from app.services.explain import Factor, Subject, tags

# Typical recent sale: storeys 07 to 09 (sum 16), lease from 1990, 100 sqm, from 20 sales.
BASE = TownBaseline(town="BISHAN", n=20, storey_sum=16, lease_year=1990, area_median=Decimal("100.0"))


def labels(subject, baseline=BASE):
    result = tags(subject, baseline)
    return None if result is None else [t.label for t in result]


def test_a_typical_subject_has_no_tags():
    assert labels(Subject(storey_sum=16, lease_year=1990, area=Decimal("100.0"))) == []


@pytest.mark.parametrize(
    "storey_sum,expected",
    [
        (4, ["Low floor"]),  # storeys 01 to 03
        (10, ["Low floor"]),  # 3 * 10 = 30 <= 32: a third or more below
        (11, []),  # 33 > 32
        (21, []),  # 63 < 64
        (22, ["High floor"]),  # 66 >= 64: a third or more above
        (40, ["High floor"]),
    ],
)
def test_floor_is_a_third_or_more_from_the_typical_storey(storey_sum, expected):
    assert labels(Subject(storey_sum=storey_sum)) == expected


@pytest.mark.parametrize(
    "lease_year,expected",
    [(1980, ["Shorter lease"]), (1981, []), (1999, []), (2000, ["Longer lease"]), (1970, ["Shorter lease"])],
)
def test_lease_is_ten_or_more_years_apart(lease_year, expected):
    assert labels(Subject(lease_year=lease_year)) == expected


@pytest.mark.parametrize(
    "area,expected",
    [("90.0", ["Smaller than usual"]), ("90.1", []), ("109.9", []), ("110.0", ["Larger than usual"])],
)
def test_size_is_ten_percent_or_more_apart(area, expected):
    assert labels(Subject(area=Decimal(area))) == expected


def test_tags_come_in_a_fixed_order_with_their_direction():
    result = tags(Subject(storey_sum=40, lease_year=1975, area=Decimal("80.0")), BASE)
    assert [(t.factor, t.label, t.higher) for t in result] == [
        (Factor.FLOOR, "High floor", True),
        (Factor.LEASE, "Shorter lease", False),
        (Factor.SIZE, "Smaller than usual", False),
    ]


def test_unknown_values_are_not_compared():
    assert labels(Subject()) == []
    no_lease = TownBaseline(town="X", n=20, storey_sum=16, lease_year=None, area_median=Decimal("100.0"))
    assert labels(Subject(lease_year=1950), no_lease) == []


def test_nothing_is_said_without_enough_sales():
    few = TownBaseline(town="X", n=4, storey_sum=16, lease_year=1990, area_median=Decimal("100.0"))
    assert labels(Subject(storey_sum=4), few) is None
    assert labels(Subject(storey_sum=4), None) is None
    enough = TownBaseline(town="X", n=5, storey_sum=16, lease_year=1990, area_median=Decimal("100.0"))
    assert labels(Subject(storey_sum=4), enough) == ["Low floor"]
