# ==============================================================================
# FILE: engines/knowledge/engine.py
# OVERVIEW: Knowledge engine - the only owner of the Library (<data>/library/ and its index): your notes,
#           datasets, captures, CAD models, databases and other files. Everything else asks it.
# PURPOSE:
#   1. File service (item_id based): list, item, read_text, search (names and note text), create_text, add_file,
#      add_bytes (attachment "data"), write_text, rename, export, path / new_path / register_path (tools that
#      open a file with their own library: SQLite, Kùzu, openpyxl - local only), unique_name, refresh_size.
#   2. View source contract: collections ("library" = everything, one per kind), schema, query (Table shape),
#      get, create (a new note), update (rename), duplicate, delete; every change -> "changed" event.
#   3. links: notes and their [[links]] as a Graph shape (the note-link graph); read_text lists a note's links too.
#   A core engine: it stays attached (engine.json "core").
# CONTRACT:
#   - Requires: engines.kernel, engines.toolkit.table, engines.knowledge.store, app.data (database,
#               content_kinds; no Qt). Its own database connection, opened on its thread.
#   - Events: changed {kind, ids, content, by} ("by": who changed it, when they said - refresh_size).
#   - Exposes: KnowledgeEngine (MANIFEST id "knowledge"), MANIFEST, item_payload(item), item_row(item), LINK
# NOTES:
#   - [2026-11-17] refresh_size announces the change, with an optional "by" (Data skips its own writes).
#   - [2026-11-17] read_text fills the Document shape's links with a note's [[links]].
#   - [2026-11-17] Phase 4: the whole Library (file service, source contract, links); the store moved in here.
#   - [2026-11-17] Shapes use the kernel names ("table", "document"; the conformance kit caught it); examples for the kit.
#   - [2026-11-17] Created in Phase 1 of the engine plan (read-only stub).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - item payload (dict): item_id, kind, name, extension, size_bytes, created_at, updated_at, metadata.
# - row (dict): An item as a Table row: id, title (its name), kind, extension, size, created, updated.
# - LINK (regex): A note link, [[Note name]] or [[Note name|label]] or [[Note name#heading]].
# - SEARCH_TEXT_LIMIT (int): Notes larger than this (bytes) are searched by name only (default: 2 MB).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. KNOWLEDGE
#    1.1 manifest
#    1.2 engine
#    1.3 file_service
#    1.4 source_contract
#    1.5 links
# ==============================================================================

# ==============================================================================
# [1.1] manifest
# ==============================================================================
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from app.data.content_kinds import CONTENT_KINDS
from app.data.store.database import DATABASE_FILE_NAME, Database, data_directory
from engines.kernel import Engine, EngineError, EngineManifest, Job, Reply, RequestSpec, shapes
from engines.knowledge.store import ContentItem, ContentStore
from engines.toolkit.table import run_query

LINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
SEARCH_TEXT_LIMIT = 2 * 1024 * 1024
ITEM, KIND = ("item_id",), ("kind",)
W = "writes"
MANIFEST = EngineManifest(
    "knowledge", "Knowledge", "0.2", "Your files and documents: the Library (notes, datasets, captures, databases …).",
    requests=(
        RequestSpec("list", "Library items, newest first", optional=KIND, example={"kind": "note"}),
        RequestSpec("item", "One Library item", params=ITEM),
        RequestSpec("read_text", "A text item's contents (optionally only the first lines)", params=ITEM,
                    optional=("max_lines",), shape="document"),
        RequestSpec("search", "Items whose name (or note text) contains the text", params=("text",),
                    optional=("kind", "limit"), example={"text": "a", "limit": 5}),
        RequestSpec("create_text", "A new text item", params=KIND + ("name",), optional=("text", "extension", "metadata"),
                    effect=W),
        RequestSpec("add_file", "Copy a file on this machine into the Library", params=KIND + ("source_path",),
                    optional=("name", "metadata"), effect=W),
        RequestSpec("add_bytes", "Store bytes (attachment “data”) as a new item", params=KIND + ("name", "extension"),
                    optional=("metadata",), effect=W),
        RequestSpec("write_text", "Replace a text item's contents", params=ITEM + ("text",), effect=W),
        RequestSpec("rename", "Rename an item", params=ITEM + ("name",), effect=W),
        RequestSpec("export", "Copy an item to a place outside the Library", params=ITEM + ("destination",), effect=W),
        RequestSpec("path", "Where an item's file is (this machine only; to open it with another library)", params=ITEM),
        RequestSpec("new_path", "A fresh file path in the Library for a tool that writes the file itself",
                    params=KIND + ("extension",)),
        RequestSpec("register_path", "Add a file written at a new_path to the Library", params=KIND + ("name", "path"),
                    optional=("metadata",), effect=W),
        RequestSpec("unique_name", "A free name (“Untitled note 2”)", params=KIND + ("base",)),
        RequestSpec("refresh_size", "Re-read the size of a file changed in place (and announce it)", params=ITEM,
                    optional=("by",), effect=W),
        RequestSpec("links", "Notes and their [[links]] as a graph", shape="graph", example={}),
        RequestSpec("collections", "The Library and each kind of item, as tables", example={}),
        RequestSpec("schema", "The fields of a Library table", params=("collection",), example={"collection": "library"}),
        RequestSpec("query", "Library items as a Table shape", params=("collection",), optional=("query",), shape="table",
                    example={"collection": "library", "query": {"limit": 5}}),
        RequestSpec("get", "One item as a table row", params=("collection", "id")),
        RequestSpec("create", "A new note", params=("collection",), optional=("title", "values"), effect=W),
        RequestSpec("update", "Rename an item (its title)", params=("collection", "id", "values"), effect=W),
        RequestSpec("duplicate", "Copy an item", params=("collection", "id"), effect=W),
        RequestSpec("delete", "Delete items (their files too)", params=("ids",), optional=("collection",), effect=W),
    ),
    events=("changed",),
    shapes=("table", "document", "graph"),
    owns=("content_items", "library/"),
)
FIELDS = [                                      # only the name (title) can change; the rest is the engine's
    {"key": "kind", "title": "Kind", "kind": "select", "options": list(CONTENT_KINDS), "unit": "", "read_only": True},
    {"key": "extension", "title": "Type", "kind": "text", "options": [], "unit": "", "read_only": True},
    {"key": "size", "title": "Size", "kind": "integer", "options": [], "unit": "bytes", "read_only": True},
    {"key": "updated", "title": "Modified", "kind": "datetime", "options": [], "unit": "", "read_only": True},
    {"key": "created", "title": "Created", "kind": "datetime", "options": [], "unit": "", "read_only": True},
]


def _when(stamp: float) -> str:
    return dt.datetime.fromtimestamp(stamp).isoformat(timespec="seconds")


def item_payload(item: ContentItem) -> dict:
    return {"item_id": item.item_id, "kind": item.kind, "name": item.name, "extension": Path(item.file_name).suffix,
            "size_bytes": item.size_bytes, "created_at": item.created_at, "updated_at": item.updated_at,
            "metadata": item.metadata}


def item_row(item: ContentItem) -> dict:
    return {"id": item.item_id, "title": item.name, "kind": item.kind, "extension": Path(item.file_name).suffix,
            "size": item.size_bytes, "updated": _when(item.updated_at), "created": _when(item.created_at)}
# --- END [1.1] ---


# ==============================================================================
# [1.2] engine
# ==============================================================================
class KnowledgeEngine(Engine):
    MANIFEST = MANIFEST

    def start(self) -> None:
        self.database = Database(data_directory() / DATABASE_FILE_NAME)   # this thread's own connection
        self.store = ContentStore(self.database, on_change=self._on_change)
        self._by = ""                                  # who made the change being handled ("data" ...), if they said

    def stop(self) -> None:
        self.database.close()

    def _on_change(self, kind: str, ids: list[int] | None, content: bool) -> None:
        self.emit("changed", {"kind": kind, "ids": ids, "content": content, "by": self._by})

    def _item(self, item_id) -> ContentItem:
        item = self.store.get(int(item_id))
        if item is None:
            raise EngineError(f"There is no Library item {item_id}", code="not_found")
        return item

    def _kind(self, kind: str) -> str:
        if kind not in CONTENT_KINDS:
            raise EngineError(f"Unknown kind “{kind}” (one of {', '.join(CONTENT_KINDS)})", code="bad_request")
        return kind
# --- END [1.2] ---

