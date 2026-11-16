# ==============================================================================
# FILE: app/modules/trading_chart/chart_items.py
# OVERVIEW: Fast pyqtgraph graphics for price charts: candles, volume bars and a time axis.
# PURPOSE:
#   1. CandlestickItem: draws only the candles inside the visible range (cheap at 100k+ bars);
#      thin wicks + bodies, up/down colours, a hairline when the body is too small to see.
#   2. VolumeItem: up/down coloured volume bars, also drawn for the visible range only.
#   3. TimeAxisItem: bottom axis for an index-based x (no gaps on weekends/outages) that labels
#      ticks with UTC times, and extrapolates times into the empty space right of the last bar.
# CONTRACT:
#   - Requires: numpy, pyqtgraph
#   - Exposes: CandlestickItem (set_colors), VolumeItem (colors), TimeAxisItem, UP_COLOR, DOWN_COLOR, CHART_THEMES
# NOTES:
#   - [2026-11-15] CHART_THEMES and settable candle / volume colours (Financial Chart theme setting).
#   - [2026-11-15] Health pass: removed unused VolumeItem.visible_max().
#   - [2026-11-15] Created. x = bar index; bar i is centred on x = i and is BODY_WIDTH wide.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - BODY_WIDTH (float): Candle body width in bar units (default: 0.7).
# - index-based x: Bars are placed at 0, 1, 2 ... regardless of time, like trading platforms do.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. ITEMS
#    1.1 candlestick_item
#    1.2 volume_item
#    1.3 time_axis_item
# ==============================================================================

from __future__ import annotations

import datetime as dt

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QLineF, QPointF, QRectF
from PySide6.QtGui import QBrush, QColor, QPen

UP_COLOR = (38, 166, 154)
DOWN_COLOR = (239, 83, 80)
CHART_THEMES = {                 # theme name: (up colour, down colour)
    "Teal / red": (UP_COLOR, DOWN_COLOR),
    "Green / red": ((76, 175, 80), (244, 67, 54)),
    "Blue / orange": ((66, 165, 245), (255, 152, 0)),
    "Mono": ((225, 225, 225), (115, 115, 115)),
}
BODY_WIDTH = 0.7


def _visible_range(item: pg.GraphicsObject, count: int) -> tuple[int, int]:
    view_rect = item.viewRect()
    if view_rect is None:
        return 0, count
    return max(0, int(np.floor(view_rect.left())) - 1), min(count, int(np.ceil(view_rect.right())) + 2)


# ==============================================================================
# [1.1] candlestick_item
# ==============================================================================
class CandlestickItem(pg.GraphicsObject):
    def __init__(self) -> None:
        super().__init__()
        self.open = self.high = self.low = self.close = np.zeros(0)
        self._bounds = QRectF()
        self.up_color, self.down_color = QColor(*UP_COLOR), QColor(*DOWN_COLOR)

    def set_colors(self, up_color: tuple, down_color: tuple) -> None:
        self.up_color, self.down_color = QColor(*up_color), QColor(*down_color)
        self.update()

    def set_data(self, open_, high, low, close) -> None:
        self.open, self.high, self.low, self.close = open_, high, low, close
        self.prepareGeometryChange()
        if len(close):
            low_value, high_value = float(np.nanmin(low)), float(np.nanmax(high))
            self._bounds = QRectF(-0.5, low_value, len(close), max(high_value - low_value, 1e-9))
        else:
            self._bounds = QRectF()
        self.update()

    def boundingRect(self) -> QRectF:
        return self._bounds

    def paint(self, painter, *args) -> None:
        start, stop = _visible_range(self, len(self.close))
        if stop <= start:
            return
        o, h, l, c = (array[start:stop] for array in (self.open, self.high, self.low, self.close))
        x = np.arange(start, stop, dtype=float)
        is_up = c >= o
        half = BODY_WIDTH / 2
        for mask, color in ((is_up, self.up_color), (~is_up, self.down_color)):
            if not mask.any():
                continue
            pen = QPen(color)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(QBrush(color))
            xs, highs, lows = x[mask], h[mask], l[mask]
            painter.drawLines([QLineF(QPointF(xi, lo), QPointF(xi, hi)) for xi, lo, hi in zip(xs, lows, highs)])
            tops, bottoms = np.maximum(o[mask], c[mask]), np.minimum(o[mask], c[mask])
            painter.drawRects([QRectF(xi - half, bottom, BODY_WIDTH, top - bottom)
                               for xi, bottom, top in zip(xs, bottoms, tops)])
