# ==============================================================================
# FILE: engines/data/views.py
# OVERVIEW: The Data engine's specialist views (they ship with the engine and leave when it is detached):
#           Sheet (cells and formulas of a sheet or CSV), SQL console (a database table's database), Cypher
#           console (a graph table's database) and Structure (how any Data collection is built).
# PURPOSE:
#   1. SheetView: a cell grid with a formula bar; typing goes to "data.set_cell" (formulas included), values
#      come back computed; errors show their reason.
#   2. ConsoleView: statements in, results of the last statement as a grid and one line per statement out;
#      Ctrl+Enter runs; history kept in the view's presentation. SQL and Cypher differ only in the request.
#   3. StructureView: columns (kind, type, key, read-only, formula), indexes, foreign keys, relationships and
#      the CREATE statement; + Column and Remove (properties you added).
# CONTRACT:
#   - Requires: PySide6, app.views (api, types.grid.NewColumnDialog), app.ui (ui_tools, theme, tile_canvas),
#               engines.toolkit.formulas.column_name. Talks only to its host (docs/views-api.md).
#   - Exposes: SheetView, ConsoleView, StructureView, register_views() (types "sheet", "sql_console",
#              "cypher_console", "structure"; engine "data"), HISTORY_SIZE
# NOTES:
#   - [2026-11-17] Created in Phase 5b of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - EXTRA_ROWS / EXTRA_COLS (int): Empty rows / columns shown past a sheet's used area (default: 20 / 5).
# - HISTORY_SIZE (int): Statements a console remembers (default: 20).
# - raw: What was typed into a cell ("12", "=B2*C2"); the grid shows the computed value.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. DATA_VIEWS
#    1.1 sheet
#    1.2 consoles
#    1.3 structure
#    1.4 registration
# ==============================================================================

# ==============================================================================
# [1.1] sheet
# ==============================================================================
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QLabel, QLineEdit, QPlainTextEdit, QSplitter, QTableWidget,
                               QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.theme import MUTED_TEXT_COLOR
from app.ui.ui_tools import add_button, block_layout, fit_columns
from app.views.api import TableData, ViewType, register_view_type
from engines.toolkit.formulas import column_name

EXTRA_ROWS, EXTRA_COLS = 20, 5
HISTORY_SIZE = 20
ERROR_COLOR = "#e06c75"


def _prefix(source: dict) -> str:
    return str(source.get("collection", "")).split(":", 1)[0] if ":" in str(source.get("collection", "")) else ""


