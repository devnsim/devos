# ==============================================================================
# FILE: app/ui/pages/canvas_page.py
# OVERVIEW: THE page template. Every user page is a CanvasPage: an editable title above a module
#           canvas where module apps tile, move and resize. Change this class to change every page.
# PURPOSE:
#   1. PageTitleEdit: the page name, shown as a heading; click (or F2 / Rename) to edit, Enter or
#      clicking away saves, Esc cancels.
#   2. A ModuleCanvas keyed by the page id, so each page keeps its own saved layout and modules.
#   3. New (empty) pages show a "Create New" panel: New Module (an empty tile with the module dropdown)
#      or New Browser (MainWindow turns the page into a browser page). Closing the last module brings
#      the panel back.
# CONTRACT:
#   - Requires: app.ui.canvas.module_canvas.ModuleCanvas, app.data.store.pages.canvas_key,
#               app.data.store.database.Database, app.ui.widgets.sidebar.load_icon, QSS selectors
#               #page_title_edit, #create_new_panel, #create_new_label, #new_module_btn, #new_browser_btn
#   - Exposes: CanvasPage(page_id, title, database) (QWidget) with signals title_edited(str), browser_requested();
#              set_title(title); start_title_edit(); add_first_module(); module_canvas; create_new_panel;
#              page_id; PageTitleEdit
# NOTES:
#   - [2026-11-16] The canvas shows as soon as it holds a tile, however it got there (not only via New Module).
#   - [2026-11-16] New pages are empty: a centred "Create New" panel with New Module / New Browser instead
#                  of an automatic empty module tile. The panel returns when the last module is closed.
#   - [2026-11-15] Created from ProjectEditorPage, which it replaces: the Project Editor is now just a
#                  user page made from this template.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - page template: The one page class all user pages share (this file).
# - PAGE_MARGIN_PX (int | px): Empty space around the page content (default: 8).
# - Create New panel: What an empty page shows - New Module / New Browser.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. PAGE_TITLE_EDIT
#    2.1 page_title_edit
# 3. CANVAS_PAGE
#    3.1 canvas_page
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from app.data.store.database import Database
from app.data.store.pages import canvas_key
from app.ui.canvas.module_canvas import ModuleCanvas
from app.ui.widgets.sidebar import load_icon
# --- END [1.1] ---


# ==============================================================================
# [2.1] page_title_edit
# ==============================================================================
class PageTitleEdit(QLineEdit):
    """A heading that becomes a text box when clicked. Emits title_edited when a new name is committed."""

    title_edited = Signal(str)

    def __init__(self, title: str) -> None:
        super().__init__(title, objectName="page_title_edit")
        self.setFrame(False)
        self.setPlaceholderText("Untitled")
        self.setToolTip("Click to rename this page")
        self._committed_title = title
        self.editingFinished.connect(self._commit)

    def set_title(self, title: str) -> None:
        """Show a name set elsewhere (sidebar, tab). Always wins: the name was just committed there."""
        self._committed_title = title
        self.setText(title)

    def start_edit(self) -> None:
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.selectAll()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.setText(self._committed_title)
            self.clearFocus()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clearFocus()                        # -> editingFinished -> _commit
            return
        super().keyPressEvent(event)

    def _commit(self) -> None:
        title = self.text().strip()
        if title and title != self._committed_title:
            self._committed_title = title
            self.title_edited.emit(title)
        elif not title:
            self.setText(self._committed_title)
# --- END [2.1] ---


# ==============================================================================
# [3.1] canvas_page
# ==============================================================================
class CanvasPage(QWidget):
    """Page template: editable title + module canvas (see file header)."""

    title_edited = Signal(str)
    browser_requested = Signal()                    # "New Browser": MainWindow replaces this page

    PAGE_MARGIN_PX = 8
    SECTION_SPACING_PX = 6

    def __init__(self, page_id: int, title: str, database: Database) -> None:
        super().__init__(objectName="canvas_page_panel")
        self.page_id = page_id
        layout = QVBoxLayout(self)
        layout.setContentsMargins(self.PAGE_MARGIN_PX, self.PAGE_MARGIN_PX, self.PAGE_MARGIN_PX, self.PAGE_MARGIN_PX)
        layout.setSpacing(self.SECTION_SPACING_PX)

        self.page_title_edit = PageTitleEdit(title)
        self.page_title_edit.title_edited.connect(self.title_edited)
        layout.addWidget(self.page_title_edit)

        self.create_new_panel = self._build_create_new_panel()
        layout.addWidget(self.create_new_panel, 1)
        self.module_canvas = ModuleCanvas(database, canvas_key(page_id))
        self.module_canvas.emptied.connect(self._show_create_new)
        self.module_canvas.layout_changed.connect(self._show_canvas_if_filled)   # modules added from code too
        layout.addWidget(self.module_canvas, 1)
        if self.module_canvas.load_saved_layout():
            self.create_new_panel.hide()
        else:
            self._show_create_new()

    def _build_create_new_panel(self) -> QWidget:
        panel = QWidget(objectName="create_new_panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setSpacing(10)
        panel_layout.addStretch()
        panel_layout.addWidget(QLabel("Create New", objectName="create_new_label"), 0, Qt.AlignmentFlag.AlignHCenter)
        self.new_module_btn = QPushButton("New Module", objectName="new_module_btn",
                                          toolTip="Add a module tile, then choose what it shows")
        self.new_module_btn.setIcon(load_icon("plus.svg"))
        self.new_module_btn.clicked.connect(self.add_first_module)
        self.new_browser_btn = QPushButton("New Browser", objectName="new_browser_btn",
                                           toolTip="Turn this page into a web browser")
        self.new_browser_btn.setIcon(load_icon("globe.svg"))
        self.new_browser_btn.clicked.connect(self.browser_requested)
        for button in (self.new_module_btn, self.new_browser_btn):
            button.setIconSize(QSize(16, 16))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            panel_layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        panel_layout.addStretch()
        return panel

    def add_first_module(self) -> None:
        """New Module: an empty module tile with the module dropdown."""
        self._show_canvas()
        self.module_canvas.add_module()

    def _show_canvas(self) -> None:
        self.create_new_panel.hide()
        self.module_canvas.show()

    def _show_canvas_if_filled(self) -> None:
        if self.module_canvas.tiles and self.create_new_panel.isVisible():
            self._show_canvas()

    def _show_create_new(self) -> None:
        self.module_canvas.hide()
        self.create_new_panel.show()

    def set_title(self, title: str) -> None:
        self.page_title_edit.set_title(title)

    def start_title_edit(self) -> None:
        self.page_title_edit.start_edit()
# --- END [3.1] ---
