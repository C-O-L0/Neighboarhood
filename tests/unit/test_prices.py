"""The price check's query model, verdict and comparables baseline (app/services/prices.py)."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.repos.dto import Comparable
from app.services.prices import (
    STOREY_BANDS,
    PriceQuery,
    baseline,
    error_messages,
    lower_median,
    storey_value,
    verdict,
    window_ending,
)

VALID = {"place_text": "186 bishan st 13", "flat_type": "4 ROOM", "area": "93", "price": "650,000"}


def sale(txn_id: int, price: str, area: str = "100", storey_min: int = 4, lease_year: int | None = 1990):
    return Comparable(
        txn_id, date(2026, 1, 1), "1", "X ST", storey_min, storey_min + 2, Decimal(area), Decimal(price), lease_year
    )


def errors(values: dict) -> list[str]:
    with pytest.raises(ValidationError) as exc:
        PriceQuery.model_validate(values)
    return error_messages(exc.value)


def test_window_is_24_months_ending_at_the_data_month():
    w = window_ending(date(2026, 9, 1))
    assert (w.start, w.end) == (date(2024, 10, 1), date(2026, 9, 1))


def test_storey_bands_are_hdb_three_storey_bands_up_to_51():
    assert STOREY_BANDS[0] == (1, 3) and STOREY_BANDS[-1] == (49, 51) and len(STOREY_BANDS) == 17
    assert storey_value((4, 6)) == "04-06"


def test_a_valid_query_is_typed():
    q = PriceQuery.model_validate({**VALID, "storey": "04-06", "place": "186"})
    assert q.storey == (4, 6) and q.area == Decimal("93") and q.price == Decimal("650000") and q.place == 186


def test_blank_storey_means_any_storey():
    assert PriceQuery.model_validate({**VALID, "storey": " "}).storey is None


@pytest.mark.parametrize(
    "change,message",
    [
        ({"place_text": ""}, "Enter the block and street, such as 186 Bishan St 13."),
        ({"flat_type": "PENTHOUSE"}, "Flat type must be one of the listed flat types."),
        ({"storey": "05-07"}, "Storey must be one of the listed storey bands."),
        ({"storey": "52-54"}, "Storey must be one of the listed storey bands."),
        ({"area": "9"}, "Floor area must be a number of square metres from 10 to 300, such as 93 or 92.5."),
        ({"area": "92.55"}, "Floor area must be a number of square metres from 10 to 300, such as 93 or 92.5."),
        ({"price": "65,00,000"}, "Asking price must be an amount in dollars such as 650,000."),
        ({"price": "999"}, "Asking price must be an amount in dollars such as 650,000."),
        ({"place": "0"}, "Choose the block from the list."),
    ],
)
def test_bad_values_have_one_message_each(change, message):
    assert errors({**VALID, **change}) == [message]


def test_missing_area_and_price_are_named():
    assert errors({"place_text": "1 x st", "flat_type": "4 ROOM"}) == [
        "Floor area is required.",
        "Asking price is required.",
    ]


def test_lower_median_is_percentile_disc():
    assert lower_median([3, 1, 2]) == 2
    assert lower_median([4, 1, 3, 2]) == 2
    assert lower_median([7]) == 7


def test_verdict_counts_by_price_per_sqm_without_rounding():
    # 100 sqm sales at $6,000, $7,000 and $8,000 per sqm; the asking price is $7,000 per sqm on 90 sqm.
    sales = [sale(1, "600000"), sale(2, "700000"), sale(3, "800000")]
    v = verdict(sales, Decimal("90"), Decimal("630000"))
    assert (v.n, v.below, v.above) == (3, 1, 1)  # the tie counts as neither
    assert (v.low, v.median, v.high, v.asking_psm) == (Decimal("6000.00"), Decimal("7000.00"), Decimal("8000.00"), 7000)
    assert v.share_below == v.share_above == 33


def test_a_hair_above_a_sale_counts_as_higher_even_when_both_round_alike():
    # $700,000.01 / 100 sqm rounds to the same $7,000.00 per sqm as the sale, but is higher.
    v = verdict([sale(1, "700000")], Decimal("100"), Decimal("700000.01"))
    assert (v.below, v.above) == (1, 0) and v.asking_psm == v.low


def test_position_on_the_range_bar_is_clamped():
    v = verdict([sale(1, "600000"), sale(2, "800000")], Decimal("100"), Decimal("1000000"))
    assert v.position(v.low) == 0 and v.position(v.high) == 100 and v.position(v.asking_psm) == 100
    assert v.position(Decimal("7000")) == 50
    single = verdict([sale(1, "600000")], Decimal("100"), Decimal("500000"))
    assert single.position(single.asking_psm) == 50  # no range to place it on


def test_typical_price_per_sqm_breaks_ties_on_txn_id():
    sales = [sale(5, "700000"), sale(2, "700000"), sale(9, "600000"), sale(1, "800000")]
    assert verdict(sales, Decimal("100"), Decimal("1")).median == Decimal("7000.00")


def test_baseline_from_the_comparables():
    sales = [sale(1, "1", "90", 1, None), sale(2, "1", "100", 7, 1990), sale(3, "1", "110", 10, 2000)]
    b = baseline(sales, "BISHAN")
    assert (b.town, b.n, b.storey_sum, b.lease_year, b.area_median) == ("BISHAN", 3, 16, 1990, Decimal("100"))
    assert baseline([sale(1, "1", lease_year=None)], "X").lease_year is None
