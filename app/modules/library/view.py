# ==============================================================================
# FILE: app/modules/library/view.py
# OVERVIEW: Library - everything DevOS keeps for you (notes, datasets, captures, CAD models, databases …) as
#           views over the Knowledge engine: a table of every item and the document view (text or details).
# PURPOSE:
#   1. A SourceModule on Knowledge's "library" table: sort, search (names and note text), rename in place,
#      delete / duplicate from the record menu, filter by kind (view menu → Filter), New view… for more.
#   2. Import… copies files in (kind from the extension); Export… copies the selected item out on purpose.
#   Phase 5 turns this composition into the Library template.
# CONTRACT:
#   - Requires: app.modules.source_module.SourceModule, app.clients.library (add_file, export, get),
#               app.data.content_kinds.kind_for_path, app.views (api, types), PySide6 (file dialogs)
#   - Exposes: LibraryModule(parent=None, context=None) with import_files(paths), export_selected(destination)
# NOTES:
#   - [2026-11-17] Import reports every file it couldn't import in its summary line (they were overwritten).
#   - [2026-11-17] Rebuilt in Phase 4 of the engine plan: views over the Knowledge engine (the hand-made table,
#                  preview and content-store calls are gone).
#   - [2026-11-15] Created with the content library (user files live in DevOS, not in random folders).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# None - see app/modules/source_module.py.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. LIBRARY
#    1.1 library_module
# ==============================================================================

# ==============================================================================
# [1.1] library_module
# ==============================================================================
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QPushButton

from app.clients.library import LibraryError, library
from app.data.content_kinds import kind_for_path
from app.modules.source_module import SourceModule
from app.views.api import make_definition
from app.views.types import BUILT_IN, LIBRARY_VIEWS


class LibraryModule(SourceModule):
    SOURCE = {"engine": "knowledge", "collection": "library"}
    NOUN = "file"
    VIEWS = ("table", "document") + tuple(view for view in BUILT_IN + LIBRARY_VIEWS if view not in ("table", "document"))
    DEFAULT_LAYOUT = {"split": "h", "sizes": [3, 2], "children": [{"kind": "table"}, {"kind": "document"}]}

    def block_definition(self, type_id: str) -> dict:
        if type_id == "table":
            return make_definition("table", self.source, {"sort": [{"field": "updated", "descending": True}]})
        if type_id == "board":
            return make_definition("board", self.source, mapping={"group": "kind"})
        return make_definition(type_id, self.source)

    def toolbar_start(self, row: QHBoxLayout) -> None:
        for text, handler, tip in (("Import…", self._choose_imports, "Copy files into your Library"),
                                   ("Export…", self._choose_export, "Copy the selected item to a folder of your choice")):
            button = QPushButton(text, toolTip=tip)
            button.clicked.connect(handler)
            row.addWidget(button)

    def import_files(self, paths: list[str]) -> int:
        added, failures = 0, []
        for path in paths:
            try:
                library().add_file(kind_for_path(path), path)
                added += 1
            except LibraryError as error:
                failures.append(f"{Path(path).name}: {error}")
        text = f"Imported {added} file{'s' if added != 1 else ''}"
        if failures:                                   # one line that keeps the failures visible
            text += f"; {len(failures)} not imported ({'; '.join(failures)})"
        self.report(text, is_error=bool(failures))
        return added

    def export_selected(self, destination: str) -> None:
        try:
            path = library().export(self.focus.selected_id, destination)
            self.report(f"Exported to {path}")
        except LibraryError as error:
            self.report(str(error), is_error=True)

    def _choose_imports(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(self, "Import into your Library")
        if paths:
            self.import_files(paths)

    def _choose_export(self) -> None:
        item = library().get(self.focus.selected_id) if self.focus.selected_id is not None else None
        if item is None:
            self.report("Select an item first", is_error=True)
            return
        destination, _filter = QFileDialog.getSaveFileName(self, "Export", item.name + item.extension)
        if destination:
            self.export_selected(destination)
# --- END [1.1] ---
