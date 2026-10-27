"""Input rules and flash messages for saved blocks (task 3.2), with no database."""

import pytest

from app.repos.dto import Outcome, RolledBack
from app.services.saved import (
    NOTE_MAX,
    clean_note,
    notice_from,
    notice_key,
    parse_building_id,
    rolled_back_params,
)


def test_clean_note_trims_and_turns_blank_into_none():
    assert clean_note("  near the MRT \n") == "near the MRT"
    assert clean_note("") is None
    assert clean_note(" \t\n ") is None


def test_clean_note_allows_500_characters_and_rejects_501():
    assert clean_note("x" * NOTE_MAX) == "x" * NOTE_MAX
    with pytest.raises(ValueError):
        clean_note("x" * (NOTE_MAX + 1))
    assert clean_note(" " + "x" * NOTE_MAX + " ") == "x" * NOTE_MAX  # the limit applies after trimming


@pytest.mark.parametrize("raw,expected", [("1", 1), (" 42 ", 42), ("2147483647", 2**31 - 1)])
def test_building_ids_in_range(raw, expected):
    assert parse_building_id(raw) == expected


@pytest.mark.parametrize("raw", ["", "0", "-1", "1.5", "abc", "2147483648", "9" * 40, "١", "1e3", "+1"])
def test_bad_building_ids(raw):
    assert parse_building_id(raw) is None


def test_every_outcome_message_round_trips_through_the_query_string():
    for action, outcomes in {
        "save": [Outcome.OK, Outcome.ALREADY_SAVED, Outcome.NO_SUCH_BUILDING, Outcome.CAP_REACHED],
        "edit": [Outcome.OK, Outcome.NOT_SAVED],
        "remove": [Outcome.OK, Outcome.NOT_SAVED],
    }.items():
        for outcome in outcomes:
            found = notice_from({"notice": notice_key(action, outcome)}, "pg")
            assert found is not None and found.text
            assert found.kind == ("ok" if outcome == Outcome.OK else "bad")


def test_unknown_or_missing_keys_give_no_message_and_text_is_never_echoed():
    assert notice_from({}, "pg") is None
    assert notice_from({"notice": "<script>alert(1)</script>"}, "pg") is None
    assert notice_from({"notice": "save_rolled_back", "insert": "x", "update": "1"}, "pg") is None
    assert notice_from({"notice": "save_rolled_back"}, "mongo") is None


def test_rollback_messages_report_the_in_transaction_counts():
    pg = notice_from(rolled_back_params(RolledBack(insert=1, update=1)), "pg")
    assert pg is not None and "inserted 1 row into saved_building and updated 1 row of buyer" in pg.text
    mongo = notice_from(rolled_back_params(RolledBack(matched=1)), "mongo")
    assert mongo is not None and "matched 1 buyer document" in mongo.text
