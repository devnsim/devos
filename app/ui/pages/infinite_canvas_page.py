# ==============================================================================
# FILE: app/ui/pages/infinite_canvas_page.py
# OVERVIEW: The "Canvas" page kind: an endless grid (like Obsidian's canvas) where module apps sit on cards
#           that snap to the grid. Pan anywhere, zoom, drop as many cards as you like.
# PURPOSE:
#   1. InfiniteCanvas: a dotted grid in world cells (GRID_PX at 100 %); pan (drag empty space, scroll),
#      zoom (Ctrl+scroll, the corner controls), fit all cards; double-click or right-click to add a card.
#   2. ModuleCard: a module on a card - header to drag it, corner grip to resize it (both snap to whole grid
#      cells), ⋯ menu (replace with, add toolbar, bring to front, close). An empty card offers the module
#      picker. Clicking a card brings it to the front.
#   3. Saves cards (module, instance, cell rectangle, stacking order) and the view (pan, zoom) with the page;
#      closing a card keeps the module's data (no silent deletes).
#   4. InfiniteCanvasPage: the page - editable title above the canvas.
# CONTRACT:
#   - Requires: app.ui.canvas.module_canvas (library_choices, create_library_module, dock_module_tools,
#               add_toolbar_menu), app.ui.canvas.tile_canvas (CanvasTile, TILE_MIME_PREFIX),
#               app.ui.pages.canvas_page.PageTitleEdit, app.data.store.pages.canvas_key, app.data.store.database,
#               app.ui.theme (colour tokens), app.ui.ui_tools, app.ui.window_events.mouse_hub,
#               QSS selectors #infinite_canvas_page_panel, #module_card_panel, #card_header_panel,
#               #card_title_label, #card_resize_grip, #canvas_controls_panel, #canvas_control_btn
#   - Exposes: InfiniteCanvasPage(page_id, title, database) (signal title_edited(str); set_title();
#              start_title_edit(); infinite_canvas); InfiniteCanvas(database, page_key) with add_card(),
#              close_card(), bring_to_front(), zoom_to(), fit(), cells, cards, to_layout(), load_saved_layout(),
#              save(); ModuleCard; GRID_PX
# NOTES:
#   - [2026-11-17] Has a channel_hub (app.views.channels): views on this page share their selection.
#   - [2026-11-17] Created (Canvas page kind). Cards are real widgets laid out over a painted grid, not a
#                  QGraphicsScene: web views and OpenGL views don't render inside graphics proxies. Zoom
#                  scales the card rectangles; the modules re-lay themselves out at that size.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - cell: One grid square; card positions and sizes are whole cells (x, y, w, h), so cards always snap.
# - GRID_PX (int | px): Size of a cell at 100 % zoom (default: 20).
# - origin (QPointF | px): Where cell (0, 0)'s corner is on screen; panning moves it.
# - zoom (float): Screen pixels per world pixel (MIN_ZOOM ... MAX_ZOOM).
# - stacking order: self.cards from back to front; saved, so the front card stays in front.
# - canvas layout (dict): {"cards": [{"kind", "instance_id", "title", "cell": [x, y, w, h]}],
#   "view": {"origin": [x, y], "zoom": z}} saved under the page's canvas key.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. CARD
#    2.1 grid_drag_handle
#    2.2 module_card
# 3. CANVAS
#    3.1 init_canvas
#    3.2 geometry
#    3.3 cards
#    3.4 gestures
#    3.5 view_controls
#    3.6 painting
#    3.7 persistence
# 4. PAGE
#    4.1 infinite_canvas_page
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import math
import uuid

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget

