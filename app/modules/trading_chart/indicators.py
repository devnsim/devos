# ==============================================================================
# FILE: app/modules/trading_chart/indicators.py
# OVERVIEW: Technical indicators as a registry the chart (and later strategies) can draw from.
# PURPOSE:
#   1. Vectorised indicator maths on BarSeries (recursive filters via the C++ ema()).
#   2. IndicatorSpec: how an indicator is computed, which pane it lives in, its lines and levels.
#   3. INDICATORS registry + ActiveIndicator (an indicator with chosen parameters).
# CONTRACT:
#   - Requires: numpy, app.native.ema, market_data.BarSeries
#   - Exposes: sma(), ema(), rolling_std(), bollinger(), rsi(), macd(), vwap(), atr(),
#              IndicatorSpec, INDICATORS, ActiveIndicator, DEFAULT_PRESETS
# NOTES:
#   - [2026-11-15] Created. Every output has the same length as the input; values that are not
#                  defined yet (warm-up) are NaN. No indicator looks ahead (value i uses bars <= i).
#   - [2026-11-15] To add an indicator: write a compute(bars, **params) -> {line_name: array} and
#                  register an IndicatorSpec in INDICATORS.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - pane (str): "price" = drawn over the candles; "separate" = its own pane under the chart.
# - levels (tuple): Horizontal reference lines in a separate pane (e.g. RSI 30 / 70).
# - histogram_lines (tuple): Lines drawn as bars instead of curves (e.g. MACD histogram).
# - warm-up: The first bars where an indicator doesn't have enough history yet (NaN).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. MATHS
#    1.1 moving_averages
#    1.2 oscillators_and_bands
# 2. REGISTRY
#    2.1 indicator_spec
#    2.2 indicators
# ==============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from app import native
from app.modules.trading_chart.market_data import BarSeries


# ==============================================================================
# [1.1] moving_averages
# ==============================================================================
def sma(values: np.ndarray, length: int) -> np.ndarray:
    values = np.asarray(values, float)
    result = np.full(len(values), np.nan)
    if length <= len(values):
        sums = np.cumsum(np.concatenate([[0.0], values]))
        result[length - 1:] = (sums[length:] - sums[:-length]) / length
    return result


def ema(values: np.ndarray, length: int) -> np.ndarray:
    """EMA with alpha = 2 / (length + 1); first `length - 1` values are warm-up (NaN)."""
    result = native.ema(values, 2.0 / (length + 1))
    result[: length - 1] = np.nan
    return result


def rolling_std(values: np.ndarray, length: int) -> np.ndarray:
    values = np.asarray(values, float)
    mean = sma(values, length)
    mean_of_squares = sma(values * values, length)
    return np.sqrt(np.maximum(mean_of_squares - mean * mean, 0.0))
# --- END [1.1] ---

# ==============================================================================
# [1.2] oscillators_and_bands
# ==============================================================================
def bollinger(bars: BarSeries, length: int = 20, deviations: float = 2.0) -> dict[str, np.ndarray]:
    middle = sma(bars.close, length)
    width = deviations * rolling_std(bars.close, length)
    return {"upper": middle + width, "middle": middle, "lower": middle - width}


def rsi(values: np.ndarray, length: int = 14) -> np.ndarray:
    """Wilder's RSI: smoothed gains / losses with alpha = 1 / length."""
    change = np.diff(np.asarray(values, float), prepend=np.nan)
    gains = native.ema(np.where(np.isnan(change), np.nan, np.maximum(change, 0.0)), 1.0 / length)
    losses = native.ema(np.where(np.isnan(change), np.nan, np.maximum(-change, 0.0)), 1.0 / length)
    with np.errstate(divide="ignore", invalid="ignore"):
        result = 100.0 - 100.0 / (1.0 + gains / losses)
    result[losses == 0] = 100.0
    result[: length] = np.nan
    return result


def macd(values: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, np.ndarray]:
    line = ema(values, fast) - ema(values, slow)
    signal_line = native.ema(line, 2.0 / (signal + 1))
    signal_line[: slow + signal - 2] = np.nan
    return {"macd": line, "signal": signal_line, "histogram": line - signal_line}


def vwap(bars: BarSeries) -> np.ndarray:
    """Volume-weighted average price, reset at 00:00 UTC each day."""
    typical = (bars.high + bars.low + bars.close) / 3.0
    day = bars.time // 86_400
    weighted = np.cumsum(typical * bars.volume)
    volume = np.cumsum(bars.volume)
    day_start = np.concatenate([[0], np.flatnonzero(np.diff(day)) + 1])
    offsets = np.repeat(day_start, np.diff(np.concatenate([day_start, [len(day)]])))
    weighted_before = np.concatenate([[0.0], weighted])[offsets]
    volume_before = np.concatenate([[0.0], volume])[offsets]
    with np.errstate(divide="ignore", invalid="ignore"):
        return (weighted - weighted_before) / (volume - volume_before)


