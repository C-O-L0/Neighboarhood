from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pytest

from app.repos.dto import BuildingStats, FlatTypeStats
from tests.equivalence.compare import assert_equivalent


@dataclass(frozen=True)
class Row:
    name: str
    avg: Decimal | None = field(metadata={"tol": Decimal("0.01")})
    median: Decimal | None


def row(avg="10.00", median="5.00", name="a"):
    return Row(name, None if avg is None else Decimal(avg), None if median is None else Decimal(median))


def test_equal_values_pass():
    assert_equivalent(row(), row())


def test_tolerant_field_within_one_cent_passes():
    assert_equivalent(row(avg="10.00"), row(avg="10.01"))


def test_tolerant_field_two_cents_fails():
    with pytest.raises(AssertionError):
        assert_equivalent(row(avg="10.00"), row(avg="10.02"))


def test_non_tolerant_decimal_off_by_a_millionth_fails():
    with pytest.raises(AssertionError, match=r"root\.median"):
        assert_equivalent(row(median="5.000000"), row(median="5.000001"))


def test_coordinate_off_by_a_millionth_fails():
    a = (Decimal("1.300000"), Decimal("103.800000"))
    with pytest.raises(AssertionError):
        assert_equivalent({"lat": a[0]}, {"lat": Decimal("1.300001")})


def test_none_versus_value_fails_even_on_a_tolerant_field():
    with pytest.raises(AssertionError, match=r"root\.avg"):
        assert_equivalent(row(avg=None), row(avg="10.00"))


def test_none_equals_none():
    assert_equivalent(row(avg=None, median=None), row(avg=None, median=None))


def test_list_order_matters():
    with pytest.raises(AssertionError):
        assert_equivalent([row(name="a"), row(name="b")], [row(name="b"), row(name="a")])


def test_list_length_matters():
    with pytest.raises(AssertionError, match="lengths"):
        assert_equivalent([row()], [row(), row()])


def test_failure_message_names_the_field_path():
    def stats(median):
        return BuildingStats(
            2,
            Decimal("1"),
            Decimal("2.00"),
            date(2024, 1, 1),
            [FlatTypeStats("3 ROOM", 1, Decimal("4"), Decimal("5.00")), FlatTypeStats("4 ROOM", 1, median, None)],
        )

    with pytest.raises(AssertionError) as exc:
        assert_equivalent(stats(Decimal("9")), stats(Decimal("8")), path="building")
    assert "building.by_flat_type[1].median_price" in str(exc.value)


def test_dicts_compare_by_key_and_value():
    assert_equivalent({"a": True}, {"a": True})
    with pytest.raises(AssertionError):
        assert_equivalent({"a": True}, {"a": False})
    with pytest.raises(AssertionError):
        assert_equivalent({"a": True}, {"b": True})


def test_float_in_a_dto_is_rejected():
    with pytest.raises(AssertionError, match="float"):
        assert_equivalent(1.5, 1.5)
