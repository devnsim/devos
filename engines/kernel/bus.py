# ==============================================================================
# FILE: engines/kernel/bus.py
# OVERVIEW: The bus - how everything talks to engines: requests (one answer), streams (many answers, can be
#           cancelled, with back-pressure) and events (publish / subscribe). It starts each engine's host on
#           first use, detaches engines, enforces timeouts, and counts what happens for the bus inspector.
# PURPOSE:
#   1. register(engine_class) / detach(engine_id, title): which engines are reachable. A request to a detached
#      engine fails with "not_attached" ("<Title> isn't attached - attach it in Settings → Engines").
#   2. request(topic, payload) -> Future (reply Message or EngineError); call() is the blocking form.
#      Every request has a deadline (the request's timeout_s, else REQUEST_TIMEOUT_S): late answers become a
#      "timeout" error and the engine is told to cancel.
#   3. stream(topic, payload, on_item, on_end, window) -> handle with cancel() and ack(): with a window, the
#      engine pauses after `window` unacknowledged items (credit-based back-pressure).
#   4. subscribe(pattern, callback) for events; publish() for events that don't come from an engine.
#   5. stats() per engine (requests, errors, timeouts, latency, queue depth, running); add_tap(callback) sees
#      every message (the bus inspector); health(engine_id).
#   Callbacks run on the engine's thread; the UI uses app/ui/engine_bridge.py.
# CONTRACT:
#   - Requires: engines.kernel (messages, engine, hosts). Standard library only.
#   - Exposes: Bus(host_class=InProcessHost) with register(), detach(), is_attached(), engine_ids(), manifest(),
#              manifests(), is_running(), request(), call(), stream(), subscribe(), publish(), stats(), health(),
#              add_tap(), remove_tap(), shutdown(); StreamHandle (cancel, ack); Subscription; REQUEST_TIMEOUT_S
# NOTES:
#   - [2026-11-17] Phase 3: detach / not_attached, request timeouts (watchdog), stream windows + credit, per-engine
#                  stats, taps for the bus inspector, health(). Rewritten around one _Pending record per request.
#   - [2026-11-17] Created in Phase 1 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - pending: A request still waiting for its reply, or a stream still open (keyed by request id).
# - pattern (str): fnmatch pattern over topics ("knowledge.*", "*.log", "*").
# - REQUEST_TIMEOUT_S (float): Default deadline of a request (60 s); streams have none unless their RequestSpec
#   sets timeout_s.
# - tap: A callback that sees a summary of every message (time, kind, topic, latency) - only while one exists.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. BUS
#    1.1 imports_and_handles
#    1.2 engines
#    1.3 requests_and_streams
#    1.4 events_and_delivery
#    1.5 watchdog_stats_taps
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_handles
# ==============================================================================
from __future__ import annotations

import fnmatch
import sys
import threading
import time
import traceback
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Callable

from engines.kernel.engine import Engine
from engines.kernel.hosts import HEALTH_REQUEST, InProcessHost
from engines.kernel.messages import EngineError, EngineManifest, Message

REQUEST_TIMEOUT_S = 60.0
WATCHDOG_INTERVAL_S = 0.25


@dataclass
class _Pending:
    request: Message | None = None
    future: Future | None = None
    on_item: Callable[[Message], None] | None = None
    on_end: Callable[[bool, EngineError | None], None] | None = None
    started: float = field(default_factory=time.perf_counter)
    deadline: float | None = None


class StreamHandle:
    def __init__(self, bus: "Bus", request: Message) -> None:
        self._bus, self.request = bus, request

    def cancel(self) -> None:
        self._bus._send_control("cancel", self.request)

    def ack(self, items: int = 1) -> None:
        """The receiver handled `items` more items (only matters when the stream has a window)."""
        if self.request.window:
            self._bus._send_control("credit", self.request, {"items": items})


class Subscription:
    def __init__(self, bus: "Bus", pattern: str, callback: Callable[[Message], None]) -> None:
        self._bus, self.pattern, self.callback = bus, pattern, callback

    def unsubscribe(self) -> None:
        self._bus._unsubscribe(self)


