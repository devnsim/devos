# ==============================================================================
# FILE: engines/data/engine.py
# OVERVIEW: Data engine - one interface for tables of every kind: record collections (Projects, Agents …),
#           the user's SQLite databases, CSV datasets, workbook sheets and Kùzu graph node tables. Views ask
#           it the same questions (Query v1) whatever the data is; it answers with Table shapes, runs each
#           question where the data lives (SQL, Cypher, in memory), checks every write, and announces each
#           change with what changed.
# PURPOSE:
#   1. The source contract (docs/views-api.md) for every collection: collections, schema, query, get,
#      create, update, duplicate, delete, move, add_property, remove_property.
#   2. Record collections: register (a module's default properties), export_json; formula properties.
#   3. graph: a Kùzu database as a Graph shape. For the specialist views: structure, cells / set_cell (sheets),
#      sql (databases) and cypher (graphs) - consoles that report what they changed.
#   4. Files come from Knowledge (Engine.ask: list, path, refresh_size); Data never looks into the Library
#      itself. Without Knowledge, record collections still work.
#   5. "changed" events: {collection, ids, op, schema, changes: {id: {key: [old, new]}}} (writes, not reads);
#      op "external" when another tool changed the file (Engine.watch on "knowledge.changed").
# CONTRACT:
#   - Requires: engines.kernel, engines.data (common, records, tables, grids, graphs), app.data.store
#               (database, records). Own connection on its thread. Uses Knowledge (engine.json "uses").
#   - Exposes: DataEngine (MANIFEST id "data"), MANIFEST
#   - Owns: record_collections, record_items, records_adapter_defaults (+ expression indexes on record_items).
# NOTES:
#   - [2026-11-17] Phase 5b: structure, cells, set_cell, sql, cypher; graph takes a collection.
#   - [2026-11-17] Phase 5b: watches knowledge.changed - files changed by other tools refresh their Data views.
#   - [2026-11-17] Created in Phase 5a of the engine plan (replaces the Phase 2 records engine).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - source: A Source object serving one collection (engines/data/common.py), kept open while the engine runs.
# - LISTED_KINDS (dict): Library kind -> collection prefix of the files Data lists as collections.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. DATA
#    1.1 manifest
#    1.2 engine
#    1.3 sources
#    1.4 reads
#    1.5 writes
# ==============================================================================

# ==============================================================================
# [1.1] manifest
# ==============================================================================
from __future__ import annotations

import json
from pathlib import Path

from app.data.store.database import DATABASE_FILE_NAME, Database, data_directory
from app.data.store.records import MIGRATIONS as RECORD_MIGRATIONS
from app.data.store.records import NAMESPACE as RECORD_NAMESPACE
from app.data.store.records import PropertySpec
from engines.data.common import FILE_KINDS, FileRef, Source, file_collection, parse_collection
from engines.data.records import MIGRATIONS, RecordsSource, ensure_indexes
from engines.kernel import Engine, EngineError, EngineManifest, Job, Reply, RequestSpec, shapes

