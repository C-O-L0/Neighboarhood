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


@pytest.mark.parametrize(
    ("hdb", "onemap"),
    [
        ("ST. GEORGE'S RD", "ST. GEORGE'S ROAD"),
        ("ANG MO KIO AVE 10", "ANG MO KIO AVENUE 10"),
        ("TEBAN GDNS RD", "TEBAN GARDENS ROAD"),
        ("JLN BT MERAH", "JALAN BUKIT MERAH"),
    ],
)
def test_same_street_matches_onemap_road_names(hdb, onemap):
    assert same_street(hdb, onemap)


def test_read_cache_drops_torn_last_line(tmp_path):
    from etl.geocode import FIELDS, read_cache

    out = tmp_path / "c.csv"
    good = "1,A RD,1.3,103.8,123456,addr,exact,1 A RD\n"
    out.write_text(",".join(FIELDS) + "\n" + good + "2,B RD,1.3")
    rows = read_cache(out)
    assert len(rows) == 1
