# ==============================================================================
# FILE: app/ui/canvas/tile_canvas.py
# OVERVIEW: The one tiling canvas DevOS uses everywhere: pages hold module tiles, modules hold block
#           tiles. Tiles tile, move, split, close and resize; empty tiles offer a picker; the layout
#           can be saved and restored.
# PURPOSE:
#   1. CanvasTile: one tile - optional chrome (header / floating controls, from subclasses), a body that
#      shows content or a picker, drag source, drop target with zone preview.
#   2. TileCanvas: splitter tree of tiles (tiling_layout.py): add / split / close / move (edge = tile
#      beside, centre = swap), active tile, debounced layout_changed, to_layout() / restore_layout().
#   3. DragStarter: turns any widget into a drag handle (press + move past the drag distance).
#   4. CornerControlsTile: tile chrome with no header - fading grip / split / close in the corner;
#      reserve_controls_space() keeps content rows clear of it.
# CONTRACT:
#   - Requires: app.ui.widgets.tiling_layout, app.ui.ui_tools, app.ui.window_events.mouse_hub, QSS selectors #drop_overlay_panel, #tile_controls_panel,
#               #tile_control_btn,
#               #tile_picker_panel, #empty_tile_label, #tile_choice_combo, #open_tile_btn
#   - Exposes: CanvasTile, CornerControlsTile (extend_menu hook), reserve_controls_space(), TileCanvas, DragStarter, TILE_MIME_PREFIX. Subclasses implement
#              TileCanvas.create_content(kind, instance_id, state) -> (widget, title, instance_id) and
#              set TileCanvas.tile_class (and keeps_last_tile). TileCanvas signals: layout_changed, emptied.
# NOTES:
#   - [2026-11-16] CornerControlsTile.extend_menu(menu) hook: module tiles add "Add toolbar" to the grip menu.
#   - [2026-11-16] Active tile follows mouse presses via window_events.mouse_hub() instead of one event
#                  filter per canvas on the QApplication (PySide crashed on Qt WebEngine's internal objects).
#   - [2026-11-16] keeps_last_tile (class flag): canvases that may become empty (page canvases) let the last
#                  tile close and emit emptied(); block canvases still always keep one tile.
#   - [2026-11-15] CornerControlsTile (moved here from BlockTile) so module tiles and blocks share the same
#                  header-less chrome; reserve_controls_space() moved here too.
#   - [2026-11-15] Created in the health pass from module_canvas.py + module_tile.py so blocks inside
#                  modules reuse the exact same tiling instead of a second copy. Layout entries now use
#                  "kind" (old saved layouts with "module" are still read).
#   - [2026-11-15] Every canvas has its own drag MIME type, so tiles only drop into their own canvas;
#                  a drag a nested canvas ignores falls through to the parent canvas's tile.
#   - [2026-11-15] Moves run on the next event-loop turn: a drop is delivered inside the source tile's
#                  QDrag.exec(), and reparenting the drag source mid-drag is unsafe.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - choices (list[tuple[str, str, str]]): (kind, title, description) offered by empty-tile pickers.
# - content state (dict | None): Optional per-tile settings a content widget saves via block_state()
#   and gets back via restore_block_state(state).
# - kind (str): What a tile shows (a module type id, a block type id); None = empty tile.
# - layout (dict): {"split": "h"|"v", "sizes": [...], "children": [...]} or a tile entry
#   {"kind", "instance_id", "title", "state"}.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS_AND_CONSTANTS
#    1.1 imports
# 2. DRAG_STARTER
#    2.1 drag_starter
# 3. CANVAS_TILE
#    3.1 init_tile
#    3.2 content_and_picker
#    3.3 drag_and_drop
#    3.4 corner_controls_tile
# 4. TILE_CANVAS
#    4.1 init_canvas
#    4.2 add_split_close
#    4.3 move_tiles
#    4.4 active_tile
#    4.5 layout_persistence
#    4.6 bookkeeping
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import uuid

