"""Places a buyer wants to live near (UC9): reading a typed block and street, and the radii on offer.

A place is any block in the data, such as the parents' block. The text is read as an optional block number
followed by a street, with full words turned into HDB's abbreviations, so "Blk 186 Bishan Street 13",
"186 bishan st 13" and "bishan street 13" all work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from etl.clean import normalise_key
from etl.streets import abbreviate_street

MAX_PLACE = 60  # a block (5) and a street (40) with room for "BLK" and spaces
SUGGESTIONS = 8  # matching blocks offered when the text names more than one, or none exactly
RADII_M = (1000, 2000, 5000)
DEFAULT_RADIUS_M = 2000

_BLOCK = re.compile(r"\d{1,4}[A-Z]?", re.ASCII)  # HDB block numbers: 1 to 4 digits and an optional letter
_PREFIX = ("BLK", "BLOCK")


@dataclass(frozen=True)
class PlaceText:
    block: str | None  # None when the text gives only a street
    street: str  # abbreviated, matched anywhere in the stored street


def parse_place(text: str) -> PlaceText | None:
    """The block and street in a person's text, or None when it is empty, too long or not printable."""
    if not text.isprintable():
        return None
    tokens = normalise_key(text).split(" ")
    if tokens and tokens[0] in _PREFIX:
        tokens = tokens[1:]
    words = " ".join(t for t in tokens if t)
    if not words or len(words) > MAX_PLACE:
        return None
    first, _, rest = words.partition(" ")
    if rest and _BLOCK.fullmatch(first):
        return PlaceText(block=first, street=abbreviate_street(rest))
    return PlaceText(block=None, street=abbreviate_street(words))


def parse_radius(text: str) -> int | None:
    """One of the offered radii in metres, from its text ("2000"); None for anything else."""
    return int(text) if text in {str(r) for r in RADII_M} else None
