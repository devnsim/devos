# ==============================================================================
# FILE: app/ui/main_window.py
# OVERVIEW: The main DevOS window: sidebar of user pages on the left, tiled tab panes on the right,
#           an integrated title bar on top and a resource status bar at the bottom.
# PURPOSE:
#   1. Opens pages (from the sidebar, new-page buttons or empty panes) in tabs; builds each page from
#      the page template (CanvasPage) the first time it is opened.
#   2. Keeps sidebar, tab titles, page headings and the window title in step when pages are created,
#      renamed or deleted.
#   3. Places the sidebar and the page area side by side, split by a draggable seam; slides the
#      sidebar closed and open again, remembering how wide it was.
#   4. Frameless window with an integrated title bar and an IDE-style status bar.
#   5. Several windows: a Workspace holds what windows share (page store, page widgets, parked pages);
#      any tab can move to a new window (tab menu, or drag it out of every window) - the new window is a
#      full DevOS window (sidebar, tabs, status bar) holding just that page. A page lives in one tab across
#      all windows: opening it anywhere brings its window forward.
# CONTRACT:
#   - Requires: app.data.store (database.get_database, pages.PageStore), app.ui.pages (CanvasPage,
#               BrowserPage, InfiniteCanvasPage, PlaceholderPage), app.ui.widgets.tab_group (PAGE_MIME_TYPE,
#               note_tab_drag_over_window), app.ui.widgets (sidebar.Sidebar, tab_area.TabArea, title_bar,
#               status_bar.SystemStatusBar), assets/icons (sidebar_open, page, settings .svg),
#               QSS selector #open_sidebar_btn
#   - Exposes: MainWindow(workspace=None) (QMainWindow), created and shown by app/main.py (the first window
#              makes the Workspace); show_page(page_id); set_log_visible(); open_tool_bag() (Ctrl+K);
#              create_page(tab_group=None); rename_page(page_id, title, is_automatic=False);
#              convert_page(page_id, kind); open_browser_tab(url); page_store; page_widgets; workspace;
#              SETTINGS_PAGE_ID; Workspace with windows, open_in_new_window(page_id, source, at_cursor),
#              window_with_page(page_id)
# NOTES:
#   - [2026-11-17] Several windows: Workspace (shared page store, page widgets, parked pages, log flush);
#                  "Open in new window" / dragging a tab out of every window opens it in a new MainWindow;
#                  renames, deletions and new pages reach every window's sidebar and tabs; a closing window
#                  parks (and so suspends) its pages. The window accepts stray tab drops (a tab dropped on
#                  the sidebar or status bar stays put instead of opening a new window).
#   - [2026-11-17] convert_page(page_id, kind) replaces convert_to_browser(): New Browser and New Canvas
#                  (InfiniteCanvasPage) both turn an empty page into another kind in the same tab.
#   - [2026-11-16] Ctrl+K opens the tool bag in run mode for the module last clicked (tool_bag.note_pressed
#                  listens on mouse_hub).
#   - [2026-11-16] Browser pages: a page's kind decides what page_widget() builds (CanvasPage or BrowserPage);
#                  "New Browser" on an empty page converts it in place (same tab, sidebar icon -> globe);
#                  links that open new windows become new browser pages. Browser pages are named after the
#                  website until the user renames them (automatic renames are debug-level in the log).
#                  The tab row's + / Ctrl+T create pages like the sidebar's +. Mouse presses are watched at
#                  the window (window_events.mouse_hub), not with an app-wide filter (crashed with Qt WebEngine).
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
#   - [2026-11-15] Frameless window with an integrated title bar ([3.12], widgets/title_bar.py):
#                  sidebar header + top tab rows move the window, controls top-right, thin resize
#                  edge. The unused "Ready" status bar was removed (it read as window chrome).
#   - [2026-11-15] Pages are user data now: no fixed page_entries list. Pages and folders live in
#                  SQLite (PageStore); every page is a CanvasPage (the page template), built on first
#                  open. MainWindow is TabArea's page provider and keeps sidebar, tabs, page heading
#                  and window title in step when a page is created, renamed or deleted. Removed pages:
#                  Home (system monitors), Trading Dashboard folder (Overview, Market Lab, Co-Pilot,
#                  Account Manager, Simulation Sandbox, System Monitor) and the fixed Project Editor
#                  (now a user page in Favorites). Settings opens from the sidebar's gear.
#   - [2026-11-15] System Log: LogPanel docked under the tab panes (work_splitter), toggled from the
#                  status bar's Log tile; open state + height are user preferences; the activity log is
#                  flushed to the database every LOG_FLUSH_INTERVAL_MS. Opening a page is a logged step.
#   - [2026-11-15] SystemStatusBar (bottom, IDE-style) shows app/system resource tiles.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - APP_TITLE (str): Window title; with a tab open it reads "DevOS — <page title>".
# - ANIMATION_MS (int | ms): Sidebar slide duration (default: 180; 0 = no animation).
# - INITIAL_CONTENT_WIDTH_PX (int | px): Starting splitter share for the page area; only its ratio to
#   the sidebar width matters (default: 1000).
# - page id (int): A user page's database id (>= 1); SETTINGS_PAGE_ID (0) is the settings page.
# - page_widgets (dict[int, QWidget]): Pages built so far (built on first open, kept until deleted); one
#   dict for the whole workspace, whichever window shows them.
# - workspace: Everything DevOS windows share (Workspace); the first window creates it.
# - _pending_animation_callback (callable | None): Function run once when the running slide finishes.
# - _saved_sidebar_width_px (int | px): Sidebar width remembered on collapse, restored on expand.
# - SPLITTER_HANDLE_WIDTH_PX (int | px): Grab width of the seam between sidebar and pages (default: 4).
# - WINDOW_WIDTH_PX / WINDOW_HEIGHT_PX (int | px): Window size at startup (default: 1200 x 760).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. DESIGN_NOTES
#    1.1 design_notes_docstring
# 2. IMPORTS
#    2.1 imports
# 3. MAIN_WINDOW
#    3.1 class_constants
#    3.2 init_window
#    3.3 build_tab_area
#    3.4 build_nav_sidebar
#    3.5 build_main_splitter
#    3.6 page_provider
#    3.7 page_lifecycle
#    3.8 build_sidebar_animation
#    3.9 collapse_sidebar
#    3.10 expand_sidebar
#    3.11 animation_helpers
#    3.12 title_bar
#    3.13 window_drops_and_close
# 4. WORKSPACE
#    4.1 workspace
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
    | Sidebar   | [open][Page x][Page x]  [_][□][x]|
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
# [2.1] imports
# ==============================================================================
from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import QMainWindow, QSplitter, QToolButton, QVBoxLayout, QWidget

