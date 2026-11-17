# ==============================================================================
# FILE: app/modules/db_editor/view.py
# OVERVIEW: Database Editor module: create, manage, view and edit the user's SQLite databases. A toolbar
#           on top; everything else is blocks (explorer tree, data grid, record form, structure, SQL
#           console, properties) the user can split, rearrange and replace.
# PURPOSE:
#   1. Toolbar: database picker, New database, Import, + Table, a ⋯ menu (rename / export / delete /
#      properties) and a status line that reports every action's outcome (errors are never silent).
#   2. Shared state for the blocks: the open connection, current table, the table query (filter, sort)
#      and the current row (row_offset within that query); signals tell blocks what changed.
#   3. Actions used by toolbar and blocks: new / import / export / rename / delete database, new / rename
#      / drop table, add / rename / drop column, add index, show_block(kind).
# CONTRACT:
#   - Requires: app.modules.base.ModuleView, app.modules.db_editor (engine, blocks, dialogs),
#               app.clients.library, app.data.activity_log, app.ui (ui_tools, canvas.tile_canvas),
#               QSS selectors #db_editor_panel, #db_status_label
#   - Exposes: DbEditorModule(parent=None, context=None); signals databases_changed, database_changed,
#              table_changed, query_changed, row_selected, schema_changed, data_changed(str);
#              open_database(item_id); select_table(name); set_query(); select_row_at(offset); attempt();
#              after_write(); show_block(kind); connection, table, where, order_by, descending, row_offset
# NOTES:
#   - [2026-11-17] Library access goes through the Knowledge engine (app.clients.library), not the content store (Phase 4).
#   - [2026-11-16] Created (Database Editor). Opens the last database / table again; the DevOS system
#                  database is listed read-only for inspection.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - query: How the current table is being viewed - filter (SQL WHERE text), sort column, direction.
#   The data grid sets it; the record form walks the same rows.
# - row_offset (int | None): Position of the current row within the query (None = no row selected).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. DB_EDITOR_MODULE
#    2.1 init
#    2.2 state
#    2.3 helpers
#    2.4 database_actions
#    2.5 schema_actions
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from app.data.activity_log import get_activity_log
from app.clients.library import library
from app.modules.base import ModuleView
from app.modules.db_editor.blocks import DEFAULT_BLOCKS, block_specs
from app.modules.db_editor.dialogs import ColumnDialog, CreateTableDialog, IndexDialog
from app.modules.db_editor.engine import SYSTEM_DATABASE_ID, DbConnection, DbEditorError, UserDatabases
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.ui_tools import set_style_property, tool_button
from app.ui.widgets.tiling_layout import DROP_ZONE_BOTTOM
from app.ui.engine_bridge import watch_library
# --- END [1.1] ---


# ==============================================================================
# [2.1] init
# ==============================================================================
class DbEditorModule(ModuleView):
    databases_changed = Signal()       # the list of databases changed
    database_changed = Signal()        # another database is open (or none)
    table_changed = Signal()
    query_changed = Signal()           # filter / sort of the current table
    row_selected = Signal()            # row_offset changed
    schema_changed = Signal()          # tables / columns / indexes changed
    data_changed = Signal(str)         # rows of this table changed

    def __init__(self, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, context, "db_editor_panel")
        self.log = get_activity_log()
        self.databases = UserDatabases()
        self.connection: DbConnection | None = None
        self.table: str | None = None
        self.where, self.order_by, self.descending = "", None, False
        self.row_offset: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        row = QHBoxLayout()
        self.db_combo = QComboBox(toolTip="Open a database")
        self.db_combo.setMinimumWidth(180)
        self.db_combo.activated.connect(lambda: self.open_database(self.db_combo.currentData()))
        new_btn = QPushButton("New database", toolTip="Create an empty database in your library")
        new_btn.clicked.connect(self.new_database)
        import_btn = QPushButton("Import…", toolTip="Copy an SQLite file into your library")
        import_btn.clicked.connect(self.import_database)
        self.new_table_btn = QPushButton("+ Table", toolTip="Create a table in the open database")
        self.new_table_btn.clicked.connect(self.new_table)
        more_btn = tool_button("module_settings_btn", text="⋯", tool_tip="More database actions")
        more_btn.clicked.connect(lambda: self._database_menu().exec(more_btn.mapToGlobal(more_btn.rect().bottomLeft())))
        self.db_status_label = QLabel(objectName="db_status_label")
        for widget in (self.db_combo, new_btn, import_btn, self.new_table_btn, more_btn):
            row.addWidget(widget)
        row.addWidget(self.db_status_label, 1)
        reserve_controls_space(row)
        layout.addLayout(row)

        self.block_canvas = self.build_block_canvas(block_specs(self), DEFAULT_BLOCKS)
        layout.addWidget(self.block_canvas, 1)

        watch_library(self, self._on_content_changed)
        self._fill_db_combo()
        saved_id = self.setting("database_id")
        if saved_id is not None and self.databases.entry(saved_id) is not None:
            self.open_database(saved_id, table=self.setting("table"))
        else:
            self._update_controls()
            self.report("Pick a database, or create one with New database")
