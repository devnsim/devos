# ==============================================================================
# FILE: app/modules/trading_chart/backtest.py
# OVERVIEW: Strategy interface and a vectorised, lookahead-free backtester.
# PURPOSE:
#   1. Strategy: turns bars into a target position per bar (+1 long, 0 flat, -1 short), decided on
#      that bar's CLOSE.
#   2. run_backtest(): executes each position change at the NEXT bar's open, charges fees on traded
#      notional, and reports equity, trades and statistics (return, drawdown, win rate, Sharpe ...).
#   3. STRATEGIES registry with example strategies (SMA cross, RSI mean reversion).
# CONTRACT:
#   - Requires: numpy, market_data.BarSeries, indicators (sma, rsi)
#   - Exposes: Strategy, StrategySpec, STRATEGIES, Trade, BacktestResult, run_backtest()
# NOTES:
#   - [2026-11-15] Created. Execution model: signal at close[i] -> fill at open[i+1]; position held
#                  from that open onward. A signal on the last bar is not filled. Fees = fee_rate x
#                  |position change| per fill (0.06 % default, typical taker fee).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - equity: Account value over time, starting at initial_equity.
# - exposure (%): Share of bars with an open position.
# - max drawdown (%): Largest fall from a running equity peak.
# - position (int8): Target holding per bar: +1 long, 0 flat, -1 short (fraction of equity).
# - Sharpe: Mean / std of per-bar returns, annualised with the bars-per-year of the timeframe.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. STRATEGIES
#    1.1 strategy_spec
#    1.2 example_strategies
# 2. BACKTEST
#    2.1 results
#    2.2 run_backtest
# ==============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np

from app.modules.trading_chart.indicators import rsi, sma
from app.modules.trading_chart.market_data import BarSeries

SECONDS_PER_YEAR = 365 * 86_400


# ==============================================================================
# [1.1] strategy_spec
# ==============================================================================
class Strategy(Protocol):
    def __call__(self, bars: BarSeries, **params) -> np.ndarray:
        """Target position per bar (int8, -1/0/+1), using only bars <= i for position i."""


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    title: str
    positions: Callable[..., np.ndarray]
    default_params: dict = field(default_factory=dict)
    description: str = ""

    def label(self, params: dict | None = None) -> str:
        params = params or self.default_params
        return f"{self.title} ({', '.join(f'{key} {value}' for key, value in params.items())})"
# --- END [1.1] ---

# ==============================================================================
# [1.2] example_strategies
# ==============================================================================
def sma_cross(bars: BarSeries, fast: int = 20, slow: int = 50, allow_short: bool = False) -> np.ndarray:
    fast_line, slow_line = sma(bars.close, fast), sma(bars.close, slow)
    position = np.where(fast_line > slow_line, 1, -1 if allow_short else 0)
    position[np.isnan(slow_line)] = 0
    return position.astype(np.int8)


def rsi_reversion(bars: BarSeries, length: int = 14, buy_below: float = 30, sell_above: float = 55) -> np.ndarray:
    """Long when RSI dips below buy_below, flat again once it recovers above sell_above."""
    values = rsi(bars.close, length)
    signal = np.full(len(values), np.nan)
    signal[values < buy_below] = 1
    signal[values > sell_above] = 0
    # Hold the last signal until the next one (forward fill), flat before the first.
    index = np.where(np.isnan(signal), 0, np.arange(len(signal)))
    np.maximum.accumulate(index, out=index)
    filled = signal[index]
    return np.nan_to_num(filled, nan=0.0).astype(np.int8)


STRATEGIES: dict[str, StrategySpec] = {spec.strategy_id: spec for spec in (
    StrategySpec("sma_cross", "SMA cross", sma_cross, {"fast": 20, "slow": 50, "allow_short": False},
                 "Long while the fast SMA is above the slow SMA."),
    StrategySpec("sma_cross_ls", "SMA cross L/S", sma_cross, {"fast": 20, "slow": 50, "allow_short": True},
                 "Long above, short below the slow SMA."),
    StrategySpec("rsi_reversion", "RSI reversion", rsi_reversion, {"length": 14, "buy_below": 30, "sell_above": 55},
                 "Buy oversold dips, exit on recovery."),
)}
# --- END [1.2] ---


# ==============================================================================
# [2.1] results
# ==============================================================================
@dataclass
class Trade:
    direction: int            # +1 long, -1 short
    entry_index: int          # bar whose OPEN filled the entry
    exit_index: int           # bar whose OPEN filled the exit (or the last bar's close if still open)
    entry_price: float
    exit_price: float
    is_open: bool

    @property
    def return_pct(self) -> float:
        return 100.0 * self.direction * (self.exit_price / self.entry_price - 1.0)


@dataclass
class BacktestResult:
    equity: np.ndarray
    held_position: np.ndarray            # position actually held during each bar
    trades: list[Trade]
    stats: dict[str, float]
# --- END [2.1] ---

# ==============================================================================
# [2.2] run_backtest
# ==============================================================================
def run_backtest(bars: BarSeries, positions: np.ndarray, timeframe_seconds: int,
                 fee_rate: float = 0.0006, initial_equity: float = 10_000.0) -> BacktestResult:
    count = len(bars)
    positions = np.asarray(positions, float)
    held = np.concatenate([[0.0], positions[:-1]])           # filled at this bar's open
    previous_held = np.concatenate([[0.0], held[:-1]])
    previous_close = np.concatenate([[bars.open[0]], bars.close[:-1]])

    # Old position earns previous close -> this open; new position earns this open -> this close.
    overnight = previous_held * (bars.open / previous_close - 1.0)
    intrabar = held * (bars.close / bars.open - 1.0)
    fees = fee_rate * np.abs(held - previous_held)
    bar_returns = (1.0 + overnight) * (1.0 + intrabar) * (1.0 - fees) - 1.0
    equity = initial_equity * np.cumprod(1.0 + bar_returns)

    # Trades: runs of a constant non-zero held position.
    trades: list[Trade] = []
    changes = np.flatnonzero(np.diff(np.concatenate([[0.0], held])) != 0)
    for start_position, start in enumerate(changes):
        direction = int(held[start])
        if direction == 0:
            continue
        end = changes[start_position + 1] if start_position + 1 < len(changes) else None
        if end is None:
            trades.append(Trade(direction, int(start), count - 1, float(bars.open[start]), float(bars.close[-1]), True))
        else:
            trades.append(Trade(direction, int(start), int(end), float(bars.open[start]), float(bars.open[end]), False))

    peak = np.maximum.accumulate(equity)
    bars_per_year = SECONDS_PER_YEAR / timeframe_seconds
    std = bar_returns.std()
    closed = [trade for trade in trades if not trade.is_open]
    stats = {
        "total_return_pct": 100.0 * (equity[-1] / initial_equity - 1.0) if count else 0.0,
        "buy_hold_return_pct": 100.0 * (bars.close[-1] / bars.open[0] - 1.0) if count else 0.0,
        "max_drawdown_pct": 100.0 * float((equity / peak - 1.0).min()) if count else 0.0,
        "trade_count": float(len(trades)),
        "win_rate_pct": 100.0 * np.mean([trade.return_pct > 0 for trade in closed]) if closed else 0.0,
        "sharpe": float(bar_returns.mean() / std * np.sqrt(bars_per_year)) if std > 0 else 0.0,
        "exposure_pct": 100.0 * float((held != 0).mean()) if count else 0.0,
        "fees_paid_pct": 100.0 * float(fees.sum()),
    }
    return BacktestResult(equity, held.astype(np.int8), trades, stats)
# --- END [2.2] ---
