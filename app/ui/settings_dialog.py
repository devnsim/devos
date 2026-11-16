# ==============================================================================
# FILE: app/ui/settings_dialog.py
# OVERVIEW: The reusable settings window tools open from their gear icon (Graph, Financial Chart, ...).
# PURPOSE:
#   1. A separate, non-modal window titled "<tool> settings" with tabs.
#   2. add_form_tab(title) -> QFormLayout for label / field rows; add_tab(title, widget) for anything else.
#   3. Apply / Close buttons; applied() signal so the tool re-reads the fields.
# CONTRACT:
#   - Requires: PySide6, QSS selector #settings_dialog
#   - Exposes: SettingsDialog(parent, title) with signal applied(); add_form_tab(); add_tab(); tabs
# NOTES:
#   - [2026-11-15] Created for the Graph and Financial Chart tools (build once, reuse).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - non-modal: The window stays open while the user keeps working in the main window.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. DIALOG
#    1.1 settings_dialog
# ==============================================================================

# ==============================================================================
# [1.1] settings_dialog
# ==============================================================================
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QTabWidget, QVBoxLayout, QWidget


class SettingsDialog(QDialog):
    applied = Signal()

    def __init__(self, parent: QWidget | None, title: str) -> None:
        super().__init__(parent, objectName="settings_dialog")
        self.setWindowTitle(f"{title} settings")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.Tool, True)                 # stays above the main window
        self.resize(460, 520)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.applied)
        buttons.button(QDialogButtonBox.StandardButton.Ok).clicked.connect(lambda: (self.applied.emit(), self.hide()))
        buttons.rejected.connect(self.hide)
        layout.addWidget(buttons)

    def add_form_tab(self, title: str) -> QFormLayout:
        page = QWidget()
        form = QFormLayout(page)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.tabs.addTab(page, title)
        return form

    def add_tab(self, title: str, widget: QWidget) -> None:
        self.tabs.addTab(widget, title)

    def open_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
# --- END [1.1] ---
