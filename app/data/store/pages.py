# ==============================================================================
# FILE: app/data/store/pages.py
# OVERVIEW: The user's pages and folders: what the sidebar shows and every page's canvas key.
# PURPOSE:
#   1. pages / page_folders tables: titles, folder tree, order, favourites.
#   2. Create / rename / move / delete pages and folders; reorder siblings and favourites.
#   3. First run: creates a "Project Editor" page in Favorites and moves the old Project Editor
#      canvas layout into it, so nothing built there is lost.
# CONTRACT:
#   - Requires: app.data.store.database.Database
#   - Exposes: PageStore(database) (+ set_page_kind, page_state, save_page_state); Page, Folder; canvas_key(page_id);
#              UNTITLED_TITLE; PAGE_KIND_CANVAS, PAGE_KIND_BROWSER, PAGE_KIND_ICONS
# NOTES:
#   - [2026-11-16] Pages have a kind ("canvas" = module canvas, "browser" = web browser) and a small JSON
#                  state (browser address, zoom ...): migration v2. create_page() takes kind and state.
#   - [2026-11-15] Created when pages became user data (pages built from one template, CanvasPage).
#   - [2026-11-15] Page / folder / favourite changes are announced in the activity log.
#   - [2026-11-15] Deleting a page also forgets its modules' instances + settings (forget_instance).
#   - [2026-11-15] Deleting a folder moves its pages and sub-folders up to its parent - it never
#                  deletes pages. Deleting a page removes its canvas layout (module data is kept).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - canvas key (str): "page:<page_id>"; the ModuleCanvas layout key of a page (canvas_layouts table).
# - page kind (str): What a page shows: PAGE_KIND_CANVAS (modules) or PAGE_KIND_BROWSER (a web browser).
# - page state (dict): Small per-page settings kept with the page (state_json), e.g. the browser's address.
# - favorite_order (int | None): Position in the Favorites list; None = not a favourite.
# - folder_id (int | None): Containing folder; None = top level of "All Pages".
# - sort_order (int): Position among siblings (pages and folders share one ordering per parent).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. MODEL
#    1.1 migrations_and_types
# 2. PAGE_STORE
#    2.1 queries
#    2.2 pages
#    2.3 folders
#    2.4 ordering
#    2.5 first_run
# ==============================================================================

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from app.data.activity_log import get_activity_log
from app.data.store.database import Database

# ==============================================================================
# [1.1] migrations_and_types
# ==============================================================================
NAMESPACE = "pages"
UNTITLED_TITLE = "Untitled"
LEGACY_PROJECT_EDITOR_KEY = "project_editor"
MIGRATIONS = [
    """
    CREATE TABLE page_folders (
        folder_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        parent_id   INTEGER REFERENCES page_folders(folder_id),
        sort_order  INTEGER NOT NULL DEFAULT 0,
        is_expanded INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE pages (
        page_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        title          TEXT NOT NULL,
        folder_id      INTEGER REFERENCES page_folders(folder_id),
        sort_order     INTEGER NOT NULL DEFAULT 0,
        favorite_order INTEGER,
        created_at     REAL NOT NULL,
        updated_at     REAL NOT NULL
    );
    """,
    """
    ALTER TABLE pages ADD COLUMN kind TEXT NOT NULL DEFAULT 'canvas';
    ALTER TABLE pages ADD COLUMN state_json TEXT NOT NULL DEFAULT '{}';
    """,
]
PAGE_KIND_CANVAS = "canvas"
PAGE_KIND_BROWSER = "browser"
PAGE_KIND_ICONS = {PAGE_KIND_CANVAS: "page.svg", PAGE_KIND_BROWSER: "globe.svg"}
PAGE_COLUMNS = "page_id, title, folder_id, sort_order, favorite_order, kind"


def canvas_key(page_id: int) -> str:
    return f"page:{page_id}"


def _instance_ids(layout: dict | None) -> list[str]:
    """Every module instance id in a canvas layout tree."""
    if not layout:
        return []
    if "children" in layout:
        return [instance_id for child in layout["children"] for instance_id in _instance_ids(child)]
    return [layout["instance_id"]] if layout.get("instance_id") else []


@dataclass
class Page:
    page_id: int
    title: str
    folder_id: int | None
    sort_order: int
    favorite_order: int | None
    kind: str = PAGE_KIND_CANVAS

    @property
    def icon(self) -> str:
        return PAGE_KIND_ICONS.get(self.kind, PAGE_KIND_ICONS[PAGE_KIND_CANVAS])

    @property
    def is_favorite(self) -> bool:
        return self.favorite_order is not None


