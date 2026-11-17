# ==============================================================================
# FILE: app/modules/records_module.py
# OVERVIEW: RecordsModule - the base for modules whose data is a record collection (Projects, Agents ...): a
#           SourceModule on the Data engine that declares the collection's default properties first.
# PURPOSE:
#   1. Subclasses declare COLLECTION, NOUN, PROPERTIES, DEFAULT_LAYOUT (and NEW_RECORD_PRESETS); the module
#      registers the collection's default properties with the records engine before any view asks for data.
#   2. Blocks: one per built-in view type (table, board, timeline, calendar, form) plus the module's own
#      (extra_block_specs); saved layouts from before Phase 2 keep their settings (definitions are upgraded).
#   3. Toolbar: + New (or a preset menu), New view… (from SourceModule), ⋯ Export as JSON.
# CONTRACT:
#   - Requires: app.modules.source_module (SourceModule, RecordFocus), app.views.types.BUILT_IN,
#               app.ui.engine_bridge.call_engine, app.clients.library, app.data.store.records.PropertySpec
#   - Exposes: RecordsModule(parent=None, context=None) with create_record(), export_json(), NEW_RECORD_PRESETS;
#              plus SourceModule's API (source, focus, block_canvas, hooks); RecordFocus, record_from_row (re-exported)
# NOTES:
#   - [2026-11-17] Phase 5a: record collections live in the Data engine (the records engine is gone).
#   - [2026-11-17] Phase 4: the generic half moved to app/modules/source_module.py (SourceModule, RecordFocus).
#   - [2026-11-17] Phase 2: views are ViewHosts on the records engine (RecordsController and app/ui/record_views.py
#                  are gone); RecordFocus replaces the controller for module code; New view… opens the builder.
#   - [2026-11-17] Form requests open / bring forward the form view; hooks for preset new records (+ New menu),
#                  module-specific blocks and record actions (Agents: Claude / Gemini / Ollama agents, Chat).
#   - [2026-11-16] Created with the Agents and Projects modules (the reusable "every line is a record" model).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - NEW_RECORD_PRESETS (list): (menu title, values) offered by + New as a menu (Agents: Claude / Gemini / Ollama).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. RECORDS_MODULE
#    1.1 records_module
# ==============================================================================

# ==============================================================================
# [1.1] records_module
# ==============================================================================
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QMenu, QPushButton

from app.clients.library import LibraryError, library
from app.data.activity_log import get_activity_log
from app.data.store.records import PropertySpec
from app.modules.source_module import RecordFocus, SourceModule, record_from_row  # noqa: F401 (re-exported)
from app.ui.engine_bridge import call_engine
from app.views.types import BUILT_IN


class RecordsModule(SourceModule):
    COLLECTION = "records"
    NOUN = "record"
    PROPERTIES: list[PropertySpec] = []
    VIEWS = BUILT_IN
    NEW_RECORD_PRESETS: list[tuple[str, dict]] = []

    def before_views(self) -> None:
        self.log = get_activity_log()
        self.source = {"engine": "data", "collection": self.COLLECTION}
        call_engine(self, "data.register", {"collection": self.COLLECTION, "noun": self.NOUN,   # before any query
                                            "properties": [spec.to_dict() for spec in self.PROPERTIES]})

    def toolbar_start(self, row: QHBoxLayout) -> None:
        new_btn = QPushButton(f"+ New {self.NOUN}")
        if self.NEW_RECORD_PRESETS:
            new_menu = QMenu(new_btn)
            for title, values in self.NEW_RECORD_PRESETS:
                new_menu.addAction(title).triggered.connect(
                    lambda _checked=False, title=title, values=values: self.create_record(dict(values), f"New {title}"))
            new_btn.setMenu(new_menu)
        else:
            new_btn.clicked.connect(lambda: self.create_record())
        row.addWidget(new_btn)

    def export_actions(self) -> list[tuple[str, object]]:
        return [(f"Export all {self.NOUN}s as JSON (to Library)", self.export_json)]

    def create_record(self, values: dict | None = None, title: str | None = None) -> None:
        payload = {"collection": self.COLLECTION, "values": values or {}} | ({"title": title} if title else {})

        def answered(reply, error) -> None:
            if error is not None:
                self.report(str(error), is_error=True)
            else:
                self.focus.select(reply["record"]["id"])
        call_engine(self, "data.create", payload, answered)

    def export_json(self) -> None:
        def answered(reply, error) -> None:
            if error is not None:
                self.report(str(error), is_error=True)
                return
            try:
                with self.log.step(f"records:{self.COLLECTION}", f"Export {self.NOUN}s as JSON"):
                    item = library().create_text("file", f"{self.COLLECTION.capitalize()} export", reply["json"], ".json")
                self.report(f"Saved “{item.name}” in your Library")
            except LibraryError as error:
                self.report(str(error), is_error=True)
        call_engine(self, "data.export_json", {"collection": self.COLLECTION}, answered)
# --- END [1.1] ---
