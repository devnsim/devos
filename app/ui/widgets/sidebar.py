# ==============================================================================
# FILE: app/ui/widgets/sidebar.py
# OVERVIEW: The collapsible navigation sidebar: app title, close button and a list of apps.
# PURPOSE:
#   1. Shows the "DevOS" title and a button that asks the main window to close the sidebar.
#   2. Shows section labels ("Home", "Workbenches") with clickable items (icon + name) under each.
#   3. Tells the main window which app was clicked, and highlights the open one.
#   4. Loads SVG icons from assets/icons/ for itself and for main_window.py.
# CONTRACT:
#   - Requires: SVG icons in assets/icons/ (sidebar_close.svg + one per app),
#               QSS selectors #nav_sidebar_panel, #sidebar_header_panel, #app_title_label,
#               #sidebar_section_label, #sidebar_folder_btn, #sidebar_item_btn, #close_sidebar_btn
#               in app/ui/theme.py
#   - Exposes: Sidebar (QWidget) with signals page_selected(int), close_requested();
#              select_page(page_index); clear_selection(); MIN/MAX/DEFAULT_WIDTH_PX; load_icon(icon_file_name);
#              ICON_SIZE; ICONS_DIRECTORY
# NOTES:
#   - [2026-11-14] Renamed to the standard: select() -> select_page(), *_WIDTH -> *_WIDTH_PX,
#                  objectNames to snake_case; icon files sidebar-close/open.svg -> sidebar_close/open.svg.
#   - [2026-11-14] All app items share objectName "sidebar_item_btn" (reusable-widget rule) so one
#                  QSS selector styles every item.
#   - [2026-11-14] WA_StyledBackground is required: a QWidget *subclass* ignores its QSS background
#                  without it.
#   - [2026-11-14] load_icon() returns an empty QIcon and prints a warning when a file is missing,
#                  instead of crashing.
#   - [2026-11-15] Single "Menu" label replaced by per-item section titles: nav_items is now
#                  (section_title, item_title, icon_file_name); a label is added when the section changes.
#   - [2026-11-15] Added collapsible folders: nav_items is now (section_title, folder_title,
#                  item_title, icon_file_name). Folders start collapsed; select_page() opens the folder
#                  of the item it highlights. Nested items carry the QSS property is_nested=true.
#   - [2026-11-15] Added clear_selection() so no item is highlighted while no tab is open.
#   - [2026-11-15] Folders can nest: the folder field is now a folder_path. Rows carry the QSS
#                  property nest_level (replaces is_nested); a row shows only if all its folders are open.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - DEFAULT_WIDTH_PX (int | px): Sidebar width at startup (default: 220).
# - ICON_SIZE (QSize | px): Drawn size of every sidebar / top-bar icon (default: 16 x 16).
# - ICONS_DIRECTORY (Path): devos/assets/icons/; parents[3] of this file is the project root.
# - icon_file_name (str): SVG file name inside ICONS_DIRECTORY, e.g. "home.svg".
# - MIN_WIDTH_PX / MAX_WIDTH_PX (int | px): Limits when dragging the seam (default: 160 / 400).
# - folder_path (tuple[str, ...] | str): Folders an item sits in, outermost first, e.g.
#   ("Finance", "Trading Dashboard"). A plain string means one folder; "" or () = directly under the section.
# - folder_title (str): Name of one folder row, e.g. "Finance".
# - nest_level (str): QSS property on every row: how many folders deep it is ("0", "1", "2", ...).
# - FOLDER_CLOSED_ARROW / FOLDER_OPEN_ARROW (str): Arrow drawn before a folder's name ("▸" / "▾").
# - nav_items (list[tuple]): One entry per app, in sidebar order:
#   {section_title, folder_path, item_title, icon_file_name}. Items of one section, and of one
#   folder, must be next to each other.
# - section_title (str): Label shown above a group of items, e.g. "Home", "Workbenches".
# - page_index (int): Position of an app in nav_items; same index as MainWindow.page_entries.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. DESIGN_NOTES
#    1.1 design_notes_docstring
# 2. IMPORTS_AND_CONSTANTS
#    2.1 imports
#    2.2 icon_constants
# 3. ICON_LOADING
#    3.1 load_icon
# 4. SIDEBAR
#    4.1 class_signals_and_constants
#    4.2 init_sidebar
#    4.3 build_header
#    4.4 build_nav_items
#    4.5 select_page
#    4.6 folders
# ==============================================================================

# ==============================================================================
# [1.1] design_notes_docstring
# ==============================================================================
"""Collapsible navigation sidebar.

Layout:
    [ DevOS                 [close] ]   <- header
    Home                                <- section label
    [icon] Home                         <- one button per application
    [icon] Settings
    Workbenches                         <- section label
    ▸  Project Manager
        [icon] Project Editor
    ▾  Finance                          <- folder row: click to show/hide what's inside
        ▾  Trading Dashboard            <- folders can nest
            [icon] Overview             <- nested items (hidden while any folder above is closed)
            [icon] Market Lab
            ...

Icons are SVG files in devos/assets/icons/. Change which icon an app uses
in the `self.page_entries` list in main_window.py.
"""
# --- END [1.1] ---

