import pytest

from etl.streets import expand_street, normalise_key, same_street


@pytest.mark.parametrize(
    ("hdb", "full"),
    [
        ("ANG MO KIO AVE 10", "ANG MO KIO AVENUE 10"),
        ("TEBAN GDNS RD", "TEBAN GARDENS ROAD"),
        ("BT BATOK WEST AVE 5", "BUKIT BATOK WEST AVENUE 5"),
        ("C'WEALTH CRES", "COMMONWEALTH CRESCENT"),
        ("JLN KUKOH", "JALAN KUKOH"),
        ("ST. GEORGE'S RD", "SAINT GEORGE'S ROAD"),
        ("UPP BOON KENG RD", "UPPER BOON KENG ROAD"),
    ],
)
def test_expand_street(hdb, full):
    assert expand_street(hdb) == full


def test_normalise_key_collapses_whitespace_and_case():
    assert normalise_key("  38a  bedok  nth rd ") == "38A BEDOK NTH RD"


def test_same_street_ignores_punctuation():
    assert same_street("ST. GEORGE'S RD", "SAINT GEORGES ROAD")
    assert not same_street("BEDOK NTH RD", "BEDOK SOUTH ROAD")