C = ("collection",)
W = "writes"
LISTED_KINDS = {kind: prefix for prefix, kind in FILE_KINDS.items()}
MANIFEST = EngineManifest(
    "data", "Data", "0.2", "Tables of every kind: records, your databases, datasets, sheets and graphs.",
    requests=(
        RequestSpec("collections", "Every table Data can show, with its noun and size", optional=("kinds",),
                    example={"kinds": ["records"]}),
        RequestSpec("schema", "A collection's fields", params=C),
        RequestSpec("query", "Rows of a collection as a Table shape (Query v1)", params=C, optional=("query",),
                    shape="table"),
        RequestSpec("get", "One row", params=C + ("id",)),
        RequestSpec("create", "A new row", params=C, optional=("title", "values"), effect=W),
        RequestSpec("update", "Change a row's values", params=C + ("id", "values"), effect=W),
        RequestSpec("duplicate", "Copy a row", params=C + ("id",), effect=W),
        RequestSpec("delete", "Delete rows", params=C + ("ids",), effect=W),
        RequestSpec("move", "Place a record before another (None: at the end)", params=C + ("id",),
                    optional=("before_id",), effect=W),
        RequestSpec("add_property", "Add a property (a formula property computes its value)", params=C + ("title", "kind"),
                    optional=("options", "formula"), effect=W),
        RequestSpec("remove_property", "Remove a property you added (values are kept)", params=C + ("key",), effect=W),
        RequestSpec("register", "Declare a record collection's default properties", params=C + ("properties",),
                    optional=("noun",), effect=W),
        RequestSpec("export_json", "Every record of a collection as JSON text", params=C),
        RequestSpec("graph", "The nodes and relationships of a graph table's database (a sample)", params=C,
                    optional=("limit",), shape="graph"),
        RequestSpec("structure", "How a collection is built: columns, keys, indexes, relationships", params=C),
        RequestSpec("cells", "A sheet's cells: what was typed and what shows", params=C),
        RequestSpec("set_cell", "Type into a sheet cell (formulas too)", params=C + ("row", "col", "raw"), effect=W),
        RequestSpec("sql", "Run SQL on a table's database (results of each statement)", params=C + ("sql",),
                    optional=("row_limit",), effect=W, timeout_s=300),
        RequestSpec("cypher", "Run Cypher on a graph table's database", params=C + ("cypher",), optional=("row_limit",),
                    effect=W, timeout_s=300),
    ),
    events=("changed",),
    shapes=("table", "graph"),
    owns=("record_collections", "record_items", "records_adapter_defaults"),
    uses=("knowledge",),
)
# --- END [1.1] ---


# ==============================================================================
# [1.2] engine
# ==============================================================================
class DataEngine(Engine):
    MANIFEST = MANIFEST

    def start(self) -> None:
        self.database = Database(data_directory() / DATABASE_FILE_NAME)
        self.database.migrate(RECORD_NAMESPACE, RECORD_MIGRATIONS)
        self.database.migrate("records_adapter", MIGRATIONS)    # the Phase 2 name: same table, now Data's
        self._sources: dict[str, Source] = {}
        self._graphs: dict[int, object] = {}
        self.watch("knowledge.changed", self._library_changed)   # files changed by other tools, renamed, deleted

    def stop(self) -> None:
        for source in self._sources.values():
            source.close()
        for db in self._graphs.values():
            db.close()
        self.database.close()

    def _changed(self, collection: str, op: str, ids: list | None = None, changes: dict | None = None,
                 schema: bool = False) -> None:
        self.emit("changed", {"collection": collection, "op": op, "ids": ids, "schema": schema,
                              "changes": {str(key): value for key, value in (changes or {}).items()}})

    def _file_written(self, source: Source) -> None:
        ref = parse_collection(source.collection)
        if ref is not None:
            try:
                self.ask("knowledge.refresh_size", {"item_id": ref.item_id, "by": "data"})
            except EngineError as error:
                self.log("warning", f"The Library didn't hear about the change to {source.title}: {error}")
    def _library_changed(self, message) -> None:
        """A Library file changed outside Data (the Spreadsheet saved, the Database Editor edited, it was renamed
        or deleted): drop what Data holds open for it and tell every view of its tables."""
        payload = message.payload
        if payload.get("by") == "data" or payload.get("kind") not in FILE_KINDS.values():
            return
        ids = payload.get("ids")
        touched = [collection for collection in self._sources
                   if (ref := parse_collection(collection)) is not None and FILE_KINDS[ref.prefix] == payload["kind"]
                   and (ids is None or ref.item_id in ids)]
        for collection in touched:
            self._sources.pop(collection).close()
        for item_id in [item_id for item_id in self._graphs if ids is None or item_id in ids] if payload["kind"] == "graph" else []:
            self._graphs.pop(item_id).close()           # reopened on the next question: sees the other tool's writes
        for collection in touched:
            self._changed(collection, "external", schema=True)
