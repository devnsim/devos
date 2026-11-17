# ==============================================================================
# FILE: app/views/types/form.py
# OVERVIEW: Form view - every field of the selected record, editable; add your own properties, remove the
#           ones you added. Follows the page's selection (or its own, when unlinked).
# PURPOSE:
#   1. One editor per field (app.views.editors); edits go to the record that is shown, even if the selection
#      moves while long text is still being typed (pending text is saved first).
#   2. Duplicate / Delete in the view menu (extend_view_menu); + Property; right-click a property you added to remove it (values are kept).
# CONTRACT:
#   - Requires: app.views (api, editors), app.ui.ui_tools, app.ui.canvas.tile_canvas.reserve_controls_space,
#               QSS selectors #record_title_edit, #record_meta_label, #record_form_panel, #empty_tile_label
#   - Exposes: FormView(host), AddPropertyDialog, FORM_VIEW (registered as "form")
# NOTES:
#   - [2026-11-17] Ported from app/ui/record_views.py FormBlock onto the view API (Phase 2).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - shown record: The record the editors display; edits always go to it.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. FORM_VIEW
#    1.1 add_property_dialog
#    1.2 view
# ==============================================================================

# ==============================================================================
# [1.1] add_property_dialog
# ==============================================================================
from __future__ import annotations

import datetime as dt

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
    QScrollArea, QWidget,
)

from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.ui_tools import add_button, block_layout
from app.views.api import TableData, ViewType, register_view_type
from app.views.editors import KIND_TITLES, make_editor


