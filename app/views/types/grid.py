# ==============================================================================
# FILE: app/views/types/grid.py
# OVERVIEW: Grid view - an editable grid for any table, big ones included: one column per field, a page of
#           rows at a time (the engine pages), cells edited in place by kind, computed and read-only columns
#           shown muted (a formula's text in the tooltip), copy / paste of cell blocks, new columns.
# PURPOSE:
#   1. Paging: the view's question carries limit / offset (PAGE_ROWS rows); ‹ › move a page; the count says
#      which rows of how many show. Sorting by a header click and searching go to the engine too.
#   2. Editing: the table view's model and select editor (app/views/types/table.py), with every field as a
#      column; Ctrl+C copies the selected cells as tab-separated text, Ctrl+V pastes such text from the
#      current cell (one write per row; the engine checks each value).
#   3. + Column: a name and a kind (a formula for computed ones) -> host.add_property().
# CONTRACT:
#   - Requires: app.views.types.table (RowsModel, SelectDelegate), app.views.api, app.ui.ui_tools,
#               app.ui.theme (MUTED_TEXT_COLOR), app.ui.canvas.tile_canvas.reserve_controls_space,
#               QSS selector #records_table
#   - Exposes: GridView(host), GridModel, NewColumnDialog, GRID_VIEW (registered as "grid"), PAGE_ROWS
# NOTES:
#   - [2026-11-17] Created in Phase 5b of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - PAGE_ROWS (int): Rows per page (default: 200).
# - COLUMN_KINDS (tuple): Kinds offered for a new column (the engine refuses what a source can't hold).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. GRID_VIEW
#    1.1 model_and_dialog
#    1.2 view
#    1.3 clipboard_and_paging
# ==============================================================================

# ==============================================================================
# [1.1] model_and_dialog
# ==============================================================================
from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                               QWidget)

from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.theme import MUTED_TEXT_COLOR
from app.ui.ui_tools import add_button, block_layout, fit_columns, table_view
from app.views.api import TableData, ViewType, register_view_type
from app.views.types.table import SEARCH_DELAY_MS, RowsModel, SelectDelegate

PAGE_ROWS = 200
COLUMN_KINDS = ("text", "number", "integer", "select", "date", "checkbox", "url", "long_text", "formula")


class GridModel(RowsModel):
    """Every field as a column (the title column first); computed / read-only columns muted."""

    def reload(self, data: TableData) -> None:
        self.beginResetModel()
        self.data_source = data
        title = data.field(data.title_key)
        self.columns = ([title] if title else [{"key": data.title_key, "title": "Name", "kind": "text", "options": []}]) + \
            [field for field in data.fields if field["key"] != data.title_key]
        self.rows = data.rows
        self.endResetModel()

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            return str(self.view.offset + section + 1)
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.ToolTipRole:
            field = self.columns[section]
            return field.get("formula") or ("read-only" if field.get("read_only") else field["kind"])
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        field = self.columns[index.column()]
        if field.get("read_only") or field.get("computed"):
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(MUTED_TEXT_COLOR)
            if role == Qt.ItemDataRole.ToolTipRole and field.get("formula"):
                return field["formula"]
        if role == Qt.ItemDataRole.FontRole and index.column() == 0 and field["key"] != self.data_source.key:
            font = QFont()
            font.setBold(True)
            return font
        return super().data(index, role)


class NewColumnDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, windowTitle="New column")
        form = QFormLayout(self)
        self.name_edit = QLineEdit(placeholderText="Name")
        self.kind_combo = QComboBox()
        self.kind_combo.addItems(COLUMN_KINDS)
        self.formula_edit = QLineEdit(placeholderText="=[Price] * [Quantity]", enabled=False)
        self.kind_combo.currentTextChanged.connect(lambda kind: self.formula_edit.setEnabled(kind == "formula"))
        form.addRow("Name", self.name_edit)
        form.addRow("Kind", self.kind_combo)
        form.addRow("Formula", self.formula_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def column(self) -> tuple[str, str, str]:
        kind = self.kind_combo.currentText()
        return self.name_edit.text().strip(), kind, self.formula_edit.text().strip() if kind == "formula" else ""
# --- END [1.1] ---


# ==============================================================================
# [1.2] view
# ==============================================================================
class GridView(QWidget):
    def __init__(self, host) -> None:
        super().__init__()
        self.host = host
        self.offset = int(host.definition["query"].get("offset") or 0)
        self.total = 0
        layout, row = block_layout(self)
        self.search_edit = QLineEdit(placeholderText="Search…", clearButtonEnabled=True)
        self.search_edit.setText(host.definition["query"].get("search", ""))
        self._search_timer = QTimer(self, singleShot=True, interval=SEARCH_DELAY_MS,
                                    timeout=lambda: host.set_query(search=self.search_edit.text().strip(), offset=None))
        self.search_edit.textChanged.connect(lambda: self._search_timer.start())
        row.addWidget(self.search_edit, 1)
        add_button(row, "+ Row", lambda: host.create_record(), "Add a row")
        add_button(row, "+ Column", self.new_column, "Add a column (a formula computes it)")
        add_button(row, "Delete", self._delete_selected, "Delete the selected rows")
        self.previous_btn = add_button(row, "‹", lambda: self.go_to(self.offset - PAGE_ROWS), "Previous page")
        self.page_label = QLabel(objectName="control_label")
        row.addWidget(self.page_label)
        self.next_btn = add_button(row, "›", lambda: self.go_to(self.offset + PAGE_ROWS), "Next page")
        row.addWidget(host.view_menu_button())
        reserve_controls_space(row)
        self.model = GridModel(self)
        self.grid_table = table_view("records_table")
        self.grid_table.setModel(self.model)
        self.grid_table.setItemDelegate(SelectDelegate(self.model))
        self.grid_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.grid_table.horizontalHeader().sectionClicked.connect(self._sort_by)
        self.grid_table.selectionModel().currentRowChanged.connect(self._on_row_changed)
        self.grid_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid_table.customContextMenuRequested.connect(
            lambda pos: self._row_id(self.grid_table.indexAt(pos)) is not None and host.show_record_menu(
                self._row_id(self.grid_table.indexAt(pos)), self.grid_table.viewport().mapToGlobal(pos), self))
        self.grid_table.installEventFilter(self)
        layout.addWidget(self.grid_table, 1)
        if host.definition["query"].get("limit") is None:
            QTimer.singleShot(0, lambda: host.set_query(limit=PAGE_ROWS))     # page through the engine, not all rows

    def set_data(self, data: TableData) -> None:
        self.offset, self.total = int(data.payload.get("offset") or 0), data.total
        self.model.reload(data)
        fit_columns(self.grid_table, 240)
        last = self.offset + len(data.rows)
        self.page_label.setText(f"{self.offset + 1 if data.rows else 0:,}–{last:,} of {self.total:,}")
        self.previous_btn.setEnabled(self.offset > 0)
        self.next_btn.setEnabled(last < self.total)

    def set_selection(self, record_id) -> None:
        for row_index, row in enumerate(self.model.rows):
            if row.get(self.model.data_source.key) == record_id:
                if self.grid_table.currentIndex().row() != row_index:
                    self.grid_table.selectionModel().blockSignals(True)
                    self.grid_table.setCurrentIndex(self.model.index(row_index, max(0, self.grid_table.currentIndex().column())))
                    self.grid_table.selectionModel().blockSignals(False)
                return

    def _row_id(self, index: QModelIndex):
        if not index.isValid() or index.row() >= len(self.model.rows):
            return None
        return self.model.rows[index.row()][self.model.data_source.key]

    def _on_row_changed(self, current: QModelIndex, _previous) -> None:
        row_id = self._row_id(current)
        if row_id is not None:
            self.host.select(row_id)

    def _sort_by(self, column: int) -> None:
        key = self.model.columns[column]["key"]
        current = (self.host.definition["query"].get("sort") or [{}])[0]
        sort = [{"field": key, "descending": False}] if current.get("field") != key else \
            [{"field": key, "descending": True}] if not current.get("descending") else None
        self.host.set_query(sort=sort, offset=None)

    def _delete_selected(self) -> None:
        rows = sorted({index.row() for index in self.grid_table.selectionModel().selectedIndexes()})
        self.host.delete_records([self.model.rows[row][self.model.data_source.key] for row in rows if row < len(self.model.rows)])

    def new_column(self) -> None:
        dialog = NewColumnDialog(self)
        if dialog.exec():
            name, kind, formula = dialog.column()
            if name:
                self.host.add_property(name, kind, (), formula)
# --- END [1.2] ---


# ==============================================================================
# [1.3] clipboard_and_paging
# ==============================================================================
    def go_to(self, offset: int) -> None:
        offset = max(0, min(offset, max(0, self.total - 1)))
        self.host.set_query(offset=offset or None)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:      # noqa: N802
        if watched is self.grid_table and event.type() == QEvent.Type.KeyPress:
            if event.matches(QKeySequence.StandardKey.Copy):
                QGuiApplication.clipboard().setText(self.copied_text())
                return True
            if event.matches(QKeySequence.StandardKey.Paste):
                self.paste_text(QGuiApplication.clipboard().text())
                return True
        return super().eventFilter(watched, event)

    def copied_text(self) -> str:
        """The selected cells as tab-separated lines (what spreadsheets paste)."""
        indexes = self.grid_table.selectionModel().selectedIndexes()
        if not indexes:
            return ""
        rows = sorted({index.row() for index in indexes})
        columns = sorted({index.column() for index in indexes})
        chosen = {(index.row(), index.column()) for index in indexes}
        return "\n".join("\t".join(str(self.model.index(row, column).data(Qt.ItemDataRole.EditRole) or "")
                                   if (row, column) in chosen else "" for column in columns) for row in rows)

    def paste_text(self, text: str) -> int:
        """Tab-separated text into the grid from the current cell: one update per row (read-only cells skipped).
        Returns how many rows were written."""
        start = self.grid_table.currentIndex()
        if not start.isValid() or not text:
            return 0
        written = 0
        for row_offset, line in enumerate(text.rstrip("\n").split("\n")):
            row = start.row() + row_offset
            if row >= len(self.model.rows):
                break
            values = {}
            for column_offset, value in enumerate(line.rstrip("\r").split("\t")):
                column = start.column() + column_offset
                if column < len(self.model.columns):
                    field = self.model.columns[column]
                    if not field.get("read_only") and not field.get("computed"):
                        values[field["key"]] = value
            if values:
                self.host.update_record(self.model.rows[row][self.model.data_source.key], values)
                written += 1
        return written


GRID_VIEW = ViewType("grid", "Grid", "Every column, a page of rows at a time; edit, paste and add columns", ("table",), GridView)
register_view_type(GRID_VIEW)
# --- END [1.3] ---
