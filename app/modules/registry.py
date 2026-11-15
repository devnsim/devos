# ==============================================================================
# FILE: app/modules/registry.py
# OVERVIEW: The module library: every module app DevOS can place on a canvas, and how to create one.
# PURPOSE:
#   1. ModuleSpec describes one module type (id, title, category, description, where its view lives).
#   2. MODULE_LIBRARY lists them; the canvas "Add module" dropdown and empty tiles read it.
#   3. ModuleContext is what a module receives when created: its instance id, the shared database
#      and its own persisted settings - the module's data layer.
#   4. create_module() imports the view lazily (heavy modules cost nothing until placed).
# CONTRACT:
#   - Requires: app.data.store.database (Database, ModuleSettings); each view class must accept a
#               keyword argument context: ModuleContext | None
#   - Exposes: ModuleSpec, ModuleContext, MODULE_LIBRARY, MODULE_BY_ID, create_module()
# NOTES:
#   - [2026-11-15] Created. To add a module app: make app/modules/<name>/ with view.py (Qt UI, takes
#                  context=...), optional engine.py / store.py, then add one ModuleSpec here.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - factory_path (str): "package.module:ClassName" of the module's view widget, imported on demand.
# - module app: A small app shown in a tile: view (UI) + engine (logic, maybe C++) + store (SQLite).
# - module_type_id (str): Stable id of a module type; saved in layouts, so never rename one.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. TYPES
#    2.1 module_context
#    2.2 module_spec
# 3. LIBRARY
#    3.1 module_library
#    3.2 create_module
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import importlib
from dataclasses import dataclass

from PySide6.QtWidgets import QWidget

from app.data.store.database import Database, ModuleSettings
# --- END [1.1] ---


# ==============================================================================
# [2.1] module_context
# ==============================================================================
@dataclass
class ModuleContext:
    """Handed to a module when it is created: who it is and where its data lives."""

    instance_id: str
    module_type_id: str
    database: Database
    settings: ModuleSettings
# --- END [2.1] ---

# ==============================================================================
# [2.2] module_spec
# ==============================================================================
@dataclass(frozen=True)
class ModuleSpec:
    module_type_id: str
    title: str
    category: str
    description: str
    factory_path: str

    def view_class(self) -> type[QWidget]:
        module_path, class_name = self.factory_path.split(":")
        return getattr(importlib.import_module(module_path), class_name)
# --- END [2.2] ---


# ==============================================================================
# [3.1] module_library
# ==============================================================================
MODULE_LIBRARY: tuple[ModuleSpec, ...] = (
    ModuleSpec(
        "rf_imaging", "RF Imaging · 2.5–6 GHz", "RF & sensing",
        "Live MIMO radar imaging: 3D reflectivity, motion tracking and material ID from sensor sweeps.",
        "app.modules.rf_imaging.view:RfInspectorModule",
    ),
    ModuleSpec(
        "wave_sim", "RF Wave Simulator · 1–10 GHz", "RF & sensing",
        "Physics engine: oscillator + antenna in an imported CAD scene, dielectric ray tracing, 4D CSI tensor.",
        "app.modules.wave_sim.view:WaveSimModule",
    ),
    ModuleSpec(
        "tensor_inspector", "Tensor Inspector (4D)", "Data",
        "Waterfall + 3D scatter viewer for any 4D NumPy tensor (live demo field by default).",
        "app.modules.tensor_inspector.view:TensorInspectorModule",
    ),
    ModuleSpec(
        "calendar", "Calendar", "Planning",
        "Month calendar; remembers the selected day.",
        "app.modules.calendar.view:CalendarModule",
    ),
)
MODULE_BY_ID = {spec.module_type_id: spec for spec in MODULE_LIBRARY}
# --- END [3.1] ---

# ==============================================================================
# [3.2] create_module
# ==============================================================================
def create_module(module_type_id: str, database: Database, instance_id: str | None = None) -> tuple[QWidget, ModuleContext]:
    """Create a module's view. A new instance_id is registered unless an existing one is given
    (restoring a saved layout), in which case the module finds its saved settings and data."""
    spec = MODULE_BY_ID[module_type_id]
    instance_id = database.register_instance(module_type_id, spec.title, instance_id)
    context = ModuleContext(instance_id, module_type_id, database, ModuleSettings(database, instance_id))
    return spec.view_class()(context=context), context
# --- END [3.2] ---
