# ==============================================================================
# FILE: app/views/types/__init__.py
# OVERVIEW: The built-in view types. Importing this package registers them (app.views.api.view_type() does
#           it on first use).
# PURPOSE:
#   1. One place that lists every built-in view type; engine-bundled views (Phase 3) and user views (Phase 12)
#      register the same way.
# CONTRACT:
#   - Requires: app.views.types (table, board, timeline, calendar, form, document, links)
#   - Exposes: BUILT_IN (type ids in picker order), LIBRARY_VIEWS
# NOTES:
#   - [2026-11-17] Document and Links view types (Phase 4).
#   - [2026-11-17] Created in Phase 2 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - BUILT_IN (tuple): The built-in view type ids for any table source, in the order pickers show them.
# - LIBRARY_VIEWS (tuple): Built-in view types for sources with documents (read_text / write_text / links).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. TYPES
#    1.1 registration
# ==============================================================================

# ==============================================================================
# [1.1] registration
# ==============================================================================
from app.views.types import board, calendar, document, form, links, table, timeline  # noqa: F401  (each registers itself)

BUILT_IN = ("table", "board", "timeline", "calendar", "form")       # any table source
LIBRARY_VIEWS = ("document", "links")                               # sources answering read_text / links (Knowledge)
# --- END [1.1] ---
