# ==============================================================================
# FILE: engines/data/records.py
# OVERVIEW: Record collections in the Data engine (Projects, Agents …): a title plus JSON values per record
#           (the Phase 5 decision: JSON values with indexes). Queries run in SQLite through json_extract,
#           helped by expression indexes on the properties views filter and sort by; formula properties are
#           computed per record.
# PURPOSE:
#   1. RecordsSource: the source contract for one collection - schema (declared + user properties; select
#      options include every value in use), query (pushed down to SQL; in memory only when the question
#      touches a formula), get, create, update, duplicate, delete, move, add / remove property.
#   2. Formula properties: [Field] references to other properties (by title or key), evaluated with the
#      toolkit's formula language; errors show as the spreadsheet error code ("#VALUE!").
#   3. ensure_indexes(): one expression index per indexed property key (select, number, date, checkbox).
# CONTRACT:
#   - Requires: app.data.store (database, records: RecordCollection, PropertySpec), engines.toolkit (sql,
#               table, formulas), engines.data.common. Runs on the Data engine's thread and connection.
#   - Exposes: RecordsSource(database, name, defaults, noun), record_row(record), ensure_indexes(database,
#              properties), check_formula(formula, properties), MIGRATIONS (records_adapter_defaults)
# NOTES:
#   - [2026-11-17] Phase 5b: structure() names it a record collection.
#   - [2026-11-17] Created in Phase 5a of the engine plan (from the Phase 2 records engine, which it replaces).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - INDEXED_KINDS (tuple): Property kinds that get an expression index ("select", "number", "date", "checkbox").
# - computed value: A formula property's value for one record (number, text, bool or an error code).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. RECORDS
#    1.1 rows_and_indexes
#    1.2 formulas
#    1.3 records_source
# ==============================================================================

# ==============================================================================
# [1.1] rows_and_indexes
# ==============================================================================
from __future__ import annotations

import hashlib
import json

from app.data.store.database import Database
from app.data.store.records import TITLE_KEY, PropertySpec, Record, RecordCollection
from engines.data.common import Source, coerce, diff
from engines.kernel import EngineError
from engines.toolkit.formulas import FormulaError, evaluate, field_references, parse
from engines.toolkit.sql import compile_query, json_path_expr, run_compiled
from engines.toolkit.table import run_query

INDEXED_KINDS = ("select", "number", "date", "checkbox")
MIGRATIONS = ["""
    CREATE TABLE records_adapter_defaults (
        collection TEXT PRIMARY KEY, noun TEXT NOT NULL DEFAULT 'record', properties_json TEXT NOT NULL DEFAULT '[]'
    )
"""]


def record_row(record: Record) -> dict:
    return {"id": record.record_id, TITLE_KEY: record.title, **record.values,
            "created_at": record.created_at, "updated_at": record.updated_at}


def ensure_indexes(database: Database, properties: list[PropertySpec]) -> None:
    """Expression indexes (collection, value) for the properties views filter and sort by. One per key, shared
    by every collection with that key; the expression is the one queries use, so SQLite picks it up."""
    for spec in properties:
        if spec.kind in INDEXED_KINDS:
            name = "record_items_value_" + hashlib.sha1(spec.key.encode()).hexdigest()[:12]
            database.execute(f"CREATE INDEX IF NOT EXISTS {name} ON record_items "
                             f"(collection, {json_path_expr('values_json', spec.key)})")
# --- END [1.1] ---


# ==============================================================================
# [1.2] formulas
# ==============================================================================
def _by_name(properties: list[PropertySpec]) -> dict[str, PropertySpec]:
    names = {TITLE_KEY: PropertySpec(TITLE_KEY, "Name"), "name": PropertySpec(TITLE_KEY, "Name")}
    for spec in properties:
        names[spec.key.casefold()] = spec
        names[spec.title.casefold()] = spec
    return names


def check_formula(formula: str, properties: list[PropertySpec]) -> None:
    """EngineError("invalid_value") when the formula doesn't parse or names a property that doesn't exist."""
    try:
        missing = [name for name in field_references(formula) if name.casefold() not in _by_name(properties)]
    except FormulaError as error:
        raise EngineError(f"The formula doesn't read: {error}", code="invalid_value") from None
    if missing:
        raise EngineError(f"The formula names {', '.join(f'[{name}]' for name in missing)}, which this collection "
                          "doesn't have", code="invalid_value")


