"""Places without a database: reading typed places, the radii and the picker pipeline."""

import pytest

from app.repos.mongo.places import find_pipeline
from app.services.places import PlaceText, parse_place, parse_radius
from app.services.search import SearchQuery
from etl.streets import abbreviate_street, expand_street


@pytest.mark.parametrize(
    "text,expected",
    [
        ("17 marine terrace", "17 MARINE TER"),
        ("Bishan Street 13", "BISHAN ST 13"),
        ("commonwealth crescent", "C'WEALTH CRES"),
        ("saint george's road", "ST. GEORGE'S RD"),
        ("ANG MO KIO AVE 10", "ANG MO KIO AVE 10"),
        ("  upper  boon keng road ", "UPP BOON KENG RD"),
        ("bukit batok west avenue 5", "BT BATOK WEST AVE 5"),
        ("", ""),
    ],
)
def test_abbreviate_street(text, expected):
    assert abbreviate_street(text) == expected


@pytest.mark.parametrize("street", ["ANG MO KIO AVE 10", "TEBAN GDNS RD", "C'WEALTH CRES", "ST. GEORGE'S RD"])
def test_abbreviating_an_expanded_street_gives_it_back(street):
    assert abbreviate_street(expand_street(street)) == street


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Blk 186 Bishan Street 13", PlaceText("186", "BISHAN ST 13")),
        ("block 17 marine terrace", PlaceText("17", "MARINE TER")),
        ("273a bishan st 24", PlaceText("273A", "BISHAN ST 24")),
        ("bishan street 13", PlaceText(None, "BISHAN ST 13")),
        ("10", PlaceText(None, "10")),  # a lone number is part of a street, such as "AVE 10"
        ("12345 marine ter", PlaceText(None, "12345 MARINE TER")),  # not a block number
    ],
)
def test_parse_place(text, expected):
    assert parse_place(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "blk", "x" * 61, "bishan\x00st"])
def test_parse_place_refuses(text):
    assert parse_place(text) is None


def test_parse_radius_accepts_only_the_offered_radii():
    assert [parse_radius(t) for t in ("1000", "2000", "5000")] == [1000, 2000, 5000]
    assert [parse_radius(t) for t in ("", "1500", "2km", "-1000", " 2000")] == [None] * 5


def test_search_street_accepts_full_words():
    assert SearchQuery.model_validate({"street": "marine terrace"}).street == "MARINE TER"
    assert SearchQuery.model_validate({"street": "queen's"}).street == "QUEEN'S"


def test_picker_pipeline_escapes_the_street():
    match = find_pipeline("17", "A.B(C)", 8)[0]["$match"]
    assert match == {"street": {"$regex": r"A\.B\(C\)"}, "block": "17"}
    assert "block" not in find_pipeline(None, "X", 8)[0]["$match"]