# --- END [1.1] ---

# ==============================================================================
# [1.2] volume_item
# ==============================================================================
class VolumeItem(pg.GraphicsObject):
    ALPHA = 140

    def __init__(self) -> None:
        super().__init__()
        self.volume = np.zeros(0)
        self.is_up = np.zeros(0, bool)
        self.colors = (UP_COLOR, DOWN_COLOR)
        self._bounds = QRectF()

    def set_data(self, volume, is_up) -> None:
        self.volume, self.is_up = volume, is_up
        self.prepareGeometryChange()
        self._bounds = QRectF(-0.5, 0, len(volume), float(np.nanmax(volume)) if len(volume) else 1.0)
        self.update()

    def boundingRect(self) -> QRectF:
        return self._bounds

    def paint(self, painter, *args) -> None:
        start, stop = _visible_range(self, len(self.volume))
        if stop <= start:
            return
        painter.setPen(pg.mkPen(None))
        half = BODY_WIDTH / 2
        x = np.arange(start, stop, dtype=float)
        volume, is_up = self.volume[start:stop], self.is_up[start:stop]
        for mask, color in ((is_up, self.colors[0]), (~is_up, self.colors[1])):
            painter.setBrush(QBrush(QColor(*color, self.ALPHA)))
            painter.drawRects([QRectF(xi - half, 0.0, BODY_WIDTH, vi) for xi, vi in zip(x[mask], volume[mask])])
# --- END [1.2] ---

# ==============================================================================
# [1.3] time_axis_item
# ==============================================================================
class TimeAxisItem(pg.AxisItem):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.times = np.zeros(0, np.int64)
        self.timeframe_seconds = 60

    def set_times(self, times: np.ndarray, timeframe_seconds: int) -> None:
        self.times, self.timeframe_seconds = times, timeframe_seconds
        self.picture = None
        self.update()

    def time_at(self, index: float) -> int | None:
        if len(self.times) == 0:
            return None
        position = int(round(index))
        if position < 0:
            return int(self.times[0] + position * self.timeframe_seconds)
        if position >= len(self.times):
            return int(self.times[-1] + (position - len(self.times) + 1) * self.timeframe_seconds)
        return int(self.times[position])

    def format_time(self, epoch_s: int, detailed: bool = False) -> str:
        moment = dt.datetime.fromtimestamp(epoch_s, dt.timezone.utc)
        if self.timeframe_seconds >= 86_400:
            return moment.strftime("%a %d %b '%y" if detailed else "%d %b '%y")
        return moment.strftime("%a %d %b '%y  %H:%M" if detailed else "%d %b %H:%M")

    def tickStrings(self, values, scale, spacing):
        labels, previous_day = [], None
        for value in values:
            epoch_s = self.time_at(value)
            if epoch_s is None:
                labels.append("")
                continue
            moment = dt.datetime.fromtimestamp(epoch_s, dt.timezone.utc)
            if self.timeframe_seconds >= 86_400:
                labels.append(moment.strftime("%d %b"))
            elif moment.date() != previous_day:
                labels.append(moment.strftime("%d %b %H:%M"))
            else:
                labels.append(moment.strftime("%H:%M"))
            previous_day = moment.date()
        return labels
# --- END [1.3] ---