from PySide6.QtCore import QByteArray, QEasingCurve, QEvent, QMimeData, QObject, QPoint, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QDrag
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QComboBox, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

from app.ui.ui_tools import tool_button
from app.ui.window_events import mouse_hub
from app.ui.widgets.tiling_layout import (
    DROP_ZONE_BOTTOM, DROP_ZONE_RIGHT, EDGE_DROP_ZONES, edge_zone_at, insert_beside, new_splitter, normalize_tree, swap_leaves,
    zone_preview_rect,
)

TILE_MIME_PREFIX = "application/x-devos-tile-"
# --- END [1.1] ---


# ==============================================================================
# [2.1] drag_starter
# ==============================================================================
class DragStarter(QObject):
    """Event filter: a press + move past the system drag distance on `widget` calls `on_drag()`.
    A press-release without movement still reaches the widget (e.g. a button's click)."""

    def __init__(self, widget: QWidget, on_drag) -> None:
        super().__init__(widget)
        self.widget, self.on_drag = widget, on_drag
        self._press_point: QPoint | None = None
        widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._press_point = event.position().toPoint()
        elif event_type == QEvent.Type.MouseMove and self._press_point is not None:
            if (event.position().toPoint() - self._press_point).manhattanLength() >= QApplication.startDragDistance():
                self._press_point = None
                if isinstance(self.widget, QAbstractButton):
                    self.widget.setDown(False)           # the drag loop swallows the release
                self.on_drag()
                return True
        elif event_type == QEvent.Type.MouseButtonRelease:
            self._press_point = None
        return False
# --- END [2.1] ---


# ==============================================================================
# [3.1] init_tile
# ==============================================================================
class CanvasTile(QFrame):
    """One tile. Subclasses add chrome in build_chrome() and react in set_title / set_can_close /
    set_is_active."""

    split_requested = Signal(str)            # drop zone for the new empty tile ("right", "bottom" ...)
    close_requested = Signal()
    kind_chosen = Signal(str)                # picked in the empty-tile picker
    tile_dropped = Signal(int, str)          # source tile_id, drop zone
    drag_started = Signal()
    drag_finished = Signal()

    MIN_WIDTH_PX = 160
    MIN_HEIGHT_PX = 100
    DRAG_PIXMAP_MAX_WIDTH_PX = 240
    EMPTY_TEXT = "Empty — pick what to show here"

    def __init__(self, tile_id: int, mime_type: str, choices: list[tuple[str, str, str]], object_name: str) -> None:
        super().__init__(objectName=object_name)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumSize(self.MIN_WIDTH_PX, self.MIN_HEIGHT_PX)
        self.setAcceptDrops(True)
        self.tile_id, self.mime_type, self.choices = tile_id, mime_type, choices
        self.content_widget: QWidget | None = None
        self.kind: str | None = None
        self.instance_id: str | None = None
        self.title = ""
        self.tile_picker_panel: QWidget | None = None

        self.tile_layout = QVBoxLayout(self)
        self.tile_layout.setContentsMargins(0, 0, 0, 0)
        self.tile_layout.setSpacing(0)
        self.build_chrome()
        self.body_panel = QWidget(objectName="tile_body_panel")
        self.body_layout = QVBoxLayout(self.body_panel)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.tile_layout.addWidget(self.body_panel, 1)
        self._show_picker()

        self.drop_overlay_panel = QWidget(self, objectName="drop_overlay_panel")
        self.drop_overlay_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.drop_overlay_panel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.drop_overlay_panel.hide()

    # --- hooks for subclasses ---
    def build_chrome(self) -> None:
        """Add a header or overlay controls (called before the body is built)."""

    def set_title(self, title: str) -> None:
        self.title = title

    def set_can_close(self, can_close: bool) -> None:
        """Called with False while this is the canvas's only tile."""

    def set_is_active(self, is_active: bool) -> None:
        """Called when this tile becomes / stops being the canvas's active tile."""
# --- END [3.1] ---