@dataclass
class Folder:
    folder_id: int
    name: str
    parent_id: int | None
    sort_order: int
    is_expanded: bool
# --- END [1.1] ---


class PageStore:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.log = get_activity_log()
        is_first_run = database.schema_version(NAMESPACE) == 0
        database.migrate(NAMESPACE, MIGRATIONS)
        if is_first_run:
            self._create_starter_pages()

# ==============================================================================
# [2.1] queries
# ==============================================================================
    def pages(self) -> list[Page]:
        rows = self.database.fetch_all(
            f"SELECT {PAGE_COLUMNS} FROM pages ORDER BY sort_order, page_id"
        )
        return [Page(**dict(row)) for row in rows]

    def page(self, page_id: int) -> Page | None:
        row = self.database.fetch_one(
            f"SELECT {PAGE_COLUMNS} FROM pages WHERE page_id = ?", (page_id,)
        )
        return Page(**dict(row)) if row else None

    def favorites(self) -> list[Page]:
        return sorted((page for page in self.pages() if page.is_favorite), key=lambda page: page.favorite_order)

    def folders(self) -> list[Folder]:
        rows = self.database.fetch_all(
            "SELECT folder_id, name, parent_id, sort_order, is_expanded FROM page_folders ORDER BY sort_order, folder_id"
        )
        return [Folder(row["folder_id"], row["name"], row["parent_id"], row["sort_order"], bool(row["is_expanded"]))
                for row in rows]
# --- END [2.1] ---

# ==============================================================================
# [2.2] pages
# ==============================================================================
    def unique_title(self, base: str = UNTITLED_TITLE) -> str:
        taken = {page.title for page in self.pages()}
        if base not in taken:
            return base
        number = 2
        while f"{base} {number}" in taken:
            number += 1
        return f"{base} {number}"

    def create_page(self, title: str | None = None, folder_id: int | None = None, kind: str = PAGE_KIND_CANVAS,
                    state: dict | None = None) -> int:
        now = time.time()
        title = title or self.unique_title()
        cursor = self.database.execute(
            "INSERT INTO pages (title, folder_id, sort_order, created_at, updated_at, kind, state_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title, folder_id, self._next_sort_order(folder_id), now, now, kind, json.dumps(state or {})),
        )
        self.log.success("pages", f"Created {'browser ' if kind == PAGE_KIND_BROWSER else ''}page “{title}”")
        return cursor.lastrowid

    def set_page_kind(self, page_id: int, kind: str) -> None:
        self.database.execute("UPDATE pages SET kind = ?, updated_at = ? WHERE page_id = ?", (kind, time.time(), page_id))

    def page_state(self, page_id: int) -> dict:
        row = self.database.fetch_one("SELECT state_json FROM pages WHERE page_id = ?", (page_id,))
        return json.loads(row["state_json"] or "{}") if row else {}

    def save_page_state(self, page_id: int, state: dict) -> None:
        self.database.execute("UPDATE pages SET state_json = ? WHERE page_id = ?", (json.dumps(state), page_id))

    def rename_page(self, page_id: int, title: str, quiet: bool = False) -> str:
        """Rename (blank names fall back to "Untitled"); returns the stored title. quiet: debug-level log
        (automatic renames, e.g. a browser page following its website's title)."""
        old = self.page(page_id)
        title = title.strip() or self.unique_title()
        self.database.execute("UPDATE pages SET title = ?, updated_at = ? WHERE page_id = ?", (title, time.time(), page_id))
        if old is not None and old.title != title:
            (self.log.debug if quiet else self.log.info)("pages", f"Renamed page “{old.title}” → “{title}”")
        return title

    def delete_page(self, page_id: int) -> None:
        """Delete a page, its layout and its modules' instance settings. Module-owned tables (captures,
        snapshots, backtests) are kept: they may hold work worth keeping."""
        page = self.page(page_id)
        instance_ids = _instance_ids(self.database.load_layout(canvas_key(page_id)))
        with self.database.transaction():
            self.database.execute("DELETE FROM pages WHERE page_id = ?", (page_id,))
            self.database.execute("DELETE FROM canvas_layouts WHERE page_key = ?", (canvas_key(page_id),))
        for instance_id in instance_ids:
            self.database.forget_instance(instance_id)
        self.log.info("pages", f"Deleted page “{page.title if page else page_id}” and {len(instance_ids)} module setting set(s)")

    def set_favorite(self, page_id: int, is_favorite: bool) -> None:
        if is_favorite:
            row = self.database.fetch_one("SELECT COALESCE(MAX(favorite_order), -1) + 1 AS next FROM pages")
            self.database.execute("UPDATE pages SET favorite_order = ? WHERE page_id = ? AND favorite_order IS NULL",
                                  (row["next"], page_id))
        else:
            self.database.execute("UPDATE pages SET favorite_order = NULL WHERE page_id = ?", (page_id,))
        page = self.page(page_id)
        self.log.info("pages", f"{'Added' if is_favorite else 'Removed'} “{page.title if page else page_id}” "
                               f"{'to' if is_favorite else 'from'} Favorites")
