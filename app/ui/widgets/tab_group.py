# ==============================================================================
# FILE: app/ui/widgets/tab_group.py
# OVERVIEW: One pane of tabs: a tab row on top and the selected tab's page underneath.
# PURPOSE:
#   1. Shows a row of tabs (drag to reorder, x / middle-click to close) and the selected page.
#   2. Lets a tab be dragged out of the row and dropped onto any pane (including this one).
#   3. Accepts dropped tabs and reports where they landed (tab row, centre, or one of four edges);
#      previews the landing spot while a tab hovers.
#   4. Offers a "split" button (new pane to the right) and a "close pane" button.
# CONTRACT:
#   - Requires: app.ui.widgets.sidebar (ICON_SIZE, load_icon), app.ui.widgets.tiling_layout
#               (drop zones, edge_zone_at, zone_preview_rect), assets/icons/tab_close.svg,
#               assets/icons/split_pane.svg, QSS selectors #tab_group_panel, #tab_bar_panel,
#               #page_tab_bar, #tab_close_btn, #split_pane_btn, #close_pane_btn, #empty_tab_label,
#               #drop_overlay_panel in app/ui/theme.py
#   - Exposes: TabGroup (QWidget) with signals current_page_changed(int), tab_close_requested(int),
#              page_dropped(int, str, int), split_requested(), close_pane_requested(), drag_started(),
#              drag_finished(); add_page(); remove_page(); select_page(); has_page(); page_indices();
#              current_page_index(); step_tab(); tab_count(); set_empty_hint(); set_is_active();
#              set_can_close_pane(); add_leading_widget(); PageTabBar; PAGE_MIME_TYPE; NO_PAGE_INDEX;
#              DROP_ZONE_* and EDGE_DROP_ZONES
# NOTES:
#   - [2026-11-15] Created when TabArea became a multi-pane workspace. A TabGroup never decides
#                  what happens to a page; it reports clicks, closes and drops, and TabArea acts.
#   - [2026-11-15] Dragging a tab out of its row first ends Qt's own tab-move (fake release),
#                  then starts a QDrag carrying only the page_index (PAGE_MIME_TYPE).
#   - [2026-11-15] Drop zones: the outer EDGE_ZONE_FRACTION of the page area on each side means
#                  "split here"; the overlay previews the half the new pane will take.
#   - [2026-11-15] Edge-zone maths and the edge DROP_ZONE_* constants moved to tiling_layout.py
#                  (re-exported here); only DROP_ZONE_TABS is specific to tab panes.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - drop zone (str): Where a tab was dropped: DROP_ZONE_TABS, _CENTER, _LEFT, _RIGHT, _TOP or _BOTTOM.
# - EDGE_ZONE_FRACTION (float): Share of the page area, from each edge, that counts as that edge (default: 0.25).
# - DRAG_OUT_MARGIN_PX (int | px): How far past the tab row the mouse must go before a tab drag
#   turns into a drag-and-drop between panes (default: 12).
# - insert_index (int): Tab position a dropped page should take; -1 = after the current tab.
# - NO_PAGE_INDEX (int): Sent by current_page_changed when the pane has no tabs (value: -1).
# - page_index (int): Position of a page in MainWindow.page_entries; stored as each tab's data.
# - PAGE_MIME_TYPE (str): Drag-and-drop format for a tab; its payload is the page_index as text.
# - pane: One TabGroup inside the TabArea workspace.
# - tab_index (int): Position of a tab in this pane's tab row (changes when tabs are dragged).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. DESIGN_NOTES
#    1.1 design_notes_docstring
# 2. IMPORTS_AND_CONSTANTS
#    2.1 imports
#    2.2 constants
# 3. PAGE_TAB_BAR
#    3.1 class_signals_and_constants
#    3.2 mouse_events
#    3.3 start_drag
# 4. TAB_GROUP
#    4.1 class_signals_and_constants
#    4.2 init_tab_group
#    4.3 build_tab_row
#    4.4 build_page_area
#    4.5 page_api
#    4.6 pane_state
#    4.7 drop_events
#    4.8 helpers
# ==============================================================================

