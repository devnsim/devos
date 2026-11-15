# ==============================================================================
# FILE: app/ui/pages/overview_page.py
# OVERVIEW: The Trading Dashboard › Overview page, a host for dashboard modules.
# PURPOSE:
#   1. Shows the page title and subtitle.
#   2. Hosts the dashboard modules (currently: CalendarModule) in a module grid.
# CONTRACT:
#   - Requires: app.modules.calendar.view.CalendarModule,
#               QSS selectors #page_title_label, #page_subtitle_label in app/ui/theme.py
#   - Exposes: OverviewPage (QWidget), calendar_module attribute
# NOTES:
#   - [2026-11-15] Created; replaces the PlaceholderPage for Overview. Modules sit in a QGridLayout
#                  (module_grid_layout) as a stepping stone to the free snap-grid canvas.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - MODULE_SPACING_PX (int | px): Gap between modules in the grid (default: 4).
# - module_grid_layout (QGridLayout): Rows/columns of modules; empty columns/rows absorb spare space.
# - PAGE_MARGIN_PX (int | px): Empty space around the page content on all sides (default: 8).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 pyside_imports
#    1.2 devos_imports
# 2. OVERVIEW_PAGE
#    2.1 class_constants
#    2.2 init_page
#    2.3 build_modules
# ==============================================================================

# ==============================================================================
# [1.1] pyside_imports
# ==============================================================================
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget
# --- END [1.1] ---

# ==============================================================================
# [1.2] devos_imports
# ==============================================================================
from app.modules.calendar.view import CalendarModule
# --- END [1.2] ---


# ==============================================================================
# [2.1] class_constants
# ==============================================================================
class OverviewPage(QWidget):
    """Trading Dashboard overview: a grid of dashboard modules."""

    PAGE_MARGIN_PX = 8
    SECTION_SPACING_PX = 6
    MODULE_SPACING_PX = 4
    TITLE_TEXT = "Overview"
    SUBTITLE_TEXT = "Trading Dashboard"
# --- END [2.1] ---

# ==============================================================================
# [2.2] init_page
# ==============================================================================
    def __init__(self) -> None:
        super().__init__()
        self.page_layout = QVBoxLayout(self)
        self.page_layout.setContentsMargins(
            self.PAGE_MARGIN_PX, self.PAGE_MARGIN_PX, self.PAGE_MARGIN_PX, self.PAGE_MARGIN_PX
        )
        self.page_layout.setSpacing(self.SECTION_SPACING_PX)

        self.page_title_label = QLabel(self.TITLE_TEXT, objectName="page_title_label")
        self.page_subtitle_label = QLabel(self.SUBTITLE_TEXT, objectName="page_subtitle_label")
        self.page_layout.addWidget(self.page_title_label)
        self.page_layout.addWidget(self.page_subtitle_label)

        self._build_modules()
# --- END [2.2] ---

# ==============================================================================
# [2.3] build_modules
# ==============================================================================
    def _build_modules(self) -> None:
        self.module_grid_layout = QGridLayout()
        self.module_grid_layout.setSpacing(self.MODULE_SPACING_PX)

        self.calendar_module = CalendarModule()
        self.module_grid_layout.addWidget(self.calendar_module, 0, 0)

        # Column 1 / row 1 are empty and stretch, so the calendar keeps a compact size
        # in the top-left until more modules fill the grid.
        self.module_grid_layout.setColumnStretch(0, 2)
        self.module_grid_layout.setColumnStretch(1, 3)
        self.module_grid_layout.setRowStretch(0, 3)
        self.module_grid_layout.setRowStretch(1, 2)
        self.page_layout.addLayout(self.module_grid_layout, 1)
# --- END [2.3] ---
