# ==============================================================================
# FILE: app/views/types/chart.py
# OVERVIEW: Chart view - any table as a line, bar or scatter chart: an X field, a Y field and an optional
#           second Y. Dates on X read as dates; text or select on X becomes categories. "Totals by X" asks the
#           engine to group the rows by X (sum / average / count of Y), so big tables are summed where they live.
# PURPOSE:
#   1. Slots: x (number, integer, date, datetime, text, select), y and y2 (number, integer); presentation:
#      style (Line / Bar / Scatter) and total (None / Sum / Average / Count).
#   2. Long series are thinned to MAX_POINTS with the series toolkit (peaks kept); clicking a point (not a
#      total) selects its row on the page.
# CONTRACT:
#   - Requires: pyqtgraph, numpy, app.ui.plot_tools (styled_plot, style_plot_item), engines.toolkit.series.decimate,
#               engines.kernel.query.aggregate_names, app.views.api, app.ui.ui_tools, app.ui.theme tokens
#   - Exposes: ChartView(host), CHART_VIEW (registered as "chart"), STYLES, TOTALS, MAX_POINTS, series_for()
# NOTES:
#   - [2026-11-17] Created in Phase 5b of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - STYLES (tuple): "Line", "Bar", "Scatter".
# - TOTALS (dict): Total choice -> aggregate op (None: one point per row).
# - MAX_POINTS (int): Points drawn per series before thinning (default: 5000).
# - category axis: Text / select X values in first-seen order, drawn at 0, 1, 2 …
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. CHART_VIEW
#    1.1 series
#    1.2 view
# ==============================================================================

# ==============================================================================
# [1.1] series
# ==============================================================================
from __future__ import annotations

import datetime as dt

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QComboBox, QLabel, QWidget

from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.plot_tools import style_plot_item, styled_plot
from app.ui.theme import ACCENT_HIGHLIGHT_COLOR, TEXT_COLOR
from app.ui.ui_tools import block_layout
from app.views.api import MappingSlot, TableData, ViewType, register_view_type
from engines.kernel.query import aggregate_names
from engines.toolkit.series import decimate

STYLES = ("Line", "Bar", "Scatter")
TOTALS = {"None": None, "Sum": "sum", "Average": "avg", "Count": "count"}
MAX_POINTS = 5000
NUMERIC = ("number", "integer")


def _x_number(value, kind: str):
    if value in (None, ""):
        return None
    if kind in NUMERIC:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    try:
        return dt.datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


def series_for(rows: list[dict], x_key: str, x_kind: str, y_key: str, key: str) -> tuple[np.ndarray, np.ndarray, list, list[str]]:
    """(xs, ys, row ids, categories): rows with both values, sorted by X; text X becomes category indexes."""
    categories: list[str] = []
    points = []
    for row in rows:
        y = row.get(y_key)
        if y in (None, "") or isinstance(y, bool):
            continue
        try:
            y = float(y)
        except (TypeError, ValueError):
            continue
        if x_kind in ("text", "select", "long_text"):
            label = str(row.get(x_key) or "—")
            if label not in categories:
                categories.append(label)
            x = float(categories.index(label))
        else:
            x = _x_number(row.get(x_key), x_kind)
            if x is None:
                continue
        points.append((x, y, row.get(key)))
    if not categories:
        points.sort(key=lambda point: point[0])
    xs = np.array([point[0] for point in points], dtype=float)
    ys = np.array([point[1] for point in points], dtype=float)
    return xs, ys, [point[2] for point in points], categories
# --- END [1.1] ---


