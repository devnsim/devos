# ==============================================================================
# FILE: app/data/store/content.py
# OVERVIEW: The user's content library: notes, datasets, captures, CAD models and other files DevOS
#           keeps for the user, stored and organised by the app (never "save to some folder").
# PURPOSE:
#   1. Files live in <data folder>/library/<kind>/<random name><ext>; the content_items table holds
#      their names, kinds, sizes and metadata.
#   2. Add (copy in a file, bytes or text), read / write text, rename, delete, list by kind.
#   3. add_listener(): open tools (notes, graph, library) refresh when items change.
#   4. unique_name() for "Untitled note 2"-style naming.
# CONTRACT:
#   - Requires: app.data.store.database (Database, data_directory), app.data.activity_log
#   - Exposes: ContentStore(database) (+ add_listener, refresh_size), get_content_store(), ContentItem, CONTENT_KINDS,
#              EXTENSION_KINDS, kind_for_path(), size_text()
# NOTES:
#   - [2026-11-16] Content kinds graph (Kùzu graph databases), sheet (spreadsheets), geo (map layers) and
#                  image (captures, downloads) with their extensions.
#   - [2026-11-16] New kind "database" (user SQLite databases, Database Editor). EXTENSION_KINDS / kind_for_path()
#                  moved here from the Library tool so imports and browser downloads share one mapping.
#                  refresh_size() for files edited in place (databases).
#   - [2026-11-15] Created so modules stop asking users where to save things (RF captures used to land in
#                  the repo root). Files on disk get random names; the user only ever sees item names.
#                  Every add / rename / delete is announced in the activity log.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - content item: One user file managed by DevOS (ContentItem).
# - kind (str): Category in CONTENT_KINDS ("note", "dataset", "capture", "cad", "database", "file").
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
#    2.4 shared_instance
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
from app.data.store.database import Database, data_directory, get_database

NAMESPACE = "content"
CONTENT_KINDS = {               # kind: (display name, default extension)
    "note": ("Notes", ".md"),
    "dataset": ("Datasets", ".csv"),
    "capture": ("RF captures", ".npz"),
    "cad": ("CAD models", ".stl"),
    "database": ("Databases", ".sqlite3"),
    "graph": ("Graph databases", ".kuzu"),
    "sheet": ("Spreadsheets", ".xlsx"),
    "geo": ("Map layers", ".geojson"),
    "image": ("Images", ".png"),
    "file": ("Other files", ""),
}
EXTENSION_KINDS = {".md": "note", ".markdown": "note", ".txt": "note", ".csv": "dataset", ".npz": "capture",
                   ".stl": "cad", ".obj": "cad", ".ply": "cad", ".off": "cad", ".glb": "cad", ".gltf": "cad",
                   ".sqlite": "database", ".sqlite3": "database", ".db": "database", ".kuzu": "graph",
                   ".xlsx": "sheet", ".xlsm": "sheet", ".ods": "sheet", ".xls": "sheet", ".geojson": "geo",
                   ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image", ".svg": "image"}


def kind_for_path(path: str | Path) -> str:
    """Content kind for a file name by its extension ("file" if unknown)."""
    return EXTENSION_KINDS.get(Path(path).suffix.lower(), "file")
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
    def __init__(self, database: Database) -> None:
        self.database = database
        database.migrate(NAMESPACE, MIGRATIONS)
        self.root = data_directory() / "library"
        for kind in CONTENT_KINDS:
            (self.root / kind).mkdir(parents=True, exist_ok=True)
        self.log = get_activity_log()
        self._listeners: list = []

    def add_listener(self, listener) -> None:
        """listener(kind) is called after an item of that kind is added, renamed or deleted."""
        self._listeners.append(listener)

    def remove_listener(self, listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _changed(self, kind: str) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind)
            except RuntimeError:                       # listener's widget already deleted
                self.remove_listener(listener)
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
        self._changed(kind)
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

    def refresh_size(self, item_id: int) -> None:
        """Re-read the size of a file that was changed in place (e.g. a database being edited)."""
        path = self.path(item_id)
        if path.exists():
            self.database.execute("UPDATE content_items SET size_bytes = ?, updated_at = ? WHERE item_id = ?",
                                  (path.stat().st_size, time.time(), item_id))

    def rename(self, item_id: int, name: str) -> str:
        item = self.get(item_id)
        name = name.strip() or item.name
        if name != item.name:
            self.database.execute("UPDATE content_items SET name = ?, updated_at = ? WHERE item_id = ?",
                                  (name, time.time(), item_id))
            self.log.info("library", f"Renamed “{item.name}” → “{name}”")
            self._changed(item.kind)
        return name

    def delete(self, item_id: int) -> None:
        item = self.get(item_id)
        if item is None:
            return
        self.path(item).unlink(missing_ok=True)
        self.database.execute("DELETE FROM content_items WHERE item_id = ?", (item_id,))
        self.log.info("library", f"Deleted “{item.name}”")
        self._changed(item.kind)

    def export(self, item_id: int, destination: str | Path) -> Path:
        """Copy an item out of the library (when the user explicitly wants a file elsewhere)."""
        destination = Path(destination)
        shutil.copy2(self.path(item_id), destination)
        self.log.info("library", f"Exported “{self.get(item_id).name}” to {destination}")
        return destination
# --- END [2.3] ---

# ==============================================================================
# [2.4] shared_instance
# ==============================================================================
_content_store: ContentStore | None = None


def get_content_store() -> ContentStore:
    global _content_store
    if _content_store is None:
        _content_store = ContentStore(get_database())
    return _content_store


def size_text(size_bytes: int) -> str:
    for unit, size in (("GB", 2**30), ("MB", 2**20), ("KB", 2**10)):
        if size_bytes >= size:
            return f"{size_bytes / size:.1f} {unit}"
    return f"{size_bytes} B"
# --- END [2.4] ---
