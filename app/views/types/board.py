# ==============================================================================
# FILE: app/views/types/board.py
# OVERVIEW: Board view (kanban) - cards grouped into columns by a select field; drag a card to another column
#           or position, double-click to open it, right-click for the record menu.
# PURPOSE:
#   1. Mapping slot "group" (any select field; the first one by default), chosen in the view's own combo.
#   2. Moving a card sets its group value and its position through the host (the engine stores both).
# CONTRACT:
#   - Requires: app.views.api, app.ui.ui_tools, app.ui.canvas.tile_canvas.reserve_controls_space,
#               QSS selectors #board_column_panel, #board_column_label
#   - Exposes: BoardView(host), BOARD_VIEW (registered as "board")
# NOTES:
#   - [2026-11-17] Ported from app/ui/record_views.py BoardBlock onto the view API (Phase 2).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - COLUMN_PX (int | px): Width of one board column (default: 220).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. BOARD_VIEW
#    1.1 column
#    1.2 view
# ==============================================================================

# ==============================================================================
# [1.1] column
# ==============================================================================
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.ui_tools import block_layout, control_label
from app.views.api import MappingSlot, TableData, ViewType, dragged_record_id, field_text, register_view_type, \
    start_record_drag, tag_color

COLUMN_PX = 220


class BoardColumn(QListWidget):
    """One column: the cards whose group value is `value`; cards drag between columns."""

    def __init__(self, board: "BoardView", value: str) -> None:
        super().__init__()
        self.board, self.value = board, value
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setSpacing(2)
        self.setWordWrap(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.itemClicked.connect(lambda item: board.host.select(item.data(Qt.ItemDataRole.UserRole)))
        self.itemDoubleClicked.connect(lambda item: board.host.open_record(item.data(Qt.ItemDataRole.UserRole)))

    def contextMenuEvent(self, event) -> None:                            # noqa: N802
        item = self.itemAt(event.pos())
        if item is not None:
            self.board.host.show_record_menu(item.data(Qt.ItemDataRole.UserRole), event.globalPos(), self)

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
# --- END [1.1] ---


# ==============================================================================
# [1.2] view
# ==============================================================================
class BoardView(QWidget):
    def __init__(self, host) -> None:
        super().__init__()
        self.host = host
        self.data: TableData | None = None
        self.column_lists: list[BoardColumn] = []
        layout, row = block_layout(self)
        row.addWidget(control_label("Group by"))
        self.group_combo = QComboBox()
        self.group_combo.activated.connect(lambda: host.set_mapping("group", self.group_combo.currentData()))
        row.addWidget(self.group_combo)
        row.addStretch()
        row.addWidget(host.view_menu_button())
        reserve_controls_space(row)
        self.columns_area = QScrollArea(widgetResizable=True)
        self.columns_area.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.columns_area, 1)

    @property
    def group_key(self) -> str | None:
        return self.host.mapping_for("group")

    def set_data(self, data: TableData) -> None:
        self.data = data
        selects = data.fields_of("select")
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        for field in selects:
            self.group_combo.addItem(field["title"], field["key"])
        self.group_combo.setCurrentIndex(max(0, self.group_combo.findData(self.group_key)))
        self.group_combo.blockSignals(False)

        panel = QWidget()
        columns_layout = QHBoxLayout(panel)
        columns_layout.setContentsMargins(0, 0, 0, 0)
        columns_layout.setSpacing(6)
        self.column_lists = []
        field = data.field(self.group_key) if self.group_key else None
        if field is None:
            columns_layout.addWidget(QLabel("Add a Select property to group cards by it."))
        else:
            for value in data.options(field["key"]) + [""]:
                members = [row for row in data.rows if (row.get(field["key"]) or "") == value]
                if value == "" and not members and field.get("options"):
                    continue                                   # "No value" column only when it holds cards
                columns_layout.addWidget(self._column(field, value, members))
        columns_layout.addStretch()
        self.columns_area.setWidget(panel)

    def _column(self, field: dict, value: str, members: list[dict]) -> QWidget:
        column_panel = QFrame(objectName="board_column_panel")
        column_panel.setFixedWidth(COLUMN_PX)
        column_layout = QVBoxLayout(column_panel)
        column_layout.setContentsMargins(4, 4, 4, 4)
        column_layout.setSpacing(4)
        header = QLabel(f"{value or 'No ' + field['title']}  ·  {len(members)}", objectName="board_column_label")
        if value:
            header.setStyleSheet(f"border-left: 3px solid {tag_color(value).name()};")
        column_layout.addWidget(header)
        column_list = BoardColumn(self, value)
        for row in members:
            item = QListWidgetItem(self._card_text(row, field["key"]))
            item.setData(Qt.ItemDataRole.UserRole, row[self.data.key])
            column_list.addItem(item)
        self.column_lists.append(column_list)
        column_layout.addWidget(column_list, 1)
        new_btn = QPushButton("+ New", flat=True)
        new_btn.clicked.connect(lambda: self.host.create_record({field["key"]: value} if value else {}))
        column_layout.addWidget(new_btn)
        return column_panel

    def _card_text(self, row: dict, group_key: str) -> str:
        details = []
        for field in self.data.fields:
            if field["key"] != group_key and field["kind"] not in ("long_text", "checkbox") and row.get(field["key"]):
                details.append(field_text(field, row[field["key"]]))
            if len(details) == 2:
                break
        return self.data.title(row) + ("\n" + " · ".join(details) if details else "")

    def move_card(self, record_id, value: str, before_id) -> None:
        self.host.update_record(record_id, {self.group_key: value})
        if before_id != record_id:
            self.host.move_record(record_id, before_id)
        self.host.select(record_id)

    def set_selection(self, record_id) -> None:
        for column_list in self.column_lists:
            for index in range(column_list.count()):
                item = column_list.item(index)
                item.setSelected(item.data(Qt.ItemDataRole.UserRole) == record_id)


BOARD_VIEW = ViewType("board", "Board", "Cards grouped by a select field; drag between columns", ("table",), BoardView,
                      slots=(MappingSlot("group", "Group by", ("select",), required=True),))
register_view_type(BOARD_VIEW)
# --- END [1.2] ---
