# ==============================================================================
# FILE: app/modules/trading_chart/market_data.py
# OVERVIEW: Market data model for the trading chart: OHLCV bar series, timeframes, the data-source
#           interface, and a mock BTC-USD feed that behaves like a live exchange.
# PURPOSE:
#   1. BarSeries: columnar OHLCV bars (NumPy) with resampling to any timeframe and live merging.
#   2. MarketDataSource: what any feed provides (history + new bars since a time); real exchange
#      feeds implement the same thing later.
#   3. MockBitcoinFeed: deterministic 1-minute BTC-USD history from 2026-01-01 to "now" plus a live
#      partial bar that fills in tick by tick. Same history every launch, so backtests repeat.
# CONTRACT:
#   - Requires: numpy, app.native.ema (Ornstein-Uhlenbeck volatility)
#   - Exposes: BarSeries, TIMEFRAMES, MarketDataSource, MockBitcoinFeed, MockClock
# NOTES:
#   - [2026-11-15] Created. Mock price model, per 1-minute bar (8 sub-steps for high/low):
#                    log-return = sigma * vol_mult * seasonality * N(0,1) (+ rare jumps)
#                    vol_mult   = exp(OU process), stationary sd ~0.45 (volatility clustering)
#                    sigma      = 55 % / year, seasonality peaks in the US session (UTC 13-21)
#                  Volume is log-normal, rising with |return| and the session. Days are generated
#                  in order from the anchor with per-day seeds, so any day's bars are reproducible.
#   - [2026-11-15] All times are UTC epoch seconds of the bar's OPEN.
#   - [2026-11-15] A live partial bar reveals its 8 sub-steps over the minute (linear in between),
#                  so it ends exactly equal to the deterministic history bar.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - bar: One time bucket of trading: open, high, low, close prices and traded volume (BTC).
# - partial bar: The current, still-forming bar (its close is the live price).
# - timeframe (str): Bar length key in TIMEFRAMES, e.g. "1m", "1h", "1D".
# - MockClock.speed (float): Simulated seconds per real second (1 = real time).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. BARS
#    1.1 timeframes
#    1.2 bar_series
# 2. SOURCES
#    2.1 market_data_source
#    2.2 mock_clock
#    2.3 mock_bitcoin_feed
# ==============================================================================

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from app import native

# ==============================================================================
# [1.1] timeframes
# ==============================================================================
TIMEFRAMES: dict[str, int] = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14_400, "1D": 86_400}
BASE_TIMEFRAME = "1m"
# --- END [1.1] ---


