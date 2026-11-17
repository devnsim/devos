# ==============================================================================
# FILE: app/modules/records_module.py
# OVERVIEW: RecordsModule - the base for modules whose data is a record collection (Agents, Projects ...):
#           a toolbar (+ New, search, export, status) over a canvas of record views.
# PURPOSE:
#   1. Subclasses declare COLLECTION, NOUN, PROPERTIES and DEFAULT_LAYOUT; everything else is shared.
#   2. Opens the collection (shared per database, so two tiles of the same module stay in sync), keeps
#      the selected record in the module's settings, reports outcomes in a status line.
#   3. Export: the records as JSON into the user's library (subclasses can add more actions).
#   4. A record's form opens as a view on request (double-click / right-click a record in any view).
# CONTRACT:
#   - Requires: app.modules.base.ModuleView, app.data.store.records, app.ui.record_views,
#               app.data.store.content, app.ui.ui_tools, QSS selector #records_module_panel
#   - Exposes: RecordsModule(parent=None, context=None) with collection, controller, block_canvas,
#              NEW_RECORD_PRESETS, hooks add_toolbar_actions(row), extra_block_specs() -> [BlockSpec],
#              record_actions() -> [(title, handler(record_id))], export_actions() -> [(title, handler)]
# NOTES:
#   - [2026-11-17] Form requests open / bring forward the form view; hooks for preset new records (+ New menu),
#                  module-specific blocks and record actions (Agents: Claude / Gemini / Ollama agents, Chat).
#   - [2026-11-16] Created with the Agents and Projects modules (the reusable "every line is a record" model).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - SELECTED_SETTING (str): Module setting holding the selected record id ("selected_id").
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. RECORDS_MODULE
#    1.1 imports
#    1.2 init
#    1.3 actions
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from app.data.activity_log import get_activity_log
from app.data.store.content import get_content_store
from app.data.store.records import PropertySpec, get_collection
from app.modules.base import ModuleView
from app.ui.canvas.block_canvas import BlockSpec
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.record_views import RecordsController, record_block_specs
from app.ui.ui_tools import set_status, status_label, tool_button
# --- END [1.1] ---


# ==============================================================================
# [1.2] init
# ==============================================================================
class RecordsModule(ModuleView):
    COLLECTION = "records"
    NOUN = "record"
    PROPERTIES: list[PropertySpec] = []
    DEFAULT_LAYOUT: dict | None = None
    NEW_RECORD_PRESETS: list[tuple[str, dict]] = []   # (menu title, values): + New offers these as a menu
    SELECTED_SETTING = "selected_id"

    def __init__(self, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, context, "records_module_panel")
        self.log = get_activity_log()
        database = context.database if context is not None else None
        self.collection = get_collection(self.COLLECTION, self.PROPERTIES, database)
        self.controller = RecordsController(self.collection, self.NOUN, self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        row = QHBoxLayout()
        new_btn = QPushButton(f"+ New {self.NOUN}")
        if self.NEW_RECORD_PRESETS:
            new_menu = QMenu(new_btn)
            for title, values in self.NEW_RECORD_PRESETS:
                new_menu.addAction(title).triggered.connect(
                    lambda _checked=False, title=title, values=values: self.controller.new_record(dict(values), f"New {title}"))
            new_btn.setMenu(new_menu)
        else:
            new_btn.clicked.connect(lambda: self.controller.new_record())
        row.addWidget(new_btn)
        self.add_toolbar_actions(row)
        export_btn = tool_button("module_settings_btn", text="⋯", tool_tip="Export")
        export_btn.clicked.connect(lambda: self._export_menu().exec(export_btn.mapToGlobal(export_btn.rect().bottomLeft())))
        row.addWidget(export_btn)
        self.count_label = QLabel(objectName="control_label")
        row.addWidget(self.count_label)
        self.module_status_label = status_label()
        row.addWidget(self.module_status_label, 1)
        reserve_controls_space(row)
        layout.addLayout(row)

        self.controller.record_actions = self.record_actions()
        self.block_canvas = self.build_block_canvas(record_block_specs(self.controller) + self.extra_block_specs(),
                                                    self.DEFAULT_LAYOUT)
        layout.addWidget(self.block_canvas, 1)
        self.controller.form_requested.connect(lambda _record_id: self.block_canvas.show_block("form"))
        self.controller.status.connect(lambda text, is_error: set_status(self.module_status_label, text, is_error))
        self.controller.records_changed.connect(self._update_count)
        self.controller.selection_changed.connect(lambda: self.save_setting(self.SELECTED_SETTING, self.controller.selected_id))
        saved_id = self.setting(self.SELECTED_SETTING)
        if saved_id is not None and self.collection.get(saved_id) is not None:
            self.controller.select(saved_id)
        self._update_count()

    def add_toolbar_actions(self, row: QHBoxLayout) -> None:
        """Hook: subclasses add their own buttons after + New."""

    def extra_block_specs(self) -> list[BlockSpec]:
        """Hook: the module's own views, offered next to table / board / timeline / calendar / form."""
        return []

    def record_actions(self) -> list[tuple[str, object]]:
        """Hook: (menu title, handler(record_id)) added to the right-click menu of a record in every view."""
        return []

    def export_actions(self) -> list[tuple[str, object]]:
        """Hook: (menu title, handler) pairs added to the ⋯ menu."""
        return []

    def _update_count(self) -> None:
        total = self.collection.count()
        self.count_label.setText(f"{total} {self.NOUN}{'' if total == 1 else 's'}")
# --- END [1.2] ---

# ==============================================================================
# [1.3] actions
# ==============================================================================
    def _export_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction(f"Export all {self.NOUN}s as JSON (to Library)").triggered.connect(self.export_json)
        for title, handler in self.export_actions():
            menu.addAction(title).triggered.connect(handler)
        return menu

    def export_json(self) -> None:
        with self.log.step(f"records:{self.COLLECTION}", f"Export {self.NOUN}s as JSON"):
            item = get_content_store().create_text("file", f"{self.COLLECTION.capitalize()} export",
                                                   self.collection.to_json(), ".json")
        set_status(self.module_status_label, f"Saved “{item.name}” in your Library")
# --- END [1.3] ---
