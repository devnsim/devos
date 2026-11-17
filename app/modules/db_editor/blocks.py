# ==============================================================================
# FILE: app/modules/db_editor/blocks.py
# OVERVIEW: The Database Editor's views as blocks: explorer tree, data grid, record form, structure,
#           SQL console and properties. They share the module's state (open database, table, query,
#           current row) and react to its signals, so any mix of blocks stays in step.
# PURPOSE:
#   1. ExplorerBlock: file tree of databases -> tables / views / indexes / triggers -> columns, with a
#      filter and context menus for every object (open, structure, rename, add column / index, drop, copy SQL).
#   2. DataBlock: paged grid of the current table (200 rows a page); edit cells in place, sort by header,
#      SQL filter, add / delete rows, set NULL, copy, save the numeric columns as a Graph dataset.
#   3. RecordBlock: one row as a form (field + NULL box per column); prev / next walk the grid's rows;
#      New / Save / Revert / Delete; unsaved edits are saved before moving to another row.
#   4. StructureBlock: columns, indexes, foreign keys and the CREATE statement; schema actions.
#   5. SqlBlock: SQL editor (Ctrl+Enter runs all or the selection), results grid, timing, history.
#   6. PropertiesBlock: file, size, version, page stats, journal mode, user version; integrity check,
#      compact (VACUUM), export.
# CONTRACT:
#   - Requires: app.modules.db_editor.engine, app.modules.graph.datasets.save_dataset, app.clients.library
#               (size_text), app.ui (canvas.block_canvas.BlockSpec, canvas.tile_canvas.reserve_controls_space,
#               ui_tools, theme, widgets.sidebar.load_icon), a DbEditorModule (view.py) passed to every block;
#               QSS selectors #db_tree, #sql_editor, #structure_sql_view, #structure_title_label, #record_form_panel
#   - Exposes: block_specs(module), DEFAULT_BLOCKS, RowsModel, ExplorerBlock, DataBlock, RecordBlock,
#              StructureBlock, SqlBlock, PropertiesBlock, PAGE_SIZE
# NOTES:
#   - [2026-11-17] Library access goes through the Knowledge engine (app.clients.library), not the content store (Phase 4).
#   - [2026-11-16] block_layout / table_view / copy_selection / fit_columns / add_button / add_menu_action
#                  moved to app/ui/ui_tools.py (the new record views and modules use them too).
#   - [2026-11-16] Created with the Database Editor module. Record form: navigation on top, actions
#                  (New / Revert / Save / Delete) under the form so they never get squeezed.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - PAGE_SIZE (int): Rows the data grid loads at a time (default: 200).
# - node data (tuple): What a tree item stands for - ("database", id), ("group", type), ("table", name),
#   ("view", name), ("index", name), ("trigger", name), ("column", table, name).
# - own edit: A change a block made itself; it doesn't reload on its own data_changed signal.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SHARED
#    1.1 imports_and_layout
#    1.2 rows_model
# 2. BLOCKS
#    2.1 explorer_block
#    2.2 data_block
#    2.3 record_block
#    2.4 structure_block
#    2.5 sql_block
#    2.6 properties_block
# 3. SPECS
#    3.1 block_specs
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_layout
# ==============================================================================
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMenu, QPlainTextEdit, QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app.data.content_kinds import size_text
from app.modules.db_editor.engine import coerce_value, quote_identifier
from app.ui.canvas.block_canvas import BlockSpec
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.theme import MUTED_TEXT_COLOR
from app.ui.ui_tools import (
    add_button, add_menu_action, block_layout, copy_selection, fit_columns, set_style_property, table_view, tool_button,
)
from app.ui.widgets.sidebar import load_icon

PAGE_SIZE = 200
MAX_CELL_CHARS = 200
SQL_HISTORY_SIZE = 30
DEFAULT_BLOCKS = {"split": "h", "sizes": [1, 3], "children": [
    {"kind": "explorer"},
    {"split": "v", "sizes": [3, 2], "children": [
        {"kind": "data"},
        {"split": "h", "sizes": [1, 1], "children": [{"kind": "record"}, {"kind": "sql"}]},
    ]},
]}
# --- END [1.1] ---

