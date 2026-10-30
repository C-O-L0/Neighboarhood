"""The admin record form and delete id: every bound is checked before any quantize and before any database."""

from datetime import date
from decimal import Decimal

import pytest

from app.services.admin import MAX_TXN_ID, parse_sale_form, parse_txn_id

GOOD = {
    "block": " 18 ", "street": "marine   ter", "month": "2024-03", "flat_type": "4 ROOM", "flat_model": "Improved",
    "storey_min": "3", "storey_max": "5", "floor_area_sqm": "93.5", "remaining_lease_months": "700",
    "resale_price": "612345.67",
}  # fmt: skip


def parse(**overrides):
    return parse_sale_form({**GOOD, **overrides})


def test_a_good_form_is_normalised_and_quantized():
    form, errors = parse(resale_price="700000.5", floor_area_sqm="100", street="marine ter")
    assert errors == []
    assert (form.block, form.street, form.month) == ("18", "MARINE TER", date(2024, 3, 1))
    assert form.resale_price == Decimal("700000.50") and str(form.resale_price) == "700000.50"
    assert form.floor_area_sqm == Decimal("100.0") and str(form.floor_area_sqm) == "100.0"
    sale = form.new_sale(7)
    assert (sale.building_id, sale.storey_min, sale.storey_max, sale.remaining_lease_months) == (7, 3, 5, 700)


def test_the_boundaries_just_inside_are_accepted():
    form, errors = parse(
        resale_price="9999999.99",
        floor_area_sqm="399.9",
        storey_min="1",
        storey_max="60",
        remaining_lease_months="1188",
    )
    assert errors == [] and form.resale_price == Decimal("9999999.99")
    form, errors = parse(resale_price="0.01", floor_area_sqm="0.1", remaining_lease_months="0", month="1900-01")
    assert errors == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"resale_price": "612345.678"},
        {"floor_area_sqm": "93.55"},
        {"floor_area_sqm": "400"},
        {"floor_area_sqm": "0"},
        {"resale_price": "10000000"},
        {"resale_price": "0"},
        {"resale_price": "NaN"},
        {"resale_price": "-Infinity"},
        {"resale_price": "Infinity"},
        {"resale_price": "1E+30"},
        {"resale_price": "1e3"},
        {"resale_price": "-5"},
        {"resale_price": "5" * 30},
        {"resale_price": "٣"},  # a non-ASCII digit
        {"floor_area_sqm": "sNaN"},
        {"storey_min": "6", "storey_max": "5"},
        {"storey_min": "0"},
        {"storey_max": "61"},
        {"remaining_lease_months": "1189"},
        {"remaining_lease_months": "-1"},
        {"flat_type": "STUDIO APARTMENT"},
        {"flat_type": "OTHER"},
        {"flat_model": "Palace"},
        {"month": "2024-13"},
        {"month": "1899-12"},
        {"month": "2101-01"},
        {"block": "   "},
        {"block": "123456"},
        {"street": "X" * 41},
        {"street": "bad\x00street"},
    ],
)
def test_an_invalid_form_names_the_problem_and_never_raises(overrides):
    form, errors = parse(**overrides)
    assert form is None and errors and all(isinstance(e, str) and e for e in errors)


def test_every_blank_field_is_reported():
    form, errors = parse_sale_form({})
    assert form is None and len(errors) == 10


@pytest.mark.parametrize("raw", ["1", " 42 ", str(MAX_TXN_ID), "007"])
def test_good_sale_ids(raw):
    assert parse_txn_id(raw) == int(raw)


@pytest.mark.parametrize("raw", ["0", str(MAX_TXN_ID + 1), "-1", "", "abc", "1.5", "1e3", "1" * 20, "٣"])
def test_bad_sale_ids(raw):
    assert parse_txn_id(raw) is None