@dataclass
class _Stats:
    requests: int = 0
    errors: int = 0
    timeouts: int = 0
    total_ms: float = 0.0
    last_ms: float = 0.0
    max_ms: float = 0.0
# --- END [1.1] ---


# ==============================================================================
# [1.2] engines
# ==============================================================================
class Bus:
    def __init__(self, host_class: type = InProcessHost) -> None:
        self.host_class = host_class
        self._engines: dict[str, type[Engine]] = {}
        self._detached: dict[str, str] = {}              # engine id -> title, for a helpful error
        self._hosts: dict[str, object] = {}
        self._starting: dict[str, threading.Lock] = {}
        self._pending: dict[str, _Pending] = {}
        self._subscriptions: list[Subscription] = []
        self._taps: list[Callable[[dict], None]] = []
        self._stats: dict[str, _Stats] = {}
        self._lock = threading.RLock()
        self._is_shut_down = False
        self._watchdog: threading.Thread | None = None

    def register(self, engine_class: type[Engine]) -> None:
        engine_id = engine_class.MANIFEST.engine_id
        with self._lock:
            if engine_id in self._engines and self._engines[engine_id] is not engine_class:
                raise EngineError(f"Another engine is already registered as “{engine_id}”", code="duplicate_engine")
            self._engines[engine_id] = engine_class
            self._detached.pop(engine_id, None)

    def detach(self, engine_id: str, title: str = "") -> None:
        """Stop and forget an engine; its waiting requests fail with "detached". Its data is untouched."""
        with self._lock:
            engine_class = self._engines.pop(engine_id, None)
            host = self._hosts.pop(engine_id, None)
            self._detached[engine_id] = title or (engine_class.MANIFEST.title if engine_class else engine_id)
            waiting = [(key, pending) for key, pending in self._pending.items()
                       if pending.request is not None and pending.request.engine_id == engine_id]
            for key, _pending in waiting:
                del self._pending[key]
        if host is not None:
            host.stop()
        for _key, pending in waiting:
            self._finish(pending, error=EngineError(f"{self._detached[engine_id]} was detached", code="detached"))

    def is_attached(self, engine_id: str) -> bool:
        return engine_id in self._engines

    def engine_ids(self) -> list[str]:
        return sorted(self._engines)

    def manifest(self, engine_id: str) -> EngineManifest:
        if engine_id in self._detached:
            raise EngineError(f"{self._detached[engine_id]} isn't attached – attach it in Settings → Engines",
                              code="not_attached")
        if engine_id not in self._engines:
            raise EngineError(f"No engine called “{engine_id}”", code="unknown_engine")
        return self._engines[engine_id].MANIFEST

    def manifests(self) -> list[EngineManifest]:
        """Every attached engine's manifest (capability queries)."""
        with self._lock:
            return [engine_class.MANIFEST for engine_class in self._engines.values()]

    def is_running(self, engine_id: str) -> bool:
        return engine_id in self._hosts

    def _host(self, engine_id: str):
        with self._lock:
            if self._is_shut_down:
                raise EngineError("DevOS is shutting down", code="shutdown")
            if engine_id in self._hosts:
                return self._hosts[engine_id]
            self.manifest(engine_id)                  # detached / unknown engine -> EngineError
            starting = self._starting.setdefault(engine_id, threading.Lock())
        with starting:                                # not the bus lock: a starting engine may already log
            with self._lock:
                if engine_id in self._hosts:
                    return self._hosts[engine_id]
                engine_class = self._engines.get(engine_id)
            if engine_class is None:
                self.manifest(engine_id)
            host = self.host_class(engine_class, self._deliver)
            host.start()                              # raises EngineError when the engine can't start
            with self._lock:
                self._hosts[engine_id] = host
            return host

    def shutdown(self) -> None:
        """Stop every engine; anything still waiting gets a "shutdown" error."""
        with self._lock:
            self._is_shut_down = True
            hosts, self._hosts = list(self._hosts.values()), {}
            pending, self._pending = list(self._pending.values()), {}
        for host in hosts:
            host.stop()
        for waiting in pending:
            self._finish(waiting, error=EngineError("DevOS is shutting down", code="shutdown"))
# --- END [1.2] ---