from app.data.activity_log import get_activity_log
from app.data.store.database import Database
from app.data.store.pages import canvas_key
from app.ui.canvas.module_canvas import add_toolbar_menu, create_library_module, dock_module_tools, library_choices
from app.ui.canvas.tile_canvas import TILE_MIME_PREFIX, CanvasTile
from app.ui.pages.canvas_page import PageTitleEdit
from app.ui.theme import EDGE_COLOR, MUTED_TEXT_COLOR, SCROLLBAR_HOVER_COLOR
from app.ui.ui_tools import set_style_property, tool_button
from app.ui.window_events import mouse_hub
from app.views.channels import ChannelHub

GRID_PX = 20
# --- END [1.1] ---


# ==============================================================================
# [2.1] grid_drag_handle
# ==============================================================================
class GridDragHandle(QWidget):
    """A press-and-drag area (card header, resize grip): reports how far the mouse moved since the press."""

    started = Signal()
    dragged = Signal(QPoint)                 # movement since the press (screen px)
    finished = Signal()

    def __init__(self, object_name: str, cursor: Qt.CursorShape, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName=object_name)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(cursor)
        self._press_point: QPoint | None = None

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._press_point = event.globalPosition().toPoint()
        self.started.emit()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._press_point is not None:
            self.dragged.emit(event.globalPosition().toPoint() - self._press_point)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._press_point is not None and event.button() == Qt.MouseButton.LeftButton:
            self._press_point = None
            self.finished.emit()
            event.accept()


class CardResizeGrip(GridDragHandle):
    """Bottom-right corner of a card: three short diagonal strokes."""

    SIZE_PX = 14

    def __init__(self, parent: QWidget) -> None:
        super().__init__("card_resize_grip", Qt.CursorShape.SizeFDiagCursor, parent)
        self.setFixedSize(self.SIZE_PX, self.SIZE_PX)
        self.setToolTip("Drag to resize (snaps to the grid)")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(QPen(QColor(MUTED_TEXT_COLOR), 1.2))
        size = self.SIZE_PX - 3
        for offset in (4, 8, 12):
            if offset <= size:
                painter.drawLine(size, size - offset, size - offset, size)
# --- END [2.1] ---


# ==============================================================================
# [2.2] module_card
# ==============================================================================
class ModuleCard(CanvasTile):
    """A module on a card: header (drag to move), body (module or picker), grip (drag to resize)."""

    raise_requested = Signal()

    NOUN = "card"
    EMPTY_TEXT = "What goes on this card?"
    HEADER_HEIGHT_PX = 26

    def __init__(self, tile_id: int, mime_type: str, choices: list[tuple[str, str, str]]) -> None:
        super().__init__(tile_id, mime_type, choices, "module_card_panel")
        self.setMinimumSize(1, 1)                     # the canvas decides the size (zoomed out: tiny cards)
        self.tool_dock = dock_module_tools(self)
        self.card_resize_grip = CardResizeGrip(self)
        self.set_title("")

    def build_chrome(self) -> None:
        self.card_header_panel = GridDragHandle("card_header_panel", Qt.CursorShape.OpenHandCursor)
        self.card_header_panel.setFixedHeight(self.HEADER_HEIGHT_PX)
        self.card_header_panel.setToolTip("Drag to move (snaps to the grid)")
        row = QHBoxLayout(self.card_header_panel)
        row.setContentsMargins(8, 0, 2, 0)
        row.setSpacing(0)
        self.card_title_label = QLabel(objectName="card_title_label")
        self.card_menu_btn = tool_button("tile_control_btn", text="⋯", tool_tip="Card options")
        self.card_menu_btn.clicked.connect(self._show_menu)
        self.card_close_btn = tool_button("tile_control_btn", "tab_close.svg", "Close card (the module's data is kept)", icon_px=12)
        self.card_close_btn.clicked.connect(self.close_requested)
        row.addWidget(self.card_title_label, 1)
        row.addWidget(self.card_menu_btn)
        row.addWidget(self.card_close_btn)
        self.tile_layout.addWidget(self.card_header_panel)

    def set_title(self, title: str) -> None:
        super().set_title(title)
        self.card_title_label.setText(title or "New card")

    def set_content(self, content_widget: QWidget, title: str, kind: str, instance_id: str | None = None) -> None:
        super().set_content(content_widget, title, kind, instance_id)
        self.tool_dock.bind(content_widget)
        self.tool_dock.show()
        self.card_resize_grip.raise_()

    def set_is_active(self, is_active: bool) -> None:
        set_style_property(self, "is_active", is_active)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        grip = self.card_resize_grip
        grip.move(self.width() - grip.width() - 1, self.height() - grip.height() - 1)
        grip.raise_()

    def mousePressEvent(self, event) -> None:
        event.accept()                                # a press on the card never pans the canvas

    def _show_menu(self) -> None:
        menu = QMenu(self)
        replace_menu = menu.addMenu("Replace with" if self.kind else "Show")
        for kind, title, _description in self.choices:
            replace_menu.addAction(title).triggered.connect(lambda _checked=False, kind=kind: self.kind_chosen.emit(kind))
        add_toolbar_menu(menu, self.tool_dock)
        menu.addAction("Bring to front").triggered.connect(self.raise_requested)
        menu.addSeparator()
        menu.addAction("Close card").triggered.connect(self.close_requested)
        menu.exec(self.card_menu_btn.mapToGlobal(self.card_menu_btn.rect().bottomLeft()))
