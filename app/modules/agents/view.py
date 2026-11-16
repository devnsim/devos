# ==============================================================================
# FILE: app/modules/agents/view.py
# OVERVIEW: Agents module - define AI agents as records: name, role, type, model, status, permissions,
#           tools, limits and instructions; see them as a table, a board, a form (any record view).
# PURPOSE:
#   1. AgentsModule: a RecordsModule over the "agents" collection with agent properties.
#   2. Export an agent as an agent definition file (Markdown with front matter: name, description, model,
#      tools mapped from permissions, then the instructions) into the Library.
# CONTRACT:
#   - Requires: app.modules.records_module.RecordsModule, app.data.store.records, app.data.store.content
#   - Exposes: AgentsModule(parent=None, context=None), AGENT_PROPERTIES, agent_definition(record) -> str,
#              PERMISSION_TOOLS
# NOTES:
#   - [2026-11-16] Created. Agents are only defined here (no runtime yet): running them belongs to the
#                  piping / service layer that is still to be designed.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - agent definition: Markdown file with YAML front matter describing one agent (the format Claude Code
#   reads from .claude/agents/); the body is the agent's instructions.
# - PERMISSION_TOOLS (dict): Permission -> the tool names it grants in an agent definition.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. AGENTS
#    1.1 properties
#    1.2 agent_definition
#    1.3 agents_module
# ==============================================================================

# ==============================================================================
# [1.1] properties
# ==============================================================================
from __future__ import annotations

import re

from PySide6.QtWidgets import QHBoxLayout, QPushButton

from app.data.store.content import get_content_store
from app.data.store.records import PropertySpec, Record
from app.modules.records_module import RecordsModule
from app.ui.ui_tools import set_status

PERMISSION_TOOLS = {
    "Read files": ("Read", "Grep", "Glob"),
    "Write files": ("Edit", "Write"),
    "Run commands": ("Bash",),
    "Web access": ("WebFetch", "WebSearch"),
}
AGENT_PROPERTIES = [
    PropertySpec("role", "Role"),
    PropertySpec("status", "Status", "select", ("Draft", "Active", "Paused", "Retired")),
    PropertySpec("agent_type", "Type", "select", ("Assistant", "Researcher", "Coder", "Reviewer", "Planner", "Operator", "Custom")),
    PropertySpec("model", "Model", "select", ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2", "inherit")),
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
    DEFAULT_LAYOUT = {"split": "h", "sizes": [3, 2], "children": [
        {"split": "v", "sizes": [3, 2], "children": [{"kind": "table"}, {"kind": "board"}]},
        {"kind": "form"},
    ]}

    def add_toolbar_actions(self, row: QHBoxLayout) -> None:
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
