# ==============================================================================
# FILE: engines/kernel/messages.py
# OVERVIEW: The only things that cross between engines and the rest of DevOS: messages (plain data plus
#           binary attachments), engine manifests (what an engine offers), and EngineError.
# PURPOSE:
#   1. Message: kind, topic ("knowledge.list"), JSON payload, named binary attachments, ids. Payloads are
#      always checked and copied through JSON (plain_data()), so nothing that couldn't travel to another
#      process or machine ever sneaks in, and no object is shared between an engine and the UI.
#   2. RequestSpec / EngineManifest: an engine's declared requests (with their effect: none, writes,
#      external, spends), events, shapes and the data it owns.
#   3. EngineError: a failure with a message a user can act on (code + details for the log).
# CONTRACT:
#   - Requires: standard library only. No Qt, no app imports.
#   - Exposes: Message, MESSAGE_KINDS, EFFECTS, RequestSpec, EngineManifest, EngineError, plain_data(value),
#              new_id(), WIRE_VERSION
# NOTES:
#   - [2026-11-17] Phase 3: "credit" message kind (stream back-pressure); RequestSpec shape, example, timeout_s.
#   - [2026-11-17] Created in Phase 1 of the engine plan (docs/architecture/engine-plan.md).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - topic (str): "<engine id>.<name>" - a request ("knowledge.list") or an event ("knowledge.changed").
# - correlation (str): The request id a reply / stream item / error / cancel / credit belongs to.
# - credit: The receiver of a stream saying "I can take N more items" (back-pressure, Phase 3).
# - attachment: Named binary data next to the payload (bytes, or anything with __array_interface__, e.g. a
#   NumPy array); never JSON-encoded, so big arrays travel cheaply.
# - effect (str): What a request does to the world: "none" (reads), "writes" (changes data), "external"
#   (talks to the outside world), "spends" (costs money). Agents' permissions follow it (Phase 10).
# - WIRE_VERSION (int): Version of the message format (bump when a field's meaning changes).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. MESSAGES
#    1.1 imports_and_constants
#    1.2 engine_error
#    1.3 message
#    1.4 manifest
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_constants
# ==============================================================================
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

WIRE_VERSION = 1
MESSAGE_KINDS = ("event", "request", "reply", "stream_item", "stream_end", "error", "cancel", "credit")
EFFECTS = ("none", "writes", "external", "spends")


def new_id() -> str:
    return uuid.uuid4().hex
# --- END [1.1] ---


# ==============================================================================
# [1.2] engine_error
# ==============================================================================
class EngineError(Exception):
    """An engine couldn't do what was asked. `message` is for the user; `code` for code; `details` for the log."""

    def __init__(self, message: str, code: str = "failed", details: str = "") -> None:
        super().__init__(message)
        self.message, self.code, self.details = message, code, details

    def to_payload(self) -> dict:
        return {"message": self.message, "code": self.code, "details": self.details}

    @staticmethod
    def from_payload(payload: dict) -> "EngineError":
        return EngineError(str(payload.get("message", "Engine error")), str(payload.get("code", "failed")),
                           str(payload.get("details", "")))


def plain_data(value: Any, what: str = "payload") -> Any:
    """A deep copy of `value` through JSON; EngineError("not plain data") if it can't travel as JSON
    (objects, NaN, sets, bytes - binary data goes in attachments)."""
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise EngineError(f"The {what} isn't plain data: {error}", code="not_plain_data") from None
# --- END [1.2] ---


# ==============================================================================
# [1.3] message
# ==============================================================================
def _is_binary(value: Any) -> bool:
    return isinstance(value, (bytes, bytearray, memoryview)) or hasattr(value, "__array_interface__")


@dataclass
class Message:
    kind: str
    topic: str
    payload: dict = field(default_factory=dict)
    attachments: dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=new_id)
    correlation: str | None = None
    version: int = WIRE_VERSION
    window: int = 0                             # stream requests: items in flight before the sender waits (0 = no limit)

    def __post_init__(self) -> None:
        if self.kind not in MESSAGE_KINDS:
            raise EngineError(f"Unknown message kind “{self.kind}”", code="bad_message")
        if "." not in self.topic:
            raise EngineError(f"A topic is “engine.name”, not “{self.topic}”", code="bad_message")
        self.payload = plain_data(self.payload or {}, f"{self.topic} payload")
        if not isinstance(self.payload, dict):
            raise EngineError(f"The {self.topic} payload must be an object", code="bad_message")
        for name, value in self.attachments.items():
            if not isinstance(name, str) or not _is_binary(value):
                raise EngineError(f"Attachment “{name}” of {self.topic} must be bytes or an array", code="bad_message")

    @property
    def engine_id(self) -> str:
        return self.topic.split(".", 1)[0]

    @property
    def name(self) -> str:
        return self.topic.split(".", 1)[1]

    def header(self) -> dict:
        """Everything but the attachments' bytes: what a socket or NATS transport sends as JSON (Phase 13)."""
        return {"kind": self.kind, "topic": self.topic, "payload": self.payload, "message_id": self.message_id,
                "correlation": self.correlation, "version": self.version, "window": self.window,
                "attachments": sorted(self.attachments)}
# --- END [1.3] ---


# ==============================================================================
# [1.4] manifest
# ==============================================================================
@dataclass(frozen=True)
class RequestSpec:
    name: str
    summary: str
    effect: str = "none"
    params: tuple[str, ...] = ()                # required payload keys
    optional: tuple[str, ...] = ()              # accepted payload keys
    streams: bool = False                       # answers with stream items instead of one reply
    shape: str = ""                             # the data shape the answer carries ("table" ...), checked by the kit
    example: dict | None = None                 # a payload the conformance kit may send (read-only requests)
    timeout_s: float = 0.0                      # longest wait for an answer (0 = the bus default)

    def __post_init__(self) -> None:
        if self.effect not in EFFECTS:
            raise ValueError(f"effect must be one of {EFFECTS}")


@dataclass(frozen=True)
class EngineManifest:
    engine_id: str
    title: str
    version: str
    summary: str
    requests: tuple[RequestSpec, ...] = ()
    events: tuple[str, ...] = ()
    shapes: tuple[str, ...] = ()
    owns: tuple[str, ...] = ()                  # tables / folders only this engine touches
    placement: str = "local"                    # "local" or "remote-capable"

    def request(self, name: str) -> RequestSpec | None:
        return next((spec for spec in self.requests if spec.name == name), None)

    def describe(self) -> dict:
        """The manifest as plain data (for tools, agents and the docs)."""
        return plain_data({"engine_id": self.engine_id, "title": self.title, "version": self.version,
                           "summary": self.summary, "events": list(self.events), "shapes": list(self.shapes),
                           "owns": list(self.owns), "placement": self.placement,
                           "requests": [{"name": spec.name, "summary": spec.summary, "effect": spec.effect,
                                         "params": list(spec.params), "optional": list(spec.optional),
                                         "streams": spec.streams, "shape": spec.shape} for spec in self.requests]})
# --- END [1.4] ---
