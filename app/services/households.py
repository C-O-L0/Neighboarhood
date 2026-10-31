"""Households (UC10): input rules and the flash messages shown after an invitation, acceptance or leaving.

As for saved blocks (app/services/saved.py), a message travels in the redirect's query string as a key from the
fixed table below, plus small counts after accepting, so no free text from a request is echoed back.

An unknown email and a partner already in a household get the same message (UC10 E1): the page never says
which emails have accounts.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.repos.dto import AcceptResult, HouseholdOutcome
from app.services.saved import Notice

EMAIL_MAX = 254
CAP = 50
HO = HouseholdOutcome
MAX_ID = 2**31 - 1  # buyer.buyer_id is INT

COULD_NOT_INVITE = (
    "We could not invite that email. Check it is the email your partner registered with in this database, "
    "and that they do not already share a shortlist."
)
_TEXT = {
    ("invite", HO.OK): ("ok", "Invitation sent."),
    ("invite", HO.NOT_FOUND): ("bad", COULD_NOT_INVITE),
    ("invite", HO.PARTNER_IN_HOUSEHOLD): ("bad", COULD_NOT_INVITE),
    ("invite", HO.SELF): ("bad", "That is your own email. Enter your partner's."),
    ("invite", HO.IN_HOUSEHOLD): ("bad", "You already share a shortlist. Leave the household first."),
    ("invite", None): ("bad", "Enter your partner's email, such as alex@example.com."),
    ("cancel", HO.OK): ("ok", "Invitation cancelled."),
    ("cancel", HO.NO_INVITATION): ("bad", "There is no invitation to cancel."),
    ("decline", HO.OK): ("ok", "Invitation declined."),
    ("decline", HO.NO_INVITATION): ("bad", "That invitation is no longer open."),
    ("accept", HO.NO_INVITATION): ("bad", "That invitation is no longer open."),
    ("accept", HO.IN_HOUSEHOLD): ("bad", "You already share a shortlist, so the invitation was removed."),
    ("accept", HO.PARTNER_IN_HOUSEHOLD): (
        "bad", "They now share a shortlist with someone else, so the invitation was removed.",
    ),
    ("leave", HO.OK): ("ok", "You left the household. Your partner keeps the shortlist; yours starts empty."),
    ("leave", HO.NOT_IN_HOUSEHOLD): ("bad", "You are not in a household."),
    ("input", None): ("bad", "Choose an invitation from the list."),
}  # fmt: skip
_KEYS = {f"{action}_{outcome.value if outcome else 'invalid'}": v for (action, outcome), v in _TEXT.items()}


def notice_key(action: str, outcome: HouseholdOutcome | None) -> str:
    return f"{action}_{outcome.value if outcome else 'invalid'}"


def parse_buyer_id(raw: str) -> int | None:
    """A buyer id from a form field: plain digits in the INT range, else None."""
    value = raw.strip()
    if not value.isascii() or not value.isdigit() or len(value) > 10:
        return None
    number = int(value)
    return number if 1 <= number <= MAX_ID else None


def clean_email(raw: str) -> str | None:
    """A plausible email, trimmed; None when it is not one. The repo looks it up ignoring case."""
    email = raw.strip()
    if not email or len(email) > EMAIL_MAX or not email.isprintable() or " " in email:
        return None
    local, at, domain = email.partition("@")
    return email if at and local and domain else None


def accepted_params(result: AcceptResult) -> dict[str, str]:
    if result.outcome != HO.OK:
        return {"notice": notice_key("accept", result.outcome)}
    return {"notice": "accept_ok", "merged": str(result.merged), "left_out": str(result.left_out)}


def _count(params: Mapping[str, str], name: str) -> int | None:
    raw = params.get(name, "")
    return int(raw) if raw.isascii() and raw.isdigit() and len(raw) <= 3 else None


def blocks(n: int) -> str:
    return f"{n} block{'' if n == 1 else 's'}"


def notice_from(params: Mapping[str, str]) -> Notice | None:
    key = params.get("notice")
    if key == "accept_ok":
        merged, left_out = _count(params, "merged"), _count(params, "left_out")
        if merged is None or left_out is None:
            return None
        text = "You now share a shortlist."
        if merged:
            text += f" {blocks(merged)} of yours {'was' if merged == 1 else 'were'} added."
        if left_out:
            text += f" {blocks(left_out)} of yours {'was' if left_out == 1 else 'were'} left out"
            text += " because the shortlist was full." if merged else "."
        return Notice("ok", text)
    found = _KEYS.get(key or "")
    return Notice(*found) if found else None
