# ==============================================================================
# FILE: app/ui/record_views.py
# OVERVIEW: Views of a record collection - the same records shown as a table, a board, a timeline
#           (Gantt), a calendar or a form. Every view is a block, so a module can show several side by
#           side and the user can swap them; they share one selection and one search.
# PURPOSE:
#   1. RecordsController: the collection, the selected record, the search text; signals so views follow
#      each other; create / change / duplicate / delete with errors reported (never silent); open_form()
#      (double-click a record) and show_record_menu() (right-click a record in any view).
#   2. Blocks: TableBlock (inline edits, sort), BoardBlock (cards grouped by a select property, drag
#      between columns), TimelineBlock (bars from start / end dates; drag to move, drag the end to resize),
#      CalendarBlock (records on their date; drag to another day), FormBlock (every property of the
#      selected record, + Property).
#   3. record_block_specs(controller) for ModuleView.build_block_canvas().
# CONTRACT:
#   - Requires: app.data.store.records, app.ui.canvas.block_canvas.BlockSpec, app.ui.ui_tools, app.ui.theme,
#               QSS selectors #record_title_edit, #board_column_panel, #board_column_label, #record_meta_label,
#               #record_long_text_edit
#   - Exposes: RecordsController(collection, noun, parent) with form_requested(int), record_actions,
#              open_form(id), record_menu(id, parent), show_record_menu(id, parent, pos), record_block_specs(controller),
#              TableBlock, BoardBlock, TimelineBlock, CalendarBlock, FormBlock, RECORD_MIME
# NOTES:
#   - [2026-11-17] Double-clicking a record on the board, timeline (name or bar) or calendar opens its form view;
#                  right-clicking a record in any view offers Open form, module actions, Duplicate, Delete.
#   - [2026-11-16] Created for the Agents and Projects modules ("views": one set of data, many pictures).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - view: One way of drawing the same records (table, board, timeline, calendar, form).
# - group property: The select property a board makes its columns from.
# - RECORD_MIME (str): Drag payload of a record card / bar (its record_id).
# - day_px (int | px): Width of one day on the timeline (zoom slider).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SHARED
#    1.1 imports
#    1.2 records_controller
#    1.3 property_editors
# 2. VIEWS
#    2.1 table_block
#    2.2 board_block
#    2.3 timeline_block
#    2.4 calendar_block
#    2.5 form_block
# 3. SPECS
#    3.1 record_block_specs
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import calendar
import datetime as dt

from PySide6.QtCore import QAbstractTableModel, QByteArray, QMimeData, QModelIndex, QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QCalendarWidget, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget, QWidgetAction,
)

from app.data.activity_log import get_activity_log
from app.data.store.records import (
    PROPERTY_KINDS, TITLE_KEY, PropertySpec, Record, RecordCollection, coerce_value, display_text, parse_date,
)
from app.ui.canvas.block_canvas import BlockSpec
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.theme import (
    ACCENT_COLOR, ACCENT_HIGHLIGHT_COLOR, BORDER_COLOR, EDGE_COLOR, HEADER_COLOR, MUTED_TEXT_COLOR, PANEL_COLOR, TAG_COLORS,
    TEXT_COLOR,
)
from app.ui.ui_tools import add_button, block_layout, control_label, fit_columns, table_view

RECORD_MIME = "application/x-devos-record"
KIND_TITLES = {"text": "Text", "long_text": "Long text", "number": "Number", "select": "Select",
               "multi_select": "Multi-select", "date": "Date", "checkbox": "Checkbox", "url": "Link"}


def tag_color(option: str) -> QColor:
    """Stable colour for a select option (same option, same colour, in every view)."""
    return QColor(TAG_COLORS[sum(map(ord, option or "")) % len(TAG_COLORS)])


def start_record_drag(source: QWidget, record_id: int, label: str) -> None:
    mime = QMimeData()
    mime.setData(RECORD_MIME, QByteArray(str(record_id).encode()))
    mime.setText(label)
    drag = QDrag(source)
    drag.setMimeData(mime)
    drag.exec(Qt.DropAction.MoveAction)


def dragged_record_id(event) -> int | None:
    if not event.mimeData().hasFormat(RECORD_MIME):
        return None
    return int(bytes(event.mimeData().data(RECORD_MIME)).decode())
# --- END [1.1] ---


