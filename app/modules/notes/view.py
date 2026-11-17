# ==============================================================================
# FILE: app/modules/notes/view.py
# OVERVIEW: Notes (Markdown, Obsidian-style) - your notes as views over the Knowledge engine: a list, the
#           document view (Markdown with a live preview, saved automatically) and the note-link graph.
# PURPOSE:
#   1. A SourceModule on Knowledge's "note" table; + New note; delete and rename from the list (or any view's
#      record menu); [[links]] between notes are followable in the preview and drawn in the Links view.
#   2. Keeps the old module's settings: the open note ("note_id") and the editor mode ("mode").
#   Phase 5 turns this composition into the Notes template.
# CONTRACT:
#   - Requires: app.modules.source_module.SourceModule, app.views (api, types), app.ui.engine_bridge.call_engine
#               (knowledge.create)
#   - Exposes: NotesModule(parent=None, context=None) with new_note(), note_id
# NOTES:
#   - [2026-11-17] Rebuilt in Phase 4 of the engine plan: views over the Knowledge engine (the hand-made editor,
#                  picker and content-store listener are gone; the document view replaces them).
#   - [2026-11-15] Created (reusable tools). Create / rename / delete are announced in the log.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - note_id: The selected note (kept in the module setting "note_id", as before Phase 4).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. NOTES
#    1.1 notes_module
# ==============================================================================

# ==============================================================================
# [1.1] notes_module
# ==============================================================================
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QPushButton

from app.modules.source_module import SourceModule
from app.ui.engine_bridge import call_engine
from app.views.api import make_definition
from app.views.types import BUILT_IN, LIBRARY_VIEWS


class NotesModule(SourceModule):
    SOURCE = {"engine": "knowledge", "collection": "note"}
    NOUN = "note"
    VIEWS = ("table", "document", "links") + tuple(view for view in BUILT_IN + LIBRARY_VIEWS
                                                   if view not in ("table", "document", "links"))
    DEFAULT_LAYOUT = {"split": "h", "sizes": [1, 3], "children": [{"kind": "table"}, {"kind": "document"}]}
    SELECTED_SETTING = "note_id"

    @property
    def note_id(self):
        return self.focus.selected_id

    def block_definition(self, type_id: str) -> dict:
        if type_id == "table":                         # a slim list: name and when it changed, newest first
            return make_definition("table", self.source, {"fields": ["updated"],
                                                          "sort": [{"field": "updated", "descending": True}]})
        if type_id == "document":
            return make_definition("document", self.source, presentation={"mode": self.setting("mode", "Split")})
        return make_definition(type_id, self.source)

    def toolbar_start(self, row: QHBoxLayout) -> None:
        new_btn = QPushButton("+ New note")
        new_btn.clicked.connect(self.new_note)
        row.addWidget(new_btn)

    def new_note(self) -> None:
        def answered(reply, error) -> None:
            if error is not None:
                self.report(str(error), is_error=True)
            else:
                self.focus.select(reply["record"]["id"])
        call_engine(self, "knowledge.create", {"collection": "note"}, answered)
# --- END [1.1] ---