# ==============================================================================
# [3.2] content_and_picker
# ==============================================================================
    def _show_picker(self) -> None:
        self.tile_picker_panel = QWidget(objectName="tile_picker_panel")
        picker_layout = QVBoxLayout(self.tile_picker_panel)
        picker_layout.addStretch()
        empty_tile_label = QLabel(self.EMPTY_TEXT, objectName="empty_tile_label", alignment=Qt.AlignmentFlag.AlignCenter)
        empty_tile_label.setWordWrap(True)
        picker_layout.addWidget(empty_tile_label)
        if self.choices:
            row = QHBoxLayout()
            row.addStretch()
            self.tile_choice_combo = QComboBox(objectName="tile_choice_combo")
            for kind, title, description in self.choices:
                self.tile_choice_combo.addItem(title, kind)
                self.tile_choice_combo.setItemData(self.tile_choice_combo.count() - 1, description, Qt.ItemDataRole.ToolTipRole)
            self.open_tile_btn = QPushButton("Open", objectName="open_tile_btn")
            self.open_tile_btn.clicked.connect(lambda: self.kind_chosen.emit(self.tile_choice_combo.currentData()))
            row.addWidget(self.tile_choice_combo)
            row.addWidget(self.open_tile_btn)
            row.addStretch()
            picker_layout.addLayout(row)
        picker_layout.addStretch()
        self.body_layout.addWidget(self.tile_picker_panel)

    def set_content(self, content_widget: QWidget, title: str, kind: str, instance_id: str | None = None) -> None:
        """Show content (replacing the picker or earlier content)."""
        for old in (self.content_widget, self.tile_picker_panel):
            if old is not None:
                old.setParent(None)
                old.deleteLater()
        self.tile_picker_panel = None
        self.content_widget, self.kind, self.instance_id = content_widget, kind, instance_id
        self.body_layout.addWidget(content_widget)
        self.set_title(title)

    def content_state(self) -> dict | None:
        saver = getattr(self.content_widget, "block_state", None)
        return saver() if callable(saver) else None
# --- END [3.2] ---

# ==============================================================================
# [3.3] drag_and_drop
# ==============================================================================
    def start_drag(self) -> None:
        mime_data = QMimeData()
        mime_data.setData(self.mime_type, QByteArray(str(self.tile_id).encode()))
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        ghost = self.grab()
        if ghost.width() > self.DRAG_PIXMAP_MAX_WIDTH_PX:
            ghost = ghost.scaledToWidth(self.DRAG_PIXMAP_MAX_WIDTH_PX, Qt.TransformationMode.SmoothTransformation)
        drag.setPixmap(ghost)
        drag.setHotSpot(QPoint(20, 10))
        self.drag_started.emit()
        drag.exec(Qt.DropAction.MoveAction)            # blocks until dropped or cancelled
        self.drag_finished.emit()

    def _dragged_tile_id(self, event) -> int | None:
        """tile_id carried by the drag if it comes from this canvas (and isn't this tile)."""
        if not event.mimeData().hasFormat(self.mime_type):
            return None
        source_tile_id = int(bytes(event.mimeData().data(self.mime_type)).decode())
        return None if source_tile_id == self.tile_id else source_tile_id

    def dragEnterEvent(self, event) -> None:
        if self._dragged_tile_id(event) is None:
            event.ignore()                              # let an outer canvas's tile take it
            return
        event.acceptProposedAction()
        self._show_drop_preview(event.position().toPoint())

    def dragMoveEvent(self, event) -> None:
        if self._dragged_tile_id(event) is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self._show_drop_preview(event.position().toPoint())

    def dragLeaveEvent(self, event) -> None:
        self.drop_overlay_panel.hide()

    def dropEvent(self, event) -> None:
        self.drop_overlay_panel.hide()
        source_tile_id = self._dragged_tile_id(event)
        if source_tile_id is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self.tile_dropped.emit(source_tile_id, edge_zone_at(self.rect(), event.position().toPoint()))

    def _show_drop_preview(self, point: QPoint) -> None:
        self.drop_overlay_panel.setGeometry(zone_preview_rect(self.rect(), edge_zone_at(self.rect(), point)))
        self.drop_overlay_panel.raise_()
        self.drop_overlay_panel.show()
