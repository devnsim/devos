# ==============================================================================
# FILE: app/ui/main_window.py
# OVERVIEW: The main DevOS window: collapsible sidebar on the left, pages on the right.
# PURPOSE:
#   1. Lists every page (app) DevOS can show and builds each one once at startup.
#   2. Places the sidebar and the page area side by side, split by a draggable seam.
#   3. Switches pages when a sidebar item is clicked and shows the page name in the top bar.
#   4. Slides the sidebar closed and open again, remembering how wide it was.
# CONTRACT:
#   - Requires: app.ui.widgets.sidebar (Sidebar, ICON_SIZE, load_icon; signals page_selected,
#               close_requested; select_page; *_WIDTH_PX), pages HomePage, PlaceholderPage,
#               CostDashboardPage in app.ui.pages, assets/icons/sidebar_open.svg,
#               QSS selectors #top_bar, #top_bar_title_label, #open_sidebar_btn in app/ui/theme.py
#   - Exposes: MainWindow (QMainWindow), created and shown by app/main.py
# NOTES:
#   - [2026-11-14] Sidebar API renamed: select() -> select_page(), DEFAULT/MIN_WIDTH -> *_WIDTH_PX;
#                  open icon file renamed sidebar-open.svg -> sidebar_open.svg.
#   - [2026-11-14] Page imports updated: home_page.HomePage, placeholder_page.PlaceholderPage,
#                  cost_dashboard_page.CostDashboardPage (was Cost_Tracker.Cost_Dashboard).
#   - [2026-11-14] objectNames renamed to match theme.py: top_bar, top_bar_title_label,
#                  open_sidebar_btn, content_panel. objectName must equal the attribute name (rule 2.4).
#   - [2026-11-14] Reformatted to the file standard; __init__ split into _build_* methods;
#                  attributes and locals renamed; comment typos fixed (Tracker, Collapsible).
#   - [2026-11-14] During the slide the sidebar minimum width is 1 px, not 0: a 0 minimum makes Qt
#                  fall back to the layout's size hint, so the sidebar would stop shrinking early.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - ANIMATION_MS (int | ms): Sidebar slide duration (default: 180; 0 = no animation).
# - INITIAL_CONTENT_WIDTH_PX (int | px): Starting splitter share for the page area; Qt rescales
#   to the real window width, so only its ratio to the sidebar width matters (default: 1000).
# - on_done_callback (callable): Function run once when a slide finishes (finish collapse/expand).
# - page_entries (list[tuple[str, str, QWidget]]): One entry per app:
#   {sidebar title, icon file name in assets/icons/, page widget}. List order = sidebar order.
# - page_index (int): Position of a page in page_entries and page_stack (0 = first app).
# - _pending_animation_callback (callable | None): on_done_callback waiting for the running slide.
# - _saved_sidebar_width_px (int | px): Sidebar width remembered on collapse, restored on expand.
# - SPLITTER_HANDLE_WIDTH_PX (int | px): Grab width of the seam between sidebar and pages (default: 4).
# - WINDOW_WIDTH_PX / WINDOW_HEIGHT_PX (int | px): Window size at startup (default: 1200 x 760).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. DESIGN_NOTES
#    1.1 design_notes_docstring
# 2. IMPORTS
#    2.1 pyside_imports
#    2.2 page_imports
#    2.3 widget_imports
# 3. MAIN_WINDOW
#    3.1 class_constants
#    3.2 init_window
#    3.3 build_page_stack
#    3.4 build_nav_sidebar
#    3.5 build_main_splitter
#    3.6 build_top_bar
#    3.7 build_sidebar_animation
#    3.8 show_page
#    3.9 collapse_sidebar
#    3.10 expand_sidebar
#    3.11 animation_helpers
# ==============================================================================

# ==============================================================================
# [1.1] design_notes_docstring
# ==============================================================================
"""
---Main window----

Purpose:
1. This window will act as the main page, the start page, or otherwise the main area of navigation that the user starts with and uses to navigate modules and workspaces.


Functions:
1. User navigation and display.

Features & Layout:


--- Sidebar ----
Overview:
-On left side of window
-Collapsible with button.
-Contains list of modules
-Acts as navigation tool

    +-----------+---------------------------------+
    | Sidebar   | [open] Page title    <- top bar |
    |           |---------------------------------|
    |           |  current page (QStackedWidget)  |
    +-----------+---------------------------------+
                ^ seam: drag to resize the sidebar (QSplitter)


Notes:




-----------------




"""
# --- END [1.1] ---

