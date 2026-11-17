# ==============================================================================
# FILE: app/modules/spreadsheet/view.py
# OVERVIEW: Spreadsheet module - workbooks in the user's library (.xlsx), a grid with a formula bar and
#           sheet tabs, selection statistics and a quick chart, LibreOffice for other formats.
# PURPOSE:
#   1. Toolbar: workbook picker, New, Import…, Export ▸ (xlsx / ods / csv / pdf), Open in LibreOffice,
#      status line. Edits save automatically (debounced).
#   2. Blocks: Grid (formula bar, cells, sheet tabs, copy / paste / clear), Selection (count, sum,
#      average, min, max), Chart (selected columns as lines).
#   3. Tools for the tool bag: Sum below selection, Copy selection as CSV.
# CONTRACT:
#   - Requires: app.modules.base.ModuleView, app.modules.spreadsheet (engine, formulas),
#               app.clients.library, app.ui (ui_tools, plot_tools, tool_bag, canvas), QSS selectors
#               #spreadsheet_panel, #formula_edit, #cell_name_label, #sheet_tab_bar
#   - Exposes: SpreadsheetModule(parent=None, context=None) with workbook, sheet, item_id, open_item(),
#              new_workbook(), import_file(), export_as(extension), selection_changed, sheet_changed;
#              SheetModel, GridBlock, SelectionBlock, ChartBlock
# NOTES:
#   - [2026-11-17] Library access goes through the Knowledge engine (app.clients.library), not the content store (Phase 4).
#   - [2026-11-16] Created. Formulas are DevOS's own (formulas.py); .xlsx keeps formulas as text so Excel /
#                  LibreOffice recompute them on open.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - workbook item: A content-store item of kind "sheet" (an .xlsx file in the library).
# - selection: The grid's selected cells, shared with the Selection and Chart blocks.
# - SAVE_DELAY_MS (int | ms): Pause after the last edit before the workbook is saved (default: 800).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SHARED
#    1.1 imports
#    1.2 sheet_model
# 2. BLOCKS
#    2.1 grid_block
#    2.2 selection_block
#    2.3 chart_block
# 3. MODULE
#    3.1 init
#    3.2 files
#    3.3 tools
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import csv
import io
import subprocess
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu,
    QPushButton, QTabBar, QVBoxLayout, QWidget,
)

from app.data.activity_log import get_activity_log
from app.clients.library import library
from app.modules.base import ModuleView
from app.modules.spreadsheet.engine import (
    Sheet, SpreadsheetError, Workbook, format_value, has_openpyxl, libreoffice_path,
)
from app.modules.spreadsheet.formulas import column_name
from app.ui.canvas.block_canvas import BlockSpec
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.plot_tools import styled_plot
from app.ui.theme import ERROR_COLOR, TAG_COLORS
from app.ui.tool_bag import ToolSpec, register_tools
from app.ui.ui_tools import block_layout, control_label, set_status, status_label, table_view, tool_button
from app.ui.engine_bridge import watch_library

SAVE_DELAY_MS = 800
MIN_ROWS, MIN_COLS = 100, 26
DEFAULT_BLOCKS = {"split": "h", "sizes": [4, 1], "children": [
    {"kind": "grid"},
    {"split": "v", "sizes": [1, 2], "children": [{"kind": "selection"}, {"kind": "chart"}]},
]}
# --- END [1.1] ---