# --- END [3.3] ---


# ==============================================================================
# [3.4] corner_controls_tile
# ==============================================================================
CONTROLS_RESERVE_PX = 96                       # width of the corner controls + margin


def reserve_controls_space(row_layout) -> None:
    """End a tile content's top row with room for the floating corner controls, so they never cover
    (or steal clicks from) the content's own widgets."""
    row_layout.addSpacing(CONTROLS_RESERVE_PX)


class CornerControlsTile(CanvasTile):
    """A tile with no header: grip / split / close float in the top-right corner, almost transparent
    until the mouse is over the tile or for FLASH_MS after it appears or gets focus. The grip drags the
    tile; clicking it opens a menu (replace with, split right / down, close). Used for modules on pages
    and blocks in modules."""

    NOUN = "tile"
    IDLE_OPACITY = 0.12
    FLASH_MS = 2500
    FADE_MS = 220

    def __init__(self, tile_id: int, mime_type: str, choices: list[tuple[str, str, str]], object_name: str) -> None:
        super().__init__(tile_id, mime_type, choices, object_name)
        self.tile_controls_panel.raise_()
        self.flash_controls()                        # visible for a moment when the tile appears

    def build_chrome(self) -> None:
        # Floating (not in the layout): sits over the tile's top-right corner.
        self.tile_controls_panel = QWidget(self, objectName="tile_controls_panel")
        self.tile_controls_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        controls_layout = QHBoxLayout(self.tile_controls_panel)
        controls_layout.setContentsMargins(2, 2, 2, 2)
        controls_layout.setSpacing(0)
        self.grip_btn = tool_button("tile_control_btn", text="⠿", tool_tip="Drag to move · click for options")
        self.grip_btn.setCursor(Qt.CursorShape.OpenHandCursor)
        DragStarter(self.grip_btn, self.start_drag)
        self.grip_btn.clicked.connect(self._show_menu)
        self.split_tile_btn = tool_button("tile_control_btn", "split_pane.svg", f"Split right (new {self.NOUN})", icon_px=14)
        self.split_tile_btn.clicked.connect(lambda: self.split_requested.emit(DROP_ZONE_RIGHT))
        self.close_tile_btn = tool_button("tile_control_btn", "tab_close.svg", f"Close {self.NOUN}", icon_px=14)
        self.close_tile_btn.clicked.connect(self.close_requested)
        for button in (self.grip_btn, self.split_tile_btn, self.close_tile_btn):
            controls_layout.addWidget(button)

        self._opacity_effect = QGraphicsOpacityEffect(self.tile_controls_panel)
        self._opacity_effect.setOpacity(1.0)
        self.tile_controls_panel.setGraphicsEffect(self._opacity_effect)
        self._fade_animation = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_animation.setDuration(self.FADE_MS)
        self._fade_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._flash_timer = QTimer(self, singleShot=True, interval=self.FLASH_MS, timeout=self._fade_if_idle)

    def set_title(self, title: str) -> None:
        super().set_title(title)
        self.grip_btn.setToolTip(f"{title} · drag to move · click for options" if title else "Drag to move")

    def set_can_close(self, can_close: bool) -> None:
        self.close_tile_btn.setVisible(can_close)
        self._place_controls()

    def set_is_active(self, is_active: bool) -> None:
        if is_active:
            self.flash_controls()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_controls()

    def _place_controls(self) -> None:
        self.tile_controls_panel.adjustSize()
        size = self.tile_controls_panel.sizeHint()
        self.tile_controls_panel.setGeometry(self.width() - size.width() - 4, 4, size.width(), size.height())
        self.tile_controls_panel.raise_()

    def set_content(self, content_widget: QWidget, title: str, kind: str, instance_id: str | None = None) -> None:
        super().set_content(content_widget, title, kind, instance_id)
        self.tile_controls_panel.raise_()          # stay above the new content (also over OpenGL views)
        self.flash_controls()
    def flash_controls(self) -> None:
        """Show the controls fully for FLASH_MS, then fade (unless the mouse is over the block)."""
        self._fade_to(1.0)
        self._flash_timer.start()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._fade_to(1.0)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if not self._flash_timer.isActive():
            self._fade_if_idle()

    def _fade_if_idle(self) -> None:
        if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self._fade_to(self.IDLE_OPACITY)

    def _fade_to(self, opacity: float) -> None:
        if abs(self._opacity_effect.opacity() - opacity) < 0.01:
            return
        self._fade_animation.stop()
        self._fade_animation.setStartValue(self._opacity_effect.opacity())
        self._fade_animation.setEndValue(opacity)
        self._fade_animation.start()

    def extend_menu(self, menu: QMenu) -> None:
        """Hook: subclasses add their own entries to the grip menu (above Close)."""

    def _show_menu(self) -> None:
        menu = QMenu(self)
        replace_menu = menu.addMenu("Replace with")
        for kind, title, _description in self.choices:
            replace_menu.addAction(title).triggered.connect(lambda _checked=False, kind=kind: self.kind_chosen.emit(kind))
        menu.addAction("Split right").triggered.connect(lambda: self.split_requested.emit(DROP_ZONE_RIGHT))
        menu.addAction("Split down").triggered.connect(lambda: self.split_requested.emit(DROP_ZONE_BOTTOM))
        self.extend_menu(menu)
        close_action = menu.addAction(f"Close {self.NOUN}")
        close_action.setEnabled(self.close_tile_btn.isVisible())
        close_action.triggered.connect(self.close_requested)
        menu.exec(self.grip_btn.mapToGlobal(self.grip_btn.rect().bottomLeft()))
