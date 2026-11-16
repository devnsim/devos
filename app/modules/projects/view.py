# ==============================================================================
# FILE: app/modules/projects/view.py
# OVERVIEW: Projects module - every project is a record (status, priority, owner, dates, progress, tags,
#           description), shown as a board, a table, a timeline (Gantt), a calendar or a form.
# PURPOSE:
#   1. ProjectsModule: a RecordsModule over the "projects" collection; opens on board + timeline + form.
# CONTRACT:
#   - Requires: app.modules.records_module.RecordsModule, app.data.store.records.PropertySpec
#   - Exposes: ProjectsModule(parent=None, context=None), PROJECT_PROPERTIES
# NOTES:
#   - [2026-11-16] Created: the second module on the record model (proves the views are reusable).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - progress (number): Percent done, 0-100.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. PROJECTS
#    1.1 projects_module
# ==============================================================================

# ==============================================================================
# [1.1] projects_module
# ==============================================================================
from app.data.store.records import PropertySpec
from app.modules.records_module import RecordsModule

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


class ProjectsModule(RecordsModule):
    COLLECTION = "projects"
    NOUN = "project"
    PROPERTIES = PROJECT_PROPERTIES
    DEFAULT_LAYOUT = {"split": "h", "sizes": [3, 1], "children": [
        {"split": "v", "sizes": [3, 2], "children": [{"kind": "board"}, {"kind": "timeline"}]},
        {"kind": "form"},
    ]}
# --- END [1.1] ---