from app.data.activity_log import get_activity_log

from app.data.store.database import get_database
from app.data.store.pages import (
    PAGE_KIND_BROWSER, PAGE_KIND_ICONS, PAGE_KIND_INFINITE_CANVAS, PAGE_KIND_NOUNS, UNTITLED_TITLE, PageStore,
)
from app.ui.pages.browser_page import BrowserPage
from app.ui.pages.canvas_page import CanvasPage
from app.ui.pages.infinite_canvas_page import InfiniteCanvasPage
from app.ui.pages.placeholder_page import PlaceholderPage
from app.ui.tool_bag import ToolBagPopup, active_module, note_pressed
from app.ui.widgets.sidebar import ICON_SIZE, Sidebar, load_icon
from app.ui.widgets.log_panel import LogPanel
from app.ui.widgets.status_bar import SystemStatusBar
from app.ui.widgets.tab_area import TabArea
from app.ui.widgets.tab_group import PAGE_MIME_TYPE, TabGroup, note_tab_drag_over_window
from app.ui.widgets.title_bar import TitleBarDragFilter, WindowControls, WindowFrame, mark_title_bar_region
from app.ui.window_events import mouse_hub
# --- END [2.1] ---


# ==============================================================================
# [3.1] class_constants
# ==============================================================================
class MainWindow(QMainWindow):
    APP_TITLE = "DevOS"   # window title; the open page is added as "DevOS — <page>"
    ANIMATION_MS = 180   # sidebar slide speed; set to 0 to turn the animation off
    WINDOW_WIDTH_PX = 1200
    WINDOW_HEIGHT_PX = 760
    SPLITTER_HANDLE_WIDTH_PX = 4
    INITIAL_CONTENT_WIDTH_PX = 1000
    SETTINGS_PAGE_ID = 0
    LOG_FLUSH_INTERVAL_MS = 15_000         # activity log -> database
    DEFAULT_LOG_HEIGHT_PX = 220
# --- END [3.1] ---

