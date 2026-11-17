# ==============================================================================
# FILE: tests/test_engine_kernel.py
# OVERVIEW: Phase 1 of the engine plan - the kernel: messages, the bus, the in-process host (actor), the UI
#           bridge, and the Knowledge engine pilot behind the Library module.
# PURPOSE:
#   1. Messages carry plain data only (copied through JSON) plus binary attachments.
#   2. Requests are checked against the manifest, handled one at a time in order on the engine's thread;
#      failures become EngineError (internal bugs keep their traceback and reach the System Log).
#   3. Streams deliver items in order and stop on cancel (queued or running); events reach subscribers;
#      shutdown answers everything still waiting.
#   4. The UI bridge answers on the UI thread and never for a deleted widget.
#   5. The Knowledge engine reads the Library on its own connection; the Library module's views list through it.
# CONTRACT:
#   - Requires: tests/conftest.py fixtures, engines.kernel, engines.runtime, app.ui.engine_bridge,
#               app.clients.library, app.modules.library
#   - Exposes: pytest tests
# NOTES:
#   - [2026-11-17] Pilot test follows Phase 4: knowledge.item, library(), the Library module's table / document views.
#   - [2026-11-17] Created in Phase 1 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - EchoEngine: A test engine ("echo") with plain, streaming, failing and slow requests.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. HELPERS
#    1.1 echo_engine
# 2. TESTS
#    2.1 messages
#    2.2 requests
#    2.3 streams_and_events
#    2.4 ui_bridge
#    2.5 knowledge_pilot
# ==============================================================================

# ==============================================================================
# [1.1] echo_engine
# ==============================================================================
import threading
import time

import numpy as np
import pytest

from engines.kernel import Bus, Engine, EngineError, EngineManifest, Message, Reply, RequestSpec, plain_data


class EchoEngine(Engine):
    MANIFEST = EngineManifest(
        "echo", "Echo", "1", "Test engine",
        requests=(RequestSpec("echo", "Answer with the payload", optional=("value", "items")),
                  RequestSpec("record", "Remember an order number", params=("number",), effect="writes"),
                  RequestSpec("order", "Numbers in the order they were handled"),
                  RequestSpec("thread", "The handling thread's name"),
                  RequestSpec("fail", "Fail on purpose", params=("how",)),
                  RequestSpec("array", "Double an attached array"),
                  RequestSpec("count", "Count to n slowly", params=("n",), streams=True),
                  RequestSpec("shout", "Emit an event and log", params=("text",)),
                  RequestSpec("missing", "Declared but not implemented")),
        events=("shouted",))

    def start(self) -> None:
        self.numbers: list[int] = []

    def on_echo(self, payload, _job):
        payload.setdefault("items", []).append("engine")       # the caller's dict must not change
        return payload

    def on_record(self, payload, _job):
        time.sleep(0.001)
        self.numbers.append(payload["number"])
        return {}

    def on_order(self, _payload, _job):
        return {"numbers": self.numbers}

    def on_thread(self, _payload, _job):
        return {"name": threading.current_thread().name}

    def on_fail(self, payload, _job):
        if payload["how"] == "engine":
            raise EngineError("That can't be done", code="refused", details="because")
        if payload["how"] == "object":
            return {"value": object()}
        raise ValueError("a bug")

    def on_array(self, _payload, job):
        return Reply({"shape": list(job.attachments["data"].shape)}, {"doubled": job.attachments["data"] * 2})

    def on_count(self, payload, _job):
        for number in range(payload["n"]):
            time.sleep(0.01)
            yield {"number": number}

    def on_shout(self, payload, _job):
        self.emit("shouted", {"text": payload["text"].upper()})
        self.log("warning", f"shouted {payload['text']}")
        return {}


class BrokenEngine(Engine):
    MANIFEST = EngineManifest("broken", "Broken", "1", "Can't start", requests=(RequestSpec("ping", "Never answered"),))

    def start(self) -> None:
        raise RuntimeError("no disk")


@pytest.fixture
def bus():
    bus = Bus()
    bus.register(EchoEngine)
    bus.register(BrokenEngine)
    yield bus
    bus.shutdown()
# --- END [1.1] ---


