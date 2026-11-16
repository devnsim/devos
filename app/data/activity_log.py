# ==============================================================================
# FILE: app/data/activity_log.py
# OVERVIEW: The activity log: one place every part of DevOS announces what it is doing, in plain words.
# PURPOSE:
#   1. log.info / success / warning / error / debug(source, message): readable entries with a time,
#      level and source ("pages", "module:rf_imaging", "database" ...).
#   2. log.step(source, action): wraps a unit of work; logs "<action>: success (12 ms)" or
#      "<action>: FAILED - <error>" with the traceback in details. Nothing fails silently.
#   3. Listeners (the System Log panel, the status bar) get every entry as it happens.
#   4. Persistence: entries are written to SQLite in batches (flush()) and when the session ends;
#      the last KEPT_SESSIONS sessions are kept.
#   5. install_hooks(): uncaught exceptions (main and worker threads) become ERROR entries.
# CONTRACT:
#   - Requires: stdlib only (+ app.data.store.database.Database for persistence, passed in)
#   - Exposes: get_activity_log() -> ActivityLog; LogEntry; DEBUG, INFO, SUCCESS, WARNING, ERROR, LEVEL_NAMES
# NOTES:
#   - [2026-11-15] Created. Announce when the app acts because of the user (page created, module
#                  started, dataset loaded), not every UI twitch (tab reorder, seam drag). Use debug for
#                  chatty internals (autosaves, layout saves); the log panel hides debug by default.
#   - [2026-11-15] Warnings and errors are also echoed to stderr (run.sh's ~/.cache/devos.log).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - entry: One line of the log (LogEntry).
# - level (int): DEBUG 10 < INFO 20 < SUCCESS 25 < WARNING 30 < ERROR 40.
# - session: One run of DevOS (session_id), from start to quit.
# - source (str): Who is speaking, e.g. "pages", "module:wave_sim", "engine".
# - KEPT_SESSIONS (int): Sessions kept in the database (default: 30).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. MODEL
#    1.1 imports_and_levels
#    1.2 log_entry
# 2. ACTIVITY_LOG
#    2.1 init_and_listeners
#    2.2 logging
#    2.3 steps
#    2.4 persistence
#    2.5 hooks
#    2.6 shared_instance
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_levels
# ==============================================================================
from __future__ import annotations

import sys
import threading
import time
import traceback
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable

DEBUG, INFO, SUCCESS, WARNING, ERROR = 10, 20, 25, 30, 40
LEVEL_NAMES = {DEBUG: "debug", INFO: "info", SUCCESS: "success", WARNING: "warning", ERROR: "error"}
NAMESPACE = "activity_log"
MIGRATIONS = [
    """
    CREATE TABLE log_sessions (
        session_id  TEXT PRIMARY KEY,
        started_at  REAL NOT NULL,
        ended_at    REAL,
        entry_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE log_entries (
        entry_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id  TEXT NOT NULL,
        time        REAL NOT NULL,
        level       INTEGER NOT NULL,
        source      TEXT NOT NULL,
        message     TEXT NOT NULL,
        duration_ms REAL,
        details     TEXT
    );
    CREATE INDEX log_entries_session ON log_entries (session_id, entry_id);
    """,
]
# --- END [1.1] ---

# ==============================================================================
# [1.2] log_entry
# ==============================================================================
@dataclass
class LogEntry:
    time: float
    level: int
    source: str
    message: str
    duration_ms: float | None = None
    details: str | None = None

    @property
    def level_name(self) -> str:
        return LEVEL_NAMES.get(self.level, str(self.level))

    def as_text(self) -> str:
        stamp = time.strftime("%H:%M:%S", time.localtime(self.time)) + f".{int(self.time % 1 * 1000):03d}"
        return f"{stamp}  {self.level_name.upper():7}  {self.source:22}  {self.message}"
# --- END [1.2] ---


# ==============================================================================
# [2.1] init_and_listeners
# ==============================================================================
class ActivityLog:
    MAX_ENTRIES_IN_MEMORY = 5000
    KEPT_SESSIONS = 30

    def __init__(self) -> None:
        self.session_id = uuid.uuid4().hex
        self.started_at = time.time()
        self.entries: deque[LogEntry] = deque(maxlen=self.MAX_ENTRIES_IN_MEMORY)
        self._listeners: list[Callable[[LogEntry], None]] = []
        self._unsaved: list[LogEntry] = []
        self._lock = threading.Lock()
        self.database = None

    def add_listener(self, listener: Callable[[LogEntry], None]) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[LogEntry], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)
# --- END [2.1] ---

