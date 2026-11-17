# ==============================================================================
# FILE: tests/test_agents_and_records.py
# OVERVIEW: Headless tests for chatting with agents (Claude / Gemini / Ollama against a local fake server),
#           opening a record's form from any view, and the Part Design step bar.
# PURPOSE:
#   1. engine.stream_chat speaks each provider's streaming format and turns failures into helpful errors.
#   2. The Agents Chat view: streamed answer stored per agent, a failed turn returns the message to the box,
#      chats listed / started / deleted, + New agent presets, Chat with agent in the record menu.
#   3. Double-click a project on the board or timeline opens its form view; right-click offers Open form.
#   4. Part Design: steps shown as a top progress bar; a locked step says why; done steps tick.
# CONTRACT:
#   - Requires: tests/conftest.py fixtures (qapp, database, pump, wait_for), app.modules.agents,
#               app.modules.projects, app.modules.cad.workbench. No network: the fake server listens on 127.0.0.1.
#   - Exposes: pytest tests
# NOTES:
#   - [2026-11-17] Created with the Agents chat, record form requests and the Part Design step bar.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - fake provider: A local HTTP server answering like the Anthropic, Gemini and Ollama APIs (streamed).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. HELPERS
#    1.1 fake_provider
# 2. TESTS
#    2.1 engine
#    2.2 chat_view
#    2.3 record_forms
#    2.4 part_design_steps
# ==============================================================================

