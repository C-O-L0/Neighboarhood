"""Saved blocks: input rules and the flash messages shown after a save, edit or remove.

A message travels in the redirect's query string as a key from the fixed table below (plus small integers for the
rollback demo), so no free text from a request is ever echoed back.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.repos.dto import Outcome, RolledBack

NOTE_MAX = 500  # saved_building.note VARCHAR(500) and the buyers validator's maxLength
MAX_ID = 2**31 - 1  # building.building_id is INT

TOO_LONG = f"The note must be at most {NOTE_MAX} characters."


@dataclass(frozen=True)
class Notice:
    kind: str  # "ok" or "bad", the CSS classes in app.css
    text: str


def clean_note(raw: str) -> str | None:
    """Trim the note; a blank note is None. Raises ValueError when it is over NOTE_MAX characters."""
    note = raw.strip()
    if not note:
        return None
    if len(note) > NOTE_MAX:
        raise ValueError(TOO_LONG)
    return note


def parse_building_id(raw: str) -> int | None:
    """A building id from a form field: plain digits in the INT range, else None."""
    value = raw.strip()
    if not value.isascii() or not value.isdigit() or len(value) > 10:
        return None
    number = int(value)
    return number if 1 <= number <= MAX_ID else None


_TEXT = {
    ("save", Outcome.OK): ("ok", "Block saved."),
    ("save", Outcome.ALREADY_SAVED): ("bad", "That block is already in your saved list."),
    ("save", Outcome.NO_SUCH_BUILDING): ("bad", "There is no such block."),
    ("save", Outcome.CAP_REACHED): ("bad", "You can save at most 50 blocks. Remove one first."),
    ("edit", Outcome.OK): ("ok", "Note updated."),
    ("edit", Outcome.NOT_SAVED): ("bad", "That block is not in your saved list."),
    ("remove", Outcome.OK): ("ok", "Block removed from your saved list."),
    ("remove", Outcome.NOT_SAVED): ("bad", "That block is not in your saved list."),
    ("note", None): ("bad", TOO_LONG),
    ("input", None): ("bad", "Choose a valid block."),
}
_KEYS = {f"{action}_{outcome.value if outcome else 'invalid'}": v for (action, outcome), v in _TEXT.items()}


def notice_key(action: str, outcome: Outcome | None) -> str:
    return f"{action}_{outcome.value if outcome else 'invalid'}"


def rolled_back_params(result: RolledBack) -> dict[str, str]:
    """Query parameters that carry a RolledBack result through the redirect."""
    params = {"notice": "save_rolled_back"}
    for name in ("insert", "update", "matched"):
        value = getattr(result, name)
        if value is not None:
            params[name] = str(value)
    return params


def _count(params: Mapping[str, str], name: str) -> int | None:
    raw = params.get(name, "")
    return int(raw) if raw.isascii() and raw.isdigit() and len(raw) <= 3 else None


def notice_from(params: Mapping[str, str], mode: str) -> Notice | None:
    """The flash message for these query parameters, or None when there is none or the key is unknown."""
    key = params.get("notice")
    if key == "save_rolled_back":
        if mode == "pg":
            insert, update = _count(params, "insert"), _count(params, "update")
            if insert is None or update is None:
                return None
            text = (
                f"Rolled back: the transaction inserted {insert} row into saved_building and updated "
                f"{update} row of buyer, then failed on purpose before commit, so neither change was kept."
            )
        else:
            matched = _count(params, "matched")
            if matched is None:
                return None
            text = (
                f"Rolled back: the transaction matched {matched} buyer document and pushed the block, then "
                "failed on purpose before commit, so the transaction aborted and nothing was kept."
            )
        return Notice("ok", text)
    found = _KEYS.get(key or "")
    return Notice(*found) if found else None
