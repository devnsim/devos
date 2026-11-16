# ==============================================================================
# FILE: app/ui/widgets/tab_area.py
# OVERVIEW: The tab workspace: panes of tabs (TabGroup) tiled in any mix of rows and columns.
# PURPOSE:
#   1. Opens pages as tabs in the active pane; a page that is already open anywhere is switched to.
#   2. Tiles panes like a tiling window manager: dropping a tab on a pane's edge splits that pane
#      on that side, to any depth. Every seam between panes can be dragged.
#   3. Moves tabs between panes on drag and drop, and tidies the layout when panes close.
#   4. Tells the main window which page is showing in the active pane (sidebar highlight, title).
# CONTRACT:
#   - Requires: app.ui.widgets.tab_group (TabGroup, NO_PAGE_INDEX, DROP_ZONE_TABS),
#               app.ui.widgets.tiling_layout (drop zones, insert_beside, normalize_tree, first_leaf, new_splitter),
#               app.ui.window_events.mouse_hub,
#               QSS selectors #tab_area_panel, #pane_splitter in app/ui/theme.py
#   - Exposes: TabArea(page_provider) (QWidget) with signals current_page_changed(int),
#              create_page_requested(object TabGroup), rename_requested(int); open_page(page_index);
#              open_page_in(tab_group, page_index); set_page_title(); set_page_icon(); replace_page_widget();
#              forget_page(); PageProvider;
#              close_page(page_index); close_current_tab(); split_pane(tab_group, drop_zone);
#              close_pane(tab_group); add_leading_widget(widget); add_trailing_widget(widget); NO_PAGE_INDEX
# NOTES:
#   - [2026-11-16] Active pane follows mouse presses via window_events.mouse_hub() instead of an event
#                  filter on the QApplication (PySide crashed on Qt WebEngine's internal objects).
#   - [2026-11-16] Ctrl+T = new page in the active pane (the tab row's +); set_page_icon() and
#                  replace_page_widget() for pages that change kind (canvas -> browser).
#   - [2026-11-15] Created as a single tab row (Obsidian-style tabs).
#   - [2026-11-15] Became a multi-pane workspace: the tab row moved into TabGroup (tab_group.py);
#                  TabArea now owns the panes, the seams between them and every page move.
#   - [2026-11-15] Layout rule kept for the future grid: a page is identified only by page_index,
#                  lives in at most one tab across all panes, and pages without a tab wait in
#                  parked_pages_panel.
#   - [2026-11-15] Panes now tile as a tree of QSplitters (rows and columns, any depth).
#                  _normalize_layout() keeps the tree minimal after every close.
#   - [2026-11-15] Pages come from a PageProvider (title, icon, widget created on first open) instead
#                  of a fixed list, because pages are user data now. page_index = the page's id.
#   - [2026-11-15] Splitter-tree operations moved to tiling_layout.py (shared with ModuleCanvas);
#                  EDGE_SPLITS [2.2] and the bodies of [3.6]/[3.7] now call it.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - active pane: The TabGroup the user last clicked in, dropped on or opened; sidebar clicks open
#   pages there, and Ctrl+W / Ctrl+Tab act on it.
# - drop zone (str): Where a tab landed on a pane: tab row, centre, or left/right/top/bottom edge.
# - layout tree: root_splitter and the splitters inside it are branches; TabGroups are leaves.
#   A splitter's orientation says whether its children sit side by side or top to bottom.
# - leading widget: A widget placed at the far left of the top-left pane's tab row (open-sidebar button).
# - trailing widget: A widget at the far right of the top-right pane's tab row (window controls).
# - NO_PAGE_INDEX (int): Sent by current_page_changed when the active pane has no tab (value: -1).
# - page provider: Object answering page_title(id), page_icon(id), page_widget(id) (see PageProvider).
# - page_index (int): A page's id (user pages: database id; system pages: small reserved numbers).
# - pane: One TabGroup (a leaf of the layout tree).
# - parked_pages_panel (QWidget): Hidden holder for pages that have no tab, so they are never deleted.
# - root_splitter (QSplitter): Top of the layout tree; never removed.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. DESIGN_NOTES
#    1.1 design_notes_docstring
# 2. IMPORTS
#    2.1 imports
# 3. TAB_AREA
#    3.1 class_signals_and_constants
#    3.2 init_tab_area
#    3.3 build_shortcuts
#    3.4 open_and_close_pages
#    3.5 drop_handling
#    3.6 split_and_close_panes
#    3.7 normalize_layout
#    3.8 pane_bookkeeping
#    3.9 active_pane
#    3.10 helpers
# ==============================================================================

