# ==============================================================================
# FILE: app/modules/library/view.py
# OVERVIEW: Library tool: browse and organise everything DevOS keeps for the user (notes, datasets, RF
#           captures, CAD models, files) - rename, delete, import, export, preview.
# PURPOSE:
#   1. Table of content items (name, type, size, modified), filterable by type; rename by double-click.
#   2. Preview: Markdown for notes, first rows for datasets, details for everything else.
#   3. Import (copies a file in, type picked from its extension), Export (copy out on purpose), Delete.
# CONTRACT:
#   - Requires: app.modules.base.ModuleView, app.data.store.content (get_content_store, CONTENT_KINDS),
#               app.ui.canvas.tile_canvas, QSS selector #library_panel
#   - Exposes: LibraryModule(parent=None, context=None); refresh()
# NOTES:
#   - [2026-11-15] Created with the content library (user files live in DevOS, not in random folders).
#   - [2026-11-16] Import uses content.kind_for_path() (EXTENSION_KINDS moved to content.py, shared with
#                  browser downloads); file-size text reuses content.size_text().
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - ALL_KINDS (str): Filter entry that shows every kind.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. LIBRARY_MODULE
#    2.1 init
#    2.2 table
#    2.3 actions
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QMessageBox, QPushButton, QSplitter, QTextBrowser, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app.data.store.content import CONTENT_KINDS, get_content_store, kind_for_path, size_text
from app.modules.base import ModuleView
from app.ui.canvas.tile_canvas import reserve_controls_space

ALL_KINDS = "All types"
# --- END [1.1] ---


# ==============================================================================
# [2.1] init
# ==============================================================================
class LibraryModule(ModuleView):
    def __init__(self, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, context, "library_panel")
        self.store = get_content_store()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        self.kind_combo = QComboBox()
        self.kind_combo.addItem(ALL_KINDS, None)
        for kind, (title, _extension) in CONTENT_KINDS.items():
            self.kind_combo.addItem(title, kind)
        self.kind_combo.currentIndexChanged.connect(self.refresh)
        buttons = [("Import…", self._on_import), ("Export…", self._on_export), ("Delete", self._on_delete)]
        row.addWidget(self.kind_combo, 1)
        for text, handler in buttons:
            button = QPushButton(text)
            button.clicked.connect(handler)
            row.addWidget(button)
        reserve_controls_space(row)
        layout.addLayout(row)

        splitter = QSplitter(Qt.Orientation.Horizontal, objectName="pane_splitter")
        self.table = QTreeWidget()
        self.table.setHeaderLabels(["Name", "Type", "Size", "Modified"])
        self.table.setRootIsDecorated(False)
        self.table.setSortingEnabled(True)
        self.table.itemChanged.connect(self._on_renamed)
        self.table.currentItemChanged.connect(self._preview)
        self.preview = QTextBrowser()
        splitter.addWidget(self.table)
        splitter.addWidget(self.preview)
        splitter.setSizes([3, 2])
        layout.addWidget(splitter, 1)
        self.store.add_listener(lambda kind: self.refresh())
        self.refresh()
# --- END [2.1] ---

# ==============================================================================
# [2.2] table
# ==============================================================================
    def refresh(self) -> None:
        self.table.blockSignals(True)
        self.table.clear()
        for item in self.store.items(self.kind_combo.currentData()):
            row = QTreeWidgetItem([item.name, CONTENT_KINDS.get(item.kind, (item.kind,))[0], size_text(item.size_bytes),
                                   time.strftime("%Y-%m-%d %H:%M", time.localtime(item.updated_at))])
            row.setData(0, Qt.ItemDataRole.UserRole, item.item_id)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsEditable)
            self.table.addTopLevelItem(row)
        for column in range(4):
            self.table.resizeColumnToContents(column)
        self.table.blockSignals(False)

    def _selected_id(self) -> int | None:
        row = self.table.currentItem()
        return row.data(0, Qt.ItemDataRole.UserRole) if row else None

    def _preview(self) -> None:
        item_id = self._selected_id()
        item = self.store.get(item_id) if item_id is not None else None
        if item is None:
            self.preview.clear()
            return
        if item.kind == "note":
            self.preview.setMarkdown(self.store.read_text(item_id))
        elif item.kind == "dataset":
            lines = self.store.read_text(item_id).splitlines()
            self.preview.setPlainText("\n".join(lines[:40]) + (f"\n… {len(lines) - 40:,} more rows" if len(lines) > 40 else ""))
        else:
            details = "\n".join(f"{key}: {value}" for key, value in item.metadata.items())
            self.preview.setPlainText(f"{item.name}\n{CONTENT_KINDS.get(item.kind, (item.kind,))[0]} · "
                                      f"{size_text(item.size_bytes)}\n\n{details}")

    def _on_renamed(self, row: QTreeWidgetItem, column: int) -> None:
        if column == 0:
            self.store.rename(row.data(0, Qt.ItemDataRole.UserRole), row.text(0))
# --- END [2.2] ---

# ==============================================================================
# [2.3] actions
# ==============================================================================
    def _on_import(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Import into your library")
        for path in paths:
            self.store.add_file(kind_for_path(path), path)

    def _on_export(self) -> None:
        item_id = self._selected_id()
        if item_id is None:
            return
        item = self.store.get(item_id)
        extension = Path(item.file_name).suffix
        destination, _ = QFileDialog.getSaveFileName(self, "Export a copy", f"{item.name}{extension}")
        if destination:
            self.store.export(item_id, destination)

    def _on_delete(self) -> None:
        item_id = self._selected_id()
        if item_id is None:
            return
        name = self.store.get(item_id).name
        if QMessageBox.question(self, "Delete", f"Delete “{name}” from your library?") == QMessageBox.StandardButton.Yes:
            self.store.delete(item_id)

# --- END [2.3] ---
