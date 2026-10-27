"""DB-mode resolution (?db=pg|mongo, then the db_mode cookie, then pg) and timing records."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlencode

from fastapi import Request

MODES = ("pg", "mongo")
DEFAULT_MODE = "pg"
COOKIE = "db_mode"
COOKIE_MAX_AGE = 30 * 24 * 3600
# Header links only reuse a path of plain segments. Anything else (//host, /\host, control characters)
# could be read by a browser as another origin, so those pages link to "/" instead.
# The (?!/) stops an empty first segment: //host/x would otherwise match the segment rules.
_SAFE_PATH = re.compile(r"/(?!/)[A-Za-z0-9._~-]*(/[A-Za-z0-9._~-]+)*")
MODE_NAMES = {"pg": "PostgreSQL", "mongo": "MongoDB"}


@dataclass(frozen=True)
class Timing:
    label: str
    db: str
    ms: float

    @property
    def db_name(self) -> str:
        return MODE_NAMES[self.db]


def requested_mode(request: Request) -> str | None:
    """The ?db= value as given (may be invalid), or None when absent."""
    return request.query_params.get("db")


def resolve_mode(request: Request) -> str:
    """Mode for a request whose ?db= is absent or valid."""
    param = requested_mode(request)
    if param in MODES:
        return param
    cookie = request.cookies.get(COOKIE)
    return cookie if cookie in MODES else DEFAULT_MODE


def mode_links(request: Request) -> dict[str, str]:
    """Current path and query with db replaced, other parameters kept (URL-encoded)."""
    others = [(k, v) for k, v in request.query_params.multi_items() if k != "db"]
    path = request.url.path if _SAFE_PATH.fullmatch(request.url.path) else "/"
    return {m: f"{path}?{urlencode([*others, ('db', m)])}" for m in MODES}
