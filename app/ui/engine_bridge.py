# ==============================================================================
# FILE: app/ui/engine_bridge.py
# OVERVIEW: How widgets talk to engines: ask, stream or watch over the bus, and get the answers on the UI
#           thread - never on an engine's thread, never after the widget is gone.
# PURPOSE:
#   1. call_engine(owner, topic, payload, on_reply, full=False): one request; on_reply(payload or the whole
#      reply Message, error) on the UI thread.
#   2. stream_engine(owner, topic, payload, on_item, on_end, window, latest): items then one end; with a window
#      the engine waits for the UI (back-pressure); latest=True shows only the newest waiting item; cancelled
#      automatically when the owner is destroyed.
#   3. watch_engine(owner, pattern, on_event): events; unsubscribed when the owner is destroyed.
#   Answers for a deleted owner are dropped. It generalises run_in_thread() for engines.
# CONTRACT:
#   - Requires: engines.runtime.get_bus, engines.kernel (Message, EngineError), PySide6, shiboken6
#   - Exposes: call_engine(), stream_engine() -> StreamHandle | None, watch_engine() -> Subscription,
#              watch_library(owner, on_change(kind)), engine_manifest(engine_id) -> dict
# NOTES:
#   - [2026-11-17] watch_library(): Library changes ("knowledge.changed") for widgets (Phase 4).
#   - [2026-11-17] Phase 3: stream_engine(window, latest) - back-pressure and latest-only delivery for displays.
#   - [2026-11-17] call_engine(full=True) hands over the whole reply Message (Table shapes carry Arrow attachments).
#   - [2026-11-17] Created in Phase 1 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - owner (QObject): The widget an answer is for; when it is deleted, its answers are dropped.
# - _Dispatcher: A QObject living on the UI thread; a queued signal carries callables onto that thread.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. BRIDGE
#    1.1 dispatcher
#    1.2 calls
# ==============================================================================

# ==============================================================================
# [1.1] dispatcher
# ==============================================================================
from __future__ import annotations

import threading
from typing import Callable

import shiboken6
from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal, Slot

from engines.kernel import EngineError, Message, StreamHandle, Subscription
from engines.runtime import get_bus


class _Dispatcher(QObject):
    run = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.moveToThread(QCoreApplication.instance().thread())
        self.run.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, work: Callable[[], None]) -> None:
        work()


_dispatcher: _Dispatcher | None = None


def _on_ui_thread(owner: QObject, work: Callable[[], None]) -> None:
    """Run work() on the UI thread soon, unless owner has been deleted by then."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = _Dispatcher()
    _dispatcher.run.emit(lambda: work() if shiboken6.isValid(owner) else None)
# --- END [1.1] ---


# ==============================================================================
# [1.2] calls
# ==============================================================================
def call_engine(owner: QObject, topic: str, payload: dict | None = None,
                on_reply: Callable[[dict | None, EngineError | None], None] | None = None,
                attachments: dict | None = None, full: bool = False) -> None:
    """Ask an engine once; on_reply(payload, None) or on_reply(None, error) runs on the UI thread.
    With full=True, on_reply gets the whole reply Message (payload + binary attachments)."""
    future = get_bus().request(topic, payload, attachments)

    def done(finished) -> None:
        try:
            message, error = finished.result(), None
        except EngineError as caught:
            message, error = None, caught
        if on_reply is not None:
            answer = message if full or message is None else message.payload
            _on_ui_thread(owner, lambda: on_reply(answer, error))
    future.add_done_callback(done)


def stream_engine(owner: QObject, topic: str, payload: dict | None,
                  on_item: Callable[[Message], None],
                  on_end: Callable[[bool, EngineError | None], None] | None = None,
                  window: int = 0, latest: bool = False) -> StreamHandle | None:
    """Stream from an engine; on_item(message) per item, then on_end(cancelled, error), all on the UI thread.
    window: the engine pauses after that many items the UI hasn't handled yet (back-pressure; each item is
    acknowledged once on_item has run). latest: only the newest waiting item is shown (displays: a fast stream
    never piles up behind a slow screen). The stream is cancelled when the owner is destroyed."""
    holder: dict = {"handle": None, "waiting": None, "skipped": 0}
    lock = threading.Lock()

    def handled(count: int) -> None:
        if holder["handle"] is not None:
            holder["handle"].ack(count)

    def deliver_latest() -> None:
        with lock:
            message, count = holder["waiting"], holder["skipped"] + 1
            holder["waiting"], holder["skipped"] = None, 0
        if message is not None:
            on_item(message)
            handled(count)

    def arrived(message) -> None:                      # engine thread
        if not latest:
            _on_ui_thread(owner, lambda: (on_item(message), handled(1)))
            return
        with lock:
            is_scheduled = holder["waiting"] is not None
            if is_scheduled:
                holder["skipped"] += 1
            holder["waiting"] = message
        if not is_scheduled:
            _on_ui_thread(owner, deliver_latest)

    handle = get_bus().stream(topic, payload, arrived,
                              lambda cancelled, error: _on_ui_thread(owner, lambda: on_end and on_end(cancelled, error)),
                              window=window)
    holder["handle"] = handle
    if handle is not None:
        owner.destroyed.connect(lambda *_: handle.cancel())
    return handle


def watch_engine(owner: QObject, pattern: str, on_event: Callable[[Message], None]) -> Subscription:
    """Engine events matching pattern ("knowledge.*") reach on_event on the UI thread while owner lives."""
    subscription = get_bus().subscribe(pattern, lambda message: _on_ui_thread(owner, lambda: on_event(message)))
    owner.destroyed.connect(lambda *_: subscription.unsubscribe())
    return subscription


def watch_library(owner: QObject, on_change: Callable[[str], None]) -> Subscription:
    """The Library changed (an item added, renamed, deleted or rewritten): on_change(kind) on the UI thread."""
    return watch_engine(owner, "knowledge.changed", lambda message: on_change(message.payload.get("kind", "")))


def engine_manifest(engine_id: str) -> dict:
    """What an engine offers, as plain data (tools, agents, docs)."""
    return get_bus().manifest(engine_id).describe()
# --- END [1.2] ---
