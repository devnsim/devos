# ==============================================================================
# FILE: engines/records/engine.py
# OVERVIEW: Records engine - a TEMPORARY adapter (Phase 2) that serves the existing record collections
#           (Projects, Agents ...) over the bus as Table shapes, so views talk to an engine, never to the
#           database. Phase 5's Data engine replaces it with the same requests.
# PURPOSE:
#   1. The only writer of record collections while it runs (one connection, its own thread): create,
#      update, duplicate, delete, move, properties - every change announced as a "changed" event, so every
#      view in every window refreshes.
#   2. query: Query v1 run by the table toolkit; the result is a Table shape whose select fields carry
#      every option in use (typed values become choices).
#   3. register: a module declares its collection's default properties (and noun); they are stored, so a
#      Data view finds them even when that module isn't open.
# CONTRACT:
#   - Requires: engines.kernel, engines.toolkit.table, app.data.store (database, records; no Qt). Own
#               connection, opened on the engine thread.
#   - Exposes: RecordsEngine (MANIFEST id "records"), MANIFEST, record_row(record)
#   - Tables (namespace "records_adapter"): records_adapter_defaults (collection, noun, properties_json).
# NOTES:
#   - [2026-11-17] Phase 3: engine.json; collections has an example and query declares its shape for the kit.
#   - [2026-11-17] Created in Phase 2 of the engine plan (adapter until the Data engine, Phase 5).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - row (dict): A record as Table-shape row: "id", "title", every property value, "created_at", "updated_at".
# - defaults: The properties a module declares for its collection (users add their own on top).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. RECORDS_ENGINE
#    1.1 manifest
#    1.2 engine
#    1.3 reads
#    1.4 writes
# ==============================================================================

# ==============================================================================
# [1.1] manifest
# ==============================================================================
from __future__ import annotations

import json

from app.data.store.database import DATABASE_FILE_NAME, Database, data_directory
from app.data.store.records import MIGRATIONS as RECORD_MIGRATIONS
from app.data.store.records import NAMESPACE as RECORD_NAMESPACE
from app.data.store.records import TITLE_KEY, PropertySpec, Record, RecordCollection, coerce_value
from engines.kernel import Engine, EngineError, EngineManifest, Job, Reply, RequestSpec
from engines.kernel import shapes
from engines.toolkit.table import run_query

C = ("collection",)
MANIFEST = EngineManifest(
    "records", "Records", "0.1", "Record collections (Projects, Agents …) as tables: query, change, properties.",
    requests=(
        RequestSpec("register", "Declare a collection's default properties", params=C + ("properties",),
                    optional=("noun",), effect="writes"),
        RequestSpec("collections", "Every collection with its noun and size", example={}),
        RequestSpec("schema", "A collection's fields (properties)", params=C),
        RequestSpec("query", "Rows of a collection as a Table shape", params=C, optional=("query",), shape="table"),
        RequestSpec("get", "One record", params=C + ("id",)),
        RequestSpec("create", "A new record", params=C, optional=("title", "values"), effect="writes"),
        RequestSpec("update", "Change a record's title and / or values", params=C + ("id", "values"), effect="writes"),
        RequestSpec("duplicate", "Copy a record", params=C + ("id",), effect="writes"),
        RequestSpec("delete", "Delete records", params=C + ("ids",), effect="writes"),
        RequestSpec("move", "Place a record before another (None: at the end)", params=C + ("id",),
                    optional=("before_id",), effect="writes"),
        RequestSpec("add_property", "Add a property to every record", params=C + ("title", "kind"),
                    optional=("options",), effect="writes"),
        RequestSpec("remove_property", "Remove a user property (values are kept)", params=C + ("key",), effect="writes"),
        RequestSpec("export_json", "Every record of a collection as JSON text", params=C),
    ),
    events=("changed",),
    shapes=("table",),
    owns=("record_collections", "record_items", "records_adapter_defaults"),
)
MIGRATIONS = ["""
    CREATE TABLE records_adapter_defaults (
        collection TEXT PRIMARY KEY, noun TEXT NOT NULL DEFAULT 'record', properties_json TEXT NOT NULL DEFAULT '[]'
    )
"""]


