# ==============================================================================
# FILE: app/ui/main_window.py
# OVERVIEW: The main DevOS window: collapsible sidebar on the left, pages on the right.
# PURPOSE:
#   1. Lists every page (app) DevOS can show and builds each one once at startup.
#   2. Places the sidebar and the page area side by side, split by a draggable seam.
#   3. Opens the clicked sidebar page in a tab (TabArea) and keeps the sidebar highlight and the
#      window title in step with the selected tab.
#   4. Slides the sidebar closed and open again, remembering how wide it was.
# CONTRACT:
#   - Requires: app.ui.widgets.sidebar (Sidebar, ICON_SIZE, load_icon; signals page_selected,
#               close_requested; select_page; *_WIDTH_PX), pages HomePage, PlaceholderPage,
#               OverviewPage, ProjectEditorPage, CostDashboardPage in app.ui.pages, app.ui.widgets.tab_area (TabArea; signal
#               current_page_changed; open_page; add_leading_widget; NO_PAGE_INDEX), assets/icons/sidebar_open.svg,
#               assets/icons/page.svg (generic icon for placeholder pages),
#               QSS selector #open_sidebar_btn in app/ui/theme.py
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
#   - [2026-11-15] page_entries gained a sidebar section as its first field; sidebar grouped into
#                  "Home" (Home, Settings) and "Workbenches" (Modules, Cost Tracking).
#   - [2026-11-15] Renamed sidebar items: Modules -> Project Management, Cost Tracking -> Finance.
#   - [2026-11-15] page_entries gained a folder field; Finance page renamed Market Lab and placed in a
#                  collapsible "Finance" folder under Workbenches.
#   - [2026-11-15] Project Management page renamed Project Editor and placed in a collapsible
#                  "Project Manager" folder under Workbenches.
#   - [2026-11-15] Top bar + page stack replaced by TabArea (Obsidian-style tabs). The open-sidebar
#                  button moved to the left end of the tab row; the window title shows the open page.
#   - [2026-11-15] Finance folder now holds a nested "Trading Dashboard" folder with six pages:
#                  Overview, Market Lab (existing page), Co-Pilot, Account Manager, Simulation
#                  Sandbox and System Monitor (placeholders).
#   - [2026-11-15] TabArea now supports split panes (see tab_area.py / tab_group.py); no change
#                  needed here beyond the unchanged TabArea API.
#   - [2026-11-15] Trading Dashboard › Overview now uses OverviewPage (hosts CalendarModule)
#                  instead of a PlaceholderPage.
#   - [2026-11-15] Project Manager › Project Editor now uses ProjectEditorPage (a ModuleCanvas).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - APP_TITLE (str): Window title; with a tab open it reads "DevOS — <page title>".
# - ANIMATION_MS (int | ms): Sidebar slide duration (default: 180; 0 = no animation).
# - INITIAL_CONTENT_WIDTH_PX (int | px): Starting splitter share for the page area; Qt rescales
#   to the real window width, so only its ratio to the sidebar width matters (default: 1000).
# - on_done_callback (callable): Function run once when a slide finishes (finish collapse/expand).
# - page_entries (list[tuple[str, str, str, str, QWidget]]): One entry per app:
#   {sidebar section, folder path ("" = none, or a tuple of nested folders), sidebar title,
#   icon file name in assets/icons/, page widget}.
#   List order = sidebar order; entries of one section or folder must be next to each other.
# - page_index (int): Position of a page in page_entries (0 = first app); TabArea uses the same index.
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
#    3.3 build_tab_area
#    3.4 build_nav_sidebar
#    3.5 build_main_splitter
#    3.6 on_current_page_changed
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
    | Sidebar   | [open][Home x][Market Lab x]    |
    |           |          ^ tab row (TabArea)    |
    |           |---------------------------------|
    |           |  page of the selected tab       |
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
    QMainWindow,
    QSplitter,
    QToolButton,
)
# --- END [2.1] ---

# ==============================================================================
# [2.2] page_imports
# ==============================================================================
#--Cost Tracker Page:
from app.ui.pages.cost_dashboard_page import CostDashboardPage
#--Home Page:
from app.ui.pages.home_page import HomePage

from app.ui.pages.overview_page import OverviewPage
#-- Dummy Page/ Placeholder:
from app.ui.pages.placeholder_page import PlaceholderPage

from app.ui.pages.project_editor_page import ProjectEditorPage
# --- END [2.2] ---

# ==============================================================================
# [2.3] widget_imports
# ==============================================================================
#-- Sidebar Widget:
from app.ui.widgets.sidebar import ICON_SIZE, Sidebar, load_icon
#-- Tab Area Widget:
from app.ui.widgets.tab_area import TabArea
# --- END [2.3] ---


