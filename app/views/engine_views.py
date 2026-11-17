# ==============================================================================
# FILE: app/views/engine_views.py
# OVERVIEW: Views shipped by engines - an engine package can bring its own view types (engine.json "views").
#           They are registered while the engine is attached and removed when it is detached.
# PURPOSE:
#   1. ensure_engine_views(): on every view type lookup, bring the registry in line with what is attached (import
#      the views module of attached engines, drop detached engines' views); "runtime.*" announcements too.
#   2. The views module registers ViewTypes with engine=<its id>; a detached engine's view types disappear
#      (hosts showing them show a placeholder and keep their definitions).
# CONTRACT:
#   - Requires: engines.runtime (get_bus, packages), engines.kernel.packages.load_package_module,
#               app.views.api.unregister_engine_views, app.ui.engine_bridge.watch_engine, PySide6
#   - Exposes: ensure_engine_views(), loaded_engine_views() -> {engine id: [view type ids]}
# NOTES:
#   - [2026-11-17] Created in Phase 3 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - engine views: View types a package registers from its views module (its front-end part).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. ENGINE_VIEWS
#    1.1 sync
# ==============================================================================

# ==============================================================================
# [1.1] sync
# ==============================================================================
from __future__ import annotations

from PySide6.QtCore import QObject

from app.data.activity_log import get_activity_log
from app.views import api
from engines.kernel.packages import PackageError, load_package_module

_watcher: QObject | None = None
_loaded: dict[str, list[str]] = {}


def loaded_engine_views() -> dict[str, list[str]]:
    return {engine_id: list(type_ids) for engine_id, type_ids in _loaded.items()}


def _load(engine_id: str) -> None:
    from engines.runtime import package
    item = package(engine_id)
    if item is None or not item.views or engine_id in _loaded:
        return
    before = set(api._view_types)
    try:
        module = load_package_module(item, item.views)
        register = getattr(module, "register_views", None)
        if callable(register):                          # a views module may register on import or in register_views()
            register()
    except PackageError as error:
        get_activity_log().error("engines", f"{item.title}'s views couldn't be loaded", error)
        return
    _loaded[engine_id] = sorted(set(api._view_types) - before) or [
        type_id for type_id, kind in api._view_types.items() if kind.engine == engine_id]


def _on_runtime(message) -> None:
    engine_id = message.payload.get("engine_id", "")
    if message.name == "attached":
        _load(engine_id)
    elif message.name == "detached":
        api.unregister_engine_views(engine_id)
        _loaded.pop(engine_id, None)


def ensure_engine_views() -> None:
    """Make the registry match what is attached right now (cheap; called on every view type lookup, so a
    lookup straight after an attach / detach is already right), and follow announcements from then on."""
    global _watcher
    from engines.runtime import get_bus, packages
    if _watcher is None:
        from app.ui.engine_bridge import watch_engine
        _watcher = QObject()
        watch_engine(_watcher, "runtime.*", _on_runtime)
    bus = get_bus()
    for item in packages():
        if not item.views:
            continue
        if bus.is_attached(item.engine_id) and item.engine_id not in _loaded:
            _load(item.engine_id)
        elif not bus.is_attached(item.engine_id) and item.engine_id in _loaded:
            api.unregister_engine_views(item.engine_id)
            _loaded.pop(item.engine_id, None)
# --- END [1.1] ---