def record_row(record: Record) -> dict:
    return {"id": record.record_id, TITLE_KEY: record.title, **record.values,
            "created_at": record.created_at, "updated_at": record.updated_at}
# --- END [1.1] ---


# ==============================================================================
# [1.2] engine
# ==============================================================================
class RecordsEngine(Engine):
    MANIFEST = MANIFEST

    def start(self) -> None:
        self.database = Database(data_directory() / DATABASE_FILE_NAME)
        self.database.migrate(RECORD_NAMESPACE, RECORD_MIGRATIONS)      # a fresh data folder has no record tables yet
        self.database.migrate("records_adapter", MIGRATIONS)
        self._collections: dict[str, RecordCollection] = {}

    def stop(self) -> None:
        self.database.close()

    def _defaults(self, name: str) -> tuple[list[PropertySpec], str]:
        row = self.database.fetch_one("SELECT noun, properties_json FROM records_adapter_defaults WHERE collection = ?", (name,))
        if row is None:
            return [], "record"
        return [PropertySpec.from_dict(item) for item in json.loads(row["properties_json"])], row["noun"]

    def _collection(self, name: str, must_exist: bool = True) -> RecordCollection:
        if name not in self._collections:
            known = self.database.fetch_one("SELECT 1 FROM record_collections WHERE collection = ?", (name,)) or \
                self.database.fetch_one("SELECT 1 FROM records_adapter_defaults WHERE collection = ?", (name,))
            if must_exist and known is None:
                raise EngineError(f"There is no collection “{name}”", code="not_found")
            self._collections[name] = RecordCollection(self.database, name, self._defaults(name)[0])
        return self._collections[name]

    def _changed(self, name: str, ids: list[int] | None = None, schema: bool = False) -> None:
        self.emit("changed", {"collection": name, "ids": ids, "schema": schema})

    def _record(self, collection: RecordCollection, record_id) -> Record:
        record = collection.get(int(record_id))
        if record is None:
            raise EngineError(f"That {self._defaults(collection.collection)[1]} no longer exists", code="not_found")
        return record
# --- END [1.2] ---

# ==============================================================================
# [1.3] reads
# ==============================================================================
    def _fields(self, collection: RecordCollection) -> list[dict]:
        """Declared properties as shape fields; select options include every value in use."""
        records = collection.records()
        fields = []
        for spec in collection.properties:
            options = list(spec.options)
            if spec.kind in ("select", "multi_select"):
                for record in records:
                    value = record.values.get(spec.key)
                    for item in (value if isinstance(value, list) else [value]):
                        if item and item not in options:
                            options.append(item)
            fields.append({"key": spec.key, "title": spec.title, "kind": spec.kind, "options": options, "unit": "",
                           "is_user": spec.is_user})
        return fields

    def on_register(self, payload: dict, _job: Job) -> dict:
        properties = [PropertySpec.from_dict(item).to_dict() for item in payload["properties"]]
        self.database.execute(
            "INSERT INTO records_adapter_defaults (collection, noun, properties_json) VALUES (?, ?, ?) "
            "ON CONFLICT(collection) DO UPDATE SET noun = excluded.noun, properties_json = excluded.properties_json",
            (payload["collection"], payload.get("noun") or "record", json.dumps(properties)))
        self._collections.pop(payload["collection"], None)
        return {}

    def on_collections(self, _payload: dict, _job: Job) -> dict:
        names = sorted({row[0] for row in self.database.fetch_all("SELECT collection FROM record_collections")} |
                       {row[0] for row in self.database.fetch_all("SELECT collection FROM records_adapter_defaults")})
        return {"collections": [{"collection": name, "noun": self._defaults(name)[1],
                                 "count": self._collection(name, must_exist=False).count()} for name in names]}

    def on_schema(self, payload: dict, _job: Job) -> dict:
        collection = self._collection(payload["collection"])
        return {"fields": self._fields(collection), "title_key": TITLE_KEY, "noun": self._defaults(collection.collection)[1]}

    def on_query(self, payload: dict, _job: Job) -> Reply:
        collection = self._collection(payload["collection"])
        fields = self._fields(collection)
        rows, total, result_fields = run_query([record_row(record) for record in collection.records()], fields,
                                               payload.get("query") or {}, TITLE_KEY)
        question = payload.get("query") or {}
        shape, attachments = shapes.table(result_fields, rows, total=total, offset=question.get("offset", 0),
                                          source={"engine": "records", "collection": collection.collection,
                                                  "noun": self._defaults(collection.collection)[1]})
        return Reply(shape, attachments)

    def on_get(self, payload: dict, _job: Job) -> dict:
        collection = self._collection(payload["collection"])
        return {"record": record_row(self._record(collection, payload["id"]))}

    def on_export_json(self, payload: dict, _job: Job) -> dict:
        return {"json": self._collection(payload["collection"]).to_json()}
