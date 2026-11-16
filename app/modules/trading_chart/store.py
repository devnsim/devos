# ==============================================================================
# FILE: app/modules/trading_chart/store.py
# OVERVIEW: Trading chart's SQLite tables: a bar cache for real feeds and a log of backtest runs.
# PURPOSE:
#   1. trading_chart_bars: OHLCV bars per (source, symbol, timeframe, time) so live/real feeds keep
#      their history between runs (the mock feed regenerates its history, so it isn't cached).
#   2. trading_chart_backtests: every backtest run with its parameters and statistics.
# CONTRACT:
#   - Requires: app.data.store.database.Database, market_data.BarSeries
#   - Exposes: TradingChartStore(database, instance_id); save_bars(); load_bars(); record_backtest();
#              recent_backtests()
# NOTES:
#   - [2026-11-15] Created. save_bars() upserts, so re-saving a bar that was partial updates it.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - source (str): Feed identifier (e.g. "mock", later an exchange name).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. STORE
#    1.1 migrations
#    1.2 bars
#    1.3 backtests
# ==============================================================================

import json
import time

import numpy as np

from app.data.store.database import Database
from app.modules.trading_chart.market_data import BarSeries

NAMESPACE = "trading_chart"

# ==============================================================================
# [1.1] migrations
# ==============================================================================
MIGRATIONS = [
    """
    CREATE TABLE trading_chart_bars (
        source    TEXT NOT NULL,
        symbol    TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        time      INTEGER NOT NULL,
        open      REAL NOT NULL,
        high      REAL NOT NULL,
        low       REAL NOT NULL,
        close     REAL NOT NULL,
        volume    REAL NOT NULL,
        PRIMARY KEY (source, symbol, timeframe, time)
    ) WITHOUT ROWID;
    CREATE TABLE trading_chart_backtests (
        backtest_id INTEGER PRIMARY KEY AUTOINCREMENT,
        instance_id TEXT,
        created_at  REAL NOT NULL,
        symbol      TEXT NOT NULL,
        timeframe   TEXT NOT NULL,
        strategy    TEXT NOT NULL,
        params_json TEXT NOT NULL,
        first_time  INTEGER NOT NULL,
        last_time   INTEGER NOT NULL,
        stats_json  TEXT NOT NULL
    );
    """,
]
# --- END [1.1] ---


class TradingChartStore:
    def __init__(self, database: Database, instance_id: str | None = None) -> None:
        self.database, self.instance_id = database, instance_id
        database.migrate(NAMESPACE, MIGRATIONS)

# ==============================================================================
# [1.2] bars
# ==============================================================================
    def save_bars(self, source: str, symbol: str, timeframe: str, bars: BarSeries) -> None:
        rows = zip([source] * len(bars), [symbol] * len(bars), [timeframe] * len(bars), bars.time.tolist(),
                   bars.open.tolist(), bars.high.tolist(), bars.low.tolist(), bars.close.tolist(), bars.volume.tolist())
        with self.database.transaction() as connection:
            connection.executemany("INSERT OR REPLACE INTO trading_chart_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    def load_bars(self, source: str, symbol: str, timeframe: str, since_time: int = 0) -> BarSeries:
        rows = self.database.fetch_all(
            "SELECT time, open, high, low, close, volume FROM trading_chart_bars "
            "WHERE source = ? AND symbol = ? AND timeframe = ? AND time >= ? ORDER BY time",
            (source, symbol, timeframe, since_time),
        )
        if not rows:
            return BarSeries.empty()
        data = np.array([tuple(row) for row in rows], float)
        return BarSeries(data[:, 0].astype(np.int64), *(data[:, column].copy() for column in range(1, 6)))
# --- END [1.2] ---

# ==============================================================================
# [1.3] backtests
# ==============================================================================
    def record_backtest(self, symbol: str, timeframe: str, strategy: str, params: dict,
                        first_time: int, last_time: int, stats: dict) -> int:
        cursor = self.database.execute(
            "INSERT INTO trading_chart_backtests (instance_id, created_at, symbol, timeframe, strategy, params_json, "
            "first_time, last_time, stats_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.instance_id, time.time(), symbol, timeframe, strategy, json.dumps(params),
             int(first_time), int(last_time), json.dumps(stats)),
        )
        return cursor.lastrowid

    def recent_backtests(self, limit: int = 20) -> list[dict]:
        rows = self.database.fetch_all(
            "SELECT * FROM trading_chart_backtests ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [{**dict(row), "params": json.loads(row["params_json"]), "stats": json.loads(row["stats_json"])}
                for row in rows]
# --- END [1.3] ---