# ==============================================================================
# [2.1] messages
# ==============================================================================
def test_messages_carry_plain_data_and_binary_attachments():
    payload = {"a": [1, 2], "b": {"c": "d"}}
    message = Message("request", "echo.echo", payload, {"data": np.arange(3)})
    payload["a"].append(3)
    assert message.payload == {"a": [1, 2], "b": {"c": "d"}}        # copied, not shared
    assert message.engine_id == "echo" and message.name == "echo" and message.header()["attachments"] == ["data"]
    for bad in ({"x": object()}, {"x": float("nan")}, {"x": {1, 2}}, {"x": b"bytes"}):
        with pytest.raises(EngineError, match="isn't plain data"):
            Message("request", "echo.echo", bad)
    with pytest.raises(EngineError):
        Message("request", "no_dot_topic")
    with pytest.raises(EngineError):
        Message("request", "echo.echo", {}, {"data": [1, 2]})        # lists go in the payload, not attachments
    assert plain_data({"t": (1, 2)}) == {"t": [1, 2]}
# --- END [2.1] ---


# ==============================================================================
# [2.2] requests
# ==============================================================================
def test_requests_run_in_order_on_the_engines_own_thread(bus):
    assert not bus.is_running("echo")                              # engines start on first use
    sent = {"value": 1}
    reply = bus.call("echo.echo", sent)
    assert reply == {"value": 1, "items": ["engine"]} and sent == {"value": 1}
    assert bus.call("echo.thread")["name"] == "engine:echo" != threading.current_thread().name
    futures = [bus.request("echo.record", {"number": number}) for number in range(50)]
    for future in futures:
        future.result(5)
    assert bus.call("echo.order")["numbers"] == list(range(50))
    doubled = bus.request("echo.array", attachments={"data": np.arange(4)}).result(5)
    assert doubled.payload == {"shape": [4]} and list(doubled.attachments["doubled"]) == [0, 2, 4, 6]


def test_bad_requests_and_failures_become_engine_errors(bus):
    from app.data.activity_log import ERROR, get_activity_log
    cases = [("echo.nope", {}, "unknown_request"), ("echo.record", {}, "bad_request"),
             ("echo.echo", {"typo": 1}, "bad_request"), ("echo.missing", {}, "not_implemented"),
             ("nobody.ping", {}, "unknown_engine"), ("broken.ping", {}, "start_failed"),
             ("echo.fail", {"how": "object"}, "not_plain_data"), ("echo.count", {"n": 1}, "bad_request")]
    for topic, payload, code in cases:
        with pytest.raises(EngineError) as caught:
            bus.call(topic, payload)
        assert caught.value.code == code, topic
    with pytest.raises(EngineError, match="That can't be done") as refused:
        bus.call("echo.fail", {"how": "engine"})
    assert (refused.value.code, refused.value.details) == ("refused", "because")
    bus.subscribe("*.log", lambda message: get_activity_log().log(ERROR, "test", message.payload["message"]))
    with pytest.raises(EngineError) as bug:
        bus.call("echo.fail", {"how": "bug"})
    assert bug.value.code == "internal" and "ValueError: a bug" in bug.value.details
    assert bus.call("echo.echo", {"value": 2})["value"] == 2       # the engine survived its bug
    assert any("ValueError: a bug" in entry.message for entry in get_activity_log().entries)
# --- END [2.2] ---


# ==============================================================================
# [2.3] streams_and_events
# ==============================================================================
def test_streams_deliver_in_order_and_stop_on_cancel(bus):
    items, ends = [], []
    done = threading.Event()
    bus.stream("echo.count", {"n": 5}, lambda message: items.append(message.payload["number"]),
               lambda cancelled, error: (ends.append((cancelled, error)), done.set()))
    assert done.wait(5) and items == [0, 1, 2, 3, 4] and ends == [(False, None)]

    items.clear(), ends.clear(), done.clear()
    handle = bus.stream("echo.count", {"n": 1000}, lambda message: items.append(message.payload["number"]),
                        lambda cancelled, error: (ends.append((cancelled, error)), done.set()))
    time.sleep(0.05)
    handle.cancel()
    assert done.wait(5) and ends == [(True, None)] and 0 < len(items) < 1000

    blocker = bus.request("echo.record", {"number": -1})           # keeps the actor busy ...
    queued_end = []
    queued = bus.stream("echo.count", {"n": 3}, lambda _message: None, lambda c, e: queued_end.append(e))
    queued.cancel()                                                # ... so this one is cancelled while queued
    blocker.result(5)
    deadline = time.time() + 5
    while not queued_end and time.time() < deadline:
        time.sleep(0.01)
    assert queued_end and queued_end[0].code == "cancelled"