# ==============================================================================
# [2.1] pyside_imports
# ==============================================================================
#-- Pyside utilities:
from PySide6.QtCore import QEasingCurve, Qt, QVariantAnimation

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
# --- END [2.1] ---

# ==============================================================================
# [2.2] page_imports
# ==============================================================================
#--Cost Tracker Page:
from app.ui.pages.cost_dashboard_page import CostDashboardPage
#--Home Page:
from app.ui.pages.home_page import HomePage
#-- Dummy Page/ Placeholder:
from app.ui.pages.placeholder_page import PlaceholderPage
# --- END [2.2] ---

# ==============================================================================
# [2.3] widget_imports
# ==============================================================================
#-- Sidebar Widget:
from app.ui.widgets.sidebar import ICON_SIZE, Sidebar, load_icon
# --- END [2.3] ---


# ==============================================================================
# [3.1] class_constants
# ==============================================================================
#-- MainWindow:
class MainWindow(QMainWindow):
    ANIMATION_MS = 180   # sidebar slide speed; set to 0 to turn the animation off
    WINDOW_WIDTH_PX = 1200
    WINDOW_HEIGHT_PX = 760
    SPLITTER_HANDLE_WIDTH_PX = 4
    INITIAL_CONTENT_WIDTH_PX = 1000   # DEF: starting splitter share for the page area; only the ratio matters
# --- END [3.1] ---

# ==============================================================================
# [3.2] init_window
# ==============================================================================
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DevOS")
        self.resize(self.WINDOW_WIDTH_PX, self.WINDOW_HEIGHT_PX)

        # Applications: (sidebar title, icon file in assets/icons/, page widget).
        # Add new applications here.
        self.page_entries = [
            ("Home", "home.svg", HomePage()),
            ("Modules", "modules.svg", PlaceholderPage("Modules", "C++ and Python components will live here.")),
            ("Cost Tracking", "costs.svg", CostDashboardPage()),
            ("Settings", "settings.svg", PlaceholderPage("Settings", "Configuration options.")),
        ]

        # Order matters: the splitter needs the stack and sidebar; show_page needs the top bar.
        self._build_page_stack()
        self._build_nav_sidebar()
        self._build_main_splitter()
        self._build_sidebar_animation()

        self.statusBar().showMessage("Ready")
        self.show_page(0)
# --- END [3.2] ---

# ==============================================================================
# [3.3] build_page_stack
# ==============================================================================
    def _build_page_stack(self) -> None:
        # --- Pages ---
        self.page_stack = QStackedWidget()
        for _title, _icon_file, page_widget in self.page_entries:
            self.page_stack.addWidget(page_widget)
# --- END [3.3] ---

# ==============================================================================
# [3.4] build_nav_sidebar
# ==============================================================================
    def _build_nav_sidebar(self) -> None:
        # --- Sidebar ---
        sidebar_items = [(title, icon_file) for title, icon_file, _page_widget in self.page_entries]
        self.nav_sidebar_panel = Sidebar(sidebar_items)
        self.nav_sidebar_panel.page_selected.connect(self.show_page)
        self.nav_sidebar_panel.close_requested.connect(self.collapse_sidebar)
# --- END [3.4] ---

# ==============================================================================
# [3.5] build_main_splitter
# ==============================================================================
    def _build_main_splitter(self) -> None:
        # --- Right side: top bar + pages ---
        content_panel = QWidget(objectName="content_panel")
        content_layout = QVBoxLayout(content_panel)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._build_top_bar())
        content_layout.addWidget(self.page_stack, 1)

        # --- Splitter: sidebar and content are side-by-side tiles with a draggable seam ---
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self.nav_sidebar_panel)
        self.main_splitter.addWidget(content_panel)
        self.main_splitter.setCollapsible(0, False)   # the seam can't drag it shut; use the button
        self.main_splitter.setCollapsible(1, False)
        self.main_splitter.setStretchFactor(0, 0)     # window resizes only grow/shrink the content
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setHandleWidth(self.SPLITTER_HANDLE_WIDTH_PX)
        self.main_splitter.setSizes([Sidebar.DEFAULT_WIDTH_PX, self.INITIAL_CONTENT_WIDTH_PX])
        self.setCentralWidget(self.main_splitter)
# --- END [3.5] ---