class SheetView(QWidget):
    def __init__(self, host) -> None:
        super().__init__()
        self.host = host
        self.raws: dict[tuple[int, int], str] = {}
        self.editable = False
        self._filling = False
        layout, row = block_layout(self)
        self.cell_label = QLabel("A1", objectName="control_label")
        self.cell_label.setMinimumWidth(44)
        row.addWidget(self.cell_label)
        self.formula_edit = QLineEdit(placeholderText="Value or =formula", toolTip="What the cell holds (Enter saves)")
        self.formula_edit.returnPressed.connect(self._formula_entered)
        row.addWidget(self.formula_edit, 1)
        row.addWidget(host.view_menu_button())
        reserve_controls_space(row)
        self.sheet_table = QTableWidget(objectName="records_table")
        self.sheet_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.sheet_table.currentCellChanged.connect(self._cell_changed)
        self.sheet_table.itemChanged.connect(self._item_edited)
        layout.addWidget(self.sheet_table, 1)
        self._fetch_timer = QTimer(self, singleShot=True, interval=100, timeout=self._fetch)

    def set_data(self, _data: TableData) -> None:
        self._fetch_timer.start()                            # rows changed somewhere: read the cells again

    def set_selection(self, record_id) -> None:
        if isinstance(record_id, int) and record_id >= 1:    # a row number (_rowid) -> that sheet row
            self.sheet_table.setCurrentCell(record_id - 1, max(0, self.sheet_table.currentColumn()))

    def _fetch(self) -> None:
        self.host.request("cells", {"collection": self.host.source["collection"]}, self._show_cells)

    def _show_cells(self, reply: dict | None, error) -> None:
        if error is not None:
            self.host.report(str(error), is_error=True)
            return
        self._filling = True
        current = (self.sheet_table.currentRow(), self.sheet_table.currentColumn())
        self.editable = reply["editable"]
        self.sheet_table.clear()
        self.sheet_table.setRowCount(reply["rows"] + EXTRA_ROWS)
        self.sheet_table.setColumnCount(reply["cols"] + EXTRA_COLS)
        self.sheet_table.setHorizontalHeaderLabels([column_name(col) for col in range(reply["cols"] + EXTRA_COLS)])
        self.sheet_table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers if self.editable else
                                         QAbstractItemView.EditTrigger.NoEditTriggers)
        self.raws = {}
        for row, col, raw, shown, problem in reply["cells"]:
            self.raws[(row, col)] = raw
            item = QTableWidgetItem(shown)
            if raw.startswith("="):
                item.setToolTip(problem or raw)
                item.setForeground(QColor(ERROR_COLOR if problem or shown.startswith("#") else MUTED_TEXT_COLOR))
            if row == 0:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.sheet_table.setItem(row, col, item)
        fit_columns(self.sheet_table, 220)
        if current[0] >= 0:
            self.sheet_table.setCurrentCell(*current)
        self._filling = False

    def _cell_changed(self, row: int, col: int, *_previous) -> None:
        if row < 0 or col < 0:
            return
        self.cell_label.setText(f"{column_name(col)}{row + 1}")
        self.formula_edit.setText(self.raws.get((row, col), ""))
        self.formula_edit.setReadOnly(not self.editable)
        if row >= 1 and not self._filling:
            self.host.select(row + 1)                        # the row's id in the table views of this sheet

    def _item_edited(self, item: QTableWidgetItem) -> None:
        if not self._filling:
            self.set_cell(item.row(), item.column(), item.text())

    def _formula_entered(self) -> None:
        row, col = self.sheet_table.currentRow(), self.sheet_table.currentColumn()
        if row >= 0 and col >= 0:
            self.set_cell(row, col, self.formula_edit.text())

    def set_cell(self, row: int, col: int, raw: str) -> None:
        if raw == self.raws.get((row, col), ""):
            return
        self.raws[(row, col)] = raw
        self.host.request("set_cell", {"collection": self.host.source["collection"], "row": row, "col": col, "raw": raw},
                          lambda _reply, error: self.host.report(str(error), is_error=True) if error else self._fetch())
# --- END [1.1] ---


