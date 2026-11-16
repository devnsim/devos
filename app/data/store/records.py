# ==============================================================================
# FILE: app/data/store/records.py
# OVERVIEW: Record collections - the reusable "every line is a record with properties" model (Notion-like)
#           on SQLite. A module declares a collection (agents, projects ...) and its default properties;
#           users can add their own properties; every view (table, board, timeline, calendar, form) reads
#           the same records.
# PURPOSE:
#   1. PropertySpec: one declared property (key, title, kind, options). Kinds: text, long_text, number,
#      select, multi_select, date, checkbox, url.
#   2. RecordCollection: list / search / sort / create / update / duplicate / delete / reorder records;
#      user-added properties stored with the collection; listeners so every open view stays in sync.
#   3. Value helpers: coerce_value() (text from an editor -> stored value), display_text().
# CONTRACT:
#   - Requires: app.data.store.database (Database, get_database). No Qt.
#   - Exposes: PROPERTY_KINDS, PropertySpec, Record, RecordCollection, get_collection(name, defaults),
#              coerce_value(spec, value), display_text(spec, value), TITLE_KEY
#   - Tables (namespace "records"): record_collections, record_items. Values are JSON per record, so a
#     new property never needs a schema migration.
# NOTES:
#   - [2026-11-16] Created to explore the "everything is a database" model with the Agents and Projects
#                  modules. Deliberately self-contained (two tables, JSON values): pages and other modules
#                  are not moved onto it until the data layer is designed.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - collection (str): Name of a set of records ("agents", "projects"); one module type usually owns one.
# - record: One line of a collection: a title plus a value per property.
# - property: A declared field of a collection's records (PropertySpec).
# - TITLE_KEY (str): Property key of the record title, stored in its own column ("title").
# - user property: A property the user added (stored in record_collections); can be removed again.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. TYPES
#    1.1 imports_and_schema
#    1.2 property_spec
#    1.3 values
# 2. COLLECTION
#    2.1 init_and_properties
#    2.2 queries
#    2.3 changes
#    2.4 shared_instances
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_schema
# ==============================================================================
from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import asdict, dataclass, field

from app.data.store.database import Database, get_database

NAMESPACE = "records"
MIGRATIONS = [
    """
    CREATE TABLE record_collections (
        collection      TEXT PRIMARY KEY,
        properties_json TEXT NOT NULL DEFAULT '[]'
    );
    CREATE TABLE record_items (
        record_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        collection  TEXT NOT NULL,
        title       TEXT NOT NULL DEFAULT '',
        values_json TEXT NOT NULL DEFAULT '{}',
        position    REAL NOT NULL DEFAULT 0,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL
    );
    CREATE INDEX record_items_collection ON record_items (collection, position);
    """,
]
PROPERTY_KINDS = ("text", "long_text", "number", "select", "multi_select", "date", "checkbox", "url")
TITLE_KEY = "title"
DATE_FORMAT = "%Y-%m-%d"
# --- END [1.1] ---


# ==============================================================================
# [1.2] property_spec
# ==============================================================================
@dataclass(frozen=True)
class PropertySpec:
    key: str
    title: str
    kind: str = "text"
    options: tuple[str, ...] = ()               # select / multi_select choices (users may type new ones)
    is_user: bool = False                       # added by the user (removable)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["options"] = list(self.options)
        return data

    @staticmethod
    def from_dict(data: dict) -> "PropertySpec":
        return PropertySpec(str(data["key"]), str(data.get("title") or data["key"]), str(data.get("kind", "text")),
                            tuple(data.get("options") or ()), bool(data.get("is_user", False)))


@dataclass
class Record:
    record_id: int
    title: str
    values: dict = field(default_factory=dict)
    position: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0

    def value(self, key: str):
        return self.title if key == TITLE_KEY else self.values.get(key)
# --- END [1.2] ---

