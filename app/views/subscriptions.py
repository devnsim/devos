# ==============================================================================
# FILE: app/views/subscriptions.py
# OVERVIEW: Shared subscriptions - every view asking the same question of the same source shares ONE engine
#           request and one answer; a "changed" event from that source refreshes them all at once.
# PURPOSE:
#   1. subscribe(listener, source, query): the listener gets shared_data(data, error) now (if an answer is
#      cached) and after every refresh; unsubscribe() (automatic when the listener is destroyed).
#   2. One watcher per engine on "<engine>.changed": events whose payload matches a source's keys
#      (e.g. collection) mark it stale; refreshes are coalesced (REFRESH_DELAY_MS) and never overlap.
#      "runtime.attached" / "runtime.detached" refresh that engine's questions too.
#   3. request_count: how many engine requests were made (tests prove sharing with it).
# CONTRACT:
#   - Requires: app.ui.engine_bridge (call_engine, watch_engine), app.views.api.TableData,
#               engines.kernel.query.query_key
#   - Exposes: shared_queries() -> SharedQueries with subscribe(), unsubscribe(), refresh(), request_count
#   - Source contract: an engine serving views answers "<engine>.query" {**source params, query} with a Table
#     shape and emits "<engine>.changed" with the source params of what changed.
# NOTES:
#   - [2026-11-17] Phase 3: an engine attached or detached refreshes its questions.
#   - [2026-11-17] Created in Phase 2 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - key (str): query_key(source, query) - the identity of a question.
# - listener: Any QObject with shared_data(data: TableData | None, error) (ViewHost, a module's count label).
# - REFRESH_DELAY_MS (int): Changes arriving within this window cause one refresh (default: 30).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SUBSCRIPTIONS
#    1.1 shared_queries
# ==============================================================================

# ==============================================================================
# [1.1] shared_queries
# ==============================================================================
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer

from app.ui.engine_bridge import call_engine, watch_engine
from app.views.api import TableData
from engines.kernel.query import query_key

REFRESH_DELAY_MS = 30


@dataclass
class _Entry:
    source: dict
    query: dict
    listeners: list = field(default_factory=list)
    data: TableData | None = None
    error: object = None
    in_flight: bool = False
    stale: bool = False


class SharedQueries(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.entries: dict[str, _Entry] = {}
        self.request_count = 0
        self._watched: set[str] = set()
        self._timer = QTimer(self, singleShot=True, interval=REFRESH_DELAY_MS, timeout=self._refresh_stale)
        watch_engine(self, "runtime.*", self._on_runtime)

    def _on_runtime(self, message) -> None:
        """An engine was attached or detached: ask its questions again (answers, or "not attached")."""
        engine_id = message.payload.get("engine_id")
        for entry in self.entries.values():
            if entry.source.get("engine") == engine_id:
                entry.stale = True
        self._timer.start()

    def subscribe(self, listener: QObject, source: dict, question: dict) -> str:
        key = query_key(source, question)
        entry = self.entries.setdefault(key, _Entry(dict(source), dict(question)))
        if listener not in entry.listeners:
            entry.listeners.append(listener)
            listener.destroyed.connect(lambda *_, key=key, listener=listener: self._forget(key, listener))
        self._watch(source["engine"])
        if entry.data is not None or entry.error is not None:
            QTimer.singleShot(0, lambda: self._deliver_one(key, listener))
        elif not entry.in_flight:
            self._fetch(key)
        return key

    def unsubscribe(self, listener: QObject, key: str) -> None:
        self._forget(key, listener)

    def refresh(self, key: str) -> None:
        if key in self.entries:
            self.entries[key].stale = True
            self._timer.start()

    def _forget(self, key: str, listener) -> None:
        entry = self.entries.get(key)
        if entry is not None and listener in entry.listeners:
            entry.listeners.remove(listener)
            if not entry.listeners:
                del self.entries[key]

    def _watch(self, engine: str) -> None:
        if engine not in self._watched:
            self._watched.add(engine)
            watch_engine(self, f"{engine}.changed", self._on_changed)

    def _on_changed(self, message) -> None:
        for entry in self.entries.values():
            params = {key: value for key, value in entry.source.items() if key != "engine"}
            if entry.source["engine"] == message.engine_id and \
                    all(message.payload.get(key, value) == value for key, value in params.items()):
                entry.stale = True
        self._timer.start()

    def _refresh_stale(self) -> None:
        for key, entry in list(self.entries.items()):
            if entry.stale and not entry.in_flight:
                self._fetch(key)

    def _fetch(self, key: str) -> None:
        entry = self.entries[key]
        entry.in_flight, entry.stale = True, False
        self.request_count += 1
        params = {name: value for name, value in entry.source.items() if name != "engine"}
        call_engine(self, f"{entry.source['engine']}.query", {**params, "query": entry.query},
                    lambda message, error, key=key: self._answered(key, message, error), full=True)

    def _answered(self, key: str, message, error) -> None:
        entry = self.entries.get(key)
        if entry is None:
            return
        entry.in_flight = False
        entry.data = TableData(message.payload, message.attachments) if message is not None else None
        entry.error = error
        for listener in list(entry.listeners):
            self._deliver_one(key, listener)
        if entry.stale:
            self._fetch(key)

    def _deliver_one(self, key: str, listener) -> None:
        entry = self.entries.get(key)
        if entry is not None and listener in entry.listeners:
            listener.shared_data(entry.data, entry.error)


_shared: SharedQueries | None = None


def shared_queries() -> SharedQueries:
    global _shared
    if _shared is None:
        _shared = SharedQueries()
    return _shared
# --- END [1.1] ---
