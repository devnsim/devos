# ==============================================================================
# FILE: app/views/types/calendar.py
# OVERVIEW: Calendar view - records on their date in a month grid. Click selects, drag moves to another day,
#           double-click a record to open it or an empty part of a day to add one there.
# PURPOSE:
#   1. Mapping slots "date" (date) and "color" (select).
#   2. Month navigation (◀ ▶ Today); date changes go through the host.
# CONTRACT:
#   - Requires: app.views.api, app.ui.ui_tools, app.ui.theme, app.ui.canvas.tile_canvas.reserve_controls_space
#   - Exposes: CalendarView(host), CalendarGrid, CALENDAR_VIEW (registered as "calendar")
# NOTES:
#   - [2026-11-17] Ported from app/ui/record_views.py CalendarBlock onto the view API (Phase 2).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - chip: One record drawn inside a day cell.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. CALENDAR_VIEW
#    1.1 grid
#    1.2 view
# ==============================================================================

# ==============================================================================
# [1.1] grid
# ==============================================================================
from __future__ import annotations

import calendar
import datetime as dt

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QLabel, QWidget

from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.theme import ACCENT_COLOR, ACCENT_HIGHLIGHT_COLOR, BORDER_COLOR, HEADER_COLOR, MUTED_TEXT_COLOR, PANEL_COLOR, \
    TEXT_COLOR
from app.ui.ui_tools import add_button, block_layout
from app.views.api import MappingSlot, TableData, ViewType, dragged_record_id, parse_date, register_view_type, \
    start_record_drag, tag_color


