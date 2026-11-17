# ==============================================================================
# FILE: app/views/graph_canvas.py
# OVERVIEW: GraphCanvas - draws a graph (nodes and edges) with a seeded spring layout: the same picture every
#           time for the same graph. Shared by the Links view (notes) and the Network view (any Graph shape).
# PURPOSE:
#   1. set_graph(nodes, edges): nodes {id, title, hollow?, group?}, edges {source, target}; laid out with
#      networkx (seed 7), scaled to the widget.
#   2. Interaction: hover tooltip, click -> node_clicked(id), double-click -> node_activated(id); the selected
#      node is highlighted; nodes of the same group share a tag colour.
# CONTRACT:
#   - Requires: networkx, app.ui.theme colour tokens, app.views.api.tag_color
#   - Exposes: GraphCanvas(parent=None) with set_graph(), set_selected(), point_of(id), node_clicked,
#              node_activated, nodes, edges
# NOTES:
#   - [2026-11-17] Created in Phase 5b of the engine plan (from the Links view's drawing, now shared).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - NODE_PX (int | px): Diameter of a node's dot (default: 12).
# - MARGIN_PX (int | px): Space kept around the graph (default: 40).
# - hollow node: Drawn as a dashed outline (a note link to a note not written yet); not clickable.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. GRAPH_CANVAS
#    1.1 canvas
# ==============================================================================

# ==============================================================================
# [1.1] canvas
# ==============================================================================
from __future__ import annotations

import networkx as nx
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from app.ui.theme import ACCENT_HIGHLIGHT_COLOR, EDGE_COLOR, MUTED_TEXT_COLOR, PANEL_COLOR, TEXT_COLOR
from app.views.api import tag_color

NODE_PX = 12
MARGIN_PX = 40


class GraphCanvas(QWidget):
    node_clicked = Signal(object)
    node_activated = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self.positions: dict = {}                         # node id -> (x, y) in 0..1
        self.selected_id = None
        self.setMouseTracking(True)
        self.setMinimumSize(200, 140)

    def set_graph(self, nodes: list[dict], edges: list[dict]) -> None:
        self.nodes, self.edges = nodes, edges
        network = nx.Graph()
        network.add_nodes_from(node["id"] for node in nodes)
        network.add_edges_from((edge["source"], edge["target"]) for edge in edges)
        layout = nx.spring_layout(network, seed=7) if len(network) > 1 else {node: (0.0, 0.0) for node in network}
        xs = [point[0] for point in layout.values()] or [0.0]
        ys = [point[1] for point in layout.values()] or [0.0]
        span_x, span_y = (max(xs) - min(xs)) or 1.0, (max(ys) - min(ys)) or 1.0
        self.positions = {node: ((x - min(xs)) / span_x, (y - min(ys)) / span_y) for node, (x, y) in layout.items()}
        self.update()

    def set_selected(self, node_id) -> None:
        self.selected_id = node_id
        self.update()

    def point_of(self, node_id) -> QPointF:
        x, y = self.positions.get(node_id, (0.5, 0.5))
        return QPointF(MARGIN_PX + x * max(1, self.width() - 2 * MARGIN_PX),
                       MARGIN_PX / 2 + y * max(1, self.height() - MARGIN_PX * 1.5))

    def _node_at(self, point: QPointF) -> dict | None:
        for node in self.nodes:
            delta = self.point_of(node["id"]) - point
            if delta.x() ** 2 + delta.y() ** 2 <= (NODE_PX * 0.8) ** 2:
                return node
        return None

    def paintEvent(self, _event) -> None:                                # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(PANEL_COLOR))
        painter.setPen(QPen(QColor(EDGE_COLOR), 1.2))
        for edge in self.edges:
            painter.drawLine(self.point_of(edge["source"]), self.point_of(edge["target"]))
        for node in self.nodes:
            center = self.point_of(node["id"])
            is_selected = node["id"] == self.selected_id
            color = QColor(ACCENT_HIGHLIGHT_COLOR) if is_selected else \
                tag_color(node["group"]) if node.get("group") else QColor(TEXT_COLOR)
            if node.get("hollow"):
                painter.setPen(QPen(QColor(MUTED_TEXT_COLOR), 1.2, Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
            else:
                painter.setPen(QPen(color.darker(130), 1))
                painter.setBrush(color)
            painter.drawEllipse(center, NODE_PX / 2, NODE_PX / 2)
            painter.setPen(QColor(MUTED_TEXT_COLOR if node.get("hollow") else TEXT_COLOR))
            painter.drawText(QRectF(center.x() - 80, center.y() + NODE_PX / 2 + 2, 160, 16),
                             Qt.AlignmentFlag.AlignHCenter, str(node.get("title", "")))

    def mouseMoveEvent(self, event) -> None:                             # noqa: N802
        node = self._node_at(event.position())
        self.setCursor(Qt.CursorShape.PointingHandCursor if node and not node.get("hollow") else Qt.CursorShape.ArrowCursor)
        self.setToolTip("" if node is None else str(node.get("tip") or node.get("title", "")))

    def mousePressEvent(self, event) -> None:                            # noqa: N802
        node = self._node_at(event.position())
        if node is not None and not node.get("hollow"):
            self.node_clicked.emit(node["id"])

    def mouseDoubleClickEvent(self, event) -> None:                      # noqa: N802
        node = self._node_at(event.position())
        if node is not None and not node.get("hollow"):
            self.node_activated.emit(node["id"])
# --- END [1.1] ---