# ==============================================================================
# [3.2] init_window
# ==============================================================================
    def __init__(self, workspace: "Workspace | None" = None) -> None:
        super().__init__()
        self.setWindowTitle(self.APP_TITLE)
        # No system title bar: the sidebar header + top tab rows are the title bar (see [3.12]).
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.resize(self.WINDOW_WIDTH_PX, self.WINDOW_HEIGHT_PX)
        self.setAcceptDrops(True)                     # stray tab drops land here, see [3.13]

        is_first_window = workspace is None
        self.workspace = workspace or Workspace()
        self.workspace.windows.append(self)
        self.database = self.workspace.database
        self.page_store = self.workspace.page_store
        self.page_widgets = self.workspace.page_widgets

        # Order matters: the splitter needs the tab area and sidebar; the tab area talks to the sidebar.
        self._build_tab_area()
        self._build_nav_sidebar()
        self._build_main_splitter()
        self._build_sidebar_animation()
        self._build_title_bar()

        self.log = get_activity_log()
        if self.database.get_preference("log_panel_open", False):
            self.set_log_visible(True)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self.open_tool_bag)

        if is_first_window:
            start_pages = self.page_store.favorites() or self.page_store.pages()
            self.log.info("pages", f"{len(self.page_store.pages())} pages in {len(self.page_store.folders())} folders")
            if start_pages:
                self.show_page(start_pages[0].page_id)
# --- END [3.2] ---

# ==============================================================================
# [3.3] build_tab_area
# ==============================================================================
    def _build_tab_area(self) -> None:
        # --- Tabs on top, pages underneath; this window supplies the pages (see [3.6]) ---
        self.tab_area = TabArea(self, parked_pages_panel=self.workspace.parked_pages_panel)
        self.tab_area.current_page_changed.connect(self._on_current_page_changed)
        self.tab_area.create_page_requested.connect(self.create_page)
        self.tab_area.rename_requested.connect(self.start_rename)
        self.tab_area.page_to_new_window.connect(
            lambda page_id, at_cursor: self.workspace.open_in_new_window(page_id, self, at_cursor))

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
        self.nav_sidebar_panel = Sidebar(self.page_store)
        self.nav_sidebar_panel.page_selected.connect(self.show_page)
        self.nav_sidebar_panel.page_created.connect(self._on_sidebar_page_created)
        self.nav_sidebar_panel.page_renamed.connect(self.workspace.show_title_everywhere)
        self.nav_sidebar_panel.page_deleted.connect(self.workspace.forget_page)
        self.nav_sidebar_panel.settings_requested.connect(lambda: self.show_page(self.SETTINGS_PAGE_ID))
        self.nav_sidebar_panel.close_requested.connect(self.collapse_sidebar)
# --- END [3.4] ---

# ==============================================================================
# [3.5] build_main_splitter
# ==============================================================================
    def _build_main_splitter(self) -> None:
        # --- Splitter: sidebar and tab area are side-by-side tiles with a draggable seam ---
        # Right side: tab panes on top, the System Log docked underneath (hidden until opened).
        self.log_panel = LogPanel()
        self.log_panel.close_requested.connect(lambda: self.set_log_visible(False))
        self.work_splitter = QSplitter(Qt.Orientation.Vertical, objectName="pane_splitter")
        self.work_splitter.setHandleWidth(self.SPLITTER_HANDLE_WIDTH_PX)
        self.work_splitter.addWidget(self.tab_area)
        self.work_splitter.addWidget(self.log_panel)
        self.work_splitter.setChildrenCollapsible(False)
        self.work_splitter.splitterMoved.connect(self._remember_log_height)
        self.log_panel.hide()

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self.nav_sidebar_panel)
        self.main_splitter.addWidget(self.work_splitter)
        self.main_splitter.setCollapsible(0, False)   # the seam can't drag it shut; use the button
        self.main_splitter.setCollapsible(1, False)
        self.main_splitter.setStretchFactor(0, 0)     # window resizes only grow/shrink the content
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setHandleWidth(self.SPLITTER_HANDLE_WIDTH_PX)
        self.main_splitter.setSizes([Sidebar.DEFAULT_WIDTH_PX, self.INITIAL_CONTENT_WIDTH_PX])

        # --- Status bar along the bottom (IDE-style resource tiles) ---
        self.system_status_bar = SystemStatusBar()
        self.system_status_bar.log_toggled.connect(self.set_log_visible)
        self.window_content_panel = QWidget(objectName="window_content_panel")
        content_layout = QVBoxLayout(self.window_content_panel)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.main_splitter, 1)
        content_layout.addWidget(self.system_status_bar)

        # The frame around everything is the resize edge of the frameless window.
        self.window_frame_panel = WindowFrame(self, self.window_content_panel)
        self.setCentralWidget(self.window_frame_panel)