# ==============================================================================
# [1.1] design_notes_docstring
# ==============================================================================
"""One pane of tabs.

Layout:
    [leading][ Home  x ][ Market Lab  x ]          [split][close pane]   <- tab row
    +----------------------------------------------------------------+
    |  page of the selected tab, or a hint when the pane is empty     |
    +----------------------------------------------------------------+

Dragging:
    - Inside the tab row, Qt reorders tabs as usual.
    - Once the mouse leaves the row, the tab becomes a drag-and-drop item that any pane
      (this one included) accepts:
          +-------------------------+
          |          top            |     tab row -> insert at that spot
          |  left   centre   right  |     centre  -> add as a tab
          |         bottom          |     edge    -> split the pane; new pane on that side
          +-------------------------+
"""
# --- END [1.1] ---

# ==============================================================================
# [2.1] imports
# ==============================================================================
from PySide6.QtCore import QByteArray, QEvent, QMimeData, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QMouseEvent
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QStackedWidget, QTabBar, QToolButton, QVBoxLayout, QWidget,
)

from app.ui.widgets.sidebar import ICON_SIZE, load_icon
from app.ui.widgets.tiling_layout import (  # noqa: F401  (edge zones re-exported for callers)
    DROP_ZONE_BOTTOM, DROP_ZONE_CENTER, DROP_ZONE_LEFT, DROP_ZONE_RIGHT, DROP_ZONE_TOP,
    EDGE_DROP_ZONES, EDGE_ZONE_FRACTION, edge_zone_at, zone_preview_rect,
)
# --- END [2.1] ---

# ==============================================================================
# [2.2] constants
# ==============================================================================
PAGE_MIME_TYPE = "application/x-devos-page"
NO_PAGE_INDEX = -1

# Where a dragged tab was dropped on a pane (see [4.7]).
# Centre = add as a tab; edges = split the pane (constants from tiling_layout.py).
DROP_ZONE_TABS = "tabs"       # on the tab row: insert at that spot
# --- END [2.2] ---


# ==============================================================================
# [3.1] class_signals_and_constants
# ==============================================================================
class PageTabBar(QTabBar):
    """QTabBar that can drag a tab out to another pane, and closes tabs on middle-click."""

    tab_middle_clicked = Signal(int)    # tab_index
    drag_started = Signal()
    drag_finished = Signal()

    DRAG_OUT_MARGIN_PX = 12

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page_tab_bar")
        self._pressed_tab_index = -1
# --- END [3.1] ---