# ==============================================================================
# [2.1] imports
# ==============================================================================
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget,
)
# --- END [2.1] ---

# ==============================================================================
# [2.2] icon_constants
# ==============================================================================
# This file is devos/app/ui/widgets/sidebar.py -> parents[3] is devos/
ICONS_DIRECTORY = Path(__file__).resolve().parents[3] / "assets" / "icons"
ICON_SIZE = QSize(16, 16)
# --- END [2.2] ---


# ==============================================================================
# [3.1] load_icon
# ==============================================================================
def load_icon(icon_file_name: str) -> QIcon:
    """Load an icon from assets/icons/. Missing files give an empty icon instead of crashing."""
    icon_file_path = ICONS_DIRECTORY / icon_file_name
    if not icon_file_path.exists():
        print(f"[DevOS] icon not found: {icon_file_path}")
        return QIcon()
    return QIcon(str(icon_file_path))
# --- END [3.1] ---


# ==============================================================================
# [4.1] class_signals_and_constants
# ==============================================================================
class Sidebar(QWidget):
    page_selected = Signal(int)     # emitted with the index of the clicked app
    close_requested = Signal()      # emitted when the close button is pressed

    MIN_WIDTH_PX = 160              # drag limits for the seam
    MAX_WIDTH_PX = 400
    DEFAULT_WIDTH_PX = 220

    ITEM_SPACING_PX = 1
    BOTTOM_MARGIN_PX = 8

    FOLDER_CLOSED_ARROW = "▸"
    FOLDER_OPEN_ARROW = "▾"
# --- END [4.1] ---

# ==============================================================================
# [4.2] init_sidebar
# ==============================================================================
    def __init__(self, nav_items: list[tuple], parent: QWidget | None = None) -> None:
        """nav_items: list of (section title, folder path, item title, icon file name)."""
        super().__init__(parent)
        self.setObjectName("nav_sidebar_panel")
        # Needed so a QWidget *subclass* paints its stylesheet background
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(self.MIN_WIDTH_PX)
        self.setMaximumWidth(self.MAX_WIDTH_PX)

        self.sidebar_layout = QVBoxLayout(self)
        self.sidebar_layout.setContentsMargins(0, 0, 0, self.BOTTOM_MARGIN_PX)
        self.sidebar_layout.setSpacing(self.ITEM_SPACING_PX)

        self._build_header()
        self._build_nav_items(nav_items)
# --- END [4.2] ---

# ==============================================================================
# [4.3] build_header
# ==============================================================================
    def _build_header(self) -> None:
        # --- Header: app name + close button ---
        self.sidebar_header_panel = QWidget(objectName="sidebar_header_panel")
        header_layout = QHBoxLayout(self.sidebar_header_panel)
        header_layout.setContentsMargins(12, 6, 6, 6)

        self.app_title_label = QLabel("DevOS", objectName="app_title_label")
        header_layout.addWidget(self.app_title_label)
        header_layout.addStretch()

        self.close_sidebar_btn = QToolButton(objectName="close_sidebar_btn")
        self.close_sidebar_btn.setIcon(load_icon("sidebar_close.svg"))
        self.close_sidebar_btn.setIconSize(ICON_SIZE)
        self.close_sidebar_btn.setToolTip("Close sidebar")
        self.close_sidebar_btn.setAutoRaise(True)
        self.close_sidebar_btn.clicked.connect(lambda: self.close_requested.emit())
        header_layout.addWidget(self.close_sidebar_btn)

        self.sidebar_layout.addWidget(self.sidebar_header_panel)
# --- END [4.3] ---