# --- END [3.5] ---

# ==============================================================================
# [3.6] page_provider
# ==============================================================================
    # TabArea asks these three for any page it shows (see tab_area.PageProvider).
    def page_title(self, page_id: int) -> str:
        if page_id == self.SETTINGS_PAGE_ID:
            return "Settings"
        page = self.page_store.page(page_id)
        return page.title if page else "Missing page"

    def page_icon(self, page_id: int) -> str:
        if page_id == self.SETTINGS_PAGE_ID:
            return "settings.svg"
        page = self.page_store.page(page_id)
        return page.icon if page else PAGE_KIND_ICONS["canvas"]

    def page_widget(self, page_id: int) -> QWidget:
        """Build a page the first time it is opened (in any window): the class its kind asks for."""
        if page_id not in self.page_widgets:
            if page_id == self.SETTINGS_PAGE_ID:
                widget = PlaceholderPage("Settings", "Configuration options.")
            else:
                page = self.page_store.page(page_id)
                with get_activity_log().step("pages", f"Open page “{self.page_title(page_id)}”"):
                    widget = self._build_page(page_id, page.kind if page is not None else None)
            self.page_widgets[page_id] = widget
        return self.page_widgets[page_id]

    def _build_page(self, page_id: int, kind: str | None) -> QWidget:
        if kind == PAGE_KIND_BROWSER:
            return self._build_browser_page(page_id)
        if kind == PAGE_KIND_INFINITE_CANVAS:
            widget = InfiniteCanvasPage(page_id, self.page_title(page_id), self.database)
            widget.title_edited.connect(lambda title: self.rename_page(page_id, title))
            return widget
        widget = CanvasPage(page_id, self.page_title(page_id), self.database)
        widget.title_edited.connect(lambda title: self.rename_page(page_id, title))
        widget.kind_requested.connect(lambda new_kind: self.convert_page(page_id, new_kind))
        return widget

    def _build_browser_page(self, page_id: int) -> BrowserPage:
        widget = BrowserPage(page_id, self.page_store.page_state(page_id),
                             save_state=lambda state: self.page_store.save_page_state(page_id, state),
                             open_new_tab=lambda: self._window_for(page_id).open_browser_tab("about:blank"))
        widget.title_changed.connect(lambda title: self.rename_page(page_id, title, is_automatic=True))
        return widget

    def _window_for(self, page_id: int) -> "MainWindow":
        """The window showing this page now (pages move between windows), else this one."""
        return self.workspace.window_with_page(page_id) or self
# --- END [3.6] ---