# --- END [2.2] ---


# ==============================================================================
# [3.1] init_canvas
# ==============================================================================
class InfiniteCanvas(QWidget):
    """Endless grid of module cards (see file header)."""

    layout_changed = Signal()                  # debounced: the layout was just saved

    MIN_ZOOM, MAX_ZOOM = 0.25, 2.0
    DEFAULT_CARD_CELLS = (24, 16)
    MIN_CARD_CELLS = (6, 4)
    MIN_DOT_SPACING_PX = 8                     # zoomed out, draw every 2nd / 4th ... dot instead
    MAJOR_EVERY_CELLS = 5
    SAVE_DELAY_MS = 400
    CONTROLS_MARGIN_PX = 10
    EMPTY_HINT = "Double-click anywhere to add a module  ·  drag the grid to move around  ·  Ctrl+scroll to zoom"

    def __init__(self, database: Database | None, page_key: str | None, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="infinite_canvas_panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.database, self.page_key = database, page_key
        self.choices = library_choices() if database is not None else []
        self.mime_type = TILE_MIME_PREFIX + uuid.uuid4().hex
        self.cards: list[ModuleCard] = []              # stacking order: back to front
        self.cells: dict[int, list[int]] = {}         # tile_id -> [x, y, w, h] in cells
        self.origin = QPointF(GRID_PX * 2, GRID_PX * 2)
        self.zoom = 1.0
        self._next_card_id = 1
        self._is_restoring = False
        self._pan_start: tuple[QPointF, QPointF] | None = None      # (mouse, origin) at the press
        self._gesture_start: list[int] | None = None                # card cells at a move / resize press
        self._build_controls()
        self._save_timer = QTimer(self, singleShot=True, interval=self.SAVE_DELAY_MS, timeout=self.save)
        mouse_hub().pressed.connect(self._on_mouse_pressed)

    def _build_controls(self) -> None:
        """Floating bottom-right controls: + module, zoom out / level / in, fit."""
        self.canvas_controls_panel = QWidget(self, objectName="canvas_controls_panel")
        self.canvas_controls_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(self.canvas_controls_panel)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(1)
        self.add_card_btn = tool_button("canvas_control_btn", "plus.svg", "Add a module card (or double-click the grid)",
                                        text="Module", icon_px=14)
        self.add_card_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.add_card_btn.clicked.connect(lambda: self.add_card())
        self.zoom_out_btn = tool_button("canvas_control_btn", text="−", tool_tip="Zoom out (Ctrl+scroll)")
        self.zoom_out_btn.clicked.connect(lambda: self.zoom_to(self.zoom / 1.25))
        self.zoom_level_btn = tool_button("canvas_control_btn", text="100%", tool_tip="Back to 100 %")
        self.zoom_level_btn.clicked.connect(lambda: self.zoom_to(1.0))
        self.zoom_in_btn = tool_button("canvas_control_btn", text="+", tool_tip="Zoom in (Ctrl+scroll)")
        self.zoom_in_btn.clicked.connect(lambda: self.zoom_to(self.zoom * 1.25))
        self.fit_btn = tool_button("canvas_control_btn", text="Fit", tool_tip="Show every card")
        self.fit_btn.clicked.connect(self.fit)
        for button in (self.add_card_btn, self.zoom_out_btn, self.zoom_level_btn, self.zoom_in_btn, self.fit_btn):
            row.addWidget(button)
# --- END [3.1] ---

# ==============================================================================
# [3.2] geometry
# ==============================================================================
    def cell_px(self) -> float:
        return GRID_PX * self.zoom

    def cell_at(self, point: QPointF) -> tuple[float, float]:
        """World cell coordinates (fractional) under a point of this widget."""
        size = self.cell_px()
        return (point.x() - self.origin.x()) / size, (point.y() - self.origin.y()) / size

    def screen_rect(self, cells: list[int]) -> QRect:
        size = self.cell_px()
        x, y, width, height = cells
        left, top = round(self.origin.x() + x * size), round(self.origin.y() + y * size)
        right, bottom = round(self.origin.x() + (x + width) * size), round(self.origin.y() + (y + height) * size)
        return QRect(left, top, right - left, bottom - top)

    def _place(self, card: ModuleCard) -> None:
        card.setGeometry(self.screen_rect(self.cells[card.tile_id]))

    def _place_all(self) -> None:
        for card in self.cards:
            self._place(card)
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        panel = self.canvas_controls_panel
        panel.adjustSize()
        panel.move(self.width() - panel.width() - self.CONTROLS_MARGIN_PX, self.height() - panel.height() - self.CONTROLS_MARGIN_PX)
        panel.raise_()
# --- END [3.2] ---

# ==============================================================================
# [3.3] cards
# ==============================================================================
    def add_card(self, kind: str | None = None, cell: tuple[int, int] | None = None, size: tuple[int, int] | None = None,
                 instance_id: str | None = None, exact: bool = False) -> ModuleCard:
        """A new card (empty with the module picker, or showing `kind`). Without `cell` it lands in the middle
        of the view; unless `exact`, it steps aside from a card already starting at the same cell."""
        width, height = size or self.DEFAULT_CARD_CELLS
        if cell is None:
            centre_x, centre_y = self.cell_at(QPointF(self.width() / 2, self.height() / 2))
            cell = (round(centre_x - width / 2), round(centre_y - height / 2))
        x, y = cell
        taken = {(cells[0], cells[1]) for cells in self.cells.values()}
        while not exact and (x, y) in taken:
            x, y = x + 2, y + 2
        card = ModuleCard(self._next_card_id, self.mime_type, self.choices)
        self._next_card_id += 1
        card.setParent(self)
        self.cells[card.tile_id] = [int(x), int(y), max(int(width), self.MIN_CARD_CELLS[0]), max(int(height), self.MIN_CARD_CELLS[1])]
        self.cards.append(card)
        card.kind_chosen.connect(lambda chosen: self._on_kind_chosen(card, chosen))
        card.close_requested.connect(lambda: self.close_card(card))
        card.raise_requested.connect(lambda: self.bring_to_front(card))
        card.card_header_panel.started.connect(lambda: self._start_gesture(card))
        card.card_header_panel.dragged.connect(lambda delta: self._drag_card(card, delta, is_resize=False))
        card.card_header_panel.finished.connect(lambda: self._finish_gesture(card))
        card.card_resize_grip.started.connect(lambda: self._start_gesture(card))
        card.card_resize_grip.dragged.connect(lambda delta: self._drag_card(card, delta, is_resize=True))
        card.card_resize_grip.finished.connect(lambda: self._finish_gesture(card))
        if kind is not None:
            self._fill(card, kind, instance_id)
        self._place(card)
        card.show()
        self.bring_to_front(card, notify=False)
        self._changed()
        return card

    def _fill(self, card: ModuleCard, kind: str, instance_id: str | None = None) -> None:
        view, title, instance_id = create_library_module(kind, self.database, instance_id)
        card.set_content(view, title, kind, instance_id)

    def _on_kind_chosen(self, card: ModuleCard, kind: str) -> None:
        self._fill(card, kind)
        self._changed()

    def close_card(self, card: ModuleCard) -> None:
        """Take a card off the canvas; its module's saved data is kept."""
        if card not in self.cards:
            return
        if card.title:
            get_activity_log().info(f"page:{self.page_key}", f"Closed module “{card.title}” (its data is kept)")
        self.cards.remove(card)
        del self.cells[card.tile_id]
        card.hide()
        card.setParent(None)
        card.deleteLater()
        self.update()                                  # the empty hint may be back
        self._changed()

    def bring_to_front(self, card: ModuleCard, notify: bool = True) -> None:
        if card not in self.cards:
            return
        is_moved = self.cards[-1] is not card
        self.cards.remove(card)
        self.cards.append(card)
        card.raise_()
        self.canvas_controls_panel.raise_()
        for each_card in self.cards:
            each_card.set_is_active(each_card is card)
        if is_moved and notify:
            self._changed()

    def _on_mouse_pressed(self, widget: QWidget) -> None:
        """A press anywhere inside a card brings it to the front."""
        while widget is not None and widget is not self:
            if isinstance(widget, ModuleCard) and widget in self.cards:
                self.bring_to_front(widget)
                return
            widget = widget.parentWidget()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        mouse_hub().watch(self)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if self._save_timer.isActive():               # don't lose the last move when the page closes
            self.save()
# --- END [3.3] ---

# ==============================================================================
# [3.4] gestures
# ==============================================================================
    def _start_gesture(self, card: ModuleCard) -> None:
        self._gesture_start = list(self.cells[card.tile_id])
        self.bring_to_front(card)

    def _drag_card(self, card: ModuleCard, delta: QPoint, is_resize: bool) -> None:
        """Move (header) or resize (grip) in whole cells: the card snaps as the mouse goes."""
        if self._gesture_start is None:
            return
        size = self.cell_px()
        start, cells = self._gesture_start, self.cells[card.tile_id]
        step_x, step_y = round(delta.x() / size), round(delta.y() / size)
        if is_resize:
            cells[2] = max(self.MIN_CARD_CELLS[0], start[2] + step_x)
            cells[3] = max(self.MIN_CARD_CELLS[1], start[3] + step_y)
        else:
            cells[0], cells[1] = start[0] + step_x, start[1] + step_y
        self._place(card)

    def _finish_gesture(self, card: ModuleCard) -> None:
        if self._gesture_start is not None and self._gesture_start != self.cells.get(card.tile_id):
            self._changed()
        self._gesture_start = None

    def mousePressEvent(self, event) -> None:
        on_empty_grid = self.childAt(event.position().toPoint()) is None
        if on_empty_grid and event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._pan_start = (event.position(), QPointF(self.origin))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._pan_start is not None:
            mouse, origin = self._pan_start
            self.origin = origin + (event.position() - mouse)
            self._place_all()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._pan_start is not None:
            self._pan_start = None
            self.unsetCursor()
            self._changed()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.childAt(event.position().toPoint()) is None:
            cell_x, cell_y = self.cell_at(event.position())
            self.add_card(cell=(math.floor(cell_x), math.floor(cell_y)))
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:
        """Ctrl+scroll zooms around the mouse; scrolling (two-finger pan, Shift = sideways) moves the view."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_to(self.zoom * 1.0015 ** event.angleDelta().y(), event.position())
        else:
            delta = event.pixelDelta()
            if delta.isNull():
                delta = event.angleDelta() / 2
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and delta.x() == 0:
                delta = QPoint(delta.y(), 0)
            self.origin += QPointF(delta)
            self._place_all()
            self._changed()
        event.accept()

    def contextMenuEvent(self, event) -> None:
        if self.childAt(event.pos()) is not None:
            super().contextMenuEvent(event)
            return
        cell_x, cell_y = self.cell_at(QPointF(event.pos()))
        menu = QMenu(self)
        menu.addAction("Add module here").triggered.connect(
            lambda: self.add_card(cell=(math.floor(cell_x), math.floor(cell_y))))
        menu.addAction("Show every card").triggered.connect(self.fit)
        menu.addAction("Zoom to 100 %").triggered.connect(lambda: self.zoom_to(1.0))
        menu.exec(event.globalPos())
# --- END [3.4] ---

# ==============================================================================
# [3.5] view_controls
# ==============================================================================
    def zoom_to(self, zoom: float, anchor: QPointF | None = None) -> None:
        """Zoom, keeping the world point under `anchor` (default: the middle of the view) in place."""
        zoom = min(max(zoom, self.MIN_ZOOM), self.MAX_ZOOM)
        anchor = anchor if anchor is not None else QPointF(self.width() / 2, self.height() / 2)
        cell_x, cell_y = self.cell_at(anchor)
        self.zoom = zoom
        size = self.cell_px()
        self.origin = QPointF(anchor.x() - cell_x * size, anchor.y() - cell_y * size)
        self.zoom_level_btn.setText(f"{zoom:.0%}")
        self._place_all()
        self._changed()

    def fit(self) -> None:
        """Zoom and pan so every card is in view (an empty canvas goes back to 100 % at the start)."""
        if not self.cards:
            self.zoom, self.origin = 1.0, QPointF(GRID_PX * 2, GRID_PX * 2)
            self.zoom_level_btn.setText("100%")
            self._place_all()
            self._changed()
            return
        left = min(cells[0] for cells in self.cells.values())
        top = min(cells[1] for cells in self.cells.values())
        right = max(cells[0] + cells[2] for cells in self.cells.values())
        bottom = max(cells[1] + cells[3] for cells in self.cells.values())
        margin = GRID_PX * 2
        zoom = min((self.width() - 2 * margin) / ((right - left) * GRID_PX),
                   (self.height() - 2 * margin) / ((bottom - top) * GRID_PX))
        self.zoom = min(max(zoom, self.MIN_ZOOM), self.MAX_ZOOM)
        size = self.cell_px()
        self.origin = QPointF((self.width() - (right - left) * size) / 2 - left * size,
                              (self.height() - (bottom - top) * size) / 2 - top * size)
        self.zoom_level_btn.setText(f"{self.zoom:.0%}")
        self._place_all()
        self._changed()
# --- END [3.5] ---

# ==============================================================================
# [3.6] painting
# ==============================================================================
    def paintEvent(self, event) -> None:
        """Dots on every grid corner (every 2nd / 4th ... when zoomed far out), brighter every MAJOR_EVERY_CELLS."""
        super().paintEvent(event)
        painter = QPainter(self)
        step = 1
        while self.cell_px() * step < self.MIN_DOT_SPACING_PX:
            step *= 2
        spacing = self.cell_px() * step
        first_x = math.floor(-self.origin.x() / spacing) * step
        first_y = math.floor(-self.origin.y() / spacing) * step
        minor, major = QPolygonF(), QPolygonF()
        cell_y = first_y
        while self.origin.y() + cell_y * self.cell_px() <= self.height():
            cell_x = first_x
            screen_y = self.origin.y() + cell_y * self.cell_px()
            while self.origin.x() + cell_x * self.cell_px() <= self.width():
                point = QPointF(self.origin.x() + cell_x * self.cell_px(), screen_y)
                is_major = cell_x % self.MAJOR_EVERY_CELLS == 0 and cell_y % self.MAJOR_EVERY_CELLS == 0
                (major if is_major else minor).append(point)
                cell_x += step
            cell_y += step
        painter.setPen(QPen(QColor(EDGE_COLOR), 1.5))
        painter.drawPoints(minor)
        painter.setPen(QPen(QColor(SCROLLBAR_HOVER_COLOR), 2.5))
        painter.drawPoints(major)
        if not self.cards:
            painter.setPen(QColor(MUTED_TEXT_COLOR))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.EMPTY_HINT)
# --- END [3.6] ---

# ==============================================================================
# [3.7] persistence
# ==============================================================================
    def _changed(self) -> None:
        if not self._is_restoring:
            self._save_timer.start()

    def to_layout(self) -> dict:
        return {"cards": [{"kind": card.kind, "instance_id": card.instance_id, "title": card.title,
                           "cell": list(self.cells[card.tile_id])} for card in self.cards],
                "view": {"origin": [self.origin.x(), self.origin.y()], "zoom": self.zoom}}

    def save(self) -> None:
        self._save_timer.stop()
        if self.database is None or not self.page_key:
            return
        self.database.save_layout(self.page_key, self.to_layout())
        get_activity_log().debug(f"page:{self.page_key}", "Canvas saved")
        self.layout_changed.emit()

    def load_saved_layout(self) -> bool:
        """Restore this page's cards and view, if any were saved."""
        if self.database is None or not self.page_key:
            return False
        layout = self.database.load_layout(self.page_key)
        if not layout or "cards" not in layout:
            return False
        known_kinds = {kind for kind, _title, _description in self.choices}
        self._is_restoring = True
        try:
            view = layout.get("view", {})
            origin = view.get("origin", [self.origin.x(), self.origin.y()])
            self.origin = QPointF(float(origin[0]), float(origin[1]))
            self.zoom = min(max(float(view.get("zoom", 1.0)), self.MIN_ZOOM), self.MAX_ZOOM)
            self.zoom_level_btn.setText(f"{self.zoom:.0%}")
            for entry in layout["cards"]:
                x, y, width, height = (int(value) for value in entry["cell"])
                kind = entry.get("kind") if entry.get("kind") in known_kinds else None
                self.add_card(kind, (x, y), (width, height), entry.get("instance_id"), exact=True)
        except (KeyError, TypeError, ValueError) as error:
            get_activity_log().warning(f"page:{self.page_key}", f"Saved canvas could not be fully restored: {error}")
        finally:
            self._is_restoring = False
        self._place_all()
        return bool(self.cards)
# --- END [3.7] ---


# ==============================================================================
# [4.1] infinite_canvas_page
# ==============================================================================
class InfiniteCanvasPage(QWidget):
    """A "Canvas" page: editable title above an endless grid of module cards."""

    title_edited = Signal(str)

    TITLE_MARGIN_PX = 8

    def __init__(self, page_id: int, title: str, database: Database) -> None:
        super().__init__(objectName="infinite_canvas_page_panel")
        self.page_id = page_id
        self.channel_hub = ChannelHub(self)           # page-wide channels: every view on this page links here
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        title_row = QHBoxLayout()
        margin = self.TITLE_MARGIN_PX
        title_row.setContentsMargins(margin, margin, margin, margin // 2)
        self.page_title_edit = PageTitleEdit(title)
        self.page_title_edit.title_edited.connect(self.title_edited)
        title_row.addWidget(self.page_title_edit)
        layout.addLayout(title_row)
        self.infinite_canvas = InfiniteCanvas(database, canvas_key(page_id))
        layout.addWidget(self.infinite_canvas, 1)
        self.infinite_canvas.load_saved_layout()

    def set_title(self, title: str) -> None:
        self.page_title_edit.set_title(title)

    def start_title_edit(self) -> None:
        self.page_title_edit.start_edit()
# --- END [4.1] ---