# --- END [3.4] ---


# ==============================================================================
# [4.1] init_canvas
# ==============================================================================
class TileCanvas(QWidget):
    """Tiling canvas of CanvasTiles (see file header). Subclasses: ModuleCanvas, BlockCanvas."""

    layout_changed = Signal()                  # debounced: save the layout now
    emptied = Signal()                         # the last tile closed (only when keeps_last_tile is False)

    tile_class: type[CanvasTile] = CanvasTile
    keeps_last_tile = True                     # False: the last tile can close, leaving an empty canvas
    SEAM_WIDTH_PX = 4
    SAVE_DELAY_MS = 400

    def __init__(self, choices: list[tuple[str, str, str]], object_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName=object_name)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.choices = choices
        self.mime_type = TILE_MIME_PREFIX + uuid.uuid4().hex
        self.tiles: dict[int, CanvasTile] = {}
        self.active_tile: CanvasTile | None = None
        self._next_tile_id = 1
        self._is_restoring = False

        self.canvas_layout = QVBoxLayout(self)
        self.canvas_layout.setContentsMargins(0, 0, 0, 0)
        self.canvas_layout.setSpacing(4)
        self.root_splitter = new_splitter(Qt.Orientation.Horizontal, self.SEAM_WIDTH_PX)
        self.canvas_layout.addWidget(self.root_splitter, 1)
        self._change_timer = QTimer(self, singleShot=True, interval=self.SAVE_DELAY_MS, timeout=self.layout_changed.emit)
        mouse_hub().pressed.connect(self._on_mouse_pressed)   # click inside a tile -> active tile

    def create_content(self, kind: str, instance_id: str | None, state: dict | None) -> tuple[QWidget, str, str | None]:
        """Build a tile's content: (widget, title, instance_id). Implemented by subclasses."""
        raise NotImplementedError
# --- END [4.1] ---