# ==============================================================================
# [3.7] page_lifecycle
# ==============================================================================
    def show_page(self, page_id: int) -> None:
        """Open a page in a tab (or switch to its tab if it is already open - in another window too)."""
        owner = self.workspace.window_with_page(page_id)
        if owner is not None and owner is not self:
            owner.tab_area.open_page(page_id)
            owner.bring_forward()
            return
        self.tab_area.open_page(page_id)

    def bring_forward(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def create_page(self, tab_group: TabGroup | None = None) -> int:
        """New untitled page, opened in tab_group (or the active pane) with its name ready to type."""
        page_id = self.page_store.create_page()
        self.workspace.refresh_sidebars()
        if tab_group is None:
            self.tab_area.open_page(page_id)
        else:
            self.tab_area.open_page_in(tab_group, page_id)
        self.start_rename(page_id)
        return page_id

    def convert_page(self, page_id: int, kind: str) -> None:
        """New Browser / New Canvas: this (empty) page becomes another kind of page, in the same tab."""
        title = self.page_title(page_id)
        window = self._window_for(page_id)
        with get_activity_log().step("pages", f"Turn “{title}” into a {PAGE_KIND_NOUNS.get(kind, kind)}"):
            self.page_store.set_page_kind(page_id, kind)
            if kind == PAGE_KIND_BROWSER:
                self.page_store.save_page_state(page_id, {"auto_title": title.startswith(UNTITLED_TITLE)})
            widget = self._build_page(page_id, kind)
        old_widget = self.page_widgets.get(page_id)
        self.page_widgets[page_id] = widget
        replaced = window.tab_area.replace_page_widget(page_id, widget)
        for leftover in {old_widget, replaced} - {None}:
            leftover.setParent(None)
            leftover.deleteLater()
        window.tab_area.set_page_icon(page_id, PAGE_KIND_ICONS[kind])
        self.workspace.refresh_sidebars()
        window.nav_sidebar_panel.select_page(page_id)
        widget.start_title_edit()

    def open_browser_tab(self, url: str | None = None) -> BrowserPage:
        """A new browser page in the active pane (links that open a new window land here)."""
        page_id = self.page_store.create_page(kind=PAGE_KIND_BROWSER, state={"url": url} if url else None)
        self.workspace.refresh_sidebars()
        self.tab_area.open_page(page_id)
        return self.page_widget(page_id)

    def _on_sidebar_page_created(self, page_id: int) -> None:
        self.workspace.refresh_sidebars(except_window=self)
        self.show_page(page_id)
        self.start_rename(page_id)

    def start_rename(self, page_id: int) -> None:
        """Focus the page's own title field (a "name it now" prompt)."""
        if page_id == self.SETTINGS_PAGE_ID:
            return
        self._window_for(page_id).show_page(page_id)
        start_edit = getattr(self.page_widget(page_id), "start_title_edit", None)
        if start_edit is not None:
            QTimer.singleShot(0, start_edit)                 # after the tab switch has painted

    def rename_page(self, page_id: int, title: str, is_automatic: bool = False) -> str:
        """Rename a page everywhere: store, sidebar, tab, heading, window title. Automatic renames (a
        browser following its website's title) are logged at debug level."""
        title = self.page_store.rename_page(page_id, title, quiet=is_automatic)
        self.workspace.show_title_everywhere(page_id, title)
        return title

    def _show_title(self, page_id: int, title: str) -> None:
        self.tab_area.set_page_title(page_id, title)
        set_title = getattr(self.page_widgets.get(page_id), "set_title", None)
        if set_title is not None:
            set_title(title)
        if self.tab_area.active_tab_group is not None and self.tab_area.active_tab_group.current_page_index() == page_id:
            self.setWindowTitle(f"{self.APP_TITLE} — {title}")

    def _on_current_page_changed(self, page_id: int) -> None:
        """Keep the sidebar highlight and window title in step with the selected tab."""
        if page_id in (TabArea.NO_PAGE_INDEX, self.SETTINGS_PAGE_ID):
            self.nav_sidebar_panel.clear_selection()
        else:
            self.nav_sidebar_panel.select_page(page_id)
        if page_id == TabArea.NO_PAGE_INDEX:
            self.setWindowTitle(self.APP_TITLE)
        else:
            self.setWindowTitle(f"{self.APP_TITLE} — {self.page_title(page_id)}")


    def set_log_visible(self, is_visible: bool) -> None:
        """Show / hide the System Log docked under the tab panes (it tiles; it can't be dragged)."""
        self.log_panel.setVisible(is_visible)
        self.system_status_bar.set_log_open(is_visible)
        self.database.set_preference("log_panel_open", is_visible)
        if is_visible:
            height = int(self.database.get_preference("log_panel_height", self.DEFAULT_LOG_HEIGHT_PX))
            total = sum(self.work_splitter.sizes()) or self.height()
            self.work_splitter.setSizes([max(total - height, 100), height])

    def open_tool_bag(self) -> None:
        """Ctrl+K: search every tool (global + the active module's) and run one."""
        popup = ToolBagPopup(active_module(), mode="run", parent=self)
        popup.show_at(self.mapToGlobal(QPoint((self.width() - popup.width()) // 2, 60)))

    def _remember_log_height(self) -> None:
        if self.log_panel.isVisible():
            self.database.set_preference("log_panel_height", self.work_splitter.sizes()[1])
# --- END [3.7] ---

# ==============================================================================
# [3.8] build_sidebar_animation
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

# ==============================================================================
# [3.12] title_bar
# ==============================================================================
    def _build_title_bar(self) -> None:
        """Integrated title bar: the sidebar header and the top tab rows act as the window's title bar
        (drag empty space to move, double-click to maximise); window controls sit top-right."""
        self.window_controls = WindowControls(self)
        self.tab_area.add_trailing_widget(self.window_controls)
        mark_title_bar_region(self.nav_sidebar_panel.sidebar_header_panel)
        self._title_bar_drag_filter = TitleBarDragFilter(self)      # listens on mouse_hub() (window_events.py)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        mouse_hub().watch(self)                       # window-level mouse presses: title bar, panes, tiles

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "window_controls"):
            is_filling_screen = self.isMaximized() or self.isFullScreen()
            self.window_controls.sync_state()
            self.window_frame_panel.set_edge_visible(not is_filling_screen)
        elif event.type() == QEvent.Type.ActivationChange and self.isActiveWindow() and len(self.workspace.windows) > 1:
            self.nav_sidebar_panel.refresh()          # folders / favourites may have changed in another window
# --- END [3.12] ---

# ==============================================================================
# [3.13] window_drops_and_close
# ==============================================================================
    # A dragged tab over any part of this window that isn't a pane (sidebar, status bar ...) is "inside":
    # dropping it there does nothing, instead of opening a new window (tab_group.py, "drag over window").
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(PAGE_MIME_TYPE):
            note_tab_drag_over_window(True)
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(PAGE_MIME_TYPE):
            note_tab_drag_over_window(True)
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:
        note_tab_drag_over_window(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat(PAGE_MIME_TYPE):
            event.acceptProposedAction()              # nothing to do: the tab stays where it was
        else:
            super().dropEvent(event)

    def closeEvent(self, event) -> None:
        """A closing window parks its pages (they stop until opened again); other windows carry on."""
        self.tab_area.park_all()
        if self in self.workspace.windows:
            self.workspace.windows.remove(self)
        super().closeEvent(event)
# --- END [3.13] ---


# ==============================================================================
# [4.1] workspace
# ==============================================================================
class Workspace(QObject):
    """What every DevOS window shares: the page store, the page widgets (a page shows in one tab across all
    windows), the hidden panel where pages without a tab wait, and the log flush timer."""

    NEW_WINDOW_OFFSET_PX = 40
    NEW_WINDOW_WIDTH_PX = 1000
    NEW_WINDOW_HEIGHT_PX = 700

    def __init__(self) -> None:
        super().__init__()
        self.database = get_database()
        self.page_store = PageStore(self.database)
        self.page_widgets: dict[int, QWidget] = {}
        self.windows: list[MainWindow] = []
        self.parked_pages_panel = QWidget()           # top-level and never shown: parked pages outlive windows
        self.parked_pages_panel.hide()
        self.log = get_activity_log()
        self.log_flush_timer = QTimer(self, interval=MainWindow.LOG_FLUSH_INTERVAL_MS, timeout=self.log.flush)
        self.log_flush_timer.start()
        mouse_hub().pressed.connect(note_pressed)     # the tool bag runs tools on the module last clicked

    def window_with_page(self, page_id: int) -> MainWindow | None:
        for window in self.windows:
            if page_id in window.tab_area.page_indices():
                return window
        return None

    def open_in_new_window(self, page_id: int, source: MainWindow | None = None, at_cursor: bool = False) -> MainWindow:
        """Move a page into a new DevOS window of its own (it keeps running: no reload)."""
        owner = self.window_with_page(page_id)
        if owner is not None:
            owner.tab_area.release_page(page_id)
        window = MainWindow(self)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        window.resize(min(self.NEW_WINDOW_WIDTH_PX, source.width()) if source else self.NEW_WINDOW_WIDTH_PX,
                      min(self.NEW_WINDOW_HEIGHT_PX, source.height()) if source else self.NEW_WINDOW_HEIGHT_PX)
        if at_cursor:
            window.move(QCursor.pos() - QPoint(120, 18))   # (Wayland compositors place windows themselves)
        elif source is not None:
            window.move(source.pos() + QPoint(self.NEW_WINDOW_OFFSET_PX, self.NEW_WINDOW_OFFSET_PX))
        window.show()
        window.tab_area.open_page(page_id)
        self.log.info("pages", f"Opened “{window.page_title(page_id)}” in a new window")
        return window

    def refresh_sidebars(self, except_window: MainWindow | None = None) -> None:
        for window in self.windows:
            if window is not except_window:
                window.nav_sidebar_panel.refresh()

    def show_title_everywhere(self, page_id: int, title: str) -> None:
        """A page was renamed: every window's sidebar, tab, page heading and window title."""
        for window in self.windows:
            window.nav_sidebar_panel.set_page_title(page_id, title)
            window._show_title(page_id, title)

    def forget_page(self, page_id: int) -> None:
        """A page was deleted (from some sidebar): close its tab wherever it is, dispose of its widget."""
        for window in self.windows:
            window.tab_area.forget_page(page_id)
        widget = self.page_widgets.pop(page_id, None)
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        self.refresh_sidebars()
# --- END [4.1] ---
