# ==============================================================================
# FILE: app/ui/widgets/title_bar.py
# OVERVIEW: Pieces that let DevOS draw its own integrated title bar in a frameless window.
# PURPOSE:
#   1. WindowControls: minimise / maximise-restore / close buttons for the top-right corner.
#   2. WindowFrame: the window's outer container; its thin edge resizes the window (cursor changes
#      on hover, press starts a system resize). The edge disappears while maximised.
#   3. TitleBarDragFilter: a press handler on the window mouse hub (window_events.py) that turns presses on
#      empty title-bar space (sidebar header, gaps in the top tab rows) into a system window move;
#      double-click toggles maximise.
# CONTRACT:
#   - Requires: app.ui.widgets.sidebar (ICON_SIZE, load_icon), app.ui.window_events.mouse_hub, assets/icons/window_*.svg,
#               QSS selectors #window_controls_panel, #window_minimize_btn, #window_maximize_btn,
#               #window_close_btn, #window_frame_panel
#   - Exposes: WindowControls(window), WindowFrame(window, content), TitleBarDragFilter(window),
#              TITLE_BAR_REGION_PROPERTY, mark_title_bar_region(widget)
# NOTES:
#   - [2026-11-17] TitleBarDragFilter leaves the mouse hub when its window is deleted (DevOS has several windows).
#   - [2026-11-16] TitleBarDragFilter is no longer installed on the QApplication (PySide crashed on Qt
#                  WebEngine's internal objects); it registers with mouse_hub() and sees the window's presses.
#   - [2026-11-15] Created. Moving/resizing uses QWindow.startSystemMove()/startSystemResize(), so the
#                  compositor does the work: works on Wayland (KDE Plasma) as well as X11, including
#                  snapping and drag-to-unmaximise.
#   - [2026-11-15] Only regions touching the top of the window drag it (a tab row lower down in a
#                  vertical split is not a title bar).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - RESIZE_BORDER_PX (int | px): Width of the resize edge around the window (default: 4).
# - title-bar region: A widget marked with mark_title_bar_region(); empty presses on it move the window.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. WINDOW_CONTROLS
#    2.1 window_controls
# 3. WINDOW_FRAME
#    3.1 window_frame
# 4. DRAG_FILTER
#    4.1 title_bar_drag_filter
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractSpinBox, QComboBox, QHBoxLayout, QLineEdit, QTabBar, QToolButton, QVBoxLayout, QWidget,
)

from app.ui.theme import TITLE_BAR_HEIGHT_PX
from app.ui.widgets.sidebar import ICON_SIZE, load_icon
from app.ui.window_events import mouse_hub

TITLE_BAR_REGION_PROPERTY = "is_title_bar_region"
RESIZE_BORDER_PX = 4


def mark_title_bar_region(widget: QWidget) -> None:
    """Empty presses on this widget (not on its buttons/tabs) move the window."""
    widget.setProperty(TITLE_BAR_REGION_PROPERTY, True)
# --- END [1.1] ---