# ==============================================================================
# [1.1] design_notes_docstring
# ==============================================================================
"""Tiling tab workspace.

Example after a few edge drops:
    root_splitter (side by side)
    ├── TabGroup A                      +-----------+-----------+
    └── splitter (top to bottom)        |           |     B     |
        ├── TabGroup B                  |     A     +-----------+
        └── TabGroup C                  |           |     C     |
                                        +-----------+-----------+

Rules:
    - Sidebar click: if the page is open in any pane, select that tab; otherwise open it in a new
      tab in the active pane, right of the current tab.
    - Drop on a pane's tab row or centre: the tab moves into that pane.
    - Drop on a pane's edge: that pane splits; a new pane holding the tab appears on that side and
      takes half the space. If the pane's splitter already runs that way, the new pane joins it;
      otherwise the pane is wrapped in a new splitter that runs that way.
    - Split button: same as dropping on the right edge, but the new pane starts empty.
    - A pane that loses its last tab closes (if another pane exists); a fresh empty split pane
      stays until used or closed with its x. After any close the tree is tidied (see [3.7]).
"""
# --- END [1.1] ---

# ==============================================================================
# [2.1] imports
# ==============================================================================
from typing import Protocol

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QVBoxLayout, QWidget

from app.ui.widgets.tab_group import DROP_ZONE_TABS, NO_PAGE_INDEX, TabGroup
from app.ui.window_events import mouse_hub
from app.ui.widgets.tiling_layout import (
    DROP_ZONE_RIGHT, EDGE_DROP_ZONES, first_leaf, insert_beside, new_splitter, normalize_tree, top_right_leaf,
)
# --- END [2.1] ---

# ==============================================================================
# [3.1] class_signals_and_constants
# ==============================================================================
class PageProvider(Protocol):
    def page_title(self, page_index: int) -> str: ...
    def page_icon(self, page_index: int) -> str: ...
    def page_widget(self, page_index: int) -> QWidget: ...


class TabArea(QWidget):
    current_page_changed = Signal(int)   # page_index shown in the active pane, or NO_PAGE_INDEX
    create_page_requested = Signal(object)   # TabGroup whose "Create New Page" was clicked
    rename_requested = Signal(int)           # page_index (tab context menu)

    NO_PAGE_INDEX = NO_PAGE_INDEX
    SEAM_WIDTH_PX = 4
    SINGLE_PANE_HINT = "or pick a page from the sidebar"
    SPLIT_PANE_HINT = "or drag a tab here"
# --- END [3.1] ---

