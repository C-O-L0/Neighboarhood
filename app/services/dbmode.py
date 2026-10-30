"""DB-mode resolution, demo mode and timing records.

The database is ?db=pg|mongo when given, then the db_mode cookie while demo mode is on, otherwise pg. Demo mode
(the demo cookie) is what a presenter turns on to switch databases and see timings; buyers never see it, so they
always use PostgreSQL unless a link names the database (docs/conventions.md, 6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlencode

from fastapi import Request

MODES = ("pg", "mongo")
DEFAULT_MODE = "pg"
COOKIE = "db_mode"
COOKIE_MAX_AGE = 30 * 24 * 3600
DEMO_COOKIE = "demo"
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


def demo_on(request: Request) -> bool:
    return request.cookies.get(DEMO_COOKIE) == "1"


def resolve_mode(request: Request) -> str:
    """Mode for a request whose ?db= is absent or valid. The db_mode cookie counts only in demo mode."""
    param = requested_mode(request)
    if param in MODES:
        return param
    cookie = request.cookies.get(COOKIE)
    return cookie if demo_on(request) and cookie in MODES else DEFAULT_MODE


def safe_return_path(path: str, query: str = "") -> str:
    """A local path and query to send the browser to. A path that is not plain segments becomes "/" (_SAFE_PATH)."""
    path = path if _SAFE_PATH.fullmatch(path) else "/"
    return f"{path}?{query}" if query else path


def mode_links(request: Request) -> dict[str, str]:
    """Current path and query with db replaced, other parameters kept (URL-encoded)."""
    others = [(k, v) for k, v in request.query_params.multi_items() if k != "db"]
    return {m: safe_return_path(request.url.path, urlencode([*others, ("db", m)])) for m in MODES}