# ==============================================================================
# [1.3] requests_and_streams
# ==============================================================================
    def _send_request(self, topic: str, payload: dict | None, attachments: dict | None, pending: _Pending,
                      wants_stream: bool, timeout_s: float | None = None, window: int = 0) -> Message | None:
        try:
            request = Message("request", topic, payload or {}, attachments or {}, window=window if wants_stream else 0)
            pending.request = request
            spec = self.manifest(request.engine_id).request(request.name)
            if spec is not None and spec.streams != wants_stream:
                raise EngineError(f"{topic} {'streams: use stream()' if spec.streams else 'answers once: use request()'}",
                                  code="bad_request")
            limit = timeout_s if timeout_s is not None else (spec.timeout_s if spec and spec.timeout_s else
                                                             None if wants_stream else REQUEST_TIMEOUT_S)
            pending.deadline = time.perf_counter() + limit if limit else None
            host = self._host(request.engine_id)
            with self._lock:
                self._pending[request.message_id] = pending
                self._ensure_watchdog()
            self._tap(request)
            host.send(request)
            return request
        except EngineError as error:
            with self._lock:
                self._pending.pop(pending.request.message_id if pending.request else "", None)
            self._finish(pending, error=error)
            return None

    def request(self, topic: str, payload: dict | None = None, attachments: dict | None = None,
                timeout_s: float | None = None) -> Future:
        """Ask an engine once. The Future resolves to the reply Message or raises EngineError."""
        future: Future = Future()
        self._send_request(topic, payload, attachments, _Pending(future=future), False, timeout_s)
        return future

    def call(self, topic: str, payload: dict | None = None, timeout_s: float | None = None) -> dict:
        """Blocking request for scripts and tests: the reply payload (EngineError on failure). Without timeout_s
        the request's own timeout_s, else REQUEST_TIMEOUT_S, applies (the bus's watchdog ends the wait)."""
        return self.request(topic, payload, timeout_s=timeout_s).result().payload

    def health(self, engine_id: str, timeout_s: float = 5.0) -> dict:
        return self.call(f"{engine_id}.{HEALTH_REQUEST}", timeout_s=timeout_s)["health"]

    def stream(self, topic: str, payload: dict | None, on_item: Callable[[Message], None],
               on_end: Callable[[bool, EngineError | None], None], window: int = 0) -> StreamHandle | None:
        """Items arrive through on_item; then on_end(cancelled, error) exactly once. With a window, the engine
        pauses after `window` items until the receiver ack()s them."""
        request = self._send_request(topic, payload, None, _Pending(on_item=on_item, on_end=on_end), True, window=window)
        return StreamHandle(self, request) if request is not None else None

    def _send_control(self, kind: str, request: Message, payload: dict | None = None) -> None:
        with self._lock:
            host = self._hosts.get(request.engine_id)
        if host is not None:
            host.send(Message(kind, request.topic, payload or {}, correlation=request.message_id))
# --- END [1.3] ---

# ==============================================================================
# [1.4] events_and_delivery
# ==============================================================================
    def subscribe(self, pattern: str, callback: Callable[[Message], None]) -> Subscription:
        subscription = Subscription(self, pattern, callback)
        with self._lock:
            self._subscriptions.append(subscription)
        return subscription

    def _unsubscribe(self, subscription: Subscription) -> None:
        with self._lock:
            if subscription in self._subscriptions:
                self._subscriptions.remove(subscription)

    def publish(self, topic: str, payload: dict | None = None) -> None:
        """An event that doesn't come from an engine (the runtime announcing an attach ...)."""
        self._deliver(Message("event", topic, payload or {}))

    def _deliver(self, message: Message) -> None:
        """Everything engines produce arrives here (on the engine's thread)."""
        if message.kind == "event":
            self._tap(message)
            with self._lock:
                listeners = [sub.callback for sub in self._subscriptions if fnmatch.fnmatchcase(message.topic, sub.pattern)]
            for callback in listeners:
                self._safely(callback, message)
            return
        with self._lock:
            pending = self._pending.get(message.correlation or "")
            if pending is not None and message.kind in ("reply", "error", "stream_end"):
                del self._pending[message.correlation]
        if pending is None:
            return                                    # answered after a timeout, a detach or shutdown
        self._tap(message, pending)
        if message.kind == "stream_item" and pending.on_item is not None:
            self._safely(pending.on_item, message)
        elif message.kind == "reply":
            self._finish(pending, message=message)
        elif message.kind == "error":
            self._finish(pending, error=EngineError.from_payload(message.payload))
        elif message.kind == "stream_end":
            self._finish(pending, cancelled=bool(message.payload.get("cancelled")))

    def _finish(self, pending: _Pending, message: Message | None = None, error: EngineError | None = None,
                cancelled: bool = False) -> None:
        self._count(pending, error)
        if pending.future is not None:
            if error is not None:
                pending.future.set_exception(error)
            else:
                pending.future.set_result(message)
        elif pending.on_end is not None:
            self._safely(pending.on_end, cancelled, error)

    @staticmethod
    def _safely(callback: Callable, *arguments) -> None:
        """A listener's bug must not break the engine that delivered to it."""
        try:
            callback(*arguments)
        except Exception:                             # printed (run.sh keeps stderr in ~/.cache/devos.log)
            traceback.print_exc(file=sys.stderr)