# ==============================================================================
# [1.1] fake_provider
# ==============================================================================
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class FakeProvider(BaseHTTPRequestHandler):
    requests: list[tuple[str, dict, dict]] = []

    def log_message(self, *_args) -> None:
        pass

    def _send(self, status: int, content_type: str, chunks: list[bytes]) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(chunk)
            self.wfile.flush()
            time.sleep(0.01)

    def do_GET(self) -> None:                                           # noqa: N802
        FakeProvider.requests.append((self.path, {key.lower(): value for key, value in self.headers.items()}, {}))
        self._send(200, "application/json", [json.dumps({"models": [{"name": "llama3.2:latest"}, {"name": "qwen3:8b"}]}).encode()])

    def do_POST(self) -> None:                                          # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        FakeProvider.requests.append((self.path, {key.lower(): value for key, value in self.headers.items()}, body))
        if self.path.startswith("/v1/messages"):
            if self.headers.get("x-api-key") != "good-key":
                error = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
                self._send(401, "application/json", [json.dumps(error).encode()])
                return
            events = [{"type": "message_start", "message": {"usage": {"input_tokens": 7}}},
                      {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hel"}},
                      {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "lo"}},
                      {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}}]
            self._send(200, "text/event-stream",
                       [f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events])
        elif ":streamGenerateContent" in self.path:
            chunks = [{"candidates": [{"content": {"parts": [{"text": "thinking", "thought": True}, {"text": "Hi "}]}}]},
                      {"candidates": [{"content": {"parts": [{"text": "there"}]}, "finishReason": "STOP"}],
                       "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3}}]
            self._send(200, "text/event-stream", [f"data: {json.dumps(chunk)}\r\n\r\n".encode() for chunk in chunks])
        elif self.path == "/api/chat":
            if body["model"] == "missing":
                self._send(404, "application/json", [json.dumps({"error": "model 'missing' not found"}).encode()])
                return
            lines = [{"message": {"role": "assistant", "content": piece}, "done": False} for piece in ("Hello", " from", " Ollama")]
            lines.append({"done": True, "done_reason": "stop", "prompt_eval_count": 4, "eval_count": 3})
            self._send(200, "application/x-ndjson", [(json.dumps(line) + "\n").encode() for line in lines])


@pytest.fixture
def fake_provider():
    FakeProvider.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
# --- END [1.1] ---


# ==============================================================================
# [2.1] engine
# ==============================================================================
def test_stream_chat_speaks_each_provider(fake_provider):
    from app.modules.agents.engine import AgentError, ChatConfig, ChatMessage, list_models, stream_chat
    history = [ChatMessage("user", "a"), ChatMessage("user", "b")]          # merged: providers want turns to alternate
    pieces: list[str] = []

    claude = stream_chat(ChatConfig("Claude", "claude-opus-5-5", "Be brief.", None, fake_provider, "good-key"), history, pieces.append)
    assert (claude.text, claude.stop_reason, claude.input_tokens, claude.output_tokens) == ("Hello", "end_turn", 7, 2)
    assert pieces == ["Hel", "lo"]
    _path, headers, body = FakeProvider.requests[-1]
    assert body["system"] == "Be brief." and body["max_tokens"] == 8192 and body["messages"] == [{"role": "user", "content": "a\n\nb"}]
    assert headers["anthropic-version"] == "2023-06-01"

    gemini = stream_chat(ChatConfig("Gemini", "gemini-2.5-pro", "Be brief.", 100, fake_provider, "k"),
                         history + [ChatMessage("assistant", "ok"), ChatMessage("user", "c")], lambda _piece: None)
    assert (gemini.text, gemini.stop_reason, gemini.output_tokens) == ("Hi there", "STOP", 3)   # thoughts left out
    path, headers, body = FakeProvider.requests[-1]
    assert path == "/v1beta/models/gemini-2.5-pro:streamGenerateContent?alt=sse" and headers["x-goog-api-key"] == "k"
    assert [item["role"] for item in body["contents"]] == ["user", "model", "user"]
    assert body["systemInstruction"]["parts"][0]["text"] == "Be brief." and body["generationConfig"]["maxOutputTokens"] == 100

    ollama = stream_chat(ChatConfig("Ollama", "llama3.2", "Be brief.", None, fake_provider), history, lambda _piece: None)
    assert ollama.text == "Hello from Ollama" and ollama.output_tokens == 3
    assert FakeProvider.requests[-1][2]["messages"][0] == {"role": "system", "content": "Be brief."}
    assert list_models("Ollama", fake_provider) == ["llama3.2:latest", "qwen3:8b"]

    stopped = stream_chat(ChatConfig("Ollama", "llama3.2", "", None, fake_provider), history, lambda _piece: None, lambda: True)
    assert stopped.was_stopped and stopped.text == ""

    with pytest.raises(AgentError, match="invalid x-api-key.*HTTP 401.*API key"):
        stream_chat(ChatConfig("Claude", "claude-opus-5-5", "", None, fake_provider, "bad-key"), history, lambda _piece: None)
    with pytest.raises(AgentError, match="No Claude API key"):
        stream_chat(ChatConfig("Claude", "claude-opus-5-5", "", None, fake_provider, ""), history, lambda _piece: None)
    with pytest.raises(AgentError, match="model 'missing' not found"):
        stream_chat(ChatConfig("Ollama", "missing", "", None, fake_provider), history, lambda _piece: None)
    with pytest.raises(AgentError, match="ollama serve"):
        stream_chat(ChatConfig("Ollama", "llama3.2", "", None, "http://127.0.0.1:9"), history, lambda _piece: None)


def test_agent_values_become_a_chat_config():
    from app.modules.agents.engine import chat_config, provider_of
    assert provider_of({"model": "claude-sonnet-5-5"}) == "Claude" and provider_of({"model": "gemini-2.5-pro"}) == "Gemini"
    assert provider_of({"model": "llama3.2"}) == "Ollama" and provider_of({}) == "Claude"
    config = chat_config("Scout", {"provider": "Gemini", "model": "claude-opus-5-5", "role": "Finds things", "max_tokens": 500})
    assert config.model == "gemini-2.5-pro"                          # another provider's model: the default instead
    assert config.system == "You are Scout. Your role: Finds things." and config.max_tokens == 500
    assert chat_config("Scout", {"instructions": "Only answer in French."}).system == "Only answer in French."
# --- END [2.1] ---


# ==============================================================================
# [2.2] chat_view
# ==============================================================================
def test_agent_chat_view(qapp, database, pump, wait_for, fake_provider, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QPushButton

    from app.modules.agents.view import OLLAMA_URL_PREFERENCE
    from app.modules.registry import create_module
    database.set_preference(OLLAMA_URL_PREFERENCE, fake_provider)
    view, _context = create_module("agents", database)
    view.resize(1300, 700)
    view.show()
    pump()

    new_btn = next(button for button in view.findChildren(QPushButton) if button.text() == "+ New agent")
    assert [action.text() for action in new_btn.menu().actions()] == ["Claude agent", "Gemini agent", "Ollama agent"]
    new_btn.menu().actions()[2].trigger()
    pump()
    agent = view.controller.selected()
    assert agent.title == "New Ollama agent" and agent.values["provider"] == "Ollama"
    chat = view.block_canvas.blocks("chat")[0]                       # in the default layout
    assert chat.agent_id == agent.record_id and chat.provider_combo.currentText() == "Ollama"
    assert wait_for(lambda: chat.model_combo.findText("qwen3:8b") >= 0, 5)      # the installed models were listed

    chat.chat_input_edit.setPlainText("What can you do?")
    chat.send()
    assert chat.send_btn.text() == "Stop"
    assert wait_for(lambda: chat.run is None, 5)
    messages = chat.store.messages(chat.conversation_id)
    assert [(m.role, m.content) for m in messages] == [("user", "What can you do?"), ("assistant", "Hello from Ollama")]
    assert chat.store.conversations(agent.record_id)[0].title == "What can you do?"
    assert "3 out tokens" in chat.chat_status_label.text()

    claude = view.controller.new_record({"provider": "Claude"}, "Claude helper")   # no key: the message comes back
    pump()
    assert chat.agent_id == claude.record_id and chat.conversation_id is None
    chat.chat_input_edit.setPlainText("hello")
    chat.send()
    assert wait_for(lambda: chat.run is None, 5)
    assert chat.chat_input_edit.toPlainText() == "hello" and "No Claude API key" in chat.chat_status_label.text()
    assert chat.store.messages(chat.store.conversations(claude.record_id)[0].conversation_id) == []

    view.controller.select(agent.record_id)                          # back: the latest chat shows again
    pump()
    first_chat = chat.conversation_id
    chat.new_chat()
    assert chat.conversation_id is None and chat.conversation_combo.currentText() == "New chat"
    chat.show_conversation(first_chat)
    menu = view.controller.record_menu(agent.record_id, view)
    assert [action.text() for action in menu.actions() if action.text()] == [
        "Open form", "Chat with agent", "Duplicate", "Delete agent…"]
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    chat.delete_chat()
    assert chat.store.conversations(agent.record_id) == [] and chat.conversation_id is None
    view.close()
# --- END [2.2] ---


# ==============================================================================
# [2.3] record_forms
# ==============================================================================
def test_double_click_and_right_click_open_a_records_form(qapp, database, pump):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from app.modules.projects.view import ProjectsModule
    module = ProjectsModule()
    module.resize(1200, 700)
    module.show()
    pump()
    canvas = module.block_canvas
    canvas.close_tile(next(tile for tile in canvas.tiles.values() if tile.kind == "form"))   # no form view open
    pump()
    first = module.controller.new_record({"status": "Active", "start": "2026-03-01", "due": "2026-03-10"}, title="Ship")
    second = module.controller.new_record({"status": "Active"}, title="Plan")
    pump()

    board = canvas.blocks("board")[0]
    column = next(column for column in board.column_lists if column.value == "Active")
    item = next(column.item(index) for index in range(column.count()) if column.item(index).text().startswith("Ship"))
    column.itemDoubleClicked.emit(item)                                # double-click a card: its form opens
    pump()
    forms = canvas.blocks("form")
    assert len(forms) == 1 and forms[0].shown_id == first.record_id
    assert canvas.active_tile.kind == "form"

    timeline = canvas.blocks("timeline")[0]                            # double-click a name on the Gantt chart
    row = next(index for index, (record, _start, _end) in enumerate(timeline.canvas.rows) if record.record_id == second.record_id)
    y = timeline.canvas.HEADER_PX + row * timeline.canvas.ROW_PX + timeline.canvas.ROW_PX // 2
    QTest.mouseDClick(timeline.canvas, Qt.MouseButton.LeftButton, pos=QPoint(timeline.canvas.visible_left() + 20, y))
    pump()
    assert len(canvas.blocks("form")) == 1 and forms[0].shown_id == second.record_id   # the same form, brought forward
    assert module.collection.get(second.record_id).values.get("start") is None      # the name column doesn't schedule

    menu = module.controller.record_menu(first.record_id, module)       # right-click in any view
    assert [action.text() for action in menu.actions() if action.text()] == ["Open form", "Duplicate", "Delete project…"]
    assert module.controller.selected_id == first.record_id
    canvas._set_active_tile(next(tile for tile in canvas.tiles.values() if tile.kind == "board"))
    menu.actions()[0].trigger()
    assert canvas.active_tile.kind == "form" and forms[0].shown_id == first.record_id
    menu.actions()[2].trigger()                                         # Duplicate
    pump()
    assert module.collection.count() == 3 and module.controller.selected().title.startswith("Ship")
    module.deleteLater()
# --- END [2.3] ---


# ==============================================================================
# [2.4] part_design_steps
# ==============================================================================
def test_part_design_shows_steps_as_a_top_bar(qapp, pump):
    from app.modules.cad.engine import Part, Shape
    from app.modules.cad.workbench import PartDesignWindow
    window = PartDesignWindow(Part("Bracket"))
    window.show()
    pump()
    bar = window.workflow_step_bar
    assert bar.states == ["current", "locked", "locked", "locked"]
    bar.step_clicked.emit(2)                                           # locked: stays, says why
    assert window.pages.currentIndex() == 0 and window.step_hint_label.text().startswith("Not yet: Draw at least one shape")

    window.part.sketches[0].shapes.append(Shape("rect", {"x": 0, "y": 0, "w": 20, "h": 10}))
    window.rebuild()
    assert bar.states == ["current", "open", "locked", "locked"]
    bar.step_clicked.emit(1)
    assert window.pages.currentIndex() == 1 and bar.states[:2] == ["done", "current"]
    window.add_feature("pad", {"sketch": window.part.sketches[0].name, "height": 5.0})
    window.rebuild()
    assert bar.states == ["done", "current", "open", "open"]
    window.is_dirty = False
    window.close()
# --- END [2.4] ---