# ==============================================================================
# [4.4] build_nav_items
# ==============================================================================
    def _build_nav_items(self, nav_items: list[tuple]) -> None:
        # --- Sections, folders and items ---
        # A section label starts whenever section_title changes. folder_path lists the folders an
        # item sits in, outermost first; a folder row is added for every level that differs from
        # the previous item's path. Every row remembers its parent folder for show/hide.
        self.sidebar_section_labels: list[QLabel] = []
        self.parent_folder_btns: dict[QPushButton, QPushButton | None] = {}   # row -> parent folder
        self.page_folder_btns: dict[int, QPushButton] = {}                    # page_index -> folder
        current_section_title = None
        open_folder_btns: list[QPushButton] = []    # folder rows of the previous item, outermost first

        self.nav_item_group = QButtonGroup(self)
        self.nav_item_group.setExclusive(True)
        for page_index, (section_title, folder_path, item_title, icon_file_name) in enumerate(nav_items):
            folder_path = self._as_folder_path(folder_path)

            if section_title != current_section_title:
                sidebar_section_label = QLabel(section_title, objectName="sidebar_section_label")
                self.sidebar_section_labels.append(sidebar_section_label)
                self.sidebar_layout.addWidget(sidebar_section_label)
                current_section_title = section_title
                open_folder_btns = []

            # Keep the folder rows this item shares with the previous one; build the rest.
            shared_depth = 0
            while (
                shared_depth < min(len(open_folder_btns), len(folder_path))
                and open_folder_btns[shared_depth].property("folder_title") == folder_path[shared_depth]
            ):
                shared_depth += 1
            open_folder_btns = open_folder_btns[:shared_depth]
            for depth in range(shared_depth, len(folder_path)):
                parent_folder_btn = open_folder_btns[-1] if open_folder_btns else None
                open_folder_btns.append(self._build_folder_btn(folder_path[depth], depth, parent_folder_btn))

            sidebar_item_btn = QPushButton(item_title, objectName="sidebar_item_btn")
            sidebar_item_btn.setCheckable(True)
            sidebar_item_btn.setIcon(load_icon(icon_file_name))
            sidebar_item_btn.setIconSize(ICON_SIZE)
            sidebar_item_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            sidebar_item_btn.setProperty("nest_level", str(len(folder_path)))   # QSS indent
            self.nav_item_group.addButton(sidebar_item_btn, page_index)
            self.sidebar_layout.addWidget(sidebar_item_btn)

            parent_folder_btn = open_folder_btns[-1] if open_folder_btns else None
            self.parent_folder_btns[sidebar_item_btn] = parent_folder_btn
            if parent_folder_btn is not None:
                self.page_folder_btns[page_index] = parent_folder_btn

        self.sidebar_layout.addStretch()
        self.nav_item_group.idClicked.connect(lambda page_index: self.page_selected.emit(page_index))
        self._refresh_row_visibility()               # folders start collapsed
        if nav_items:
            self.select_page(0)

    @staticmethod
    def _as_folder_path(folder_path) -> tuple[str, ...]:
        """Accept "" (no folder), "Finance" (one folder) or ("Finance", "Trading Dashboard")."""
        if isinstance(folder_path, str):
            return (folder_path,) if folder_path else ()
        return tuple(folder_path)
# --- END [4.4] ---

# ==============================================================================
# [4.5] select_page
# ==============================================================================
    def select_page(self, page_index: int) -> None:
        """Highlight an item from code (does not emit page_selected). Opens its folders if closed."""
        sidebar_item_btn = self.nav_item_group.button(page_index)
        if sidebar_item_btn is None:
            return
        folder_btn = self.page_folder_btns.get(page_index)
        while folder_btn is not None:
            folder_btn.setChecked(True)
            folder_btn = self.parent_folder_btns[folder_btn]
        sidebar_item_btn.setChecked(True)

    def clear_selection(self) -> None:
        """Remove the highlight from every item (used when no tab is open)."""
        self.nav_item_group.setExclusive(False)    # an exclusive group won't let the last item uncheck
        for sidebar_item_btn in self.nav_item_group.buttons():
            sidebar_item_btn.setChecked(False)
        self.nav_item_group.setExclusive(True)
# --- END [4.5] ---

# ==============================================================================
# [4.6] folders
# ==============================================================================
    def _build_folder_btn(
        self, folder_title: str, depth: int, parent_folder_btn: QPushButton | None
    ) -> QPushButton:
        """Add a collapsible folder row. Checked = expanded. Starts collapsed."""
        sidebar_folder_btn = QPushButton(objectName="sidebar_folder_btn")
        sidebar_folder_btn.setProperty("folder_title", folder_title)
        sidebar_folder_btn.setProperty("nest_level", str(depth))   # QSS indent
        sidebar_folder_btn.setCheckable(True)
        sidebar_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sidebar_folder_btn.toggled.connect(lambda: self._on_folder_toggled(sidebar_folder_btn))
        self.parent_folder_btns[sidebar_folder_btn] = parent_folder_btn
        self.sidebar_layout.addWidget(sidebar_folder_btn)
        self._update_folder_arrow(sidebar_folder_btn)
        return sidebar_folder_btn

    def _on_folder_toggled(self, sidebar_folder_btn: QPushButton) -> None:
        self._update_folder_arrow(sidebar_folder_btn)
        self._refresh_row_visibility()

    def _update_folder_arrow(self, sidebar_folder_btn: QPushButton) -> None:
        arrow_text = self.FOLDER_OPEN_ARROW if sidebar_folder_btn.isChecked() else self.FOLDER_CLOSED_ARROW
        sidebar_folder_btn.setText(f"{arrow_text}  {sidebar_folder_btn.property('folder_title')}")

    def _refresh_row_visibility(self) -> None:
        """A row shows only when every folder above it is expanded."""
        for row_btn in self.parent_folder_btns:
            row_btn.setVisible(self._are_parents_expanded(row_btn))

    def _are_parents_expanded(self, row_btn: QPushButton) -> bool:
        parent_folder_btn = self.parent_folder_btns[row_btn]
        while parent_folder_btn is not None:
            if not parent_folder_btn.isChecked():
                return False
            parent_folder_btn = self.parent_folder_btns[parent_folder_btn]
        return True
# --- END [4.6] ---
