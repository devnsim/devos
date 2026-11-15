# ==============================================================================
# FILE: app/ui/pages/project_editor_page.py
# OVERVIEW: The Project Manager › Project Editor page: a module canvas filling the page.
# PURPOSE:
#   1. Shows the page title.
#   2. Hosts a ModuleCanvas where module apps tile, move and resize; the layout is saved in SQLite
#      and restored on start (first run: the RF Imaging inspector).
# CONTRACT:
#   - Requires: app.ui.canvas.module_canvas.ModuleCanvas,
#               app.data.store.database.get_database, QSS selector #page_title_label
#   - Exposes: ProjectEditorPage (QWidget), module_canvas attribute; PAGE_KEY
# NOTES:
#   - [2026-11-15] Created; replaces the PlaceholderPage for Project Editor.
#   - [2026-11-15] First module is the Tensor Inspector (live demo Tensor4D) instead of an empty one.
#   - [2026-11-15] First module is now the RF Imaging inspector (2.5-6 GHz mock sensor); the generic
#                  TensorInspectorModule remains for Tensor4D.inspect().
#   - [2026-11-15] Modules now come from the library (app/modules/registry.py) through the canvas;
#                  the page only restores the saved layout or places the default module.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - PAGE_MARGIN_PX (int | px): Empty space around the page content on all sides (default: 8).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. PROJECT_EDITOR_PAGE
#    2.1 class_constants
#    2.2 init_page
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.ui.canvas.module_canvas import ModuleCanvas
from app.data.store.database import get_database
# --- END [1.1] ---


# ==============================================================================
# [2.1] class_constants
# ==============================================================================
class ProjectEditorPage(QWidget):
    """Project Editor: a tiling canvas of modules."""

    PAGE_MARGIN_PX = 8
    SECTION_SPACING_PX = 6
    TITLE_TEXT = "Project Editor"
    PAGE_KEY = "project_editor"
    DEFAULT_MODULE_ID = "rf_imaging"
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
        self.page_layout.addWidget(self.page_title_label)

        self.module_canvas = ModuleCanvas(get_database(), self.PAGE_KEY)
        if not self.module_canvas.load_saved_layout():
            self.module_canvas.add_library_module(self.DEFAULT_MODULE_ID)
        self.page_layout.addWidget(self.module_canvas, 1)
# --- END [2.2] ---