class CalendarGrid(QWidget):
    HEADER_PX, CHIP_PX = 20, 16

    def __init__(self, view: "CalendarView") -> None:
        super().__init__()
        self.view = view
        self.month = dt.date.today().replace(day=1)
        self.by_day: dict[dt.date, list[dict]] = {}
        self.chips: list[tuple[QRect, dict]] = []
        self.selected_id = None
        self._press: tuple[QPoint, dict] | None = None
        self.setAcceptDrops(True)
        self.setMinimumSize(280, 200)

    def row_id(self, row: dict):
        return row[self.view.data.key]

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
            rows = self.by_day.get(day, [])
            room = max(0, (rect.height() - 18) // (self.CHIP_PX + 2))
            for slot, row in enumerate(rows[:room]):
                chip = QRect(rect.left() + 3, rect.top() + 17 + slot * (self.CHIP_PX + 2), rect.width() - 6, self.CHIP_PX)
                is_selected = self.row_id(row) == self.selected_id
                painter.setPen(Qt.PenStyle.NoPen)
                color = self.view.chip_color(row)
                painter.setBrush(color.lighter(135) if is_selected else color)
                painter.drawRoundedRect(chip, 3, 3)
                painter.setPen(QPen(QColor("#ffffff")))
                painter.drawText(chip.adjusted(4, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter,
                                 painter.fontMetrics().elidedText(self.view.data.title(row), Qt.TextElideMode.ElideRight,
                                                                  chip.width() - 6))
                self.chips.append((chip, row))
            if len(rows) > room:
                painter.setPen(QPen(QColor(MUTED_TEXT_COLOR)))
                painter.drawText(rect.adjusted(4, 2, 0, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                                 f"+{len(rows) - room}")

    def _chip_at(self, point: QPoint) -> dict | None:
        return next((row for rect, row in self.chips if rect.contains(point)), None)

    def mousePressEvent(self, event) -> None:                            # noqa: N802
        row = self._chip_at(event.position().toPoint())
        if row is not None:
            self.view.host.select(self.row_id(row))
            self._press = (event.position().toPoint(), row)

    def mouseMoveEvent(self, event) -> None:                             # noqa: N802
        if self._press and (event.position().toPoint() - self._press[0]).manhattanLength() > 6:
            row = self._press[1]
            self._press = None
            start_record_drag(self, self.row_id(row), self.view.data.title(row))

    def mouseReleaseEvent(self, _event) -> None:                         # noqa: N802
        self._press = None

    def mouseDoubleClickEvent(self, event) -> None:                      # noqa: N802
        """A record: open it. An empty part of a day: a new record on that day."""
        point = event.position().toPoint()
        row, day = self._chip_at(point), self.day_at(point)
        date_key = self.view.host.mapping_for("date")
        if row is not None:
            self._press = None
            self.view.host.open_record(self.row_id(row))
        elif day is not None and date_key:
            self.view.host.create_record({date_key: day.isoformat()})

    def contextMenuEvent(self, event) -> None:                            # noqa: N802
        row = self._chip_at(event.pos())
        if row is not None:
            self._press = None
            self.view.host.show_record_menu(self.row_id(row), event.globalPos(), self)

    def dragEnterEvent(self, event) -> None:                             # noqa: N802
        event.acceptProposedAction() if dragged_record_id(event) is not None else event.ignore()

    def dragMoveEvent(self, event) -> None:                              # noqa: N802
        event.acceptProposedAction() if dragged_record_id(event) is not None else event.ignore()

    def dropEvent(self, event) -> None:                                  # noqa: N802
        record_id, day = dragged_record_id(event), self.day_at(event.position().toPoint())
        date_key = self.view.host.mapping_for("date")
        if record_id is not None and day is not None and date_key:
            event.acceptProposedAction()
            QTimer.singleShot(0, lambda: self.view.host.set_value(record_id, date_key, day.isoformat()))
# --- END [1.1] ---


# ==============================================================================
# [1.2] view
# ==============================================================================
class CalendarView(QWidget):
    def __init__(self, host) -> None:
        super().__init__()
        self.host = host
        self.data: TableData | None = None
        layout, row = block_layout(self)
        self.date_combo = QComboBox(toolTip="Which date places a record on the calendar")
        self.date_combo.activated.connect(lambda: host.set_mapping("date", self.date_combo.currentData()))
        row.addWidget(self.date_combo)
        add_button(row, "◀", lambda: self._step(-1))
        self.month_label = QLabel()
        row.addWidget(self.month_label)
        add_button(row, "▶", lambda: self._step(1))
        add_button(row, "Today", lambda: self._step(0))
        row.addStretch()
        row.addWidget(host.view_menu_button())
        reserve_controls_space(row)
        self.grid = CalendarGrid(self)
        layout.addWidget(self.grid, 1)

    def _step(self, months: int) -> None:
        if months == 0:
            self.grid.month = dt.date.today().replace(day=1)
        else:
            year, month = divmod(self.grid.month.month - 1 + months, 12)
            self.grid.month = dt.date(self.grid.month.year + year, month + 1, 1)
        if self.data is not None:
            self.set_data(self.data)

    def set_data(self, data: TableData) -> None:
        self.data = data
        date_key = self.host.mapping_for("date")
        self.date_combo.blockSignals(True)
        self.date_combo.clear()
        for field in data.fields_of("date"):
            self.date_combo.addItem(field["title"], field["key"])
        self.date_combo.setCurrentIndex(max(0, self.date_combo.findData(date_key)))
        self.date_combo.blockSignals(False)
        self.month_label.setText(self.grid.month.strftime("%B %Y"))
        self.grid.by_day = {}
        for row in data.rows:
            day = parse_date(row.get(date_key)) if date_key else None
            if day:
                self.grid.by_day.setdefault(day, []).append(row)
        self.grid.update()

    def set_selection(self, record_id) -> None:
        self.grid.selected_id = record_id
        self.grid.update()

    def chip_color(self, row: dict) -> QColor:
        key = self.host.mapping_for("color")
        value = row.get(key) if key else None
        return tag_color(value).darker(110) if value else QColor(ACCENT_COLOR)


CALENDAR_VIEW = ViewType("calendar", "Calendar", "Records on their date in a month grid; drag to move", ("table",),
                         CalendarView, slots=(MappingSlot("date", "Date", ("date",), required=True),
                                              MappingSlot("color", "Colour by", ("select",))))
register_view_type(CALENDAR_VIEW)
# --- END [1.2] ---
