# ==============================================================================
# FILE: app/modules/agents/view.py
# OVERVIEW: Agents module - define AI agents as records (name, role, provider, model, status, permissions,
#           tools, limits, instructions), see them as a table, a board, a form, and chat with them: Claude,
#           Gemini or local Ollama agents.
# PURPOSE:
#   1. AgentsModule: a RecordsModule over the "agents" collection with agent properties; + New agent offers
#      a Claude, Gemini or Ollama agent; a Chat view (blocks.ChatBlock) talks to the selected agent.
#   2. Settings (⚙): API keys (system keyring, never the database) and the Ollama address.
#   3. Export an agent as an agent definition file (Markdown with front matter: name, description, model,
#      tools mapped from permissions, then the instructions) into the Library.
# CONTRACT:
#   - Requires: app.modules.records_module.RecordsModule, app.modules.agents (engine, blocks),
#               app.data.store.records, app.data.store.content, app.ui.settings_dialog, app.ui.tool_bag
#   - Exposes: AgentsModule(parent=None, context=None) with base_urls(), open_settings(), show_chat();
#              AGENT_PROPERTIES, agent_definition(record) -> str, PERMISSION_TOOLS, OLLAMA_URL_PREFERENCE
# NOTES:
#   - [2026-11-17] Chat: Provider property (Claude / Gemini / Ollama), Max output tokens, + New agent menu, a Chat
#                  view, Chat with agent in every view's record menu, ⚙ settings for API keys and Ollama.
#   - [2026-11-16] Created. Agents are only defined here (no runtime yet): running them belongs to the
#                  piping / service layer that is still to be designed.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - agent definition: Markdown file with YAML front matter describing one agent (the format Claude Code
#   reads from .claude/agents/); the body is the agent's instructions.
# - PERMISSION_TOOLS (dict): Permission -> the tool names it grants in an agent definition.
# - OLLAMA_URL_PREFERENCE (str): User preference holding the Ollama server address.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. AGENTS
#    1.1 properties
#    1.2 agent_definition
#    1.3 agents_module
#    1.4 settings
#    1.5 tools
# ==============================================================================

# ==============================================================================
# [1.1] properties
# ==============================================================================
from __future__ import annotations

import re

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton

from app.data.store.content import get_content_store
from app.data.store.database import get_database
from app.data.store.records import PropertySpec, Record
from app.data.store.secrets import secret_storage
from app.modules.agents.blocks import ChatBlock, forget_listed_models
from app.modules.agents.engine import (
    DEFAULT_ENDPOINTS, DEFAULT_MODELS, KEY_ENVIRONMENT, KNOWN_MODELS, PROVIDERS, api_key, forget_api_key, set_api_key,
)
from app.modules.records_module import RecordsModule
from app.ui.canvas.block_canvas import BlockSpec
from app.ui.settings_dialog import SettingsDialog
from app.ui.tool_bag import ToolSpec, register_tools
from app.ui.ui_tools import control_label, set_status, tool_button

PERMISSION_TOOLS = {
    "Read files": ("Read", "Grep", "Glob"),
    "Write files": ("Edit", "Write"),
    "Run commands": ("Bash",),
    "Web access": ("WebFetch", "WebSearch"),
}
OLLAMA_URL_PREFERENCE = "agents.ollama_url"
AGENT_PROPERTIES = [
    PropertySpec("role", "Role"),
    PropertySpec("status", "Status", "select", ("Draft", "Active", "Paused", "Retired")),
    PropertySpec("agent_type", "Type", "select", ("Assistant", "Researcher", "Coder", "Reviewer", "Planner", "Operator", "Custom")),
    PropertySpec("provider", "Provider", "select", PROVIDERS),
    PropertySpec("model", "Model", "select", tuple(model for provider in PROVIDERS for model in KNOWN_MODELS[provider]) + ("inherit",)),
    PropertySpec("max_tokens", "Max output tokens", "number"),
    PropertySpec("permissions", "Permissions", "multi_select",
                 tuple(PERMISSION_TOOLS) + ("Use databases", "Send messages", "Spend money")),
    PropertySpec("tools", "Extra tools", "multi_select"),
    PropertySpec("max_turns", "Max turns", "number"),
    PropertySpec("owner", "Owner"),
    PropertySpec("instructions", "Instructions", "long_text"),
]
# --- END [1.1] ---


