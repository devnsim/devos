# ==============================================================================
# FILE: app/modules/trading_chart/view.py
# OVERVIEW: Trading chart module UI: live candlestick chart with volume, indicators, drawings and a
#           strategy backtest overlay. Front end of the trading_chart app (market_data, indicators,
#           backtest, store).
# PURPOSE:
#   1. Price pane: candles / Heikin-Ashi / line, price-overlay indicators, last-price line, crosshair
#      with price + time labels, horizontal-line drawings, trade markers. Price scale on the right.
#   2. Panes below, sharing the x axis: volume, one per separate indicator (RSI, MACD, ATR) and the
#      strategy's equity curve vs buy & hold.
#   3. Navigation: wheel zooms time around the cursor, drag pans, price auto-fits the visible bars
#      (turn Auto-scale off to drag the price axis freely), "Latest" jumps to the live edge.
#   4. Live: polls the feed 4x a second, grows the current bar and appends new ones; the view follows
#      the live edge unless you've scrolled back.
#   5. Persists timeframe, chart type, indicators, strategy, drawings and sim speed in its settings;
#      logs backtests in SQLite.
# CONTRACT:
#   - Requires: pyqtgraph, app.modules.trading_chart (market_data, indicators, backtest, store,
#               chart_items), app.native, app.ui.theme colours; QSS selectors #trading_chart_panel,
#               #chart_legend_label, #module_stats_label, #timeframe_btn, #live_toggle_btn;
#               app.modules.base.ModuleView, app.ui.plot_tools
#   - Exposes: TradingChartModule(parent=None, context=None, source=None) (QWidget); set_timeframe();
#              set_chart_type(); toggle_indicator(); set_strategy(); attributes base_bars, bars,
#              indicator_values, backtest
# NOTES:
#   - [2026-11-15] Created. x is the bar index (see chart_items.py); bars never leave gaps.
#   - [2026-11-15] Health pass: on ModuleView (settings + live loop) and plot_tools; the copied
#                  _setting/_save, live timer, show/hide gating and axis styling are gone. Stays a single
#                  view: its panes share one linked time axis, which blocks would break.
#   - [2026-11-15] At 1m the displayed bars are the base bars themselves (no copy), so bar-count
#                  changes are tracked in _displayed_count, not by comparing len(self.bars).
#   - [2026-11-15] Indicators and the backtest always use real OHLC, also in Heikin-Ashi mode (HA
#                  is a display transform only).
#   - [2026-11-15] Module toolbar's top row ends with reserve_controls_space(): module tiles now carry the
#                  fading corner controls (no title bar), which must not cover the toolbar.
#   - [2026-11-15] Financial Chart: settings window (gear) holds data source (mock now; live feeds listed as
#                  planned), chart type, theme (CHART_THEMES), auto-scale, indicators and strategy; the toolbar
#                  keeps timeframes, drawing, Latest, Live. History loads and backtests are announced in the log.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - base bars: 1-minute bars from the feed; every timeframe is resampled from them.
# - following (bool): The view's right edge is at the live bar, so new bars scroll into view.
# - RIGHT_MARGIN_BARS (int): Empty space kept right of the last bar (default: 12).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS_AND_CONSTANTS
#    1.1 imports
# 2. TRADING_CHART_MODULE
#    2.1 init_module
#    2.2 settings
#    2.3 build_toolbar
#    2.4 build_panes
#    2.5 compute
#    2.6 draw
#    2.7 view_fitting
#    2.8 crosshair_and_legend
#    2.9 live_loop
#    2.10 drawings
#    2.11 handlers
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QSizePolicy, QToolButton, QVBoxLayout,
    QWidget,
)

from app import native
from app.modules.trading_chart.backtest import STRATEGIES, BacktestResult, run_backtest
from app.modules.trading_chart.chart_items import CHART_THEMES, CandlestickItem, TimeAxisItem, VolumeItem
from app.modules.trading_chart.indicators import DEFAULT_PRESETS, INDICATORS, ActiveIndicator
from app.modules.trading_chart.market_data import TIMEFRAMES, BarSeries, MarketDataSource, MockBitcoinFeed, MockClock
from app.modules.trading_chart.store import TradingChartStore
from app.data.activity_log import get_activity_log
from app.modules.base import ModuleView
from app.ui.settings_dialog import SettingsDialog
from app.ui.ui_tools import tool_button
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.plot_tools import style_plot_item
from app.ui.theme import BACKGROUND_COLOR, MUTED_TEXT_COLOR, TEXT_COLOR

CHART_CANDLES, CHART_HEIKIN_ASHI, CHART_LINE = "Candles", "Heikin-Ashi", "Line"
NO_STRATEGY = "No strategy"
SPEEDS = {"1×": 1.0, "10×": 10.0, "60×": 60.0, "600×": 600.0}
CROSSHAIR_COLOR = (150, 150, 150)
DRAWING_COLOR = (255, 213, 79)
# --- END [1.1] ---


