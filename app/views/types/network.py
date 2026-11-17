# ==============================================================================
# FILE: app/views/types/network.py
# OVERVIEW: Network view - the graph a table belongs to (a Graph shape from its engine's "graph" request):
#           nodes coloured by their table, a line per relationship. Nodes of the view's own table select their
#           row on the page; the selected row is highlighted.
# PURPOSE:
#   1. Asks "<engine>.graph" {collection, limit} whenever the data changes; draws it on GraphCanvas.
#   2. Only offered for sources whose collection is part of a graph (ViewType.accepts: "graph:" collections).
# CONTRACT:
#   - Requires: app.views.graph_canvas.GraphCanvas, app.views.api, app.ui.ui_tools, app.ui.canvas.tile_canvas
#   - Exposes: NetworkView(host), NETWORK_VIEW (registered as "network"; needs "graph"), LIMITS
# NOTES:
#   - [2026-11-17] Created in Phase 5b of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - LIMITS (tuple): How many relationships (and loose nodes) to draw: 100, 300, 1000; saved as "limit".
# - own node: A node of the view's own table (its label is the table name); its row id is its key.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. NETWORK_VIEW
#    1.1 view
# ==============================================================================

# ==============================================================================
# [1.1] view
# ==============================================================================
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QLabel, QWidget

from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.ui_tools import block_layout
from app.views.api import TableData, ViewType, register_view_type
from app.views.graph_canvas import GraphCanvas

LIMITS = (100, 300, 1000)


class NetworkView(QWidget):
    def __init__(self, host) -> None:
        super().__init__()
        self.host = host
        self.table = str(host.source.get("collection", "")).rsplit("/", 1)[-1]
        self.row_of_node: dict = {}
        layout, row = block_layout(self)
        self.summary_label = QLabel(objectName="control_label")
        row.addWidget(self.summary_label, 1)
        self.limit_combo = QComboBox(toolTip="How many relationships to draw")
        for limit in LIMITS:
            self.limit_combo.addItem(f"{limit} links", limit)
        self.limit_combo.setCurrentIndex(max(0, self.limit_combo.findData(host.presentation.get("limit", 300))))
        self.limit_combo.currentIndexChanged.connect(self._limit_changed)
        row.addWidget(self.limit_combo)
        row.addWidget(host.view_menu_button())
        reserve_controls_space(row)
        self.canvas = GraphCanvas(self)
        self.canvas.node_clicked.connect(self._clicked)
        self.canvas.node_activated.connect(lambda node_id: self._clicked(node_id, open_it=True))
        layout.addWidget(self.canvas, 1)
        self._fetch_timer = QTimer(self, singleShot=True, interval=150, timeout=self._fetch)

    def _limit_changed(self) -> None:
        self.host.set_presentation("limit", self.limit_combo.currentData())
        self._fetch()

    def set_data(self, _data: TableData) -> None:
        self._fetch_timer.start()

    def set_selection(self, record_id) -> None:
        node = next((node for node, row_id in self.row_of_node.items() if row_id == record_id), None)
        self.canvas.set_selected(node)

    def _fetch(self) -> None:
        self.host.request("graph", {"collection": self.host.source["collection"], "limit": self.limit_combo.currentData()},
                          self._show_graph)

    def _show_graph(self, graph: dict | None, error) -> None:
        if error is not None:
            self.summary_label.setText(str(error))
            return
        nodes = [{"id": node["id"], "title": node["title"], "group": node.get("label", ""),
                  "tip": f"{node.get('label', '')}: {node['title']}"} for node in graph["nodes"]]
        self.row_of_node = {node["id"]: node.get("row_id") for node in graph["nodes"] if node.get("label") == self.table}
        self.canvas.set_graph(nodes, graph["edges"])
        self.set_selection(self.host.selected_id)
        tables = sorted({node["group"] for node in nodes if node["group"]})
        self.summary_label.setText(f"{len(nodes)} nodes · {len(graph['edges'])} relationships · {', '.join(tables)}")

    def _clicked(self, node_id, open_it: bool = False) -> None:
        row_id = self.row_of_node.get(node_id)
        if row_id is None:
            self.canvas.set_selected(node_id)                # another table's node: highlight only
            return
        self.host.open_record(row_id) if open_it else self.host.select(row_id)


NETWORK_VIEW = ViewType("network", "Network", "The graph this table is part of: nodes and their relationships", ("table",),
                        NetworkView, requests=("graph",),
                        accepts=lambda source: str(source.get("collection", "")).startswith("graph:"))
register_view_type(NETWORK_VIEW)
# --- END [1.1] ---
