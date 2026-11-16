# ==============================================================================
# FILE: app/modules/db_editor/dialogs.py
# OVERVIEW: Forms the Database Editor opens for schema changes: create a table (columns grid with a live
#           SQL preview), add a column, add an index.
# PURPOSE:
#   1. CreateTableDialog: table name + editable column rows (name, type, PK, not null, unique, default),
#      add / remove / move rows, live CREATE TABLE preview; Create stays disabled until it is valid.
#   2. ColumnDialog: one column for ALTER TABLE ADD COLUMN (name, type, not null, default).
#   3. IndexDialog: index name, unique, which columns.
# CONTRACT:
#   - Requires: app.modules.db_editor.engine (ColumnDef, COLUMN_TYPES, create_table_sql),
#               QSS selector #sql_preview_view
#   - Exposes: CreateTableDialog(parent, existing_tables) -> table_name(), column_defs();
#              ColumnDialog(parent, title) -> column_def(); IndexDialog(parent, table, columns) ->
#              index_name(), columns(), is_unique()
# NOTES:
#   - [2026-11-16] Created with the Database Editor module.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - column row: One line of the Create table grid; check boxes for PK / not null / unique.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. DIALOGS
#    2.1 create_table_dialog
#    2.2 column_dialog
#    2.3 index_dialog
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.modules.db_editor.engine import COLUMN_TYPES, ColumnDef, create_table_sql

NAME, TYPE, PRIMARY_KEY, NOT_NULL, UNIQUE, DEFAULT = range(6)
CHECK_COLUMNS = (PRIMARY_KEY, NOT_NULL, UNIQUE)


def _type_combo(declared_type: str = "TEXT") -> QComboBox:
    combo = QComboBox(editable=True)
    combo.addItems(COLUMN_TYPES)
    combo.setCurrentText(declared_type)
    return combo


def _buttons(dialog: QDialog, ok_text: str) -> QDialogButtonBox:
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    return buttons
# --- END [1.1] ---


# ==============================================================================
# [2.1] create_table_dialog
# ==============================================================================
class CreateTableDialog(QDialog):
    def __init__(self, parent: QWidget | None, existing_tables: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create table")
        self.resize(640, 480)
        self.existing = {name.lower() for name in existing_tables}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(placeholderText="table name")
        self.name_edit.textChanged.connect(self._update)
        form.addRow("Name", self.name_edit)
        layout.addLayout(form)

        self.columns_table = QTableWidget(0, 6)
        self.columns_table.setHorizontalHeaderLabels(["Column", "Type", "PK", "Not null", "Unique", "Default"])
        header = self.columns_table.horizontalHeader()
        header.setSectionResizeMode(NAME, QHeaderView.ResizeMode.Stretch)
        for column in CHECK_COLUMNS:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.columns_table.itemChanged.connect(self._update)
        layout.addWidget(self.columns_table, 2)

        row = QHBoxLayout()
        for text, handler in (("+ Column", lambda: self.add_column_row()), ("Remove", self._remove_row),
                              ("▲", lambda: self._move_row(-1)), ("▼", lambda: self._move_row(1))):
            button = QPushButton(text)
            button.clicked.connect(handler)
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)

        layout.addWidget(QLabel("SQL"))
        self.sql_preview_view = QPlainTextEdit(objectName="sql_preview_view", readOnly=True)
        layout.addWidget(self.sql_preview_view, 1)
        self.problem_label = QLabel(objectName="db_status_label")
        layout.addWidget(self.problem_label)
        self.buttons = _buttons(self, "Create")
        layout.addWidget(self.buttons)

        self.add_column_row(ColumnDef("id", "INTEGER", is_primary_key=True))
        self.add_column_row(ColumnDef("name", "TEXT"))
        self.name_edit.setFocus()

    def add_column_row(self, column: ColumnDef | None = None) -> None:
        column = column or ColumnDef(f"column_{self.columns_table.rowCount() + 1}")
        table = self.columns_table
        table.blockSignals(True)
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, NAME, QTableWidgetItem(column.name))
        combo = _type_combo(column.declared_type)
        combo.currentTextChanged.connect(self._update)
        table.setCellWidget(row, TYPE, combo)
        for index, is_checked in zip(CHECK_COLUMNS, (column.is_primary_key, column.is_not_null, column.is_unique)):
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setCheckState(Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
            table.setItem(row, index, item)
        table.setItem(row, DEFAULT, QTableWidgetItem(column.default))
        table.blockSignals(False)
        self._update()

    def _remove_row(self) -> None:
        if self.columns_table.currentRow() >= 0:
            self.columns_table.removeRow(self.columns_table.currentRow())
            self._update()

    def _move_row(self, step: int) -> None:
        row = self.columns_table.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.columns_table.rowCount():
            return
        columns = self.column_defs()
        columns[row], columns[target] = columns[target], columns[row]
        self.columns_table.setRowCount(0)
        for column in columns:
            self.add_column_row(column)
        self.columns_table.setCurrentCell(target, NAME)

    def table_name(self) -> str:
        return self.name_edit.text().strip()

    def column_defs(self) -> list[ColumnDef]:
        table = self.columns_table
        def text(row: int, column: int) -> str:
            item = table.item(row, column)
            return item.text().strip() if item else ""
        def checked(row: int, column: int) -> bool:
            item = table.item(row, column)
            return item is not None and item.checkState() == Qt.CheckState.Checked
        return [ColumnDef(text(row, NAME), table.cellWidget(row, TYPE).currentText(), checked(row, PRIMARY_KEY),
                          checked(row, NOT_NULL), checked(row, UNIQUE), text(row, DEFAULT))
                for row in range(table.rowCount())]

    def _problem(self) -> str:
        columns = self.column_defs()
        names = [column.name.lower() for column in columns]
        if not self.table_name():
            return "Name the table"
        if self.table_name().lower() in self.existing:
            return f"A table named {self.table_name()} already exists"
        if not columns:
            return "Add at least one column"
        if "" in names:
            return "Every column needs a name"
        if len(set(names)) != len(names):
            return "Column names must be different"
        return ""

    def _update(self, *_args) -> None:
        problem = self._problem()
        self.problem_label.setText(problem)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not problem)
        self.sql_preview_view.setPlainText(create_table_sql(self.table_name() or "…", self.column_defs()))