def atr(bars: BarSeries, length: int = 14) -> np.ndarray:
    previous_close = np.concatenate([[np.nan], bars.close[:-1]])
    true_range = np.nanmax(np.stack([bars.high - bars.low, np.abs(bars.high - previous_close),
                                     np.abs(bars.low - previous_close)]), axis=0)
    result = native.ema(true_range, 1.0 / length)
    result[: length - 1] = np.nan
    return result
# --- END [1.2] ---


# ==============================================================================
# [2.1] indicator_spec
# ==============================================================================
@dataclass(frozen=True)
class IndicatorSpec:
    indicator_id: str
    title: str
    pane: str                                            # "price" or "separate"
    compute: Callable[..., dict[str, np.ndarray]]
    default_params: dict = field(default_factory=dict)
    line_colors: dict = field(default_factory=dict)      # line name -> (r, g, b)
    levels: tuple = ()
    histogram_lines: tuple = ()
    value_range: tuple | None = None                     # fixed y range for the pane, e.g. (0, 100)

    def label(self, params: dict) -> str:
        values = [str(params[key]) for key in self.default_params if key in params]
        return f"{self.title} {' '.join(values)}".strip()


@dataclass
class ActiveIndicator:
    """An indicator on the chart with its parameters (saved in the module settings)."""

    indicator_id: str
    params: dict

    @property
    def spec(self) -> IndicatorSpec:
        return INDICATORS[self.indicator_id]

    @property
    def key(self) -> str:
        return f"{self.indicator_id}:{sorted(self.params.items())}"

    @property
    def label(self) -> str:
        return self.spec.label(self.params)

    def compute(self, bars: BarSeries) -> dict[str, np.ndarray]:
        return self.spec.compute(bars, **self.params)

    def to_dict(self) -> dict:
        return {"id": self.indicator_id, "params": self.params}
# --- END [2.1] ---

# ==============================================================================
# [2.2] indicators
# ==============================================================================
INDICATORS: dict[str, IndicatorSpec] = {spec.indicator_id: spec for spec in (
    IndicatorSpec("sma", "SMA", "price", lambda bars, length: {"sma": sma(bars.close, length)},
                  {"length": 20}, {"sma": (255, 183, 77)}),
    IndicatorSpec("ema", "EMA", "price", lambda bars, length: {"ema": ema(bars.close, length)},
                  {"length": 21}, {"ema": (100, 181, 246)}),
    IndicatorSpec("bollinger", "BB", "price", lambda bars, length, deviations: bollinger(bars, length, deviations),
                  {"length": 20, "deviations": 2.0},
                  {"upper": (149, 117, 205), "middle": (149, 117, 205), "lower": (149, 117, 205)}),
    IndicatorSpec("vwap", "VWAP", "price", lambda bars: {"vwap": vwap(bars)}, {}, {"vwap": (240, 98, 146)}),
    IndicatorSpec("rsi", "RSI", "separate", lambda bars, length: {"rsi": rsi(bars.close, length)},
                  {"length": 14}, {"rsi": (179, 157, 219)}, levels=(30, 50, 70), value_range=(0, 100)),
    IndicatorSpec("macd", "MACD", "separate", lambda bars, fast, slow, signal: macd(bars.close, fast, slow, signal),
                  {"fast": 12, "slow": 26, "signal": 9},
                  {"macd": (100, 181, 246), "signal": (255, 167, 38), "histogram": (120, 144, 156)},
                  levels=(0,), histogram_lines=("histogram",)),
    IndicatorSpec("atr", "ATR", "separate", lambda bars, length: {"atr": atr(bars, length)},
                  {"length": 14}, {"atr": (77, 182, 172)}),
)}

# Ready-made entries for the Indicators menu: (indicator_id, params).
DEFAULT_PRESETS: tuple[tuple[str, dict], ...] = (
    ("sma", {"length": 20}), ("sma", {"length": 50}), ("sma", {"length": 200}), ("ema", {"length": 21}),
    ("bollinger", {"length": 20, "deviations": 2.0}), ("vwap", {}),
    ("rsi", {"length": 14}), ("macd", {"fast": 12, "slow": 26, "signal": 9}), ("atr", {"length": 14}),
)
# --- END [2.2] ---