class _RowResolver:
    def __init__(self, row: dict, names: dict[str, PropertySpec], trees: dict, evaluating: set) -> None:
        self.row, self.names, self.trees, self.evaluating = row, names, trees, evaluating

    def field(self, name: str):
        spec = self.names.get(name.casefold())
        if spec is None:
            raise FormulaError("#REF!", f"no property [{name}]")
        if spec.kind == "formula":
            return _compute(spec, self.row, self.names, self.trees, self.evaluating)
        value = self.row.get(spec.key)
        return ", ".join(value) if isinstance(value, list) else value

    def cell(self, _row: int, _col: int):
        raise FormulaError("#REF!", "use [Property] in a computed field, not cells")

    def cells(self, *_args):
        raise FormulaError("#REF!", "use [Property] in a computed field, not cells")


def _compute(spec: PropertySpec, row: dict, names: dict, trees: dict, evaluating: set):
    if spec.key in evaluating:
        return "#CYCLE!"
    evaluating.add(spec.key)
    try:
        if spec.key not in trees:
            trees[spec.key] = parse(spec.formula)
        value = evaluate(trees[spec.key], _RowResolver(row, names, trees, evaluating))
        if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
            value = int(value)
        return value if not isinstance(value, complex) else "#NUM!"
    except FormulaError as error:
        return error.code
    except (ZeroDivisionError, OverflowError, ValueError, TypeError):
        return "#VALUE!"
    finally:
        evaluating.discard(spec.key)


def compute_formulas(rows: list[dict], properties: list[PropertySpec]) -> None:
    """Fill in every formula property of the rows (in place)."""
    formulas = [spec for spec in properties if spec.kind == "formula"]
    if not formulas:
        return
    names, trees = _by_name(properties), {}
    for row in rows:
        for spec in formulas:
            row[spec.key] = _compute(spec, row, names, trees, set())
# --- END [1.2] ---


