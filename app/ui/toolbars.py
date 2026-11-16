# ==============================================================================
# FILE: app/ui/toolbars.py
# OVERVIEW: Module toolbars ("tool trays") - a standard dock for tools that any module tile can hold.
#           A tray arrives blank; right-click it to add tools from the tool bag, dock it to an edge or let
#           it float, change its size, pin it, or remove it. Tools can be dragged to reorder them or onto
#           another tray of the same module.
# PURPOSE:
#   1. ToolTray: one bar of tool buttons (glyphs) with a grip; horizontal on top / bottom / floating,
#      vertical on left / right; context menus for the tray and for each tool.
#   2. ToolDock: wraps a module tile's content; holds trays on four edges and floating ones; dragging a
#      tray's grip snaps it to the nearest edge (or floats it where it's dropped); saves the trays in the
#      module's settings ("toolbars") and restores them.
# CONTRACT:
#   - Requires: app.ui.tool_bag (ToolSpec, get_tool, run_tool, ToolBagPopup), app.ui.canvas.tile_canvas
#               (DragStarter), app.data.activity_log, QSS selectors #tool_tray_panel, #tray_grip_label,
#               #tray_tool_btn, #tray_empty_label, #tool_dock_panel, #drop_overlay_panel
#   - Exposes: ToolDock(parent) with center_layout, bind(content_widget), add_tray(side), trays, can_hold_tools;
#              ToolTray; SIDES, TRAY_SIZES, TOOL_MIME, TOOLBARS_SETTING
# NOTES:
#   - [2026-11-16] Created: toolbars in any module (module tile menu → Add toolbar), global + module tools.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - tray: One toolbar. side: "top" | "bottom" | "left" | "right" | "float".
# - pinned: The tray can't be moved and its tools can't be dragged or removed (tools still run).
# - TOOLBARS_SETTING (str): Module setting with the trays: [{side, tools, icon_px, is_pinned, x, y}]
#   (x / y: a floating tray's top-left as fractions of the dock's size).
# - SNAP_PX (int | px): Dropping a tray this close to an edge docks it there (default: 56).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SHARED
#    1.1 imports
# 2. TRAY
#    2.1 tool_tray
#    2.2 tray_drag_and_drop
#    2.3 tray_menus
# 3. DOCK
#    3.1 tool_dock
#    3.2 tray_moves
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

from PySide6.QtCore import QByteArray, QEvent, QMimeData, QObject, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QBoxLayout, QFrame, QGridLayout, QLabel, QMenu, QToolButton, QVBoxLayout, QHBoxLayout, QWidget

from app.data.activity_log import get_activity_log
from app.ui.canvas.tile_canvas import DragStarter
from app.ui.tool_bag import ToolBagPopup, get_tool, run_tool

SIDES = ("top", "bottom", "left", "right", "float")
SIDE_TITLES = {"top": "Top", "bottom": "Bottom", "left": "Left", "right": "Right", "float": "Floating"}
TRAY_SIZES = {"Small": 18, "Medium": 24, "Large": 32}
TOOL_MIME = "application/x-devos-tool"
TOOLBARS_SETTING = "toolbars"
SNAP_PX = 56
# --- END [1.1] ---


