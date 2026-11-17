# ==============================================================================
# FILE: app/views/types/links.py
# OVERVIEW: Links view - your notes as a graph: a dot per note, a line per [[link]]. Click a note to select it
#           on the page, double-click to open it; links to notes that don't exist yet show as hollow dots.
# PURPOSE:
#   1. Asks its engine for "links" (a Graph shape) whenever the data changes; lays the graph out with a seeded
#      spring layout (the same picture every time for the same notes).
#   2. Follows the page's selection (the selected note is highlighted).
# CONTRACT:
#   - Requires: networkx, app.views.api, app.ui.theme colour tokens, app.ui.canvas.tile_canvas.reserve_controls_space
#   - Exposes: LinksView(host), LINKS_VIEW (registered as "links"; needs the "links" request)
# NOTES:
#   - [2026-11-17] Created in Phase 4 of the engine plan (the note-link graph).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - NODE_PX (int | px): Diameter of a note's dot (default: 12).
# - MARGIN_PX (int | px): Space kept around the graph (default: 40).
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

import networkx as nx
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.theme import ACCENT_HIGHLIGHT_COLOR, EDGE_COLOR, MUTED_TEXT_COLOR, PANEL_COLOR, TEXT_COLOR
from app.views.api import TableData, ViewType, register_view_type

NODE_PX = 12
MARGIN_PX = 40


class LinksView(QWidget):
    def __init__(self, host) -> None:
        super().__init__()
        self.host = host
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self.positions: dict = {}                         # node id -> (x, y) in 0..1
        self.selected_id = None
        self.setMouseTracking(True)
        self.setMinimumSize(200, 160)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        self.summary_label = QLabel(objectName="control_label")
        row.addWidget(self.summary_label, 1)
        row.addWidget(host.view_menu_button())
        reserve_controls_space(row)
        layout.addLayout(row)
        layout.addStretch()
        self._fetch_timer = QTimer(self, singleShot=True, interval=150, timeout=self._fetch)

    def set_data(self, _data: TableData) -> None:
        self._fetch_timer.start()                         # notes changed: ask for the links again

    def set_selection(self, record_id) -> None:
        self.selected_id = record_id
        self.update()

    def _fetch(self) -> None:
        self.host.request("links", {}, self._show_graph)

    def _show_graph(self, graph: dict | None, error) -> None:
        if error is not None:
            self.summary_label.setText(str(error))
            return
        self.nodes, self.edges = graph["nodes"], graph["edges"]
        network = nx.Graph()
        network.add_nodes_from(node["id"] for node in self.nodes)
        network.add_edges_from((edge["source"], edge["target"]) for edge in self.edges)
        layout = nx.spring_layout(network, seed=7) if len(network) > 1 else {node: (0.0, 0.0) for node in network}
        xs = [point[0] for point in layout.values()] or [0.0]
        ys = [point[1] for point in layout.values()] or [0.0]
        span_x, span_y = (max(xs) - min(xs)) or 1.0, (max(ys) - min(ys)) or 1.0
        self.positions = {node: ((x - min(xs)) / span_x, (y - min(ys)) / span_y) for node, (x, y) in layout.items()}
        notes = sum(not node["missing"] for node in self.nodes)
        self.summary_label.setText(f"{notes} notes · {len(self.edges)} links" if self.nodes else
                                   "No notes yet. Link notes by writing [[Another note]].")
        self.update()

    def _point(self, node_id) -> QPointF:
        x, y = self.positions.get(node_id, (0.5, 0.5))
        top = 40
        return QPointF(MARGIN_PX + x * max(1, self.width() - 2 * MARGIN_PX),
                       top + MARGIN_PX / 2 + y * max(1, self.height() - top - MARGIN_PX * 1.5))

    def _node_at(self, point: QPointF) -> dict | None:
        for node in self.nodes:
            delta = self._point(node["id"]) - point
            if delta.x() ** 2 + delta.y() ** 2 <= (NODE_PX * 0.8) ** 2:
                return node
        return None

    def paintEvent(self, _event) -> None:                                # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(PANEL_COLOR))
        painter.setPen(QPen(QColor(EDGE_COLOR), 1.2))
        for edge in self.edges:
            painter.drawLine(self._point(edge["source"]), self._point(edge["target"]))
        for node in self.nodes:
            center = self._point(node["id"])
            is_selected = node["id"] == self.selected_id
            color = QColor(ACCENT_HIGHLIGHT_COLOR if is_selected else TEXT_COLOR)
            if node["missing"]:
                painter.setPen(QPen(QColor(MUTED_TEXT_COLOR), 1.2, Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
            else:
                painter.setPen(QPen(color.darker(130), 1))
                painter.setBrush(color)
            painter.drawEllipse(center, NODE_PX / 2, NODE_PX / 2)
            painter.setPen(QColor(MUTED_TEXT_COLOR if node["missing"] else TEXT_COLOR))
            painter.drawText(QRectF(center.x() - 80, center.y() + NODE_PX / 2 + 2, 160, 16),
                             Qt.AlignmentFlag.AlignHCenter, node["title"])

    def mouseMoveEvent(self, event) -> None:                             # noqa: N802
        node = self._node_at(event.position())
        self.setCursor(Qt.CursorShape.PointingHandCursor if node and not node["missing"] else Qt.CursorShape.ArrowCursor)
        self.setToolTip("" if node is None else node["title"] + (" (not written yet)" if node["missing"] else ""))

    def mousePressEvent(self, event) -> None:                            # noqa: N802
        node = self._node_at(event.position())
        if node is not None and not node["missing"]:
            self.host.select(node["id"])

    def mouseDoubleClickEvent(self, event) -> None:                      # noqa: N802
        node = self._node_at(event.position())
        if node is not None and not node["missing"]:
            self.host.open_record(node["id"])


LINKS_VIEW = ViewType("links", "Links", "Notes as a graph: a line for every [[link]]", ("table",), LinksView,
                      requests=("links",))
register_view_type(LINKS_VIEW)
# --- END [1.1] ---
