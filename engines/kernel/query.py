# ==============================================================================
# FILE: engines/kernel/query.py
# OVERVIEW: Query v1 - the question a view asks an engine: which fields, which rows (filter tree, search,
#           time range, map extent), in what order, grouped how, which page. Plain data, so it travels in a
#           request and the ENGINE runs it (views stay light; remote engines get questions, not datasets).
# PURPOSE:
#   1. query(...) builds one; validate_query(query, fields) says what's wrong with it (unknown fields,
#      operators, aggregates) before an engine runs it.
#   2. condition(), all_of(), any_of(), not_() build filter trees; query_key() is the stable identity
#      shared subscriptions use ("same question").
# CONTRACT:
#   - Requires: standard library only.
#   - Exposes: OPERATORS, AGGREGATES, query(), condition(), all_of(), any_of(), not_(), validate_query(),
#              query_key(), EMPTY_QUERY
# NOTES:
#   - [2026-11-17] Created in Phase 2 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - query (dict): {fields, filter, search, sort [{field, descending}], group_by, aggregates [{field, op, as}],
#   offset, limit, time_range {field, start, end}, extent {field, bbox}}; every key optional.
# - filter tree: {"all": [...]} | {"any": [...]} | {"not": node} | {"field", "op", "value"}.
# - EMPTY_QUERY (dict): Every row, in the source's own order.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. QUERY
#    1.1 builders
#    1.2 validate
# ==============================================================================

# ==============================================================================
# [1.1] builders
# ==============================================================================
from __future__ import annotations

import json

OPERATORS = ("eq", "ne", "lt", "le", "gt", "ge", "in", "not_in", "contains", "starts_with", "empty", "not_empty",
             "between")
VALUELESS = ("empty", "not_empty")
AGGREGATES = ("count", "sum", "avg", "min", "max")
QUERY_KEYS = ("fields", "filter", "search", "sort", "group_by", "aggregates", "offset", "limit", "time_range", "extent")
EMPTY_QUERY: dict = {}


def condition(field: str, op: str, value=None) -> dict:
    return {"field": field, "op": op} if op in VALUELESS else {"field": field, "op": op, "value": value}


def all_of(*nodes: dict) -> dict:
    return {"all": list(nodes)}


def any_of(*nodes: dict) -> dict:
    return {"any": list(nodes)}


def not_(node: dict) -> dict:
    return {"not": node}


def query(fields: list[str] | None = None, filter: dict | None = None, search: str = "",
          sort: list[tuple[str, bool]] | None = None, group_by: list[str] | None = None,
          aggregates: list[dict] | None = None, offset: int = 0, limit: int | None = None,
          time_range: dict | None = None, extent: dict | None = None) -> dict:
    """A query with only the parts that are set (so equal questions have equal keys)."""
    parts = {"fields": fields, "filter": filter, "search": search or None,
             "sort": [{"field": key, "descending": bool(descending)} for key, descending in sort] if sort else None,
             "group_by": group_by, "aggregates": aggregates, "offset": offset or None, "limit": limit,
             "time_range": time_range, "extent": extent}
    return {key: value for key, value in parts.items() if value is not None}


def query_key(source: dict, question: dict) -> str:
    """Stable identity of "this question to this source" (shared subscriptions)."""
    return json.dumps({"source": source, "query": question}, sort_keys=True, separators=(",", ":"))
# --- END [1.1] ---


# ==============================================================================
# [1.2] validate
# ==============================================================================
def _check_filter(node, field_keys: set[str] | None, problems: list[str]) -> None:
    if not isinstance(node, dict):
        problems.append(f"filter node must be an object, not {type(node).__name__}")
        return
    if "all" in node or "any" in node:
        children = node.get("all", node.get("any"))
        if not isinstance(children, list):
            problems.append("“all” / “any” take a list")
            return
        for child in children:
            _check_filter(child, field_keys, problems)
    elif "not" in node:
        _check_filter(node["not"], field_keys, problems)
    else:
        if node.get("op") not in OPERATORS:
            problems.append(f"unknown operator “{node.get('op')}”")
        if field_keys is not None and node.get("field") not in field_keys:
            problems.append(f"unknown field “{node.get('field')}”")
        if node.get("op") not in VALUELESS and "value" not in node:
            problems.append(f"“{node.get('op')}” on “{node.get('field')}” needs a value")
        if node.get("op") == "between" and not (isinstance(node.get("value"), list) and len(node["value"]) == 2):
            problems.append("“between” takes [low, high]")


def validate_query(question: dict, field_keys: set[str] | None = None) -> list[str]:
    """What is wrong with a query ([] = fine). With field_keys, field names are checked too."""
    problems = [f"unknown query key “{key}”" for key in question if key not in QUERY_KEYS]
    known = None if field_keys is None else set(field_keys)
    named = list(question.get("fields") or []) + list(question.get("group_by") or []) + \
        [item.get("field") for item in question.get("sort") or []] + \
        [item.get("field") for item in question.get("aggregates") or [] if item.get("op") != "count"]
    for part in ("time_range", "extent"):
        if question.get(part):
            named.append(question[part].get("field"))
    if known is not None:
        problems += [f"unknown field “{name}”" for name in named if name not in known]
    for item in question.get("aggregates") or []:
        if item.get("op") not in AGGREGATES:
            problems.append(f"unknown aggregate “{item.get('op')}”")
    if question.get("filter") is not None:
        _check_filter(question["filter"], known, problems)
    for key in ("offset", "limit"):
        if key in question and (not isinstance(question[key], int) or question[key] < 0):
            problems.append(f"“{key}” must be a whole number ≥ 0")
    return problems
# --- END [1.2] ---
