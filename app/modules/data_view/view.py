# ==============================================================================
# FILE: app/modules/data_view/view.py
# OVERVIEW: Data view module - any view of any engine's data as a page tile or a Canvas card: choose the
#           data, how to show it (table, board, timeline, calendar, form), its fields and filter.
# PURPOSE:
#   1. A fresh Data view shows "Choose data…", which opens the view builder.
#   2. The view's definition is the module's saved setting, kept up to date as the view changes.
#   3. Lives on the page's channels like every view (page-wide selection, or unlinked).
# CONTRACT:
#   - Requires: app.modules.base.ModuleView, app.views (host, builder, api), app.ui.ui_tools,
#               QSS selectors #data_view_panel, #empty_tile_label
#   - Exposes: DataViewModule(parent=None, context=None) with host (ViewHost | None), choose_data()
# NOTES:
#   - [2026-11-17] Created in Phase 2 of the engine plan (ViewHost as a page tile and a Canvas card).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - DEFINITION_SETTING (str): Module setting holding the view definition ("definition").
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. DATA_VIEW
#    1.1 data_view_module
# ==============================================================================

# ==============================================================================
# [1.1] data_view_module
# ==============================================================================
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from app.modules.base import ModuleView
from app.ui.ui_tools import set_status, status_label
from app.views.api import make_definition
from app.views.host import ViewHost

DEFINITION_SETTING = "definition"


class DataViewModule(ModuleView):
    def __init__(self, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, context, "data_view_panel")
        self.host: ViewHost | None = None
        self.panel_layout = QVBoxLayout(self)
        self.panel_layout.setContentsMargins(4, 4, 4, 4)
        self.empty_panel = QWidget()
        empty_layout = QVBoxLayout(self.empty_panel)
        empty_layout.addStretch()
        empty_layout.addWidget(QLabel("Show any data as a table, board, timeline, calendar or form.",
                                      objectName="empty_tile_label", alignment=Qt.AlignmentFlag.AlignCenter))
        choose_btn = QPushButton("Choose data…")
        choose_btn.clicked.connect(self.choose_data)
        empty_layout.addWidget(choose_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch()
        self.panel_layout.addWidget(self.empty_panel, 1)
        self.view_status_label = status_label()
        self.panel_layout.addWidget(self.view_status_label)
        saved = self.setting(DEFINITION_SETTING)
        if saved:
            self._show(saved)

    def choose_data(self) -> None:
        from app.views.builder import ViewBuilderDialog
        start = self.host.block_state() if self.host else make_definition("table", {"engine": "records", "collection": "projects"})
        dialog = ViewBuilderDialog(self, start)
        if dialog.exec():
            self._show(dialog.definition())
            self.save_setting(DEFINITION_SETTING, self.host.block_state())

    def _show(self, definition: dict) -> None:
        if self.host is None:
            self.host = ViewHost(definition)
            self.host.state_changed.connect(lambda: self.save_setting(DEFINITION_SETTING, self.host.block_state()))
            self.host.status.connect(lambda text, is_error: set_status(self.view_status_label, text, is_error))
            self.panel_layout.insertWidget(0, self.host, 1)
            self.empty_panel.hide()
        else:
            self.host.set_definition(definition)
# --- END [1.1] ---