# ==============================================================================
# [1.2] consoles
# ==============================================================================
class ConsoleView(QWidget):
    """A statement console; subclasses name the request, its payload key and a first statement."""
    REQUEST = KEY = ""

    def first_statement(self) -> str:
        return ""

    def __init__(self, host) -> None:
        super().__init__()
        self.host = host
        self.table_name = str(host.source.get("collection", "")).rsplit("/", 1)[-1]
        layout, row = block_layout(self)
        self.history_combo = QComboBox(toolTip="Earlier statements")
        self.history_combo.addItem("History…")
        for text in host.presentation.get("history", []):
            self.history_combo.addItem(text.splitlines()[0][:60], text)
        self.history_combo.activated.connect(self._history_chosen)
        row.addWidget(self.history_combo, 1)
        self.run_btn = add_button(row, "Run", self.run, "Run the statements (Ctrl+Enter)")
        row.addWidget(host.view_menu_button())
        reserve_controls_space(row)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.console_edit = QPlainTextEdit(objectName="note_editor")
        self.console_edit.setPlainText((host.presentation.get("history") or [self.first_statement()])[0])
        QShortcut(QKeySequence("Ctrl+Return"), self.console_edit, activated=self.run)
        splitter.addWidget(self.console_edit)
        results = QWidget()
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        self.outcome_label = QLabel(objectName="control_label", wordWrap=True)
        results_layout.addWidget(self.outcome_label)
        self.results_table = QTableWidget(objectName="records_table")
        self.results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        results_layout.addWidget(self.results_table, 1)
        splitter.addWidget(results)
        splitter.setSizes([120, 240])
        layout.addWidget(splitter, 1)

    def set_data(self, _data: TableData) -> None:
        pass                                                 # a console shows what it ran, not the table

    def set_selection(self, _record_id) -> None:
        pass

    def _history_chosen(self, index: int) -> None:
        text = self.history_combo.itemData(index)
        if text:
            self.console_edit.setPlainText(text)

    def run(self) -> None:
        text = self.console_edit.toPlainText().strip()
        if not text:
            return
        history = [text] + [item for item in self.host.presentation.get("history", []) if item != text]
        self.host.set_presentation("history", history[:HISTORY_SIZE])
        self.run_btn.setEnabled(False)
        self.outcome_label.setText("Running…")
        self.host.request(self.REQUEST, {"collection": self.host.source["collection"], self.KEY: text}, self.show_results)

    def show_results(self, reply: dict | None, error) -> None:
        self.run_btn.setEnabled(True)
        if error is not None:
            self.outcome_label.setText(str(error))
            return
        lines = []
        for result in reply["results"]:
            first = result["statement"].splitlines()[0][:50]
            if result["error"]:
                lines.append(f"✘ {first}: {result['error']}")
            elif result["columns"]:
                lines.append(f"✔ {first}: {len(result['rows']):,} rows{' (more not shown)' if result['truncated'] else ''}")
            else:
                lines.append(f"✔ {first}" + (f": {result['changed']:,} changed" if result["changed"] else ""))
        self.outcome_label.setText("\n".join(lines))
        shown = next((result for result in reversed(reply["results"]) if result["columns"]), None)
        self.results_table.clear()
        self.results_table.setColumnCount(len(shown["columns"]) if shown else 0)
        self.results_table.setRowCount(len(shown["rows"]) if shown else 0)
        if shown:
            self.results_table.setHorizontalHeaderLabels(shown["columns"])
            for row, values in enumerate(shown["rows"]):
                for col, value in enumerate(values):
                    self.results_table.setItem(row, col, QTableWidgetItem("NULL" if value is None else str(value)))
            fit_columns(self.results_table, 260)


class SqlConsoleView(ConsoleView):
    REQUEST, KEY = "sql", "sql"

    def first_statement(self) -> str:
        return f'SELECT * FROM "{self.table_name}" LIMIT 100;'


class CypherConsoleView(ConsoleView):
    REQUEST, KEY = "cypher", "cypher"

    def first_statement(self) -> str:
        return f"MATCH (n:{self.table_name}) RETURN n LIMIT 25;"
# --- END [1.2] ---