# ==============================================================================
# [4.2] add_split_close
# ==============================================================================
    def add_tile(self, kind: str | None = None, beside: CanvasTile | None = None, drop_zone: str = DROP_ZONE_RIGHT,
                 instance_id: str | None = None, state: dict | None = None) -> CanvasTile:
        """Add a tile showing `kind` (or an empty picker tile). With `beside`, it takes half of that
        tile on the drop_zone side; otherwise it joins the top-level row."""
        tile = self._new_tile()
        if kind is not None:
            self._fill_tile(tile, kind, instance_id, state)
        self._place_tile(tile, beside, drop_zone)
        if kind is not None:
            self._set_active_tile(tile)
        return tile

    def split_tile(self, tile: CanvasTile, drop_zone: str = DROP_ZONE_RIGHT) -> CanvasTile:
        """New empty tile (with a picker) on one side of `tile`."""
        return self.add_tile(beside=tile, drop_zone=drop_zone)

    def close_tile(self, tile: CanvasTile) -> None:
        """Remove a tile (its content's data is never deleted here). The last tile closes only when
        keeps_last_tile is False; the canvas then emits emptied()."""
        if (self.keeps_last_tile and len(self.tiles) <= 1) or self.tiles.get(tile.tile_id) is not tile:
            return
        del self.tiles[tile.tile_id]
        if self.active_tile is tile:
            self.active_tile = None
        tile.hide()
        tile.setParent(None)                           # leaves its splitter
        normalize_tree(self.root_splitter)
        self._after_change()
        tile.deleteLater()
        if not self.tiles:
            self.emptied.emit()

    def _fill_tile(self, tile: CanvasTile, kind: str, instance_id: str | None = None, state: dict | None = None) -> None:
        widget, title, instance_id = self.create_content(kind, instance_id, state)
        tile.set_content(widget, title, kind, instance_id)

    def _place_tile(self, tile: CanvasTile, beside: CanvasTile | None, drop_zone: str) -> None:
        if beside is None or self.tiles.get(beside.tile_id) is not beside or beside is tile:
            self.root_splitter.addWidget(tile)
        else:
            insert_beside(beside, tile, drop_zone, self.SEAM_WIDTH_PX)
        self._after_change()
# --- END [4.2] ---

# ==============================================================================
# [4.3] move_tiles
# ==============================================================================
    def move_tile(self, source: CanvasTile, target: CanvasTile, drop_zone: str) -> None:
        """Centre: swap the two tiles. Edge: take source out and tile it on that side of target."""
        if source is target:
            return
        if drop_zone not in EDGE_DROP_ZONES:
            swap_leaves(source, target)
        else:
            source.setParent(None)                     # leaves its splitter (and hides)
            normalize_tree(self.root_splitter)         # target is a leaf, so it survives the tidy
            insert_beside(target, source, drop_zone, self.SEAM_WIDTH_PX)
            source.show()
        self._after_change()

    def _on_tile_dropped(self, target: CanvasTile, source_tile_id: int, drop_zone: str) -> None:
        source = self.tiles.get(source_tile_id)
        if source is not None:
            QTimer.singleShot(0, lambda: self.move_tile(source, target, drop_zone))   # see NOTES
# --- END [4.3] ---

# ==============================================================================
# [4.4] active_tile
# ==============================================================================
    def _set_active_tile(self, tile: CanvasTile | None) -> None:
        previous, self.active_tile = self.active_tile, tile
        for each_tile in self.tiles.values():
            is_active = each_tile is tile
            if is_active or each_tile is previous:
                each_tile.set_is_active(is_active and len(self.tiles) > 1)

    def _on_mouse_pressed(self, widget: QWidget) -> None:
        while widget is not None:
            if isinstance(widget, CanvasTile) and self.tiles.get(widget.tile_id) is widget:
                if widget is not self.active_tile:
                    self._set_active_tile(widget)
                else:
                    widget.set_is_active(len(self.tiles) > 1)    # re-announce (e.g. flash controls)
                break
            widget = widget.parentWidget()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        mouse_hub().watch(self)                    # this canvas's window (main window or its own)
# --- END [4.4] ---

