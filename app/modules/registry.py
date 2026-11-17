# ==============================================================================
# FILE: app/modules/registry.py
# OVERVIEW: The module library: every module app DevOS can place on a canvas, and how to create one.
# PURPOSE:
#   1. ModuleSpec describes one module type (id, title, category, description, where its view lives).
#   2. MODULE_LIBRARY lists them; module_specs() adds every template (Phase 5c: built-ins under the ids of the
#      modules they replaced, then the user's) - what empty tiles and Canvas cards offer.
#   3. ModuleContext is what a module receives when created: its instance id, the shared database
#      and its own persisted settings - the module's data layer.
#   4. create_module() imports the view lazily (heavy modules cost nothing until placed).
# CONTRACT:
#   - Requires: app.data.store.database (Database, ModuleSettings); each view class must accept a
#               keyword argument context: ModuleContext | None
#   - Exposes: ModuleSpec, ModuleContext, MODULE_LIBRARY, MODULE_BY_ID, module_specs(), module_spec(), create_module()
# NOTES:
#   - [2026-11-17] Phase 5c: Projects, Agents, Calendar, Database Editor, Spreadsheet, Graph Database and Graph are
#                  templates (app/templates/) under the same ids; module_specs() / module_spec() list them; the
#                  Data view is offered as "New view of an engine…"; a deleted template's tile says so.
#   - [2026-11-17] Data view module (engine plan Phase 2): a ViewHost as a page tile or a Canvas card.
#   - [2026-11-16] Added Spreadsheet, Graph Database (Kùzu), Map (GIS), Projects, Agents, CAD · Part Design,
#                  Comms, Software Monitor and Web App.
#   - [2026-11-16] Added the Database Editor (db_editor) under Data.
#   - [2026-11-15] Created. To add a module app: make app/modules/<name>/ with view.py (Qt UI, takes
#                  context=...), optional engine.py / store.py, then add one ModuleSpec here.
#   - [2026-11-15] Tools added: Graph, Notes (Markdown), Library; trading_chart is now titled "Financial
#                  Chart" (id unchanged so saved layouts keep working).
#   - [2026-11-15] create_module() runs as a logged step and never raises: a failing module becomes an
#                  error panel (#module_error_label) and the traceback goes to the System Log.
#   - [2026-11-15] Added the trading_chart module ("Markets" category).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - factory_path (str): "package.module:ClassName" of the module's view widget, imported on demand.
# - module app: A small app shown in a tile: view (UI) + engine (logic, maybe C++) + store (SQLite).
# - module_type_id (str): Stable id of a module type (or template); saved in layouts, so never rename one.
# - TEMPLATE_FACTORY (str): The factory every template tile is made by (app.templates.module:open_template).
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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from app.data.activity_log import get_activity_log

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
    ModuleSpec("trading_chart", "Financial Chart", "Tools",
               "Candlestick chart with indicators, drawings and backtests; ⚙ settings for data, theme, indicators.",
               "app.modules.trading_chart.view:TradingChartModule"),
    ModuleSpec("notes", "Notes (Markdown)", "Tools",
               "Write Markdown notes with a live preview; saved automatically in your library.",
               "app.modules.notes.view:NotesModule"),
    ModuleSpec("library", "Library", "Tools",
               "Everything DevOS keeps for you: notes, datasets, captures, CAD models - rename, delete, export.",
               "app.modules.library.view:LibraryModule"),
    ModuleSpec("rf_imaging", "RF Imaging · 2.5–6 GHz", "RF & sensing",
               "Live MIMO radar imaging: 3D reflectivity, motion tracking and material ID from sensor sweeps.",
               "app.modules.rf_imaging.view:RfInspectorModule"),
    ModuleSpec("wave_sim", "RF Wave Simulator · 1–10 GHz", "RF & sensing",
               "Physics engine: oscillator + antenna in an imported CAD scene, dielectric ray tracing, 4D CSI tensor.",
               "app.modules.wave_sim.view:WaveSimModule"),
    ModuleSpec("tensor_inspector", "Tensor Inspector (4D)", "Data",
               "Waterfall + 3D scatter blocks for any 4D NumPy tensor (live demo field by default).",
               "app.modules.tensor_inspector.view:TensorInspectorModule"),
    ModuleSpec("map", "Map (GIS)", "Data",
               "Slippy map with base layers, your places and GeoJSON layers from the library.",
               "app.modules.map.view:MapModule"),
    ModuleSpec("data_view", "New view of an engine…", "Data",
               "Any data of any engine as a table, grid, board, timeline, calendar, chart or form: choose the data, "
               "its fields and a filter.",
               "app.modules.data_view.view:DataViewModule"),
    ModuleSpec("cad", "CAD · Part Design", "Engineering",
               "CAD models in 3D with measurements; design parts step by step in the Part Design workbench.",
               "app.modules.cad.view:CadModule"),
    ModuleSpec("comms", "Comms", "Communication",
               "Email (IMAP / SMTP) with inbox, reader and compose; text and calls are placeholders for now.",
               "app.modules.comms.view:CommsModule"),
    ModuleSpec("monitor", "Software Monitor", "System",
               "Live CPU / memory / I/O, every process, inefficiency insights and DevOS's own footprint.",
               "app.modules.monitor.view:MonitorModule"),
    ModuleSpec("web_app", "Web App", "Web",
               "A site in a tile with its own container (separate logins): your bank, n8n, dashboards.",
               "app.modules.web_app.view:WebAppModule"),
)
MODULE_BY_ID = {spec.module_type_id: spec for spec in MODULE_LIBRARY}
TEMPLATE_FACTORY = "app.templates.module:open_template"