# --- END [2.1] ---

# ==============================================================================
# [2.2] state
# ==============================================================================
    def open_database(self, item_id: int | None, table: str | None = None) -> None:
        if item_id is None:
            return
        entry = self.databases.entry(item_id)
        if entry is None:
            self.report_error("Open database", DbEditorError(f"database #{item_id} no longer exists"))
            return
        try:
            with self.log.step("db_editor", f"Open database “{entry.name}”"):
                connection = DbConnection(entry)
        except DbEditorError as error:
            self.report_error("Open database", error, already_logged=True)
            return
        if self.connection is not None:
            self.connection.close()
        self.connection = connection
        self.save_setting("database_id", item_id)
        self._fill_db_combo()
        tables = connection.tables()
        self.report(f"Opened “{entry.name}” · {len(tables)} table(s)" + (" · read-only" if entry.is_read_only else ""))
        # Pick the table before announcing the database, so no block asks the new database for the old table.
        self.table = table if table in [item.name for item in connection.objects()] else (tables[0] if tables else None)
        self.where, self.order_by, self.descending, self.row_offset = "", None, False, None
        self.save_setting("table", self.table)
        self._update_controls()
        self.database_changed.emit()
        self.table_changed.emit()

    def select_table(self, name: str | None) -> None:
        if name == self.table and self.connection is not None:
            return
        self.table = name
        self.where, self.order_by, self.descending = "", None, False
        self.row_offset = None
        self.save_setting("table", name)
        self.table_changed.emit()

    def set_query(self, where: str, order_by: str | None, descending: bool) -> None:
        self.where, self.order_by, self.descending = where, order_by, descending
        self.row_offset = None
        self.query_changed.emit()

    def select_row_at(self, offset: int | None) -> None:
        if offset != self.row_offset:
            self.row_offset = offset
            self.row_selected.emit()

    def can_write(self) -> bool:
        return self.connection is not None and not self.connection.is_read_only

    def _update_controls(self) -> None:
        self.new_table_btn.setEnabled(self.can_write())
# --- END [2.2] ---

# ==============================================================================
# [2.3] helpers
# ==============================================================================
    def report(self, message: str, is_error: bool = False) -> None:
        self.db_status_label.setText(message)
        self.db_status_label.setToolTip(message)
        set_style_property(self.db_status_label, "is_error", is_error)

    def report_error(self, action: str, error: Exception, already_logged: bool = False) -> None:
        if not already_logged:
            self.log.error("db_editor", f"{action} failed: {error}")
        self.report(f"{action} failed: {error}", is_error=True)

    def attempt(self, action: str, function, *arguments):
        """Run a database action. Returns its result (True if it returns nothing), or None after a failure,
        which is logged and shown in the status line."""
        try:
            result = function(*arguments)
            return True if result is None else result
        except DbEditorError as error:
            self.report_error(action, error)
        except OSError as error:
            self.report_error(action, DbEditorError(str(error)))
        return None

    def after_write(self, table: str | None = None, schema: bool = False) -> None:
        """Tell the blocks (and the library's size column) that something was written."""
        if self.connection is not None:
            self.databases.touch(self.connection.entry.item_id)
        if schema:
            if self.table is not None and self.connection is not None and self.connection.object_type(self.table) is not None:
                tables = self.connection.tables()
                self.select_table(tables[0] if tables else None)
            self.schema_changed.emit()
        if table:
            self.data_changed.emit(table)

    def confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    def ask_text(self, title: str, label: str, text: str = "") -> str | None:
        value, is_ok = QInputDialog.getText(self, title, label, text=text)
        return value.strip() if is_ok and value.strip() else None

    def show_block(self, kind: str) -> None:
        """Bring a block into view: flash it if it is there, otherwise open it under the data grid."""
        tiles = self.block_canvas.tiles.values()
        existing = next((tile for tile in tiles if tile.kind == kind), None)
        if existing is not None:
            existing.flash_controls()
            return
        beside = next((tile for tile in tiles if tile.kind == "data"), None) or self.block_canvas.active_tile
        self.block_canvas.add_tile(kind, beside=beside, drop_zone=DROP_ZONE_BOTTOM)

    def _fill_db_combo(self) -> None:
        current = self.connection.entry.item_id if self.connection else None
        self.db_combo.blockSignals(True)
        self.db_combo.clear()
        for entry in self.databases.entries():
            self.db_combo.addItem(entry.name, entry.item_id)
        index = self.db_combo.findData(current)
        if index < 0:
            self.db_combo.insertItem(0, "— no database open —", None)
            index = 0
        self.db_combo.setCurrentIndex(index)
        self.db_combo.blockSignals(False)

    def _on_content_changed(self, kind: str) -> None:
        if kind == UserDatabases.KIND:
            self._fill_db_combo()
            self.databases_changed.emit()
# --- END [2.3] ---