# ==============================================================================
# [2.1] tool_tray
# ==============================================================================
class ToolTray(QFrame):
    changed = Signal()

    def __init__(self, dock: "ToolDock", state: dict) -> None:
        super().__init__(objectName="tool_tray_panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.dock = dock
        self.side = state.get("side", "top") if state.get("side") in SIDES else "top"
        self.tool_ids: list[str] = [str(tool_id) for tool_id in state.get("tools", [])]
        self.icon_px = int(state.get("icon_px", TRAY_SIZES["Medium"]))
        self.is_pinned = bool(state.get("is_pinned", False))
        self.float_position = (float(state.get("x", 0.3)), float(state.get("y", 0.3)))
        self.setAcceptDrops(True)
        self.tray_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.tray_layout.setContentsMargins(2, 2, 2, 2)
        self.tray_layout.setSpacing(1)
        self.tray_grip_label = QLabel("⠿", objectName="tray_grip_label", toolTip="Drag to dock on another edge or float")
        self.tray_grip_label.setCursor(Qt.CursorShape.OpenHandCursor)
        self.tray_grip_label.installEventFilter(self)
        self._grip_press: QPoint | None = None
        self.buttons: list[QToolButton] = []
        self.rebuild()

    def state(self) -> dict:
        return {"side": self.side, "tools": list(self.tool_ids), "icon_px": self.icon_px, "is_pinned": self.is_pinned,
                "x": round(self.float_position[0], 4), "y": round(self.float_position[1], 4)}

    def is_vertical(self) -> bool:
        return self.side in ("left", "right")

    def rebuild(self) -> None:
        while self.tray_layout.count():
            widget = self.tray_layout.takeAt(0).widget()
            if widget is not None and widget is not self.tray_grip_label:
                widget.hide()                            # deleteLater is deferred: don't leave it drawn meanwhile
                widget.deleteLater()
        self.tray_layout.setDirection(QBoxLayout.Direction.TopToBottom if self.is_vertical() else QBoxLayout.Direction.LeftToRight)
        self.tray_grip_label.setVisible(not self.is_pinned)
        self.tray_layout.addWidget(self.tray_grip_label, 0, Qt.AlignmentFlag.AlignCenter)
        self.buttons = []
        for index, tool_id in enumerate(self.tool_ids):
            self.buttons.append(self._tool_button(index, tool_id))
            self.tray_layout.addWidget(self.buttons[-1])
        if not self.tool_ids:
            hint = "Right-click\nfor tools" if self.is_vertical() else "Right-click to add tools"
            self.tray_layout.addWidget(QLabel(hint, objectName="tray_empty_label"))
        if self.side != "float":
            self.tray_layout.addStretch()
        side_px = self.icon_px + 10
        if self.is_vertical():
            self.setFixedWidth(max(side_px, 30 if not self.tool_ids else 0) + 4)
            self.setMaximumHeight(16777215)
            self.setMinimumHeight(0)
        else:
            self.setFixedHeight(side_px + 4)
            self.setMaximumWidth(16777215)
            self.setMinimumWidth(0)
        if self.side == "float":
            self.adjustSize()

    def _tool_button(self, index: int, tool_id: str) -> QToolButton:
        spec = get_tool(tool_id)
        button = QToolButton(objectName="tray_tool_btn", autoRaise=True)
        button.setFixedSize(QSize(self.icon_px + 8, self.icon_px + 8))
        font = button.font()
        font.setPixelSize(max(10, int(self.icon_px * 0.72)))
        button.setFont(font)
        if spec is None:
            button.setText("?")
            button.setEnabled(False)
            button.setToolTip(f"“{tool_id}” isn't available here")
        else:
            button.setText(spec.glyph)
            button.setToolTip(f"{spec.title}\n{spec.description}")
            button.clicked.connect(lambda _checked=False, spec=spec: run_tool(spec, self.dock.module_view))
        button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        button.customContextMenuRequested.connect(lambda _pos, index=index, button=button: self._tool_menu(index, button))
        if not self.is_pinned:
            DragStarter(button, lambda index=index, button=button: self._drag_tool(index, button))
        return button
# --- END [2.1] ---

# ==============================================================================
# [2.2] tray_drag_and_drop
# ==============================================================================
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """The grip: press + drag moves the tray (the dock shows where it will snap)."""
        if watched is not self.tray_grip_label or self.is_pinned:
            return False
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._grip_press = event.globalPosition().toPoint()
            self.tray_grip_label.setCursor(Qt.CursorShape.ClosedHandCursor)
            return True
        if event.type() == QEvent.Type.MouseMove and self._grip_press is not None:
            self.dock.preview_move(event.globalPosition().toPoint())
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and self._grip_press is not None:
            moved = (event.globalPosition().toPoint() - self._grip_press).manhattanLength() > 4
            self._grip_press = None
            self.tray_grip_label.setCursor(Qt.CursorShape.OpenHandCursor)
            if moved:
                self.dock.finish_move(self, event.globalPosition().toPoint())
            else:
                self.dock.hide_preview()
            return True
        return False

    def _drag_tool(self, index: int, button: QToolButton) -> None:
        mime = QMimeData()
        mime.setData(TOOL_MIME, QByteArray(f"{self.dock.dock_id}|{id(self)}|{index}".encode()))
        drag = QDrag(button)
        drag.setMimeData(mime)
        drag.setPixmap(button.grab())
        drag.exec(Qt.DropAction.MoveAction)

    def _drop_source(self, event) -> tuple["ToolTray", int] | None:
        if not event.mimeData().hasFormat(TOOL_MIME) or self.is_pinned:
            return None
        dock_id, tray_key, index = bytes(event.mimeData().data(TOOL_MIME)).decode().split("|")
        if dock_id != self.dock.dock_id:
            return None                                   # tools move between trays of the same module only
        source = next((tray for tray in self.dock.trays if str(id(tray)) == tray_key), None)
        return (source, int(index)) if source is not None else None

    def dragEnterEvent(self, event) -> None:                             # noqa: N802
        event.acceptProposedAction() if self._drop_source(event) else event.ignore()

    def dragMoveEvent(self, event) -> None:                              # noqa: N802
        event.acceptProposedAction() if self._drop_source(event) else event.ignore()

    def dropEvent(self, event) -> None:                                  # noqa: N802
        found = self._drop_source(event)
        if found is None:
            return
        event.acceptProposedAction()
        source, index = found
        target_index = self._index_at(event.position().toPoint())
        tool_id = source.tool_ids[index]
        if source is self and target_index > index:
            target_index -= 1
        source.tool_ids.pop(index)
        self.tool_ids.insert(min(target_index, len(self.tool_ids)), tool_id)
        from PySide6.QtCore import QTimer                  # rebuild after the drag loop releases the button
        QTimer.singleShot(0, lambda: (source.rebuild(), self.rebuild(), self.changed.emit()))

    def _index_at(self, point: QPoint) -> int:
        for index, button in enumerate(self.buttons):
            centre = button.geometry().center()
            if (point.y() < centre.y()) if self.is_vertical() else (point.x() < centre.x()):
                return index
        return len(self.buttons)
# --- END [2.2] ---

# ==============================================================================
# [2.3] tray_menus
# ==============================================================================
    def contextMenuEvent(self, event) -> None:                          # noqa: N802
        self.show_menu(event.globalPos())

    def mouseDoubleClickEvent(self, event) -> None:                     # noqa: N802
        self.open_tool_bag(event.globalPosition().toPoint())

    def show_menu(self, global_position: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction("Add tools…").triggered.connect(lambda: self.open_tool_bag(global_position))
        dock_menu = menu.addMenu("Dock")
        for side in SIDES:
            action = dock_menu.addAction(SIDE_TITLES[side])
            action.setCheckable(True)
            action.setChecked(side == self.side)
            action.setEnabled(not self.is_pinned)
            action.triggered.connect(lambda _checked=False, side=side: self.dock.move_tray(self, side))
        size_menu = menu.addMenu("Size")
        for title, pixels in TRAY_SIZES.items():
            action = size_menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(pixels == self.icon_px)
            action.triggered.connect(lambda _checked=False, pixels=pixels: self.set_icon_px(pixels))
        pin_action = menu.addAction("Pinned in place")
        pin_action.setCheckable(True)
        pin_action.setChecked(self.is_pinned)
        pin_action.triggered.connect(self.set_pinned)
        menu.addSeparator()
        menu.addAction("Remove toolbar").triggered.connect(lambda: self.dock.remove_tray(self))
        menu.exec(global_position)

    def _tool_menu(self, index: int, button: QToolButton) -> None:
        menu = QMenu(self)
        back, forward = ("Move up", "Move down") if self.is_vertical() else ("Move left", "Move right")
        for title, step in ((back, -1), (forward, 1)):
            action = menu.addAction(title)
            action.setEnabled(not self.is_pinned and 0 <= index + step < len(self.tool_ids))
            action.triggered.connect(lambda _checked=False, step=step: self._move_tool(index, step))
        remove_action = menu.addAction("Remove from toolbar")
        remove_action.setEnabled(not self.is_pinned)
        remove_action.triggered.connect(lambda: self._remove_tool(index))
        menu.addSeparator()
        menu.addAction("Toolbar options…").triggered.connect(lambda: self.show_menu(button.mapToGlobal(button.rect().bottomLeft())))
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def open_tool_bag(self, global_position: QPoint) -> None:
        popup = ToolBagPopup(self.dock.module_view, self.add_tool, "add", set(self.tool_ids), self)
        popup.show_at(global_position)

    def add_tool(self, spec) -> None:
        if spec.tool_id not in self.tool_ids:
            self.tool_ids.append(spec.tool_id)
            self.rebuild()
            self.changed.emit()

    def _move_tool(self, index: int, step: int) -> None:
        self.tool_ids.insert(index + step, self.tool_ids.pop(index))
        self.rebuild()
        self.changed.emit()

    def _remove_tool(self, index: int) -> None:
        self.tool_ids.pop(index)
        self.rebuild()
        self.changed.emit()

    def set_icon_px(self, pixels: int) -> None:
        self.icon_px = pixels
        self.rebuild()
        self.dock.place_floating()
        self.changed.emit()

    def set_pinned(self, is_pinned: bool) -> None:
        self.is_pinned = is_pinned
        self.rebuild()
        self.changed.emit()
# --- END [2.3] ---


# ==============================================================================
# [3.1] tool_dock
# ==============================================================================
class ToolDock(QWidget):
    """A module tile's content area with room for toolbars on every edge (and floating on top)."""

    _next_id = 0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="tool_dock_panel")
        ToolDock._next_id += 1
        self.dock_id = f"dock{ToolDock._next_id}"
        self.module_view: QWidget | None = None
        self.settings = None
        self.trays: list[ToolTray] = []
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        self.edge_layouts: dict[str, QBoxLayout] = {}
        for side, (row, column) in {"top": (0, 1), "bottom": (2, 1), "left": (1, 0), "right": (1, 2)}.items():
            edge = QWidget()
            edge_layout = QVBoxLayout(edge) if side in ("top", "bottom") else QHBoxLayout(edge)
            edge_layout.setContentsMargins(0, 0, 0, 0)
            edge_layout.setSpacing(1)
            grid.addWidget(edge, row, column)
            self.edge_layouts[side] = edge_layout
        center = QWidget()
        self.center_layout = QVBoxLayout(center)
        self.center_layout.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(center, 1, 1)
        grid.setRowStretch(1, 1)
        grid.setColumnStretch(1, 1)
        self.drop_overlay_panel = QWidget(self, objectName="drop_overlay_panel")
        self.drop_overlay_panel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.drop_overlay_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.drop_overlay_panel.hide()

    @property
    def can_hold_tools(self) -> bool:
        return self.settings is not None

    def bind(self, content_widget: QWidget) -> None:
        """Attach to a module view: its settings keep the trays (no settings = no toolbars)."""
        for tray in list(self.trays):
            tray.deleteLater()
        self.trays = []
        context = getattr(content_widget, "context", None)
        self.module_view = content_widget if context is not None else None
        self.settings = context.settings if context is not None else None
        if self.settings is None:
            return
        for state in self.settings.get(TOOLBARS_SETTING, []) or []:
            self._insert(ToolTray(self, state))
        self.place_floating()

    def add_tray(self, side: str = "top") -> ToolTray:
        tray = ToolTray(self, {"side": side})
        self._insert(tray)
        self.place_floating()
        self.save()
        get_activity_log().info("tools", f"Added a toolbar ({SIDE_TITLES[side].lower()}) — right-click it to add tools")
        return tray

    def remove_tray(self, tray: ToolTray) -> None:
        self.trays.remove(tray)
        tray.deleteLater()
        self.save()

    def _insert(self, tray: ToolTray) -> None:
        tray.changed.connect(self.save)
        self.trays.append(tray)
        self._attach(tray)

    def _attach(self, tray: ToolTray) -> None:
        if tray.side == "float":
            tray.setParent(self)
            tray.show()
            tray.raise_()
        else:
            self.edge_layouts[tray.side].addWidget(tray)
            tray.show()

    def save(self) -> None:
        if self.settings is not None:
            self.settings.set(TOOLBARS_SETTING, [tray.state() for tray in self.trays])
# --- END [3.1] ---

# ==============================================================================
# [3.2] tray_moves
# ==============================================================================
    def _target(self, global_position: QPoint) -> tuple[str, QPoint]:
        point = self.mapFromGlobal(global_position)
        distances = {"top": point.y(), "bottom": self.height() - point.y(), "left": point.x(), "right": self.width() - point.x()}
        side = min(distances, key=distances.get)
        return (side if distances[side] <= SNAP_PX else "float"), point

    def preview_move(self, global_position: QPoint) -> None:
        side, point = self._target(global_position)
        rects = {"top": QRect(0, 0, self.width(), 32), "bottom": QRect(0, self.height() - 32, self.width(), 32),
                 "left": QRect(0, 0, 32, self.height()), "right": QRect(self.width() - 32, 0, 32, self.height()),
                 "float": QRect(point.x() - 6, point.y() - 14, 160, 30)}
        self.drop_overlay_panel.setGeometry(rects[side])
        self.drop_overlay_panel.show()
        self.drop_overlay_panel.raise_()

    def hide_preview(self) -> None:
        self.drop_overlay_panel.hide()

    def finish_move(self, tray: ToolTray, global_position: QPoint) -> None:
        self.hide_preview()
        side, point = self._target(global_position)
        position = (max(0.0, min(0.95, (point.x() - 6) / max(1, self.width()))),
                    max(0.0, min(0.95, (point.y() - 14) / max(1, self.height()))))
        self.move_tray(tray, side, position)

    def move_tray(self, tray: ToolTray, side: str, float_position: tuple[float, float] | None = None) -> None:
        if tray.side != "float":
            self.edge_layouts[tray.side].removeWidget(tray)
        tray.side = side
        if float_position is not None:
            tray.float_position = float_position
        tray.rebuild()
        self._attach(tray)
        self.place_floating()
        self.save()

    def place_floating(self) -> None:
        for tray in self.trays:
            if tray.side == "float":
                tray.adjustSize()
                x = int(tray.float_position[0] * self.width())
                y = int(tray.float_position[1] * self.height())
                tray.move(max(0, min(x, self.width() - tray.width())), max(0, min(y, self.height() - tray.height())))
                tray.raise_()

    def resizeEvent(self, event) -> None:                               # noqa: N802
        super().resizeEvent(event)
        self.place_floating()
# --- END [3.2] ---
