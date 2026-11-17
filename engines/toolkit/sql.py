# ==============================================================================
# FILE: engines/toolkit/sql.py
# OVERVIEW: Query v1 pushed down to SQLite: a view's question (filter tree, search, time range, sort, group +
#           aggregates, page) compiled into SQL with parameters, so the database does the work and its
#           indexes count. Used by the Data engine for record collections (JSON values through json_extract)
#           and user databases (real columns); GeoPackages (Spatial) are SQLite too.
# PURPOSE:
#   1. compile_query(question, columns, kinds, search_keys) -> Compiled: WHERE + params, ORDER BY, group
#      expressions, aggregates, offset / limit. Columns map a field key to an SQL expression.
#   2. run_compiled(execute, from_sql, base, compiled, select) -> (rows, total): runs it (grouped or not).
#   3. Semantics match the in-memory toolkit (engines/toolkit/table.py): text compares case-insensitively,
#      multi-selects (JSON arrays) match any member, empty values sort last whatever the direction.
# CONTRACT:
#   - Requires: engines.kernel.query (validate_query), engines.kernel.messages (EngineError). Standard library.
#   - Exposes: Compiled, compile_query(), run_compiled(), quote_identifier(), json_path_expr()
# NOTES:
#   - [2026-11-17] Created in Phase 5a of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - columns (dict): field key -> SQL expression ("\"qty\"", "json_extract(values_json, '$.\"status\"')").
# - kinds (dict): field key -> field kind (number, integer, checkbox, multi_select, date, text …).
# - base (tuple): (where SQL, params) every row must meet anyway (e.g. "collection = ?").
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SQL
#    1.1 helpers
#    1.2 filters
#    1.3 compile_and_run
# ==============================================================================

# ==============================================================================
# [1.1] helpers
# ==============================================================================
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from engines.kernel.messages import EngineError
from engines.kernel.query import validate_query

NUMERIC = ("number", "integer")


def quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def json_path_expr(column: str, key: str) -> str:
    """The SQL expression for one key of a JSON object column (the same text an expression index uses)."""
    return f"json_extract({column}, '$.\"{key.replace(chr(39), chr(39) * 2)}\"')"


def _param(value, kind: str):
    if value is None:
        return None
    if kind in NUMERIC:
        try:
            return float(value)
        except (TypeError, ValueError):
            raise EngineError(f"“{value}” is not a number", code="bad_query") from None
    if kind == "checkbox":
        return 1 if value in (True, 1, "1", "true", "True") else 0
    return str(value)


def _empty(expr: str, kind: str) -> str:
    if kind in NUMERIC or kind == "checkbox":
        return f"({expr} IS NULL)"
    return f"({expr} IS NULL OR {expr} = '' OR {expr} = '[]')"
# --- END [1.1] ---


# ==============================================================================
# [1.2] filters
# ==============================================================================
def _condition(node: dict, columns: dict, kinds: dict, params: list) -> str:
    key, op = node["field"], node["op"]
    expr, kind = columns[key], kinds.get(key, "text")
    if op == "empty":
        return _empty(expr, kind)
    if op == "not_empty":
        return f"NOT {_empty(expr, kind)}"
    value = node.get("value")
    text = kind not in NUMERIC and kind != "checkbox"
    collate = " COLLATE NOCASE" if text else ""
    if kind == "multi_select":                                  # a JSON array: any member matches
        members = value if isinstance(value, list) else [value]
        if op in ("eq", "contains", "in", "ne", "not_in"):
            params.extend(str(item) for item in members)
            marks = ", ".join("?" for _ in members)
            found = f"EXISTS (SELECT 1 FROM json_each({expr}) WHERE value COLLATE NOCASE IN ({marks}))"
            return f"(NOT {_empty(expr, kind)} AND {found})" if op in ("eq", "contains", "in") else \
                f"({_empty(expr, kind)} OR NOT {found})"
    if op in ("in", "not_in"):
        items = [_param(item, kind) for item in (value or [])]
        if not items:
            return "0" if op == "in" else "1"
        params.extend(items)
        inside = f"{expr}{collate} IN ({', '.join('?' for _ in items)})"
        return inside if op == "in" else f"({expr} IS NULL OR NOT {inside})"
    if op == "between":
        low, high = (_param(item, kind) for item in value)
        parts = [f"NOT {_empty(expr, kind)}"]
        if low is not None:
            parts.append(f"{expr}{collate} >= ?")
            params.append(low)
        if high is not None:
            parts.append(f"{expr}{collate} <= ?")
            params.append(high)
        return "(" + " AND ".join(parts) + ")"
    if op == "contains":
        params.append(str(value).casefold())
        return f"instr(lower({expr}), ?) > 0"
    if op == "starts_with":
        params.extend([str(value).casefold()] * 2)
        return f"substr(lower({expr}), 1, length(?)) = ?"
    params.append(_param(value, kind))
    if op == "eq":
        return f"{expr}{collate} = ?"
    if op == "ne":
        return f"({expr} IS NULL OR {expr}{collate} <> ?)"
    symbol = {"lt": "<", "le": "<=", "gt": ">", "ge": ">="}[op]
    return f"(NOT {_empty(expr, kind)} AND {expr}{collate} {symbol} ?)"


