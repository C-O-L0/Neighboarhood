"""Explore without a database: money input, the query model, the window and the best-fit rule."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from bson.decimal128 import Decimal128
from pydantic import ValidationError

from app.repos.dto import TownFit
from app.repos.mongo.explore import blocks_near_pipeline, blocks_pipeline, towns_pipeline
from app.services.explore import (
    BlocksQuery,
    FitQuery,
    Window,
    best_fit,
    block_filter,
    closest,
    error_messages,
    lease_left_years,
    parse_money,
    window_ending,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("650000", Decimal("650000")),
        ("650,000", Decimal("650000")),
        ("$650,000", Decimal("650000")),
        ("$ 1,250,000.50", Decimal("1250000.50")),
        (" 700000 ", Decimal("700000")),
        ("1,2345", None),
        ("65,0000", None),
        ("650k", None),
        ("-650000", None),
        ("650000.123", None),
        ("", None),
    ],
)
def test_parse_money(text, expected):
    assert parse_money(text) == expected


def test_fit_query_accepts_a_typed_budget():
    q = FitQuery.model_validate({"flat_type": " 4 ROOM ", "budget": "650,000"})
    assert q.flat_type == "4 ROOM" and q.budget == Decimal("650000")


@pytest.mark.parametrize(
    "data,message",
    [
        ({"budget": "650000"}, "Flat type is required."),
        ({"flat_type": "", "budget": "650000"}, "Flat type is required."),
        ({"flat_type": "PENTHOUSE", "budget": "650000"}, "Flat type must be one of the listed flat types."),
        ({"flat_type": "4 ROOM"}, "Budget is required."),
        ({"flat_type": "4 ROOM", "budget": "0"}, "Budget must be an amount in dollars such as 650,000."),
        ({"flat_type": "4 ROOM", "budget": "abc"}, "Budget must be an amount in dollars such as 650,000."),
        ({"flat_type": "4 ROOM", "budget": "10,000,001"}, "Budget must be an amount in dollars such as 650,000."),
    ],
)
def test_fit_query_errors_are_plain(data, message):
    with pytest.raises(ValidationError) as exc:
        FitQuery.model_validate(data)
    assert error_messages(exc.value) == [message]


def test_window_is_twelve_months_ending_at_the_latest():
    assert window_ending(date(2026, 9, 1)) == Window(date(2025, 10, 1), date(2026, 9, 1))
    assert window_ending(date(2026, 1, 1)) == Window(date(2025, 2, 1), date(2026, 1, 1))
    assert window_ending(date(2026, 12, 1)) == Window(date(2026, 1, 1), date(2026, 12, 1))


def town(name, n, median, within):
    return TownFit(name, n, Decimal(median), within)


def test_share_within_rounds_half_up():
    assert town("A", 8, "1", 1).share_within == 13  # 12.5
    assert town("A", 37, "1", 20).share_within == 54  # 54.05
    assert town("A", 3, "1", 0).share_within == 0
    assert town("A", 3, "1", 3).share_within == 100


def test_best_fit_is_the_fitting_town_most_often_within_budget():
    towns = [town("C", 10, "500000", 9), town("A", 37, "645000", 20), town("B", 186, "788000", 26)]
    assert best_fit(towns, Decimal("650000")).town == "C"
    assert best_fit(towns, Decimal("400000")) is None
    # equal shares: the lower typical price, then the name
    tied = [town("Y", 10, "600000", 5), town("X", 20, "600000", 10), town("Z", 4, "550000", 2)]
    assert best_fit(tied, Decimal("650000")).town == "Z"
    assert best_fit(tied[:2], Decimal("650000")).town == "X"


def test_closest_are_the_towns_least_over_budget():
    towns = [town("A", 5, "600000", 5), town("B", 5, "700000", 1), town("C", 5, "800000", 0), town("D", 5, "900000", 0),
             town("E", 5, "950000", 0)]  # fmt: skip
    assert [t.town for t in closest(towns, Decimal("650000"))] == ["B", "C", "D"]
    assert closest(towns[:1], Decimal("650000")) == []


def test_towns_pipeline_uses_only_typed_values():
    window = Window(date(2025, 10, 1), date(2026, 9, 1))
    pipeline = towns_pipeline("4 ROOM", Decimal("650000"), window)
    match = pipeline[0]["$match"]
    assert match == {
        "flat_type": "4 ROOM",
        "txn_month": {"$gte": datetime(2025, 10, 1, tzinfo=UTC), "$lte": datetime(2026, 9, 1, tzinfo=UTC)},
    }
    within = pipeline[1]["$group"]["within_budget"]["$sum"]["$cond"][0]["$lte"]
    assert within == ["$resale_price", Decimal128("650000")]


# ---------------------------------------------------------------- blocks that fit (T5)

WINDOW = Window(date(2025, 10, 1), date(2026, 9, 1))


def test_blocks_query_defaults_and_filter():
    q = BlocksQuery.model_validate({"flat_type": "4 ROOM", "budget": "650,000"})
    f = block_filter(q, WINDOW)
    assert (f.town, f.place, f.radius_m, f.min_lease_year, f.min_area, f.page, f.offset) == (
        None, None, 2000, None, None, 1, 0,
    )  # fmt: skip


def test_blocks_query_reads_every_filter():
    q = BlocksQuery.model_validate(
        {"flat_type": "4 ROOM", "budget": "650000", "town": " BISHAN ", "place": "307", "radius": "5000",
         "min_lease": "60", "min_area": "90.5", "page": "3"}
    )  # fmt: skip
    f = block_filter(q, WINDOW)
    assert (f.town, f.place, f.radius_m, f.min_area, f.page, f.offset) == ("BISHAN", 307, 5000, Decimal("90.5"), 3, 40)
    assert f.min_lease_year == 2026 + 60 - 99  # lease must start in 1987 or later to have 60 years left in 2026


def test_lease_left_years():
    assert lease_left_years(1975, date(2026, 9, 1)) == 48
    assert lease_left_years(2026, date(2026, 9, 1)) == 99


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("town", "ATLANTIS", "Town must be one of the listed towns."),
        ("place", "abc", "Choose the place from the list of blocks."),
        ("place", "0", "Choose the place from the list of blocks."),
        ("place", "2147483648", "Choose the place from the list of blocks."),
        ("radius", "1500", "Distance must be 1, 2 or 5 km."),
        ("min_lease", "99", "Lease left must be a whole number of years from 1 to 98."),
        ("min_lease", "6.5", "Lease left must be a whole number of years from 1 to 98."),
        ("min_area", "0", "Floor area must be a number of square metres from 1 to 300."),
        ("min_area", "301", "Floor area must be a number of square metres from 1 to 300."),
        ("min_area", "90.55", "Floor area must be a number of square metres from 1 to 300."),
        ("page", "0", "Page must be a whole number from 1 to 10000."),
    ],
)
def test_blocks_query_errors_are_plain(field, value, message):
    with pytest.raises(ValidationError) as exc:
        BlocksQuery.model_validate({"flat_type": "4 ROOM", "budget": "650000", field: value})
    assert error_messages(exc.value) == [message]


def test_blocks_pipelines_use_only_typed_values():
    q = BlocksQuery.model_validate({"flat_type": "4 ROOM", "budget": "650000", "town": "BISHAN", "min_area": "90"})
    f = block_filter(q, WINDOW)
    pipeline = blocks_pipeline(f)
    assert pipeline[0]["$match"]["town"] == "BISHAN" and pipeline[0]["$match"]["flat_type"] == "4 ROOM"
    budget = next(s for s in pipeline if "$match" in s and "median_price" in s["$match"])["$match"]
    assert budget == {"median_price": {"$lte": Decimal128("650000")}, "area_median": {"$gte": Decimal128("90")}}
    near = blocks_near_pipeline(f, {"type": "Point", "coordinates": [103.9, 1.3]})
    assert near[0]["$geoNear"]["maxDistance"] == 2000 and near[0]["$geoNear"]["query"] == {"town.name": "BISHAN"}
