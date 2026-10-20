"""Street-name normalisation shared by the geocoder and the cleaning step.

HDB datasets use abbreviated street names ("ANG MO KIO AVE 10") while OneMap
returns full names ("ANG MO KIO AVENUE 10"). Expanding both sides to the long
form lets us compare them token by token.
"""

from __future__ import annotations

import re

# Abbreviation -> full word, as used in HDB's resale and property datasets.
ABBREVIATIONS: dict[str, str] = {
    "AVE": "AVENUE",
    "BLVD": "BOULEVARD",
    "BT": "BUKIT",
    "C'WEALTH": "COMMONWEALTH",
    "CL": "CLOSE",
    "CRES": "CRESCENT",
    "CTR": "CENTRE",
    "CTRL": "CENTRAL",
    "DR": "DRIVE",
    "GDN": "GARDEN",
    "GDNS": "GARDENS",
    "HTS": "HEIGHTS",
    "IND": "INDUSTRIAL",
    "JLN": "JALAN",
    "KG": "KAMPONG",
    "LOR": "LORONG",
    "MKT": "MARKET",
    "NTH": "NORTH",
    "PK": "PARK",
    "PL": "PLACE",
    "RD": "ROAD",
    "SQ": "SQUARE",
    "ST": "STREET",
    "STH": "SOUTH",
    "TER": "TERRACE",
    "TG": "TANJONG",
    "UPP": "UPPER",
}

_SPACES = re.compile(r"\s+")


def normalise_key(text: str) -> str:
    """Upper-case, trim and collapse whitespace. Used for join keys."""
    return _SPACES.sub(" ", text.strip().upper())


def expand_street(street: str) -> str:
    """Expand HDB abbreviations to full words.

    "ST." as a leading token means SAINT (e.g. "ST. GEORGE'S RD"), so it is
    handled before the generic table, where "ST" means STREET.
    """
    tokens = normalise_key(street).split(" ")
    out: list[str] = []
    for i, tok in enumerate(tokens):
        if tok == "ST." and i == 0:
            out.append("SAINT")
        else:
            out.append(ABBREVIATIONS.get(tok, tok))
    return " ".join(out)


def same_street(hdb_street: str, onemap_road: str) -> bool:
    """True when an HDB street and a OneMap road name refer to the same road."""
    return _canonical(hdb_street) == _canonical(onemap_road)


def _canonical(street: str) -> str:
    """One form for both sides: abbreviations expanded, apostrophes and full stops removed."""
    return expand_street(street).replace("'", "").replace(".", "")