# ==============================================================================
# [3.1] class_constants
# ==============================================================================
#-- MainWindow:
class MainWindow(QMainWindow):
    APP_TITLE = "DevOS"   # window title; the open page is added as "DevOS — <page>"
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
        self.setWindowTitle(self.APP_TITLE)
        self.resize(self.WINDOW_WIDTH_PX, self.WINDOW_HEIGHT_PX)

        # Applications: (sidebar section, folder path, sidebar title, icon file in assets/icons/, page widget).
        # Folder path: "" = none, "Name" = one folder, ("Outer", "Inner") = nested folders.
        # Add new applications here; keep entries of the same section and folder together.
        TRADING_DASHBOARD = ("Finance", "Trading Dashboard")
        PLANNED_TEXT = "Planned: not yet built."
        self.page_entries = [
            ("Home", "", "Home", "home.svg", HomePage()),
            ("Home", "", "Settings", "settings.svg", PlaceholderPage("Settings", "Configuration options.")),
            ("Workbenches", "Project Manager", "Project Editor", "modules.svg", ProjectEditorPage()),
            ("Workbenches", TRADING_DASHBOARD, "Overview", "page.svg", OverviewPage()),
            ("Workbenches", TRADING_DASHBOARD, "Market Lab", "costs.svg", CostDashboardPage()),
            ("Workbenches", TRADING_DASHBOARD, "Co-Pilot", "page.svg", PlaceholderPage("Co-Pilot", PLANNED_TEXT)),
            ("Workbenches", TRADING_DASHBOARD, "Account Manager", "page.svg", PlaceholderPage("Account Manager", PLANNED_TEXT)),
            ("Workbenches", TRADING_DASHBOARD, "Simulation Sandbox", "page.svg", PlaceholderPage("Simulation Sandbox", PLANNED_TEXT)),
            ("Workbenches", TRADING_DASHBOARD, "System Monitor", "page.svg", PlaceholderPage("System Monitor", PLANNED_TEXT)),
        ]

        # Order matters: the splitter needs the tab area and sidebar; the tab area talks to the sidebar.
        self._build_tab_area()
        self._build_nav_sidebar()
        self._build_main_splitter()
        self._build_sidebar_animation()

        self.statusBar().showMessage("Ready")
        self.show_page(0)   # DevOS starts with one tab: Home
# --- END [3.2] ---

# ==============================================================================
# [3.3] build_tab_area
# ==============================================================================
    def _build_tab_area(self) -> None:
        # --- Tabs on top, pages underneath ---
        tab_entries = [
            (title, icon_file, page_widget)
            for _section, _folder, title, icon_file, page_widget in self.page_entries
        ]
        self.tab_area = TabArea(tab_entries)
        self.tab_area.current_page_changed.connect(self._on_current_page_changed)

        self.open_sidebar_btn = QToolButton(objectName="open_sidebar_btn")
        self.open_sidebar_btn.setIcon(load_icon("sidebar_open.svg"))
        self.open_sidebar_btn.setIconSize(ICON_SIZE)
        self.open_sidebar_btn.setToolTip("Open sidebar")
        self.open_sidebar_btn.setAutoRaise(True)
        self.open_sidebar_btn.clicked.connect(self.expand_sidebar)
        self.open_sidebar_btn.hide()                  # only visible while the sidebar is closed
        self.tab_area.add_leading_widget(self.open_sidebar_btn)
# --- END [3.3] ---

# ==============================================================================
# [3.4] build_nav_sidebar
# ==============================================================================
    def _build_nav_sidebar(self) -> None:
        # --- Sidebar ---
        sidebar_items = [
            (section, folder, title, icon_file)
            for section, folder, title, icon_file, _page_widget in self.page_entries
        ]
        self.nav_sidebar_panel = Sidebar(sidebar_items)
        self.nav_sidebar_panel.page_selected.connect(self.show_page)
        self.nav_sidebar_panel.close_requested.connect(self.collapse_sidebar)
# --- END [3.4] ---

# ==============================================================================
# [3.5] build_main_splitter
# ==============================================================================
    def _build_main_splitter(self) -> None:
        # --- Splitter: sidebar and tab area are side-by-side tiles with a draggable seam ---
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self.nav_sidebar_panel)
        self.main_splitter.addWidget(self.tab_area)
        self.main_splitter.setCollapsible(0, False)   # the seam can't drag it shut; use the button
        self.main_splitter.setCollapsible(1, False)
        self.main_splitter.setStretchFactor(0, 0)     # window resizes only grow/shrink the content
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setHandleWidth(self.SPLITTER_HANDLE_WIDTH_PX)
        self.main_splitter.setSizes([Sidebar.DEFAULT_WIDTH_PX, self.INITIAL_CONTENT_WIDTH_PX])
        self.setCentralWidget(self.main_splitter)
# --- END [3.5] ---

# ==============================================================================
# [3.6] on_current_page_changed
# ==============================================================================
    def _on_current_page_changed(self, page_index: int) -> None:
        """Keep the sidebar highlight and window title in step with the selected tab."""
        if page_index == TabArea.NO_PAGE_INDEX:
            self.nav_sidebar_panel.clear_selection()
            self.setWindowTitle(self.APP_TITLE)
            return
        self.nav_sidebar_panel.select_page(page_index)
        self.setWindowTitle(f"{self.APP_TITLE} — {self.page_entries[page_index][2]}")
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
        """Open a page in a tab (or switch to its tab if it is already open)."""
        self.tab_area.open_page(page_index)
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
