"""Enumerate every write rule from the live catalogues of both databases (task 3.5a).

PostgreSQL: constraints (c, f, n, p, u), unique indexes that have no constraint, user triggers, and the
length and precision limits of VARCHAR and NUMERIC columns. MongoDB: every rule found by walking the live
collection validators. Keys look like `pg:<table>.<name>` and `mongo:<collection>:<json path>:<keyword>`.
"""

from __future__ import annotations

TABLES = (
    "town",
    "flat_type",
    "flat_model",
    "building",
    "building_flat_supply",
    "resale_transaction",
    "buyer",
    "saved_building",
    "hawker_centre",
    "household",
    "household_member",
    "household_invitation",
    "household_saved",
)

PG_CONSTRAINTS = """
    SELECT c.relname, k.conname, k.contype,
           CASE WHEN k.contype = 'n' THEN a.attname END
    FROM pg_constraint k
    JOIN pg_class c ON c.oid = k.conrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_attribute a ON a.attrelid = k.conrelid AND k.contype = 'n' AND a.attnum = k.conkey[1]
    WHERE n.nspname = 'public' AND k.contype IN ('c', 'f', 'n', 'p', 'u') AND c.relname = ANY(%s)
    ORDER BY 1, 2"""

PG_INDEXES = """
    SELECT c.relname, i.relname
    FROM pg_index x
    JOIN pg_class c ON c.oid = x.indrelid
    JOIN pg_class i ON i.oid = x.indexrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND x.indisunique AND c.relname = ANY(%s)
      AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conindid = x.indexrelid)
    ORDER BY 1, 2"""

PG_TRIGGERS = """
    SELECT c.relname, t.tgname
    FROM pg_trigger t
    JOIN pg_class c ON c.oid = t.tgrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE NOT t.tgisinternal AND n.nspname = 'public' AND c.relname = ANY(%s)
    ORDER BY 1, 2"""

PG_TYPES = """
    SELECT table_name, column_name FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = ANY(%s)
      AND ((data_type = 'character varying' AND character_maximum_length IS NOT NULL)
           OR (data_type = 'character' AND character_maximum_length IS NOT NULL)
           OR (data_type = 'numeric' AND numeric_precision IS NOT NULL))
    ORDER BY 1, 2"""

SCHEMA_KEYWORDS = (
    "bsonType",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "pattern",
    "enum",
    "minItems",
    "maxItems",
)


def pg_rules(conn) -> dict[str, dict]:
    """key -> {kind, table, name, column}.

    kind is the pg_constraint type letter (c, f, n, p, u), "index", "trigger" or "type"; column is set for
    NOT NULL rules and for :type rules.
    """
    tables = list(TABLES)
    rules: dict[str, dict] = {}
    for table, name, kind, column in conn.execute(PG_CONSTRAINTS, (tables,)):
        rules[f"pg:{table}.{name}"] = {"kind": kind, "table": table, "name": name, "column": column}
    for table, name in conn.execute(PG_INDEXES, (tables,)):
        rules[f"pg:{table}.{name}"] = {"kind": "index", "table": table, "name": name, "column": None}
    for table, name in conn.execute(PG_TRIGGERS, (tables,)):
        rules[f"pg:{table}.{name}"] = {"kind": "trigger", "table": table, "name": name, "column": None}
    for table, column in conn.execute(PG_TYPES, (tables,)):
        rules[f"pg:{table}.{column}:type"] = {"kind": "type", "table": table, "name": None, "column": column}
    return rules


def _walk_schema(node: dict, path: str, emit) -> None:
    """List the rules of one $jsonSchema node, then recurse through properties, items, anyOf and not.

    A key is `<path>:<keyword>`; a required entry is `<path>:required.<field>`. Child paths are dotted: a
    property adds its name, array items add `items`, anyOf branches add `anyOf.<i>` and not adds `not`.
    """
    for name in node.get("required", []):
        emit(f"{path}:required.{name}")
    for keyword in SCHEMA_KEYWORDS:
        if keyword in node:
            emit(f"{path}:{keyword}")
    if node.get("additionalProperties") is False:
        emit(f"{path}:additionalProperties")
    for name, child in node.get("properties", {}).items():
        _walk_schema(child, f"{path}.{name}", emit)
    if "items" in node:
        _walk_schema(node["items"], f"{path}.items", emit)
    for i, branch in enumerate(node.get("anyOf", [])):
        _walk_schema(branch, f"{path}.anyOf.{i}", emit)
    if "not" in node:
        _walk_schema(node["not"], f"{path}.not", emit)


def _walk_expr(node, path: str, emit) -> None:
    """List the leaf predicates of a $expr (comparisons under $and and $cond), keyed by their JSON path."""
    if not isinstance(node, dict):
        return  # a literal (the true branch of a $cond) carries no rule
    for op, arg in node.items():
        here = f"{path}.{op}"
        if op == "$and":
            for i, part in enumerate(arg):
                _walk_expr(part, f"{here}.{i}", emit)
        elif op == "$cond":
            for i in (1, 2):  # index 0 is the condition, not a rule
                _walk_expr(arg[i], f"{here}.{i}", emit)
        else:
            emit(here)


def mongo_rules(db) -> dict[str, dict]:
    """key -> {} from each collection's live validator (`listCollections`)."""
    rules: dict[str, dict] = {}
    for info in db.list_collections():
        name = info["name"]
        validator = info.get("options", {}).get("validator")
        if not validator:
            continue

        def emit(suffix, name=name):
            rules[f"mongo:{name}:{suffix}"] = {"collection": name}

        if "$jsonSchema" in validator:
            _walk_schema(validator["$jsonSchema"], "$jsonSchema", emit)
        if "$expr" in validator:
            _walk_expr(validator["$expr"], "$expr", emit)
    return rules
