# ==============================================================================
# FILE: engines/kernel/hosts.py
# OVERVIEW: Engine hosts - where an engine runs. InProcessHost runs it on its own thread inside DevOS; later
#           hosts (a subprocess, a k3s worker) take the same messages over a socket or the network.
# PURPOSE:
#   1. Actor model: one inbox; requests are handled one at a time, in the order they arrived, so an
#      engine never needs locks. start() / stop() of the engine run on that thread too.
#   2. Checks every request against the manifest (known request, required and allowed payload keys)
#      before the handler sees it; turns handler results into reply / stream messages and failures into
#      error messages (EngineError as is; anything else as "internal" with the traceback in details).
#   3. Cancel arrives out of band: a queued request is answered "cancelled" without running; a running
#      stream stops at its next item. Credit arrives out of band too: a stream with a window waits for it.
#   4. Bounded inbox (INBOX_LIMIT): a full engine answers "busy" instead of queueing forever; "_health" is
#      built into every engine.
# CONTRACT:
#   - Requires: engines.kernel (messages, engine). Standard library only.
#   - Exposes: InProcessHost(engine_class, deliver) with start(), send(message), stop(), thread_name, queue_depth;
#              INBOX_LIMIT, HEALTH_REQUEST
# NOTES:
#   - [2026-11-17] Phase 5b: watched events (Engine.watch) go through the inbox, so handlers run on the engine's thread; stop unsubscribes.
#   - [2026-11-17] Phase 5a: passes the bus's ask() to its engine (Engine.ask).
#   - [2026-11-17] Phase 3: bounded inbox (busy), stream credit (back-pressure), built-in "_health", queue_depth.
#   - [2026-11-17] Created in Phase 1 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - deliver (callable): Where the host sends everything the engine produces (the bus's _deliver).
# - START_TIMEOUT_S (float): How long start() waits for the engine's own start() (default: 30).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. HOSTS
#    1.1 imports
#    1.2 in_process_host
#    1.3 handling
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import queue
import threading
import traceback
from dataclasses import dataclass
from typing import Callable

from engines.kernel.engine import Engine, Job, Reply
from engines.kernel.messages import EngineError, Message

START_TIMEOUT_S = 30.0
INBOX_LIMIT = 1000
HEALTH_REQUEST = "_health"
# --- END [1.1] ---


# ==============================================================================
# [1.2] in_process_host
# ==============================================================================
@dataclass(frozen=True)
class _Watched:
    """An event an engine watches, waiting in its inbox with the handler that hears it."""
    message: Message
    handler: Callable[[Message], None]


class InProcessHost:
    def __init__(self, engine_class: type[Engine], deliver: Callable[[Message], None],
                 ask: Callable[..., dict] | None = None, subscribe: Callable | None = None) -> None:
        self.engine_class, self.deliver, self.ask, self.subscribe = engine_class, deliver, ask, subscribe
        self._subscriptions: list = []
        self.manifest = engine_class.MANIFEST
        self.thread_name = f"engine:{self.manifest.engine_id}"
        self.engine: Engine | None = None
        self._inbox: queue.Queue = queue.Queue(maxsize=INBOX_LIMIT)
        self._jobs: dict[str, Job] = {}
        self._jobs_lock = threading.Lock()
        self._started = threading.Event()
        self._start_error: BaseException | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=self.thread_name, daemon=True)
        self._thread.start()
        if not self._started.wait(START_TIMEOUT_S):
            raise EngineError(f"{self.manifest.title} didn't start in time", code="start_failed")
        if self._start_error is not None:
            raise EngineError(f"{self.manifest.title} couldn't start: {self._start_error}", code="start_failed",
                              details="".join(traceback.format_exception(self._start_error)))

    @property
    def queue_depth(self) -> int:
        return self._inbox.qsize()

    def send(self, message: Message) -> None:
        if message.kind in ("cancel", "credit"):
            with self._jobs_lock:
                job = self._jobs.get(message.correlation or "")
            if job is not None:
                job.cancel() if message.kind == "cancel" else job.add_credit(int(message.payload.get("items", 1)))
            return
        if message.kind == "request":
            with self._jobs_lock:
                self._jobs[message.message_id] = Job(message)
        try:
            self._inbox.put_nowait(message)
        except queue.Full:
            with self._jobs_lock:
                self._jobs.pop(message.message_id, None)
            raise EngineError(f"{self.manifest.title} is busy ({INBOX_LIMIT} requests waiting); try again shortly",
                              code="busy") from None

    def stop(self, timeout_s: float = 5.0) -> None:
        for subscription in self._subscriptions:
            subscription.unsubscribe()                    # no more watched events once stopping starts
        self._subscriptions.clear()
        with self._jobs_lock:
            for job in self._jobs.values():
                job.cancel()                              # running streams stop at their next item
        if self._thread is not None and self._thread.is_alive():
            self._inbox.put(None)                         # waits for room: stopping is never refused
            self._thread.join(timeout_s)

    def _run(self) -> None:
        try:
            self.engine = self.engine_class()
            self.engine.attach(self.deliver, self.ask, self._watch if self.subscribe else None)
            self.engine.start()
        except BaseException as error:                # reported by start(); the thread ends
            self._start_error = error
            self._started.set()
            return
        self._started.set()
        while (message := self._inbox.get()) is not None:
            if isinstance(message, _Watched):
                self._handle_event(message)
            elif message.kind == "request":
                self._handle(message)
        try:
            self.engine.stop()
        except Exception as error:
            self.engine.log("error", f"Stopping failed: {error}")