# ==============================================================================
# [2.2] logging
# ==============================================================================
    def log(self, level: int, source: str, message: str, duration_ms: float | None = None,
            details: str | None = None) -> LogEntry:
        entry = LogEntry(time.time(), level, source, message, duration_ms, details)
        with self._lock:
            self.entries.append(entry)
            self._unsaved.append(entry)
        if level >= WARNING:
            print(f"[DevOS] {entry.as_text()}" + (f"\n{details}" if details else ""), file=sys.stderr)
        for listener in list(self._listeners):
            try:
                listener(entry)
            except Exception:                         # a broken listener must never break logging
                traceback.print_exc()
        return entry

    def debug(self, source: str, message: str, **kwargs) -> LogEntry:
        return self.log(DEBUG, source, message, **kwargs)

    def info(self, source: str, message: str, **kwargs) -> LogEntry:
        return self.log(INFO, source, message, **kwargs)

    def success(self, source: str, message: str, **kwargs) -> LogEntry:
        return self.log(SUCCESS, source, message, **kwargs)

    def warning(self, source: str, message: str, **kwargs) -> LogEntry:
        return self.log(WARNING, source, message, **kwargs)

    def error(self, source: str, message: str, error: BaseException | None = None, **kwargs) -> LogEntry:
        if error is not None:
            kwargs.setdefault("details", "".join(traceback.format_exception(error)))
            message = f"{message} — {type(error).__name__}: {error}"
        return self.log(ERROR, source, message, **kwargs)
# --- END [2.2] ---

# ==============================================================================
# [2.3] steps
# ==============================================================================
    @contextmanager
    def step(self, source: str, action: str, reraise: bool = True, level: int = SUCCESS):
        """with log.step("module:graph", "Load dataset sales.csv"): ...
        Logs the outcome with its duration; on an exception logs it (with traceback) and re-raises
        unless reraise=False."""
        started = time.perf_counter()
        try:
            yield
        except Exception as error:
            self.error(source, f"{action}: FAILED", error, duration_ms=(time.perf_counter() - started) * 1000)
            if reraise:
                raise
        else:
            duration_ms = (time.perf_counter() - started) * 1000
            self.log(level, source, f"{action}: success ({_format_duration(duration_ms)})", duration_ms=duration_ms)
# --- END [2.3] ---

# ==============================================================================
# [2.4] persistence
# ==============================================================================
    def attach_database(self, database) -> None:
        """Start saving this session (and prune old sessions)."""
        self.database = database
        database.migrate(NAMESPACE, MIGRATIONS)
        database.execute("INSERT OR IGNORE INTO log_sessions (session_id, started_at) VALUES (?, ?)",
                         (self.session_id, self.started_at))
        old = database.fetch_all("SELECT session_id FROM log_sessions ORDER BY started_at DESC LIMIT -1 OFFSET ?",
                                 (self.KEPT_SESSIONS,))
        for row in old:
            database.execute("DELETE FROM log_entries WHERE session_id = ?", (row["session_id"],))
            database.execute("DELETE FROM log_sessions WHERE session_id = ?", (row["session_id"],))

    def flush(self) -> int:
        """Write unsaved entries to the database; returns how many."""
        if self.database is None:
            return 0
        with self._lock:
            batch, self._unsaved = self._unsaved, []
        if not batch:
            return 0
        with self.database.transaction() as connection:
            connection.executemany(
                "INSERT INTO log_entries (session_id, time, level, source, message, duration_ms, details) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(self.session_id, e.time, e.level, e.source, e.message, e.duration_ms, e.details) for e in batch],
            )
            connection.execute("UPDATE log_sessions SET entry_count = entry_count + ? WHERE session_id = ?",
                               (len(batch), self.session_id))
        return len(batch)

    def end_session(self) -> None:
        self.info("devos", "Session ended")
        self.flush()
        if self.database is not None:
            self.database.execute("UPDATE log_sessions SET ended_at = ? WHERE session_id = ?", (time.time(), self.session_id))

    def past_sessions(self, limit: int = 30) -> list[dict]:
        if self.database is None:
            return []
        rows = self.database.fetch_all(
            "SELECT * FROM log_sessions WHERE session_id != ? ORDER BY started_at DESC LIMIT ?", (self.session_id, limit))
        return [dict(row) for row in rows]

    def load_session(self, session_id: str) -> list[LogEntry]:
        rows = self.database.fetch_all(
            "SELECT time, level, source, message, duration_ms, details FROM log_entries WHERE session_id = ? "
            "ORDER BY entry_id", (session_id,))
        return [LogEntry(row["time"], row["level"], row["source"], row["message"], row["duration_ms"], row["details"])
                for row in rows]
# --- END [2.4] ---

# ==============================================================================
# [2.5] hooks
# ==============================================================================
    def install_hooks(self) -> None:
        """Uncaught exceptions anywhere become ERROR entries (then Python's default handling runs)."""
        previous_hook = sys.excepthook

        def excepthook(error_type, error, error_traceback):
            self.error("devos", "Unhandled error", error.with_traceback(error_traceback))
            previous_hook(error_type, error, error_traceback)

        def thread_hook(arguments):
            self.error(f"thread:{arguments.thread.name if arguments.thread else '?'}", "Unhandled error in a worker thread",
                       arguments.exc_value)

        sys.excepthook = excepthook
        threading.excepthook = thread_hook
# --- END [2.5] ---

# ==============================================================================
# [2.6] shared_instance
# ==============================================================================
_activity_log: ActivityLog | None = None


def get_activity_log() -> ActivityLog:
    global _activity_log
    if _activity_log is None:
        _activity_log = ActivityLog()
    return _activity_log


def _format_duration(duration_ms: float) -> str:
    return f"{duration_ms:.0f} ms" if duration_ms < 2000 else f"{duration_ms / 1000:.1f} s"
# --- END [2.6] ---
