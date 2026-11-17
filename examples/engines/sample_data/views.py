# ==============================================================================
# FILE: examples/engines/sample_data/views.py
# OVERVIEW: The Sample data engine's own view: "Totals" - horizontal bars of a number summed per group (or rows
#           counted per group). Registered while the engine is attached, gone when it is detached.
# PURPOSE:
#   1. Shows that an engine can bring views: it uses only the view API (host, TableData, ViewType).
#   2. Mapping slots: "group" (a select field) and "value" (a number field; none = count rows).
# CONTRACT:
#   - Requires: PySide6, app.views.api (the view API), app.ui.theme colour tokens. Never app.modules.
#   - Exposes: TotalsView(host), register_views()
# NOTES:
#   - [2026-11-17] Created in Phase 3 of the engine plan (an engine-bundled view).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - BAR_PX (int | px): Height of one bar (default: 22).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. TOTALS_VIEW
#    1.1 view
# ==============================================================================

# ==============================================================================
# [1.1] view
# ==============================================================================
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from app.ui.theme import MUTED_TEXT_COLOR, PANEL_COLOR, TEXT_COLOR
from app.views.api import MappingSlot, TableData, ViewType, register_view_type, tag_color

BAR_PX = 22


class TotalsView(QWidget):
    def __init__(self, host) -> None:
        super().__init__()
        self.host = host
        self.totals: list[tuple[str, float]] = []
        self.unit = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(host.view_menu_button())
        row.addSpacing(84)                               # room for the corner controls
        layout.addLayout(row)
        layout.addStretch()
        self.setMinimumHeight(120)

    def set_data(self, data: TableData) -> None:
        group, value = self.host.mapping_for("group"), self.host.mapping_for("value")
        sums: dict[str, float] = {}
        for row in data.rows:
            key = str(row.get(group) or "—") if group else "All"
            sums[key] = sums.get(key, 0.0) + (float(row.get(value) or 0) if value else 1.0)
        field = data.field(value) if value else None
        self.unit = field.get("unit", "") if field else "rows"
        self.totals = sorted(sums.items(), key=lambda item: -item[1])
        self.update()

    def set_selection(self, _record_id) -> None:
        pass

    def paintEvent(self, _event) -> None:                                # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(PANEL_COLOR))
        if not self.totals:
            return
        top, label_px = 40, 110
        biggest = max(total for _key, total in self.totals) or 1.0
        width = max(10, self.width() - label_px - 110)
        for index, (key, total) in enumerate(self.totals):
            y = top + index * (BAR_PX + 6)
            painter.setPen(QColor(TEXT_COLOR))
            painter.drawText(QRectF(8, y, label_px - 12, BAR_PX), Qt.AlignmentFlag.AlignVCenter, key)
            painter.fillRect(QRectF(label_px, y, width * total / biggest, BAR_PX), tag_color(key))
            painter.setPen(QColor(MUTED_TEXT_COLOR))
            painter.drawText(QRectF(label_px + width * total / biggest + 6, y, 100, BAR_PX), Qt.AlignmentFlag.AlignVCenter,
                             f"{total:,.0f} {self.unit}")


def register_views() -> None:
    register_view_type(ViewType("totals", "Totals", "A number summed per group, as bars (from the Sample data engine)",
                                ("table",), TotalsView, engine="sample_data",
                                slots=(MappingSlot("group", "Group by", ("select",), required=True),
                                       MappingSlot("value", "Sum of", ("number", "integer")))))
# --- END [1.1] ---
