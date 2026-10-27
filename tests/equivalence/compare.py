"""assert_equivalent: structural comparison of the DTOs two repos return.

Recursive over dataclasses, lists (order matters), dicts and scalars. Values must be exactly
equal, including None and every Decimal, except dataclass fields declared with
field(metadata={"tol": Decimal("0.01")}), which may differ by at most that tolerance.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from typing import Any


def assert_equivalent(pg: Any, mongo: Any, path: str = "root") -> None:
    _compare(pg, mongo, path, None)


def _fail(path: str, pg: Any, mongo: Any, why: str = "") -> None:
    raise AssertionError(f"{path}: postgres={pg!r} mongo={mongo!r}{' (' + why + ')' if why else ''}")


def _compare(pg: Any, mongo: Any, path: str, tol: Decimal | None) -> None:
    if is_dataclass(pg) and not isinstance(pg, type):
        if type(pg) is not type(mongo):
            _fail(path, type(pg).__name__, type(mongo).__name__, "different types")
        for f in fields(pg):
            _compare(getattr(pg, f.name), getattr(mongo, f.name), f"{path}.{f.name}", f.metadata.get("tol"))
    elif isinstance(pg, list):
        if not isinstance(mongo, list):
            _fail(path, pg, mongo, "not both lists")
        if len(pg) != len(mongo):
            _fail(path, f"{len(pg)} items", f"{len(mongo)} items", "list lengths differ")
        for i, (a, b) in enumerate(zip(pg, mongo, strict=True)):
            _compare(a, b, f"{path}[{i}]", None)
    elif isinstance(pg, dict):
        if not isinstance(mongo, dict) or pg.keys() != mongo.keys():
            _fail(path, sorted(pg), sorted(mongo) if isinstance(mongo, dict) else mongo, "different keys")
        for k in pg:
            _compare(pg[k], mongo[k], f"{path}[{k!r}]", None)
    elif tol is not None and isinstance(pg, Decimal) and isinstance(mongo, Decimal):
        if abs(pg - mongo) > tol:
            _fail(path, pg, mongo, f"differ by more than {tol}")
    elif isinstance(pg, float) or isinstance(mongo, float):
        _fail(path, pg, mongo, "float in a DTO")
    elif type(pg) is not type(mongo) or pg != mongo:
        _fail(path, pg, mongo)
