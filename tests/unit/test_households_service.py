"""Household form rules, messages and the sign-in return path, with no database (task T12)."""

from datetime import UTC, datetime

import pytest

from app.repos.dto import AcceptResult, HouseholdOutcome
from app.services.auth import login_link, return_path
from app.services.households import COULD_NOT_INVITE, accepted_params, clean_email, notice_from, parse_buyer_id
from app.templating import day


@pytest.mark.parametrize(
    "raw,expected",
    [(" Alex@Example.com ", "Alex@Example.com"), ("a@b", "a@b"), ("", None), ("no-at", None), ("@x.com", None),
     ("a@", None), ("a b@x.com", None), ("a@x.com\n", "a@x.com"), ("a\tb@x.com", None), ("a" * 250 + "@x.com", None)],
)  # fmt: skip
def test_clean_email(raw, expected):
    assert clean_email(raw) == expected


@pytest.mark.parametrize("raw,expected", [("12", 12), (" 7 ", 7), ("0", None), ("-1", None), ("x", None),
                                          ("2147483648", None), ("١٢", None)])  # fmt: skip
def test_parse_buyer_id(raw, expected):
    assert parse_buyer_id(raw) == expected


def test_accept_messages_count_what_was_merged_and_left_out():
    def text(result):
        return notice_from(accepted_params(result)).text

    ok = HouseholdOutcome.OK
    assert text(AcceptResult(ok)) == "You now share a shortlist."
    assert text(AcceptResult(ok, 1, 0)) == "You now share a shortlist. 1 block of yours was added."
    assert text(AcceptResult(ok, 0, 2)) == "You now share a shortlist. 2 blocks of yours were left out."
    assert text(AcceptResult(ok, 3, 2)) == (
        "You now share a shortlist. 3 blocks of yours were added. 2 blocks of yours were left out because the"
        " shortlist was full."
    )
    assert notice_from(accepted_params(AcceptResult(HouseholdOutcome.NO_INVITATION))).kind == "bad"
    assert notice_from({"notice": "accept_ok", "merged": "x", "left_out": "0"}) is None


def test_an_unknown_email_and_a_taken_partner_read_the_same():
    a = notice_from({"notice": "invite_not_found"})
    b = notice_from({"notice": "invite_partner_in_household"})
    assert a == b and a.text == COULD_NOT_INVITE
    assert notice_from({"notice": "<script>"}) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/household", "/household"),
        ("/block/5?db=mongo&at=2040", "/block/5?at=2040"),
        ("/price-check?place=1&flat_type=4+ROOM", "/price-check?place=1&flat_type=4+ROOM"),
        ("//evil.example", None),
        ("/\\evil", None),
        ("https://evil.example/x", None),
        ("household", None),
        ("/a b", None),
        ("/x\n", None),
        ("/" + "a" * 400, None),
        (None, None),
    ],
)
def test_return_path_keeps_local_paths_only(raw, expected):
    assert return_path(raw) == expected


def test_login_link_carries_the_page():
    assert login_link("/block/5") == "/login?next=%2Fblock%2F5"
    assert login_link("/price-check", "place=1") == "/login?next=%2Fprice-check%3Fplace%3D1"


def test_day_is_singapore_time():
    assert day(datetime(2026, 10, 20, 17, 0, tzinfo=UTC)) == "21 Oct 2026"  # 1 am the next day in Singapore
    assert day(None) == "Unknown"
