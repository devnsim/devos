# ==============================================================================
# FILE: app/ui/pages/cost_dashboard_page.py
# OVERVIEW: The Cost Dashboard page, whose layout is drawn in Qt Designer.
# PURPOSE:
#   1. Finds the Designer file cost_dashboard.ui in app/ui/forms/.
#   2. Builds the page's widgets from that file when DevOS starts.
#   3. Lets code find any widget in the form by its objectName, to connect buttons later.
# CONTRACT:
#   - Requires: app/ui/forms/cost_dashboard.ui (made in Qt Designer, top level = Widget, with a layout),
#               PySide6.QtUiTools (QUiLoader)
#   - Exposes: CostDashboardPage (QWidget), find_form_widget(object_name) -> QWidget | None
# NOTES:
#   - [2026-11-14] Renamed Cost_Tracker.py -> cost_dashboard_page.py, class Cost_Dashboard ->
#                  CostDashboardPage, form Cost_Dashboard.ui -> cost_dashboard.ui.
#   - [2026-11-14] FORM_FILE_NAME must match the .ui file name exactly; Linux is case-sensitive
#                  and "-" vs "_" counts (this bit us once already).
#   - [2026-11-14] Raises FileNotFoundError (with the full path) at startup if the form is missing.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - form_file_path (Path): Full path to the .ui file = FORMS_DIRECTORY / FORM_FILE_NAME.
# - form_panel (QWidget): Root widget built from the .ui file; holds every widget in the form.
# - FORM_FILE_NAME (str): Name of this page's Designer file (default: "cost_dashboard.ui").
# - FORMS_DIRECTORY (Path): app/ui/forms/, found relative to this file.
# - object_name (str): A widget's objectName as set in Designer's Property Editor.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. DESIGN_NOTES
#    1.1 design_notes_docstring
# 2. IMPORTS_AND_CONSTANTS
#    2.1 imports
#    2.2 form_location
# 3. COST_DASHBOARD_PAGE
#    3.1 init_page
#    3.2 find_form_widget
# ==============================================================================

# ==============================================================================
# [1.1] design_notes_docstring
# ==============================================================================
"""A page whose layout comes from a Qt Designer .ui file."""
# --- END [1.1] ---

# ==============================================================================
# [2.1] imports
# ==============================================================================
from pathlib import Path

from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QVBoxLayout, QWidget
# --- END [2.1] ---

# ==============================================================================
# [2.2] form_location
# ==============================================================================
FORMS_DIRECTORY = Path(__file__).parent.parent / "forms"
FORM_FILE_NAME = "cost_dashboard.ui"
# --- END [2.2] ---


# ==============================================================================
# [3.1] init_page
# ==============================================================================
class CostDashboardPage(QWidget):
    def __init__(self) -> None:
        super().__init__()

        form_file_path = FORMS_DIRECTORY / FORM_FILE_NAME
        if not form_file_path.exists():
            raise FileNotFoundError(f"form not found: {form_file_path}")

        self.form_panel = QUiLoader().load(str(form_file_path))

        self.page_layout = QVBoxLayout(self)
        self.page_layout.setContentsMargins(0, 0, 0, 0)
        self.page_layout.addWidget(self.form_panel)
# --- END [3.1] ---

# ==============================================================================
# [3.2] find_form_widget
# ==============================================================================
    def find_form_widget(self, object_name: str) -> QWidget | None:
        """Return the widget in the form with this objectName, or None if there isn't one."""
        return self.form_panel.findChild(QWidget, object_name)
# --- END [3.2] ---
