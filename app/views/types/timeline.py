# ==============================================================================
# FILE: app/views/types/timeline.py
# OVERVIEW: Timeline view (Gantt) - a row per record, a bar from its start to its end date. Drag a bar to move
#           it, its right end to change the end date; double-click a name or bar to open it; double-click an
#           unscheduled row's empty chart to schedule it there.
# PURPOSE:
#   1. Mapping slots "start" (date), "end" (date) and "color" (select), chosen in the view's own combos.
#   2. Zoom (day width) is saved in the definition's presentation; date changes go through the host.
# CONTRACT:
#   - Requires: app.views.api, app.ui.ui_tools, app.ui.theme, app.ui.canvas.tile_canvas.reserve_controls_space
#   - Exposes: TimelineView(host), TimelineCanvas, TIMELINE_VIEW (registered as "timeline")
# NOTES:
#   - [2026-11-17] Ported from app/ui/record_views.py TimelineBlock onto the view API (Phase 2).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - day_px (int | px): Width of one day (zoom; 4-48, default 14), saved as presentation "day_px".
# - unscheduled row: A record without a start date (listed at the bottom, no bar).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. TIMELINE_VIEW
#    1.1 canvas
#    1.2 view
# ==============================================================================

# ==============================================================================
# [1.1] canvas
# ==============================================================================
from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QScrollArea, QToolButton, QWidget

from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.theme import (
    ACCENT_COLOR, ACCENT_HIGHLIGHT_COLOR, BORDER_COLOR, EDGE_COLOR, HEADER_COLOR, MUTED_TEXT_COLOR, PANEL_COLOR, TEXT_COLOR,
)
from app.ui.ui_tools import add_button, block_layout, control_label
from app.views.api import MappingSlot, TableData, ViewType, parse_date, register_view_type, tag_color