# --- END [1.2] ---

# ==============================================================================
# [1.3] sources
# ==============================================================================
    def _defaults(self, name: str) -> tuple[list[PropertySpec], str]:
        row = self.database.fetch_one("SELECT noun, properties_json FROM records_adapter_defaults WHERE collection = ?",
                                      (name,))
        if row is None:
            return [], "record"
        return [PropertySpec.from_dict(item) for item in json.loads(row["properties_json"])], row["noun"]

    def _record_names(self) -> list[str]:
        return sorted({row[0] for row in self.database.fetch_all("SELECT collection FROM record_collections")} |
                      {row[0] for row in self.database.fetch_all("SELECT collection FROM records_adapter_defaults")})

    def _library(self, kind: str) -> list[dict]:
        """Library items of a kind, or [] when Knowledge isn't there (record collections still work)."""
        try:
            return self.ask("knowledge.list", {"kind": kind})["items"]
        except EngineError as error:
            if error.code not in ("not_attached", "unknown_engine", "detached"):
                self.log("warning", f"Couldn't list the Library's {kind} files: {error}")
            return []

    def _item(self, item_id: int) -> tuple[dict, Path]:
        try:
            item = self.ask("knowledge.item", {"item_id": item_id})["item"]
            path = Path(self.ask("knowledge.path", {"item_id": item_id})["path"])
        except EngineError as error:
            code = "not_found" if error.code == "not_found" else error.code
            raise EngineError(f"Library item {item_id}: {error}", code=code) from None
        return item, path

    def _graph(self, item_id: int):
        from engines.toolkit.kuzu_db import GraphDb, GraphDbError, kuzu_unavailable_reason
        if item_id not in self._graphs:
            reason = kuzu_unavailable_reason()
            if reason:
                raise EngineError(reason, code="unavailable")
            _item, path = self._item(item_id)
            try:
                self._graphs[item_id] = GraphDb(path)
            except GraphDbError as error:
                raise EngineError(str(error), code="database_error") from None
        return self._graphs[item_id]

    def _source(self, collection: str, must_exist: bool = True) -> Source:
        source = self._sources.get(collection)
        if source is not None:
            return source
        ref = parse_collection(collection)
        if ref is None:
            if must_exist and collection not in self._record_names():
                raise EngineError(f"There is no collection “{collection}”", code="not_found")
            defaults, noun = self._defaults(collection)
            source = RecordsSource(self.database, collection, defaults, noun)
        else:
            source = self._file_source(ref)
        self._sources[collection] = source
        return source

    def _file_source(self, ref: FileRef) -> Source:
        if ref.prefix == "graph":
            from engines.data.graphs import NodeTable
            item, _path = self._item(ref.item_id)
            return NodeTable(ref, self._graph(ref.item_id), f"{item['name']} · {ref.table}")
        item, path = self._item(ref.item_id)
        if item["kind"] != FILE_KINDS[ref.prefix]:
            raise EngineError(f"“{item['name']}” is a {item['kind']}, not a {FILE_KINDS[ref.prefix]}", code="not_found")
        if ref.prefix == "db":
            from engines.data.tables import SqliteTable
            return SqliteTable(ref, path, f"{item['name']} · {ref.table}")
        from engines.data.grids import GridTable
        return GridTable(ref, path, item["name"] if ref.prefix == "csv" else f"{item['name']} · {ref.table}")

    def _file_tables(self, kind: str, item: dict) -> list[tuple[str, str]]:
        """(collection, title) of each table in one Library file."""
        prefix = LISTED_KINDS[kind]
        if prefix == "csv":
            return [(file_collection("csv", item["item_id"]), item["name"])]
        _item, path = self._item(item["item_id"])
        if prefix == "db":
            from engines.data.tables import table_names
            names = [name for name, _type in table_names(path)]
        elif prefix == "sheet":
            from engines.data.grids import sheet_names
            names = sheet_names(path)
        else:
            names = self._graph(item["item_id"]).node_tables()
        return [(file_collection(prefix, item["item_id"], name), f"{item['name']} · {name}") for name in names]