# ==============================================================================
# [1.3] records_source
# ==============================================================================
class RecordsSource(Source):
    key = "id"
    title_key = TITLE_KEY

    def __init__(self, database: Database, name: str, defaults: list[PropertySpec], noun: str) -> None:
        super().__init__(name)
        self.database = database
        self.records = RecordCollection(database, name, defaults)
        self.noun, self.title = noun, name.replace("_", " ").capitalize()
        ensure_indexes(database, self.records.properties)

    def _record(self, record_id) -> Record:
        record = self.records.get(int(record_id))
        if record is None:
            raise EngineError(f"That {self.noun} no longer exists", code="not_found")
        return record

    def _options_in_use(self, spec: PropertySpec) -> list[str]:
        path = json_path_expr("values_json", spec.key)
        if spec.kind == "multi_select":
            sql = (f"SELECT DISTINCT j.value FROM record_items, json_each({path}) AS j "
                   f"WHERE collection = ? AND json_type({path}) = 'array'")
        else:
            sql = f"SELECT DISTINCT {path} FROM record_items WHERE collection = ? AND {path} IS NOT NULL"
        return [str(row[0]) for row in self.database.fetch_all(sql, (self.collection,)) if row[0] not in (None, "")]

    def fields(self, computed: list[dict] | None = None) -> list[dict]:
        fields = []
        for spec in self.records.properties:
            options = list(spec.options)
            if spec.kind in ("select", "multi_select"):
                options += [option for option in self._options_in_use(spec) if option not in options]
            entry = {"key": spec.key, "title": spec.title, "kind": spec.kind, "options": options, "unit": "",
                     "is_user": spec.is_user}
            if spec.kind == "formula":
                values = [row.get(spec.key) for row in computed or []]
                numeric = values and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                                         for value in values if value is not None and not str(value).startswith("#"))
                entry.update(kind="number" if numeric else "text", formula=spec.formula, read_only=True, computed=True)
            fields.append(entry)
        return fields

    def query(self, question: dict) -> tuple[list[dict], int, list[dict]]:
        properties = self.records.properties
        formula_keys = {spec.key for spec in properties if spec.kind == "formula"}
        named = set(question.get("group_by") or []) | {item.get("field") for item in question.get("sort") or []} | \
            {item.get("field") for item in question.get("aggregates") or []} | _filter_fields(question.get("filter")) | \
            ({question["time_range"].get("field")} if question.get("time_range") else set())
        if named & formula_keys or (question.get("search") and formula_keys):
            rows = [record_row(record) for record in self.records.records()]
            compute_formulas(rows, properties)
            return run_query(rows, self.fields(rows), question, TITLE_KEY)
        kinds = {spec.key: spec.kind for spec in properties} | {TITLE_KEY: "text", "id": "integer"}
        columns = {spec.key: json_path_expr("values_json", spec.key) for spec in properties} | \
            {TITLE_KEY: "title", "id": "record_id"}
        compiled = compile_query(question, columns, kinds, [TITLE_KEY], "position, record_id",
                                 [("values_json", lambda needle: json.dumps(needle)[1:-1])])
        found, total = run_compiled(lambda sql, params: self.database.fetch_all(sql, params), "record_items",
                                    ("collection = ?", [self.collection]), compiled,
                                    "record_id, title, values_json, position, created_at, updated_at", columns)
        if compiled.group:
            fields = [field for field in self.fields() if field["key"] in compiled.group]
            fields += [{"key": name, "title": name.split("_")[0].capitalize(), "kind": "number", "options": [], "unit": ""}
                       for name, _sql in compiled.aggregates]
            return found, total, fields
        rows = [record_row(RecordCollection._record(row)) for row in found]
        compute_formulas(rows, properties)
        fields = self.fields(rows)
        if question.get("fields"):
            keep = set(question["fields"]) | {"id", TITLE_KEY}
            rows = [{key: value for key, value in row.items() if key in keep} for row in rows]
            fields = [field for field in fields if field["key"] in keep]
        return rows, total, fields

    def get(self, row_id) -> dict:
        row = record_row(self._record(row_id))
        compute_formulas([row], self.records.properties)
        return row

    def _clean(self, values: dict) -> tuple[str | None, dict]:
        title, clean = None, {}
        for key, value in values.items():
            if key == TITLE_KEY:
                title = str(value or "").strip()
                if not title:
                    raise EngineError("The name can't be empty", code="invalid_value")
                continue
            spec = self.records.property(key)
            if spec is not None and spec.kind == "formula":
                raise EngineError(f"{spec.title} is computed from its formula ({spec.formula})", code="not_allowed")
            clean[key] = coerce(spec.kind, value, spec.title) if spec is not None else value
        return title, clean

    def create(self, title: str | None, values: dict) -> dict:
        _title, clean = self._clean(values)
        return self.get(self.records.create(str(title or f"New {self.noun}"), clean).record_id)

    def update(self, row_id, values: dict) -> tuple[dict, dict]:
        before = self.get(row_id)
        title, clean = self._clean(values)
        self.records.update(int(row_id), title=title, values=clean)
        after = self.get(row_id)
        return after, diff({k: v for k, v in before.items() if k != "updated_at"},
                           {k: v for k, v in after.items() if k != "updated_at"})

    def duplicate(self, row_id) -> dict:
        return self.get(self.records.duplicate(self._record(row_id).record_id).record_id)

    def delete(self, row_ids: list) -> list[str]:
        ids = [int(item) for item in row_ids]
        names = [record.title for record in (self.records.get(item) for item in ids) if record]
        self.records.delete(ids)
        return names

    def move(self, row_id, before_id) -> None:
        self.records.move(self._record(row_id).record_id, before_id)

    def add_property(self, title: str, kind: str, options: tuple, formula: str) -> dict:
        if kind == "formula":
            check_formula(formula, self.records.properties)
        try:
            spec = self.records.add_property(title, kind, options, formula)
        except ValueError as error:
            raise EngineError(f"Add property: {error}", code="invalid_value") from None
        ensure_indexes(self.database, [spec])
        return spec.to_dict()

    def remove_property(self, key: str) -> None:
        spec = self.records.property(key)
        if spec is None or not spec.is_user:
            raise EngineError("Only properties you added can be removed", code="not_allowed")
        self.records.remove_property(spec.key)

    def structure(self) -> dict:
        return {**super().structure(), "kind": "records", "noun": self.noun}

    def export_json(self) -> str:
        return self.records.to_json()


def _filter_fields(node) -> set:
    if not node:
        return set()
    if "all" in node or "any" in node:
        return set().union(*[_filter_fields(child) for child in node.get("all", node.get("any")) or []])
    if "not" in node:
        return _filter_fields(node["not"])
    return {node.get("field")}
# --- END [1.3] ---