# ==============================================================================
# [2.1] window_controls
# ==============================================================================
class WindowControls(QWidget):
    """Minimise, maximise/restore and close, styled to sit inside a tab row."""

    BUTTON_WIDTH_PX = 44

    def __init__(self, window: QWidget) -> None:
        super().__init__(objectName="window_controls_panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)   # for the divider (QSS border-left)
        self.window_ = window
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.window_minimize_btn = self._button("window_minimize_btn", "window_minimize.svg", "Minimise")
        self.window_maximize_btn = self._button("window_maximize_btn", "window_maximize.svg", "Maximise")
        self.window_close_btn = self._button("window_close_btn", "window_close.svg", "Close")
        self.window_minimize_btn.clicked.connect(window.showMinimized)
        self.window_maximize_btn.clicked.connect(self.toggle_maximized)
        self.window_close_btn.clicked.connect(window.close)
        for button in (self.window_minimize_btn, self.window_maximize_btn, self.window_close_btn):
            layout.addWidget(button)

    def _button(self, object_name: str, icon_file: str, tool_tip: str) -> QToolButton:
        button = QToolButton(objectName=object_name, toolTip=tool_tip, autoRaise=True)
        button.setIcon(load_icon(icon_file))
        button.setIconSize(ICON_SIZE)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setFixedSize(self.BUTTON_WIDTH_PX, TITLE_BAR_HEIGHT_PX - 1)   # full bar height (minus its border)
        return button

    def toggle_maximized(self) -> None:
        if self.window_.isMaximized():
            self.window_.showNormal()
        else:
            self.window_.showMaximized()

    def sync_state(self) -> None:
        """Call after the window's state changes: swaps the maximise / restore icon."""
        is_maximized = self.window_.isMaximized()
        self.window_maximize_btn.setIcon(load_icon("window_restore.svg" if is_maximized else "window_maximize.svg"))
        self.window_maximize_btn.setToolTip("Restore" if is_maximized else "Maximise")
# --- END [2.1] ---


# ==============================================================================
# [3.1] window_frame
# ==============================================================================
class WindowFrame(QWidget):
    """Outer container of a frameless window: a thin edge that resizes the window."""

    EDGE_CURSORS = {
        Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor, Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
        Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor, Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
    }

    def __init__(self, window: QWidget, content: QWidget) -> None:
        super().__init__(objectName="window_frame_panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMouseTracking(True)                   # hover cursor on the edge without a button held
        self.window_ = window
        self.frame_layout = QVBoxLayout(self)
        self.frame_layout.setSpacing(0)
        self.frame_layout.addWidget(content)
        self.set_edge_visible(True)

    def set_edge_visible(self, is_visible: bool) -> None:
        """No resize edge while maximised / full screen."""
        border = RESIZE_BORDER_PX if is_visible else 0
        self.frame_layout.setContentsMargins(border, border, border, border)

    def _edges_at(self, position: QPoint) -> Qt.Edge:
        edges = Qt.Edge(0)
        corner = RESIZE_BORDER_PX * 4                  # corners are easier to grab than the bare edge
        if position.x() <= RESIZE_BORDER_PX or (position.x() <= corner and self._near_vertical_edge(position)):
            edges |= Qt.Edge.LeftEdge
        if position.x() >= self.width() - RESIZE_BORDER_PX or (position.x() >= self.width() - corner and self._near_vertical_edge(position)):
            edges |= Qt.Edge.RightEdge
        if position.y() <= RESIZE_BORDER_PX or (position.y() <= corner and self._near_horizontal_edge(position)):
            edges |= Qt.Edge.TopEdge
        if position.y() >= self.height() - RESIZE_BORDER_PX or (position.y() >= self.height() - corner and self._near_horizontal_edge(position)):
            edges |= Qt.Edge.BottomEdge
        return edges

    def _near_vertical_edge(self, position: QPoint) -> bool:
        return position.y() <= RESIZE_BORDER_PX or position.y() >= self.height() - RESIZE_BORDER_PX

    def _near_horizontal_edge(self, position: QPoint) -> bool:
        return position.x() <= RESIZE_BORDER_PX or position.x() >= self.width() - RESIZE_BORDER_PX

    def _cursor_for(self, edges: Qt.Edge) -> Qt.CursorShape | None:
        left, right = bool(edges & Qt.Edge.LeftEdge), bool(edges & Qt.Edge.RightEdge)
        top, bottom = bool(edges & Qt.Edge.TopEdge), bool(edges & Qt.Edge.BottomEdge)
        if (left and top) or (right and bottom):
            return Qt.CursorShape.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return Qt.CursorShape.SizeBDiagCursor
        if left or right:
            return Qt.CursorShape.SizeHorCursor
        if top or bottom:
            return Qt.CursorShape.SizeVerCursor
        return None

    def mouseMoveEvent(self, event) -> None:
        cursor = self._cursor_for(self._edges_at(event.position().toPoint())) if not self.window_.isMaximized() else None
        if cursor is None:
            self.unsetCursor()
        else:
            self.setCursor(cursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self.unsetCursor()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        edges = self._edges_at(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and edges and not self.window_.isMaximized():
            self.window_.windowHandle().startSystemResize(edges)
            event.accept()
            return
        super().mousePressEvent(event)
# --- END [3.1] ---


# ==============================================================================
# [4.1] title_bar_drag_filter
# ==============================================================================
class TitleBarDragFilter(QObject):
    """Empty presses on title-bar regions move / maximise the window (a mouse_hub() press handler)."""

    INTERACTIVE_TYPES = (QAbstractButton, QComboBox, QLineEdit, QAbstractSpinBox)
    TOP_TOLERANCE_PX = RESIZE_BORDER_PX + 2

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.window_ = window
        handler = self.handle_press
        mouse_hub().add_press_handler(handler)
        window.destroyed.connect(lambda *_: mouse_hub().remove_press_handler(handler))

    def _title_bar_region(self, widget: QWidget, local_position: QPoint) -> QWidget | None:
        """The marked region if this press is on empty title-bar space, else None."""
        if isinstance(widget, self.INTERACTIVE_TYPES):
            return None
        if isinstance(widget, QTabBar) and widget.tabAt(local_position) >= 0:
            return None                                # on a tab: let the tab bar handle it
        region = widget
        while region is not None and region is not self.window_:
            if region.property(TITLE_BAR_REGION_PROPERTY):
                is_at_top = region.mapTo(self.window_, QPoint(0, 0)).y() <= self.TOP_TOLERANCE_PX
                return region if is_at_top else None
            if isinstance(region, self.INTERACTIVE_TYPES):
                return None
            region = region.parentWidget()
        return None

    def handle_press(self, target: QWidget, local_position: QPoint, event: QEvent) -> bool:
        """True (press consumed) when it was on empty title-bar space of this window."""
        event_type = event.type()
        if target.window() is not self.window_ or event.button() != Qt.MouseButton.LeftButton:
            return False
        if self._title_bar_region(target, local_position) is None:
            return False
        if event_type == QEvent.Type.MouseButtonDblClick:
            if self.window_.isMaximized():
                self.window_.showNormal()
            else:
                self.window_.showMaximized()
        else:
            self.window_.windowHandle().startSystemMove()
        return True
# --- END [4.1] ---