# --- END [1.3] ---

# ==============================================================================
# [1.4] reads
# ==============================================================================
    def on_collections(self, payload: dict, _job: Job) -> dict:
        kinds = payload.get("kinds") or ["records", *LISTED_KINDS]
        entries = []
        if "records" in kinds:
            for name in self._record_names():
                source = self._source(name, must_exist=False)
                entries.append({"collection": name, "title": source.title, "noun": source.noun, "count": source.count(),
                                "kind": "records"})
        for kind in (kind for kind in LISTED_KINDS if kind in kinds):
            for item in self._library(kind):
                try:
                    tables = self._file_tables(kind, item)
                except Exception as error:              # one unreadable file never hides the others
                    self.log("warning", f"Couldn't read the tables of “{item['name']}”: {error}")
                    continue
                for collection, title in tables:
                    entries.append({"collection": collection, "title": title, "noun": "node" if kind == "graph" else "row",
                                    "kind": kind, "item_id": item["item_id"]})
        return {"collections": entries}

    def on_schema(self, payload: dict, _job: Job) -> dict:
        return self._source(payload["collection"]).schema()

    def on_query(self, payload: dict, _job: Job) -> Reply:
        source = self._source(payload["collection"])
        question = payload.get("query") or {}
        rows, total, fields = source.query(question)
        grouped = bool(question.get("group_by"))
        shape, attachments = shapes.table(fields, rows, total=total, offset=question.get("offset", 0),
                                          key="id" if grouped else source.key,
                                          title_key=question["group_by"][0] if grouped else source.title_key,
                                          source={"engine": "data", "collection": source.collection, "noun": source.noun})
        return Reply(shape, attachments)

    def on_get(self, payload: dict, _job: Job) -> dict:
        source = self._source(payload["collection"])
        return {"record": source.get(payload["id"]), "id": payload["id"]}

    def on_export_json(self, payload: dict, _job: Job) -> dict:
        source = self._source(payload["collection"])
        if not isinstance(source, RecordsSource):
            raise source.refuse("only record collections export as JSON here")
        return {"json": source.export_json()}

    def _file_ref(self, collection: str, prefixes: tuple[str, ...], what: str) -> FileRef:
        ref = parse_collection(collection)
        if ref is None or ref.prefix not in prefixes:
            raise EngineError(f"{what} works on {' / '.join(prefixes)} tables, not “{collection}”", code="not_allowed")
        return ref

    def on_graph(self, payload: dict, _job: Job) -> dict:
        from engines.data.graphs import graph_shape
        ref = self._file_ref(payload["collection"], ("graph",), "The graph")
        return graph_shape(self._graph(ref.item_id), int(payload.get("limit") or 300))

    def on_structure(self, payload: dict, _job: Job) -> dict:
        return self._source(payload["collection"]).structure()

    def on_cells(self, payload: dict, _job: Job) -> dict:
        self._file_ref(payload["collection"], ("sheet", "csv"), "The sheet view")
        return self._source(payload["collection"]).cells()
# --- END [1.4] ---