# ==============================================================================
# [1.3] structure
# ==============================================================================
class StructureView(QWidget):
    def __init__(self, host) -> None:
        super().__init__()
        self.host = host
        self.structure: dict = {}
        layout, row = block_layout(self)
        self.summary_label = QLabel(objectName="control_label")
        row.addWidget(self.summary_label, 1)
        add_button(row, "+ Column", self.new_column, "Add a column (a formula computes it)")
        self.remove_btn = add_button(row, "Remove", self.remove_selected, "Remove the selected column you added")
        row.addWidget(host.view_menu_button())
        reserve_controls_space(row)
        self.structure_tree = QTreeWidget()
        self.structure_tree.setHeaderLabels(["Name", "Kind", "Type", "Notes"])
        self.structure_tree.currentItemChanged.connect(lambda *_args: self._update_buttons())
        layout.addWidget(self.structure_tree, 1)
        self._fetch_timer = QTimer(self, singleShot=True, interval=100, timeout=self._fetch)

    def set_data(self, _data: TableData) -> None:
        self._fetch_timer.start()

    def set_selection(self, _record_id) -> None:
        pass

    def _fetch(self) -> None:
        self.host.request("structure", {"collection": self.host.source["collection"]}, self._show)

    def _show(self, structure: dict | None, error) -> None:
        if error is not None:
            self.summary_label.setText(str(error))
            return
        self.structure = structure
        tree = self.structure_tree
        tree.clear()
        columns = QTreeWidgetItem(tree, [f"Columns ({len(structure['columns'])})"])
        for column in structure["columns"]:
            notes = [word for word, on in (("key", column["primary_key"]), ("read-only", column["read_only"]),
                                           ("not null", column.get("not_null")), ("yours", column["is_user"])) if on]
            if column.get("default") not in (None, ""):
                notes.append(f"default {column['default']}")
            if column["formula"]:
                notes.append(column["formula"])
            item = QTreeWidgetItem(columns, [column["title"], column["kind"], str(column["type"]), ", ".join(notes)])
            item.setData(0, Qt.ItemDataRole.UserRole, column)
        for title, entries, describe in (
                ("Indexes", structure.get("indexes", []),
                 lambda entry: [entry["name"], "unique" if entry["unique"] else "index", ", ".join(entry["columns"]), ""]),
                ("Foreign keys", structure.get("foreign_keys", []),
                 lambda entry: [entry["column"], "→", f"{entry['table']}.{entry['to']}", ""]),
                ("Relationships", structure.get("relationships", []),
                 lambda entry: [entry["name"], "relationship", "; ".join(" → ".join(pair) for pair in entry["connects"]), ""])):
            if entries:
                group = QTreeWidgetItem(tree, [f"{title} ({len(entries)})"])
                for entry in entries:
                    QTreeWidgetItem(group, describe(entry))
        if structure.get("sql"):
            QTreeWidgetItem(QTreeWidgetItem(tree, ["Definition"]), [structure["sql"]])
        tree.expandAll()
        for column in range(4):
            tree.resizeColumnToContents(column)
        rows = structure.get("rows")
        self.summary_label.setText(f"{structure.get('title', '')} · {structure['kind']}" +
                                   (f" · {rows:,} rows" if isinstance(rows, int) else ""))
        self._update_buttons()

    def _selected_column(self) -> dict | None:
        item = self.structure_tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None

    def _update_buttons(self) -> None:
        column = self._selected_column()
        self.remove_btn.setEnabled(bool(column and column["is_user"]))

    def new_column(self) -> None:
        from app.views.types.grid import NewColumnDialog
        dialog = NewColumnDialog(self)
        if dialog.exec():
            name, kind, formula = dialog.column()
            if name:
                self.host.add_property(name, kind, (), formula)

    def remove_selected(self) -> None:
        column = self._selected_column()
        if column and column["is_user"]:
            self.host.remove_property(column["name"])
# --- END [1.3] ---


# ==============================================================================
# [1.4] registration
# ==============================================================================
def register_views() -> None:
    register_view_type(ViewType("sheet", "Sheet", "The cells of a sheet or CSV, formulas included", ("table",), SheetView,
                                engine="data", requests=("cells", "set_cell"),
                                accepts=lambda source: source.get("engine") == "data" and _prefix(source) in ("sheet", "csv")))
    register_view_type(ViewType("sql_console", "SQL console", "Run SQL on this table's database", ("table",), SqlConsoleView,
                                engine="data", requests=("sql",),
                                accepts=lambda source: source.get("engine") == "data" and _prefix(source) == "db"))
    register_view_type(ViewType("cypher_console", "Cypher console", "Run Cypher on this table's graph database", ("table",),
                                CypherConsoleView, engine="data", requests=("cypher",),
                                accepts=lambda source: source.get("engine") == "data" and _prefix(source) == "graph"))
    register_view_type(ViewType("structure", "Structure", "How this table is built: columns, keys, indexes, relationships",
                                ("table",), StructureView, engine="data", requests=("structure",),
                                accepts=lambda source: source.get("engine") == "data"))
# --- END [1.4] ---