# ==============================================================================
# [3.2] init_tab_area
# ==============================================================================
    def __init__(self, page_provider: PageProvider, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tab_area_panel")
        self.page_provider = page_provider
        self.tab_groups: list[TabGroup] = []
        self.active_tab_group: TabGroup | None = None
        self.leading_widgets: list[QWidget] = []
        self.trailing_widgets: list[QWidget] = []
        self._is_dragging_tab = False

        # --- Pages without a tab wait here (hidden), so closing a pane never deletes them ---
        self.parked_pages_panel = QWidget(self)
        self.parked_pages_panel.hide()

        # --- Layout tree: starts as one splitter holding one pane ---
        self.root_splitter = new_splitter(Qt.Orientation.Horizontal, self.SEAM_WIDTH_PX)
        tab_area_layout = QVBoxLayout(self)
        tab_area_layout.setContentsMargins(0, 0, 0, 0)
        tab_area_layout.addWidget(self.root_splitter)

        first_tab_group = self._new_tab_group()
        self.root_splitter.addWidget(first_tab_group)
        self._refresh_panes()
        self._set_active_tab_group(first_tab_group)

        self._build_shortcuts()
        # A click anywhere inside a pane makes it the active pane, see [3.9].
        mouse_hub().pressed.connect(self._on_mouse_pressed)
# --- END [3.2] ---

# ==============================================================================
# [3.3] build_shortcuts
# ==============================================================================
    def _build_shortcuts(self) -> None:
        # --- Keyboard: work anywhere in the main window, on the active pane ---
        shortcut_actions = [
            ("Ctrl+W", self.close_current_tab),
            ("Ctrl+T", lambda: self.create_page_requested.emit(self.active_tab_group)),
            ("Ctrl+Tab", lambda: self.active_tab_group.step_tab(1)),
            ("Ctrl+Shift+Tab", lambda: self.active_tab_group.step_tab(-1)),
        ]
        for key_text, action in shortcut_actions:
            shortcut = QShortcut(QKeySequence(key_text), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(action)
# --- END [3.3] ---

# ==============================================================================
# [3.4] open_and_close_pages
# ==============================================================================
    def open_page(self, page_index: int) -> None:
        """Select the page's tab wherever it is open; otherwise open it in the active pane."""
        owner_tab_group = self._find_owner(page_index)
        if owner_tab_group is not None:
            self._set_active_tab_group(owner_tab_group)
            owner_tab_group.select_page(page_index)
            return
        self._add_page_to(self.active_tab_group, page_index, insert_index=-1)

    def close_page(self, page_index: int) -> None:
        """Close a page's tab; the page is parked, not destroyed."""
        owner_tab_group = self._find_owner(page_index)
        if owner_tab_group is None:
            return
        self._park(owner_tab_group.remove_page(page_index))
        self._schedule_prune()

    def open_page_in(self, tab_group: TabGroup, page_index: int) -> None:
        """Open a page in a specific pane (moving its tab there if it is open elsewhere)."""
        owner_tab_group = self._find_owner(page_index)
        if owner_tab_group is tab_group:
            self._set_active_tab_group(tab_group)
            tab_group.select_page(page_index)
            return
        if owner_tab_group is not None:
            owner_tab_group.remove_page(page_index)
        self._add_page_to(tab_group, page_index, insert_index=-1)
        self._schedule_prune()

    def set_page_title(self, page_index: int, title: str) -> None:
        """A page was renamed: update its tab (wherever it is)."""
        owner_tab_group = self._find_owner(page_index)
        if owner_tab_group is not None:
            owner_tab_group.set_page_title(page_index, title)

    def set_page_icon(self, page_index: int, icon_file_name: str) -> None:
        owner_tab_group = self._find_owner(page_index)
        if owner_tab_group is not None:
            owner_tab_group.set_page_icon(page_index, icon_file_name)

    def replace_page_widget(self, page_index: int, page_widget: QWidget) -> QWidget | None:
        """Swap the widget shown for a page (open or parked); returns the old widget for disposal."""
        owner_tab_group = self._find_owner(page_index)
        if owner_tab_group is not None:
            return owner_tab_group.replace_page_widget(page_index, page_widget)
        return None

    def forget_page(self, page_index: int) -> None:
        """A page was deleted: close its tab. The caller disposes of the widget."""
        owner_tab_group = self._find_owner(page_index)
        if owner_tab_group is not None:
            owner_tab_group.remove_page(page_index)
            self._schedule_prune()

    def close_current_tab(self) -> None:
        page_index = self.active_tab_group.current_page_index()
        if page_index != NO_PAGE_INDEX:
            self.close_page(page_index)
# --- END [3.4] ---

# ==============================================================================
# [3.5] drop_handling
# ==============================================================================
    def _on_page_dropped(
        self, target_tab_group: TabGroup, page_index: int, drop_zone: str, insert_index: int
    ) -> None:
        """A tab was dropped on target_tab_group: move it in, or split the pane at that edge."""
        source_tab_group = self._find_owner(page_index)

        if drop_zone in EDGE_DROP_ZONES:
            if source_tab_group is target_tab_group and target_tab_group.tab_count() <= 1:
                return                              # splitting a pane off its own only tab: no change
            new_tab_group = self.split_pane(target_tab_group, drop_zone)
            if source_tab_group is not None:
                source_tab_group.remove_page(page_index)
            self._add_page_to(new_tab_group, page_index, insert_index=-1)
            self._schedule_prune()
            return

        self._set_active_tab_group(target_tab_group)
        if source_tab_group is target_tab_group:
            if drop_zone == DROP_ZONE_TABS:
                target_tab_group.move_tab(page_index, insert_index)
            else:
                target_tab_group.select_page(page_index)
            return
        if source_tab_group is not None:
            source_tab_group.remove_page(page_index)
        self._add_page_to(target_tab_group, page_index, insert_index)
        self._schedule_prune()
# --- END [3.5] ---

# ==============================================================================
# [3.6] split_and_close_panes
# ==============================================================================
    def split_pane(self, tab_group: TabGroup, drop_zone: str = DROP_ZONE_RIGHT) -> TabGroup:
        """Put a new empty pane on one side of tab_group (half its space) and make it active."""
        new_tab_group = self._new_tab_group()
        insert_beside(tab_group, new_tab_group, drop_zone, self.SEAM_WIDTH_PX)
        self._refresh_panes()
        self._set_active_tab_group(new_tab_group)
        return new_tab_group

    def close_pane(self, tab_group: TabGroup) -> None:
        """Close a pane and its tabs (pages are parked). The last pane can't be closed."""
        if len(self.tab_groups) <= 1:
            return
        for page_index in tab_group.page_indices():
            self._park(tab_group.remove_page(page_index))
        self._remove_tab_group(tab_group)

    def _remove_tab_group(self, tab_group: TabGroup) -> None:
        self.tab_groups.remove(tab_group)
        tab_group.hide()
        tab_group.setParent(None)                  # leaves its splitter
        self._normalize_layout()
        self._refresh_panes()                      # also moves the leading widgets to a live pane
        if self.active_tab_group is tab_group or self.active_tab_group is None:
            self._set_active_tab_group(self._top_left_tab_group())
        tab_group.deleteLater()
# --- END [3.6] ---

# ==============================================================================
# [3.7] normalize_layout
# ==============================================================================
    def _normalize_layout(self) -> None:
        """Tidy the layout tree after a pane closed (rules in tiling_layout.normalize_tree)."""
        normalize_tree(self.root_splitter)
# --- END [3.7] ---

# ==============================================================================
# [3.8] pane_bookkeeping
# ==============================================================================
    def _new_tab_group(self) -> TabGroup:
        tab_group = TabGroup()
        tab_group.current_page_changed.connect(lambda _page_index: self._on_group_page_changed(tab_group))
        tab_group.tab_close_requested.connect(self.close_page)
        tab_group.page_dropped.connect(
            lambda page_index, drop_zone, insert_index:
                self._on_page_dropped(tab_group, page_index, drop_zone, insert_index)
        )
        tab_group.split_requested.connect(lambda: self.split_pane(tab_group, DROP_ZONE_RIGHT))
        tab_group.close_pane_requested.connect(lambda: self.close_pane(tab_group))
        tab_group.drag_started.connect(self._on_drag_started)
        tab_group.drag_finished.connect(self._on_drag_finished)
        tab_group.create_page_requested.connect(lambda: self.create_page_requested.emit(tab_group))
        tab_group.rename_requested.connect(self.rename_requested)
        self.tab_groups.append(tab_group)
        return tab_group

    def _schedule_prune(self) -> None:
        # Deferred: a drop is handled inside the source tab row's drag loop, and that row may be
        # the one removed. While a drag runs, _on_drag_finished schedules the prune instead.
        if not self._is_dragging_tab:
            QTimer.singleShot(0, self._prune_empty_panes)

    def _prune_empty_panes(self) -> None:
        """Close panes that lost their last tab, as long as another pane stays open."""
        for tab_group in list(self.tab_groups):
            if len(self.tab_groups) > 1 and tab_group.tab_count() == 0 and tab_group.property("was_used"):
                self._remove_tab_group(tab_group)

    def _on_drag_started(self) -> None:
        self._is_dragging_tab = True

    def _on_drag_finished(self) -> None:
        self._is_dragging_tab = False
        QTimer.singleShot(0, self._prune_empty_panes)

    def _refresh_panes(self) -> None:
        """Re-apply things that depend on how many panes there are and which one is active."""
        has_several_panes = len(self.tab_groups) > 1
        for tab_group in self.tab_groups:
            tab_group.set_can_close_pane(has_several_panes)
            tab_group.set_empty_hint(self.SPLIT_PANE_HINT if has_several_panes else self.SINGLE_PANE_HINT)
            tab_group.set_is_active(has_several_panes and tab_group is self.active_tab_group)
        self._place_leading_widgets()
# --- END [3.8] ---

# ==============================================================================
# [3.9] active_pane
# ==============================================================================
    def _set_active_tab_group(self, tab_group: TabGroup) -> None:
        if tab_group is not self.active_tab_group:
            self.active_tab_group = tab_group
            self._refresh_panes()
        self.current_page_changed.emit(tab_group.current_page_index())

    def _on_group_page_changed(self, tab_group: TabGroup) -> None:
        if tab_group is self.active_tab_group:
            self.current_page_changed.emit(tab_group.current_page_index())

    def _on_mouse_pressed(self, widget: QWidget) -> None:
        """Any mouse press inside a pane makes it the active pane (the press still goes through)."""
        clicked_tab_group = self._tab_group_containing(widget)
        if clicked_tab_group is not None and clicked_tab_group is not self.active_tab_group:
            self._set_active_tab_group(clicked_tab_group)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        mouse_hub().watch(self)
# --- END [3.9] ---

# ==============================================================================
# [3.10] helpers
# ==============================================================================
    def add_leading_widget(self, widget: QWidget) -> None:
        """Place a widget at the far left of the top-left pane's tab row (e.g. the open-sidebar button)."""
        self.leading_widgets.append(widget)
        self._place_leading_widgets()

    def add_trailing_widget(self, widget: QWidget) -> None:
        """Place a widget at the far right of the top-right pane's tab row (e.g. the window controls)."""
        self.trailing_widgets.append(widget)
        self._place_leading_widgets()

    def _place_leading_widgets(self) -> None:
        # Leading widgets sit in the top-left pane, trailing ones in the top-right pane; follow those
        # corners when panes split, move or close.
        corners = ((self._top_left_tab_group(), reversed(self.leading_widgets), "add_leading_widget"),
                   (top_right_leaf(self.root_splitter), self.trailing_widgets, "add_trailing_widget"))
        for tab_group, widgets, add_method in corners:
            if tab_group is None:
                continue
            for widget in widgets:
                if widget.parentWidget() is tab_group.tab_bar_panel:
                    continue
                # Keep only an explicit hide() (e.g. the open-sidebar button while the sidebar is open);
                # a brand-new parentless widget also reports isHidden(), but should appear.
                was_hidden = widget.isHidden() and widget.testAttribute(Qt.WidgetAttribute.WA_WState_ExplicitShowHide)
                widget.setParent(None)                     # leaves its old row
                getattr(tab_group, add_method)(widget)
                widget.setVisible(not was_hidden)

    def _top_left_tab_group(self) -> TabGroup | None:
        """First pane in the layout tree (always the top-left one)."""
        return first_leaf(self.root_splitter)

    def _add_page_to(self, tab_group: TabGroup, page_index: int, insert_index: int) -> None:
        provider = self.page_provider
        tab_group.setProperty("was_used", True)
        tab_group.add_page(page_index, provider.page_title(page_index), provider.page_icon(page_index),
                           provider.page_widget(page_index), insert_index)
        self._set_active_tab_group(tab_group)

    def _park(self, page_widget: QWidget | None) -> None:
        if page_widget is not None:
            page_widget.setParent(self.parked_pages_panel)

    def _find_owner(self, page_index: int) -> TabGroup | None:
        for tab_group in self.tab_groups:
            if tab_group.has_page(page_index):
                return tab_group
        return None

    def _tab_group_containing(self, widget: QWidget) -> TabGroup | None:
        while widget is not None:
            if isinstance(widget, TabGroup) and widget in self.tab_groups:
                return widget
            widget = widget.parentWidget()
        return None
# --- END [3.10] ---