# ==============================================================================
# [1.2] sheet_model
# ==============================================================================
class SheetModel(QAbstractTableModel):
    """The grid's view of a Sheet: shows values, edits raw text."""

    def __init__(self, module: "SpreadsheetModule") -> None:
        super().__init__()
        self.module = module

    @property
    def sheet(self) -> Sheet:
        return self.module.sheet

    def rowCount(self, parent=QModelIndex()) -> int:                     # noqa: N802
        return 0 if parent.isValid() else max(MIN_ROWS, self.sheet.used_size()[0] + 20)

    def columnCount(self, parent=QModelIndex()) -> int:                  # noqa: N802
        return 0 if parent.isValid() else max(MIN_COLS, self.sheet.used_size()[1] + 4)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole:
            return column_name(section) if orientation == Qt.Orientation.Horizontal else str(section + 1)
        return None

    def flags(self, index):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        row, col = index.row(), index.column()
        if role == Qt.ItemDataRole.EditRole:
            return self.sheet.raw(row, col)
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ForegroundRole, Qt.ItemDataRole.TextAlignmentRole,
                        Qt.ItemDataRole.ToolTipRole):
            return None
        if (row, col) not in self.sheet.cells:
            return None
        value = self.sheet.value(row, col)
        is_error = isinstance(value, str) and value.startswith("#") and value.endswith(("!", "?"))
        if role == Qt.ItemDataRole.DisplayRole:
            return format_value(value)
        if role == Qt.ItemDataRole.ForegroundRole:
            return QBrush(QColor(ERROR_COLOR)) if is_error else None
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.sheet.error_detail(row, col) if is_error else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if role != Qt.ItemDataRole.EditRole:
            return False
        self.module.set_cells({(index.row(), index.column()): str(value)})
        return True

    def reload(self) -> None:
        self.beginResetModel()
        self.endResetModel()
# --- END [1.2] ---