def module_specs(database: Database | None = None) -> tuple[ModuleSpec, ...]:
    """What a tile can hold: every template (built-in, then yours) and the modules not yet made templates."""
    from app.templates import all_templates
    templates = tuple(ModuleSpec(spec.template_id, spec.title, spec.category, spec.description, TEMPLATE_FACTORY)
                      for spec in all_templates(database))
    return templates + MODULE_LIBRARY


def module_spec(kind: str, database: Database | None = None) -> ModuleSpec | None:
    if kind in MODULE_BY_ID:
        return MODULE_BY_ID[kind]
    return next((spec for spec in module_specs(database) if spec.module_type_id == kind), None)
# --- END [3.1] ---

# ==============================================================================
# [3.2] create_module
# ==============================================================================
def create_module(module_type_id: str, database: Database, instance_id: str | None = None) -> tuple[QWidget, ModuleContext]:
    """Create a module's view. A new instance_id is registered unless an existing one is given
    (restoring a saved layout), in which case the module finds its saved settings and data.
    Never raises: a module that fails to start is logged (with traceback) and shown as an error panel."""
    spec = module_spec(module_type_id, database)
    log = get_activity_log()
    if spec is None:                                    # a deleted user template: its settings stay in the database
        label = QLabel(f"“{module_type_id}” no longer exists (its template was deleted). Its data is kept.",
                       objectName="module_error_label", alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        return label, ModuleContext(instance_id or "", module_type_id, database, ModuleSettings(database, instance_id or ""))
    is_new = instance_id is None
    instance_id = database.register_instance(module_type_id, spec.title, instance_id)
    context = ModuleContext(instance_id, module_type_id, database, ModuleSettings(database, instance_id))
    action = f"{'Start' if is_new else 'Restore'} {spec.title}"
    try:
        with log.step(f"module:{module_type_id}", action):
            view = spec.view_class()(context=context)
    except Exception as error:                          # logged by step(); keep the app running
        view = QLabel(f"Couldn't open {spec.title}.\n{type(error).__name__}: {error}\n\nSee the System Log for details.",
                      objectName="module_error_label", alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
    return view, context
# --- END [3.2] ---