# --- END [1.3] ---

# ==============================================================================
# [1.4] writes
# ==============================================================================
    def _coerced(self, collection: RecordCollection, values: dict) -> tuple[str | None, dict]:
        """(new title or None, values) checked against the properties; EngineError says which value is wrong."""
        title, clean = None, {}
        for key, value in values.items():
            if key == TITLE_KEY:
                title = str(value or "").strip()
                if not title:
                    raise EngineError("The name can't be empty", code="invalid_value")
                continue
            spec = collection.property(key)
            try:
                clean[key] = coerce_value(spec, value) if spec is not None else value
            except ValueError as error:
                raise EngineError(f"{spec.title}: {error}", code="invalid_value") from None
        return title, clean

    def on_create(self, payload: dict, _job: Job) -> dict:
        name = payload["collection"]
        collection = self._collection(name, must_exist=False)
        noun = self._defaults(name)[1]
        _title, values = self._coerced(collection, payload.get("values") or {})
        record = collection.create(str(payload.get("title") or f"New {noun}"), values)
        self.log("info", f"Created {noun} “{record.title}”")
        self._changed(name, [record.record_id])
        return {"record": record_row(record)}

    def on_update(self, payload: dict, _job: Job) -> dict:
        collection = self._collection(payload["collection"])
        record = self._record(collection, payload["id"])
        title, values = self._coerced(collection, payload["values"])
        updated = collection.update(record.record_id, title=title, values=values)
        self._changed(collection.collection, [record.record_id])
        return {"record": record_row(updated)}

    def on_duplicate(self, payload: dict, _job: Job) -> dict:
        collection = self._collection(payload["collection"])
        copy = collection.duplicate(self._record(collection, payload["id"]).record_id)
        self._changed(collection.collection, [copy.record_id])
        return {"record": record_row(copy)}

    def on_delete(self, payload: dict, _job: Job) -> dict:
        collection = self._collection(payload["collection"])
        ids = [int(item) for item in payload["ids"]]
        names = [record.title for record in (collection.get(item) for item in ids) if record]
        collection.delete(ids)
        noun = self._defaults(collection.collection)[1]
        what = f"“{names[0]}”" if len(names) == 1 else f"{len(names)} {noun}s"
        self.log("info", f"Deleted {what}")
        self._changed(collection.collection, ids)
        return {"deleted": len(names), "what": what}

    def on_move(self, payload: dict, _job: Job) -> dict:
        collection = self._collection(payload["collection"])
        collection.move(self._record(collection, payload["id"]).record_id, payload.get("before_id"))
        self._changed(collection.collection, [int(payload["id"])])
        return {}

    def on_add_property(self, payload: dict, _job: Job) -> dict:
        collection = self._collection(payload["collection"], must_exist=False)
        try:
            spec = collection.add_property(payload["title"], payload["kind"], tuple(payload.get("options") or ()))
        except ValueError as error:
            raise EngineError(f"Add property: {error}", code="invalid_value") from None
        self._changed(collection.collection, schema=True)
        return {"field": spec.to_dict()}

    def on_remove_property(self, payload: dict, _job: Job) -> dict:
        collection = self._collection(payload["collection"])
        spec = collection.property(payload["key"])
        if spec is None or not spec.is_user:
            raise EngineError("Only properties you added can be removed", code="not_allowed")
        collection.remove_property(spec.key)
        self._changed(collection.collection, schema=True)
        return {}
# --- END [1.4] ---