# ==============================================================================
# [1.3] values
# ==============================================================================
def coerce_value(spec: PropertySpec, value):
    """Editor value -> stored value (None = empty). Raises ValueError for text that doesn't fit the kind."""
    if spec.kind == "checkbox":
        return bool(value)
    if spec.kind == "multi_select":
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",")]
        return [str(part) for part in (value or []) if str(part).strip()] or None
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if spec.kind == "number":
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"“{value}” is not a number") from None
        return int(number) if number.is_integer() else number
    if spec.kind == "date":
        text = value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else str(value).strip()
        try:
            return dt.datetime.strptime(text[:10], DATE_FORMAT).date().isoformat()
        except ValueError:
            raise ValueError(f"“{text}” is not a date (use YYYY-MM-DD)") from None
    return str(value) if spec.kind == "long_text" else str(value).strip()


def display_text(spec: PropertySpec, value) -> str:
    if value is None:
        return ""
    if spec.kind == "checkbox":
        return "✓" if value else ""
    if spec.kind == "multi_select":
        return ", ".join(value)
    if spec.kind == "long_text":
        first_line = str(value).strip().splitlines()[0] if str(value).strip() else ""
        return first_line[:80]
    return str(value)


def parse_date(value) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None
# --- END [1.3] ---


# ==============================================================================
# [2.1] init_and_properties
# ==============================================================================
class RecordCollection:
    def __init__(self, database: Database, collection: str, default_properties: list[PropertySpec]) -> None:
        database.migrate(NAMESPACE, MIGRATIONS)
        self.database, self.collection = database, collection
        self.default_properties = [spec for spec in default_properties if spec.key != TITLE_KEY]
        self._listeners: list = []
        row = database.fetch_one("SELECT properties_json FROM record_collections WHERE collection = ?", (collection,))
        if row is None:
            database.execute("INSERT INTO record_collections (collection) VALUES (?)", (collection,))
        self._user_properties = [PropertySpec.from_dict(item) for item in json.loads(row[0] if row else "[]")]

    @property
    def properties(self) -> list[PropertySpec]:
        """Declared properties (defaults first, then the user's), without the title."""
        default_keys = {spec.key for spec in self.default_properties}
        return self.default_properties + [spec for spec in self._user_properties if spec.key not in default_keys]

    def property(self, key: str) -> PropertySpec | None:
        if key == TITLE_KEY:
            return PropertySpec(TITLE_KEY, "Name")
        return next((spec for spec in self.properties if spec.key == key), None)

    def add_property(self, title: str, kind: str, options: tuple[str, ...] = ()) -> PropertySpec:
        if kind not in PROPERTY_KINDS:
            raise ValueError(f"unknown property kind {kind!r}")
        title = title.strip()
        if not title:
            raise ValueError("a property needs a name")
        base = "".join(char if char.isalnum() else "_" for char in title.lower()).strip("_") or "property"
        keys = {spec.key for spec in self.properties} | {TITLE_KEY}
        key, number = base, 2
        while key in keys:
            key, number = f"{base}_{number}", number + 1
        spec = PropertySpec(key, title, kind, tuple(options), is_user=True)
        self._user_properties.append(spec)
        self._save_properties()
        return spec

    def remove_property(self, key: str) -> None:
        """Forget a user property (its values stay in the records, so re-adding it brings them back)."""
        self._user_properties = [spec for spec in self._user_properties if spec.key != key]
        self._save_properties()

    def add_option(self, key: str, option: str) -> None:
        """Remember a new select option typed by the user (user properties only; defaults are fixed)."""
        self._user_properties = [
            PropertySpec(spec.key, spec.title, spec.kind, spec.options + (option,), True)
            if spec.key == key and option not in spec.options else spec
            for spec in self._user_properties]
        self._save_properties()

    def _save_properties(self) -> None:
        self.database.execute("UPDATE record_collections SET properties_json = ? WHERE collection = ?",
                              (json.dumps([spec.to_dict() for spec in self._user_properties]), self.collection))
        self._changed()
# --- END [2.1] ---