def test_events_reach_subscribers_and_shutdown_answers_waiters(bus):
    heard = []
    subscription = bus.subscribe("echo.*", lambda message: heard.append((message.topic, message.payload)))
    bus.call("echo.shout", {"text": "hi"})
    assert ("echo.shouted", {"text": "HI"}) in heard and any(topic == "echo.log" for topic, _ in heard)
    subscription.unsubscribe()
    heard.clear()
    bus.call("echo.shout", {"text": "again"})
    assert heard == []
    bus.publish("app.something", {"x": 1})                          # app events reach "*" subscribers too
    slow = bus.stream("echo.count", {"n": 10_000}, lambda _message: None, lambda c, e: None)
    assert slow is not None
    bus.shutdown()
    with pytest.raises(EngineError) as caught:
        bus.call("echo.echo")
    assert caught.value.code == "shutdown"
# --- END [2.3] ---


# ==============================================================================
# [2.4] ui_bridge
# ==============================================================================
def test_ui_bridge_answers_on_the_ui_thread_and_never_for_deleted_widgets(qapp, wait_for):
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QWidget

    from app.ui.engine_bridge import call_engine, engine_manifest, stream_engine, watch_engine
    from engines.runtime import get_bus
    get_bus().register(EchoEngine)
    owner = QWidget()
    answers, threads, items, events = [], [], [], []
    call_engine(owner, "echo.echo", {"value": 7}, lambda reply, error: (answers.append((reply, error)),
                                                                          threads.append(QThread.currentThread())))
    assert wait_for(lambda: answers, 5)
    assert answers[0][0]["value"] == 7 and answers[0][1] is None and threads[0] is qapp.thread()
    call_engine(owner, "echo.record", {}, lambda reply, error: answers.append((reply, error)))
    assert wait_for(lambda: len(answers) == 2, 5) and answers[1][0] is None and answers[1][1].code == "bad_request"
    stream_engine(owner, "echo.count", {"n": 3}, lambda message: items.append(message.payload["number"]))
    watch_engine(owner, "echo.shouted", lambda message: events.append(message.payload["text"]))
    get_bus().call("echo.shout", {"text": "ui"})
    assert wait_for(lambda: items == [0, 1, 2] and events == ["UI"], 5)
    assert engine_manifest("echo")["requests"][0]["name"] == "echo"

    import shiboken6
    gone = QWidget()
    late = []
    call_engine(gone, "echo.echo", {}, lambda reply, error: late.append(reply))
    shiboken6.delete(gone)                                         # gone before the answer reaches the UI thread
    time.sleep(0.2)
    qapp.processEvents()
    assert late == []
    owner.deleteLater()
# --- END [2.4] ---


# ==============================================================================
# [2.5] knowledge_pilot
# ==============================================================================
def test_knowledge_engine_reads_the_library_and_the_library_module_lists_through_it(qapp, database, wait_for):
    from app.clients.library import library
    from app.modules.registry import create_module
    from engines.runtime import get_bus
    store = library()
    note = store.create_text("note", "Kernel pilot note", "# Pilot\n\nThrough the bus.\n", ".md")
    data = store.create_text("dataset", "Kernel pilot data", "\n".join(f"{n},{n * n}" for n in range(300)), ".csv")
    bus = get_bus()
    listed = bus.call("knowledge.list", {"kind": "note"})["items"]
    assert any(item["item_id"] == note.item_id and "path" not in item for item in listed)
    assert bus.call("knowledge.read_text", {"item_id": note.item_id})["text"].startswith("# Pilot")
    head = bus.call("knowledge.read_text", {"item_id": data.item_id, "max_lines": 5})
    assert head["text"].count("\n") == 4 and head["more_lines"] == 295
    assert [item["name"] for item in bus.call("knowledge.search", {"text": "kernel pilot"})["items"]] == [
        "Kernel pilot data", "Kernel pilot note"]
    with pytest.raises(EngineError) as missing:
        bus.call("knowledge.item", {"item_id": 999_999})
    assert missing.value.code == "not_found"

    view, _context = create_module("library", database)        # the Library module is views over Knowledge
    view.resize(1000, 600)
    view.show()
    table, document = view.block_canvas.blocks("table")[0], view.block_canvas.blocks("document")[0]
    names = lambda: [row["title"] for row in (table.data.rows if table.data else [])]   # noqa: E731
    assert wait_for(lambda: "Kernel pilot note" in names(), 5)
    table.select(data.item_id)
    assert wait_for(lambda: "100 more lines" in document.view.note_preview.toPlainText(), 5)   # 300 rows, 200 shown
    store.create_text("note", "Kernel pilot later", "added after", ".md")   # the views hear the change
    assert wait_for(lambda: "Kernel pilot later" in names(), 5)
    view.close()
    view.deleteLater()
# --- END [2.5] ---