# ==============================================================================
# [2.4] database_actions
# ==============================================================================
    def _database_menu(self) -> QMenu:
        menu = QMenu(self)
        has_user_db = self.connection is not None and self.connection.entry.item_id != SYSTEM_DATABASE_ID
        for text, handler, is_enabled in (("Rename database…", self.rename_database, has_user_db),
                                          ("Export a copy…", self.export_database, self.connection is not None),
                                          ("Properties", lambda: self.show_block("properties"), self.connection is not None),
                                          ("Delete database…", self.delete_database, has_user_db)):
            action = menu.addAction(text)
            action.setEnabled(is_enabled)
            action.triggered.connect(handler)
        return menu

    def new_database(self) -> None:
        name = self.ask_text("New database", "Name:", library().unique_name(UserDatabases.KIND, "New database"))
        if name:
            entry = self.attempt("Create database", self.databases.create, name)
            if entry is not None:
                self.open_database(entry.item_id)

    def import_database(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import a database", "", "SQLite databases (*.sqlite *.sqlite3 *.db);;All files (*)")
        if path:
            entry = self.attempt("Import database", self.databases.import_file, path)
            if entry is not None:
                self.open_database(entry.item_id)

    def export_database(self) -> None:
        if self.connection is None:
            return
        entry = self.connection.entry
        destination, _ = QFileDialog.getSaveFileName(self, "Export a copy", f"{entry.name}.sqlite3", "SQLite database (*.sqlite3)")
        if destination and self.attempt("Export database", self.databases.export, entry.item_id, destination) is not None:
            self.report(f"Exported a copy of “{entry.name}”")

    def rename_database(self) -> None:
        if self.connection is None:
            return
        entry = self.connection.entry
        name = self.ask_text("Rename database", "Name:", entry.name)
        if name and (new_name := self.attempt("Rename database", self.databases.rename, entry.item_id, name)):
            entry.name = new_name
            self._fill_db_combo()

    def delete_database(self, item_id: int | None = None) -> None:
        entry = self.databases.entry(item_id if item_id is not None else (self.connection.entry.item_id if self.connection else -1))
        if entry is None or entry.is_read_only:
            return
        if not self.confirm("Delete database", f"Delete “{entry.name}” and everything in it? This can't be undone."):
            return
        if self.connection is not None and self.connection.entry.item_id == entry.item_id:
            self.connection.close()
            self.connection, self.table = None, None
            self.save_setting("database_id", None)
        if self.attempt("Delete database", self.databases.delete, entry.item_id) is not None:
            self.report(f"Deleted “{entry.name}”")
        self._fill_db_combo()
        self._update_controls()
        self.database_changed.emit()
        self.table_changed.emit()
# --- END [2.4] ---

# ==============================================================================
# [2.5] schema_actions
# ==============================================================================
    def new_table(self) -> None:
        if not self.can_write():
            return
        dialog = CreateTableDialog(self, self.connection.tables())
        if dialog.exec() and self.attempt("Create table", self.connection.create_table, dialog.table_name(),
                                          dialog.column_defs()) is not None and self.connection.object_type(dialog.table_name()):
            self.after_write(schema=True)
            self.select_table(dialog.table_name())
            self.report(f"Created table {dialog.table_name()}")

    def rename_table(self, name: str) -> None:
        new = self.ask_text("Rename table", f"New name for {name}:", name)
        if new and new != name and self.attempt("Rename table", self.connection.rename_table, name, new) is not None:
            if self.connection.object_type(new):
                self.after_write(schema=True)
                self.select_table(new)

    def drop_object(self, object_type: str, name: str) -> None:
        if self.confirm(f"Drop {object_type}", f"Drop {object_type} “{name}”?"
                        + (" All of its rows are deleted. This can't be undone." if object_type == "table" else "")):
            if self.attempt(f"Drop {object_type}", self.connection.drop_object, object_type, name) is not None:
                self.after_write(schema=True)
                self.report(f"Dropped {object_type} {name}")

    def add_column(self, table: str) -> None:
        dialog = ColumnDialog(self, f"Add column to {table}")
        if dialog.exec() and self.attempt("Add column", self.connection.add_column, table, dialog.column_def()) is not None:
            self.after_write(table, schema=True)

    def rename_column(self, table: str, column: str) -> None:
        new = self.ask_text("Rename column", f"New name for {table}.{column}:", column)
        if new and new != column and self.attempt("Rename column", self.connection.rename_column, table, column, new) is not None:
            self.after_write(table, schema=True)

    def drop_column(self, table: str, column: str) -> None:
        if self.confirm("Drop column", f"Drop column {table}.{column}? Its values are deleted."):
            if self.attempt("Drop column", self.connection.drop_column, table, column) is not None:
                self.after_write(table, schema=True)

    def add_index(self, table: str) -> None:
        dialog = IndexDialog(self, table, [column.name for column in self.connection.columns(table)])
        if dialog.exec() and dialog.columns():
            if self.attempt("Create index", self.connection.create_index, dialog.index_name(), table,
                            dialog.columns(), dialog.is_unique()) is not None:
                self.after_write(schema=True)
# --- END [2.5] ---
