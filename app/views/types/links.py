# ==============================================================================
# FILE: app/views/types/links.py
# OVERVIEW: Links view - your notes as a graph: a dot per note, a line per [[link]]. Click a note to select it
#           on the page, double-click to open it; links to notes that don't exist yet show as hollow dots.
# PURPOSE:
#   1. Asks its engine for "links" (a Graph shape) whenever the data changes; lays the graph out with a seeded
#      spring layout (the same picture every time for the same notes).
#   2. Follows the page's selection (the selected note is highlighted).
# CONTRACT:
#   - Requires: app.views.graph_canvas.GraphCanvas, app.views.api, app.ui.canvas.tile_canvas.reserve_controls_space
#   - Exposes: LinksView(host), LINKS_VIEW (registered as "links"; needs the "links" request)
# NOTES:
#   - [2026-11-17] Phase 5b: draws with the shared GraphCanvas (app/views/graph_canvas.py), like the Network view.
#   - [2026-11-17] Created in Phase 4 of the engine plan (the note-link graph).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - links graph: The "links" answer: every note (missing ones hollow) and a line per [[link]].
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. LINKS_VIEW
#    1.1 view
# ==============================================================================

# ==============================================================================
# [1.1] view
# ==============================================================================
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.ui.canvas.tile_canvas import reserve_controls_space
from app.views.api import TableData, ViewType, register_view_type
from app.views.graph_canvas import GraphCanvas


class LinksView(QWidget):
    def __init__(self, host) -> None:
        super().__init__()
        self.host = host
        self.nodes: list[dict] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        self.summary_label = QLabel(objectName="control_label")
        row.addWidget(self.summary_label, 1)
        row.addWidget(host.view_menu_button())
        reserve_controls_space(row)
        layout.addLayout(row)
        self.canvas = GraphCanvas(self)
        self.canvas.node_clicked.connect(host.select)
        self.canvas.node_activated.connect(host.open_record)
        layout.addWidget(self.canvas, 1)
        self._fetch_timer = QTimer(self, singleShot=True, interval=150, timeout=self._fetch)

    def set_data(self, _data: TableData) -> None:
        self._fetch_timer.start()                         # notes changed: ask for the links again

    def set_selection(self, record_id) -> None:
        self.canvas.set_selected(record_id)

    def _fetch(self) -> None:
        self.host.request("links", {}, self._show_graph)

    def _show_graph(self, graph: dict | None, error) -> None:
        if error is not None:
            self.summary_label.setText(str(error))
            return
        self.nodes = [{**node, "hollow": node["missing"],
                       "tip": node["title"] + (" (not written yet)" if node["missing"] else "")} for node in graph["nodes"]]
        self.canvas.set_graph(self.nodes, graph["edges"])
        notes = sum(not node["missing"] for node in self.nodes)
        self.summary_label.setText(f"{notes} notes · {len(graph['edges'])} links" if self.nodes else
                                   "No notes yet. Link notes by writing [[Another note]].")


LINKS_VIEW = ViewType("links", "Links", "Notes as a graph: a line for every [[link]]", ("table",), LinksView,
                      requests=("links",))
register_view_type(LINKS_VIEW)
# --- END [1.1] ---