# ==============================================================================
# [1.2] view
# ==============================================================================
class ChartView(QWidget):
    def __init__(self, host) -> None:
        super().__init__()
        self.host = host
        self.data: TableData | None = None
        self.point_ids: list = []
        self._axis_kind = "plain"
        layout, row = block_layout(self)
        self.style_combo, self.total_combo = QComboBox(toolTip="How to draw"), QComboBox(toolTip="Totals by X")
        self.style_combo.addItems(STYLES)
        self.total_combo.addItems(list(TOTALS))
        self.style_combo.setCurrentText(host.presentation.get("style", "Line"))
        self.total_combo.setCurrentText(host.presentation.get("total", "None"))
        self.style_combo.currentTextChanged.connect(self._style_changed)
        self.total_combo.currentTextChanged.connect(self._total_changed)
        self.summary_label = QLabel(objectName="control_label")
        row.addWidget(self.style_combo)
        row.addWidget(QLabel("Totals", objectName="control_label"))
        row.addWidget(self.total_combo)
        row.addWidget(self.summary_label, 1)
        row.addWidget(host.view_menu_button())
        reserve_controls_space(row)
        self.plot = styled_plot()
        self.plot.getPlotItem().addLegend(offset=(8, 8))
        layout.addWidget(self.plot, 1)

    def _style_changed(self, style: str) -> None:
        self.host.set_presentation("style", style)
        if self.data is not None:
            self.set_data(self.data)

    def _total_changed(self, total: str) -> None:
        self.host.set_presentation("total", total)
        self._apply_total()

    def _apply_total(self) -> None:
        """Totals run in the engine: group the question by X (Sum / Average / Count of Y)."""
        op, x, y = TOTALS[self.total_combo.currentText()], self.host.mapping_for("x"), self.host.mapping_for("y")
        if op is None or not x:
            self.host.set_query(group_by=None, aggregates=None)
            return
        aggregates = [{"op": "count"}] if op == "count" or not y else \
            [{"op": op, "field": y}] + ([{"op": op, "field": y2}] if (y2 := self.host.mapping_for("y2")) else [])
        self.host.set_query(group_by=[x], aggregates=aggregates)

    def set_data(self, data: TableData) -> None:
        self.data = data
        plot = self.plot.getPlotItem()
        plot.clear()
        if plot.legend is not None:
            plot.legend.clear()
        x_key = self.host.mapping_for("x")
        grouped = bool(self.host.definition["query"].get("group_by"))
        if grouped and self.host.definition["query"].get("group_by") != [x_key]:
            self._apply_total()                              # X changed since the totals were asked for
            return
        y_keys = aggregate_names(self.host.definition["query"]) if grouped else \
            [key for key in (self.host.mapping_for("y"), self.host.mapping_for("y2")) if key]
        x_field = data.field(x_key) if x_key else None
        if x_field is None or not y_keys:
            self.summary_label.setText("Choose X and Y in Edit view…")
            return
        x_kind = "text" if grouped and x_field["kind"] not in NUMERIC + ("date", "datetime") else x_field["kind"]
        style = self.style_combo.currentText()
        colors = [ACCENT_HIGHLIGHT_COLOR, TEXT_COLOR]
        categories: list[str] = []
        self.point_ids = []
        drawn = 0
        for number, y_key in enumerate(y_keys):
            xs, ys, ids, categories = series_for(data.rows, x_key, x_kind, y_key, data.key)
            if len(xs) > MAX_POINTS and not categories:
                xs, ys = decimate(xs, ys, MAX_POINTS)
                ids = []                                     # thinned points no longer map to rows
            name = (data.field(y_key) or {"title": y_key})["title"]
            color = colors[number % len(colors)]
            if style == "Bar":
                width = 0.8 / len(y_keys) if categories else (np.min(np.diff(xs)) * 0.8 if len(xs) > 1 else 1.0)
                shift = (number - (len(y_keys) - 1) / 2) * width
                item = pg.BarGraphItem(x=xs + shift, height=ys, width=width, brush=color, name=name)
            elif style == "Scatter":
                item = pg.ScatterPlotItem(xs, ys, size=7, brush=pg.mkBrush(color), pen=None, name=name, data=ids or None)
                item.sigClicked.connect(self._point_clicked)
            else:
                item = pg.PlotDataItem(xs, ys, pen=pg.mkPen(color, width=1.6), name=name)
            plot.addItem(item)
            if style == "Bar" and plot.legend is not None:
                plot.legend.addItem(item, name)
            drawn += len(xs)
            self.point_ids = ids
        wanted = "date" if x_kind in ("date", "datetime") and not categories else "plain"
        if wanted != self._axis_kind:                        # swap the X axis only when its kind changes
            plot.setAxisItems({"bottom": pg.DateAxisItem(orientation="bottom") if wanted == "date" else
                               pg.AxisItem(orientation="bottom")})
            style_plot_item(plot, ("bottom",))
            self._axis_kind = wanted
        plot.getAxis("bottom").setTicks([[(index, label) for index, label in enumerate(categories)]] if categories else None)
        plot.setLabel("bottom", x_field["title"])
        self.summary_label.setText(f"{drawn:,} points" + (f" of {data.total:,} rows" if not grouped else " (totals)"))

    def _point_clicked(self, _item, points) -> None:
        if len(points) and points[0].data() is not None:
            self.host.select(points[0].data())

    def set_selection(self, _record_id) -> None:
        pass


CHART_VIEW = ViewType("chart", "Chart", "A line, bar or scatter chart of one or two number fields", ("table",), ChartView,
                      slots=(MappingSlot("x", "X", ("number", "integer", "date", "datetime", "text", "select"), required=True),
                             MappingSlot("y", "Y", ("number", "integer"), required=True),
                             MappingSlot("y2", "Second Y", ("number", "integer"), auto=False)))
register_view_type(CHART_VIEW)
# --- END [1.2] ---