# --- END [1.4] ---

# ==============================================================================
# [1.5] watchdog_stats_taps
# ==============================================================================
    def _ensure_watchdog(self) -> None:
        if self._watchdog is None or not self._watchdog.is_alive():
            self._watchdog = threading.Thread(target=self._watch_deadlines, name="bus:watchdog", daemon=True)
            self._watchdog.start()

    def _watch_deadlines(self) -> None:
        while not self._is_shut_down:
            time.sleep(WATCHDOG_INTERVAL_S)
            now = time.perf_counter()
            with self._lock:
                late = [(key, pending) for key, pending in self._pending.items()
                        if pending.deadline is not None and now > pending.deadline]
                for key, _pending in late:
                    del self._pending[key]
            for _key, pending in late:
                request = pending.request
                self._send_control("cancel", request)
                with self._lock:
                    self._stats.setdefault(request.engine_id, _Stats()).timeouts += 1
                waited = now - pending.started
                self._finish(pending, error=EngineError(f"{request.topic} didn't answer within {waited:.0f} s",
                                                        code="timeout"))

    def _count(self, pending: _Pending, error: EngineError | None) -> None:
        if pending.request is None or pending.request.kind != "request":
            return
        elapsed_ms = (time.perf_counter() - pending.started) * 1000
        with self._lock:
            stats = self._stats.setdefault(pending.request.engine_id, _Stats())
            stats.requests += 1
            stats.errors += error is not None
            stats.total_ms += elapsed_ms
            stats.last_ms = elapsed_ms
            stats.max_ms = max(stats.max_ms, elapsed_ms)

    def stats(self) -> dict[str, dict]:
        """Per engine (attached or seen): requests, errors, timeouts, average / last / max latency (ms), queue
        depth, whether it is running or attached."""
        with self._lock:
            names = set(self._engines) | set(self._stats) | set(self._detached)
            result = {}
            for engine_id in sorted(names):
                stats = self._stats.get(engine_id, _Stats())
                host = self._hosts.get(engine_id)
                result[engine_id] = {"requests": stats.requests, "errors": stats.errors, "timeouts": stats.timeouts,
                                     "average_ms": stats.total_ms / stats.requests if stats.requests else 0.0,
                                     "last_ms": stats.last_ms, "max_ms": stats.max_ms,
                                     "queue_depth": getattr(host, "queue_depth", 0) if host else 0,
                                     "running": host is not None, "attached": engine_id in self._engines}
            return result

    def add_tap(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            self._taps.append(callback)

    def remove_tap(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            if callback in self._taps:
                self._taps.remove(callback)

    def _tap(self, message: Message, pending: _Pending | None = None) -> None:
        if not self._taps:
            return
        record = {"time": time.time(), "kind": message.kind, "topic": message.topic,
                  "id": message.correlation or message.message_id,
                  "ms": (time.perf_counter() - pending.started) * 1000 if pending is not None else None,
                  "error": message.payload.get("message") if message.kind == "error" else None}
        for callback in list(self._taps):
            self._safely(callback, record)
# --- END [1.5] ---
