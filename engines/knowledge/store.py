# ==============================================================================
# FILE: engines/knowledge/store.py
# OVERVIEW: The Library store - inside the Knowledge engine, the only code that touches <data>/library/ and
#           the content_items index: notes, datasets, captures, CAD models and other files DevOS keeps for you.
# PURPOSE:
#   1. Files live in <data folder>/library/<kind>/<random name><ext>; content_items holds their names, kinds,
#      sizes and metadata.
#   2. Add (copy in a file, bytes or text), read / write text, rename, delete, export, list by kind; every
#      change is reported to the engine (on_change), which announces it on the bus.
#   3. unique_name() for "Untitled note 2"-style naming.
# CONTRACT:
#   - Requires: app.data.store.database (Database, data_directory), app.data.activity_log, app.data.content_kinds.
#               Used ONLY by engines/knowledge (checked by tools/check_standard.py).
#   - Exposes: ContentStore(database, on_change=None), ContentItem
# NOTES:
#   - [2026-11-17] refresh_size announces the change (content=True): a file changed in place is news for the Data engine.
#   - [2026-11-17] Moved here from app/data/store/content.py (engine plan Phase 4): the Knowledge engine owns the
#                  Library. Kinds and size_text() moved to app/data/content_kinds.py; get_content_store() and
#                  listeners are gone (changes become "knowledge.changed" events); write_text() reports changes too.
#   - [2026-11-16] Content kinds graph (Kùzu graph databases), sheet (spreadsheets), geo (map layers) and
#                  image (captures, downloads) with their extensions.
#   - [2026-11-16] New kind "database" (user SQLite databases, Database Editor). refresh_size() for files edited
#                  in place (databases).
#   - [2026-11-15] Created so modules stop asking users where to save things (RF captures used to land in
#                  the repo root). Files on disk get random names; the user only ever sees item names.
#                  Every add / rename / delete is announced in the activity log.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - content item: One user file managed by DevOS (ContentItem).
# - kind (str): Category in app.data.content_kinds.CONTENT_KINDS.
# - library folder: <data folder>/library, see app.data.store.database.data_directory().
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. MODEL
#    1.1 imports_and_kinds
#    1.2 content_item
# 2. CONTENT_STORE
#    2.1 init
#    2.2 queries
#    2.3 add_and_edit
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_kinds
# ==============================================================================
from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.data.activity_log import get_activity_log
from app.data.content_kinds import CONTENT_KINDS, size_text
from app.data.store.database import Database, data_directory

NAMESPACE = "content"
MIGRATIONS = [
    """
    CREATE TABLE content_items (
        item_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        kind          TEXT NOT NULL,
        name          TEXT NOT NULL,
        file_name     TEXT NOT NULL,
        size_bytes    INTEGER NOT NULL,
        created_at    REAL NOT NULL,
        updated_at    REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX content_items_kind ON content_items (kind, updated_at DESC);
    """,
]
# --- END [1.1] ---

# ==============================================================================
# [1.2] content_item
# ==============================================================================
@dataclass
class ContentItem:
    item_id: int
    kind: str
    name: str
    file_name: str
    size_bytes: int
    created_at: float
    updated_at: float
    metadata: dict
# --- END [1.2] ---


# ==============================================================================
# [2.1] init
# ==============================================================================
class ContentStore:
    def __init__(self, database: Database, on_change=None) -> None:
        self.database = database
        database.migrate(NAMESPACE, MIGRATIONS)
        self.root = data_directory() / "library"
        for kind in CONTENT_KINDS:
            (self.root / kind).mkdir(parents=True, exist_ok=True)
        self.log = get_activity_log()
        self.on_change = on_change

    def _changed(self, kind: str, item_id: int | None = None, content: bool = False) -> None:
        """Report a change (on_change(kind, ids, content)): the engine turns it into a "changed" event."""
        if self.on_change is not None:
            self.on_change(kind, [item_id] if item_id is not None else None, content)
# --- END [2.1] ---

# ==============================================================================
# [2.2] queries
# ==============================================================================
    def items(self, kind: str | None = None) -> list[ContentItem]:
        """Newest first; optionally one kind."""
        if kind is None:
            rows = self.database.fetch_all("SELECT * FROM content_items ORDER BY updated_at DESC")
        else:
            rows = self.database.fetch_all("SELECT * FROM content_items WHERE kind = ? ORDER BY updated_at DESC", (kind,))
        return [self._item(row) for row in rows]

    def get(self, item_id: int) -> ContentItem | None:
        row = self.database.fetch_one("SELECT * FROM content_items WHERE item_id = ?", (item_id,))
        return self._item(row) if row else None

    def path(self, item: ContentItem | int) -> Path:
        item = self.get(item) if isinstance(item, int) else item
        return self.root / item.kind / item.file_name

    def unique_name(self, kind: str, base: str) -> str:
        taken = {item.name for item in self.items(kind)}
        if base not in taken:
            return base
        number = 2
        while f"{base} {number}" in taken:
            number += 1
        return f"{base} {number}"

    @staticmethod
    def _item(row) -> ContentItem:
        return ContentItem(row["item_id"], row["kind"], row["name"], row["file_name"], row["size_bytes"],
                           row["created_at"], row["updated_at"], json.loads(row["metadata_json"] or "{}"))