# ==============================================================================
# [1.3] file_service
# ==============================================================================
    def on_list(self, payload: dict, _job: Job) -> dict:
        return {"items": [item_payload(item) for item in self.store.items(payload.get("kind"))]}

    def on_item(self, payload: dict, _job: Job) -> dict:
        return {"item": item_payload(self._item(payload["item_id"]))}

    def on_read_text(self, payload: dict, _job: Job) -> dict:
        item = self._item(payload["item_id"])
        try:
            text = self.store.read_text(item.item_id)
        except (OSError, UnicodeDecodeError) as error:
            raise EngineError(f"“{item.name}” can't be read as text: {error}", code="unreadable") from None
        max_lines = payload.get("max_lines")
        more = 0
        if max_lines:
            lines = text.splitlines()
            text, more = "\n".join(lines[:int(max_lines)]), max(0, len(lines) - int(max_lines))
        links = [{"target": target, "label": target} for target in self._link_targets(text)] if item.kind == "note" else []
        document, _attachments = shapes.document(text, "markdown" if item.kind == "note" else "plain", item.name, links)
        return {**document, "more_lines": more, "item_id": item.item_id}

    def _matches_text(self, item: ContentItem, needle: str) -> bool:
        if needle in item.name.casefold():
            return True
        if item.kind != "note" or item.size_bytes > SEARCH_TEXT_LIMIT:
            return False
        try:
            return needle in self.store.read_text(item.item_id).casefold()
        except (OSError, UnicodeDecodeError):
            return False

    def on_search(self, payload: dict, _job: Job) -> dict:
        needle = str(payload["text"]).casefold()
        limit = int(payload.get("limit") or 200)
        found = [item for item in self.store.items(payload.get("kind")) if self._matches_text(item, needle)]
        return {"items": [item_payload(item) for item in found[:limit]], "total": len(found)}

    def on_create_text(self, payload: dict, _job: Job) -> dict:
        kind = self._kind(payload["kind"])
        item = self.store.add_bytes(kind, payload["name"], str(payload.get("text", "")).encode("utf-8"),
                                    payload.get("extension") or CONTENT_KINDS[kind][1], payload.get("metadata"))
        return {"item": item_payload(item)}

    def on_add_file(self, payload: dict, _job: Job) -> dict:
        source = Path(payload["source_path"])
        if not source.is_file():
            raise EngineError(f"There is no file at {source}", code="not_found")
        item = self.store.add_file(self._kind(payload["kind"]), source, payload.get("name"), payload.get("metadata"))
        return {"item": item_payload(item)}

    def on_add_bytes(self, payload: dict, job: Job) -> dict:
        data = job.attachments.get("data")
        if data is None:
            raise EngineError("add_bytes needs the bytes as attachment “data”", code="bad_request")
        item = self.store.add_bytes(self._kind(payload["kind"]), payload["name"], bytes(data), payload["extension"],
                                    payload.get("metadata"))
        return {"item": item_payload(item)}

    def on_write_text(self, payload: dict, _job: Job) -> dict:
        self.store.write_text(self._item(payload["item_id"]).item_id, payload["text"])
        return {"item": item_payload(self._item(payload["item_id"]))}

    def on_rename(self, payload: dict, _job: Job) -> dict:
        return {"name": self.store.rename(self._item(payload["item_id"]).item_id, str(payload["name"]))}

    def on_export(self, payload: dict, _job: Job) -> dict:
        try:
            return {"path": str(self.store.export(self._item(payload["item_id"]).item_id, payload["destination"]))}
        except OSError as error:
            raise EngineError(f"Couldn't export: {error}", code="io_error") from None

    def on_path(self, payload: dict, _job: Job) -> dict:
        return {"path": str(self.store.path(self._item(payload["item_id"])))}

    def on_new_path(self, payload: dict, _job: Job) -> dict:
        return {"path": str(self.store.new_path(self._kind(payload["kind"]), payload["extension"]))}

    def on_register_path(self, payload: dict, _job: Job) -> dict:
        path = Path(payload["path"])
        if path.parent.resolve() != (self.store.root / self._kind(payload["kind"])).resolve() or not path.exists():
            raise EngineError("register_path takes a path from new_path that has been written", code="bad_request")
        return {"item": item_payload(self.store.register_path(payload["kind"], payload["name"], path, payload.get("metadata")))}

    def on_unique_name(self, payload: dict, _job: Job) -> dict:
        return {"name": self.store.unique_name(self._kind(payload["kind"]), str(payload["base"]))}

    def on_refresh_size(self, payload: dict, _job: Job) -> dict:
        self._by = str(payload.get("by") or "")
        try:
            self.store.refresh_size(self._item(payload["item_id"]).item_id)
        finally:
            self._by = ""
        return {"item": item_payload(self._item(payload["item_id"]))}
# --- END [1.3] ---