# ==============================================================================
# [1.5] writes
# ==============================================================================
    def on_register(self, payload: dict, _job: Job) -> dict:
        name = payload["collection"]
        if ":" in name:
            raise EngineError("Record collection names can't contain “:”", code="invalid_value")
        properties = [PropertySpec.from_dict(item) for item in payload["properties"]]
        self.database.execute(
            "INSERT INTO records_adapter_defaults (collection, noun, properties_json) VALUES (?, ?, ?) "
            "ON CONFLICT(collection) DO UPDATE SET noun = excluded.noun, properties_json = excluded.properties_json",
            (name, payload.get("noun") or "record", json.dumps([spec.to_dict() for spec in properties])))
        ensure_indexes(self.database, properties)
        self._sources.pop(name, None)
        return {}

    def _id_of(self, source: Source, row: dict):
        return row[source.key]

    def on_create(self, payload: dict, _job: Job) -> dict:
        source = self._source(payload["collection"], must_exist=":" in payload["collection"])
        row = source.create(payload.get("title"), payload.get("values") or {})
        row_id = self._id_of(source, row)
        self.log("info", f"Created {source.noun} “{row.get(source.title_key, row_id)}” in {source.title}")
        self._file_written(source)
        self._changed(source.collection, "create", [row_id])
        return {"record": row, "id": row_id}

    def on_update(self, payload: dict, _job: Job) -> dict:
        source = self._source(payload["collection"])
        row, changes = source.update(payload["id"], payload["values"])
        if changes:
            self._file_written(source)
            self._changed(source.collection, "update", [payload["id"]], {payload["id"]: changes})
        return {"record": row, "id": payload["id"], "changes": changes}

    def on_duplicate(self, payload: dict, _job: Job) -> dict:
        source = self._source(payload["collection"])
        row = source.duplicate(payload["id"])
        row_id = self._id_of(source, row)
        self._file_written(source)
        self._changed(source.collection, "create", [row_id])
        return {"record": row, "id": row_id}

    def on_delete(self, payload: dict, _job: Job) -> dict:
        source = self._source(payload["collection"])
        names = source.delete(list(payload["ids"]))
        what = f"“{names[0]}”" if len(names) == 1 else f"{len(names)} {source.noun}s"
        self.log("info", f"Deleted {what} from {source.title}")
        self._file_written(source)
        self._changed(source.collection, "delete", list(payload["ids"]))
        return {"deleted": len(names), "what": what}

    def on_move(self, payload: dict, _job: Job) -> dict:
        source = self._source(payload["collection"])
        source.move(payload["id"], payload.get("before_id"))
        self._changed(source.collection, "move", [payload["id"]])
        return {}

    def on_add_property(self, payload: dict, _job: Job) -> dict:
        source = self._source(payload["collection"], must_exist=":" in payload["collection"])
        spec = source.add_property(payload["title"], payload["kind"], tuple(payload.get("options") or ()),
                                   str(payload.get("formula") or ""))
        self._file_written(source)
        self._changed(source.collection, "schema", schema=True)
        return {"field": spec}

    def on_set_cell(self, payload: dict, _job: Job) -> dict:
        self._file_ref(payload["collection"], ("sheet", "csv"), "Typing into cells")
        source = self._source(payload["collection"])
        source.set_cell(int(payload["row"]), int(payload["col"]), str(payload["raw"]))
        self._file_written(source)
        self._changed(source.collection, "update", schema=int(payload["row"]) == 0)   # values anywhere may follow
        return {}

    def _console(self, payload: dict, prefix: str, run) -> dict:
        ref = self._file_ref(payload["collection"], (prefix,), "This console")
        source = self._source(payload["collection"])
        results, wrote = run(source, int(payload.get("row_limit") or 5000))
        if wrote:
            self._file_written(source)
            for collection in [name for name in self._sources if (other := parse_collection(name)) is not None
                               and other.prefix == prefix and other.item_id == ref.item_id]:
                self._changed(collection, "external", schema=True)
            self.log("info", f"Ran {len(results)} statement(s) that changed {source.title}")
        return {"results": results, "wrote": wrote}

    def on_sql(self, payload: dict, _job: Job) -> dict:
        return self._console(payload, "db", lambda source, limit: source.sql(payload["sql"], limit))

    def on_cypher(self, payload: dict, _job: Job) -> dict:
        return self._console(payload, "graph", lambda source, limit: source.cypher(payload["cypher"], limit))

    def on_remove_property(self, payload: dict, _job: Job) -> dict:
        source = self._source(payload["collection"])
        source.remove_property(payload["key"])
        self._changed(source.collection, "schema", schema=True)
        return {}
# --- END [1.5] ---