# ==============================================================================
# [2.1] init_module
# ==============================================================================
class TradingChartModule(ModuleView):
    LIVE_INTERVAL_MS = 250
    HISTORY_DAYS = 60
    INITIAL_VISIBLE_BARS = 140
    RIGHT_MARGIN_BARS = 12
    Y_PADDING = 0.06
    DEFAULT_INDICATORS = ({"id": "ema", "params": {"length": 21}}, {"id": "sma", "params": {"length": 50}},
                          {"id": "rsi", "params": {"length": 14}})

    def __init__(self, parent: QWidget | None = None, context=None, source: MarketDataSource | None = None) -> None:
        super().__init__(parent, context, "trading_chart_panel")
        self.store = TradingChartStore(context.database, context.instance_id) if context is not None else None
        self._load_settings()
        self.source = source or MockBitcoinFeed(MockClock(SPEEDS.get(self.speed_name, 1.0)))
        with get_activity_log().step("chart", f"Load {self.HISTORY_DAYS} days of {self.source.symbol} history"):
            self.base_bars = self.source.history(self.HISTORY_DAYS)
        get_activity_log().info("chart", f"Feed: {self.source.description} · {len(self.base_bars):,} one-minute bars")
        self.bars = BarSeries.empty()
        self._displayed_count = 0
        self.indicator_values: dict[str, dict[str, np.ndarray]] = {}
        self.backtest: BacktestResult | None = None
        self._mouse_index: int | None = None
        self._last_poll_ms = 0.0
        self.drawing_lines: list[pg.InfiniteLine] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(4)
        self._build_toolbar(layout)
        self.chart_legend_label = QLabel(objectName="chart_legend_label", textFormat=Qt.TextFormat.RichText)
        self.chart_legend_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.chart_legend_label)
        self.chart_widget = pg.GraphicsLayoutWidget()
        self.chart_widget.setBackground(BACKGROUND_COLOR)
        self.chart_widget.ci.layout.setSpacing(0)
        self.chart_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.chart_widget.scene().sigMouseClicked.connect(self._on_mouse_clicked)
        layout.addWidget(self.chart_widget, 1)
        self.status_label = QLabel(objectName="module_stats_label")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.status_label)

        self._rebuild_panes()
        self._recompute(reset_view=True, log_backtest=False)
# --- END [2.1] ---

# ==============================================================================
# [2.2] settings
# ==============================================================================
    def _load_settings(self) -> None:
        self.timeframe = self.setting("timeframe", "15m")
        if self.timeframe not in TIMEFRAMES:
            self.timeframe = "15m"
        self.chart_type = self.setting("chart_type", CHART_CANDLES)
        self.indicators = [ActiveIndicator(entry["id"], dict(entry["params"]))
                           for entry in self.setting("indicators", list(self.DEFAULT_INDICATORS))
                           if entry.get("id") in INDICATORS]
        self.strategy_id = self.setting("strategy", None)
        if self.strategy_id not in STRATEGIES:
            self.strategy_id = None
        self.drawing_prices: list[float] = list(self.setting("drawings", []))
        self.speed_name = self.setting("speed", "1×")
        self.is_auto_scale = self.setting("auto_scale", True)
        self.theme_name = self.setting("theme", "Teal / red")
        if self.theme_name not in CHART_THEMES:
            self.theme_name = "Teal / red"

    @property
    def up_color(self) -> tuple:
        return CHART_THEMES[self.theme_name][0]

    @property
    def down_color(self) -> tuple:
        return CHART_THEMES[self.theme_name][1]
# --- END [2.2] ---