# ==============================================================================
# [3.6] build_top_bar
# ==============================================================================
    def _build_top_bar(self) -> QWidget:
        top_bar = QWidget(objectName="top_bar")
        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(6, 4, 10, 4)
        top_bar_layout.setSpacing(6)

        self.open_sidebar_btn = QToolButton(objectName="open_sidebar_btn")
        self.open_sidebar_btn.setIcon(load_icon("sidebar_open.svg"))
        self.open_sidebar_btn.setIconSize(ICON_SIZE)
        self.open_sidebar_btn.setToolTip("Open sidebar")
        self.open_sidebar_btn.setAutoRaise(True)
        self.open_sidebar_btn.clicked.connect(self.expand_sidebar)
        self.open_sidebar_btn.hide()                  # only visible while the sidebar is closed

        self.top_bar_title_label = QLabel(objectName="top_bar_title_label")

        top_bar_layout.addWidget(self.open_sidebar_btn)
        top_bar_layout.addWidget(self.top_bar_title_label)
        top_bar_layout.addStretch()
        return top_bar
# --- END [3.6] ---

# ==============================================================================
# [3.7] build_sidebar_animation
# ==============================================================================
    def _build_sidebar_animation(self) -> None:
        # --- Slide animation for open/close ---
        self._saved_sidebar_width_px = Sidebar.DEFAULT_WIDTH_PX
        self._pending_animation_callback = None
        self._sidebar_animation = QVariantAnimation(self)
        self._sidebar_animation.setDuration(self.ANIMATION_MS)
        self._sidebar_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._sidebar_animation.valueChanged.connect(self._set_sidebar_width)
        self._sidebar_animation.finished.connect(self._on_animation_finished)
# --- END [3.7] ---

# ==============================================================================
# [3.8] show_page
# ==============================================================================
    def show_page(self, page_index: int) -> None:
        self.page_stack.setCurrentIndex(page_index)
        self.top_bar_title_label.setText(self.page_entries[page_index][0])
        self.nav_sidebar_panel.select_page(page_index)
# --- END [3.8] ---

# ==============================================================================
# [3.9] collapse_sidebar
# ==============================================================================
    def collapse_sidebar(self) -> None:
        if self._is_sidebar_animating() or not self.nav_sidebar_panel.isVisible():
            return
        self._saved_sidebar_width_px = self.main_splitter.sizes()[0]   # remember width for reopening
        self.nav_sidebar_panel.setMinimumWidth(1)                      # allow shrinking during the slide
        self._run_sidebar_animation(self._saved_sidebar_width_px, 0, self._finish_collapse)

    def _finish_collapse(self) -> None:
        self.nav_sidebar_panel.hide()
        self.open_sidebar_btn.show()
# --- END [3.9] ---

# ==============================================================================
# [3.10] expand_sidebar
# ==============================================================================
    def expand_sidebar(self) -> None:
        if self._is_sidebar_animating() or self.nav_sidebar_panel.isVisible():
            return
        self.open_sidebar_btn.hide()
        self.nav_sidebar_panel.setMinimumWidth(1)
        self.nav_sidebar_panel.show()
        self._set_sidebar_width(0)
        self._run_sidebar_animation(0, self._saved_sidebar_width_px, self._finish_expand)

    def _finish_expand(self) -> None:
        self.nav_sidebar_panel.setMinimumWidth(Sidebar.MIN_WIDTH_PX)      # restore drag limit
# --- END [3.10] ---

# ==============================================================================
# [3.11] animation_helpers
# ==============================================================================
    def _is_sidebar_animating(self) -> bool:
        return self._sidebar_animation.state() == QVariantAnimation.State.Running

    def _run_sidebar_animation(self, start_width_px: int, end_width_px: int, on_done_callback) -> None:
        self._pending_animation_callback = on_done_callback
        if self.ANIMATION_MS <= 0:
            self._set_sidebar_width(end_width_px)
            self._on_animation_finished()
            return
        self._sidebar_animation.setStartValue(start_width_px)
        self._sidebar_animation.setEndValue(end_width_px)
        self._sidebar_animation.start()

    def _set_sidebar_width(self, width_px) -> None:
        total_width_px = sum(self.main_splitter.sizes())
        width_px = int(width_px)
        self.main_splitter.setSizes([width_px, max(total_width_px - width_px, 0)])

    def _on_animation_finished(self) -> None:
        if self._pending_animation_callback is not None:
            callback, self._pending_animation_callback = self._pending_animation_callback, None
            callback()
# --- END [3.11] ---
