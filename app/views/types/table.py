# ==============================================================================
# FILE: app/views/types/table.py
# OVERVIEW: Table view - every row of a Table shape as a row: edit cells in place, sort by a column, search,
#           right-click a row for the record menu.
# PURPOSE:
#   1. RowsModel: the title + every field as columns; edits go to the host (host.set_value), never to a store.
#   2. Header click cycles the sort (ascending, descending, none) as part of the view's query; the search box
#      becomes the query's search (the engine filters, not the view).
# CONTRACT:
#   - Requires: app.views.api (ViewType, register_view_type, field_text, tag_color), app.ui.ui_tools,
#               app.ui.canvas.tile_canvas.reserve_controls_space, QSS selector #records_table
#   - Exposes: TableView(host), TABLE_VIEW (registered as "table")
# NOTES:
#   - [2026-11-17] Phase 5b: a title column that is also a field (file tables) shows once.
#   - [2026-11-17] Fields marked read_only can't be edited in place (Library sizes, dates).
#   - [2026-11-17] Ported from app/ui/record_views.py TableBlock onto the view API (Phase 2).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - SEARCH_DELAY_MS (int): Typing pauses this long before the search reaches the engine (default: 300).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. TABLE_VIEW
#    1.1 model
#    1.2 view
# ==============================================================================

# ==============================================================================
# [1.1] model
# ==============================================================================
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPoint, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QLineEdit, QStyledItemDelegate, QWidget

from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.ui_tools import add_button, block_layout, fit_columns, table_view
from app.views.api import TableData, ViewType, field_text, register_view_type, tag_color

SEARCH_DELAY_MS = 300


class RowsModel(QAbstractTableModel):
    def __init__(self, view: "TableView") -> None:
        super().__init__()
        self.view = view
        self.columns: list[dict] = []
        self.rows: list[dict] = []
        self.data_source: TableData | None = None

    def reload(self, data: TableData) -> None:
        self.beginResetModel()
        self.data_source = data
        title = data.field(data.title_key)                 # file tables list their title column as a field too
        self.columns = [title or {"key": data.title_key, "title": "Name", "kind": "text", "options": []}] + \
            [field for field in data.fields if field["key"] != data.title_key]
        self.rows = data.rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:                     # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:                  # noqa: N802
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            field = self.columns[section]
            sort = (self.view.host.definition["query"].get("sort") or [{}])[0]
            arrow = (" ▼" if sort.get("descending") else " ▲") if sort.get("field") == field["key"] else ""
            return field["title"] + arrow
        return None

    def flags(self, index):
        field = self.columns[index.column()]
        kind = field["kind"]
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if field.get("read_only"):
            return flags
        if kind == "checkbox":
            return flags | Qt.ItemFlag.ItemIsUserCheckable
        return flags if kind == "long_text" else flags | Qt.ItemFlag.ItemIsEditable

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        field, row = self.columns[index.column()], self.rows[index.row()]
        value = row.get(field["key"])
        if role == Qt.ItemDataRole.DisplayRole:
            return field_text(field, value) if field["kind"] != "checkbox" else None
        if role == Qt.ItemDataRole.EditRole:
            return ", ".join(value) if isinstance(value, list) else ("" if value is None else str(value))
        if role == Qt.ItemDataRole.CheckStateRole and field["kind"] == "checkbox":
            return Qt.CheckState.Checked if value else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.ForegroundRole and field["kind"] == "select" and value:
            return tag_color(value).lighter(150)
        if role == Qt.ItemDataRole.FontRole and index.column() == 0:
            font = QFont()
            font.setBold(True)
            return font
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        field, row = self.columns[index.column()], self.rows[index.row()]
        if role == Qt.ItemDataRole.CheckStateRole:
            value = Qt.CheckState(value) == Qt.CheckState.Checked
        elif role != Qt.ItemDataRole.EditRole:
            return False
        if value == self.data(index, Qt.ItemDataRole.EditRole):
            return False
        self.view.host.set_value(row[self.data_source.key], field["key"], value)
        return True


