"""The block profile's summary sentence, headline flat type and lease check (app/services/block.py)."""

from decimal import Decimal

import pytest

from app.repos.dto import BlockFoodAccess, BuildingDetail, BuildingStats, FlatTypeRecent
from app.services.block import LeaseCheck, headline, lease_check, lease_left, summary

NOW = 2026


def detail(year_completed=1986, lease_year=1987) -> BuildingDetail:
    return BuildingDetail(
        building_id=1, block="186", street="BISHAN ST 13", town="BISHAN", town_code="BS", in_property_dataset=True,
        year_completed=year_completed, lease_commence_year=lease_year, max_floor=11, land_use=None,
        latitude=None, longitude=None, supply=None, total_units=None,
        stats=BuildingStats(0, None, None, None, []), recent_sales=[],
    )  # fmt: skip


def food(within: int) -> BlockFoodAccess:
    return BlockFoodAccess(1, "186", "BISHAN ST 13", [], within)


def recent(flat_type: str, n: int) -> FlatTypeRecent:
    return FlatTypeRecent(flat_type, n, Decimal("500000"), Decimal("5000"), 10, Decimal("90"))


def test_lease_left_counts_99_years_from_the_lease_start_year():
    assert lease_left(1987, 2026) == 60
    assert lease_left(1987, 2086) == 0
    assert lease_left(1987, 2090) == -4


def test_summary_names_age_lease_and_food():
    assert (
        summary(detail(), food(0), NOW)
        == "Completed 1986, about 60 years of lease left, 0 open hawker centres within 1 km."
    )
    assert summary(detail(), food(1), NOW).endswith("1 open hawker centre within 1 km.")
    assert summary(detail(lease_year=2026 - 98), food(2), NOW).startswith("Completed 1986, about 1 year of lease left")


def test_summary_without_figures_still_reads_as_a_sentence():
    text = summary(detail(year_completed=None, lease_year=None), None, NOW)
    assert text == "Location not available, so no food access figures."
    assert "lease has run out" in summary(detail(lease_year=1900), food(3), NOW)


def test_headline_is_the_most_sold_flat_type_first_in_order_on_ties():
    assert headline([]) is None
    assert headline([recent("3 ROOM", 2), recent("4 ROOM", 7), recent("5 ROOM", 7)]).flat_type == "4 ROOM"


def test_no_lease_check_asked_is_neither_a_result_nor_an_error():
    assert lease_check({}, 1987, NOW) == (None, [])
    assert lease_check({"at": " ", "min_left": ""}, 1987, NOW) == (None, [])


def test_a_year_gives_the_lease_left_then():
    check, errors = lease_check({"at": "2040"}, 1987, NOW)
    assert errors == [] and check == LeaseCheck(year=2040, left=46)
    assert not check.expired and not check.below


def test_ages_give_the_year_and_win_over_a_year():
    check, errors = lease_check({"at": "2030", "age": "30", "at_age": "65", "min_left": "30"}, 1987, NOW)
    assert errors == [] and check == LeaseCheck(year=2061, left=25, age=65, min_left=30)
    assert check.below


def test_a_year_past_the_lease_end_has_expired():
    check, _ = lease_check({"at": "2100", "min_left": "20"}, 1987, NOW)
    assert check.left == -14 and check.expired


def test_the_threshold_is_not_below_when_equal():
    check, _ = lease_check({"at": "2056", "min_left": "30"}, 1987, NOW)
    assert check.left == 30 and not check.below


@pytest.mark.parametrize(
    "values,message",
    [
        ({"at": "2025"}, "Year must be from 2026 to 2146."),
        ({"at": "2147"}, "Year must be from 2026 to 2146."),
        ({"at": "20x0"}, "Year must be from 2026 to 2146."),
        ({"at": "٢٠٤٠"}, "Year must be from 2026 to 2146."),  # non-ASCII digits
        ({"age": "30"}, "Enter both ages as whole numbers from 1 to 120."),
        ({"age": "30", "at_age": "121"}, "Enter both ages as whole numbers from 1 to 120."),
        ({"age": "40", "at_age": "40"}, "The age to check must be older than your age now."),
        ({"min_left": "30"}, "Enter a year, or your age now and the age to check."),
        ({"at": "2040", "min_left": "99"}, "Least lease left must be a whole number of years from 1 to 98."),
    ],
)
def test_bad_input_gives_one_message_and_no_result(values, message):
    check, errors = lease_check(values, 1987, NOW)
    assert check is None and errors == [message]
