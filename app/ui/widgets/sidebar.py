# ==============================================================================
# FILE: app/ui/widgets/sidebar.py
# OVERVIEW: The collapsible navigation sidebar: app title, close button and a list of apps.
# PURPOSE:
#   1. Shows the "DevOS" title and a button that asks the main window to close the sidebar.
#   2. Shows a "Menu" label and one clickable item (icon + name) per app.
#   3. Tells the main window which app was clicked, and highlights the open one.
#   4. Loads SVG icons from assets/icons/ for itself and for main_window.py.
# CONTRACT:
#   - Requires: SVG icons in assets/icons/ (sidebar_close.svg + one per app),
#               QSS selectors #nav_sidebar_panel, #sidebar_header_panel, #app_title_label,
#               #sidebar_section_label, #sidebar_item_btn, #close_sidebar_btn in app/ui/theme.py
#   - Exposes: Sidebar (QWidget) with signals page_selected(int), close_requested();
#              select_page(page_index); MIN/MAX/DEFAULT_WIDTH_PX; load_icon(icon_file_name);
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
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - DEFAULT_WIDTH_PX (int | px): Sidebar width at startup (default: 220).
# - ICON_SIZE (QSize | px): Drawn size of every sidebar / top-bar icon (default: 16 x 16).
# - ICONS_DIRECTORY (Path): devos/assets/icons/; parents[3] of this file is the project root.
# - icon_file_name (str): SVG file name inside ICONS_DIRECTORY, e.g. "home.svg".
# - MIN_WIDTH_PX / MAX_WIDTH_PX (int | px): Limits when dragging the seam (default: 160 / 400).
# - nav_items (list[tuple[str, str]]): One entry per app, in sidebar order: {item_title, icon_file_name}.
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
# ==============================================================================

# ==============================================================================
# [1.1] design_notes_docstring
# ==============================================================================
"""Collapsible navigation sidebar.

Layout:
    [ DevOS                 [close] ]   <- header
    Menu                                <- section label
    [icon] Home                         <- one button per application
    [icon] Cost Tracking
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
# --- END [4.1] ---

# ==============================================================================
# [4.2] init_sidebar
# ==============================================================================
    def __init__(self, nav_items: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        """nav_items: list of (title, icon file name)."""
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
    def _build_nav_items(self, nav_items: list[tuple[str, str]]) -> None:
        # --- "Menu" section ---
        self.sidebar_section_label = QLabel("Menu", objectName="sidebar_section_label")
        self.sidebar_layout.addWidget(self.sidebar_section_label)

        self.nav_item_group = QButtonGroup(self)
        self.nav_item_group.setExclusive(True)
        for page_index, (item_title, icon_file_name) in enumerate(nav_items):
            sidebar_item_btn = QPushButton(item_title, objectName="sidebar_item_btn")
            sidebar_item_btn.setCheckable(True)
            sidebar_item_btn.setIcon(load_icon(icon_file_name))
            sidebar_item_btn.setIconSize(ICON_SIZE)
            sidebar_item_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.nav_item_group.addButton(sidebar_item_btn, page_index)
            self.sidebar_layout.addWidget(sidebar_item_btn)

        self.sidebar_layout.addStretch()
        self.nav_item_group.idClicked.connect(lambda page_index: self.page_selected.emit(page_index))
        if nav_items:
            self.nav_item_group.button(0).setChecked(True)
# --- END [4.4] ---

# ==============================================================================
# [4.5] select_page
# ==============================================================================
    def select_page(self, page_index: int) -> None:
        """Highlight an item from code (does not emit page_selected)."""
        sidebar_item_btn = self.nav_item_group.button(page_index)
        if sidebar_item_btn is not None:
            sidebar_item_btn.setChecked(True)
# --- END [4.5] ---