# ==============================================================================
# [1.4] source_contract
# ==============================================================================
    def _collection_kind(self, collection: str) -> str | None:
        if collection == "library":
            return None
        if collection not in CONTENT_KINDS:
            raise EngineError(f"There is no Library table “{collection}”", code="not_found")
        return collection

    def on_collections(self, _payload: dict, _job: Job) -> dict:
        counts = {kind: 0 for kind in CONTENT_KINDS}
        for item in self.store.items():
            counts[item.kind] = counts.get(item.kind, 0) + 1
        return {"collections": [{"collection": "library", "title": "Library", "noun": "file", "count": sum(counts.values())}] +
                [{"collection": kind, "title": title, "noun": "note" if kind == "note" else "file", "count": counts[kind]}
                 for kind, (title, _extension) in CONTENT_KINDS.items()]}

    def on_schema(self, payload: dict, _job: Job) -> dict:
        self._collection_kind(payload["collection"])
        return {"fields": FIELDS, "title_key": "title", "noun": "note" if payload["collection"] == "note" else "file"}

    def on_query(self, payload: dict, _job: Job) -> Reply:
        kind = self._collection_kind(payload["collection"])
        question = dict(payload.get("query") or {})
        items = self.store.items(kind)
        if question.get("search"):                     # names and note text, before the generic query
            needle = question.pop("search").casefold()
            items = [item for item in items if self._matches_text(item, needle)]
        rows, total, fields = run_query([item_row(item) for item in items], FIELDS, question)
        noun = "note" if kind == "note" else "file"
        shape, attachments = shapes.table(fields, rows, total=total, offset=question.get("offset", 0),
                                          source={"engine": "knowledge", "collection": payload["collection"], "noun": noun})
        return Reply(shape, attachments)

    def on_get(self, payload: dict, _job: Job) -> dict:
        return {"record": item_row(self._item(payload["id"]))}

    def on_create(self, payload: dict, _job: Job) -> dict:
        if payload["collection"] not in ("note", "library"):
            raise EngineError("New items of this kind come from their tools or an import", code="not_allowed")
        name = self.store.unique_name("note", str(payload.get("title") or "Untitled note"))
        item = self.store.create_text("note", name, f"# {name}\n\n")
        return {"record": item_row(item)}

    def on_update(self, payload: dict, _job: Job) -> dict:
        values = dict(payload["values"])
        name = values.pop("title", None)
        if values:
            raise EngineError(f"Only an item's name can be changed here (not {', '.join(values)})", code="not_allowed")
        if name is not None:
            if not str(name).strip():
                raise EngineError("The name can't be empty", code="invalid_value")
            self.store.rename(self._item(payload["id"]).item_id, str(name))
        return {"record": item_row(self._item(payload["id"]))}

    def on_duplicate(self, payload: dict, _job: Job) -> dict:
        item = self._item(payload["id"])
        copy = self.store.add_file(item.kind, self.store.path(item), self.store.unique_name(item.kind, f"{item.name} (copy)"),
                                   item.metadata)
        return {"record": item_row(copy)}

    def on_delete(self, payload: dict, _job: Job) -> dict:
        names = []
        for item_id in payload["ids"]:
            item = self.store.get(int(item_id))
            if item is not None:
                names.append(item.name)
                self.store.delete(item.item_id)
        what = f"“{names[0]}”" if len(names) == 1 else f"{len(names)} items"
        return {"deleted": len(names), "what": what}
# --- END [1.4] ---

# ==============================================================================
# [1.5] links
# ==============================================================================
    @staticmethod
    def _link_targets(text: str) -> list[str]:
        """The notes a text links to, each once, in order."""
        return list(dict.fromkeys(match.strip() for match in LINK.findall(text)))

    def on_links(self, _payload: dict, _job: Job) -> dict:
        """Every note as a node; an edge for every [[link]] (to a missing note too, marked missing)."""
        notes = self.store.items("note")
        by_name = {item.name.casefold(): item.item_id for item in notes}
        nodes = [{"id": item.item_id, "title": item.name, "missing": False} for item in notes]
        edges, missing = [], {}
        for item in notes:
            try:
                text = self.store.read_text(item.item_id) if item.size_bytes <= SEARCH_TEXT_LIMIT else ""
            except (OSError, UnicodeDecodeError):
                continue
            for target in self._link_targets(text):
                target_id = by_name.get(target.casefold())
                if target_id is None:
                    target_id = missing.setdefault(target.casefold(), f"missing:{target}")
                if target_id != item.item_id:
                    edges.append({"id": f"{item.item_id}->{target_id}", "source": item.item_id, "target": target_id})
        nodes += [{"id": node_id, "title": node_id.split(":", 1)[1], "missing": True} for node_id in missing.values()]
        fields = [{"key": "title", "title": "Note", "kind": "text"}, {"key": "missing", "title": "Missing", "kind": "checkbox"}]
        graph, _attachments = shapes.graph(fields, nodes, [], edges)
        return graph
# --- END [1.5] ---
