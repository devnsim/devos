# ==============================================================================
# FILE: app/templates/builtin.py
# OVERVIEW: The templates DevOS ships: Projects, Agents, Events (records), Database, Spreadsheet, Graph
#           database and Dataset chart (Library files). Their ids are the ids of the modules they replaced,
#           so every page saved with those modules opens them as templates without changing the page.
# PURPOSE:
#   1. BUILT_IN_TEMPLATES: TemplateSpec per template - its data, views (and their settings), layout, toolbar
#      actions; legacy_blocks map the old modules' blocks to the views that replace them.
#   2. PROJECT_PROPERTIES / EVENT_PROPERTIES: the schemas of the Projects and Events collections.
# CONTRACT:
#   - Requires: app.templates.spec, app.data.store.records.PropertySpec, app.views.types.BUILT_IN,
#               app.modules.agents.template (AGENT_PROPERTIES, AGENT_PRESETS)
#   - Exposes: BUILT_IN_TEMPLATES (tuple), PROJECT_PROPERTIES, EVENT_PROPERTIES, LEGACY_SETTINGS
# NOTES:
#   - [2026-11-17] Created in Phase 5c of the engine plan (from the Projects, Agents, Calendar, Database Editor,
#                  Spreadsheet, Graph Database and Graph modules).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - LEGACY_SETTINGS (dict): Template id -> how its old module named the open file in its settings.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. BUILT_IN
#    1.1 schemas
#    1.2 templates
# ==============================================================================

# ==============================================================================
# [1.1] schemas
# ==============================================================================
from __future__ import annotations

from app.data.store.records import PropertySpec
from app.modules.agents.template import AGENT_PRESETS, AGENT_PROPERTIES
from app.templates.spec import TemplateSpec
from app.views.types import BUILT_IN

PROJECT_PROPERTIES = [
    PropertySpec("status", "Status", "select", ("Idea", "Planned", "Active", "Blocked", "Done")),
    PropertySpec("priority", "Priority", "select", ("Low", "Medium", "High", "Urgent")),
    PropertySpec("owner", "Owner"),
    PropertySpec("start", "Start", "date"),
    PropertySpec("due", "Due", "date"),
    PropertySpec("progress", "Progress %", "number"),
    PropertySpec("tags", "Tags", "multi_select"),
    PropertySpec("link", "Link", "url"),
    PropertySpec("description", "Description", "long_text"),
]
EVENT_PROPERTIES = [
    PropertySpec("date", "Date", "date"),
    PropertySpec("end", "Ends", "date"),
    PropertySpec("kind", "Kind", "select", ("Meeting", "Deadline", "Reminder", "Personal")),
    PropertySpec("place", "Place"),
    PropertySpec("notes", "Notes", "long_text"),
]
LEGACY_SETTINGS = {"db_editor": ("database_id", "table"), "spreadsheet": ("item_id", None), "graph_db": ("item_id", None),
                   "graph": ("graph.dataset_id", None)}


def _schema(properties: list[PropertySpec]) -> tuple:
    return tuple(spec.to_dict() for spec in properties)
# --- END [1.1] ---


# ==============================================================================
# [1.2] templates
# ==============================================================================
FILE_ACTIONS = ("new_file", "import_file", "export_file")
BUILT_IN_TEMPLATES: tuple[TemplateSpec, ...] = (
    TemplateSpec(
        "projects", "Projects", "Every project is a record: board, timeline (Gantt), calendar, table and form.",
        source_kind="records", collection="projects", noun="project", properties=_schema(PROJECT_PROPERTIES),
        views=BUILT_IN, actions=("new_record", "export_json"),
        layout={"split": "h", "sizes": [3, 1], "children": [
            {"split": "v", "sizes": [3, 2], "children": [{"kind": "board"}, {"kind": "timeline"}]}, {"kind": "form"}]}),
    TemplateSpec(
        "agents", "Agents", "AI agents as records (role, model, permissions, instructions); chat with Claude, Gemini or Ollama.",
        source_kind="records", collection="agents", noun="agent", properties=_schema(AGENT_PROPERTIES),
        presets=tuple(AGENT_PRESETS), views=BUILT_IN + ("chat",), actions=("new_record", "export_json"),
        extension="app.modules.agents.template:AgentsExtension",
        layout={"split": "h", "sizes": [3, 2, 3], "children": [
            {"split": "v", "sizes": [3, 2], "children": [{"kind": "table"}, {"kind": "board"}]},
            {"kind": "form"}, {"kind": "chat"}]}),
    TemplateSpec(
        "calendar", "Events", "Events with a date: a month calendar, a list and a form (replaces the Calendar).",
        source_kind="records", collection="events", noun="event", properties=_schema(EVENT_PROPERTIES),
        views=BUILT_IN, actions=("new_record", "export_json"),
        definitions={"calendar": {"mapping": {"date": "date", "color": "kind"}},
                     "table": {"query": {"sort": [{"field": "date", "descending": False}]}}},
        layout={"split": "h", "sizes": [3, 1], "children": [{"kind": "calendar"}, {"kind": "form"}]}),
    TemplateSpec(
        "db_editor", "Database", "One of your SQLite databases: a grid of any table, a form, its structure and SQL.",
        source_kind="database", noun="row", views=("grid", "form", "structure", "sql_console", "table", "chart"),
        actions=FILE_ACTIONS + ("new_table",),
        layout={"split": "h", "sizes": [3, 2], "children": [
            {"kind": "grid"}, {"split": "v", "sizes": [1, 1], "children": [{"kind": "form"}, {"kind": "structure"}]}]},
        legacy_blocks={"explorer": None, "data": "grid", "record": "form", "structure": "structure", "sql": "sql_console",
                       "properties": None}),
    TemplateSpec(
        "spreadsheet", "Spreadsheet", "A workbook: cells and formulas, a chart, and each sheet as a table.",
        source_kind="sheet", noun="row", views=("sheet", "chart", "grid", "structure"), actions=FILE_ACTIONS + ("add_sheet",),
        layout={"split": "h", "sizes": [3, 2], "children": [{"kind": "sheet"}, {"kind": "chart"}]},
        legacy_blocks={"grid": "sheet", "selection": None, "chart": "chart"}),
    TemplateSpec(
        "graph_db", "Graph database", "A Kùzu graph database: the network, a table of nodes and a Cypher console.",
        source_kind="graph", noun="node", views=("network", "grid", "form", "cypher_console", "structure"),
        actions=FILE_ACTIONS[:1] + ("export_file", "new_table", "sample_graph"),
        layout={"split": "h", "sizes": [3, 2], "children": [
            {"kind": "network"}, {"split": "v", "sizes": [1, 1], "children": [{"kind": "grid"}, {"kind": "cypher_console"}]}]},
        legacy_blocks={"schema": "structure", "cypher": "cypher_console", "graph": "network", "inspector": "form"}),
    TemplateSpec(
        "graph", "Dataset chart", "A dataset (CSV) as a chart and a grid; import one or make sample data.",
        source_kind="dataset", noun="row", views=("chart", "grid", "table"), actions=FILE_ACTIONS + ("mock_dataset",),
        layout={"split": "h", "sizes": [3, 2], "children": [{"kind": "chart"}, {"kind": "grid"}]}),
)
# --- END [1.2] ---
