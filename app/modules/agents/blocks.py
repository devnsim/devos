# ==============================================================================
# FILE: app/modules/agents/blocks.py
# OVERVIEW: The Agents module's Chat view - talk to the selected agent (Claude, Gemini or Ollama); answers
#           stream in; every agent keeps its own list of chats.
# PURPOSE:
#   1. ChatBlock: follows the module's selected agent; its provider and model can be switched in the header
#      (written back to the agent); conversations listed, started, renamed, saved to the Library, deleted.
#   2. Sending runs off the UI thread (run_in_thread with progress); Stop keeps the answer so far; a failed
#      turn puts your message back in the box and says what went wrong (missing key, Ollama not running).
# CONTRACT:
#   - Requires: app.modules.agents (engine, store), app.ui.record_views.RecordsController (module.controller),
#               app.ui.ui_tools, app.data.store.content, QSS selectors #chat_messages_panel,
#               #chat_message_panel[role], #chat_caption_label, #chat_message_label, #chat_input_edit,
#               #chat_agent_label
#   - Exposes: ChatBlock(module) (module: controller, context, base_urls()) with reload_models(); ChatInput;
#              forget_listed_models()
# NOTES:
#   - [2026-11-17] Created: chat with agents directly in the Agents module.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - run: The turn being answered right now (stop flag, the answer so far, its conversation).
# - REDRAW_MS (int | ms): The streamed answer is redrawn at most this often (Markdown layout is not free).
# - _model_cache: Models a provider listed, per (provider, endpoint), shared by every chat view.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. CHAT
#    1.1 imports
#    1.2 chat_input
#    1.3 chat_block
#    1.4 header
#    1.5 messages
#    1.6 sending
#    1.7 conversation_actions
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from app.data.activity_log import get_activity_log
from app.data.store.content import get_content_store
from app.modules.agents.engine import (
    DEFAULT_MODELS, KNOWN_MODELS, PROVIDERS, AgentError, ChatMessage, api_key, chat_config, list_models, model_of,
    provider_of, stream_chat,
)
from app.modules.agents.store import conversation_title, get_chat_store
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.ui_tools import add_button, block_layout, run_in_thread, set_status, status_label

REDRAW_MS = 50
_model_cache: dict[tuple[str, str], list[str]] = {}


def forget_listed_models() -> None:
    """New keys or addresses: every chat view lists the providers' models again."""
    _model_cache.clear()
# --- END [1.1] ---


# ==============================================================================
# [1.2] chat_input
# ==============================================================================
class ChatInput(QPlainTextEdit):
    """Message box: Enter sends, Shift+Enter starts a new line."""

    submitted = Signal()

    def keyPressEvent(self, event) -> None:                              # noqa: N802
        is_enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if is_enter and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
            return
        super().keyPressEvent(event)
# --- END [1.2] ---


