# ==============================================================================
# FILE: app/templates/__init__.py
# OVERVIEW: Templates - recipes for page tiles made of views over engine data (Phase 5c of the engine plan).
#           The picker offers them next to the remaining modules; the built-ins replace the old Data-group
#           modules under the same ids, and the user's own are saved in the workspace database.
# PURPOSE:
#   1. template(template_id, database) -> TemplateSpec (built-in first, then the user's); KeyError if gone.
#   2. all_templates(database) -> every template, built-ins first.
# CONTRACT:
#   - Requires: app.templates (spec, builtin), app.data.store.templates
#   - Exposes: template(), all_templates(), built_in_ids(), TemplateSpec
# NOTES:
#   - [2026-11-17] Created in Phase 5c of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - built-in template: Written in app/templates/builtin.py; its id is the old module's id where it replaced one.
# - user template: Saved with "Save as template…"; id "user:<random>" (app/data/store/templates.py).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. TEMPLATES
#    1.1 lookup
# ==============================================================================

# ==============================================================================
# [1.1] lookup
# ==============================================================================
from __future__ import annotations

from app.data.store.database import Database
from app.data.store.templates import USER_PREFIX, get_template_store
from app.templates.spec import TemplateSpec


def _built_in() -> dict[str, TemplateSpec]:
    from app.templates.builtin import BUILT_IN_TEMPLATES    # lazy: it imports view types and the Agents parts
    return {spec.template_id: spec for spec in BUILT_IN_TEMPLATES}


def built_in_ids() -> list[str]:
    return list(_built_in())


def template(template_id: str, database: Database | None = None) -> TemplateSpec:
    built_in = _built_in()
    if template_id in built_in:
        return built_in[template_id]
    if template_id.startswith(USER_PREFIX):
        stored = get_template_store(database).get(template_id)
        if stored is not None:
            return TemplateSpec.from_dict(stored.spec)
    raise KeyError(f"There is no template “{template_id}” (it may have been deleted; the tile's data is kept)")


def all_templates(database: Database | None = None) -> list[TemplateSpec]:
    return list(_built_in().values()) + [TemplateSpec.from_dict(stored.spec)
                                         for stored in get_template_store(database).templates()]
# --- END [1.1] ---