# ==============================================================================
# [2.1] grid_block
# ==============================================================================
class GridBlock(QWidget):
    def __init__(self, module: "SpreadsheetModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        self.cell_name_label = QLabel("A1", objectName="cell_name_label")
        self.cell_name_label.setFixedWidth(56)
        self.formula_edit = QLineEdit(objectName="formula_edit", placeholderText="Value or =formula  (e.g. =SUM(A1:A9))")
        self.formula_edit.returnPressed.connect(self._commit_formula)
        row.addWidget(self.cell_name_label)
        row.addWidget(QLabel("ƒx", objectName="control_label"))
        row.addWidget(self.formula_edit, 1)
        reserve_controls_space(row)
        self.model = SheetModel(module)
        self.grid_view = table_view("sheet_grid")
        self.grid_view.setModel(self.model)
        self.grid_view.horizontalHeader().setDefaultSectionSize(90)
        self.grid_view.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
                                       | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.grid_view.selectionModel().selectionChanged.connect(self._on_selection)
        self.grid_view.selectionModel().currentChanged.connect(self._show_current)
        self.grid_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid_view.customContextMenuRequested.connect(self._context_menu)
        for keys, action in ((QKeySequence.StandardKey.Paste, self.paste), (QKeySequence.StandardKey.Delete, self.clear_selection),
                             (QKeySequence("Backspace"), self.clear_selection), (QKeySequence.StandardKey.Cut, self.cut)):
            QShortcut(keys, self.grid_view, context=Qt.ShortcutContext.WidgetShortcut).activated.connect(action)
        layout.addWidget(self.grid_view, 1)
        self.sheet_tab_bar = QTabBar(objectName="sheet_tab_bar", expanding=False, movable=False)
        self.sheet_tab_bar.currentChanged.connect(lambda index: module.select_sheet(index) if index >= 0 else None)
        self.sheet_tab_bar.tabBarDoubleClicked.connect(self._rename_sheet)
        tabs_row = QHBoxLayout()
        tabs_row.addWidget(self.sheet_tab_bar)
        add_sheet_btn = tool_button("module_settings_btn", text="+", tool_tip="Add a sheet")
        add_sheet_btn.clicked.connect(module.add_sheet)
        tabs_row.addWidget(add_sheet_btn)
        tabs_row.addStretch()
        layout.addLayout(tabs_row)
        module.sheet_changed.connect(self.refresh)
        module.cells_changed.connect(self._on_cells_changed)
        self.refresh()

    def refresh(self) -> None:
        self.sheet_tab_bar.blockSignals(True)
        while self.sheet_tab_bar.count():
            self.sheet_tab_bar.removeTab(0)
        for sheet in self.module.workbook.sheets:
            self.sheet_tab_bar.addTab(sheet.name)
        self.sheet_tab_bar.setCurrentIndex(self.module.workbook.sheets.index(self.module.sheet))
        self.sheet_tab_bar.blockSignals(False)
        self.model.reload()
        self._show_current(self.grid_view.currentIndex())

    def _on_cells_changed(self) -> None:
        self.model.reload()
        self._show_current(self.grid_view.currentIndex())

    def _show_current(self, index: QModelIndex, _previous=None) -> None:
        row, col = (index.row(), index.column()) if index.isValid() else (0, 0)
        self.cell_name_label.setText(f"{column_name(col)}{row + 1}")
        if not self.formula_edit.hasFocus():
            self.formula_edit.setText(self.module.sheet.raw(row, col))

    def _commit_formula(self) -> None:
        index = self.grid_view.currentIndex()
        row, col = (index.row(), index.column()) if index.isValid() else (0, 0)
        self.module.set_cells({(row, col): self.formula_edit.text()})
        self.grid_view.setCurrentIndex(self.model.index(row + 1, col))
        self.grid_view.setFocus()

    def selected_cells(self) -> list[tuple[int, int]]:
        return sorted((index.row(), index.column()) for index in self.grid_view.selectedIndexes())

    def _on_selection(self) -> None:
        self.module.set_selection(self.selected_cells())

    def paste(self) -> None:
        text = QApplication.clipboard().text()
        index = self.grid_view.currentIndex()
        if not text or not index.isValid():
            return
        rows = list(csv.reader(io.StringIO(text.rstrip("\n")), delimiter="\t" if "\t" in text else ","))
        self.module.set_cells({(index.row() + r, index.column() + c): value
                               for r, values in enumerate(rows) for c, value in enumerate(values)})

    def clear_selection(self) -> None:
        self.module.set_cells({cell: "" for cell in self.selected_cells()})

    def cut(self) -> None:
        from app.ui.ui_tools import copy_selection
        copy_selection(self.grid_view)
        self.clear_selection()

    def _context_menu(self, position) -> None:
        from app.ui.ui_tools import copy_selection
        menu = QMenu(self)
        menu.addAction("Copy  (Ctrl+C)").triggered.connect(lambda: copy_selection(self.grid_view))
        menu.addAction("Paste  (Ctrl+V)").triggered.connect(self.paste)
        menu.addAction("Clear  (Del)").triggered.connect(self.clear_selection)
        menu.addSeparator()
        menu.addAction("Sum below selection").triggered.connect(lambda: self.module.sum_below_selection())
        menu.exec(self.grid_view.viewport().mapToGlobal(position))

    def _rename_sheet(self, index: int) -> None:
        if index < 0:
            return
        sheet = self.module.workbook.sheets[index]
        name, is_ok = QInputDialog.getText(self, "Rename sheet", "Name", text=sheet.name)
        if is_ok and name.strip() and not self.module.workbook.sheet_named(name.strip()):
            sheet.name = name.strip()[:31]
            self.module.schedule_save()
            self.refresh()
# --- END [2.1] ---

# ==============================================================================
# [2.2] selection_block
# ==============================================================================
class SelectionBlock(QWidget):
    def __init__(self, module: "SpreadsheetModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        row.addWidget(control_label("Selection"))
        row.addStretch()
        reserve_controls_space(row)
        self.summary_label = QLabel(objectName="module_summary_label", wordWrap=True)
        self.summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary_label, 1, Qt.AlignmentFlag.AlignTop)
        module.selection_changed.connect(self.refresh)
        module.cells_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        values = [self.module.sheet.value(row, col) for row, col in self.module.selection]
        numbers = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
        filled = [value for value in values if value not in (None, "")]
        if not self.module.selection:
            self.summary_label.setText("Select cells to see their statistics.")
            return
        lines = [f"Cells: {len(values)}  ·  filled: {len(filled)}  ·  numbers: {len(numbers)}"]
        if numbers:
            lines += [f"Sum: {format_value(float(sum(numbers)))}", f"Average: {format_value(sum(numbers) / len(numbers))}",
                      f"Min: {format_value(float(min(numbers)))}   Max: {format_value(float(max(numbers)))}"]
        self.summary_label.setText("\n".join(lines))
# --- END [2.2] ---

# ==============================================================================
# [2.3] chart_block
# ==============================================================================
class ChartBlock(QWidget):
    """Selected columns as lines (the first column is x when the selection has more than one)."""

    def __init__(self, module: "SpreadsheetModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        row.addWidget(control_label("Chart of the selection"))
        row.addStretch()
        reserve_controls_space(row)
        self.plot = styled_plot()
        self.plot.addLegend(offset=(6, 6))
        layout.addWidget(self.plot, 1)
        self.hint_label = QLabel("Select a column of numbers (or several: the first is x).", objectName="control_label")
        layout.addWidget(self.hint_label)
        module.selection_changed.connect(self.refresh)
        module.cells_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self.plot.clear()
        columns: dict[int, list[int]] = {}
        for row, col in self.module.selection:
            columns.setdefault(col, []).append(row)
        series = []
        for col in sorted(columns):
            rows = sorted(columns[col])
            values = [self.module.sheet.value(row, col) for row in rows]
            numeric = [(row, value) for row, value in zip(rows, values) if isinstance(value, (int, float)) and not isinstance(value, bool)]
            label = values[0] if values and isinstance(values[0], str) and not values[0].startswith("#") else column_name(col)
            if numeric:
                series.append((label, dict(numeric)))
        self.hint_label.setVisible(not series)
        if not series:
            return
        if len(series) > 1:
            (_x_label, x_by_row), series = series[0], series[1:]
        else:
            x_by_row = {row: float(index) for index, row in enumerate(sorted(series[0][1]))}
        for number, (label, y_by_row) in enumerate(series):
            rows = [row for row in sorted(y_by_row) if row in x_by_row]
            self.plot.plot([x_by_row[row] for row in rows], [y_by_row[row] for row in rows], name=str(label),
                           pen={"color": TAG_COLORS[number % len(TAG_COLORS)], "width": 2})
# --- END [2.3] ---


# ==============================================================================
# [3.1] init
# ==============================================================================
class SpreadsheetModule(ModuleView):
    sheet_changed = Signal()            # another workbook or sheet is shown
    cells_changed = Signal()            # values changed (edits)
    selection_changed = Signal()

    def __init__(self, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, context, "spreadsheet_panel")
        self.log = get_activity_log()
        self.store = library()
        self.workbook = Workbook()
        self.sheet = self.workbook.sheets[0]
        self.item_id: int | None = None
        self.selection: list[tuple[int, int]] = []
        self._save_timer = QTimer(self, singleShot=True, interval=SAVE_DELAY_MS, timeout=self.save)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        row = QHBoxLayout()
        self.file_combo = QComboBox(toolTip="Open a workbook from your library")
        self.file_combo.setMinimumWidth(170)
        self.file_combo.activated.connect(lambda: self.open_item(self.file_combo.currentData()))
        row.addWidget(self.file_combo)
        for text, handler, tip in (("New", self.new_workbook, "New workbook in your library"),
                                   ("Import…", self.import_file, "Copy an .xlsx / .ods / .xls / .csv into your library")):
            button = QPushButton(text, toolTip=tip)
            button.clicked.connect(handler)
            row.addWidget(button)
        more_btn = tool_button("module_settings_btn", text="⋯", tool_tip="Export, open in LibreOffice")
        more_btn.clicked.connect(lambda: self._file_menu().exec(more_btn.mapToGlobal(more_btn.rect().bottomLeft())))
        row.addWidget(more_btn)
        self.module_status_label = status_label()
        row.addWidget(self.module_status_label, 1)
        reserve_controls_space(row)
        layout.addLayout(row)
        self.block_canvas = self.build_block_canvas(self.block_specs(), DEFAULT_BLOCKS)
        layout.addWidget(self.block_canvas, 1)

        watch_library(self, self._on_content_changed)
        self._fill_file_combo()
        saved_id = self.setting("item_id")
        if saved_id is not None and self.store.get(saved_id) is not None:
            self.open_item(saved_id)
        else:
            set_status(self.module_status_label, "Type into the grid — New saves it as a workbook in your Library")

    def block_specs(self) -> list[BlockSpec]:
        return [
            BlockSpec("grid", "Grid", "Cells with a formula bar and sheet tabs", lambda: GridBlock(self)),
            BlockSpec("selection", "Selection", "Count, sum, average, min and max of the selected cells", lambda: SelectionBlock(self)),
            BlockSpec("chart", "Chart", "The selected columns as a line chart", lambda: ChartBlock(self)),
        ]

    def select_sheet(self, index: int) -> None:
        if 0 <= index < len(self.workbook.sheets) and self.workbook.sheets[index] is not self.sheet:
            self.sheet = self.workbook.sheets[index]
            self.selection = []
            self.sheet_changed.emit()
            self.selection_changed.emit()

    def add_sheet(self) -> None:
        self.sheet = self.workbook.add_sheet()
        self.schedule_save()
        self.sheet_changed.emit()

    def set_cells(self, changes: dict[tuple[int, int], str]) -> None:
        for (row, col), text in changes.items():
            self.sheet.set_raw(row, col, text)
        self.cells_changed.emit()
        self.schedule_save()

    def set_selection(self, cells: list[tuple[int, int]]) -> None:
        self.selection = cells
        self.selection_changed.emit()
# --- END [3.1] ---

# ==============================================================================
# [3.2] files
# ==============================================================================
    def _fill_file_combo(self) -> None:
        self.file_combo.clear()
        self.file_combo.addItem("(unsaved workbook)", None)
        for item in self.store.items("sheet"):
            self.file_combo.addItem(item.name, item.item_id)
        self.file_combo.setCurrentIndex(max(0, self.file_combo.findData(self.item_id)))

    def _on_content_changed(self, kind: str) -> None:
        if kind == "sheet":
            self._fill_file_combo()

    def open_item(self, item_id: int | None) -> None:
        if item_id is None or item_id == self.item_id:
            return
        self.save_now()
        item = self.store.get(item_id)
        if item is None:
            set_status(self.module_status_label, "That workbook no longer exists", is_error=True)
            return
        try:
            with self.log.step("spreadsheet", f"Open workbook “{item.name}”"):
                workbook = Workbook.load(self.store.path(item))
        except SpreadsheetError as error:
            set_status(self.module_status_label, f"Couldn't open “{item.name}”: {error}", is_error=True)
            return
        self.workbook, self.sheet, self.item_id = workbook, workbook.sheets[0], item_id
        self.save_setting("item_id", item_id)
        self.selection = []
        self._fill_file_combo()
        self.sheet_changed.emit()
        self.selection_changed.emit()
        set_status(self.module_status_label, f"{item.name} · {len(workbook.sheets)} sheet(s)")

    def _register(self, name: str, workbook: Workbook) -> int:
        extension = ".xlsx" if has_openpyxl() else ".csv"
        path = self.store.new_path("sheet", extension)
        workbook.save(path)
        return self.store.register_path("sheet", self.store.unique_name("sheet", name), path).item_id

    def new_workbook(self) -> None:
        name, is_ok = QInputDialog.getText(self, "New workbook", "Name", text="Workbook")
        if not is_ok or not name.strip():
            return
        unsaved = self.workbook if self.item_id is None and any(sheet.cells for sheet in self.workbook.sheets) else Workbook()
        with self.log.step("spreadsheet", f"Create workbook “{name.strip()}”"):
            item_id = self._register(name.strip(), unsaved)
        self.item_id = None
        self.open_item(item_id)

    def import_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Import spreadsheet", "",
                                                    "Spreadsheets (*.xlsx *.xlsm *.ods *.xls *.csv *.tsv)")
        if path:
            self.import_path(Path(path))

    def import_path(self, path: Path) -> int | None:
        try:
            with self.log.step("spreadsheet", f"Import {path.name}"):
                item_id = self._register(path.stem, Workbook.load(path))
        except SpreadsheetError as error:
            set_status(self.module_status_label, f"Import failed: {error}", is_error=True)
            return None
        self.open_item(item_id)
        return item_id

    def schedule_save(self) -> None:
        if self.item_id is not None:
            self._save_timer.start()

    def save_now(self) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
            self.save()

    def save(self) -> None:
        item = self.store.get(self.item_id) if self.item_id is not None else None
        if item is None:
            return
        try:
            self.workbook.save(self.store.path(item))
            self.store.refresh_size(item.item_id)
            set_status(self.module_status_label, f"Saved {item.name}")
        except (SpreadsheetError, OSError) as error:
            self.log.error("spreadsheet", f"Save “{item.name}” failed", error)
            set_status(self.module_status_label, f"Save failed: {error}", is_error=True)

    def hideEvent(self, event) -> None:                                  # noqa: N802
        self.save_now()
        super().hideEvent(event)

    def _file_menu(self) -> QMenu:
        menu = QMenu(self)
        has_office = libreoffice_path() is not None
        for title, extension, needs_office in (("Export as Excel (.xlsx)…", ".xlsx", False),
                                               ("Export as OpenDocument (.ods)…", ".ods", True),
                                               ("Export sheet as CSV…", ".csv", False),
                                               ("Export as PDF…", ".pdf", True)):
            action = menu.addAction(title + ("" if has_office or not needs_office else "  (needs LibreOffice)"))
            action.setEnabled(has_office or not needs_office)
            action.triggered.connect(lambda _checked=False, extension=extension: self.export_as(extension))
        menu.addSeparator()
        open_action = menu.addAction("Open in LibreOffice")
        open_action.setEnabled(has_office and self.item_id is not None)
        open_action.triggered.connect(self.open_in_libreoffice)
        return menu

    def export_as(self, extension: str, destination: str | None = None) -> bool:
        if destination is None:
            destination, _filter = QFileDialog.getSaveFileName(self, "Export", f"workbook{extension}", f"*{extension}")
        if not destination:
            return False
        workbook = Workbook([self.sheet]) if extension == ".csv" else self.workbook
        try:
            with self.log.step("spreadsheet", f"Export {Path(destination).name}"):
                workbook.save(destination)
        except SpreadsheetError as error:
            set_status(self.module_status_label, f"Export failed: {error}", is_error=True)
            return False
        set_status(self.module_status_label, f"Exported {Path(destination).name}")
        return True

    def open_in_libreoffice(self) -> None:
        self.save_now()
        item = self.store.get(self.item_id)
        subprocess.Popen([libreoffice_path(), str(self.store.path(item))], start_new_session=True)
        self.log.info("spreadsheet", f"Opened “{item.name}” in LibreOffice (reopen it here after saving there)")
# --- END [3.2] ---

# ==============================================================================
# [3.3] tools
# ==============================================================================
    def sum_below_selection(self) -> None:
        """=SUM(...) under each selected column's cells."""
        columns: dict[int, list[int]] = {}
        for row, col in self.selection:
            columns.setdefault(col, []).append(row)
        if not columns:
            set_status(self.module_status_label, "Select the cells to sum first", is_error=True)
            return
        self.set_cells({(max(rows) + 1, col): f"=SUM({column_name(col)}{min(rows) + 1}:{column_name(col)}{max(rows) + 1})"
                        for col, rows in columns.items()})

    def selection_as_csv(self) -> str:
        if not self.selection:
            return ""
        rows = sorted({row for row, _ in self.selection})
        cols = sorted({col for _, col in self.selection})
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        for row in rows:
            writer.writerow([format_value(self.sheet.value(row, col)) for col in cols])
        return buffer.getvalue()


register_tools([
    ToolSpec("spreadsheet.sum_below", "Sum below selection", "Σ", "Put =SUM(...) under each selected column",
             lambda module: module.sum_below_selection(), "spreadsheet", "total add"),
    ToolSpec("spreadsheet.copy_csv", "Copy selection as CSV", "⎘", "Copy the selected values as comma-separated text",
             lambda module: QApplication.clipboard().setText(module.selection_as_csv()), "spreadsheet", "clipboard"),
    ToolSpec("spreadsheet.add_sheet", "Add sheet", "⊞", "Add a sheet to the workbook", lambda module: module.add_sheet(),
             "spreadsheet", "tab"),
])
# --- END [3.3] ---