# ==============================================================================
# [1.3] chat_block
# ==============================================================================
class ChatBlock(QWidget):
    def __init__(self, module) -> None:
        super().__init__()
        self.module, self.controller = module, module.controller
        self.store = get_chat_store(module.context.database if module.context is not None else None)
        self.log = get_activity_log()
        self.agent_id: int | None = None
        self.conversation_id: int | None = None
        self.run: dict | None = None
        self._listing: set[tuple[str, str]] = set()

        layout, row = block_layout(self)
        self.chat_agent_label = QLabel(objectName="chat_agent_label")
        row.addWidget(self.chat_agent_label)
        self.provider_combo = QComboBox(toolTip="Who runs this agent's model (saved with the agent)")
        self.provider_combo.addItems(PROVIDERS)
        self.provider_combo.activated.connect(self._on_provider_chosen)
        row.addWidget(self.provider_combo)
        self.model_combo = QComboBox(editable=True, toolTip="The agent's model (saved with the agent); type any model id")
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_combo.setMinimumContentsLength(12)
        self.model_combo.activated.connect(self._on_model_chosen)
        self.model_combo.lineEdit().editingFinished.connect(self._on_model_chosen)
        row.addWidget(self.model_combo, 1)
        reserve_controls_space(row)
        chats_row = QHBoxLayout()                     # second row: which chat
        self.conversation_combo = QComboBox(toolTip="Your chats with this agent (newest first)")
        self.conversation_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.conversation_combo.setMinimumContentsLength(12)
        self.conversation_combo.activated.connect(lambda index: self.show_conversation(self.conversation_combo.itemData(index)))
        chats_row.addWidget(self.conversation_combo, 1)
        add_button(chats_row, "New chat", self.new_chat, "Start a fresh conversation (earlier ones stay in the list)")
        self.more_btn = QToolButton(text="⋯", objectName="menu_glyph_btn", toolTip="Rename, save or delete this chat",
                                    popupMode=QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.more_btn)
        menu.addAction("Rename chat…").triggered.connect(self.rename_chat)
        menu.addAction("Save chat to Library (Markdown)").triggered.connect(self.save_chat)
        menu.addSeparator()
        menu.addAction("Delete chat…").triggered.connect(self.delete_chat)
        self.more_btn.setMenu(menu)
        chats_row.addWidget(self.more_btn)
        layout.addLayout(chats_row)

        self.messages_area = QScrollArea(widgetResizable=True)
        self.messages_area.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.messages_area, 1)
        self.empty_label = QLabel(objectName="empty_tile_label", alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        layout.addWidget(self.empty_label, 1)
        input_row = QHBoxLayout()
        self.chat_input_edit = ChatInput(objectName="chat_input_edit",
                                         placeholderText="Message the agent…  (Enter sends · Shift+Enter: new line)")
        self.chat_input_edit.setFixedHeight(64)
        self.chat_input_edit.submitted.connect(self.send)
        input_row.addWidget(self.chat_input_edit, 1)
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send)
        input_row.addWidget(self.send_btn, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(input_row)
        self.chat_status_label = status_label()
        layout.addWidget(self.chat_status_label)

        self._redraw_timer = QTimer(self, singleShot=True, interval=REDRAW_MS, timeout=self._redraw_answer)
        self.controller.selection_changed.connect(self.refresh)
        self.controller.records_changed.connect(self._refresh_header)
        self.refresh()

    def refresh(self) -> None:
        record = self.controller.selected()
        agent_id = record.record_id if record is not None else None
        if agent_id != self.agent_id:
            self.agent_id = agent_id
            conversations = self.store.conversations(agent_id) if agent_id is not None else []
            self.conversation_id = conversations[0].conversation_id if conversations else None
            self._render_messages()
        self._refresh_header()
# --- END [1.3] ---

# ==============================================================================
# [1.4] header
# ==============================================================================
    def _refresh_header(self) -> None:
        record = self.controller.selected() if self.agent_id is not None else None
        has_agent = record is not None
        for widget in (self.provider_combo, self.model_combo, self.conversation_combo, self.more_btn, self.chat_input_edit):
            widget.setEnabled(has_agent)
        self.send_btn.setEnabled(has_agent or self.run is not None)
        self.send_btn.setText("Stop" if self.run is not None else "Send")
        self.chat_agent_label.setText(record.title if has_agent else "No agent selected")
        if not has_agent:
            self.conversation_combo.clear()
            return
        provider = provider_of(record.values)
        model = model_of(record.values, provider)
        self.provider_combo.setCurrentText(provider)
        base_url = self.module.base_urls().get(provider, "")
        models = list(_model_cache.get((provider, base_url)) or KNOWN_MODELS[provider])
        if model not in models:
            models.insert(0, model)
        if not self.model_combo.lineEdit().hasFocus():
            self.model_combo.blockSignals(True)
            self.model_combo.clear()
            self.model_combo.addItems(models)
            self.model_combo.setCurrentText(model)
            self.model_combo.blockSignals(False)
        self._load_models(provider, base_url)

        self.conversation_combo.blockSignals(True)
        self.conversation_combo.clear()
        conversations = self.store.conversations(record.record_id)
        if self.conversation_id is None:
            self.conversation_combo.addItem("New chat", None)
        for conversation in conversations:
            self.conversation_combo.addItem(conversation.title, conversation.conversation_id)
        self.conversation_combo.setCurrentIndex(max(0, self.conversation_combo.findData(self.conversation_id)))
        self.conversation_combo.blockSignals(False)

    def _load_models(self, provider: str, base_url: str) -> None:
        """Ask the provider which models it offers (once per view and endpoint); the list replaces the
        built-in one. Failures only show in the model box's tooltip: sending reports them properly."""
        key = (provider, base_url)
        if key in _model_cache or key in self._listing:
            return
        self._listing.add(key)
        key_text = api_key(provider)[0]
        if provider != "Ollama" and not key_text:
            return

        def done(models, error) -> None:
            if error is None and models:
                _model_cache[key] = sorted(models)
                self._refresh_header()
            elif error is not None:
                self.model_combo.setToolTip(f"Couldn't list {provider} models: {error}")
        run_in_thread(self, lambda: list_models(provider, base_url, key_text), done)

    def reload_models(self) -> None:
        self._listing.clear()
        self.model_combo.setToolTip("The agent's model (saved with the agent); type any model id")
        self._refresh_header()

    def _on_provider_chosen(self, _index: int) -> None:
        record = self.controller.selected()
        provider = self.provider_combo.currentText()
        if record is None or provider == provider_of(record.values):
            return
        base_url = self.module.base_urls().get(provider, "")
        offered = _model_cache.get((provider, base_url)) or KNOWN_MODELS[provider]
        model = record.values.get("model")
        self.controller.set_value(record.record_id, "provider", provider)
        self.controller.set_value(record.record_id, "model", model if model in offered else DEFAULT_MODELS[provider])

    def _on_model_chosen(self, *_args) -> None:
        record = self.controller.selected()
        model = self.model_combo.currentText().strip()
        if record is not None and model and model != record.values.get("model"):
            self.controller.set_value(record.record_id, "model", model)
# --- END [1.4] ---

# ==============================================================================
# [1.5] messages
# ==============================================================================
    def _render_messages(self) -> None:
        record = self.controller.selected() if self.agent_id is not None else None
        messages = self.store.messages(self.conversation_id) if self.conversation_id is not None else []
        is_live = self.run is not None and self.run["conversation_id"] == self.conversation_id and self.conversation_id is not None
        if record is None:
            self.empty_label.setText("Select an agent (or create one with + New agent) to chat with it.")
        else:
            config = chat_config(record.title, record.values)
            self.empty_label.setText(f"Say hello to {record.title}.\nIt answers with {config.provider} · {config.model}; "
                                     "its Instructions are its system prompt.")
        has_messages = bool(messages) or is_live
        self.messages_area.setVisible(has_messages)
        self.empty_label.setVisible(not has_messages)
        panel = QWidget(objectName="chat_messages_panel")
        column = QVBoxLayout(panel)
        column.setContentsMargins(4, 4, 4, 4)
        column.setSpacing(6)
        agent_name = record.title if record is not None else "Agent"
        for message in messages:
            caption = "You" if message.role == "user" else f"{agent_name}" + (f" · {message.model}" if message.model else "")
            column.addWidget(self._bubble(message.role, message.content, caption)[0])
        if self.run is not None:
            self.run["label"] = None
        if is_live:
            bubble, label = self._bubble("assistant", self.run["text"] or "…", f"{agent_name} · {self.run['model']}")
            self.run["label"] = label
            column.addWidget(bubble)
        column.addStretch()
        self.messages_area.setWidget(panel)
        QTimer.singleShot(0, self._scroll_to_end)

    @staticmethod
    def _bubble(role: str, text: str, caption: str) -> tuple[QFrame, QLabel]:
        bubble = QFrame(objectName="chat_message_panel")
        bubble.setProperty("role", role)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(8, 6, 8, 6)
        bubble_layout.setSpacing(2)
        bubble_layout.addWidget(QLabel(caption, objectName="chat_caption_label"))
        body = QLabel(objectName="chat_message_label", wordWrap=True, openExternalLinks=True)
        body.setTextFormat(Qt.TextFormat.MarkdownText if role == "assistant" else Qt.TextFormat.PlainText)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        body.setText(text)
        bubble_layout.addWidget(body)
        return bubble, body

    def _is_at_end(self) -> bool:
        bar = self.messages_area.verticalScrollBar()
        return bar.value() >= bar.maximum() - 24

    def _scroll_to_end(self) -> None:
        bar = self.messages_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _redraw_answer(self) -> None:
        if self.run is not None and self.run["label"] is not None:
            follow = self._is_at_end()
            self.run["label"].setText(self.run["text"] or "…")
            if follow:
                QTimer.singleShot(0, self._scroll_to_end)
# --- END [1.5] ---

# ==============================================================================
# [1.6] sending
# ==============================================================================
    def send(self) -> None:
        """Send the typed message (or, while an answer streams, stop it - the answer so far is kept)."""
        if self.run is not None:
            self.run["stop"].set()
            set_status(self.chat_status_label, "Stopping…")
            return
        record = self.controller.selected()
        text = self.chat_input_edit.toPlainText().strip()
        if record is None:
            set_status(self.chat_status_label, "Select an agent to chat with", is_error=True)
            return
        if not text:
            return
        if self.conversation_id is None:
            self.conversation_id = self.store.new_conversation(record.record_id, conversation_title(text)).conversation_id
        elif not self.store.messages(self.conversation_id):
            self.store.rename_conversation(self.conversation_id, conversation_title(text))
        config = chat_config(record.title, record.values, self.module.base_urls())
        user_message_id = self.store.add_message(self.conversation_id, "user", text)
        history = [ChatMessage(message.role, message.content) for message in self.store.messages(self.conversation_id)]
        stop = threading.Event()
        self.destroyed.connect(lambda *_: stop.set())          # a closed view stops its answer
        self.run = {"stop": stop, "conversation_id": self.conversation_id, "text": "", "label": None, "agent": record.title,
                    "model": config.model, "provider": config.provider, "user_message_id": user_message_id, "user_text": text}
        self.chat_input_edit.clear()
        self._render_messages()
        self._refresh_header()
        set_status(self.chat_status_label, f"{record.title} is answering ({config.provider} · {config.model})…")
        run_in_thread(self, lambda report: stream_chat(config, history, report, stop.is_set), self._on_done, self._on_piece)

    def _on_piece(self, piece: str) -> None:
        if self.run is not None:
            self.run["text"] += piece
            if not self._redraw_timer.isActive():
                self._redraw_timer.start()

    def _on_done(self, result, error) -> None:
        run, self.run = self.run, None
        self._redraw_timer.stop()
        if run is None:
            return
        source, who = "agents", f"“{run['agent']}” ({run['provider']} · {run['model']})"
        if error is not None:
            if run["text"]:                                   # broke off mid-answer: keep what arrived
                self.store.add_message(run["conversation_id"], "assistant", run["text"], run["model"])
            else:                                             # nothing came back: your message returns to the box
                self.store.delete_message(run["user_message_id"])
                if not self.chat_input_edit.toPlainText().strip():
                    self.chat_input_edit.setPlainText(run["user_text"])
            if isinstance(error, AgentError):
                self.log.warning(source, f"Chat with {who}: {error}")
            else:
                self.log.error(source, f"Chat with {who} failed", error)
            set_status(self.chat_status_label, str(error), is_error=True)
        else:
            if result.text:
                self.store.add_message(run["conversation_id"], "assistant", result.text, run["model"])
            notes = []
            if result.was_stopped:
                notes.append("stopped")
            elif result.stop_reason in ("max_tokens", "MAX_TOKENS", "length"):
                notes.append("cut off: the output limit was reached (Max output tokens)")
            tokens = f"{result.input_tokens} in · {result.output_tokens} out tokens" if result.output_tokens else ""
            self.log.info(source, f"{who} answered" + (f" ({tokens})" if tokens else ""))
            set_status(self.chat_status_label, " · ".join([f"{run['provider']} · {run['model']}"] + ([tokens] if tokens else []) + notes))
        self._render_messages()
        self._refresh_header()
# --- END [1.6] ---

# ==============================================================================
# [1.7] conversation_actions
# ==============================================================================
    def show_conversation(self, conversation_id: int | None) -> None:
        if conversation_id != self.conversation_id:
            self.conversation_id = conversation_id
            self._render_messages()
            self._refresh_header()

    def new_chat(self) -> None:
        if self.agent_id is not None:
            self.show_conversation(None)
            self.chat_input_edit.setFocus()

    def _conversation(self):
        if self.agent_id is None or self.conversation_id is None:
            return None
        return next((item for item in self.store.conversations(self.agent_id) if item.conversation_id == self.conversation_id), None)

    def rename_chat(self) -> None:
        conversation = self._conversation()
        if conversation is None:
            return
        title, is_ok = QInputDialog.getText(self, "Rename chat", "Name", text=conversation.title)
        if is_ok and title.strip():
            self.store.rename_conversation(conversation.conversation_id, title.strip())
            self._refresh_header()

    def chat_markdown(self) -> str:
        conversation = self._conversation()
        record = self.controller.selected()
        if conversation is None or record is None:
            return ""
        lines = [f"# {conversation.title}", "", f"Chat with {record.title}", ""]
        for message in self.store.messages(conversation.conversation_id):
            who = "You" if message.role == "user" else f"{record.title}" + (f" ({message.model})" if message.model else "")
            lines += [f"**{who}:**", "", message.content, ""]
        return "\n".join(lines)

    def save_chat(self) -> None:
        conversation = self._conversation()
        if conversation is None:
            set_status(self.chat_status_label, "Nothing to save yet", is_error=True)
            return
        with self.log.step("agents", f"Save chat “{conversation.title}” to the Library"):
            item = get_content_store().create_text("note", conversation.title, self.chat_markdown(), ".md")
        set_status(self.chat_status_label, f"Saved “{item.name}” in your Library (Notes)")

    def delete_chat(self) -> None:
        conversation = self._conversation()
        if conversation is None or (self.run is not None and self.run["conversation_id"] == conversation.conversation_id):
            return
        if QMessageBox.question(self, "Delete chat", f"Delete the chat “{conversation.title}” and all its messages? "
                                "This can't be undone.") != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_conversation(conversation.conversation_id)
        self.log.info("agents", f"Deleted chat “{conversation.title}”")
        remaining = self.store.conversations(self.agent_id)
        self.conversation_id = remaining[0].conversation_id if remaining else None
        self._render_messages()
        self._refresh_header()
# --- END [1.7] ---