# ==============================================================================
# [2.2] queries
# ==============================================================================
    def records(self, search: str = "", sort_key: str | None = None, descending: bool = False) -> list[Record]:
        rows = self.database.fetch_all(
            "SELECT * FROM record_items WHERE collection = ? ORDER BY position, record_id", (self.collection,))
        records = [self._record(row) for row in rows]
        if search.strip():
            needle = search.strip().lower()
            records = [record for record in records
                       if needle in record.title.lower() or needle in json.dumps(record.values).lower()]
        if sort_key:
            def sort_value(record: Record):
                value = record.value(sort_key)
                if isinstance(value, list):
                    value = ", ".join(value)
                return (0, value, "") if isinstance(value, (int, float)) else (1, 0, str(value).lower())
            filled = [record for record in records if record.value(sort_key) not in (None, "", [])]
            empty = [record for record in records if record.value(sort_key) in (None, "", [])]
            records = sorted(filled, key=sort_value, reverse=descending) + empty      # empty values always last
        return records

    def get(self, record_id: int) -> Record | None:
        row = self.database.fetch_one("SELECT * FROM record_items WHERE record_id = ? AND collection = ?",
                                      (record_id, self.collection))
        return self._record(row) if row else None

    def count(self) -> int:
        return self.database.fetch_one("SELECT COUNT(*) FROM record_items WHERE collection = ?", (self.collection,))[0]

    @staticmethod
    def _record(row) -> Record:
        return Record(row["record_id"], row["title"], json.loads(row["values_json"]), row["position"],
                      row["created_at"], row["updated_at"])

    def to_json(self, records: list[Record] | None = None) -> str:
        records = self.records() if records is None else records
        return json.dumps([{"id": record.record_id, TITLE_KEY: record.title, **record.values} for record in records],
                          indent=2, ensure_ascii=False)
# --- END [2.2] ---

# ==============================================================================
# [2.3] changes
# ==============================================================================
    def create(self, title: str = "", values: dict | None = None) -> Record:
        now = time.time()
        last = self.database.fetch_one("SELECT MAX(position) FROM record_items WHERE collection = ?", (self.collection,))[0]
        cursor = self.database.execute(
            "INSERT INTO record_items (collection, title, values_json, position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (self.collection, title, json.dumps(self._clean(values or {})), (last or 0) + 1, now, now))
        self._changed()
        return self.get(cursor.lastrowid)

    def update(self, record_id: int, title: str | None = None, values: dict | None = None) -> Record:
        """Change the title and / or merge values (a None value clears that property)."""
        record = self.get(record_id)
        if record is None:
            raise KeyError(f"record #{record_id} no longer exists")
        merged = {**record.values, **(values or {})}
        self.database.execute(
            "UPDATE record_items SET title = ?, values_json = ?, updated_at = ? WHERE record_id = ?",
            (record.title if title is None else title, json.dumps(self._clean(merged)), time.time(), record_id))
        self._changed()
        return self.get(record_id)

    def set_value(self, record_id: int, key: str, value) -> Record:
        if key == TITLE_KEY:
            return self.update(record_id, title=str(value or ""))
        return self.update(record_id, values={key: value})

    def duplicate(self, record_id: int) -> Record:
        record = self.get(record_id)
        return self.create(f"{record.title} (copy)", dict(record.values))

    def delete(self, record_ids: list[int]) -> None:
        with self.database.transaction():
            for record_id in record_ids:
                self.database.execute("DELETE FROM record_items WHERE record_id = ? AND collection = ?",
                                      (record_id, self.collection))
        self._changed()

    def move(self, record_id: int, before_id: int | None) -> None:
        """Place a record before another one (None = at the end)."""
        ordered = [record.record_id for record in self.records() if record.record_id != record_id]
        index = ordered.index(before_id) if before_id in ordered else len(ordered)
        ordered.insert(index, record_id)
        with self.database.transaction():
            for position, item_id in enumerate(ordered, start=1):
                self.database.execute("UPDATE record_items SET position = ? WHERE record_id = ?", (position, item_id))
        self._changed()

    @staticmethod
    def _clean(values: dict) -> dict:
        return {key: value for key, value in values.items() if value is not None and key != TITLE_KEY}

    def add_listener(self, listener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _changed(self) -> None:
        for listener in list(self._listeners):
            listener()
# --- END [2.3] ---

# ==============================================================================
# [2.4] shared_instances
# ==============================================================================
_collections: dict[tuple[int, str], RecordCollection] = {}


def get_collection(collection: str, default_properties: list[PropertySpec], database: Database | None = None) -> RecordCollection:
    """One RecordCollection per (database, name), so every open view hears every change."""
    database = database or get_database()
    key = (id(database), collection)
    if key not in _collections:
        _collections[key] = RecordCollection(database, collection, default_properties)
    return _collections[key]
# --- END [2.4] ---