# --- END [1.2] ---

# ==============================================================================
# [1.3] handling
# ==============================================================================
    def _handle(self, request: Message) -> None:
        with self._jobs_lock:
            job = self._jobs.get(request.message_id) or Job(request)
        try:
            if request.name == HEALTH_REQUEST:            # built into every engine, answered on its own thread
                self._answer("reply", request, {"health": self.engine.health()})
                return
            spec = self.manifest.request(request.name)
            if spec is None:
                raise EngineError(f"{self.manifest.title} has no request “{request.name}”", code="unknown_request")
            missing = [key for key in spec.params if key not in request.payload]
            unknown = [key for key in request.payload if key not in spec.params + spec.optional]
            if missing or unknown:
                problems = ([f"missing {', '.join(missing)}"] if missing else []) + \
                           ([f"unknown {', '.join(unknown)}"] if unknown else [])
                raise EngineError(f"{request.topic}: {'; '.join(problems)}", code="bad_request")
            handler = self.engine.handler(request.name)
            if handler is None:
                raise EngineError(f"{request.topic} isn't implemented yet", code="not_implemented")
            if job.is_cancelled():
                raise EngineError(f"{request.topic} was cancelled", code="cancelled")
            result = handler(request.payload, job)
            if not spec.streams:
                self._answer("reply", request, result)
                return
            for item in result:
                if not job.take_credit():                 # back-pressure: waits while the receiver is full
                    break
                self._answer("stream_item", request, item)
            if hasattr(result, "close"):
                result.close()                        # runs the generator's cleanup (finally:) now
            self._answer("stream_end", request, {"cancelled": job.is_cancelled()})
        except EngineError as error:
            self._fail(request, error)
        except Exception as error:                    # a bug: never lost, never fatal to the engine
            self._fail(request, EngineError(f"{request.topic} failed: {error}", code="internal",
                                            details="".join(traceback.format_exception(error))))
        finally:
            with self._jobs_lock:
                self._jobs.pop(request.message_id, None)

    def _watch(self, pattern: str, handler: Callable[[Message], None]) -> None:
        """Engine.watch: events matching pattern join the inbox, so the handler runs on the engine's thread."""
        def arrived(message: Message) -> None:
            try:
                self._inbox.put_nowait(_Watched(message, handler))
            except queue.Full:                        # never block the engine that published it
                self.engine.log("warning", f"Missed {message.topic}: {self.manifest.title} is too busy")
        self._subscriptions.append(self.subscribe(pattern, arrived))

    def _handle_event(self, watched: "_Watched") -> None:
        try:
            watched.handler(watched.message)
        except Exception as error:                    # a bug in a handler: logged, the engine keeps running
            self.engine.log("error", f"Handling {watched.message.topic} failed: {error}\n"
                                     f"{''.join(traceback.format_exception(error))}")

    def _answer(self, kind: str, request: Message, result) -> None:
        payload, attachments = (result.payload, result.attachments) if isinstance(result, Reply) else (result or {}, {})
        self.deliver(Message(kind, request.topic, payload, attachments, correlation=request.message_id))

    def _fail(self, request: Message, error: EngineError) -> None:
        if error.code == "internal":
            self.engine.log("error", f"{error.message}\n{error.details}")
        self.deliver(Message("error", request.topic, error.to_payload(), correlation=request.message_id))
# --- END [1.3] ---
