"""Loads bench/cases.yaml into typed cases.

Bench-only statements are never string-built: SQL has %(name)s placeholders bound at execution, and MongoDB
pipelines carry {"$param": name} markers that this loader replaces with the typed argument (dates become midnight-UTC
datetimes, decimals become Decimal128).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from bson.decimal128 import Decimal128

from bench import calls

CASES_FILE = Path(__file__).with_name("cases.yaml")
DEFAULT_RUNS = 50
DEFAULT_WARMUP = 5
PARAM_SETS = 3

# A prepared call has the parameters already bound: prepared(connection_or_database) -> result.
Prepared = Callable[[Any], Any]


@dataclass(frozen=True)
class ParamSet:
    label: str
    args: dict


@dataclass(frozen=True)
class Side:
    """The function one database runs for a case: a bench.calls function or a bench-only statement."""

    spec: dict

    def prepare(self, args: dict) -> Prepared:
        spec = self.spec
        if "module" in spec:
            fn = getattr(importlib.import_module(spec["module"]), spec["name"])
            return lambda target: fn(target, args)
        if "sql" in spec:
            return calls.sql_call(spec["sql"], {k: args[k] for k in spec["bind"]}, spec.get("scalar", False))
        collection = spec["collection"]
        scalar = spec.get("scalar")
        if "pipeline" in spec:
            return calls.pipeline_call(collection, to_mongo(spec["pipeline"], args), scalar)
        return calls.find_call(collection, to_mongo(spec["find"], args), to_mongo(spec.get("projection"), args), scalar)


@dataclass(frozen=True)
class BenchCase:
    id: str
    pair: str
    pg: Side | None
    mongo: Side | None
    params: list[ParamSet]
    runs: int = DEFAULT_RUNS
    warmup: int = DEFAULT_WARMUP
    writes: bool = False
    note: str = ""
    resolve: str | None = None
    raw: dict = field(default_factory=dict, repr=False, compare=False)


def typed_arg(value: Any) -> Any:
    """YAML argument values: {"$decimal": "1.50"} becomes a Decimal; everything else is already typed."""
    if isinstance(value, dict) and set(value) == {"$decimal"}:
        return Decimal(value["$decimal"])
    return value


def to_mongo(node: Any, args: dict) -> Any:
    """A MongoDB statement template with its {"$param": name} markers filled in with typed values."""
    if isinstance(node, dict):
        if set(node) == {"$param"}:
            return to_mongo(args[node["$param"]], args)
        if set(node) == {"$decimal"}:
            return Decimal128(node["$decimal"])
        return {k: to_mongo(v, args) for k, v in node.items()}
    if isinstance(node, list):
        return [to_mongo(v, args) for v in node]
    if isinstance(node, Decimal):
        return Decimal128(node)
    if isinstance(node, date) and not isinstance(node, datetime):
        return datetime(node.year, node.month, node.day, tzinfo=UTC)
    return node


def _side(spec: dict | None) -> Side | None:
    return None if spec is None else Side(spec)


def load_cases(path: Path = CASES_FILE) -> list[BenchCase]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: list[BenchCase] = []
    for c in doc["cases"]:
        params = [ParamSet(p["label"], {k: typed_arg(v) for k, v in p["args"].items()}) for p in c["params"]]
        if len(params) != PARAM_SETS:
            raise ValueError(f"case {c['id']}: {len(params)} parameter sets, expected {PARAM_SETS}")
        if len({p.label for p in params}) != len(params):
            raise ValueError(f"case {c['id']}: duplicate parameter set labels")
        if c.get("pg") is None and c.get("mongo") is None:
            raise ValueError(f"case {c['id']} runs on neither database")
        out.append(
            BenchCase(
                id=c["id"],
                pair=c["pair"],
                pg=_side(c.get("pg")),
                mongo=_side(c.get("mongo")),
                params=params,
                runs=int(c.get("runs", DEFAULT_RUNS)),
                warmup=int(c.get("warmup", DEFAULT_WARMUP)),
                writes=bool(c.get("writes", False)),
                note=c.get("note", ""),
                resolve=c.get("resolve"),
                raw=c,
            )
        )
    ids = [c.id for c in out]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case ids")
    return out
