# ==============================================================================
# FILE: app/templates/spec.py
# OVERVIEW: TemplateSpec - what a template is: the recipe for a page tile made of views over engine data
#           (the plan's Template contract: engines, schema, view definitions, block layout, actions, version).
#           Built-ins are written in code; the user's are saved as JSON in the workspace database.
# PURPOSE:
#   1. One frozen dataclass with to_dict() / from_dict() (plain JSON, so templates can be stored and later
#      shared without code).
#   2. SOURCE_KINDS: what a template's tile shows - a record collection, or one of the Library's databases,
#      workbooks, datasets or graphs (the tile picks which file).
# CONTRACT:
#   - Requires: standard library only.
#   - Exposes: TemplateSpec, SOURCE_KINDS, FILE_SOURCE_KINDS, TEMPLATE_VERSION
# NOTES:
#   - [2026-11-17] Created in Phase 5c of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - source kind: "records" (one collection, named by the template) or a Library kind ("database", "sheet",
#   "dataset", "graph": each tile chooses its file and table).
# - definitions (dict): view type id -> the parts of a view definition the template sets (title, query,
#   mapping, presentation); the source is always the tile's own.
# - legacy_blocks (dict): An old module's block kind -> the view that replaces it (None: dropped).
# - extension: "module:Class" adding blocks, actions and record actions a template needs (Agents: chat).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SPEC
#    1.1 template_spec
# ==============================================================================

# ==============================================================================
# [1.1] template_spec
# ==============================================================================
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field

SOURCE_KINDS = ("records", "database", "sheet", "dataset", "graph")
FILE_SOURCE_KINDS = SOURCE_KINDS[1:]
TEMPLATE_VERSION = 1


@dataclass(frozen=True)
class TemplateSpec:
    template_id: str
    title: str
    description: str = ""
    category: str = "Templates"
    source_kind: str = "records"
    collection: str = ""                         # records: the collection every tile of this template shows
    noun: str = "record"
    properties: tuple = ()                       # records: PropertySpec dicts (the collection's schema)
    presets: tuple = ()                          # records: (menu title, values) for + New
    views: tuple = ()                            # block kinds offered (view type ids, extension blocks)
    definitions: dict = field(default_factory=dict)
    layout: dict | None = None
    actions: tuple = ()                          # toolbar action ids (app/templates/actions.py)
    extension: str = ""
    legacy_blocks: dict = field(default_factory=dict)
    is_user: bool = False
    version: int = TEMPLATE_VERSION

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("properties", "presets", "views", "actions"):
            data[key] = [list(item) if isinstance(item, tuple) else item for item in data[key]]
        return copy.deepcopy(data)

    @staticmethod
    def from_dict(data: dict) -> "TemplateSpec":
        known = {key: value for key, value in data.items() if key in TemplateSpec.__dataclass_fields__}
        for key in ("properties", "views", "actions"):
            known[key] = tuple(known.get(key) or ())
        known["presets"] = tuple(tuple(item) for item in known.get("presets") or ())
        if known.get("source_kind") not in SOURCE_KINDS:
            known["source_kind"] = "records"
        return TemplateSpec(**known)

    @property
    def is_file_kind(self) -> bool:
        return self.source_kind in FILE_SOURCE_KINDS
# --- END [1.1] ---