def _filter(node: dict | None, columns: dict, kinds: dict, params: list) -> str:
    if not node:
        return "1"
    if "all" in node:
        return "(" + " AND ".join([_filter(child, columns, kinds, params) for child in node["all"]] or ["1"]) + ")"
    if "any" in node:
        return "(" + " OR ".join([_filter(child, columns, kinds, params) for child in node["any"]] or ["0"]) + ")"
    if "not" in node:
        return f"NOT {_filter(node['not'], columns, kinds, params)}"
    return _condition(node, columns, kinds, params)
# --- END [1.2] ---


# ==============================================================================
# [1.3] compile_and_run
# ==============================================================================
@dataclass
class Compiled:
    where: str = "1"
    params: list = field(default_factory=list)
    order: str = ""
    group: list[str] = field(default_factory=list)              # group_by field keys
    aggregates: list[tuple[str, str]] = field(default_factory=list)   # (result name, SQL)
    offset: int = 0
    limit: int | None = None


def compile_query(question: dict, columns: dict[str, str], kinds: dict[str, str], search_keys: list[str],
                  default_order: str = "", search_extra: list[tuple[str, str]] | None = None) -> Compiled:
    """Question -> Compiled. search_extra: (SQL expression, how to write the needle) pairs searched besides
    search_keys (e.g. a JSON column, with the needle JSON-escaped)."""
    problems = validate_query(question, set(columns))
    if question.get("extent"):
        problems.append("this data has no locations (extent)")
    if problems:
        raise EngineError(f"Bad query: {'; '.join(problems)}", code="bad_query")
    params: list = []
    parts = [_filter(question.get("filter"), columns, kinds, params)]
    if question.get("search"):
        needle = str(question["search"]).casefold()
        checks = [f"instr(lower({columns[key]}), ?) > 0" for key in search_keys]
        params.extend([needle] * len(search_keys))
        for expr, escape in search_extra or []:
            checks.append(f"instr(lower({expr}), ?) > 0")
            params.append(escape(needle))
        parts.append("(" + " OR ".join(checks or ["0"]) + ")")
    if question.get("time_range"):
        span = question["time_range"]
        parts.append(_filter({"field": span["field"], "op": "between", "value": [span.get("start"), span.get("end")]},
                             columns, kinds, params))
    compiled = Compiled(" AND ".join(parts), params, offset=int(question.get("offset") or 0), limit=question.get("limit"))
    if question.get("group_by"):
        compiled.group = list(question["group_by"])
        for item in question.get("aggregates") or [{"op": "count"}]:
            name = item.get("as") or (item["op"] if item["op"] == "count" else f"{item['op']}_{item['field']}")
            sql = "COUNT(*)" if item["op"] == "count" else f"{item['op'].upper()}({columns[item['field']]})"
            compiled.aggregates.append((name, sql))
    order = []
    for item in question.get("sort") or []:
        key = item["field"]
        expr = quote_identifier(key) if compiled.group else columns[key]
        kind = "number" if compiled.group and key not in compiled.group else kinds.get(key, "text")
        collate = "" if kind in NUMERIC or kind == "checkbox" else " COLLATE NOCASE"
        order.append(f"{_empty(expr, kind)}, {expr}{collate}{' DESC' if item.get('descending') else ''}")
    compiled.order = ", ".join(order) or ("" if compiled.group else default_order)
    return compiled


def run_compiled(execute: Callable[[str, list], list], from_sql: str, base: tuple[str, list], compiled: Compiled,
                 select: str, columns: dict[str, str]) -> tuple[list, int]:
    """(rows, total before paging). Plain: the `select` columns of the page. Grouped: one dict per group (group
    values as text, the aggregates), "id" = the values joined by "|", like the in-memory toolkit."""
    where = f"({base[0] or '1'}) AND {compiled.where}"
    params = list(base[1]) + compiled.params
    if compiled.group:
        keys = ", ".join(f"{columns[key]} AS {quote_identifier(key)}" for key in compiled.group)
        aggregates = ", ".join(f"{sql} AS {quote_identifier(name)}" for name, sql in compiled.aggregates)
        groups = ", ".join(columns[key] for key in compiled.group)
        sql = f"SELECT {keys}, {aggregates} FROM {from_sql} WHERE {where} GROUP BY {groups}"
        if compiled.order:
            sql += f" ORDER BY {compiled.order}"
        rows = []
        for values in execute(sql, params):
            texts = ["" if value is None else str(value) for value in values[:len(compiled.group)]]
            rows.append({"id": "|".join(texts), **dict(zip(compiled.group, texts)),
                         **{name: value for (name, _sql), value in zip(compiled.aggregates, values[len(compiled.group):])}})
        end = None if compiled.limit is None else compiled.offset + compiled.limit
        return rows[compiled.offset:end], len(rows)
    total = execute(f"SELECT COUNT(*) FROM {from_sql} WHERE {where}", params)[0][0]
    sql = f"SELECT {select} FROM {from_sql} WHERE {where}"
    if compiled.order:
        sql += f" ORDER BY {compiled.order}"
    sql += " LIMIT ? OFFSET ?"
    return execute(sql, params + [-1 if compiled.limit is None else compiled.limit, compiled.offset]), total
# --- END [1.3] ---