# ==============================================================================
# [4.5] layout_persistence
# ==============================================================================
    def to_layout(self) -> dict:
        def describe(widget: QWidget) -> dict:
            if isinstance(widget, QSplitter):
                return {"split": "h" if widget.orientation() == Qt.Orientation.Horizontal else "v",
                        "sizes": widget.sizes(),
                        "children": [describe(widget.widget(index)) for index in range(widget.count())]}
            entry = {"kind": widget.kind, "instance_id": widget.instance_id, "title": widget.title}
            state = widget.content_state()
            if state:
                entry["state"] = state
            return entry
        return describe(self.root_splitter)

    def restore_layout(self, layout: dict | None) -> bool:
        """Rebuild tiles from a layout. Returns False (canvas left empty) if it can't be used."""
        if not layout or not layout.get("children"):
            return False
        self._is_restoring = True
        try:
            self._build_from_layout(self.root_splitter, layout)
        except (KeyError, TypeError, ValueError) as error:
            print(f"[DevOS] could not restore {self.objectName()} layout: {error}")
            for tile in list(self.tiles.values()):
                tile.setParent(None)
                tile.deleteLater()
            self.tiles.clear()
            return False
        finally:
            self._is_restoring = False
        normalize_tree(self.root_splitter)
        self._after_change(notify=False)
        return bool(self.tiles)

    def _build_from_layout(self, splitter: QSplitter, layout: dict) -> None:
        splitter.setOrientation(Qt.Orientation.Horizontal if layout.get("split", "h") == "h" else Qt.Orientation.Vertical)
        known_kinds = {kind for kind, _title, _description in self.choices}
        for child in layout["children"]:
            if "children" in child:
                child_splitter = new_splitter(Qt.Orientation.Horizontal, self.SEAM_WIDTH_PX)
                splitter.addWidget(child_splitter)
                self._build_from_layout(child_splitter, child)
                continue
            tile = self._new_tile()
            kind = child.get("kind", child.get("module"))       # "module": layouts saved before tile_canvas
            if kind in known_kinds:
                self._fill_tile(tile, kind, child.get("instance_id"), child.get("state"))
            splitter.addWidget(tile)
        if layout.get("sizes") and len(layout["sizes"]) == splitter.count():
            splitter.setSizes(layout["sizes"])
# --- END [4.5] ---

# ==============================================================================
# [4.6] bookkeeping
# ==============================================================================
    def _new_tile(self) -> CanvasTile:
        tile_id, self._next_tile_id = self._next_tile_id, self._next_tile_id + 1
        tile = self.tile_class(tile_id, self.mime_type, self.choices)
        tile.split_requested.connect(lambda drop_zone: self.split_tile(tile, drop_zone))
        tile.close_requested.connect(lambda: self.close_tile(tile))
        tile.tile_dropped.connect(lambda source_tile_id, drop_zone: self._on_tile_dropped(tile, source_tile_id, drop_zone))
        tile.kind_chosen.connect(lambda kind: self._on_kind_chosen(tile, kind))
        self.tiles[tile_id] = tile
        return tile

    def _on_kind_chosen(self, tile: CanvasTile, kind: str) -> None:
        self._fill_tile(tile, kind)
        self._set_active_tile(tile)
        self._after_change()

    def _after_change(self, notify: bool = True) -> None:
        can_close = len(self.tiles) > 1 or not self.keeps_last_tile
        for tile in self.tiles.values():
            tile.set_can_close(can_close)
        self._hook_splitters(self.root_splitter)
        if notify and not self._is_restoring:
            self._change_timer.start()

    def _hook_splitters(self, splitter: QSplitter) -> None:
        """Seam drags count as layout changes too (each splitter hooked once)."""
        if not splitter.property("is_change_hooked"):
            splitter.splitterMoved.connect(lambda *_: None if self._is_restoring else self._change_timer.start())
            splitter.setProperty("is_change_hooked", True)
        for index in range(splitter.count()):
            child = splitter.widget(index)
            if isinstance(child, QSplitter):
                self._hook_splitters(child)
# --- END [4.6] ---
