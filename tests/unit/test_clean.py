"""Cleaner tests: parsers in isolation, then a full run on the committed sample."""

from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from etl import clean

SAMPLE = Path(__file__).resolve().parents[2] / "data" / "sample"


# ---------------------------------------------------------------------- parsers


@pytest.mark.parametrize(
    ("text", "months", "label"),
    [
        ("61 years 04 months", 61 * 12 + 4, "years and months"),
        ("61 years 01 month", 61 * 12 + 1, "years and 1 month"),
        ("95 years", 95 * 12, "years only"),
        ("  70 YEARS  11 MONTHS ", 70 * 12 + 11, "years and months"),
    ],
)
def test_remaining_lease_formats(text, months, label):
    assert clean.parse_remaining_lease(text) == (months, label)


@pytest.mark.parametrize("text", ["61", "61 years 12 months", "years 4 months", ""])
def test_remaining_lease_rejects_bad_values(text):
    with pytest.raises(ValueError):
        clean.parse_remaining_lease(text)


def test_storey_band():
    assert clean.parse_storey("01 TO 03") == (1, 3)
    assert clean.parse_storey("49 TO 51") == (49, 51)
    for bad in ("03 TO 01", "00 TO 03", "61 TO 63", "1-3"):
        with pytest.raises(ValueError):
            clean.parse_storey(bad)


def test_decimals_are_exact():
    assert clean.parse_decimal("888888.88", 2, "price") == Decimal("888888.88")
    assert clean.parse_decimal("232000", 2, "price") == Decimal("232000.00")
    assert clean.parse_decimal("44.00", 1, "area") == Decimal("44.0")
    with pytest.raises(ValueError):
        clean.parse_decimal("60.35", 1, "area")  # would lose a digit
    with pytest.raises(ValueError):
        clean.parse_decimal("abc", 2, "price")


def test_month_is_first_of_month():
    assert clean.parse_month("2017-01") == pd.Timestamp(2017, 1, 1)
    with pytest.raises(ValueError):
        clean.parse_month("2017-13")


def test_lookups_are_fixed():
    town, flat_type, flat_model = clean.lookups()
    assert len(town) == 27 and town.code.is_unique and town.name.is_unique
    assert list(flat_type.name[flat_type.is_resale_type]) == [
        "1 ROOM",
        "2 ROOM",
        "3 ROOM",
        "4 ROOM",
        "5 ROOM",
        "EXECUTIVE",
        "MULTI-GENERATION",
    ]
    assert len(flat_type) == 9
    assert len(flat_model) == 21 and flat_model.name.is_unique


# ---------------------------------------------------------------------- sample run


@pytest.fixture(scope="module")
def sample_run(tmp_path_factory):
    cache = tmp_path_factory.mktemp("cache")
    (cache / clean.CACHE_FILE).write_text(
        "block,street,latitude,longitude,postal_code,onemap_address,match_status,query\n"
        "82,MACPHERSON LANE,1.326000,103.886000,,,exact,82 MACPHERSON LANE\n"
        "37,TEBAN GDNS RD,1.321000,103.743000,,,exact,37 TEBAN GDNS RD\n",
        encoding="utf-8",
    )
    (cache / clean.OVERRIDES_FILE).write_text(
        "block,street,latitude,longitude,postal_code,onemap_address,match_status,query\n"
        "37,TEBAN GDNS RD,1.322000,103.744000,,,manual,\n",
        encoding="utf-8",
    )
    out = tmp_path_factory.mktemp("interim")
    tables, report, rejected = clean.clean(SAMPLE, cache)
    clean.write(tables, report, rejected, out)
    return tables, report, out


def test_sample_counts(sample_run):
    tables, report, _ = sample_run
    assert report["rejected"] == 0
    assert report["rows_out"]["resale_transaction"] == report["rows_in"]["resale"]
    assert report["rows_out"]["building"] == report["rows_in"]["property"] + 1  # plus the stub
    assert report["rows_out"]["hawker_centre"] == 129
    assert report["duplicates_kept"] > 0 and report["fractional_prices"] > 0
    assert set(report["remaining_lease_formats"]) == {"years and months", "years and 1 month", "years only"}


def test_stub_building_for_block_without_property_row(sample_run):
    tables, report, _ = sample_run
    b = tables["building"]
    stub = b[(b.block == "82") & (b.street == "MACPHERSON LANE")].iloc[0]
    assert not stub.in_property_dataset and pd.isna(stub.year_completed) and pd.isna(stub.max_floor)
    town = tables["town"].set_index("town_id").loc[stub.town_id, "name"]
    assert town == "GEYLANG"
    assert report["stub_buildings"] == [{"block": "82", "street": "MACPHERSON LANE", "town": "GEYLANG", "sales": 3}]


def test_lease_year_majority_rule(sample_run):
    tables, report, _ = sample_run
    b = tables["building"]
    teban = b[(b.block == "37") & (b.street == "TEBAN GDNS RD")].iloc[0]
    assert teban.lease_commence_year == 1966
    assert report["lease_year_anomalies"][0]["values"] == {1966: 11, 1981: 1}


def test_coordinates_from_cache_with_overrides(sample_run):
    tables, report, _ = sample_run
    b = tables["building"].set_index(["block", "street"])
    assert b.loc[("37", "TEBAN GDNS RD"), "latitude"] == Decimal("1.322000")  # override wins
    assert b.loc[("82", "MACPHERSON LANE"), "longitude"] == Decimal("103.886000")
    # both-or-neither
    assert (b.latitude.isna() == b.longitude.isna()).all()
    assert report["coordinates"]["geocoded_buildings"] == 2


def test_supply_matches_and_skips_zero_pairs(sample_run):
    tables, report, _ = sample_run
    s = tables["building_flat_supply"]
    assert ((s.sold_units + s.rental_units) > 0).all()
    assert not s.duplicated(["building_id", "flat_type_id"]).any()
    assert report["supply_total_matches_dwelling_units"] is True


def test_ids_are_deterministic_and_keys_valid(sample_run):
    tables, _, _ = sample_run
    b, t = tables["building"], tables["resale_transaction"]
    assert list(b.building_id) == list(range(1, len(b) + 1))
    assert list(b.sort_values(["street", "block"]).building_id) == list(b.building_id)
    assert list(t.txn_id) == list(range(1, len(t) + 1))
    assert t.building_id.isin(b.building_id).all()
    assert t.flat_type_id.isin(tables["flat_type"].flat_type_id).all()
    assert t.flat_model_id.isin(tables["flat_model"].flat_model_id).all()


def test_parquet_keeps_exact_money(sample_run):
    tables, _, out = sample_run
    pt = pq.read_table(out / "resale_transaction.parquet")
    assert str(pt.schema.field("resale_price").type) == "decimal128(9, 2)"
    prices = pt.column("resale_price").to_pylist()
    assert sum(p != p.to_integral_value() for p in prices) == sum(
        p != p.to_integral_value() for p in tables["resale_transaction"].resale_price
    )
    assert pq.read_table(out / "building.parquet").num_rows == len(tables["building"])