# ==============================================================================
# [2.3] build_toolbar
# ==============================================================================
    def _build_toolbar(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(4)
        self.symbol_label = QLabel(f"<b>{self.source.symbol}</b>", objectName="module_title_label")
        self.symbol_label.setToolTip(self.source.description)
        row.addWidget(self.symbol_label)
        row.addSpacing(6)

        self.timeframe_btns: dict[str, QToolButton] = {}
        for name in TIMEFRAMES:
            button = QToolButton(objectName="timeframe_btn", text=name, checkable=True, autoRaise=True)
            button.setChecked(name == self.timeframe)
            button.clicked.connect(lambda _checked=False, timeframe=name: self.set_timeframe(timeframe))
            self.timeframe_btns[name] = button
            row.addWidget(button)
        row.addSpacing(6)

        row.addStretch()

        self.draw_line_btn = QToolButton(objectName="draw_line_btn", text="─ H-line", checkable=True, autoRaise=True,
                                         toolTip="Click on the price pane to place a horizontal line (drag to move)")
        self.clear_drawings_btn = QToolButton(text="Clear", autoRaise=True, toolTip="Remove all drawn lines")
        self.clear_drawings_btn.clicked.connect(self.clear_drawings)
        self.latest_btn = QToolButton(text="⇥ Latest", autoRaise=True, toolTip="Jump to the live bar")
        self.latest_btn.clicked.connect(lambda: self._reset_view())
        self.setup_live(self.LIVE_INTERVAL_MS, self.poll, live_text="❚❚ Live", paused_text="▶ Paused")
        self.settings_btn = tool_button("module_settings_btn", "settings.svg",
                                        "Chart settings: data source, chart type, theme, indicators, strategy")
        self.settings_btn.clicked.connect(self.open_settings)
        for widget in (self.draw_line_btn, self.clear_drawings_btn, self.latest_btn, self.live_toggle_btn,
                       self.settings_btn):
            row.addWidget(widget)
        reserve_controls_space(row)
        layout.addLayout(row)
# --- END [2.3] ---

# ==============================================================================
# [2.4] build_panes
# ==============================================================================
    def _new_pane(self, row: int) -> pg.PlotItem:
        axis = TimeAxisItem(orientation="bottom")
        plot = self.chart_widget.addPlot(row=row, col=0, axisItems={"bottom": axis})
        style_plot_item(plot, axes=("right", "bottom"))
        plot.hideAxis("left")
        plot.showAxis("right")
        plot.showGrid(x=True, y=True, alpha=0.08)
        plot.setClipToView(True)
        plot.setDownsampling(auto=True, mode="peak")
        plot.getAxis("right").setWidth(72)
        view_box = plot.getViewBox()
        view_box.setMouseEnabled(x=True, y=False)
        view_box.enableAutoRange(enable=False)
        view_box.setDefaultPadding(0)
        vertical = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen(CROSSHAIR_COLOR, style=Qt.PenStyle.DashLine), label="",
            labelOpts={"position": 0.04, "color": (230, 230, 230), "fill": (70, 70, 70, 230), "anchors": [(0.5, 1), (0.5, 1)]},
        )
        vertical.setVisible(False)
        plot.addItem(vertical, ignoreBounds=True)
        plot.crosshair_vertical = vertical
        horizontal = pg.InfiniteLine(
            angle=0, movable=False, pen=pg.mkPen(CROSSHAIR_COLOR, style=Qt.PenStyle.DashLine),
            label="{value:,.2f}", labelOpts={"position": 0.995, "color": (230, 230, 230), "fill": (70, 70, 70, 230),
                                             "anchors": [(1, 0.5), (1, 0.5)]},
        )
        horizontal.setVisible(False)
        plot.addItem(horizontal, ignoreBounds=True)
        plot.crosshair_horizontal = horizontal
        if self.panes:
            plot.setXLink(self.panes[0])
        self.panes.append(plot)
        return plot

    def _rebuild_panes(self) -> None:
        """(Re)create every pane: price, volume, one per separate indicator, equity."""
        self.chart_widget.clear()
        self.panes: list[pg.PlotItem] = []
        self.overlay_items: dict[str, dict[str, pg.PlotDataItem]] = {}
        self.indicator_panes: dict[str, pg.PlotItem] = {}

        # --- price ---
        self.price_plot = self._new_pane(0)
        price_view = self.price_plot.getViewBox()
        price_view.setMouseEnabled(x=True, y=not self.is_auto_scale)
        price_view.sigXRangeChanged.connect(lambda: self._fit_visible())
        self.candle_item = CandlestickItem()
        self.candle_item.set_colors(self.up_color, self.down_color)
        self.price_plot.addItem(self.candle_item)
        self.line_item = pg.PlotDataItem(pen=pg.mkPen((100, 181, 246), width=1.6))
        self.price_plot.addItem(self.line_item)
        self.last_price_line = pg.InfiniteLine(
            angle=0, movable=False, pen=pg.mkPen(self.up_color, style=Qt.PenStyle.DotLine),
            label="{value:,.2f}", labelOpts={"position": 0.995, "color": (255, 255, 255), "fill": self.up_color,
                                             "anchors": [(1, 0.5), (1, 0.5)]},
        )
        self.price_plot.addItem(self.last_price_line, ignoreBounds=True)
        # Trade markers: one uniformly styled layer each, filled with plain arrays (fast at 1000s of trades).
        self.long_entry_markers = pg.ScatterPlotItem(pxMode=True, size=11, symbol="t1", pen=None, brush=pg.mkBrush(self.up_color))
        self.short_entry_markers = pg.ScatterPlotItem(pxMode=True, size=11, symbol="t", pen=None, brush=pg.mkBrush(self.down_color))
        self.exit_markers = pg.ScatterPlotItem(pxMode=True, size=8, symbol="x", pen=None, brush=pg.mkBrush(220, 220, 220))
        for markers in (self.long_entry_markers, self.short_entry_markers, self.exit_markers):
            self.price_plot.addItem(markers)
        self._drawn_backtest = None
        for indicator in self.indicators:
            if indicator.spec.pane == "price":
                self.overlay_items[indicator.key] = {
                    line: self.price_plot.plot(pen=pg.mkPen(indicator.spec.line_colors.get(line, (200, 200, 200)), width=1.3),
                                               connect="finite")
                    for line in indicator.spec.line_colors
                }
        self.drawing_lines = []
        for price in self.drawing_prices:
            self._add_drawing_line(price, save=False)

        # --- volume ---
        self.volume_plot = self._new_pane(1)
        self.volume_item = VolumeItem()
        self.volume_item.colors = (self.up_color, self.down_color)
        self.volume_plot.addItem(self.volume_item)
        self.volume_plot.getAxis("right").setStyle(showValues=False)
        stretch = [(0, 7), (1, 1.4)]

        # --- separate indicators ---
        row = 2
        for indicator in self.indicators:
            spec = indicator.spec
            if spec.pane != "separate":
                continue
            pane = self._new_pane(row)
            pane.indicator = indicator
            for level in spec.levels:
                pane.addItem(pg.InfiniteLine(pos=level, angle=0, pen=pg.mkPen((90, 90, 90), style=Qt.PenStyle.DashLine)),
                             ignoreBounds=True)
            items = {}
            for line, color in spec.line_colors.items():
                if line in spec.histogram_lines:
                    items[line] = pane.plot(pen=pg.mkPen((*color, 160)), fillLevel=0, brush=(*color, 90), connect="finite")
                else:
                    items[line] = pane.plot(pen=pg.mkPen(color, width=1.3), connect="finite")
            label = pg.TextItem(indicator.label, color=MUTED_TEXT_COLOR, anchor=(0, 0))
            pane.addItem(label, ignoreBounds=True)
            pane.title_item = label
            self.overlay_items[indicator.key] = items
            self.indicator_panes[indicator.key] = pane
            stretch.append((row, 2))
            row += 1

        # --- equity ---
        self.equity_plot = None
        if self.strategy_id is not None:
            self.equity_plot = self._new_pane(row)
            self.equity_curve = self.equity_plot.plot(pen=pg.mkPen((255, 202, 40), width=1.5), name="strategy")
            self.hold_curve = self.equity_plot.plot(pen=pg.mkPen((120, 144, 156), width=1, style=Qt.PenStyle.DashLine))
            self.equity_plot.addItem(pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen((90, 90, 90))), ignoreBounds=True)
            self.equity_plot.title_item = pg.TextItem("Equity % (strategy vs buy & hold)", color=MUTED_TEXT_COLOR, anchor=(0, 0))
            self.equity_plot.addItem(self.equity_plot.title_item, ignoreBounds=True)
            stretch.append((row, 2))

        for pane in self.panes[:-1]:
            pane.hideAxis("bottom")
        for pane_row, factor in stretch:
            self.chart_widget.ci.layout.setRowStretchFactor(pane_row, int(factor * 10))