# ==============================================================================
# [3.2] mouse_events
# ==============================================================================
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed_tab_index = self.tabAt(event.position().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._pressed_tab_index >= 0 and event.buttons() & Qt.MouseButton.LeftButton:
            margin_px = self.DRAG_OUT_MARGIN_PX
            drag_zone = self.rect().adjusted(-margin_px, -margin_px, margin_px, margin_px)
            if not drag_zone.contains(event.position().toPoint()):
                self._start_drag(event)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._pressed_tab_index = -1
        if event.button() == Qt.MouseButton.MiddleButton:
            tab_index = self.tabAt(event.position().toPoint())
            if tab_index >= 0:
                self.tab_middle_clicked.emit(tab_index)
            return
        super().mouseReleaseEvent(event)
# --- END [3.2] ---

# ==============================================================================
# [3.3] start_drag
# ==============================================================================
    def _start_drag(self, event: QMouseEvent) -> None:
        page_index = self.tabData(self._pressed_tab_index)
        self._pressed_tab_index = -1

        # End Qt's own tab-move first, otherwise the tab stays "lifted" in this row.
        fake_release = QMouseEvent(
            QEvent.Type.MouseButtonRelease, event.position(), event.globalPosition(),
            Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, event.modifiers(),
        )
        super().mouseReleaseEvent(fake_release)

        mime_data = QMimeData()
        mime_data.setData(PAGE_MIME_TYPE, QByteArray(str(page_index).encode()))
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        for tab_index in range(self.count()):          # the tab may have moved during the reorder
            if self.tabData(tab_index) == page_index:
                drag.setPixmap(self.grab(self.tabRect(tab_index)))
                drag.setHotSpot(QPoint(20, 10))
                break

        self.drag_started.emit()
        drag.exec(Qt.DropAction.MoveAction)             # blocks until the tab is dropped or cancelled
        self.drag_finished.emit()
# --- END [3.3] ---


# ==============================================================================
# [4.1] class_signals_and_constants
# ==============================================================================
class TabGroup(QWidget):
    current_page_changed = Signal(int)   # page_index of this pane's selected tab, or NO_PAGE_INDEX
    tab_close_requested = Signal(int)    # page_index
    page_dropped = Signal(int, str, int) # page_index, drop zone, insert_index (-1 = after the current tab)
    split_requested = Signal()
    close_pane_requested = Signal()
    drag_started = Signal()
    drag_finished = Signal()

    MIN_WIDTH_PX = 200
    MIN_HEIGHT_PX = 120
    CLOSE_ICON_SIZE_PX = 12
# --- END [4.1] ---

# ==============================================================================
# [4.2] init_tab_group
# ==============================================================================
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tab_group_panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumSize(self.MIN_WIDTH_PX, self.MIN_HEIGHT_PX)
        self.setAcceptDrops(True)
        self._page_widgets: dict[int, QWidget] = {}        # page_index -> widget, for this pane's tabs

        self.tab_group_layout = QVBoxLayout(self)
        self.tab_group_layout.setContentsMargins(0, 0, 0, 0)
        self.tab_group_layout.setSpacing(0)

        self._build_tab_row()
        self._build_page_area()
# --- END [4.2] ---

# ==============================================================================
# [4.3] build_tab_row
# ==============================================================================
    def _build_tab_row(self) -> None:
        # --- Row: leading widgets, tabs, then the pane buttons on the right ---
        self.tab_bar_panel = QWidget(objectName="tab_bar_panel")
        self.tab_bar_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.tab_row_layout = QHBoxLayout(self.tab_bar_panel)
        self.tab_row_layout.setContentsMargins(4, 0, 4, 0)
        self.tab_row_layout.setSpacing(4)

        self.page_tab_bar = PageTabBar()
        self.page_tab_bar.setMovable(True)                 # drag to reorder inside the row
        self.page_tab_bar.setExpanding(False)              # tabs keep their natural width
        self.page_tab_bar.setDrawBase(False)
        self.page_tab_bar.setElideMode(Qt.TextElideMode.ElideRight)
        self.page_tab_bar.setUsesScrollButtons(True)
        self.page_tab_bar.setIconSize(ICON_SIZE)
        self.page_tab_bar.currentChanged.connect(self._on_current_tab_changed)
        self.page_tab_bar.tab_middle_clicked.connect(self._request_close_at)
        self.page_tab_bar.drag_started.connect(self.drag_started)
        self.page_tab_bar.drag_finished.connect(self.drag_finished)

        self.split_pane_btn = self._build_row_btn("split_pane_btn", "split_pane.svg", "Split right")
        self.split_pane_btn.clicked.connect(self.split_requested)
        self.close_pane_btn = self._build_row_btn("close_pane_btn", "tab_close.svg", "Close this pane")
        self.close_pane_btn.clicked.connect(self.close_pane_requested)
        self.close_pane_btn.hide()                         # only shown while there are 2+ panes

        self.tab_row_layout.addWidget(self.page_tab_bar, 1, Qt.AlignmentFlag.AlignBottom)
        self.tab_row_layout.addWidget(self.split_pane_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.tab_row_layout.addWidget(self.close_pane_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.tab_group_layout.addWidget(self.tab_bar_panel)

    def add_leading_widget(self, widget: QWidget) -> None:
        """Place a widget at the far left of the tab row (e.g. the open-sidebar button)."""
        self.tab_row_layout.insertWidget(0, widget, 0, Qt.AlignmentFlag.AlignVCenter)
# --- END [4.3] ---

# ==============================================================================
# [4.4] build_page_area
# ==============================================================================
    def _build_page_area(self) -> None:
        # --- Pages of this pane's tabs; index 0 is the empty-pane hint ---
        self.page_stack = QStackedWidget()
        self.empty_tab_label = QLabel(objectName="empty_tab_label")
        self.empty_tab_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_stack.addWidget(self.empty_tab_label)
        self.tab_group_layout.addWidget(self.page_stack, 1)

        # --- Drop highlight: drawn over the page area while a tab hovers this pane ---
        self.drop_overlay_panel = QWidget(self, objectName="drop_overlay_panel")
        self.drop_overlay_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.drop_overlay_panel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.drop_overlay_panel.hide()
# --- END [4.4] ---

# ==============================================================================
# [4.5] page_api
# ==============================================================================
    def add_page(
        self, page_index: int, title_text: str, icon_file_name: str, page_widget: QWidget,
        insert_index: int = -1,
    ) -> None:
        """Add a tab for a page and select it. insert_index -1 = right of the current tab."""
        if insert_index < 0 or insert_index > self.page_tab_bar.count():
            insert_index = self.page_tab_bar.currentIndex() + 1
        self._page_widgets[page_index] = page_widget
        self.page_stack.addWidget(page_widget)

        # Signals off while the tab is set up: inserting the first tab makes Qt select it before
        # its page_index is stored.
        self.page_tab_bar.blockSignals(True)
        tab_index = self.page_tab_bar.insertTab(insert_index, load_icon(icon_file_name), title_text)
        self.page_tab_bar.setTabData(tab_index, page_index)
        self.page_tab_bar.setTabToolTip(tab_index, title_text)
        self.page_tab_bar.setTabButton(tab_index, QTabBar.ButtonPosition.RightSide, self._build_close_btn())
        self.page_tab_bar.blockSignals(False)
        self.select_page(page_index)

    def remove_page(self, page_index: int) -> QWidget | None:
        """Remove a page's tab and return its widget (the caller decides where it goes next)."""
        tab_index = self._find_tab(page_index)
        if tab_index is None:
            return None
        page_widget = self._page_widgets.pop(page_index)
        self.page_stack.removeWidget(page_widget)
        self.page_tab_bar.removeTab(tab_index)     # may select another tab, so the dict is updated first
        if self.page_tab_bar.count() == 0:
            self._on_current_tab_changed(-1)               # Qt doesn't always report the last removal
        return page_widget

    def select_page(self, page_index: int) -> None:
        tab_index = self._find_tab(page_index)
        if tab_index is None:
            return
        if self.page_tab_bar.currentIndex() == tab_index:
            self._on_current_tab_changed(tab_index)        # already selected, so no signal will come
        else:
            self.page_tab_bar.setCurrentIndex(tab_index)

    def move_tab(self, page_index: int, insert_index: int) -> None:
        """Reorder a tab inside this pane."""
        tab_index = self._find_tab(page_index)
        if tab_index is not None:
            insert_index = min(max(insert_index, 0), self.page_tab_bar.count() - 1)
            self.page_tab_bar.moveTab(tab_index, insert_index)
            self.select_page(page_index)

    def has_page(self, page_index: int) -> bool:
        return self._find_tab(page_index) is not None

    def page_indices(self) -> list[int]:
        return [self.page_tab_bar.tabData(tab_index) for tab_index in range(self.page_tab_bar.count())]

    def current_page_index(self) -> int:
        tab_index = self.page_tab_bar.currentIndex()
        return NO_PAGE_INDEX if tab_index < 0 else self.page_tab_bar.tabData(tab_index)

    def tab_count(self) -> int:
        return self.page_tab_bar.count()

    def step_tab(self, step: int) -> None:
        tab_count = self.page_tab_bar.count()
        if tab_count > 1:
            self.page_tab_bar.setCurrentIndex((self.page_tab_bar.currentIndex() + step) % tab_count)
# --- END [4.5] ---

# ==============================================================================
# [4.6] pane_state
# ==============================================================================
    def set_empty_hint(self, hint_text: str) -> None:
        self.empty_tab_label.setText(hint_text)

    def set_is_active(self, is_active: bool) -> None:
        """Active pane: its selected tab gets an accent line (QSS property is_active_group)."""
        self._set_style_property(self.page_tab_bar, "is_active_group", is_active)

    def set_can_close_pane(self, can_close_pane: bool) -> None:
        self.close_pane_btn.setVisible(can_close_pane)
# --- END [4.6] ---

# ==============================================================================
# [4.7] drop_events
# ==============================================================================
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(PAGE_MIME_TYPE):
            event.acceptProposedAction()
            self._show_drop_preview(self._drop_zone_at(event.position().toPoint()))
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(PAGE_MIME_TYPE):
            event.acceptProposedAction()
            self._show_drop_preview(self._drop_zone_at(event.position().toPoint()))

    def dragLeaveEvent(self, event) -> None:
        self.drop_overlay_panel.hide()

    def dropEvent(self, event) -> None:
        self.drop_overlay_panel.hide()
        if not event.mimeData().hasFormat(PAGE_MIME_TYPE):
            event.ignore()
            return
        page_index = int(bytes(event.mimeData().data(PAGE_MIME_TYPE)).decode())
        drop_point = event.position().toPoint()
        drop_zone = self._drop_zone_at(drop_point)

        # Dropped on the tab row: insert at the hovered tab (or at the end).
        insert_index = -1
        if drop_zone == DROP_ZONE_TABS:
            hovered_tab_index = self.page_tab_bar.tabAt(self.page_tab_bar.mapFrom(self, drop_point))
            insert_index = hovered_tab_index if hovered_tab_index >= 0 else self.page_tab_bar.count()

        event.acceptProposedAction()
        self.page_dropped.emit(page_index, drop_zone, insert_index)

    def _drop_zone_at(self, point: QPoint) -> str:
        """Tab row -> "tabs"; outer EDGE_ZONE_FRACTION of the page area -> that edge; else "center"."""
        if self.tab_bar_panel.geometry().contains(point):
            return DROP_ZONE_TABS
        return edge_zone_at(self.page_stack.geometry(), point)

    def _show_drop_preview(self, drop_zone: str) -> None:
        """Highlight where the page will land: one half for an edge, the whole page area otherwise."""
        preview_rect = zone_preview_rect(self.page_stack.geometry(), drop_zone)
        self.drop_overlay_panel.setGeometry(preview_rect)
        self.drop_overlay_panel.raise_()
        self.drop_overlay_panel.show()
# --- END [4.7] ---

# ==============================================================================
# [4.8] helpers
# ==============================================================================
    def _build_row_btn(self, object_name: str, icon_file_name: str, tool_tip_text: str) -> QToolButton:
        row_btn = QToolButton(objectName=object_name)
        row_btn.setIcon(load_icon(icon_file_name))
        row_btn.setIconSize(ICON_SIZE)
        row_btn.setToolTip(tool_tip_text)
        row_btn.setAutoRaise(True)
        row_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return row_btn

    def _build_close_btn(self) -> QToolButton:
        tab_close_btn = QToolButton(objectName="tab_close_btn")
        tab_close_btn.setIcon(load_icon("tab_close.svg"))
        tab_close_btn.setIconSize(QSize(self.CLOSE_ICON_SIZE_PX, self.CLOSE_ICON_SIZE_PX))
        tab_close_btn.setAutoRaise(True)
        tab_close_btn.setToolTip("Close tab (Ctrl+W)")
        tab_close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tab_close_btn.clicked.connect(lambda: self._request_close_of_btn(tab_close_btn))
        return tab_close_btn

    def _request_close_of_btn(self, tab_close_btn: QToolButton) -> None:
        # Tabs move when dragged, so look up the button's current position at click time.
        for tab_index in range(self.page_tab_bar.count()):
            if self.page_tab_bar.tabButton(tab_index, QTabBar.ButtonPosition.RightSide) is tab_close_btn:
                self._request_close_at(tab_index)
                return

    def _request_close_at(self, tab_index: int) -> None:
        if 0 <= tab_index < self.page_tab_bar.count():
            self.tab_close_requested.emit(self.page_tab_bar.tabData(tab_index))

    def _find_tab(self, page_index: int) -> int | None:
        for tab_index in range(self.page_tab_bar.count()):
            if self.page_tab_bar.tabData(tab_index) == page_index:
                return tab_index
        return None

    def _page_widget_at(self, tab_index: int) -> QWidget:
        return self._page_widgets[self.page_tab_bar.tabData(tab_index)]

    def _on_current_tab_changed(self, tab_index: int) -> None:
        if tab_index < 0 or self.page_tab_bar.count() == 0:
            self.page_stack.setCurrentWidget(self.empty_tab_label)
            self.current_page_changed.emit(NO_PAGE_INDEX)
            return
        self.page_stack.setCurrentWidget(self._page_widget_at(tab_index))
        self.current_page_changed.emit(self.page_tab_bar.tabData(tab_index))

    @staticmethod
    def _set_style_property(widget: QWidget, property_name: str, value: bool) -> None:
        """Set a QSS property and re-apply the stylesheet so the change shows."""
        widget.setProperty(property_name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()
# --- END [4.8] ---