# ==============================================================================
# [1.2] rows_model
# ==============================================================================
class RowsModel(QAbstractTableModel):
    """Rows for a QTableView. NULL shows as a muted italic "NULL"; BLOBs as their size. With on_edit,
    cells of rows that have a key are editable: on_edit(row, column, text_or_None) -> (ok, stored value)."""

    def __init__(self, columns: list[str], rows: list[list], first_row_number: int = 1,
                 keys: list | None = None, on_edit=None) -> None:
        super().__init__()
        self.columns, self.rows, self.first_row_number = columns, rows, first_row_number
        self.keys = keys if keys is not None else [None] * len(rows)
        self.on_edit = on_edit
        self._muted = QBrush(QColor(MUTED_TEXT_COLOR))
        self._italic = QFont()
        self._italic.setItalic(True)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        value = self.rows[index.row()][index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            if value is None:
                return "NULL"
            if isinstance(value, bytes):
                return f"BLOB · {len(value):,} bytes"
            text = str(value)
            first_line = text.split("\n", 1)[0]
            return first_line[:MAX_CELL_CHARS] + ("…" if len(first_line) > MAX_CELL_CHARS or "\n" in text else "")
        if role == Qt.ItemDataRole.EditRole:
            return "" if value is None else str(value)
        if role == Qt.ItemDataRole.ForegroundRole and (value is None or isinstance(value, bytes)):
            return self._muted
        if role == Qt.ItemDataRole.FontRole and value is None:
            return self._italic
        if role == Qt.ItemDataRole.TextAlignmentRole and isinstance(value, (int, float)):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole and isinstance(value, str) and len(value) > MAX_CELL_CHARS:
            return value[:2000]
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.columns[section] if section < len(self.columns) else None
        return str(self.first_row_number + section)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        flags = super().flags(index)
        if (self.on_edit is not None and self.keys[index.row()] is not None
                and not isinstance(self.rows[index.row()][index.column()], bytes)):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not (self.flags(index) & Qt.ItemFlag.ItemIsEditable):
            return False
        is_ok, stored = self.on_edit(index.row(), index.column(), value)
        if is_ok:
            self.rows[index.row()][index.column()] = stored
            self.dataChanged.emit(index, index)
        return is_ok
# --- END [1.2] ---



# ==============================================================================
# [2.1] explorer_block
# ==============================================================================
class ExplorerBlock(QWidget):
    GROUPS = (("table", "Tables"), ("view", "Views"), ("index", "Indexes"), ("trigger", "Triggers"))

    def __init__(self, module) -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        self.filter_edit = QLineEdit(placeholderText="Filter tables…", clearButtonEnabled=True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        refresh_btn = tool_button("module_settings_btn", text="⟳", tool_tip="Refresh")
        refresh_btn.clicked.connect(self.refresh)
        row.addWidget(self.filter_edit, 1)
        row.addWidget(refresh_btn)
        reserve_controls_space(row)

        self.db_tree = QTreeWidget(objectName="db_tree")
        self.db_tree.setHeaderHidden(True)
        self.db_tree.setColumnCount(2)
        self.db_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.db_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.db_tree.header().setStretchLastSection(False)
        self.db_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.db_tree.customContextMenuRequested.connect(self._show_menu)
        self.db_tree.itemClicked.connect(self._on_clicked)
        self.db_tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.db_tree.itemExpanded.connect(self._on_expanded)
        layout.addWidget(self.db_tree, 1)

        for signal in (module.databases_changed, module.database_changed, module.schema_changed):
            signal.connect(self.refresh)
        module.data_changed.connect(lambda _table: self.refresh())
        module.table_changed.connect(self._highlight_current)
        self.refresh()

    def refresh(self) -> None:
        module = self.module
        expanded = self._expanded_nodes()
        scroll = self.db_tree.verticalScrollBar().value()
        self.db_tree.clear()
        current_id = module.connection.entry.item_id if module.connection else None
        for entry in module.databases.entries():
            item = QTreeWidgetItem([entry.name, size_text(entry.size_bytes)])
            item.setIcon(0, load_icon("database.svg"))
            item.setData(0, Qt.ItemDataRole.UserRole, ("database", entry.item_id))
            item.setToolTip(0, f"{entry.name}{' (read-only)' if entry.is_read_only else ''}")
            self.db_tree.addTopLevelItem(item)
            if entry.item_id == current_id:
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
                self._add_objects(item, expanded)
                item.setExpanded(True)
            else:
                item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        self._apply_filter()
        self._highlight_current()
        self.db_tree.verticalScrollBar().setValue(scroll)

    def _add_objects(self, database_item: QTreeWidgetItem, expanded: set) -> None:
        connection = self.module.connection
        objects = self.module.attempt("Read database objects", connection.objects) or []
        is_first_view = not expanded
        for object_type, title in self.GROUPS:
            members = [item for item in objects if item.object_type == object_type]
            group = QTreeWidgetItem(database_item, [title, str(len(members))])
            group.setData(0, Qt.ItemDataRole.UserRole, ("group", object_type))
            for member in members:
                if object_type in ("table", "view"):
                    count = self.module.attempt("Count rows", connection.row_count, member.name)
                    detail = f"{count:,} rows" if isinstance(count, int) else "?"
                else:
                    detail = f"on {member.table_name}"
                child = QTreeWidgetItem(group, [member.name, detail])
                child.setData(0, Qt.ItemDataRole.UserRole, (object_type, member.name))
                if object_type in ("table", "view"):
                    for column in connection.columns(member.name):
                        key = " 🔑" if column.primary_key_position else ""
                        column_item = QTreeWidgetItem(child, [f"{column.name}{key}", column.declared_type])
                        column_item.setData(0, Qt.ItemDataRole.UserRole, ("column", member.name, column.name))
                node = (object_type, member.name)
                child.setExpanded(node in expanded)
            group.setExpanded(("group", object_type) in expanded or (is_first_view and object_type == "table"))

    def _expanded_nodes(self) -> set:
        nodes = set()
        def walk(item: QTreeWidgetItem) -> None:
            for index in range(item.childCount()):
                child = item.child(index)
                if child.isExpanded():
                    nodes.add(child.data(0, Qt.ItemDataRole.UserRole))
                walk(child)
        walk(self.db_tree.invisibleRootItem())
        return nodes

    def _apply_filter(self) -> None:
        text = self.filter_edit.text().strip().lower()
        root = self.db_tree.invisibleRootItem()
        for database_index in range(root.childCount()):
            database_item = root.child(database_index)
            for group_index in range(database_item.childCount()):
                group = database_item.child(group_index)
                for member_index in range(group.childCount()):
                    member = group.child(member_index)
                    member.setHidden(bool(text) and text not in member.text(0).lower())

    def _highlight_current(self) -> None:
        table = self.module.table
        for item in self._iter_items():
            node = item.data(0, Qt.ItemDataRole.UserRole)
            if node and node[0] in ("table", "view") and node[1] == table:
                self.db_tree.blockSignals(True)
                self.db_tree.setCurrentItem(item)
                self.db_tree.blockSignals(False)
                return

    def _iter_items(self):
        stack = [self.db_tree.invisibleRootItem()]
        while stack:
            item = stack.pop()
            for index in range(item.childCount()):
                stack.append(item.child(index))
                yield item.child(index)

    def _ensure_open(self, item_id: int) -> bool:
        module = self.module
        if module.connection is None or module.connection.entry.item_id != item_id:
            module.open_database(item_id)
        return module.connection is not None and module.connection.entry.item_id == item_id

    def _on_clicked(self, item: QTreeWidgetItem) -> None:
        node = item.data(0, Qt.ItemDataRole.UserRole)
        if node[0] == "database":
            self._ensure_open(node[1])
        elif node[0] in ("table", "view"):
            self.module.select_table(node[1])
        elif node[0] == "column":
            self.module.select_table(node[1])

    def _on_double_clicked(self, item: QTreeWidgetItem) -> None:
        node = item.data(0, Qt.ItemDataRole.UserRole)
        if node[0] in ("table", "view"):
            self.module.show_block("data")
        elif node[0] in ("index", "trigger"):
            self.module.show_block("structure")

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        node = item.data(0, Qt.ItemDataRole.UserRole)
        if node and node[0] == "database" and item.childCount() == 0:
            self._ensure_open(node[1])                 # refresh() rebuilds the tree with its objects

    def _show_menu(self, position) -> None:
        item = self.db_tree.itemAt(position)
        if item is None:
            return
        module = self.module
        node = item.data(0, Qt.ItemDataRole.UserRole)
        kind = node[0]
        menu = QMenu(self)
        if kind == "database":
            entry = module.databases.entry(node[1])
            is_user_db = entry is not None and not entry.is_read_only
            add_menu_action(menu, "Open", lambda: self._ensure_open(node[1]))
            add_menu_action(menu, "New table…", lambda: self._ensure_open(node[1]) and module.new_table(), is_user_db)
            add_menu_action(menu, "Properties", lambda: self._ensure_open(node[1]) and module.show_block("properties"))
            add_menu_action(menu, "Rename…", lambda: self._ensure_open(node[1]) and module.rename_database(), is_user_db)
            add_menu_action(menu, "Export a copy…", lambda: self._ensure_open(node[1]) and module.export_database())
            menu.addSeparator()
            add_menu_action(menu, "Delete database…", lambda: module.delete_database(node[1]), is_user_db)
        elif kind == "group":
            add_menu_action(menu, "New table…", module.new_table, module.can_write())
        elif kind in ("table", "view"):
            name = node[1]
            is_table = kind == "table"
            can_write = module.can_write()
            add_menu_action(menu, "Open data", lambda: (module.select_table(name), module.show_block("data")))
            add_menu_action(menu, "Record form", lambda: (module.select_table(name), module.show_block("record")))
            add_menu_action(menu, "Structure", lambda: (module.select_table(name), module.show_block("structure")))
            menu.addSeparator()
            add_menu_action(menu, "Add column…", lambda: module.add_column(name), is_table and can_write)
            add_menu_action(menu, "Add index…", lambda: module.add_index(name), is_table and can_write)
            add_menu_action(menu, "Rename…", lambda: module.rename_table(name), is_table and can_write)
            menu.addSeparator()
            add_menu_action(menu, "Copy CREATE statement", lambda: QApplication.clipboard().setText(module.connection.object_sql(name)))
            add_menu_action(menu, "Copy name", lambda: QApplication.clipboard().setText(name))
            menu.addSeparator()
            add_menu_action(menu, f"Drop {kind}…", lambda: module.drop_object(kind, name), can_write)
        elif kind in ("index", "trigger"):
            name = node[1]
            add_menu_action(menu, "Copy CREATE statement", lambda: QApplication.clipboard().setText(module.connection.object_sql(name)))
            add_menu_action(menu, f"Drop {kind}…", lambda: module.drop_object(kind, name), module.can_write())
        elif kind == "column":
            table, column = node[1], node[2]
            is_table = module.connection.object_type(table) == "table"
            add_menu_action(menu, "Rename column…", lambda: module.rename_column(table, column), is_table and module.can_write())
            add_menu_action(menu, "Drop column…", lambda: module.drop_column(table, column), is_table and module.can_write())
            add_menu_action(menu, "Copy name", lambda: QApplication.clipboard().setText(column))
        menu.exec(self.db_tree.viewport().mapToGlobal(position))
# --- END [2.1] ---


# ==============================================================================
# [2.2] data_block
# ==============================================================================
class DataBlock(QWidget):
    def __init__(self, module) -> None:
        super().__init__()
        self.module = module
        self.page = None
        self.page_offset = 0
        self._is_own_edit = False
        self._is_following = False
        layout, row = block_layout(self)
        self.table_combo = QComboBox(toolTip="Table or view")
        self.table_combo.setMinimumWidth(120)
        self.table_combo.activated.connect(lambda: module.select_table(self.table_combo.currentData()))
        self.filter_edit = QLineEdit(placeholderText="Filter (SQL condition, e.g. price > 10)", clearButtonEnabled=True)
        self.filter_edit.returnPressed.connect(self._apply_filter)
        row.addWidget(self.table_combo)
        row.addWidget(self.filter_edit, 1)
        self.prev_btn = add_button(row, "◀", lambda: self._go_page(-1), "Previous rows")
        self.page_label = QLabel(objectName="control_label")
        row.addWidget(self.page_label)
        self.next_btn = add_button(row, "▶", lambda: self._go_page(1), "Next rows")
        self.add_row_btn = add_button(row, "+ Row", self.add_row, "Add a row (defaults for every column)")
        self.delete_btn = add_button(row, "Delete", self.delete_selected, "Delete the selected rows")
        reserve_controls_space(row)

        self.data_table_view = table_view("data_table_view")
        self.data_table_view.horizontalHeader().setSortIndicatorShown(True)
        self.data_table_view.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self.data_table_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.data_table_view.customContextMenuRequested.connect(self._show_menu)
        layout.addWidget(self.data_table_view, 1)

        module.database_changed.connect(self._on_table_changed)
        module.table_changed.connect(self._on_table_changed)
        module.schema_changed.connect(self._on_table_changed)
        module.query_changed.connect(self._on_query_changed)
        module.data_changed.connect(self._on_data_changed)
        module.row_selected.connect(self._follow_row)
        self._on_table_changed()

    # --- loading ---
    def _fill_table_combo(self) -> None:
        self.table_combo.blockSignals(True)
        self.table_combo.clear()
        connection = self.module.connection
        if connection is not None:
            for item in connection.objects():
                if item.object_type in ("table", "view"):
                    self.table_combo.addItem(item.name if item.object_type == "table" else f"{item.name} (view)", item.name)
        self.table_combo.setCurrentIndex(max(self.table_combo.findData(self.module.table), 0))
        self.table_combo.blockSignals(False)

    def _on_table_changed(self) -> None:
        self._fill_table_combo()
        self.filter_edit.setText(self.module.where)
        self.page_offset = 0
        self.refresh()

    def _on_query_changed(self) -> None:
        self.page_offset = 0
        self.refresh()

    def _on_data_changed(self, table: str) -> None:
        if table == self.module.table and not self._is_own_edit:
            self.refresh()

    def refresh(self) -> None:
        module = self.module
        connection, table = module.connection, module.table
        page = None
        if connection is not None and table:
            page = module.attempt(f"Load {table}", connection.fetch_rows, table, PAGE_SIZE, self.page_offset,
                                  module.order_by, module.descending, module.where)
        self.page = page
        if page is None:
            self.data_table_view.setModel(RowsModel([], []))
            self.page_label.setText("no table" if not table else "—")
            for button in (self.prev_btn, self.next_btn, self.add_row_btn, self.delete_btn):
                button.setEnabled(False)
            return
        if self.page_offset and self.page_offset >= page.total_rows:          # rows deleted under this page
            self.page_offset = max(0, (page.total_rows - 1) // PAGE_SIZE * PAGE_SIZE)
            self.refresh()
            return
        model = RowsModel(page.columns, page.rows, self.page_offset + 1, page.keys,
                          self._on_edit if page.is_editable else None)
        self.data_table_view.setModel(model)
        self.data_table_view.selectionModel().currentRowChanged.connect(self._on_current_row_changed)
        header = self.data_table_view.horizontalHeader()
        if module.order_by in page.columns:
            header.setSortIndicator(page.columns.index(module.order_by),
                                    Qt.SortOrder.DescendingOrder if module.descending else Qt.SortOrder.AscendingOrder)
        else:
            header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        fit_columns(self.data_table_view)
        shown = len(page.rows)
        self.page_label.setText(f"{self.page_offset + 1:,}–{self.page_offset + shown:,} of {page.total_rows:,}"
                                if shown else f"0 of {page.total_rows:,}")
        self.prev_btn.setEnabled(self.page_offset > 0)
        self.next_btn.setEnabled(self.page_offset + shown < page.total_rows)
        self.add_row_btn.setEnabled(page.is_editable or (module.can_write() and connection.object_type(table) == "table"))
        self.delete_btn.setEnabled(page.is_editable)
        self._follow_row()

    # --- selection follows the module's current row (and the other way round) ---
    def _on_current_row_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if current.isValid():
            self.module.select_row_at(self.page_offset + current.row())

    def _follow_row(self) -> None:
        offset, page = self.module.row_offset, self.page
        if offset is None or page is None:
            return
        local = offset - self.page_offset
        if 0 <= local < len(page.rows):
            selection = self.data_table_view.selectionModel()
            selection.blockSignals(True)
            column = max(self.data_table_view.currentIndex().column(), 0)
            self.data_table_view.setCurrentIndex(self.data_table_view.model().index(local, column))
            selection.blockSignals(False)
            self.data_table_view.viewport().update()
            self.data_table_view.scrollTo(self.data_table_view.currentIndex())
        elif not self._is_following and 0 <= offset < page.total_rows:
            self._is_following = True
            self.page_offset = offset // PAGE_SIZE * PAGE_SIZE
            self.refresh()
            self._is_following = False

    # --- editing ---
    def _on_edit(self, row: int, column: int, text) -> tuple[bool, object]:
        module, page = self.module, self.page
        value = None if text is None else coerce_value(str(text), page.declared_types[column])
        self._is_own_edit = True
        try:
            is_ok = module.attempt(f"Edit {module.table}.{page.columns[column]}", module.connection.update_cell,
                                   module.table, page.keys[row], page.columns[column], value) is not None
            if is_ok:
                module.after_write(module.table)
        finally:
            self._is_own_edit = False
        return is_ok, value

    def add_row(self) -> None:
        module = self.module
        if module.attempt(f"Add row to {module.table}", module.connection.insert_row, module.table) is None:
            return
        module.after_write(module.table)
        if self.page is not None and not module.order_by:
            module.select_row_at(self.page.total_rows - 1)          # natural order: the new row is last
        module.report(f"Added a row to {module.table}")

    def _selected_keys(self) -> list[dict]:
        if self.page is None:
            return []
        rows = sorted({index.row() for index in self.data_table_view.selectedIndexes()})
        return [self.page.keys[row] for row in rows if self.page.keys[row] is not None]

    def delete_selected(self) -> None:
        keys, module = self._selected_keys(), self.module
        if not keys or not module.confirm("Delete rows", f"Delete {len(keys)} row(s) from {module.table}?"):
            return
        deleted = module.attempt(f"Delete rows from {module.table}", module.connection.delete_rows, module.table, keys)
        if deleted is not None:
            module.after_write(module.table)
            module.report(f"Deleted {deleted} row(s) from {module.table}")

    def set_null(self) -> None:
        model = self.data_table_view.model()
        for index in self.data_table_view.selectedIndexes():
            model.setData(index, None)

    # --- query ---
    def _apply_filter(self) -> None:
        self.module.set_query(self.filter_edit.text().strip(), self.module.order_by, self.module.descending)

    def _on_header_clicked(self, section: int) -> None:
        if self.page is None or section >= len(self.page.columns):
            return
        module, name = self.module, self.page.columns[section]
        if module.order_by != name:
            module.set_query(module.where, name, False)
        elif not module.descending:
            module.set_query(module.where, name, True)
        else:
            module.set_query(module.where, None, False)            # third click: back to natural order

    def _go_page(self, step: int) -> None:
        self.page_offset = max(0, self.page_offset + step * PAGE_SIZE)
        self.refresh()

    # --- menu / export ---
    def _show_menu(self, position) -> None:
        is_editable = self.page is not None and self.page.is_editable
        menu = QMenu(self)
        add_menu_action(menu, "Set to NULL", self.set_null, is_editable)
        add_menu_action(menu, "Copy", lambda: copy_selection(self.data_table_view))
        menu.addSeparator()
        add_menu_action(menu, "Add row", self.add_row, self.add_row_btn.isEnabled())
        add_menu_action(menu, "Delete selected rows…", self.delete_selected, is_editable)
        menu.addSeparator()
        add_menu_action(menu, "Show record form", lambda: self.module.show_block("record"))
        add_menu_action(menu, "Save numeric columns as a Graph dataset", self.save_as_dataset, self.page is not None)
        menu.exec(self.data_table_view.viewport().mapToGlobal(position))

    def save_as_dataset(self) -> None:
        """Every row of the current query, numeric columns only, into the library as a Graph dataset."""
        from app.modules.graph.datasets import save_dataset
        module = self.module
        page = module.attempt("Read rows", module.connection.fetch_rows, module.table, max(self.page.total_rows, 1), 0,
                              module.order_by, module.descending, module.where)
        if page is None:
            return
        numeric = [index for index in range(len(page.columns))
                   if any(isinstance(row[index], (int, float)) for row in page.rows)
                   and all(row[index] is None or isinstance(row[index], (int, float)) for row in page.rows)]
        if not numeric or not page.rows:
            module.report_error("Save as dataset", ValueError(f"{module.table} has no numeric columns to plot"))
            return
        data = np.array([[math.nan if row[index] is None else float(row[index]) for index in numeric] for row in page.rows])
        item = module.attempt("Save as dataset", save_dataset, module.table, [page.columns[index] for index in numeric], data,
                              {"source": f"{module.connection.entry.name} › {module.table}"})
        if item is not None:
            module.report(f"Saved dataset “{item.name}” ({len(page.rows):,} rows) — open it in a Graph tool")
# --- END [2.2] ---


# ==============================================================================
# [2.3] record_block
# ==============================================================================
class RecordBlock(QWidget):
    def __init__(self, module) -> None:
        super().__init__()
        self.module = module
        self.columns = []
        self.fields: dict[str, tuple[QLineEdit, QCheckBox]] = {}
        self.key: dict | None = None
        self.dirty: set[str] = set()
        self._is_own_edit = False
        layout, row = block_layout(self)
        self.prev_btn = add_button(row, "◀", lambda: self._step(-1), "Previous row")
        self.record_label = QLabel(objectName="control_label", alignment=Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.record_label, 1)
        self.next_btn = add_button(row, "▶", lambda: self._step(1), "Next row")
        reserve_controls_space(row)

        self.record_form_panel = QWidget(objectName="record_form_panel")
        self.form = QFormLayout(self.record_form_panel)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scroll = QScrollArea(widgetResizable=True, frameShape=QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.record_form_panel)
        layout.addWidget(scroll, 1)
        actions = QHBoxLayout()                         # actions under the form, where there is room for them
        self.new_btn = add_button(actions, "New", self.new_record, "Add a row and edit it here")
        actions.addStretch()
        self.revert_btn = add_button(actions, "Revert", self.load, "Undo unsaved changes")
        self.save_btn = add_button(actions, "Save", self.save, "Save the changed fields")
        self.delete_btn = add_button(actions, "Delete", self.delete_record, "Delete this row")
        layout.addLayout(actions)

        for signal in (module.database_changed, module.table_changed, module.schema_changed):
            signal.connect(self.rebuild)
        module.row_selected.connect(self._on_row_selected)
        module.query_changed.connect(self.load)
        module.data_changed.connect(lambda table: self.load() if table == module.table and not self._is_own_edit else None)
        self.rebuild()

    def rebuild(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.fields.clear()
        module = self.module
        self.columns = []
        if module.connection is not None and module.table:
            self.columns = module.attempt("Read columns", module.connection.columns, module.table) or []
        for column in self.columns:
            edit = QLineEdit()
            edit.textEdited.connect(lambda _text, name=column.name: self._mark_dirty(name))
            null_check = QCheckBox("NULL", toolTip="Store no value (NULL)")
            null_check.toggled.connect(lambda checked, name=column.name, edit=edit: (edit.setEnabled(not checked), self._mark_dirty(name)))
            field_row = QHBoxLayout()
            field_row.addWidget(edit, 1)
            field_row.addWidget(null_check)
            label = QLabel(f"{column.name}{' 🔑' if column.primary_key_position else ''}",
                           toolTip=f"{column.declared_type or 'any type'}{' · not null' if column.is_not_null else ''}")
            self.form.addRow(label, field_row)
            self.fields[column.name] = (edit, null_check)
        self.load()

    def _mark_dirty(self, name: str) -> None:
        self.dirty.add(name)
        self.save_btn.setEnabled(self.key is not None)
        self.revert_btn.setEnabled(True)

    def _on_row_selected(self) -> None:
        self.save()                                     # don't lose edits when moving to another row
        self.load()

    def load(self) -> None:
        module = self.module
        self.dirty.clear()
        self.key, page = None, None
        if module.connection is not None and module.table and module.row_offset is not None:
            page = module.attempt("Load row", module.connection.fetch_rows, module.table, 1, module.row_offset,
                                  module.order_by, module.descending, module.where)
        has_row = page is not None and bool(page.rows)
        for name, (edit, null_check) in self.fields.items():
            value = page.rows[0][page.columns.index(name)] if has_row and name in page.columns else None
            for widget in (edit, null_check):
                widget.blockSignals(True)
            null_check.setChecked(has_row and value is None)
            edit.setText("" if value is None else (f"BLOB · {len(value):,} bytes" if isinstance(value, bytes) else str(value)))
            is_editable = has_row and page.keys[0] is not None and not isinstance(value, bytes)
            edit.setEnabled(is_editable and value is not None)
            null_check.setEnabled(is_editable)
            for widget in (edit, null_check):
                widget.blockSignals(False)
        if has_row:
            self.key = page.keys[0]
            self.record_label.setText(f"Row {module.row_offset + 1:,} of {page.total_rows:,}"
                                      + ("" if self.key is not None else " · read-only"))
        else:
            self.record_label.setText("Select a row in the data grid, or press New" if module.table else "No table selected")
        total = page.total_rows if page is not None else 0
        self.prev_btn.setEnabled(has_row and module.row_offset > 0)
        self.next_btn.setEnabled(has_row and module.row_offset + 1 < total)
        self.new_btn.setEnabled(module.can_write() and module.table is not None
                                and module.connection.object_type(module.table) == "table")
        self.delete_btn.setEnabled(self.key is not None)
        self.save_btn.setEnabled(False)
        self.revert_btn.setEnabled(False)

    def _values(self, names) -> dict:
        types = {column.name: column.declared_type for column in self.columns}
        return {name: None if self.fields[name][1].isChecked() else coerce_value(self.fields[name][0].text(), types[name])
                for name in names}

    def save(self) -> None:
        module = self.module
        if not self.dirty or self.key is None:
            return
        values = self._values(sorted(self.dirty))
        self._is_own_edit = True
        try:
            if module.attempt(f"Save row in {module.table}", module.connection.update_row, module.table, self.key, values) is not None:
                self.dirty.clear()
                self.save_btn.setEnabled(False)
                self.revert_btn.setEnabled(False)
                module.after_write(module.table)
                module.report(f"Saved row {module.row_offset + 1:,} of {module.table}")
        finally:
            self._is_own_edit = False

    def _step(self, step: int) -> None:
        if self.module.row_offset is not None:
            self.module.select_row_at(max(0, self.module.row_offset + step))

    def new_record(self) -> None:
        module = self.module
        self.save()
        if module.attempt(f"Add row to {module.table}", module.connection.insert_row, module.table) is None:
            return
        module.after_write(module.table)
        if not module.order_by:
            total = module.attempt("Count rows", module.connection.row_count, module.table, module.where)
            if isinstance(total, int) and total:
                module.select_row_at(total - 1)
        first_edit = next(iter(self.fields.values()), (None, None))[0]
        if first_edit is not None:
            first_edit.setFocus()
        module.report(f"Added a row to {module.table}")

    def delete_record(self) -> None:
        module = self.module
        if self.key is None or not module.confirm("Delete row", f"Delete row {module.row_offset + 1:,} from {module.table}?"):
            return
        if module.attempt(f"Delete row from {module.table}", module.connection.delete_rows, module.table, [self.key]) is not None:
            self.dirty.clear()
            module.after_write(module.table)
            total = module.connection.row_count(module.table, module.where)
            module.select_row_at(min(module.row_offset, total - 1) if total else None)
            self.load()

    def hideEvent(self, event) -> None:
        self.save()
        super().hideEvent(event)
# --- END [2.3] ---


# ==============================================================================
# [2.4] structure_block
# ==============================================================================
class StructureBlock(QWidget):
    def __init__(self, module) -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        self.structure_title_label = QLabel(objectName="structure_title_label")
        row.addWidget(self.structure_title_label, 1)
        self.add_column_btn = add_button(row, "+ Column", lambda: module.add_column(module.table))
        self.add_index_btn = add_button(row, "+ Index", lambda: module.add_index(module.table))
        self.rename_btn = add_button(row, "Rename…", lambda: module.rename_table(module.table))
        self.drop_btn = add_button(row, "Drop…", lambda: module.drop_object(module.connection.object_type(module.table), module.table))
        reserve_controls_space(row)

        splitter = QSplitter(Qt.Orientation.Vertical, objectName="pane_splitter")
        self.columns_table = QTableWidget(0, 5)
        self.columns_table.setHorizontalHeaderLabels(["Column", "Type", "Primary key", "Not null", "Default"])
        self.columns_table.horizontalHeader().setStretchLastSection(True)
        self.columns_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.columns_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.columns_table.verticalHeader().hide()
        self.columns_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.columns_table.customContextMenuRequested.connect(self._show_column_menu)
        self.details_tree = QTreeWidget()
        self.details_tree.setHeaderLabels(["Index / foreign key", "Details"])
        self.details_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.details_tree.customContextMenuRequested.connect(self._show_details_menu)
        self.structure_sql_view = QPlainTextEdit(objectName="structure_sql_view", readOnly=True)
        for widget in (self.columns_table, self.details_tree, self.structure_sql_view):
            splitter.addWidget(widget)
        splitter.setSizes([3, 2, 2])
        layout.addWidget(splitter, 1)

        for signal in (module.database_changed, module.table_changed, module.schema_changed):
            signal.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        module = self.module
        connection, table = module.connection, module.table
        object_type = connection.object_type(table) if connection is not None and table else None
        is_table = object_type == "table" and module.can_write()
        for button in (self.add_column_btn, self.add_index_btn, self.rename_btn):
            button.setEnabled(is_table)
        self.drop_btn.setEnabled(object_type is not None and module.can_write())
        self.columns_table.setRowCount(0)
        self.details_tree.clear()
        if object_type is None:
            self.structure_title_label.setText("No table selected")
            self.structure_sql_view.clear()
            return
        self.structure_title_label.setText(f"{table}  ·  {object_type}")
        columns = module.attempt("Read columns", connection.columns, table) or []
        for column in columns:
            row = self.columns_table.rowCount()
            self.columns_table.insertRow(row)
            cells = (column.name, column.declared_type, f"yes ({column.primary_key_position})" if column.primary_key_position else "",
                     "yes" if column.is_not_null else "", "" if column.default is None else str(column.default))
            for index, text in enumerate(cells):
                self.columns_table.setItem(row, index, QTableWidgetItem(text))
        self.columns_table.resizeColumnsToContents()
        if object_type == "table":
            indexes = module.attempt("Read indexes", connection.indexes, table) or []
            group = QTreeWidgetItem(self.details_tree, [f"Indexes ({len(indexes)})", ""])
            for name, is_unique, index_columns in indexes:
                item = QTreeWidgetItem(group, [name, ("unique · " if is_unique else "") + ", ".join(index_columns)])
                item.setData(0, Qt.ItemDataRole.UserRole, name)
            keys = module.attempt("Read foreign keys", connection.foreign_keys, table) or []
            fk_group = QTreeWidgetItem(self.details_tree, [f"Foreign keys ({len(keys)})", ""])
            for column, target_table, target_column in keys:
                QTreeWidgetItem(fk_group, [column, f"→ {target_table}.{target_column or '(primary key)'}"])
            self.details_tree.expandAll()
            self.details_tree.resizeColumnToContents(0)
        sql_parts = [connection.object_sql(table)] + [item.sql for item in connection.objects()
                                                     if item.table_name == table and item.object_type in ("index", "trigger") and item.sql]
        self.structure_sql_view.setPlainText(";\n\n".join(part for part in sql_parts if part) + ";")

    def _show_column_menu(self, position) -> None:
        row = self.columns_table.rowAt(position.y())
        module = self.module
        if row < 0 or module.table is None:
            return
        column = self.columns_table.item(row, 0).text()
        can_edit = module.can_write() and module.connection.object_type(module.table) == "table"
        menu = QMenu(self)
        add_menu_action(menu, "Rename column…", lambda: module.rename_column(module.table, column), can_edit)
        add_menu_action(menu, "Drop column…", lambda: module.drop_column(module.table, column), can_edit)
        add_menu_action(menu, "Copy name", lambda: QApplication.clipboard().setText(column))
        menu.exec(self.columns_table.viewport().mapToGlobal(position))

    def _show_details_menu(self, position) -> None:
        item = self.details_tree.itemAt(position)
        name = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not name:
            return
        menu = QMenu(self)
        add_menu_action(menu, "Drop index…", lambda: self.module.drop_object("index", name), self.module.can_write())
        menu.exec(self.details_tree.viewport().mapToGlobal(position))
# --- END [2.4] ---


# ==============================================================================
# [2.5] sql_block
# ==============================================================================
class SqlBlock(QWidget):
    def __init__(self, module) -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        self.run_btn = add_button(row, "▶ Run", self.run, "Run everything, or just the selected text (Ctrl+Enter)")
        self.sql_history_combo = QComboBox(toolTip="Recent statements")
        self.sql_history_combo.setMinimumWidth(120)
        self.sql_history_combo.activated.connect(self._on_history_chosen)
        row.addWidget(self.sql_history_combo, 1)
        add_button(row, "Clear", lambda: self.sql_editor.clear())
        reserve_controls_space(row)

        splitter = QSplitter(Qt.Orientation.Vertical, objectName="pane_splitter")
        self.sql_editor = QPlainTextEdit(objectName="sql_editor", placeholderText="SELECT * FROM …   (Ctrl+Enter runs)")
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(keys), self.sql_editor, context=Qt.ShortcutContext.WidgetShortcut).activated.connect(self.run)
        results_panel = QWidget()
        results_layout = QVBoxLayout(results_panel)
        results_layout.setContentsMargins(0, 0, 0, 0)
        self.sql_results_view = table_view("sql_results_view")
        self.sql_results_view.setModel(RowsModel([], []))
        self.sql_message_label = QLabel(objectName="db_status_label", wordWrap=True)
        results_layout.addWidget(self.sql_results_view, 1)
        results_layout.addWidget(self.sql_message_label)
        splitter.addWidget(self.sql_editor)
        splitter.addWidget(results_panel)
        splitter.setSizes([1, 2])
        layout.addWidget(splitter, 1)

        self._fill_history()
        module.table_changed.connect(self._suggest)
        self._suggest()

    def _suggest(self) -> None:
        if not self.sql_editor.toPlainText().strip() and self.module.table:
            self.sql_editor.setPlainText(f"SELECT * FROM {quote_identifier(self.module.table)} LIMIT 100;")

    def _fill_history(self) -> None:
        self.sql_history_combo.clear()
        self.sql_history_combo.addItem("History…", None)
        for sql in self.module.setting("sql_history", []):
            self.sql_history_combo.addItem(" ".join(sql.split())[:80], sql)

    def _on_history_chosen(self) -> None:
        sql = self.sql_history_combo.currentData()
        if sql:
            self.sql_editor.setPlainText(sql)
        self.sql_history_combo.setCurrentIndex(0)

    def run(self) -> None:
        module = self.module
        if module.connection is None:
            self._show_message("Open a database first", is_error=True)
            return
        cursor = self.sql_editor.textCursor()
        sql = (cursor.selectedText().replace(" ", "\n") if cursor.hasSelection() else self.sql_editor.toPlainText()).strip()
        if not sql:
            return
        results, error = module.connection.execute_script(sql)
        shown = next((result for result in reversed(results) if result.has_rows), None)
        self.sql_results_view.setModel(RowsModel(shown.columns, [list(row) for row in shown.rows]) if shown else RowsModel([], []))
        fit_columns(self.sql_results_view)
        if error:
            self._show_message(error, is_error=True)
            module.report(f"SQL failed: {error}", is_error=True)
        else:
            total_ms = sum(result.elapsed_ms for result in results)
            changed = sum(result.affected_rows for result in results if not result.has_rows)
            parts = [f"{len(results)} statement(s)", f"{total_ms:.1f} ms"]
            if shown:
                parts.insert(1, f"{len(shown.rows):,} row(s) returned{' (first 5,000)' if shown.is_truncated else ''}")
            if any(not result.has_rows for result in results):
                parts.insert(1, f"{changed:,} row(s) changed")
            self._show_message(" · ".join(parts))
        history = [sql] + [item for item in module.setting("sql_history", []) if item != sql]
        module.save_setting("sql_history", history[:SQL_HISTORY_SIZE])
        self._fill_history()
        if any(not result.has_rows for result in results):
            module.after_write(module.table, schema=True)

    def _show_message(self, text: str, is_error: bool = False) -> None:
        self.sql_message_label.setText(text)
        set_style_property(self.sql_message_label, "is_error", is_error)

    def block_state(self) -> dict:
        return {"sql": self.sql_editor.toPlainText()}

    def restore_block_state(self, state: dict) -> None:
        if state.get("sql"):
            self.sql_editor.setPlainText(state["sql"])
# --- END [2.5] ---


# ==============================================================================
# [2.6] properties_block
# ==============================================================================
class PropertiesBlock(QWidget):
    JOURNAL_MODES = ("delete", "truncate", "persist", "memory", "wal", "off")

    def __init__(self, module) -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        row.addWidget(QLabel("Database properties", objectName="structure_title_label"), 1)
        self.check_btn = add_button(row, "Integrity check", self.check, "Look for corruption")
        self.vacuum_btn = add_button(row, "Compact", self.compact, "Rebuild the file without free pages (VACUUM)")
        add_button(row, "Export…", module.export_database, "Save a copy somewhere else")
        reserve_controls_space(row)
        panel = QWidget(objectName="record_form_panel")
        self.form = QFormLayout(panel)
        scroll = QScrollArea(widgetResizable=True, frameShape=QScrollArea.Shape.NoFrame)
        scroll.setWidget(panel)
        layout.addWidget(scroll, 1)
        self.result_label = QLabel(objectName="db_status_label", wordWrap=True)
        layout.addWidget(self.result_label)

        for signal in (module.database_changed, module.schema_changed):
            signal.connect(self.refresh)
        module.data_changed.connect(lambda _table: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        module = self.module
        properties = module.attempt("Read properties", module.connection.properties) if module.connection else None
        for button in (self.check_btn, self.vacuum_btn):
            button.setEnabled(properties is not None)
        self.vacuum_btn.setEnabled(module.can_write())
        if not properties:
            self.form.addRow(QLabel("No database open"))
            return
        for name, value in properties.items():
            if name == "User version":
                spin = QSpinBox(minimum=0, maximum=2**31 - 1, value=int(value), enabled=module.can_write(),
                                toolTip="A number apps use to track their own schema version")
                spin.editingFinished.connect(lambda spin=spin: self._set("user_version", spin.value()))
                self.form.addRow(name, spin)
            elif name == "Journal mode":
                combo = QComboBox(enabled=module.can_write())
                combo.addItems(self.JOURNAL_MODES)
                combo.setCurrentText(str(value).lower())
                combo.activated.connect(lambda _index, combo=combo: self._set("journal_mode", combo.currentText()))
                self.form.addRow(name, combo)
            else:
                text = size_text(value) if name == "Size" else (f"{value:,}" if isinstance(value, int) else str(value))
                label = QLabel(text, textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse, wordWrap=True)
                self.form.addRow(name, label)

    def _set(self, name: str, value) -> None:
        if self.module.attempt(f"Set {name}", self.module.connection.set_pragma, name, value) is not None:
            self.result_label.setText(f"{name} set to {value}")
            self.refresh()

    def check(self) -> None:
        problems = self.module.attempt("Integrity check", self.module.connection.integrity_check)
        if problems is not None:
            self.result_label.setText("Integrity check: ok" if problems == ["ok"] else "Problems:\n" + "\n".join(problems[:20]))

    def compact(self) -> None:
        if self.module.attempt("Compact", self.module.connection.vacuum) is not None:
            self.module.after_write()
            self.result_label.setText("Compacted")
            self.refresh()
# --- END [2.6] ---


# ==============================================================================
# [3.1] block_specs
# ==============================================================================
def block_specs(module) -> list[BlockSpec]:
    return [
        BlockSpec("explorer", "Explorer", "Databases, tables, views, indexes and columns as a tree", lambda: ExplorerBlock(module)),
        BlockSpec("data", "Data grid", "Rows of the current table: edit, sort, filter, add, delete", lambda: DataBlock(module)),
        BlockSpec("record", "Record form", "The current row as a form: edit field by field, walk rows", lambda: RecordBlock(module)),
        BlockSpec("structure", "Structure", "Columns, indexes, foreign keys and the CREATE statement", lambda: StructureBlock(module)),
        BlockSpec("sql", "SQL console", "Run SQL; results, timing and history", lambda: SqlBlock(module)),
        BlockSpec("properties", "Properties", "File, size, version, journal mode; check, compact, export", lambda: PropertiesBlock(module)),
    ]
# --- END [3.1] ---