# ==============================================================================
# [1.2] agent_definition
# ==============================================================================
def agent_slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "agent"


def agent_definition(record: Record) -> str:
    """The agent as Markdown with front matter (name, description, model, tools) + its instructions."""
    values = record.values
    tools: list[str] = []
    for permission in values.get("permissions") or []:
        tools += [tool for tool in PERMISSION_TOOLS.get(permission, ()) if tool not in tools]
    tools += [tool for tool in values.get("tools") or [] if tool not in tools]
    description = (values.get("role") or f"{values.get('agent_type') or 'Agent'} defined in DevOS").replace("\n", " ")
    lines = ["---", f"name: {agent_slug(record.title)}", f"description: {description}"]
    if values.get("model"):
        lines.append(f"model: {values['model']}")
    if tools:
        lines.append(f"tools: {', '.join(tools)}")
    if values.get("max_turns"):
        lines.append(f"maxTurns: {int(values['max_turns'])}")
    lines += ["---", "", values.get("instructions") or f"You are {record.title}.", ""]
    return "\n".join(lines)
# --- END [1.2] ---


# ==============================================================================
# [1.3] agents_module
# ==============================================================================
class AgentsModule(RecordsModule):
    COLLECTION = "agents"
    NOUN = "agent"
    PROPERTIES = AGENT_PROPERTIES
    NEW_RECORD_PRESETS = [(f"{provider} agent", {"provider": provider, "model": DEFAULT_MODELS[provider], "status": "Draft"})
                          for provider in PROVIDERS]
    DEFAULT_LAYOUT = {"split": "h", "sizes": [3, 2, 3], "children": [
        {"split": "v", "sizes": [3, 2], "children": [{"kind": "table"}, {"kind": "board"}]},
        {"kind": "form"},
        {"kind": "chat"},
    ]}

    def extra_block_specs(self) -> list[BlockSpec]:
        return [BlockSpec("chat", "Chat", "Talk to the selected agent (Claude, Gemini or Ollama); chats are kept per agent",
                          lambda: ChatBlock(self))]

    def record_actions(self) -> list[tuple[str, object]]:
        return [("Chat with agent", self.show_chat)]

    def show_chat(self, record_id: int | None = None) -> None:
        if record_id is not None:
            self.controller.select(record_id)
        chat = self.block_canvas.show_block("chat")
        chat.chat_input_edit.setFocus()

    def base_urls(self) -> dict[str, str]:
        database = self.context.database if self.context is not None else get_database()
        return {"Ollama": database.get_preference(OLLAMA_URL_PREFERENCE) or DEFAULT_ENDPOINTS["Ollama"]}

    def add_toolbar_actions(self, row: QHBoxLayout) -> None:
        chat_btn = QPushButton("Chat", toolTip="Chat with the selected agent")
        chat_btn.clicked.connect(lambda: self.show_chat())
        row.addWidget(chat_btn)
        settings_btn = tool_button("module_settings_btn", "settings.svg", "Agents settings: API keys, Ollama address")
        settings_btn.clicked.connect(self.open_settings)
        row.addWidget(settings_btn)
        export_btn = QPushButton("Export agent file", toolTip="Save the selected agent as an agent definition "
                                 "(Markdown + front matter) in your Library")
        export_btn.clicked.connect(self.export_agent_file)
        row.addWidget(export_btn)

    def export_agent_file(self) -> None:
        record = self.controller.selected()
        if record is None:
            set_status(self.module_status_label, "Select an agent first", is_error=True)
            return
        with self.log.step("records:agents", f"Export agent file for “{record.title}”"):
            item = get_content_store().create_text("note", f"{agent_slug(record.title)} (agent)", agent_definition(record), ".md")
        set_status(self.module_status_label, f"Saved “{item.name}” in your Library (Notes)")
