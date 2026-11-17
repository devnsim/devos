# ==============================================================================
# FILE: engines/toolkit/table.py
# OVERVIEW: Table shape toolkit - the everyday operations on rows of typed fields, shared by every engine
#           that holds tables in memory: run a Query v1 (filter tree, search, time range, sort, group +
#           aggregate, page), compare values by their field kind.
# PURPOSE:
#   1. run_query(rows, fields, query) -> (page, total, fields) - the in-memory executor (records today, CSV
#      datasets in the Data engine). Engines with a real database push the query down instead.
#   2. Typed comparison: numbers as numbers, dates / datetimes as ISO text, multi-selects as lists, text
#      case-insensitively; empty values always sort last.
# CONTRACT:
#   - Requires: engines.kernel.query (validate_query). Standard library only.
#   - Exposes: run_query(rows, fields, question), matches(row, node, kinds), sort_rows(rows, sort, kinds)
# NOTES:
#   - [2026-11-17] Created in Phase 2 of the engine plan (first shape toolkit).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - kinds (dict): field key -> field kind ("number", "date", "multi_select" ...), from the shape's fields.
# - group row: One row per group_by combination, with the aggregates as extra fields ("count" by default).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. TABLE_TOOLKIT
#    1.1 values
#    1.2 filter_and_sort
#    1.3 run_query
# ==============================================================================

# ==============================================================================
# [1.1] values
# ==============================================================================
from __future__ import annotations

from engines.kernel.messages import EngineError
from engines.kernel.query import validate_query

NUMERIC = ("number", "integer")


def _is_empty(value) -> bool:
    return value is None or value == "" or value == []


def _comparable(value, kind: str):
    """A value made comparable for its field kind (None stays None)."""
    if _is_empty(value):
        return None
    if kind in NUMERIC:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if kind == "checkbox":
        return bool(value)
    if isinstance(value, list):
        return [str(item).casefold() for item in value]
    return str(value).casefold() if kind not in ("date", "datetime") else str(value)
# --- END [1.1] ---


# ==============================================================================
# [1.2] filter_and_sort
# ==============================================================================
def matches(row: dict, node: dict | None, kinds: dict[str, str]) -> bool:
    """Does the row pass the filter tree?"""
    if not node:
        return True
    if "all" in node:
        return all(matches(row, child, kinds) for child in node["all"])
    if "any" in node:
        return any(matches(row, child, kinds) for child in node["any"])
    if "not" in node:
        return not matches(row, node["not"], kinds)
    kind = kinds.get(node["field"], "text")
    value, op = _comparable(row.get(node["field"]), kind), node["op"]
    if op == "empty":
        return value is None
    if op == "not_empty":
        return value is not None
    target = node.get("value")
    if op in ("in", "not_in"):
        options = [_comparable(item, kind) for item in (target or [])]
        found = any(item in options for item in value) if isinstance(value, list) else value in options
        return found if op == "in" else not found
    if op == "between":
        low, high = (_comparable(item, kind) for item in target)
        return value is not None and (low is None or value >= low) and (high is None or value <= high)
    target = _comparable(target, kind)
    if op == "contains":
        return value is not None and (target in value if isinstance(value, list) else str(target) in str(value))
    if op == "starts_with":
        return value is not None and str(value).startswith(str(target))
    if op == "eq":
        return target in value if isinstance(value, list) else value == target
    if op == "ne":
        return not (target in value if isinstance(value, list) else value == target)
    if value is None or target is None:
        return False
    try:
        return {"lt": value < target, "le": value <= target, "gt": value > target, "ge": value >= target}[op]
    except TypeError:
        return False


def sort_rows(rows: list[dict], sort: list[dict], kinds: dict[str, str]) -> list[dict]:
    """Stable multi-key sort; empty values last whatever the direction."""
    ordered = list(rows)
    for item in reversed(sort or []):
        key, descending = item["field"], bool(item.get("descending"))
        kind = kinds.get(key, "text")
        filled = [row for row in ordered if _comparable(row.get(key), kind) is not None]
        empty = [row for row in ordered if _comparable(row.get(key), kind) is None]
        filled.sort(key=lambda row: _comparable(row.get(key), kind), reverse=descending)
        ordered = filled + empty
    return ordered
# --- END [1.2] ---


# ==============================================================================
# [1.3] run_query
# ==============================================================================
def _aggregate(rows: list[dict], aggregates: list[dict], kinds: dict[str, str]) -> dict:
    result = {}
    for item in aggregates:
        name = item.get("as") or (item["op"] if item["op"] == "count" else f"{item['op']}_{item['field']}")
        if item["op"] == "count":
            result[name] = len(rows)
            continue
        numbers = [number for row in rows if (number := _comparable(row.get(item["field"]), "number")) is not None]
        result[name] = (None if not numbers else sum(numbers) if item["op"] == "sum" else
                        sum(numbers) / len(numbers) if item["op"] == "avg" else
                        min(numbers) if item["op"] == "min" else max(numbers))
    return result


def run_query(rows: list[dict], fields: list[dict], question: dict, title_key: str = "title") -> tuple[list[dict], int, list[dict]]:
    """(page rows, rows matching before paging, fields of the result). Grouping replaces the rows by one
    row per group (group fields + aggregates)."""
    kinds = {field["key"]: field["kind"] for field in fields}
    problems = validate_query(question, set(kinds) | {title_key, "id"})
    if problems:
        raise EngineError(f"Bad query: {'; '.join(problems)}", code="bad_query")
    kinds.setdefault(title_key, "text")
    selected = [row for row in rows if matches(row, question.get("filter"), kinds)]
    if question.get("search"):
        needle = question["search"].casefold()
        text_keys = [title_key] + [key for key, kind in kinds.items() if kind in ("text", "long_text", "select", "url")]
        selected = [row for row in selected if any(needle in str(row.get(key) or "").casefold() for key in text_keys)]
    if question.get("time_range"):
        span = question["time_range"]
        between = {"field": span["field"], "op": "between", "value": [span.get("start"), span.get("end")]}
        selected = [row for row in selected if matches(row, between, kinds)]
    result_fields = fields
    if question.get("group_by"):
        groups: dict[tuple, list[dict]] = {}
        for row in selected:
            groups.setdefault(tuple(str(row.get(key) or "") for key in question["group_by"]), []).append(row)
        aggregates = question.get("aggregates") or [{"op": "count"}]
        selected = [{"id": "|".join(values), **dict(zip(question["group_by"], values)), **_aggregate(members, aggregates, kinds)}
                    for values, members in groups.items()]
        result_fields = [field for field in fields if field["key"] in question["group_by"]] + \
            [{"key": next(iter(_aggregate([], [item], kinds))), "title": item["op"].capitalize(), "kind": "number",
              "options": [], "unit": ""} for item in aggregates]
    selected = sort_rows(selected, question.get("sort") or [], kinds)
    total = len(selected)
    offset = question.get("offset", 0)
    limit = question.get("limit")
    page = selected[offset:offset + limit] if limit is not None else selected[offset:]
    if question.get("fields"):
        keep = set(question["fields"]) | {"id", title_key}
        page = [{key: value for key, value in row.items() if key in keep} for row in page]
        result_fields = [field for field in result_fields if field["key"] in keep]
    return page, total, result_fields
# --- END [1.3] ---
