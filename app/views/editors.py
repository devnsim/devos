# ==============================================================================
# FILE: app/views/editors.py
# OVERVIEW: Field editors for views - one editing widget per field kind (text, long text, number, select,
#           multi-select, date, checkbox, link), used by the form view and anywhere a value is edited.
# PURPOSE:
#   1. make_editor(field, commit) -> (widget, setter): commit(value) when the user is done (long text after a
#      pause in typing); setter(value) shows a value without committing.
#   2. DateField (text + calendar pop-up), MultiSelectField (toggle options, add a new one).
#   Options come from the field (declared + every value in use), so a typed option becomes a choice.
# CONTRACT:
#   - Requires: PySide6, app.views.api.parse_date, QSS selector #record_long_text_edit, #menu_glyph_btn
#   - Exposes: make_editor(field, commit), DateField, MultiSelectField, KIND_TITLES
# NOTES:
#   - [2026-11-17] Moved from app/ui/record_views.py in Phase 2 (fields are shape fields, not PropertySpecs).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - field (dict): A shape field {key, title, kind, options}.
# - KIND_TITLES (dict): Field kind -> the name users see.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. EDITORS
#    1.1 fields
#    1.2 make_editor
# ==============================================================================

# ==============================================================================
# [1.1] fields
# ==============================================================================
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCalendarWidget, QCheckBox, QComboBox, QHBoxLayout, QInputDialog, QLineEdit, QMenu, QPlainTextEdit, QToolButton,
    QWidget, QWidgetAction,
)

from app.views.api import parse_date

KIND_TITLES = {"text": "Text", "long_text": "Long text", "number": "Number", "select": "Select",
               "multi_select": "Multi-select", "date": "Date", "checkbox": "Checkbox", "url": "Link"}


class DateField(QWidget):
    """Text field (YYYY-MM-DD, may be empty) + a calendar pop-up button."""
    committed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.edit = QLineEdit(placeholderText="YYYY-MM-DD")
        self.edit.editingFinished.connect(lambda: self.committed.emit(self.edit.text()))
        pick_btn = QToolButton(text="📅", toolTip="Pick a date", objectName="menu_glyph_btn")
        menu = QMenu(pick_btn)
        self.calendar_widget = QCalendarWidget()
        self.calendar_widget.clicked.connect(lambda date: (menu.close(), self.set_value(date.toPython().isoformat()),
                                                           self.committed.emit(self.edit.text())))
        action = QWidgetAction(menu)
        action.setDefaultWidget(self.calendar_widget)
        menu.addAction(action)
        pick_btn.setMenu(menu)
        pick_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        layout.addWidget(self.edit, 1)
        layout.addWidget(pick_btn)

    def set_value(self, value) -> None:
        self.edit.setText(value or "")
        date = parse_date(value)
        if date:
            self.calendar_widget.setSelectedDate(date)

    def hasFocus(self) -> bool:                                          # noqa: N802 (Qt naming)
        return self.edit.hasFocus()


class MultiSelectField(QToolButton):
    """Button listing the chosen options; its menu toggles options and can add a new one."""
    committed = Signal(object)

    def __init__(self, field: dict) -> None:
        super().__init__(popupMode=QToolButton.ToolButtonPopupMode.InstantPopup)
        self.field, self.values = field, []
        self.setMenu(QMenu(self))
        self.menu().aboutToShow.connect(self._fill_menu)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

    def set_value(self, value) -> None:
        self.values = list(value or [])
        self.setText(", ".join(self.values) or "—")

    def _fill_menu(self) -> None:
        menu = self.menu()
        menu.clear()
        for option in list(self.field.get("options", [])) + [value for value in self.values
                                                            if value not in self.field.get("options", [])]:
            action = menu.addAction(option)
            action.setCheckable(True)
            action.setChecked(option in self.values)
            action.toggled.connect(lambda is_on, option=option: self._toggle(option, is_on))
        menu.addSeparator()
        menu.addAction("Add option…").triggered.connect(self._add_option)

    def _toggle(self, option: str, is_on: bool) -> None:
        values = [value for value in self.values if value != option] + ([option] if is_on else [])
        self.set_value(values)
        self.committed.emit(values)

    def _add_option(self) -> None:
        text, is_ok = QInputDialog.getText(self, self.field.get("title", ""), "New option")
        if is_ok and text.strip():
            self._toggle(text.strip(), True)                 # in use = a choice from now on
# --- END [1.1] ---


# ==============================================================================
# [1.2] make_editor
# ==============================================================================
def make_editor(field: dict, commit) -> tuple[QWidget, callable]:
    """Editor widget for a field and a setter(value). commit(value) is called when the user is done."""
    kind = field.get("kind", "text")
    if kind == "long_text":
        editor = QPlainTextEdit(objectName="record_long_text_edit")
        editor.setMinimumHeight(140)
        editor.pending_timer = QTimer(editor, singleShot=True, interval=700, timeout=lambda: commit(editor.toPlainText()))
        editor.textChanged.connect(lambda: editor.pending_timer.start() if editor.hasFocus() else None)
        return editor, lambda value: editor.setPlainText(value or "")
    if kind == "checkbox":
        editor = QCheckBox()
        editor.clicked.connect(lambda is_on: commit(is_on))
        return editor, lambda value: editor.setChecked(bool(value))
    if kind == "select":
        editor = QComboBox(editable=True)
        editor.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

        def set_select(value) -> None:
            editor.blockSignals(True)
            editor.clear()
            editor.addItems([""] + list(field.get("options", [])))
            editor.setCurrentText(value or "")
            editor.blockSignals(False)
        editor.activated.connect(lambda _index: commit(editor.currentText()))
        editor.lineEdit().editingFinished.connect(lambda: commit(editor.currentText()))
        return editor, set_select
    if kind == "multi_select":
        editor = MultiSelectField(field)
        editor.committed.connect(commit)
        return editor, editor.set_value
    if kind == "date":
        editor = DateField()
        editor.committed.connect(commit)
        return editor, editor.set_value
    editor = QLineEdit(placeholderText={"number": "number", "url": "https://…"}.get(kind, ""))
    editor.editingFinished.connect(lambda: commit(editor.text()) if editor.isModified() else None)

    def set_text(value) -> None:
        editor.setText("" if value is None else str(value))
        editor.setModified(False)
    return editor, set_text
# --- END [1.2] ---