# --- END [2.4] ---

# ==============================================================================
# [2.5] compute
# ==============================================================================
    def _recompute(self, reset_view: bool = False, log_backtest: bool = False) -> None:
        """Resample, compute indicators and the backtest, then redraw."""
        seconds = TIMEFRAMES[self.timeframe]
        # Not len(self.bars): at 1m, self.bars IS base_bars, which poll() has already grown.
        previous_count = self._displayed_count
        self.bars = self.base_bars if seconds == 60 else self.base_bars.resample(seconds)
        self.indicator_values = {indicator.key: indicator.compute(self.bars) for indicator in self.indicators}
        needs_backtest = self.backtest is None or len(self.backtest.equity) != len(self.bars)
        if self.strategy_id is not None and (log_backtest or reset_view or needs_backtest):
            spec = STRATEGIES[self.strategy_id]
            self.backtest = run_backtest(self.bars, spec.positions(self.bars, **spec.default_params), seconds)
            if log_backtest and self.store is not None:
                stats = self.backtest.stats
                get_activity_log().info("chart", f"Backtest {spec.title} on {self.timeframe}: {stats['total_return_pct']:+.1f}% "
                                                 f"(buy & hold {stats['buy_hold_return_pct']:+.1f}%), {int(stats['trade_count'])} trades")
                self.store.record_backtest(self.source.symbol, self.timeframe, self.strategy_id, spec.default_params,
                                           self.bars.time[0], self.bars.time[-1], self.backtest.stats)
        elif self.strategy_id is None:
            self.backtest = None
        self._displayed_count = len(self.bars)
        self._draw()
        if reset_view:
            self._reset_view()
        elif len(self.bars) != previous_count and self._is_following(previous_count):
            self.price_plot.getViewBox().translateBy(x=len(self.bars) - previous_count)
        self._fit_visible()

    def _display_ohlc(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        bars = self.bars
        if self.chart_type != CHART_HEIKIN_ASHI or len(bars) == 0:
            return bars.open, bars.high, bars.low, bars.close
        ha_close = (bars.open + bars.high + bars.low + bars.close) / 4
        first_open = (bars.open[0] + bars.close[0]) / 2
        # ha_open[i] = (ha_open[i-1] + ha_close[i-1]) / 2  ->  an EMA (alpha 0.5) of the previous HA close
        ha_open = np.concatenate([[first_open], native.ema(ha_close[:-1], 0.5, first_open)])
        return ha_open, np.maximum.reduce([bars.high, ha_open, ha_close]), np.minimum.reduce([bars.low, ha_open, ha_close]), ha_close
# --- END [2.5] ---

# ==============================================================================
# [2.6] draw
# ==============================================================================
    def _draw(self) -> None:
        bars = self.bars
        count = len(bars)
        x = np.arange(count, dtype=float)
        is_line = self.chart_type == CHART_LINE
        open_, high, low, close = self._display_ohlc()
        self.candle_item.setVisible(not is_line)
        self.line_item.setVisible(is_line)
        if is_line:
            self.line_item.setData(x, bars.close)
        else:
            self.candle_item.set_data(open_, high, low, close)
        self.volume_item.set_data(bars.volume, bars.close >= bars.open)

        if count:
            is_up = bars.close[-1] >= bars.open[-1]
            color = self.up_color if is_up else self.down_color
            self.last_price_line.setPen(pg.mkPen(color, style=Qt.PenStyle.DotLine))
            self.last_price_line.label.fill = pg.mkBrush(color)
            self.last_price_line.label.update()
            self.last_price_line.setValue(float(bars.close[-1]))
        for pane in self.panes:
            pane.getAxis("bottom").set_times(bars.time, TIMEFRAMES[self.timeframe])

        for indicator in self.indicators:
            values = self.indicator_values[indicator.key]
            for line, item in self.overlay_items.get(indicator.key, {}).items():
                item.setData(x, values[line])

        self._draw_backtest(x)
        limit = count + 400
        for pane in self.panes:
            pane.getViewBox().setLimits(xMin=-100, xMax=limit, minXRange=8, maxXRange=limit + 100)
        self._update_legend()
        self._update_status()

    def _draw_backtest(self, x: np.ndarray) -> None:
        if self.backtest is None or self.equity_plot is None:
            for markers in (self.long_entry_markers, self.short_entry_markers, self.exit_markers):
                markers.setData([], [])
            self._drawn_backtest = None
            return
        if self._drawn_backtest is self.backtest:
            return                                   # unchanged since the last draw (most live ticks)
        self._drawn_backtest = self.backtest
        bars = self.bars
        self.equity_curve.setData(x, 100 * (self.backtest.equity / self.backtest.equity[0] - 1))
        self.hold_curve.setData(x, 100 * (bars.close / bars.open[0] - 1))
        trades = self.backtest.trades
        offset = 0.004 * float(np.nanmax(bars.high) - np.nanmin(bars.low)) if len(bars) else 1.0
        direction = np.array([trade.direction for trade in trades], int)
        entry = np.array([trade.entry_index for trade in trades], int)
        is_long = direction > 0
        self.long_entry_markers.setData(entry[is_long], bars.low[entry[is_long]] - offset)
        self.short_entry_markers.setData(entry[~is_long], bars.high[entry[~is_long]] + offset)
        closed = [trade for trade in trades if not trade.is_open]
        exit_index = np.array([trade.exit_index for trade in closed], int)
        exit_long = np.array([trade.direction > 0 for trade in closed], bool)
        exit_y = np.where(exit_long, bars.high[exit_index] + offset, bars.low[exit_index] - offset) if closed else []
        self.exit_markers.setData(exit_index, exit_y)
# --- END [2.6] ---

# ==============================================================================
# [2.7] view_fitting
# ==============================================================================
    def _visible_slice(self) -> slice:
        left, right = self.price_plot.getViewBox().viewRange()[0]
        return slice(max(0, int(np.floor(left))), max(0, min(len(self.bars), int(np.ceil(right)) + 1)))

    def _is_following(self, bar_count: int | None = None) -> bool:
        bar_count = len(self.bars) if bar_count is None else bar_count
        return self.price_plot.getViewBox().viewRange()[0][1] >= bar_count - 2

    def _reset_view(self) -> None:
        count = len(self.bars)
        self.price_plot.getViewBox().setXRange(count - self.INITIAL_VISIBLE_BARS, count + self.RIGHT_MARGIN_BARS, padding=0)
        self._fit_visible()

    def _fit_visible(self) -> None:
        """Fit each pane's y range to the visible bars."""
        visible = self._visible_slice()
        if visible.stop <= visible.start or len(self.bars) == 0:
            return
        if self.is_auto_scale:
            _open, high, low, close = self._display_ohlc()
            if self.chart_type == CHART_LINE:
                high = low = close
            low_values, high_values = [np.nanmin(low[visible])], [np.nanmax(high[visible])]
            for indicator in self.indicators:
                if indicator.spec.pane == "price":
                    for values in self.indicator_values.get(indicator.key, {}).values():
                        window = values[visible]
                        if np.isfinite(window).any():
                            low_values.append(np.nanmin(window))
                            high_values.append(np.nanmax(window))
            low_value, high_value = min(low_values), max(high_values)
            padding = (high_value - low_value) * self.Y_PADDING or 1.0
            self.price_plot.getViewBox().setYRange(low_value - padding, high_value + padding, padding=0)
        self.volume_plot.getViewBox().setYRange(0, float(np.max(self.bars.volume[visible])) * 1.15, padding=0)

        for key, pane in self.indicator_panes.items():
            spec = pane.indicator.spec
            if spec.value_range is not None:
                low_value, high_value = spec.value_range
            else:
                windows = [values[visible] for values in self.indicator_values.get(key, {}).values()]
                finite = [window[np.isfinite(window)] for window in windows]
                finite = [window for window in finite if len(window)]
                if not finite:
                    continue
                low_value = min(float(window.min()) for window in finite)
                high_value = max(float(window.max()) for window in finite)
                if spec.histogram_lines:
                    low_value, high_value = min(low_value, 0.0), max(high_value, 0.0)
            padding = (high_value - low_value) * 0.08 or 1.0
            pane.getViewBox().setYRange(low_value - padding, high_value + padding, padding=0)
            pane.title_item.setPos(visible.start, high_value + padding)

        if self.equity_plot is not None and self.backtest is not None:
            equity_pct = 100 * (self.backtest.equity[visible] / self.backtest.equity[0] - 1)
            hold_pct = 100 * (self.bars.close[visible] / self.bars.open[0] - 1)
            low_value = float(min(equity_pct.min(), hold_pct.min(), 0))
            high_value = float(max(equity_pct.max(), hold_pct.max(), 0))
            padding = (high_value - low_value) * 0.08 or 1.0
            self.equity_plot.getViewBox().setYRange(low_value - padding, high_value + padding, padding=0)
            self.equity_plot.title_item.setPos(visible.start, high_value + padding)
# --- END [2.7] ---

# ==============================================================================
# [2.8] crosshair_and_legend
# ==============================================================================
    def _on_mouse_moved(self, scene_position) -> None:
        hovered = next((pane for pane in self.panes if pane.sceneBoundingRect().contains(scene_position)), None)
        if hovered is None or len(self.bars) == 0:
            self._clear_crosshair()
            return
        point = hovered.getViewBox().mapSceneToView(scene_position)
        index = int(round(point.x()))
        time_axis = self.panes[-1].getAxis("bottom")
        epoch_s = time_axis.time_at(index)
        for pane in self.panes:
            pane.crosshair_vertical.setValue(index)
            pane.crosshair_vertical.setVisible(True)
            pane.crosshair_horizontal.setVisible(pane is hovered)
        hovered.crosshair_horizontal.setValue(point.y())
        # Only the bottom pane's vertical line carries the time label (like a time-axis tag).
        time_text = time_axis.format_time(epoch_s, detailed=True) if epoch_s is not None else ""
        for pane in self.panes:
            is_bottom = pane is self.panes[-1]
            pane.crosshair_vertical.label.setVisible(is_bottom)
            if is_bottom:
                pane.crosshair_vertical.label.setFormat(time_text)
        self._mouse_index = min(max(index, 0), len(self.bars) - 1)
        self._update_legend()

    def _clear_crosshair(self) -> None:
        """Hide the crosshair; the legend goes back to the live bar."""
        self._mouse_index = None
        for pane in getattr(self, "panes", []):
            pane.crosshair_vertical.setVisible(False)
            pane.crosshair_horizontal.setVisible(False)
        self._update_legend()

    def _update_legend(self) -> None:
        bars = self.bars
        if len(bars) == 0:
            self.chart_legend_label.setText("")
            return
        index = len(bars) - 1 if self._mouse_index is None else min(self._mouse_index, len(bars) - 1)
        o, h, l, c, v = (float(array[index]) for array in (bars.open, bars.high, bars.low, bars.close, bars.volume))
        previous_close = float(bars.close[index - 1]) if index > 0 else o
        change = 100 * (c / previous_close - 1)
        color = "#%02x%02x%02x" % (self.up_color if c >= o else self.down_color)
        muted = MUTED_TEXT_COLOR
        parts = [
            f"<span style='color:{TEXT_COLOR}'><b>{self.source.symbol}</b> · {self.timeframe}</span>",
            f"<span style='color:{muted}'>O</span> <span style='color:{color}'>{o:,.2f}</span>",
            f"<span style='color:{muted}'>H</span> <span style='color:{color}'>{h:,.2f}</span>",
            f"<span style='color:{muted}'>L</span> <span style='color:{color}'>{l:,.2f}</span>",
            f"<span style='color:{muted}'>C</span> <span style='color:{color}'>{c:,.2f} ({change:+.2f}%)</span>",
            f"<span style='color:{muted}'>Vol</span> {v:,.1f}",
        ]
        for indicator in self.indicators:
            values = self.indicator_values.get(indicator.key, {})
            readings = [f"{values[line][index]:,.2f}" for line in indicator.spec.line_colors
                        if line in values and np.isfinite(values[line][index])]
            if readings:
                swatch = "#%02x%02x%02x" % next(iter(indicator.spec.line_colors.values()))
                parts.append(f"<span style='color:{swatch}'>{indicator.label}</span> {' / '.join(readings)}")
        self.chart_legend_label.setText("&nbsp;&nbsp;".join(parts))

    def _update_status(self) -> None:
        if len(self.base_bars) == 0:
            return
        change, change_pct = self.base_bars.change_since(86_400)
        day = slice(int(np.searchsorted(self.base_bars.time, self.base_bars.time[-1] - 86_400)), None)
        engine = "C++" if native.HAS_NATIVE and hasattr(native.devos_core, "ema") else "NumPy"
        text = (f"Last {self.base_bars.close[-1]:,.2f} ({change_pct:+.2f}% 24h) · 24h H {self.base_bars.high[day].max():,.2f} "
                f"L {self.base_bars.low[day].min():,.2f} · {len(self.bars):,} bars · {self.source.description}"
                f" · {self.speed_name} · maths {engine}")
        if self.backtest is not None:
            stats = self.backtest.stats
            text += (f"  │  {STRATEGIES[self.strategy_id].title}: {stats['total_return_pct']:+.1f}% "
                     f"(B&H {stats['buy_hold_return_pct']:+.1f}%) · max DD {stats['max_drawdown_pct']:.1f}% · "
                     f"{int(stats['trade_count'])} trades · win {stats['win_rate_pct']:.0f}% · Sharpe {stats['sharpe']:.2f}")
        self.status_label.setText(text)
# --- END [2.8] ---

# ==============================================================================
# [2.9] live_loop
# ==============================================================================
    def poll(self) -> None:
        """Fetch new ticks/bars from the feed and redraw (the live timer calls this)."""
        if len(self.base_bars) == 0:
            return
        started = time.perf_counter()
        update = self.source.bars_since(int(self.base_bars.time[-1]))
        if len(update) == 0:
            return
        self.base_bars.merge(update)
        self._recompute()
        self._last_poll_ms = (time.perf_counter() - started) * 1000
# --- END [2.9] ---

# ==============================================================================
# [2.10] drawings
# ==============================================================================
    def _on_mouse_clicked(self, event) -> None:
        if not self.draw_line_btn.isChecked() or event.button() != Qt.MouseButton.LeftButton:
            return
        if not self.price_plot.sceneBoundingRect().contains(event.scenePos()):
            return
        price = self.price_plot.getViewBox().mapSceneToView(event.scenePos()).y()
        self._add_drawing_line(price)
        self.draw_line_btn.setChecked(False)

    def _add_drawing_line(self, price: float, save: bool = True) -> None:
        line = pg.InfiniteLine(
            pos=price, angle=0, movable=True, pen=pg.mkPen(DRAWING_COLOR, width=1, style=Qt.PenStyle.DashLine),
            hoverPen=pg.mkPen(DRAWING_COLOR, width=2), label="{value:,.2f}",
            labelOpts={"position": 0.02, "color": (30, 30, 30), "fill": DRAWING_COLOR, "anchors": [(0, 0.5), (0, 0.5)]},
        )
        line.sigPositionChangeFinished.connect(lambda _line: self._save_drawings())
        self.price_plot.addItem(line, ignoreBounds=True)
        self.drawing_lines.append(line)
        if save:
            self._save_drawings()

    def _save_drawings(self) -> None:
        self.drawing_prices = [float(line.value()) for line in self.drawing_lines]
        self.save_setting("drawings", self.drawing_prices)

    def clear_drawings(self) -> None:
        for line in self.drawing_lines:
            self.price_plot.removeItem(line)
        self.drawing_lines = []
        self._save_drawings()
# --- END [2.10] ---

# ==============================================================================
# [2.11] handlers
# ==============================================================================
    def set_timeframe(self, timeframe: str) -> None:
        self.timeframe = timeframe
        for name, button in self.timeframe_btns.items():
            button.setChecked(name == timeframe)
        self.save_setting("timeframe", timeframe)
        self.backtest = None
        self._clear_crosshair()                      # its bar index means a different time now
        self._recompute(reset_view=True, log_backtest=self.strategy_id is not None)

    def set_chart_type(self, chart_type: str) -> None:
        self.chart_type = chart_type
        self.save_setting("chart_type", chart_type)
        self._draw()
        self._fit_visible()

    def toggle_indicator(self, indicator: ActiveIndicator, is_on: bool) -> None:
        self.indicators = [active for active in self.indicators if active.key != indicator.key]
        if is_on:
            self.indicators.append(indicator)
        self.save_setting("indicators", [active.to_dict() for active in self.indicators])
        self._rebuild_with_same_view()

    def set_strategy(self, strategy_id: str | None) -> None:
        self.strategy_id = strategy_id
        self.save_setting("strategy", strategy_id)
        self.backtest = None
        self._rebuild_with_same_view(log_backtest=strategy_id is not None)

    def _rebuild_with_same_view(self, log_backtest: bool = False) -> None:
        x_range = self.price_plot.getViewBox().viewRange()[0]
        self._rebuild_panes()
        self._recompute(log_backtest=log_backtest)
        self.price_plot.getViewBox().setXRange(*x_range, padding=0)
        self._fit_visible()

    def _on_speed_changed(self, speed_name: str) -> None:
        self.speed_name = speed_name
        self.save_setting("speed", speed_name)
        if isinstance(getattr(self.source, "clock", None), MockClock):
            self.source.clock.set_speed(SPEEDS[speed_name])
        self._update_status()

    # --- settings window ---
    def open_settings(self) -> None:
        if getattr(self, "settings_window", None) is None:
            self._build_settings_window()
        self._read_settings_into_fields()
        self.settings_window.open_window()

    def _build_settings_window(self) -> None:
        window = SettingsDialog(self.window(), "Financial Chart")
        data = window.add_form_tab("Data")
        self.source_combo = QComboBox()
        self.source_combo.addItem("Mock BTC-USD (simulated, deterministic)", "mock")
        for name in ("Coinbase live feed (planned — not connected yet)", "Binance live feed (planned — not connected yet)"):
            self.source_combo.addItem(name, None)
            self.source_combo.model().item(self.source_combo.count() - 1).setEnabled(False)
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(list(SPEEDS))
        self.speed_combo.setEnabled(isinstance(getattr(self.source, "clock", None), MockClock))
        data.addRow("Source", self.source_combo)
        data.addRow("Mock speed", self.speed_combo)
        data.addRow("", QLabel("Live feeds plug in through MarketDataSource (market_data.py)."))
        display = window.add_form_tab("Display")
        self.chart_type_combo = QComboBox()
        self.chart_type_combo.addItems([CHART_CANDLES, CHART_HEIKIN_ASHI, CHART_LINE])
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(CHART_THEMES))
        self.auto_scale_check = QCheckBox("Fit the price axis to the visible bars")
        display.addRow("Chart type", self.chart_type_combo)
        display.addRow("Theme", self.theme_combo)
        display.addRow("Auto-scale", self.auto_scale_check)
        self.indicator_list = QListWidget()
        for indicator_id, params in DEFAULT_PRESETS:
            indicator = ActiveIndicator(indicator_id, dict(params))
            row = QListWidgetItem(indicator.label)
            row.setData(Qt.ItemDataRole.UserRole, indicator.to_dict())
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            self.indicator_list.addItem(row)
        window.add_tab("Indicators", self.indicator_list)
        strategy = window.add_form_tab("Strategy")
        self.strategy_combo = QComboBox(toolTip="Backtest a strategy on the loaded bars")
        self.strategy_combo.addItem(NO_STRATEGY, None)
        for strategy_id, spec in STRATEGIES.items():
            self.strategy_combo.addItem(spec.title, strategy_id)
            self.strategy_combo.setItemData(self.strategy_combo.count() - 1, f"{spec.description}\n{spec.label()}",
                                            Qt.ItemDataRole.ToolTipRole)
        strategy.addRow("Backtest", self.strategy_combo)
        window.applied.connect(self._apply_settings_from_fields)
        self.settings_window = window

    def _read_settings_into_fields(self) -> None:
        self.speed_combo.setCurrentText(self.speed_name)
        self.chart_type_combo.setCurrentText(self.chart_type)
        self.theme_combo.setCurrentText(self.theme_name)
        self.auto_scale_check.setChecked(self.is_auto_scale)
        active = {indicator.key for indicator in self.indicators}
        for index in range(self.indicator_list.count()):
            row = self.indicator_list.item(index)
            entry = row.data(Qt.ItemDataRole.UserRole)
            is_on = ActiveIndicator(entry["id"], dict(entry["params"])).key in active
            row.setCheckState(Qt.CheckState.Checked if is_on else Qt.CheckState.Unchecked)
        self.strategy_combo.setCurrentIndex(max(0, self.strategy_combo.findData(self.strategy_id)))

    def _apply_settings_from_fields(self) -> None:
        """Apply everything at once (one rebuild), save it, announce it."""
        self.speed_name = self.speed_combo.currentText()
        self._on_speed_changed(self.speed_name)
        self.chart_type, self.theme_name = self.chart_type_combo.currentText(), self.theme_combo.currentText()
        self.is_auto_scale = self.auto_scale_check.isChecked()
        self.indicators = [ActiveIndicator(entry["id"], dict(entry["params"]))
                           for entry in (self.indicator_list.item(i).data(Qt.ItemDataRole.UserRole)
                                         for i in range(self.indicator_list.count())
                                         if self.indicator_list.item(i).checkState() == Qt.CheckState.Checked)]
        new_strategy = self.strategy_combo.currentData()
        strategy_changed = new_strategy != self.strategy_id
        self.strategy_id = new_strategy
        for key, value in (("chart_type", self.chart_type), ("theme", self.theme_name), ("auto_scale", self.is_auto_scale),
                           ("indicators", [indicator.to_dict() for indicator in self.indicators]),
                           ("strategy", self.strategy_id)):
            self.save_setting(key, value)
        if strategy_changed:
            self.backtest = None
        self._rebuild_with_same_view(log_backtest=strategy_changed and self.strategy_id is not None)
        self.price_plot.getViewBox().setMouseEnabled(x=True, y=not self.is_auto_scale)
        get_activity_log().info("chart", f"Chart settings applied: {self.chart_type}, {self.theme_name}, "
                                         f"{len(self.indicators)} indicators, strategy {self.strategy_id or 'none'}")
# --- END [2.11] ---