# --- END [2.2] ---

# ==============================================================================
# [2.3] add_and_edit
# ==============================================================================
    def _register(self, kind: str, name: str, file_path: Path, metadata: dict | None) -> ContentItem:
        now = time.time()
        cursor = self.database.execute(
            "INSERT INTO content_items (kind, name, file_name, size_bytes, created_at, updated_at, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kind, name, file_path.name, file_path.stat().st_size, now, now, json.dumps(metadata or {})),
        )
        self.log.success("library", f"Saved {CONTENT_KINDS.get(kind, (kind,))[0].lower()[:-1]} “{name}” "
                                    f"({size_text(file_path.stat().st_size)})")
        self._changed(kind, cursor.lastrowid)
        return self.get(cursor.lastrowid)

    def _new_file_path(self, kind: str, extension: str) -> Path:
        if kind not in CONTENT_KINDS:
            raise ValueError(f"Unknown content kind {kind!r}")
        return self.root / kind / f"{uuid.uuid4().hex}{extension}"

    def add_file(self, kind: str, source_path: str | Path, name: str | None = None, metadata: dict | None = None) -> ContentItem:
        """Copy an outside file into the library."""
        source_path = Path(source_path)
        destination = self._new_file_path(kind, source_path.suffix.lower())
        shutil.copy2(source_path, destination)
        return self._register(kind, name or self.unique_name(kind, source_path.stem), destination,
                              {"imported_from": source_path.name, **(metadata or {})})

    def add_bytes(self, kind: str, name: str, data: bytes, extension: str, metadata: dict | None = None) -> ContentItem:
        destination = self._new_file_path(kind, extension)
        destination.write_bytes(data)
        return self._register(kind, name, destination, metadata)

    def new_path(self, kind: str, extension: str) -> Path:
        """A fresh library path for writers that need a path (e.g. numpy.savez); then call register_path()."""
        return self._new_file_path(kind, extension)

    def register_path(self, kind: str, name: str, file_path: Path, metadata: dict | None = None) -> ContentItem:
        return self._register(kind, name, Path(file_path), metadata)

    def create_text(self, kind: str, name: str, text: str = "", extension: str | None = None) -> ContentItem:
        return self.add_bytes(kind, name, text.encode("utf-8"), extension or CONTENT_KINDS[kind][1])

    def read_text(self, item_id: int) -> str:
        return self.path(item_id).read_text(encoding="utf-8")

    def write_text(self, item_id: int, text: str) -> None:
        path = self.path(item_id)
        path.write_text(text, encoding="utf-8")
        self.database.execute("UPDATE content_items SET size_bytes = ?, updated_at = ? WHERE item_id = ?",
                              (path.stat().st_size, time.time(), item_id))
        self._changed(self.get(item_id).kind, item_id, content=True)

    def refresh_size(self, item_id: int) -> None:
        """Re-read the size of a file that was changed in place (e.g. a database being edited) and announce it."""
        path = self.path(item_id)
        if path.exists():
            self.database.execute("UPDATE content_items SET size_bytes = ?, updated_at = ? WHERE item_id = ?",
                                  (path.stat().st_size, time.time(), item_id))
            self._changed(self.get(item_id).kind, item_id, content=True)

    def rename(self, item_id: int, name: str) -> str:
        item = self.get(item_id)
        name = name.strip() or item.name
        if name != item.name:
            self.database.execute("UPDATE content_items SET name = ?, updated_at = ? WHERE item_id = ?",
                                  (name, time.time(), item_id))
            self.log.info("library", f"Renamed “{item.name}” → “{name}”")
            self._changed(item.kind, item_id)
        return name

    def delete(self, item_id: int) -> None:
        item = self.get(item_id)
        if item is None:
            return
        self.path(item).unlink(missing_ok=True)
        self.database.execute("DELETE FROM content_items WHERE item_id = ?", (item_id,))
        self.log.info("library", f"Deleted “{item.name}”")
        self._changed(item.kind, item_id)

    def export(self, item_id: int, destination: str | Path) -> Path:
        """Copy an item out of the library (when the user explicitly wants a file elsewhere)."""
        destination = Path(destination)
        shutil.copy2(self.path(item_id), destination)
        self.log.info("library", f"Exported “{self.get(item_id).name}” to {destination}")
        return destination
# --- END [2.3] ---
