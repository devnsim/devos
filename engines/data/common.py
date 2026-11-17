# ==============================================================================
# FILE: engines/data/common.py
# OVERVIEW: What every Data source shares: collection ids, the Source interface (the source contract each
#           kind of data implements), value checking by field kind, kind inference for files without types.
# PURPOSE:
#   1. Collection ids: record collections keep their plain names ("projects"); file tables are
#      "<kind>:<library item id>[/<table>]" - db:12/parts, csv:34, sheet:56/Budget, graph:78/Person.
#   2. Source: schema / query / get / create / update / duplicate / delete / move / add_property /
#      remove_property; a source that can't do something says so ("not_allowed") instead of failing.
#   3. Writes return diffs ({key: [old, new]}) so the engine can announce what changed.
# CONTRACT:
#   - Requires: engines.kernel (EngineError), app.data.store.records (coerce_value, PropertySpec). No Qt.
#   - Exposes: Source, FileRef, parse_collection(), file_collection(), coerce(), infer_kind(), field(), diff(),
#              FILE_KINDS, ROW_KEY
# NOTES:
#   - [2026-11-17] Created in Phase 5a of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - FILE_KINDS (dict): collection prefix -> Library kind ("db" -> "database", "csv" -> "dataset" ...).
# - ROW_KEY (str): The row key of file tables ("_rowid": a database rowid, a CSV / sheet line, a node's key);
#   record collections keep "id".
# - SHORT_VALUE (int): Values longer than this (as text) appear in diffs without old / new (default: 200).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. COMMON
#    1.1 ids
#    1.2 values
#    1.3 source
# ==============================================================================

# ==============================================================================
# [1.1] ids
# ==============================================================================
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from app.data.store.records import PropertySpec, coerce_value
from engines.kernel import EngineError

FILE_KINDS = {"db": "database", "csv": "dataset", "sheet": "sheet", "graph": "graph"}
ROW_KEY = "_rowid"
SHORT_VALUE = 200
_FILE_ID = re.compile(r"^(db|csv|sheet|graph):(\d+)(?:/(.+))?$")


@dataclass(frozen=True)
class FileRef:
    prefix: str                       # db, csv, sheet, graph
    item_id: int                      # the Library item
    table: str = ""                   # table / sheet / node table ("" for a CSV)

    @property
    def collection(self) -> str:
        return file_collection(self.prefix, self.item_id, self.table)


def file_collection(prefix: str, item_id: int, table: str = "") -> str:
    return f"{prefix}:{int(item_id)}" + (f"/{table}" if table else "")


def parse_collection(collection: str) -> FileRef | None:
    """A file table's parts, or None for a record collection. Bad file ids are a not_found error."""
    if ":" not in collection:
        return None
    match = _FILE_ID.match(collection)
    if match is None or (match.group(1) != "csv" and not match.group(3)):
        raise EngineError(f"“{collection}” isn't a Data collection (db:ID/table, csv:ID, sheet:ID/sheet, graph:ID/table)",
                          code="not_found")
    return FileRef(match.group(1), int(match.group(2)), match.group(3) or "")
# --- END [1.1] ---


# ==============================================================================
# [1.2] values
# ==============================================================================
def field(key: str, title: str, kind: str, **extra) -> dict:
    return {"key": key, "title": title, "kind": kind, "options": [], "unit": "", **extra}


def coerce(kind: str, value, title: str = ""):
    """An edited value for a field kind (None = empty); EngineError("invalid_value") says what's wrong."""
    try:
        if kind == "integer":
            number = coerce_value(PropertySpec("v", title, "number"), value)
            if number is not None and not float(number).is_integer():
                raise ValueError(f"“{value}” is not a whole number")
            return None if number is None else int(number)
        if kind == "datetime":
            if value in (None, ""):
                return None
            text = str(value).strip().replace(" ", "T")
            dt.datetime.fromisoformat(text)
            return text
        if kind in ("json", "geometry"):
            raise ValueError("this value can't be edited here")
        return coerce_value(PropertySpec("v", title, kind if kind != "select" else "text"), value)
    except ValueError as error:
        raise EngineError(f"{title or 'Value'}: {error}", code="invalid_value") from None


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def infer_kind(values: list) -> str:
    """The field kind that fits every non-empty value (files without declared types: CSV, sheets)."""
    present = [value for value in values if value not in (None, "")]
    if not present:
        return "text"
    if all(isinstance(value, bool) for value in present):
        return "checkbox"
    numbers = []
    for value in present:
        if isinstance(value, bool):
            break
        try:
            numbers.append(float(value))
        except (TypeError, ValueError):
            break
    else:
        return "integer" if all(number.is_integer() for number in numbers) else "number"
    texts = [str(value) for value in present]
    if all(_DATE.match(text) for text in texts):
        return "date"
    if all(_DATETIME.match(text) for text in texts):
        return "datetime"
    return "long_text" if any("\n" in text for text in texts) else "text"


def diff(old: dict, new: dict) -> dict:
    """{key: [old, new]} for the keys whose value changed; long values as {key: None} (the fact, not the data)."""
    changes = {}
    for key in set(old) | set(new):
        if old.get(key) != new.get(key):
            short = all(len(str(value)) <= SHORT_VALUE for value in (old.get(key), new.get(key)))
            changes[key] = [old.get(key), new.get(key)] if short else None
    return changes
# --- END [1.2] ---


# ==============================================================================
# [1.3] source
# ==============================================================================
class Source:
    """One collection of the Data engine. Rows are dicts with `key` (stable id) and `title_key`."""
    key = ROW_KEY
    title_key = ROW_KEY
    noun = "row"
    title = ""

    def __init__(self, collection: str) -> None:
        self.collection = collection

    def refuse(self, what: str, how: str = "") -> EngineError:
        return EngineError(f"{self.title or self.collection}: {what}" + (f" – {how}" if how else ""), code="not_allowed")

    def fields(self) -> list[dict]:
        raise NotImplementedError

    def schema(self) -> dict:
        return {"fields": self.fields(), "title_key": self.title_key, "key": self.key, "noun": self.noun}

    def count(self) -> int:
        return self.query({"limit": 0})[1]

    def query(self, question: dict) -> tuple[list[dict], int, list[dict]]:
        """(page rows, total matching, fields of the result)."""
        raise NotImplementedError

    def get(self, row_id) -> dict:
        raise NotImplementedError

    def create(self, title: str | None, values: dict) -> dict:
        raise self.refuse("new rows can't be added here")

    def update(self, row_id, values: dict) -> tuple[dict, dict]:
        """(row after the change, diff)."""
        raise self.refuse("this data is read-only")

    def duplicate(self, row_id) -> dict:
        raise self.refuse("rows can't be copied here")

    def delete(self, row_ids: list) -> list[str]:
        """The titles of what was deleted."""
        raise self.refuse("rows can't be deleted here")

    def move(self, row_id, before_id) -> None:
        raise self.refuse("rows keep their own order here")

    def add_property(self, title: str, kind: str, options: tuple, formula: str) -> dict:
        raise self.refuse("its columns are changed in its own tool")

    def remove_property(self, key: str) -> None:
        raise self.refuse("its columns are changed in its own tool")

    def close(self) -> None:
        """Let go of open files (the engine stops, or the file changed)."""
# --- END [1.3] ---
