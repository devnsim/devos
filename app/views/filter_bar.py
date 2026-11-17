# ==============================================================================
# FILE: app/views/filter_bar.py
# OVERVIEW: FilterBar - the shared filter editor every view host shows on demand: conditions (field,
#           operator, value) joined by "all" or "any", turned into a Query v1 filter tree.
# PURPOSE:
#   1. Operators offered per field kind (text: contains / is / starts with; numbers and dates: compare;
#      selects: is / is not; anything: is empty / is not empty).
#   2. changed() fires (debounced) whenever the filter's meaning changes; filter() returns the tree or None.
# CONTRACT:
#   - Requires: PySide6, engines.kernel.query (condition, OPERATORS), app.ui.ui_tools.tool_button,
#               QSS selector #filter_bar_panel
#   - Exposes: FilterBar(parent) with set_fields(fields, tree), set_filter(tree), filter(), signal changed()
# NOTES:
#   - [2026-11-17] Created in Phase 2 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - OPS_BY_KIND (dict): Field kind -> the operators that make sense for it.
# - OP_TITLES (dict): Operator -> the words users see.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. FILTER_BAR
#    1.1 operators
#    1.2 filter_bar
# ==============================================================================

# ==============================================================================
# [1.1] operators
# ==============================================================================
from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from app.ui.ui_tools import tool_button
from engines.kernel.query import VALUELESS, condition

OP_TITLES = {"contains": "contains", "eq": "is", "ne": "is not", "starts_with": "starts with", "lt": "<", "le": "≤",
             "gt": ">", "ge": "≥", "empty": "is empty", "not_empty": "is not empty"}
_COMPARE = ("eq", "ne", "lt", "le", "gt", "ge", "empty", "not_empty")
OPS_BY_KIND = {"text": ("contains", "eq", "ne", "starts_with", "empty", "not_empty"),
               "long_text": ("contains", "empty", "not_empty"), "url": ("contains", "eq", "empty", "not_empty"),
               "number": _COMPARE, "integer": _COMPARE, "date": _COMPARE, "datetime": _COMPARE,
               "select": ("eq", "ne", "empty", "not_empty"), "multi_select": ("contains", "empty", "not_empty"),
               "checkbox": ("eq",)}
# --- END [1.1] ---


# ==============================================================================
# [1.2] filter_bar
# ==============================================================================
class _ConditionRow(QWidget):
    def __init__(self, bar: "FilterBar") -> None:
        super().__init__()
        self.bar = bar
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.field_combo, self.op_combo = QComboBox(), QComboBox()
        self.value_edit = QLineEdit(placeholderText="value")
        for field in bar.fields:
            self.field_combo.addItem(field["title"], field["key"])
        self.field_combo.currentIndexChanged.connect(self._fill_ops)
        self.op_combo.currentIndexChanged.connect(self._on_op)
        self.value_edit.textChanged.connect(bar.changed_soon)
        remove_btn = tool_button("filter_remove_btn", text="×", tool_tip="Remove this condition")
        remove_btn.clicked.connect(lambda: bar.remove_row(self))
        for widget, stretch in ((self.field_combo, 0), (self.op_combo, 0), (self.value_edit, 1), (remove_btn, 0)):
            row.addWidget(widget, stretch)
        self._fill_ops()

    def kind(self) -> str:
        field = next((field for field in self.bar.fields if field["key"] == self.field_combo.currentData()), None)
        return field["kind"] if field else "text"

    def _fill_ops(self) -> None:
        self.op_combo.blockSignals(True)
        self.op_combo.clear()
        for op in OPS_BY_KIND.get(self.kind(), ("eq", "ne", "empty", "not_empty")):
            self.op_combo.addItem(OP_TITLES[op], op)
        self.op_combo.blockSignals(False)
        self.value_edit.setPlaceholderText("true / false" if self.kind() == "checkbox" else
                                           "YYYY-MM-DD" if self.kind() == "date" else "value")
        self._on_op()

    def _on_op(self) -> None:
        self.value_edit.setVisible(self.op_combo.currentData() not in VALUELESS)
        self.bar.changed_soon()

    def condition(self) -> dict | None:
        op = self.op_combo.currentData()
        if op in VALUELESS:
            return condition(self.field_combo.currentData(), op)
        text = self.value_edit.text().strip()
        if not text:
            return None
        value = text.lower() in ("true", "yes", "1", "✓") if self.kind() == "checkbox" else text
        return condition(self.field_combo.currentData(), op, value)

    def load(self, node: dict) -> None:
        self.field_combo.setCurrentIndex(max(0, self.field_combo.findData(node.get("field"))))
        self.op_combo.setCurrentIndex(max(0, self.op_combo.findData(node.get("op"))))
        value = node.get("value")
        self.value_edit.setText("" if value is None else str(value).lower() if isinstance(value, bool) else str(value))


class FilterBar(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="filter_bar_panel")
        self.fields: list[dict] = []
        self.rows: list[_ConditionRow] = []
        self.bar_layout = QVBoxLayout(self)
        self.bar_layout.setContentsMargins(4, 4, 4, 4)
        self.bar_layout.setSpacing(2)
        footer = QHBoxLayout()
        self.match_combo = QComboBox(toolTip="Rows must match all conditions, or any of them")
        self.match_combo.addItem("Match all", "all")
        self.match_combo.addItem("Match any", "any")
        self.match_combo.currentIndexChanged.connect(self.changed_soon)
        add_btn = QPushButton("+ Condition")
        add_btn.clicked.connect(lambda: self.add_row())
        footer.addWidget(self.match_combo)
        footer.addWidget(add_btn)
        footer.addStretch()
        self.bar_layout.addLayout(footer)
        self._timer = QTimer(self, singleShot=True, interval=350, timeout=self.changed.emit)

    def changed_soon(self, *_args) -> None:
        self._timer.start()

    def set_fields(self, fields: list[dict], tree: dict | None = None) -> None:
        """The fields conditions can use, and the filter to show with them (no changed() signal)."""
        self.fields = [field for field in fields if field["kind"] in OPS_BY_KIND]
        self.set_filter(tree, emit=False)

    def add_row(self, node: dict | None = None) -> _ConditionRow:
        row = _ConditionRow(self)
        if node:
            row.load(node)
        self.rows.append(row)
        self.bar_layout.insertWidget(len(self.rows) - 1, row)
        return row

    def remove_row(self, row: _ConditionRow) -> None:
        self.rows.remove(row)
        row.deleteLater()
        self.changed_soon()

    def set_filter(self, tree: dict | None, emit: bool = True) -> None:
        for row in self.rows:
            row.deleteLater()
        self.rows = []
        nodes, match = [], "all"
        if tree:
            match = "any" if "any" in tree else "all"
            nodes = tree.get(match, [tree]) if ("all" in tree or "any" in tree) else [tree]
        self.match_combo.blockSignals(True)
        self.match_combo.setCurrentIndex(self.match_combo.findData(match))
        self.match_combo.blockSignals(False)
        for node in nodes:
            if "field" in node:
                self.add_row(node)
        self._timer.stop()
        if emit:
            self.changed.emit()

    def filter(self) -> dict | None:
        nodes = [node for row in self.rows if (node := row.condition()) is not None]
        return {self.match_combo.currentData(): nodes} if nodes else None
# --- END [1.2] ---