# --- END [1.3] ---


# ==============================================================================
# [1.4] settings
# ==============================================================================
    def open_settings(self) -> None:
        window = getattr(self, "settings_window", None)
        if window is None:
            window = self.settings_window = SettingsDialog(self.window(), "Agents")
            form = window.add_form_tab("Providers")
            note = control_label(f"API keys are kept in: {secret_storage()} — never in the DevOS database. "
                                 "Without a saved key, the usual environment variable is used.")
            note.setWordWrap(True)
            form.addRow(note)
            self.key_edits: dict[str, QLineEdit] = {}
            for provider in ("Claude", "Gemini"):
                edit = QLineEdit(echoMode=QLineEdit.EchoMode.Password)
                forget_btn = QPushButton("Forget", toolTip=f"Remove the saved {provider} key from the keyring")
                forget_btn.clicked.connect(lambda _checked=False, provider=provider: self._forget_key(provider))
                key_row = QHBoxLayout()
                key_row.addWidget(edit, 1)
                key_row.addWidget(forget_btn)
                form.addRow(f"{provider} API key", key_row)
                self.key_edits[provider] = edit
            self.ollama_url_edit = QLineEdit(placeholderText=DEFAULT_ENDPOINTS["Ollama"])
            form.addRow("Ollama address", self.ollama_url_edit)
            form.addRow(QLabel("Ollama runs models on this machine: no key needed. Start it with `ollama serve` "
                               "(NixOS: services.ollama.enable = true) and pull a model: `ollama pull llama3.2`.",
                               objectName="control_label", wordWrap=True))
            window.applied.connect(self._apply_settings)
        self._read_settings()
        window.open_window()

    def _read_settings(self) -> None:
        for provider, edit in self.key_edits.items():
            _key, source = api_key(provider)
            names = " / ".join(KEY_ENVIRONMENT[provider])
            edit.clear()
            edit.setPlaceholderText(f"saved in the {source} — type to replace" if source else f"paste your key (or set {names})")
        self.ollama_url_edit.setText(self.base_urls()["Ollama"])

    def _apply_settings(self) -> None:
        changed = []
        for provider, edit in self.key_edits.items():
            if edit.text().strip():
                set_api_key(provider, edit.text())
                changed.append(f"{provider} key")
        database = self.context.database if self.context is not None else get_database()
        url = self.ollama_url_edit.text().strip().rstrip("/") or DEFAULT_ENDPOINTS["Ollama"]
        if url != self.base_urls()["Ollama"]:
            database.set_preference(OLLAMA_URL_PREFERENCE, url)
            changed.append("Ollama address")
        forget_listed_models()                               # new keys / address: list the models again
        self._read_settings()
        if changed:
            self.log.info("agents", f"Agents settings saved: {', '.join(changed)}")
            set_status(self.module_status_label, f"Saved: {', '.join(changed)}")
        for chat in self.block_canvas.blocks("chat"):
            chat.reload_models()

    def _forget_key(self, provider: str) -> None:
        forget_api_key(provider)
        self.log.info("agents", f"Forgot the saved {provider} API key")
        self._read_settings()
# --- END [1.4] ---


# ==============================================================================
# [1.5] tools
# ==============================================================================
register_tools([
    ToolSpec("agents.chat", "Chat with agent", "💬", "Open the Chat view for the selected agent",
             lambda module: module.show_chat(), "agents", "chat talk claude gemini ollama"),
    ToolSpec("agents.settings", "Agents settings", "⚙", "API keys (Claude, Gemini) and the Ollama address",
             lambda module: module.open_settings(), "agents", "api key ollama"),
])
# --- END [1.5] ---