# --- END [2.2] ---

# ==============================================================================
# [2.3] folders
# ==============================================================================
    def create_folder(self, name: str = "New folder", parent_id: int | None = None) -> int:
        cursor = self.database.execute(
            "INSERT INTO page_folders (name, parent_id, sort_order) VALUES (?, ?, ?)",
            (name, parent_id, self._next_sort_order(parent_id)),
        )
        self.log.success("pages", f"Created folder “{name}”")
        return cursor.lastrowid

    def rename_folder(self, folder_id: int, name: str) -> str:
        name = name.strip() or "Folder"
        self.database.execute("UPDATE page_folders SET name = ? WHERE folder_id = ?", (name, folder_id))
        self.log.info("pages", f"Folder renamed to “{name}”")
        return name

    def set_folder_expanded(self, folder_id: int, is_expanded: bool) -> None:
        self.database.execute("UPDATE page_folders SET is_expanded = ? WHERE folder_id = ?", (int(is_expanded), folder_id))

    def delete_folder(self, folder_id: int) -> None:
        """Remove a folder; its pages and sub-folders move up to its parent."""
        row = self.database.fetch_one("SELECT parent_id FROM page_folders WHERE folder_id = ?", (folder_id,))
        if row is None:
            return
        with self.database.transaction():
            self.database.execute("UPDATE pages SET folder_id = ? WHERE folder_id = ?", (row["parent_id"], folder_id))
            self.database.execute("UPDATE page_folders SET parent_id = ? WHERE parent_id = ?", (row["parent_id"], folder_id))
            self.database.execute("DELETE FROM page_folders WHERE folder_id = ?", (folder_id,))
        self.log.info("pages", "Deleted a folder; its pages and sub-folders moved up a level")
# --- END [2.3] ---

# ==============================================================================
# [2.4] ordering
# ==============================================================================
    def _next_sort_order(self, folder_id: int | None) -> int:
        row = self.database.fetch_one(
            "SELECT MAX(sort_order) AS top FROM (SELECT sort_order FROM pages WHERE folder_id IS ? "
            "UNION ALL SELECT sort_order FROM page_folders WHERE parent_id IS ?)", (folder_id, folder_id),
        )
        return (row["top"] + 1) if row and row["top"] is not None else 0

    def apply_tree(self, entries: list[tuple[str, int, int | None, int]]) -> None:
        """Save the whole sidebar arrangement at once: (kind "page"/"folder", id, parent folder id, order)."""
        self.log.debug("pages", "Sidebar arrangement saved")
        with self.database.transaction():
            for kind, item_id, parent_id, order in entries:
                if kind == "page":
                    self.database.execute("UPDATE pages SET folder_id = ?, sort_order = ? WHERE page_id = ?",
                                          (parent_id, order, item_id))
                else:
                    self.database.execute("UPDATE page_folders SET parent_id = ?, sort_order = ? WHERE folder_id = ?",
                                          (parent_id, order, item_id))

    def apply_favorite_order(self, page_ids: list[int]) -> None:
        with self.database.transaction():
            for order, page_id in enumerate(page_ids):
                self.database.execute("UPDATE pages SET favorite_order = ? WHERE page_id = ?", (order, page_id))
# --- END [2.4] ---

# ==============================================================================
# [2.5] first_run
# ==============================================================================
    def _create_starter_pages(self) -> None:
        """A favourite "Project Editor" page that inherits the old Project Editor canvas, if any."""
        self.log.info("pages", "First run: creating the starter page")
        page_id = self.create_page("Project Editor")
        self.set_favorite(page_id, True)
        old_layout = self.database.load_layout(LEGACY_PROJECT_EDITOR_KEY)
        if old_layout is not None:
            self.database.save_layout(canvas_key(page_id), old_layout)
            self.database.execute("DELETE FROM canvas_layouts WHERE page_key = ?", (LEGACY_PROJECT_EDITOR_KEY,))
        else:
            # Fresh install: start the page with the RF imaging module, like the old Project Editor.
            self.database.save_layout(canvas_key(page_id), {
                "split": "h", "sizes": [1], "children": [{"module": "rf_imaging", "instance_id": None,
                                                         "title": "RF Imaging · 2.5–6 GHz"}],
            })
# --- END [2.5] ---