class TimelineCanvas(QWidget):
    NAME_PX, HEADER_PX, ROW_PX, HANDLE_PX = 170, 34, 24, 6

    def __init__(self, view: "TimelineView") -> None:
        super().__init__()
        self.view = view
        self.day_px = 14
        self.rows: list[tuple[dict, dt.date | None, dt.date | None]] = []
        self.first_day = dt.date.today()
        self.day_count = 60
        self.selected_id = None
        self._drag: dict | None = None
        self.setMouseTracking(True)

    def load(self, rows: list[dict], start_key: str | None, end_key: str | None) -> None:
        self.rows = []
        for row in rows:
            start = parse_date(row.get(start_key)) if start_key else None
            end = parse_date(row.get(end_key)) if end_key else None
            end = max(end, start) if start and end else start
            self.rows.append((row, start, end))
        self.rows.sort(key=lambda item: (item[1] is None, item[1] or dt.date.max))
        dates = [day for _row, start, end in self.rows for day in (start, end) if day] + [dt.date.today()]
        self.first_day = min(dates) - dt.timedelta(days=7)
        last_day = max(max(dates) + dt.timedelta(days=21), self.first_day + dt.timedelta(days=60))
        self.day_count = (last_day - self.first_day).days
        self.setMinimumSize(self.NAME_PX + self.day_count * self.day_px, self.HEADER_PX + max(1, len(self.rows)) * self.ROW_PX + 8)
        self.update()

    def row_id(self, row: dict):
        return row[self.view.data.key]

    def x_of(self, day: dt.date) -> int:
        return self.NAME_PX + (day - self.first_day).days * self.day_px

    def day_at(self, x: float) -> dt.date:
        return self.first_day + dt.timedelta(days=int((x - self.NAME_PX) // self.day_px))

    def bar_rect(self, row_index: int) -> QRect | None:
        _row, start, end = self.rows[row_index]
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
        for row_index, (row, _start, _end) in enumerate(self.rows):
            top = self.HEADER_PX + row_index * self.ROW_PX
            is_selected = self.row_id(row) == self.selected_id
            if is_selected:
                painter.fillRect(0, top, self.width(), self.ROW_PX, QColor(ACCENT_COLOR).darker(160))
            rect = self.bar_rect(row_index)
            if rect is not None:
                color = self.view.bar_color(row)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color.lighter(130) if is_selected else color)
                painter.drawRoundedRect(rect, 3, 3)
        # Name column on top, stuck to the visible left edge while scrolling sideways
        painter.fillRect(left, 0, self.NAME_PX - 4, self.height(), QColor(PANEL_COLOR))
        painter.fillRect(left, 0, self.NAME_PX - 4, self.HEADER_PX, QColor(HEADER_COLOR))
        for row_index, (row, start, _end) in enumerate(self.rows):
            top = self.HEADER_PX + row_index * self.ROW_PX
            if self.row_id(row) == self.selected_id:
                painter.fillRect(left, top, self.NAME_PX - 4, self.ROW_PX, QColor(ACCENT_COLOR))
            painter.setPen(QPen(QColor(TEXT_COLOR) if start else muted))
            name = painter.fontMetrics().elidedText(self.view.data.title(row) + ("" if start else "  (unscheduled)"),
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
        self.view.host.select(self.row_id(self.rows[row_index][0]))
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
            row, start, end = self.rows[self._drag["row"]]
            step = dt.timedelta(days=days - self._drag["days"])
            if self._drag["mode"] == "move":
                start, end = start + step, end + step
            else:
                end = max(start, end + step)
            self.rows[self._drag["row"]] = (row, start, end)
            self._drag["days"] = days
            self.update()

    def mouseReleaseEvent(self, _event) -> None:                         # noqa: N802
        drag, self._drag = self._drag, None
        if drag and drag["days"]:
            row, start, end = self.rows[drag["row"]]
            self.view.commit_dates(self.row_id(row), start, end)

    def mouseDoubleClickEvent(self, event) -> None:                      # noqa: N802
        """An unscheduled row's empty chart: schedule it there. Its name or its bar: open it."""
        row_index = self._row_at(event.position().y())
        if row_index is None:
            return
        row, start, _end = self.rows[row_index]
        x = event.position().x()
        if start is None and x > self.visible_left() + self.NAME_PX:
            day = self.day_at(x)
            self.view.commit_dates(self.row_id(row), day, day + dt.timedelta(days=6))
        else:
            self._drag = None
            self.view.host.open_record(self.row_id(row))

    def contextMenuEvent(self, event) -> None:                            # noqa: N802
        row_index = self._row_at(event.pos().y())
        if row_index is not None:
            self._drag = None
            self.view.host.show_record_menu(self.row_id(self.rows[row_index][0]), event.globalPos(), self)
# --- END [1.1] ---


# ==============================================================================
# [1.2] view
# ==============================================================================
class TimelineView(QWidget):
    def __init__(self, host) -> None:
        super().__init__()
        self.host = host
        self.data: TableData | None = None
        layout, row = block_layout(self)
        self.start_combo, self.end_combo = QComboBox(toolTip="Start date"), QComboBox(toolTip="End date")
        self.color_combo = QComboBox(toolTip="Colour bars by")
        for caption, combo, slot in (("From", self.start_combo, "start"), ("to", self.end_combo, "end"),
                                     ("", self.color_combo, "color")):
            if caption:
                row.addWidget(control_label(caption))
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            row.addWidget(combo)
            combo.activated.connect(lambda _index, combo=combo, slot=slot: host.set_mapping(slot, combo.currentData()))
        for text, step in (("−", -1), ("+", 1)):
            zoom_btn = QToolButton(text=text, toolTip="Zoom out" if step < 0 else "Zoom in", autoRaise=True)
            zoom_btn.clicked.connect(lambda _checked=False, step=step: self._zoom(step))
            row.addWidget(zoom_btn)
        add_button(row, "Today", self._scroll_to_today)
        row.addStretch()
        row.addWidget(host.view_menu_button())
        reserve_controls_space(row)
        self.canvas = TimelineCanvas(self)
        self.canvas.day_px = max(4, min(48, int(host.presentation.get("day_px", 14))))
        self.scroll_area = QScrollArea(widgetResizable=True)
        self.scroll_area.setWidget(self.canvas)
        self.scroll_area.horizontalScrollBar().valueChanged.connect(self.canvas.update)   # repaint the sticky names
        layout.addWidget(self.scroll_area, 1)
        hint = control_label("Drag a bar to move it, its right end to resize · double-click a name to open it · "
                             "double-click an unscheduled row to schedule it")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        QTimer.singleShot(0, self._scroll_to_today)

    def set_data(self, data: TableData) -> None:
        self.data = data
        dates, selects = data.fields_of("date"), data.fields_of("select")
        for combo, fields, slot in ((self.start_combo, dates, "start"), (self.end_combo, dates, "end"),
                                    (self.color_combo, selects, "color")):
            combo.blockSignals(True)
            combo.clear()
            for field in fields:
                combo.addItem(field["title"], field["key"])
            combo.setCurrentIndex(max(0, combo.findData(self.host.mapping_for(slot))))
            combo.blockSignals(False)
        self.canvas.load(data.rows, self.host.mapping_for("start"), self.host.mapping_for("end"))

    def set_selection(self, record_id) -> None:
        self.canvas.selected_id = record_id
        self.canvas.update()

    def _zoom(self, step: int) -> None:
        self.canvas.day_px = max(4, min(48, round(self.canvas.day_px * (1.4 if step > 0 else 1 / 1.4))))
        self.host.set_presentation("day_px", self.canvas.day_px)
        if self.data is not None:
            self.set_data(self.data)

    def _scroll_to_today(self) -> None:
        self.scroll_area.horizontalScrollBar().setValue(max(0, self.canvas.x_of(dt.date.today()) - TimelineCanvas.NAME_PX - 60))

    def bar_color(self, row: dict) -> QColor:
        key = self.host.mapping_for("color")
        value = row.get(key) if key else None
        return tag_color(value) if value else QColor(ACCENT_HIGHLIGHT_COLOR)

    def commit_dates(self, record_id, start: dt.date, end: dt.date) -> None:
        start_key, end_key = self.host.mapping_for("start"), self.host.mapping_for("end")
        if start_key is None:
            return
        values = {start_key: start.isoformat()}
        if end_key and end_key != start_key:
            values[end_key] = end.isoformat()
        self.host.update_record(record_id, values)


TIMELINE_VIEW = ViewType("timeline", "Timeline", "Bars from a start to an end date (Gantt); drag to reschedule", ("table",),
                         TimelineView, slots=(MappingSlot("start", "Start", ("date",), required=True),
                                              MappingSlot("end", "End", ("date",)),
                                              MappingSlot("color", "Colour by", ("select",))))
register_view_type(TIMELINE_VIEW)
# --- END [1.2] ---