class SelectDelegate(QStyledItemDelegate):
    """Inline editor for select columns: a combo of the known options (typing a new one works too)."""

    def __init__(self, model: RowsModel) -> None:
        super().__init__()
        self.model = model

    def createEditor(self, parent, option, index):                       # noqa: N802
        field = self.model.columns[index.column()]
        if field["kind"] != "select":
            return super().createEditor(parent, option, index)
        combo = QComboBox(parent, editable=True)
        combo.addItems([""] + list(field.get("options", [])))
        return combo

    def setEditorData(self, editor, index) -> None:                       # noqa: N802
        if isinstance(editor, QComboBox):
            editor.setCurrentText(index.data(Qt.ItemDataRole.EditRole))
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index) -> None:                 # noqa: N802
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText())
        else:
            super().setModelData(editor, model, index)
# --- END [1.1] ---


# ==============================================================================
# [1.2] view
# ==============================================================================
class TableView(QWidget):
    def __init__(self, host) -> None:
        super().__init__()
        self.host = host
        layout, row = block_layout(self)
        self.search_edit = QLineEdit(placeholderText="Search…", clearButtonEnabled=True)
        self.search_edit.setText(host.definition["query"].get("search", ""))
        self._search_timer = QTimer(self, singleShot=True, interval=SEARCH_DELAY_MS,
                                    timeout=lambda: host.set_query(search=self.search_edit.text().strip()))
        self.search_edit.textChanged.connect(lambda: self._search_timer.start())
        row.addWidget(self.search_edit, 1)
        add_button(row, "+ New", lambda: host.create_record(), "Add a row")
        add_button(row, "Delete", self._delete_selected, "Delete the selected rows")
        row.addWidget(host.view_menu_button())
        reserve_controls_space(row)
        self.model = RowsModel(self)
        self.records_table = table_view("records_table")
        self.records_table.setModel(self.model)
        self.records_table.setItemDelegate(SelectDelegate(self.model))
        self.records_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.records_table.verticalHeader().hide()
        self.records_table.horizontalHeader().sectionClicked.connect(self._sort_by)
        self.records_table.selectionModel().currentRowChanged.connect(self._on_row_changed)
        self.records_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.records_table.customContextMenuRequested.connect(self._show_record_menu)
        layout.addWidget(self.records_table, 1)

    def set_data(self, data: TableData) -> None:
        self.model.reload(data)
        fit_columns(self.records_table, 240)

    def set_selection(self, record_id) -> None:
        for row_index, row in enumerate(self.model.rows):
            if row.get(self.model.data_source.key) == record_id:
                if self.records_table.currentIndex().row() != row_index:
                    self.records_table.selectionModel().blockSignals(True)
                    self.records_table.selectRow(row_index)
                    self.records_table.selectionModel().blockSignals(False)
                return
        self.records_table.clearSelection()

    def _on_row_changed(self, current: QModelIndex, _previous) -> None:
        if current.isValid() and current.row() < len(self.model.rows):
            self.host.select(self.model.rows[current.row()][self.model.data_source.key])

    def _show_record_menu(self, pos: QPoint) -> None:
        index = self.records_table.indexAt(pos)
        if index.isValid() and index.row() < len(self.model.rows):
            self.host.show_record_menu(self.model.rows[index.row()][self.model.data_source.key],
                                       self.records_table.viewport().mapToGlobal(pos), self)

    def _sort_by(self, column: int) -> None:
        key = self.model.columns[column]["key"]
        current = (self.host.definition["query"].get("sort") or [{}])[0]
        if current.get("field") != key:
            sort = [{"field": key, "descending": False}]
        elif not current.get("descending"):
            sort = [{"field": key, "descending": True}]
        else:
            sort = None
        self.host.set_query(sort=sort)

    def _delete_selected(self) -> None:
        rows = sorted({index.row() for index in self.records_table.selectionModel().selectedRows()})
        ids = [self.model.rows[row][self.model.data_source.key] for row in rows] or \
            ([self.host.selected_id] if self.host.selected_id is not None else [])
        self.host.delete_records(ids)


TABLE_VIEW = ViewType("table", "Table", "Every row; edit cells in place, sort by a column, search", ("table",), TableView)
register_view_type(TABLE_VIEW)
# --- END [1.2] ---
