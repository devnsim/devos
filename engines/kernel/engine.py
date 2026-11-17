# ==============================================================================
# FILE: engines/kernel/engine.py
# OVERVIEW: Engine - the base every DevOS engine builds on: a manifest, request handlers, events, a log.
#           Hosts run an engine as an actor (one inbox, one message at a time, on its own thread).
# PURPOSE:
#   1. Subclasses set MANIFEST and write one handler per request: on_<name>(payload, job) returning a dict
#      or a Reply (dict + binary attachments); a streaming request's handler yields them instead.
#   2. start() / stop() run on the engine's own thread (open connections there: SQLite connections can't
#      cross threads); health() for the Services engine later.
#   3. emit(event, payload) publishes a declared event; log(level, text) reaches the System Log through
#      the bus ("<engine>.log" events), so engines never import the app's log.
#   4. Job: the request being handled; long handlers check job.is_cancelled() and stop early.
#   5. ask(topic, payload): ask an engine named in MANIFEST.uses and wait (never one that asks back: no cycles).
# CONTRACT:
#   - Requires: engines.kernel.messages. Standard library only.
#   - Exposes: Engine (MANIFEST, start, stop, health, emit, log, ask, handler), Job, Reply, LOG_LEVELS, ASK_TIMEOUT_S
# NOTES:
#   - [2026-11-17] Phase 5a: Engine.ask(topic, payload) - ask an engine declared in MANIFEST.uses (Data asks Knowledge for files).
#   - [2026-11-17] Phase 3: Job credit (stream back-pressure: take_credit / add_credit).
#   - [2026-11-17] Created in Phase 1 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - handler: Method on_<request name> (dots become underscores) answering one request.
# - LOG_LEVELS: Levels an engine may log at ("debug", "info", "success", "warning", "error").
# - ASK_TIMEOUT_S (float | s): Default limit for Engine.ask (default: 30; shorter than a request's own 60 s).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. ENGINE
#    1.1 imports_and_types
#    1.2 engine
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_types
# ==============================================================================
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from engines.kernel.messages import EngineError, EngineManifest, Message

LOG_LEVELS = ("debug", "info", "success", "warning", "error")
ASK_TIMEOUT_S = 30.0


@dataclass
class Reply:
    """A handler's answer when it carries binary attachments (otherwise just return the dict)."""
    payload: dict = field(default_factory=dict)
    attachments: dict[str, Any] = field(default_factory=dict)


class Job:
    """One request while it is queued or being handled. A stream with a window waits for credit."""

    def __init__(self, request: Message) -> None:
        self.request = request
        self.attachments = request.attachments
        self._cancelled = threading.Event()
        self._credit = request.window
        self._credit_changed = threading.Condition()

    def cancel(self) -> None:
        self._cancelled.set()
        with self._credit_changed:
            self._credit_changed.notify_all()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def add_credit(self, items: int) -> None:
        with self._credit_changed:
            self._credit += items
            self._credit_changed.notify_all()

    def take_credit(self) -> bool:
        """Before sending a stream item: wait until the receiver can take one (no window = never waits).
        False when the job was cancelled while waiting."""
        if not self.request.window:
            return not self.is_cancelled()
        with self._credit_changed:
            while self._credit <= 0 and not self.is_cancelled():
                self._credit_changed.wait(0.2)
            if self.is_cancelled():
                return False
            self._credit -= 1
            return True
# --- END [1.1] ---


# ==============================================================================
# [1.2] engine
# ==============================================================================
class Engine:
    MANIFEST: EngineManifest

    def __init__(self) -> None:
        self._publish: Callable[[Message], None] | None = None
        self._ask: Callable[..., dict] | None = None

    def attach(self, publish: Callable[[Message], None], ask: Callable[..., dict] | None = None) -> None:
        """Called by the host: where this engine's events go, and how it asks other engines."""
        self._publish = publish
        self._ask = ask

    def ask(self, topic: str, payload: dict | None = None, attachments: dict | None = None,
            timeout_s: float = ASK_TIMEOUT_S) -> dict:
        """Ask another engine (one declared in MANIFEST.uses) and wait for its answer's payload. Raises
        EngineError: its own failures, "undeclared_use", or "not_attached" when it isn't there - so callers
        can carry on without it."""
        target = topic.split(".", 1)[0]
        if target not in self.MANIFEST.uses or target == self.MANIFEST.engine_id:
            raise EngineError(f"{self.MANIFEST.title} doesn't declare that it uses “{target}”", code="undeclared_use")
        if self._ask is None:
            raise EngineError(f"{target} isn't reachable from here", code="not_attached")
        return self._ask(topic, payload or {}, attachments, timeout_s)

    def start(self) -> None:
        """Hook: open stores and connections (runs on the engine's thread)."""

    def stop(self) -> None:
        """Hook: close what start() opened (runs on the engine's thread)."""

    def health(self) -> dict:
        return {"ok": True}

    def handler(self, request_name: str) -> Callable | None:
        return getattr(self, "on_" + request_name.replace(".", "_"), None)

    def emit(self, event: str, payload: dict | None = None, attachments: dict | None = None) -> None:
        """Publish one of this engine's declared events (or "log")."""
        if event != "log" and event not in self.MANIFEST.events:
            raise EngineError(f"{self.MANIFEST.engine_id} declares no event “{event}”", code="undeclared_event")
        if self._publish is not None:
            self._publish(Message("event", f"{self.MANIFEST.engine_id}.{event}", payload or {}, attachments or {}))

    def log(self, level: str, text: str) -> None:
        self.emit("log", {"level": level if level in LOG_LEVELS else "info", "message": text})
# --- END [1.2] ---