# --- END [2.1] ---


# ==============================================================================
# [2.2] column_dialog
# ==============================================================================
class ColumnDialog(QDialog):
    """A column for ALTER TABLE ADD COLUMN (SQLite allows no PRIMARY KEY / UNIQUE there)."""

    def __init__(self, parent: QWidget | None, title: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QFormLayout(self)
        self.name_edit = QLineEdit(placeholderText="column name")
        self.type_combo = _type_combo()
        self.not_null_check = QCheckBox("Not null (needs a default)")
        self.default_edit = QLineEdit(placeholderText="e.g. 0, 'text', CURRENT_TIMESTAMP")
        form.addRow("Name", self.name_edit)
        form.addRow("Type", self.type_combo)
        form.addRow("", self.not_null_check)
        form.addRow("Default", self.default_edit)
        self.buttons = _buttons(self, "Add")
        form.addRow(self.buttons)
        for signal in (self.name_edit.textChanged, self.default_edit.textChanged, self.not_null_check.toggled):
            signal.connect(self._update)
        self._update()

    def column_def(self) -> ColumnDef:
        return ColumnDef(self.name_edit.text().strip(), self.type_combo.currentText(), False,
                         self.not_null_check.isChecked(), False, self.default_edit.text().strip())

    def _update(self, *_args) -> None:
        is_valid = bool(self.name_edit.text().strip()) and (not self.not_null_check.isChecked() or bool(self.default_edit.text().strip()))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(is_valid)
# --- END [2.2] ---


# ==============================================================================
# [2.3] index_dialog
# ==============================================================================
class IndexDialog(QDialog):
    def __init__(self, parent: QWidget | None, table: str, columns: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Add index to {table}")
        self.table = table
        form = QFormLayout(self)
        self.name_edit = QLineEdit(f"idx_{table}")
        self.unique_check = QCheckBox("Unique (no two rows may share these values)")
        self.column_list = QListWidget()
        for column in columns:
            item = QListWidgetItem(column)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.column_list.addItem(item)
        self.column_list.itemChanged.connect(self._on_columns_changed)
        form.addRow("Name", self.name_edit)
        form.addRow("Columns", self.column_list)
        form.addRow("", self.unique_check)
        self.buttons = _buttons(self, "Create")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        form.addRow(self.buttons)

    def _on_columns_changed(self) -> None:
        chosen = self.columns()
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(chosen))
        if chosen and self.name_edit.text().startswith(f"idx_{self.table}"):
            self.name_edit.setText(f"idx_{self.table}_" + "_".join(chosen))

    def index_name(self) -> str:
        return self.name_edit.text().strip()

    def columns(self) -> list[str]:
        return [self.column_list.item(row).text() for row in range(self.column_list.count())
                if self.column_list.item(row).checkState() == Qt.CheckState.Checked]

    def is_unique(self) -> bool:
        return self.unique_check.isChecked()
# --- END [2.3] ---