class AddPropertyDialog(QDialog):
    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add property")
        form = QFormLayout(self)
        self.name_edit = QLineEdit(placeholderText="e.g. Budget, Reviewer, Due date")
        self.kind_combo = QComboBox()
        for kind, title in KIND_TITLES.items():
            self.kind_combo.addItem(title, kind)
        self.options_edit = QLineEdit(placeholderText="Options, comma separated (Select / Multi-select)")
        form.addRow("Name", self.name_edit)
        form.addRow("Type", self.kind_combo)
        form.addRow("Options", self.options_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> tuple[str, str, tuple[str, ...]]:
        options = tuple(part.strip() for part in self.options_edit.text().split(",") if part.strip())
        return self.name_edit.text(), self.kind_combo.currentData(), options
# --- END [1.1] ---


# ==============================================================================
# [1.2] view
# ==============================================================================
class FormView(QWidget):
    def __init__(self, host) -> None:
        super().__init__()
        self.host = host
        self.data: TableData | None = None
        self.setters: dict[str, tuple[QWidget, callable]] = {}
        self._shown_fields: list[str] = []
        self.shown_id = None
        layout, row = block_layout(self)
        self.title_edit = QLineEdit(objectName="record_title_edit", placeholderText="Name")
        self.title_edit.editingFinished.connect(self._commit_title)
        row.addWidget(self.title_edit, 1)
        row.addWidget(host.view_menu_button())
        reserve_controls_space(row)
        self.scroll_area = QScrollArea(widgetResizable=True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.scroll_area, 1)
        self.empty_label = QLabel("Select a record, or create one with + New.", objectName="empty_tile_label",
                                  alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label, 1)
        footer = QHBoxLayout()
        self.meta_label = QLabel(objectName="record_meta_label")
        footer.addWidget(self.meta_label, 1)
        add_button(footer, "+ Property", self._add_property, "Add a property to every record")
        layout.addLayout(footer)
        self._show(None)

    def set_data(self, data: TableData) -> None:
        self.data = data
        self.empty_label.setText(f"Select a {data.noun}, or create one with + New.")
        self._show(self.host.selected_id)

    def set_selection(self, record_id) -> None:
        self._show(record_id)

    def _build_form(self) -> None:
        panel = QWidget(objectName="record_form_panel")
        form = QFormLayout(panel)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.setters = {}
        for field in self.data.fields:
            editor, setter = make_editor(field, lambda value, key=field["key"]: self._commit(key, value))
            label = QLabel(field["title"])
            label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            label.customContextMenuRequested.connect(lambda _pos, field=field, label=label: self._property_menu(field, label))
            label.setToolTip(KIND_TITLES.get(field["kind"], field["kind"]) + (" · right-click to remove" if field.get("is_user") else ""))
            if field["kind"] == "long_text":
                form.addRow(label)
                form.addRow(editor)
            else:
                form.addRow(label, editor)
            self.setters[field["key"]] = (editor, setter)
        self.scroll_area.setWidget(panel)
        self._shown_fields = [field["key"] for field in self.data.fields]

    def _show(self, record_id) -> None:
        row = self.data.row(record_id) if self.data is not None and record_id is not None else None
        is_same = row is not None and record_id == self.shown_id
        if not is_same:
            self._flush_pending()
        self.shown_id = record_id if row is not None else None
        for widget in (self.scroll_area, self.title_edit):
            widget.setVisible(row is not None)
        self.empty_label.setVisible(row is None)
        if row is None:
            self.meta_label.setText("")
            return
        if self._shown_fields != [field["key"] for field in self.data.fields]:
            self._build_form()
        if not (is_same and self.title_edit.hasFocus()):
            self.title_edit.setText(self.data.title(row))
        for key, (editor, setter) in self.setters.items():
            if not (is_same and editor.hasFocus()):          # never overwrite what the user is typing
                setter(row.get(key))
        if row.get("created_at"):
            self.meta_label.setText(f"Created {dt.datetime.fromtimestamp(row['created_at']):%Y-%m-%d %H:%M} · "
                                    f"edited {dt.datetime.fromtimestamp(row['updated_at']):%Y-%m-%d %H:%M}")

    def extend_view_menu(self, menu: QMenu) -> None:
        """Duplicate / Delete join the view menu (one ⋯ per view)."""
        if self.shown_id is not None:
            menu.addAction("Duplicate").triggered.connect(lambda: self.host.duplicate_record(self.shown_id))
            menu.addAction(f"Delete {self.data.noun}…").triggered.connect(lambda: self.host.delete_records([self.shown_id]))

    def _flush_pending(self) -> None:
        """Long text saves after a pause in typing; save it now (to the record it belongs to)."""
        for editor, _setter in self.setters.values():
            timer = getattr(editor, "pending_timer", None)
            if timer is not None and timer.isActive():
                timer.stop()
                timer.timeout.emit()

    def _commit(self, key: str, value) -> None:
        row = self.data.row(self.shown_id) if self.data is not None and self.shown_id is not None else None
        if row is not None and row.get(key) != value:
            self.host.set_value(self.shown_id, key, value)

    def _commit_title(self) -> None:
        row = self.data.row(self.shown_id) if self.data is not None and self.shown_id is not None else None
        text = self.title_edit.text().strip()
        if row is not None and text != self.data.title(row):
            if text:
                self.host.set_value(self.shown_id, self.data.title_key, text)
            else:
                self.title_edit.setText(self.data.title(row))

    def _add_property(self) -> None:
        dialog = AddPropertyDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.host.add_property(*dialog.values())

    def _property_menu(self, field: dict, label: QLabel) -> None:
        menu = QMenu(self)
        action = menu.addAction(f"Remove property “{field['title']}”")
        action.setEnabled(bool(field.get("is_user")))
        action.triggered.connect(lambda: self._remove_property(field))
        menu.exec(label.mapToGlobal(label.rect().bottomLeft()))

    def _remove_property(self, field: dict) -> None:
        if QMessageBox.question(self, "Remove property", f"Remove “{field['title']}” from every record's form?\n"
                                "Values are kept and come back if you add it again.") == QMessageBox.StandardButton.Yes:
            self.host.remove_property(field["key"])


FORM_VIEW = ViewType("form", "Form", "Every property of the selected record; add your own properties", ("table",), FormView)
register_view_type(FORM_VIEW)
# --- END [1.2] ---