# ==============================================================================
# [1.2] bar_series
# ==============================================================================
@dataclass
class BarSeries:
    time: np.ndarray        # int64 epoch seconds (bar open), ascending
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    COLUMNS = ("time", "open", "high", "low", "close", "volume")

    @classmethod
    def empty(cls) -> BarSeries:
        return cls(np.zeros(0, np.int64), *(np.zeros(0) for _ in range(5)))

    def __len__(self) -> int:
        return len(self.time)

    def slice(self, start: int | None = None, stop: int | None = None) -> BarSeries:
        return BarSeries(*(getattr(self, name)[start:stop] for name in self.COLUMNS))

    def copy(self) -> BarSeries:
        return BarSeries(*(getattr(self, name).copy() for name in self.COLUMNS))

    def resample(self, seconds: int) -> BarSeries:
        """Aggregate into buckets of `seconds`, aligned to UTC (days start at 00:00 UTC)."""
        if len(self) == 0:
            return BarSeries.empty()
        bucket = (self.time // seconds) * seconds
        starts = np.concatenate([[0], np.flatnonzero(np.diff(bucket)) + 1])
        ends = np.concatenate([starts[1:], [len(self)]]) - 1
        return BarSeries(
            bucket[starts], self.open[starts], np.maximum.reduceat(self.high, starts),
            np.minimum.reduceat(self.low, starts), self.close[ends], np.add.reduceat(self.volume, starts),
        )

    def merge(self, update: BarSeries) -> int:
        """Merge newer bars in place-style (returns a new length): bars with a time already present
        replace it (a partial bar growing), later ones are appended. Returns how many bars were appended."""
        if len(update) == 0:
            return 0
        keep = self.time < update.time[0]
        appended = len(update) - int((self.time >= update.time[0]).sum())
        for name in self.COLUMNS:
            setattr(self, name, np.concatenate([getattr(self, name)[keep], getattr(update, name)]))
        return max(appended, 0)

    def change_since(self, seconds: float) -> tuple[float, float]:
        """(absolute, percent) change of the last close vs the close `seconds` before it."""
        if len(self) == 0:
            return 0.0, 0.0
        index = max(0, int(np.searchsorted(self.time, self.time[-1] - seconds)))
        reference = self.close[index]
        return float(self.close[-1] - reference), float(100 * (self.close[-1] / reference - 1))
# --- END [1.2] ---


# ==============================================================================
# [2.1] market_data_source
# ==============================================================================
class MarketDataSource(Protocol):
    """A feed of 1-minute bars. Real exchange connectors implement this too."""

    symbol: str
    description: str
    is_persistent: bool          # True: completed bars are worth caching in SQLite (real feeds)

    def now(self) -> float:
        """Current time on this feed's clock (epoch seconds)."""

    def history(self, days: float) -> BarSeries:
        """1-minute bars for the last `days` up to now, including the current partial bar."""

    def bars_since(self, time_s: int) -> BarSeries:
        """1-minute bars with open time >= time_s, up to now (the last one may be partial)."""
# --- END [2.1] ---

# ==============================================================================
# [2.2] mock_clock
# ==============================================================================
class MockClock:
    """Simulated time: starts at the real time and runs `speed` times faster."""

    def __init__(self, speed: float = 1.0) -> None:
        self._start_real = time.time()
        self._start_wall = time.monotonic()
        self.speed = speed

    def now(self) -> float:
        return self._start_real + (time.monotonic() - self._start_wall) * self.speed

    def set_speed(self, speed: float) -> None:
        current = self.now()                         # keep simulated time continuous
        self._start_real, self._start_wall, self.speed = current, time.monotonic(), speed
# --- END [2.2] ---

# ==============================================================================
# [2.3] mock_bitcoin_feed
# ==============================================================================
class MockBitcoinFeed:
    """Deterministic mock BTC-USD 1-minute feed (see NOTES for the model)."""

    ANCHOR_EPOCH_S = 1_767_225_600            # 2026-01-01 00:00 UTC
    START_PRICE = 94_000.0
    ANNUAL_VOLATILITY = 0.55
    SUBSTEPS = 8
    MINUTES_PER_DAY = 1440
    VOL_REVERSION = 0.002                     # OU mean reversion per minute
    VOL_OF_VOL = 0.03
    JUMP_PROBABILITY = 1 / 3000
    JUMP_SIZE = 0.006
    BASE_VOLUME_BTC = 14.0
    SEED = 20_260_101

    def __init__(self, clock: MockClock | None = None) -> None:
        self.symbol = "BTC-USD"
        self.description = "Mock BTC-USD (simulated, deterministic)"
        self.is_persistent = False
        self.clock = clock or MockClock()
        self._days: list[dict[str, np.ndarray]] = []
        self._state = (np.log(self.START_PRICE), 0.0)     # (log price, log vol multiplier)

    def now(self) -> float:
        return self.clock.now()

    # --- generation ---
    def _generate_day(self, day_index: int) -> dict[str, np.ndarray]:
        rng = np.random.default_rng([self.SEED, day_index])
        minutes, steps = self.MINUTES_PER_DAY, self.SUBSTEPS
        log_price, log_vol = self._state
        sigma_minute = self.ANNUAL_VOLATILITY / np.sqrt(365 * minutes)

        # Volatility clustering: OU process x_t = (1 - k) x_{t-1} + eta eps_t  (as an EMA of eps * eta / k)
        shocks = self.VOL_OF_VOL * rng.standard_normal(minutes)
        log_vol_path = native.ema(shocks / self.VOL_REVERSION, self.VOL_REVERSION, initial=log_vol)
        hours = np.arange(minutes) / 60.0
        session = 1.0 + 0.35 * np.cos(2 * np.pi * (hours - 17.0) / 24.0)
        minute_sigma = sigma_minute * np.exp(log_vol_path) * session

        step_returns = rng.standard_normal((minutes, steps)) * (minute_sigma / np.sqrt(steps))[:, None]
        jumps = (rng.random(minutes) < self.JUMP_PROBABILITY) * rng.normal(0, self.JUMP_SIZE, minutes)
        step_returns[:, 0] += jumps
        step_returns -= 0.5 * (minute_sigma[:, None] ** 2) / steps            # martingale in price
        path = log_price + np.cumsum(step_returns.ravel()).reshape(minutes, steps)
        opens = np.concatenate([[log_price], path[:-1, -1]])
        prices = np.exp(path)
        open_price = np.exp(opens)
        minute_return = np.abs(path[:, -1] - opens) / sigma_minute
        volume = (self.BASE_VOLUME_BTC * session * (1 + 0.6 * minute_return)
                  * np.exp(0.45 * rng.standard_normal(minutes)))
        self._state = (path[-1, -1], log_vol_path[-1])
        return {
            "open": open_price, "substeps": prices,
            "high": np.maximum(open_price, prices.max(axis=1)), "low": np.minimum(open_price, prices.min(axis=1)),
            "close": prices[:, -1], "volume": volume,
        }

    def _ensure_days(self, last_day_index: int) -> None:
        while len(self._days) <= last_day_index:
            self._days.append(self._generate_day(len(self._days)))

    def _minute_bars(self, first_minute: int, last_minute: int, now_s: float) -> BarSeries:
        """Bars for minute indices [first, last] (since the anchor); the minute containing now_s is partial."""
        first_minute = max(first_minute, 0)
        if last_minute < first_minute:
            return BarSeries.empty()
        self._ensure_days(last_minute // self.MINUTES_PER_DAY)
        columns = {name: [] for name in ("open", "high", "low", "close", "volume")}
        for day in range(first_minute // self.MINUTES_PER_DAY, last_minute // self.MINUTES_PER_DAY + 1):
            start = max(first_minute - day * self.MINUTES_PER_DAY, 0)
            stop = min(last_minute - day * self.MINUTES_PER_DAY, self.MINUTES_PER_DAY - 1) + 1
            for name in columns:
                columns[name].append(self._days[day][name][start:stop])
        bars = BarSeries(
            self.ANCHOR_EPOCH_S + 60 * np.arange(first_minute, last_minute + 1, dtype=np.int64),
            *(np.concatenate(columns[name]).copy() for name in ("open", "high", "low", "close", "volume")),
        )
        self._make_last_partial(bars, last_minute, now_s)
        return bars

    def _make_last_partial(self, bars: BarSeries, minute: int, now_s: float) -> None:
        fraction = (now_s - (self.ANCHOR_EPOCH_S + 60 * minute)) / 60.0
        if fraction >= 1.0 or len(bars) == 0:
            return
        day = self._days[minute // self.MINUTES_PER_DAY]
        row = minute % self.MINUTES_PER_DAY
        points = np.concatenate([[day["open"][row]], day["substeps"][row]])       # 9 points over the minute
        position = max(fraction, 0.0) * self.SUBSTEPS
        index = int(position)
        price = points[index] + (points[min(index + 1, self.SUBSTEPS)] - points[index]) * (position - index)
        seen = np.concatenate([points[: index + 1], [price]])
        bars.high[-1], bars.low[-1], bars.close[-1] = seen.max(), seen.min(), price
        bars.volume[-1] = day["volume"][row] * max(fraction, 0.02)

    def _minute_index(self, time_s: float) -> int:
        return int((time_s - self.ANCHOR_EPOCH_S) // 60)

    # --- MarketDataSource ---
    def history(self, days: float) -> BarSeries:
        now_s = self.now()
        last = self._minute_index(now_s)
        return self._minute_bars(last - int(days * self.MINUTES_PER_DAY) + 1, last, now_s)

    def bars_since(self, time_s: int) -> BarSeries:
        now_s = self.now()
        return self._minute_bars(self._minute_index(time_s), self._minute_index(now_s), now_s)
# --- END [2.3] ---