# ==============================================================================
# [1.2] records_controller
# ==============================================================================
class RecordsController(QObject):
    records_changed = Signal()
    selection_changed = Signal()
    status = Signal(str, bool)                       # message, is_error
    form_requested = Signal(int)                     # record_id: show this record's form view

    def __init__(self, collection: RecordCollection, noun: str = "record", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.collection, self.noun = collection, noun
        self.selected_id: int | None = None
        self.search = ""
        self.log = get_activity_log()
        self.source = f"records:{collection.collection}"
        self.record_actions: list[tuple[str, object]] = []   # (title, handler(record_id)) added to the record menu
        # Views refresh on the next event-loop turn: several changes coalesce, and an editor that caused the
        # change (a table cell commit) has finished before its model resets.
        self._changed_timer = QTimer(self, singleShot=True, interval=0, timeout=self.records_changed.emit)
        collection.add_listener(self._on_collection_changed)
        self.destroyed.connect(lambda *_: collection.remove_listener(self._on_collection_changed))

    def _on_collection_changed(self) -> None:
        if self.selected_id is not None and self.collection.get(self.selected_id) is None:
            self.selected_id = None
            self.selection_changed.emit()
        self._changed_timer.start()

    def records(self, sort_key: str | None = None, descending: bool = False) -> list[Record]:
        return self.collection.records(self.search, sort_key, descending)

    def set_search(self, text: str) -> None:
        if text != self.search:
            self.search = text
            self.records_changed.emit()

    def select(self, record_id: int | None) -> None:
        if record_id != self.selected_id:
            self.selected_id = record_id
            self.selection_changed.emit()

    def selected(self) -> Record | None:
        return self.collection.get(self.selected_id) if self.selected_id is not None else None

    def open_form(self, record_id: int) -> None:
        """Select the record and ask the module to show its form view (double-click, menu)."""
        self.select(record_id)
        self.form_requested.emit(record_id)

    def record_menu(self, record_id: int, parent: QWidget) -> QMenu | None:
        """The right-click menu every view shows for a record: open its form, module actions, duplicate,
        delete. Selects the record."""
        if self.collection.get(record_id) is None:
            return None
        self.select(record_id)
        menu = QMenu(parent)
        menu.addAction("Open form").triggered.connect(lambda: self.open_form(record_id))
        for title, handler in self.record_actions:
            menu.addAction(title).triggered.connect(lambda _checked=False, handler=handler: handler(record_id))
        menu.addSeparator()
        menu.addAction("Duplicate").triggered.connect(lambda: self.duplicate(record_id))
        menu.addAction(f"Delete {self.noun}…").triggered.connect(lambda: self.delete([record_id], parent))
        return menu

    def show_record_menu(self, record_id: int, parent: QWidget, global_pos: QPoint) -> None:
        menu = self.record_menu(record_id, parent)
        if menu is not None:
            menu.exec(global_pos)
            menu.deleteLater()

    def duplicate(self, record_id: int) -> None:
        if self.collection.get(record_id) is not None:
            self.select(self.collection.duplicate(record_id).record_id)

    def new_record(self, values: dict | None = None, title: str | None = None) -> Record:
        record = self.collection.create(title or f"New {self.noun}", values or {})
        self.log.info(self.source, f"Created {self.noun} “{record.title}”")
        self.select(record.record_id)
        return record

    def set_value(self, record_id: int, key: str, value) -> bool:
        """Store an edited value; text that doesn't fit the property is reported, not stored."""
        spec = self.collection.property(key)
        try:
            stored = coerce_value(spec, value) if spec is not None else value
            if key == TITLE_KEY and not stored:
                raise ValueError("the name can't be empty")
            self.collection.set_value(record_id, key, stored)
            return True
        except (ValueError, KeyError) as error:
            self.report(f"{spec.title if spec else key}: {error}", is_error=True)
            return False

    def delete(self, record_ids: list[int], parent: QWidget) -> None:
        if not record_ids:
            return
        names = [record.title for record in (self.collection.get(item) for item in record_ids) if record]
        what = f"“{names[0]}”" if len(names) == 1 else f"{len(names)} {self.noun}s"
        if QMessageBox.question(parent, f"Delete {self.noun}", f"Delete {what}? This can't be undone.") != QMessageBox.StandardButton.Yes:
            return
        self.collection.delete(record_ids)
        self.log.info(self.source, f"Deleted {what}")
        self.report(f"Deleted {what}")

    def report(self, message: str, is_error: bool = False) -> None:
        if is_error:
            self.log.warning(self.source, message)
        self.status.emit(message, is_error)

    def properties_of(self, *kinds: str) -> list[PropertySpec]:
        return [spec for spec in self.collection.properties if spec.kind in kinds]

    def options_of(self, spec: PropertySpec) -> list[str]:
        """Declared options plus any value already used in a record (so typed values become choices)."""
        options = list(spec.options)
        for record in self.collection.records():
            value = record.values.get(spec.key)
            for item in (value if isinstance(value, list) else [value]):
                if item and item not in options:
                    options.append(item)
        return options
# --- END [1.2] ---

# ==============================================================================
# [1.3] property_editors
# ==============================================================================
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

    def __init__(self, controller: RecordsController, spec: PropertySpec) -> None:
        super().__init__(popupMode=QToolButton.ToolButtonPopupMode.InstantPopup)
        self.controller, self.spec, self.values = controller, spec, []
        self.setMenu(QMenu(self))
        self.menu().aboutToShow.connect(self._fill_menu)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

    def set_value(self, value) -> None:
        self.values = list(value or [])
        self.setText(", ".join(self.values) or "—")

    def _fill_menu(self) -> None:
        menu = self.menu()
        menu.clear()
        for option in self.controller.options_of(self.spec):
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
        from PySide6.QtWidgets import QInputDialog
        text, is_ok = QInputDialog.getText(self, self.spec.title, "New option")
        if is_ok and text.strip():
            if self.spec.is_user:
                self.controller.collection.add_option(self.spec.key, text.strip())
            self._toggle(text.strip(), True)


def make_editor(controller: RecordsController, spec: PropertySpec, commit) -> tuple[QWidget, callable]:
    """Editor widget for a property and a setter(value). commit(value) is called when the user is done."""
    if spec.kind == "long_text":
        editor = QPlainTextEdit(objectName="record_long_text_edit")
        editor.setMinimumHeight(140)
        editor.pending_timer = QTimer(editor, singleShot=True, interval=700, timeout=lambda: commit(editor.toPlainText()))
        editor.textChanged.connect(lambda: editor.pending_timer.start() if editor.hasFocus() else None)
        return editor, lambda value: editor.setPlainText(value or "")
    if spec.kind == "checkbox":
        editor = QCheckBox()
        editor.clicked.connect(lambda is_on: commit(is_on))
        return editor, lambda value: editor.setChecked(bool(value))
    if spec.kind == "select":
        editor = QComboBox(editable=True)
        editor.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

        def set_select(value) -> None:
            editor.blockSignals(True)
            editor.clear()
            editor.addItems([""] + controller.options_of(spec))
            editor.setCurrentText(value or "")
            editor.blockSignals(False)
        editor.activated.connect(lambda _index: commit(editor.currentText()))
        editor.lineEdit().editingFinished.connect(lambda: commit(editor.currentText()))
        return editor, set_select
    if spec.kind == "multi_select":
        editor = MultiSelectField(controller, spec)
        editor.committed.connect(commit)
        return editor, editor.set_value
    if spec.kind == "date":
        editor = DateField()
        editor.committed.connect(commit)
        return editor, editor.set_value
    editor = QLineEdit(placeholderText={"number": "number", "url": "https://…"}.get(spec.kind, ""))
    editor.editingFinished.connect(lambda: commit(editor.text()) if editor.isModified() else None)

    def set_text(value) -> None:
        editor.setText("" if value is None else str(value))
        editor.setModified(False)
    return editor, set_text
# --- END [1.3] ---


# ==============================================================================
# [2.1] table_block
# ==============================================================================
class RecordsTableModel(QAbstractTableModel):
    def __init__(self, controller: RecordsController) -> None:
        super().__init__()
        self.controller = controller
        self.columns: list[PropertySpec] = []
        self.rows: list[Record] = []
        self.sort_key, self.descending = None, False

    def reload(self) -> None:
        self.beginResetModel()
        self.columns = [self.controller.collection.property(TITLE_KEY)] + self.controller.collection.properties
        self.rows = self.controller.records(self.sort_key, self.descending)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:                     # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:                  # noqa: N802
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            spec = self.columns[section]
            arrow = (" ▼" if self.descending else " ▲") if spec.key == self.sort_key else ""
            return spec.title + arrow
        return None

    def flags(self, index):
        spec = self.columns[index.column()]
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if spec.kind == "checkbox":
            return flags | Qt.ItemFlag.ItemIsUserCheckable
        return flags if spec.kind == "long_text" else flags | Qt.ItemFlag.ItemIsEditable

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        spec, record = self.columns[index.column()], self.rows[index.row()]
        value = record.value(spec.key)
        if role == Qt.ItemDataRole.DisplayRole:
            return display_text(spec, value) if spec.kind != "checkbox" else None
        if role == Qt.ItemDataRole.EditRole:
            return ", ".join(value) if isinstance(value, list) else ("" if value is None else str(value))
        if role == Qt.ItemDataRole.CheckStateRole and spec.kind == "checkbox":
            return Qt.CheckState.Checked if value else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.ForegroundRole and spec.kind == "select" and value:
            return tag_color(value).lighter(150)
        if role == Qt.ItemDataRole.FontRole and spec.key == TITLE_KEY:
            font = QFont()
            font.setBold(True)
            return font
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        spec, record = self.columns[index.column()], self.rows[index.row()]
        if role == Qt.ItemDataRole.CheckStateRole:
            value = Qt.CheckState(value) == Qt.CheckState.Checked
        elif role != Qt.ItemDataRole.EditRole:
            return False
        return self.controller.set_value(record.record_id, spec.key, value)


class SelectDelegate(QStyledItemDelegate):
    """Inline editor for select columns: a combo of the known options (typing a new one works too)."""

    def __init__(self, model: RecordsTableModel) -> None:
        super().__init__()
        self.model = model

    def createEditor(self, parent, option, index):                       # noqa: N802
        spec = self.model.columns[index.column()]
        if spec.kind != "select":
            return super().createEditor(parent, option, index)
        combo = QComboBox(parent, editable=True)
        combo.addItems([""] + self.model.controller.options_of(spec))
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


class TableBlock(QWidget):
    def __init__(self, controller: RecordsController) -> None:
        super().__init__()
        self.controller = controller
        layout, row = block_layout(self)
        self.search_edit = QLineEdit(placeholderText=f"Search {controller.noun}s…", clearButtonEnabled=True)
        self.search_edit.setText(controller.search)
        self.search_edit.textChanged.connect(controller.set_search)
        row.addWidget(self.search_edit, 1)
        add_button(row, "+ New", lambda: controller.new_record(), f"Add a {controller.noun}")
        add_button(row, "Delete", self._delete_selected, f"Delete the selected {controller.noun}s")
        reserve_controls_space(row)
        self.model = RecordsTableModel(controller)
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
        controller.records_changed.connect(self.refresh)
        controller.selection_changed.connect(self._show_selection)
        self.refresh()

    def refresh(self) -> None:
        if self.search_edit.text() != self.controller.search:
            self.search_edit.setText(self.controller.search)
        self.model.reload()
        fit_columns(self.records_table, 240)
        self._show_selection()

    def _on_row_changed(self, current: QModelIndex, _previous) -> None:
        if current.isValid() and current.row() < len(self.model.rows):
            self.controller.select(self.model.rows[current.row()].record_id)

    def _show_selection(self) -> None:
        for row_index, record in enumerate(self.model.rows):
            if record.record_id == self.controller.selected_id:
                if self.records_table.currentIndex().row() != row_index:
                    self.records_table.selectRow(row_index)
                return

    def _show_record_menu(self, pos: QPoint) -> None:
        index = self.records_table.indexAt(pos)
        if index.isValid() and index.row() < len(self.model.rows):
            self.controller.show_record_menu(self.model.rows[index.row()].record_id, self,
                                             self.records_table.viewport().mapToGlobal(pos))

    def _sort_by(self, column: int) -> None:
        key = self.model.columns[column].key
        if self.model.sort_key != key:
            self.model.sort_key, self.model.descending = key, False
        elif not self.model.descending:
            self.model.descending = True
        else:
            self.model.sort_key = None
        self.refresh()

    def _delete_selected(self) -> None:
        rows = sorted({index.row() for index in self.records_table.selectionModel().selectedRows()})
        ids = [self.model.rows[row].record_id for row in rows] or ([self.controller.selected_id] if self.controller.selected_id else [])
        self.controller.delete(ids, self)

    def block_state(self) -> dict:
        return {"sort_key": self.model.sort_key, "descending": self.model.descending}

    def restore_block_state(self, state: dict) -> None:
        self.model.sort_key, self.model.descending = state.get("sort_key"), bool(state.get("descending"))
        self.refresh()
# --- END [2.1] ---

# ==============================================================================
# [2.2] board_block
# ==============================================================================
class BoardColumn(QListWidget):
    """One board column: cards of the records with this group value; cards drag between columns."""

    def __init__(self, board: "BoardBlock", value: str) -> None:
        super().__init__()
        self.board, self.value = board, value
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setSpacing(2)
        self.setWordWrap(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.itemClicked.connect(lambda item: board.controller.select(item.data(Qt.ItemDataRole.UserRole)))
        self.itemDoubleClicked.connect(lambda item: board.controller.open_form(item.data(Qt.ItemDataRole.UserRole)))

    def contextMenuEvent(self, event) -> None:                            # noqa: N802
        item = self.itemAt(event.pos())
        if item is not None:
            self.board.controller.show_record_menu(item.data(Qt.ItemDataRole.UserRole), self, event.globalPos())

    def startDrag(self, _actions) -> None:                               # noqa: N802
        item = self.currentItem()
        if item is not None:
            start_record_drag(self, item.data(Qt.ItemDataRole.UserRole), item.text())

    def dragEnterEvent(self, event) -> None:                             # noqa: N802
        event.acceptProposedAction() if dragged_record_id(event) is not None else event.ignore()

    def dragMoveEvent(self, event) -> None:                              # noqa: N802
        event.acceptProposedAction() if dragged_record_id(event) is not None else event.ignore()

    def dropEvent(self, event) -> None:                                  # noqa: N802
        record_id = dragged_record_id(event)
        if record_id is None:
            return
        event.acceptProposedAction()
        before = self.itemAt(event.position().toPoint())
        before_id = before.data(Qt.ItemDataRole.UserRole) if before is not None else None
        QTimer.singleShot(0, lambda: self.board.move_card(record_id, self.value, before_id))   # after the drag loop


class BoardBlock(QWidget):
    def __init__(self, controller: RecordsController) -> None:
        super().__init__()
        self.controller = controller
        self.group_key: str | None = None
        layout, row = block_layout(self)
        row.addWidget(control_label("Group by"))
        self.group_combo = QComboBox()
        self.group_combo.currentIndexChanged.connect(lambda: self._set_group(self.group_combo.currentData()))
        row.addWidget(self.group_combo)
        row.addStretch()
        reserve_controls_space(row)
        self.columns_area = QScrollArea(widgetResizable=True)
        self.columns_area.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.columns_area, 1)
        controller.records_changed.connect(self.refresh)
        controller.selection_changed.connect(self._show_selection)
        self.refresh()

    def _set_group(self, key: str | None) -> None:
        if key and key != self.group_key:
            self.group_key = key
            self.refresh()

    def refresh(self) -> None:
        selects = self.controller.properties_of("select")
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        for spec in selects:
            self.group_combo.addItem(spec.title, spec.key)
        if self.group_key not in [spec.key for spec in selects]:
            self.group_key = selects[0].key if selects else None
        self.group_combo.setCurrentIndex(max(0, self.group_combo.findData(self.group_key)))
        self.group_combo.blockSignals(False)

        panel = QWidget()
        columns_layout = QHBoxLayout(panel)
        columns_layout.setContentsMargins(0, 0, 0, 0)
        columns_layout.setSpacing(6)
        self.column_lists: list[BoardColumn] = []
        if self.group_key is None:
            columns_layout.addWidget(QLabel("Add a Select property to group cards by it."))
        else:
            spec = self.controller.collection.property(self.group_key)
            records = self.controller.records()
            for value in self.controller.options_of(spec) + [""]:
                members = [record for record in records if (record.values.get(spec.key) or "") == value]
                if value == "" and not members and spec.options:
                    continue                                   # "No value" column only when it holds cards
                columns_layout.addWidget(self._column(spec, value, members))
        columns_layout.addStretch()
        self.columns_area.setWidget(panel)
        self._show_selection()

    def _column(self, spec: PropertySpec, value: str, members: list[Record]) -> QWidget:
        column_panel = QFrame(objectName="board_column_panel")
        column_panel.setFixedWidth(220)
        column_layout = QVBoxLayout(column_panel)
        column_layout.setContentsMargins(4, 4, 4, 4)
        column_layout.setSpacing(4)
        header = QLabel(f"{value or 'No ' + spec.title}  ·  {len(members)}", objectName="board_column_label")
        if value:
            header.setStyleSheet(f"border-left: 3px solid {tag_color(value).name()};")
        column_layout.addWidget(header)
        column_list = BoardColumn(self, value)
        for record in members:
            item = QListWidgetItem(self._card_text(record, spec.key))
            item.setData(Qt.ItemDataRole.UserRole, record.record_id)
            column_list.addItem(item)
        self.column_lists.append(column_list)
        column_layout.addWidget(column_list, 1)
        new_btn = QPushButton("+ New", flat=True)
        new_btn.clicked.connect(lambda: self.controller.new_record({spec.key: value} if value else {}))
        column_layout.addWidget(new_btn)
        return column_panel

    def _card_text(self, record: Record, group_key: str) -> str:
        details = []
        for spec in self.controller.collection.properties:
            if spec.key != group_key and spec.kind not in ("long_text", "checkbox") and record.values.get(spec.key):
                details.append(display_text(spec, record.values[spec.key]))
            if len(details) == 2:
                break
        return record.title + ("\n" + " · ".join(details) if details else "")

    def move_card(self, record_id: int, value: str, before_id: int | None) -> None:
        self.controller.set_value(record_id, self.group_key, value)
        if before_id != record_id:
            self.controller.collection.move(record_id, before_id)
        self.controller.select(record_id)

    def _show_selection(self) -> None:
        for column_list in self.column_lists:
            for index in range(column_list.count()):
                item = column_list.item(index)
                item.setSelected(item.data(Qt.ItemDataRole.UserRole) == self.controller.selected_id)

    def block_state(self) -> dict:
        return {"group_key": self.group_key}

    def restore_block_state(self, state: dict) -> None:
        self.group_key = state.get("group_key")
        self.refresh()
# --- END [2.2] ---

# ==============================================================================
# [2.3] timeline_block
# ==============================================================================
class TimelineCanvas(QWidget):
    """Gantt chart: a row per record, a bar from start to end date. Drag a bar to move it, its right end
    to change the end date. Rows without a start date are listed as unscheduled."""

    NAME_PX, HEADER_PX, ROW_PX, HANDLE_PX = 170, 34, 24, 6

    def __init__(self, block: "TimelineBlock") -> None:
        super().__init__()
        self.block = block
        self.day_px = 14
        self.rows: list[tuple[Record, dt.date | None, dt.date | None]] = []
        self.first_day = dt.date.today()
        self._drag: dict | None = None
        self.setMouseTracking(True)

    def load(self, records: list[Record], start_key: str | None, end_key: str | None) -> None:
        self.rows = []
        for record in records:
            start = parse_date(record.values.get(start_key)) if start_key else None
            end = parse_date(record.values.get(end_key)) if end_key else None
            end = max(end, start) if start and end else start
            self.rows.append((record, start, end))
        self.rows.sort(key=lambda row: (row[1] is None, row[1] or dt.date.max))
        dates = [day for _record, start, end in self.rows for day in (start, end) if day] + [dt.date.today()]
        self.first_day = min(dates) - dt.timedelta(days=7)
        last_day = max(max(dates) + dt.timedelta(days=21), self.first_day + dt.timedelta(days=60))
        self.day_count = (last_day - self.first_day).days
        self.setMinimumSize(self.NAME_PX + self.day_count * self.day_px, self.HEADER_PX + max(1, len(self.rows)) * self.ROW_PX + 8)
        self.update()

    def x_of(self, day: dt.date) -> int:
        return self.NAME_PX + (day - self.first_day).days * self.day_px

    def day_at(self, x: float) -> dt.date:
        return self.first_day + dt.timedelta(days=int((x - self.NAME_PX) // self.day_px))

    def bar_rect(self, row_index: int) -> QRect | None:
        _record, start, end = self.rows[row_index]
        if start is None:
            return None
        top = self.HEADER_PX + row_index * self.ROW_PX + 4
        return QRect(self.x_of(start), top, (end - start).days * self.day_px + self.day_px, self.ROW_PX - 8)

    def visible_left(self) -> int:
        """Canvas x at the scroll area's left edge (the name column sticks there)."""
        return max(0, -self.x())

    def paintEvent(self, _event) -> None:                                # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(PANEL_COLOR))
        muted, grid = QColor(MUTED_TEXT_COLOR), QColor(EDGE_COLOR)
        painter.fillRect(0, 0, self.width(), self.HEADER_PX, QColor(HEADER_COLOR))
        for offset in range(self.day_count + 1):
            day = self.first_day + dt.timedelta(days=offset)
            x = self.x_of(day)
            if day.day == 1 or (offset == 0 and day.day < 22):
                painter.setPen(QPen(QColor(TEXT_COLOR)))
                painter.drawText(x + 3, 14, day.strftime("%b %Y"))
                painter.setPen(QPen(grid))
                painter.drawLine(x, 0, x, self.height())
            elif day.weekday() == 0:
                painter.setPen(QPen(muted))
                if self.day_px >= 8:
                    painter.drawText(x + 2, 29, str(day.day))
                painter.setPen(QPen(QColor(BORDER_COLOR)))
                painter.drawLine(x, self.HEADER_PX, x, self.height())
        today_x = self.x_of(dt.date.today())
        painter.setPen(QPen(QColor(ACCENT_HIGHLIGHT_COLOR), 1, Qt.PenStyle.DashLine))
        painter.drawLine(today_x, self.HEADER_PX - 6, today_x, self.height())
        left = self.visible_left()
        for row_index, (record, _start, _end) in enumerate(self.rows):
            top = self.HEADER_PX + row_index * self.ROW_PX
            if record.record_id == self.block.controller.selected_id:
                painter.fillRect(0, top, self.width(), self.ROW_PX, QColor(ACCENT_COLOR).darker(160))
            rect = self.bar_rect(row_index)
            if rect is not None:
                color = self.block.bar_color(record)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color.lighter(130) if record.record_id == self.block.controller.selected_id else color)
                painter.drawRoundedRect(rect, 3, 3)
        # Name column on top, stuck to the visible left edge while scrolling sideways
        painter.fillRect(left, 0, self.NAME_PX - 4, self.height(), QColor(PANEL_COLOR))
        painter.fillRect(left, 0, self.NAME_PX - 4, self.HEADER_PX, QColor(HEADER_COLOR))
        for row_index, (record, start, _end) in enumerate(self.rows):
            top = self.HEADER_PX + row_index * self.ROW_PX
            if record.record_id == self.block.controller.selected_id:
                painter.fillRect(left, top, self.NAME_PX - 4, self.ROW_PX, QColor(ACCENT_COLOR))
            painter.setPen(QPen(QColor(TEXT_COLOR) if start else muted))
            name = painter.fontMetrics().elidedText(record.title + ("" if start else "  (unscheduled)"),
                                                    Qt.TextElideMode.ElideRight, self.NAME_PX - 12)
            painter.drawText(QRect(left + 6, top, self.NAME_PX - 12, self.ROW_PX), Qt.AlignmentFlag.AlignVCenter, name)
        painter.setPen(QPen(grid))
        painter.drawLine(left + self.NAME_PX - 4, 0, left + self.NAME_PX - 4, self.height())

    def _row_at(self, y: float) -> int | None:
        row_index = int((y - self.HEADER_PX) // self.ROW_PX)
        return row_index if y >= self.HEADER_PX and 0 <= row_index < len(self.rows) else None

    def mousePressEvent(self, event) -> None:                            # noqa: N802
        row_index = self._row_at(event.position().y())
        if row_index is None:
            return
        record = self.rows[row_index][0]
        self.block.controller.select(record.record_id)
        rect = self.bar_rect(row_index)
        x = event.position().x()
        if x < self.visible_left() + self.NAME_PX:
            return                                       # the name column covers the bars there
        if rect is not None and rect.left() <= x <= rect.right() + 2:
            mode = "resize" if x >= rect.right() - self.HANDLE_PX else "move"
            self._drag = {"row": row_index, "mode": mode, "x": x, "days": 0}

    def mouseMoveEvent(self, event) -> None:                             # noqa: N802
        x = event.position().x()
        if self._drag is None:
            row_index = self._row_at(event.position().y())
            rect = self.bar_rect(row_index) if row_index is not None else None
            on_handle = rect is not None and rect.right() - self.HANDLE_PX <= x <= rect.right() + 2
            on_bar = rect is not None and rect.left() <= x <= rect.right()
            self.setCursor(Qt.CursorShape.SizeHorCursor if on_handle else
                           Qt.CursorShape.OpenHandCursor if on_bar else Qt.CursorShape.ArrowCursor)
            return
        days = round((x - self._drag["x"]) / self.day_px)
        if days != self._drag["days"]:
            record, start, end = self.rows[self._drag["row"]]
            step = dt.timedelta(days=days - self._drag["days"])
            if self._drag["mode"] == "move":
                start, end = start + step, end + step
            else:
                end = max(start, end + step)
            self.rows[self._drag["row"]] = (record, start, end)
            self._drag["days"] = days
            self.update()

    def mouseReleaseEvent(self, _event) -> None:                         # noqa: N802
        drag, self._drag = self._drag, None
        if drag and drag["days"]:
            record, start, end = self.rows[drag["row"]]
            self.block.commit_dates(record.record_id, start, end)

    def mouseDoubleClickEvent(self, event) -> None:                      # noqa: N802
        """An unscheduled row's empty chart: schedule it there. Its name or its bar: open its form."""
        row_index = self._row_at(event.position().y())
        if row_index is None:
            return
        record, start, _end = self.rows[row_index]
        x = event.position().x()
        if start is None and x > self.visible_left() + self.NAME_PX:
            day = self.day_at(x)
            self.block.commit_dates(record.record_id, day, day + dt.timedelta(days=6))
        else:
            self._drag = None
            self.block.controller.open_form(record.record_id)

    def contextMenuEvent(self, event) -> None:                            # noqa: N802
        row_index = self._row_at(event.pos().y())
        if row_index is not None:
            self._drag = None
            self.block.controller.show_record_menu(self.rows[row_index][0].record_id, self, event.globalPos())


class TimelineBlock(QWidget):
    def __init__(self, controller: RecordsController) -> None:
        super().__init__()
        self.controller = controller
        self.start_key = self.end_key = self.color_key = None
        layout, row = block_layout(self)
        self.start_combo, self.end_combo = QComboBox(toolTip="Start date"), QComboBox(toolTip="End date")
        self.color_combo = QComboBox(toolTip="Colour bars by")
        for caption, combo in (("From", self.start_combo), ("to", self.end_combo), ("", self.color_combo)):
            if caption:
                row.addWidget(control_label(caption))
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            row.addWidget(combo)
            combo.activated.connect(self._read_combos)
        for text, step in (("−", -1), ("+", 1)):
            zoom_btn = QToolButton(text=text, toolTip="Zoom out" if step < 0 else "Zoom in", autoRaise=True)
            zoom_btn.clicked.connect(lambda _checked=False, step=step: self._zoom(step))
            row.addWidget(zoom_btn)
        add_button(row, "Today", self._scroll_to_today)
        row.addStretch()
        reserve_controls_space(row)
        self.canvas = TimelineCanvas(self)
        self.scroll_area = QScrollArea(widgetResizable=True)
        self.scroll_area.setWidget(self.canvas)
        self.scroll_area.horizontalScrollBar().valueChanged.connect(self.canvas.update)   # repaint the sticky names
        layout.addWidget(self.scroll_area, 1)
        hint = control_label("Drag a bar to move it, its right end to resize · double-click an unscheduled row to schedule it")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        controller.records_changed.connect(self.refresh)
        controller.selection_changed.connect(self.canvas.update)
        self.refresh()
        QTimer.singleShot(0, self._scroll_to_today)

    def refresh(self) -> None:
        dates = self.controller.properties_of("date")
        selects = self.controller.properties_of("select")
        if self.start_key not in [spec.key for spec in dates]:
            self.start_key = dates[0].key if dates else None
        if self.end_key not in [spec.key for spec in dates]:
            self.end_key = dates[1].key if len(dates) > 1 else self.start_key
        if self.color_key not in [spec.key for spec in selects]:
            self.color_key = selects[0].key if selects else None
        for combo, specs, key in ((self.start_combo, dates, self.start_key), (self.end_combo, dates, self.end_key),
                                  (self.color_combo, selects, self.color_key)):
            combo.blockSignals(True)
            combo.clear()
            for spec in specs:
                combo.addItem(spec.title, spec.key)
            combo.setCurrentIndex(max(0, combo.findData(key)))
            combo.blockSignals(False)
        self.canvas.load(self.controller.records(), self.start_key, self.end_key)

    def _read_combos(self) -> None:
        self.start_key, self.end_key = self.start_combo.currentData(), self.end_combo.currentData()
        self.color_key = self.color_combo.currentData()
        self.refresh()

    def _zoom(self, step: int) -> None:
        self.canvas.day_px = max(4, min(48, round(self.canvas.day_px * (1.4 if step > 0 else 1 / 1.4))))
        self.refresh()

    def _scroll_to_today(self) -> None:
        self.scroll_area.horizontalScrollBar().setValue(max(0, self.canvas.x_of(dt.date.today()) - TimelineCanvas.NAME_PX - 60))

    def bar_color(self, record: Record) -> QColor:
        value = record.values.get(self.color_key) if self.color_key else None
        return tag_color(value) if value else QColor(ACCENT_HIGHLIGHT_COLOR)

    def commit_dates(self, record_id: int, start: dt.date, end: dt.date) -> None:
        if self.start_key is None:
            return
        self.controller.set_value(record_id, self.start_key, start.isoformat())
        if self.end_key and self.end_key != self.start_key:
            self.controller.set_value(record_id, self.end_key, end.isoformat())

    def block_state(self) -> dict:
        return {"start_key": self.start_key, "end_key": self.end_key, "color_key": self.color_key,
                "day_px": self.canvas.day_px}

    def restore_block_state(self, state: dict) -> None:
        self.start_key, self.end_key, self.color_key = state.get("start_key"), state.get("end_key"), state.get("color_key")
        self.canvas.day_px = max(4, min(48, int(state.get("day_px", 14))))
        self.refresh()
# --- END [2.3] ---

# ==============================================================================
# [2.4] calendar_block
# ==============================================================================
class CalendarGrid(QWidget):
    """Month grid; each day lists the records dated that day. Click selects, drag moves to another day,
    double-click an empty spot creates a record on that day."""

    HEADER_PX, CHIP_PX = 20, 16

    def __init__(self, block: "CalendarBlock") -> None:
        super().__init__()
        self.block = block
        self.month = dt.date.today().replace(day=1)
        self.by_day: dict[dt.date, list[Record]] = {}
        self.chips: list[tuple[QRect, Record]] = []
        self._press: tuple[QPoint, Record] | None = None
        self.setAcceptDrops(True)
        self.setMinimumSize(280, 200)

    def days(self) -> list[dt.date]:
        first = self.month - dt.timedelta(days=self.month.weekday())
        return [first + dt.timedelta(days=offset) for offset in range(42)]

    def cell_rect(self, index: int) -> QRect:
        width, height = self.width() / 7, (self.height() - self.HEADER_PX) / 6
        return QRect(int(index % 7 * width), int(self.HEADER_PX + index // 7 * height), int(width), int(height))

    def day_at(self, point: QPoint) -> dt.date | None:
        for index, day in enumerate(self.days()):
            if self.cell_rect(index).contains(point):
                return day
        return None

    def paintEvent(self, _event) -> None:                                # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(PANEL_COLOR))
        painter.setPen(QPen(QColor(MUTED_TEXT_COLOR)))
        for column, name in enumerate(calendar.day_abbr):
            painter.drawText(QRect(int(column * self.width() / 7), 0, int(self.width() / 7), self.HEADER_PX),
                             Qt.AlignmentFlag.AlignCenter, name)
        self.chips = []
        today = dt.date.today()
        for index, day in enumerate(self.days()):
            rect = self.cell_rect(index)
            painter.setPen(QPen(QColor(BORDER_COLOR)))
            painter.setBrush(QColor(HEADER_COLOR) if day.month == self.month.month else QColor(PANEL_COLOR))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
            painter.setPen(QPen(QColor(ACCENT_HIGHLIGHT_COLOR) if day == today else
                                QColor(TEXT_COLOR) if day.month == self.month.month else QColor(MUTED_TEXT_COLOR)))
            painter.drawText(rect.adjusted(4, 2, -4, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, str(day.day))
            records = self.by_day.get(day, [])
            room = max(0, (rect.height() - 18) // (self.CHIP_PX + 2))
            for slot, record in enumerate(records[:room]):
                chip = QRect(rect.left() + 3, rect.top() + 17 + slot * (self.CHIP_PX + 2), rect.width() - 6, self.CHIP_PX)
                is_selected = record.record_id == self.block.controller.selected_id
                painter.setPen(Qt.PenStyle.NoPen)
                color = self.block.chip_color(record)
                painter.setBrush(color.lighter(135) if is_selected else color)
                painter.drawRoundedRect(chip, 3, 3)
                painter.setPen(QPen(QColor("#ffffff")))
                painter.drawText(chip.adjusted(4, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter,
                                 painter.fontMetrics().elidedText(record.title, Qt.TextElideMode.ElideRight, chip.width() - 6))
                self.chips.append((chip, record))
            if len(records) > room:
                painter.setPen(QPen(QColor(MUTED_TEXT_COLOR)))
                painter.drawText(rect.adjusted(4, 2, 0, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                                 f"+{len(records) - room}")

    def _chip_at(self, point: QPoint) -> Record | None:
        return next((record for rect, record in self.chips if rect.contains(point)), None)

    def mousePressEvent(self, event) -> None:                            # noqa: N802
        record = self._chip_at(event.position().toPoint())
        if record is not None:
            self.block.controller.select(record.record_id)
            self._press = (event.position().toPoint(), record)
            self.update()

    def mouseMoveEvent(self, event) -> None:                             # noqa: N802
        if self._press and (event.position().toPoint() - self._press[0]).manhattanLength() > 6:
            record = self._press[1]
            self._press = None
            start_record_drag(self, record.record_id, record.title)

    def mouseReleaseEvent(self, _event) -> None:                         # noqa: N802
        self._press = None

    def mouseDoubleClickEvent(self, event) -> None:                      # noqa: N802
        """A record: open its form. An empty part of a day: a new record on that day."""
        point = event.position().toPoint()
        record, day = self._chip_at(point), self.day_at(point)
        if record is not None:
            self._press = None
            self.block.controller.open_form(record.record_id)
        elif day is not None and self.block.date_key:
            self.block.controller.new_record({self.block.date_key: day.isoformat()})

    def contextMenuEvent(self, event) -> None:                            # noqa: N802
        record = self._chip_at(event.pos())
        if record is not None:
            self._press = None
            self.block.controller.show_record_menu(record.record_id, self, event.globalPos())

    def dragEnterEvent(self, event) -> None:                             # noqa: N802
        event.acceptProposedAction() if dragged_record_id(event) is not None else event.ignore()

    def dragMoveEvent(self, event) -> None:                              # noqa: N802
        event.acceptProposedAction() if dragged_record_id(event) is not None else event.ignore()

    def dropEvent(self, event) -> None:                                  # noqa: N802
        record_id, day = dragged_record_id(event), self.day_at(event.position().toPoint())
        if record_id is not None and day is not None:
            event.acceptProposedAction()
            QTimer.singleShot(0, lambda: self.block.move_to_day(record_id, day))


class CalendarBlock(QWidget):
    def __init__(self, controller: RecordsController) -> None:
        super().__init__()
        self.controller = controller
        self.date_key: str | None = None
        layout, row = block_layout(self)
        self.date_combo = QComboBox(toolTip="Which date places a record on the calendar")
        self.date_combo.activated.connect(lambda: (setattr(self, "date_key", self.date_combo.currentData()), self.refresh()))
        row.addWidget(self.date_combo)
        add_button(row, "◀", lambda: self._step(-1))
        self.month_label = QLabel()
        row.addWidget(self.month_label)
        add_button(row, "▶", lambda: self._step(1))
        add_button(row, "Today", lambda: self._step(0))
        row.addStretch()
        reserve_controls_space(row)
        self.grid = CalendarGrid(self)
        layout.addWidget(self.grid, 1)
        controller.records_changed.connect(self.refresh)
        controller.selection_changed.connect(self.grid.update)
        self.refresh()

    def _step(self, months: int) -> None:
        if months == 0:
            self.grid.month = dt.date.today().replace(day=1)
        else:
            year, month = divmod(self.grid.month.month - 1 + months, 12)
            self.grid.month = dt.date(self.grid.month.year + year, month + 1, 1)
        self.refresh()

    def refresh(self) -> None:
        dates = self.controller.properties_of("date")
        if self.date_key not in [spec.key for spec in dates]:
            self.date_key = dates[0].key if dates else None
        self.date_combo.blockSignals(True)
        self.date_combo.clear()
        for spec in dates:
            self.date_combo.addItem(spec.title, spec.key)
        self.date_combo.setCurrentIndex(max(0, self.date_combo.findData(self.date_key)))
        self.date_combo.blockSignals(False)
        self.month_label.setText(self.grid.month.strftime("%B %Y"))
        self.grid.by_day = {}
        for record in self.controller.records():
            day = parse_date(record.values.get(self.date_key)) if self.date_key else None
            if day:
                self.grid.by_day.setdefault(day, []).append(record)
        self.grid.update()

    def chip_color(self, record: Record) -> QColor:
        selects = self.controller.properties_of("select")
        value = record.values.get(selects[0].key) if selects else None
        return tag_color(value).darker(110) if value else QColor(ACCENT_COLOR)

    def move_to_day(self, record_id: int, day: dt.date) -> None:
        if self.date_key:
            self.controller.set_value(record_id, self.date_key, day.isoformat())

    def block_state(self) -> dict:
        return {"date_key": self.date_key}

    def restore_block_state(self, state: dict) -> None:
        self.date_key = state.get("date_key")
        self.refresh()
# --- END [2.4] ---

# ==============================================================================
# [2.5] form_block
# ==============================================================================
class AddPropertyDialog(QDialog):
    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add property")
        form = QFormLayout(self)
        self.name_edit = QLineEdit(placeholderText="e.g. Budget, Reviewer, Due date")
        self.kind_combo = QComboBox()
        for kind in PROPERTY_KINDS:
            self.kind_combo.addItem(KIND_TITLES[kind], kind)
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


class FormBlock(QWidget):
    def __init__(self, controller: RecordsController) -> None:
        super().__init__()
        self.controller = controller
        self.setters: dict[str, tuple[QWidget, callable]] = {}
        self._shown_properties: list[str] = []
        self.shown_id: int | None = None                 # record the editors show (edits go to it)
        layout, row = block_layout(self)
        self.title_edit = QLineEdit(objectName="record_title_edit", placeholderText="Name")
        self.title_edit.editingFinished.connect(self._commit_title)
        row.addWidget(self.title_edit, 1)
        self.more_btn = QToolButton(text="⋯", toolTip=f"Duplicate / delete this {controller.noun}", objectName="menu_glyph_btn",
                                    popupMode=QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.more_btn)
        menu.addAction("Duplicate").triggered.connect(lambda: controller.duplicate(controller.selected_id))
        menu.addAction("Delete").triggered.connect(lambda: controller.delete([controller.selected_id], self))
        self.more_btn.setMenu(menu)
        row.addWidget(self.more_btn)
        reserve_controls_space(row)
        self.scroll_area = QScrollArea(widgetResizable=True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.scroll_area, 1)
        self.empty_label = QLabel(f"Select a {controller.noun}, or create one with + New.", objectName="empty_tile_label",
                                  alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label, 1)
        footer = QHBoxLayout()
        self.meta_label = QLabel(objectName="record_meta_label")
        footer.addWidget(self.meta_label, 1)
        add_button(footer, "+ Property", self._add_property, "Add a property to every record of this collection")
        layout.addLayout(footer)
        controller.records_changed.connect(self.refresh)
        controller.selection_changed.connect(self.refresh)
        self.refresh()

    def _build_form(self) -> None:
        panel = QWidget(objectName="record_form_panel")
        form = QFormLayout(panel)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.setters = {}
        for spec in self.controller.collection.properties:
            editor, setter = make_editor(self.controller, spec,
                                         lambda value, key=spec.key: self._commit(key, value))
            label = QLabel(spec.title)
            label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            label.customContextMenuRequested.connect(lambda _pos, spec=spec, label=label: self._property_menu(spec, label))
            label.setToolTip(f"{KIND_TITLES.get(spec.kind, spec.kind)}" + (" · right-click to remove" if spec.is_user else ""))
            if spec.kind == "long_text":
                form.addRow(label)
                form.addRow(editor)
            else:
                form.addRow(label, editor)
            self.setters[spec.key] = (editor, setter)
        self.scroll_area.setWidget(panel)
        self._shown_properties = [spec.key for spec in self.controller.collection.properties]

    def refresh(self) -> None:
        record = self.controller.selected()
        is_same_record = record is not None and record.record_id == self.shown_id
        if not is_same_record:
            self._flush_pending()
        self.shown_id = record.record_id if record is not None else None
        for widget in (self.scroll_area, self.title_edit, self.more_btn):
            widget.setVisible(record is not None)
        self.empty_label.setVisible(record is None)
        if record is None:
            self.meta_label.setText("")
            return
        if self._shown_properties != [spec.key for spec in self.controller.collection.properties]:
            self._build_form()
        if not (is_same_record and self.title_edit.hasFocus()):
            self.title_edit.setText(record.title)
        for key, (editor, setter) in self.setters.items():
            if not (is_same_record and editor.hasFocus()):   # never overwrite what the user is typing
                setter(record.values.get(key))
        self.meta_label.setText(f"Created {dt.datetime.fromtimestamp(record.created_at):%Y-%m-%d %H:%M} · "
                                f"edited {dt.datetime.fromtimestamp(record.updated_at):%Y-%m-%d %H:%M}")

    def _flush_pending(self) -> None:
        """Long text saves after a pause in typing; save it now (to the record it belongs to)."""
        for editor, _setter in self.setters.values():
            timer = getattr(editor, "pending_timer", None)
            if timer is not None and timer.isActive():
                timer.stop()
                timer.timeout.emit()

    def _commit(self, key: str, value) -> None:
        record = self.controller.collection.get(self.shown_id) if self.shown_id is not None else None
        if record is not None and record.values.get(key) != value:
            self.controller.set_value(record.record_id, key, value)

    def _commit_title(self) -> None:
        record = self.controller.collection.get(self.shown_id) if self.shown_id is not None else None
        if record is not None and self.title_edit.text().strip() != record.title:
            if not self.controller.set_value(record.record_id, TITLE_KEY, self.title_edit.text().strip()):
                self.title_edit.setText(record.title)

    def _add_property(self) -> None:
        dialog = AddPropertyDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            spec = self.controller.collection.add_property(*dialog.values())
            self.controller.report(f"Added property “{spec.title}”")
        except ValueError as error:
            self.controller.report(f"Add property: {error}", is_error=True)

    def _property_menu(self, spec: PropertySpec, label: QLabel) -> None:
        menu = QMenu(self)
        action = menu.addAction(f"Remove property “{spec.title}”")
        action.setEnabled(spec.is_user)
        action.triggered.connect(lambda: self._remove_property(spec))
        menu.exec(label.mapToGlobal(label.rect().bottomLeft()))

    def _remove_property(self, spec: PropertySpec) -> None:
        if QMessageBox.question(self, "Remove property", f"Remove “{spec.title}” from every record's form?\n"
                                "Values are kept and come back if you add it again.") == QMessageBox.StandardButton.Yes:
            self.controller.collection.remove_property(spec.key)
            self.controller.report(f"Removed property “{spec.title}”")
# --- END [2.5] ---


# ==============================================================================
# [3.1] record_block_specs
# ==============================================================================
def record_block_specs(controller: RecordsController) -> list[BlockSpec]:
    noun = controller.noun
    return [
        BlockSpec("table", "Table", f"Every {noun} as a row; edit cells in place, sort by a column", lambda: TableBlock(controller)),
        BlockSpec("board", "Board", f"{noun.capitalize()} cards grouped by a select property; drag between columns",
                  lambda: BoardBlock(controller)),
        BlockSpec("form", "Form", f"Every property of the selected {noun}; add your own properties", lambda: FormBlock(controller)),
        BlockSpec("timeline", "Timeline", f"{noun.capitalize()}s as bars from start to end date (Gantt); drag to reschedule",
                  lambda: TimelineBlock(controller)),
        BlockSpec("calendar", "Calendar", f"{noun.capitalize()}s on their date in a month grid; drag to move",
                  lambda: CalendarBlock(controller)),
    ]
# --- END [3.1] ---
