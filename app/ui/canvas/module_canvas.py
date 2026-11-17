# ==============================================================================
# FILE: app/ui/canvas/module_canvas.py
# OVERVIEW: The page-level canvas: module apps as header-less tiles. A new page shows one empty tile
#           with a "what goes here" dropdown; split from the corner icon to add more.
# PURPOSE:
#   1. ModuleTile: a CornerControlsTile (fading grip / split / close in the corner, no title bar).
#   2. ModuleCanvas: a TileCanvas whose tiles hold module apps from module_specs() (templates and modules, picked in empty tiles).
#   3. Saves the layout (tree, sizes, module + instance per tile) to SQLite and restores it.
# CONTRACT:
#   - Requires: app.ui.canvas.tile_canvas (CornerControlsTile, TileCanvas), app.ui.toolbars.ToolDock, app.modules.registry
#               (module_specs, module_spec, create_module), app.data.store.database.Database,
#               app.data.activity_log, QSS selectors #module_canvas_panel, #module_tile_panel
#   - Exposes: ModuleCanvas(database=None, page_key=None, library=None); add_library_module();
#              add_module() (empty tile); load_saved_layout(); save_layout(); plus TileCanvas API (emptied); ModuleTile;
#              dock_module_tools(tile), add_toolbar_menu(menu, tool_dock), library_choices(library),
#              create_library_module(kind, database, instance_id) (shared with the infinite canvas's cards)
# NOTES:
#   - [2026-11-17] Phase 5c: the picker offers templates (module_specs()); choices are read again for every new tile.
#   - [2026-11-17] Tool-dock wiring, the "Add toolbar" menu, the picker choices and module creation moved into
#                  helpers so the infinite canvas's module cards share them.
#   - [2026-11-16] Module tiles hold their module in a ToolDock: grip menu → Add toolbar (top / bottom / left /
#                  right / floating); trays are saved in the module's settings.
#   - [2026-11-16] The last module tile can be closed (keeps_last_tile = False): the page then shows its
#                  "Create New" panel again and the saved layout is removed. An empty saved layout is
#                  treated as "nothing saved", without a warning.
#   - [2026-11-15] Created. Same tiling engine as TabArea (tiling_layout.py).
#   - [2026-11-15] Closing a tile removes it from the layout but keeps its saved data (no silent deletes).
#   - [2026-11-15] Health pass: generic tiling moved to tile_canvas.py (shared with block canvases);
#                  module_tile.py merged in here as ModuleTile.
#   - [2026-11-15] Page template simplified: library bar ("Module library", "+ Add module", description)
#                  and the module title bar removed. Tiles never announce "Module 1"; empty tiles show
#                  only the module dropdown; the split icon stays in the (fading) corner controls.
#                  Opening / closing modules is announced in the activity log.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - active tile: The tile last clicked (its corner controls flash).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. MODULE_TILE
#    2.1 module_tile
# 3. MODULE_CANVAS
#    3.1 init
#    3.2 modules
#    3.3 persistence
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from PySide6.QtWidgets import QMenu, QWidget

from app.data.activity_log import get_activity_log
from app.data.store.database import Database
from app.modules.registry import ModuleSpec, create_module, module_spec, module_specs
from app.ui.canvas.tile_canvas import CanvasTile, CornerControlsTile, TileCanvas
from app.ui.toolbars import SIDE_TITLES, SIDES, ToolDock
from app.ui.widgets.tiling_layout import DROP_ZONE_RIGHT
# --- END [1.1] ---


# ==============================================================================
# [2.1] module_tile
# ==============================================================================
def library_choices(library: tuple[ModuleSpec, ...] | None = None, database: Database | None = None) -> list[tuple[str, str, str]]:
    """(kind, title, description) of every template and module: what empty tiles / cards offer (asked again for
    each new tile, so a template saved a moment ago is there)."""
    return [(spec.module_type_id, spec.title, spec.description) for spec in (library or module_specs(database))]


def create_library_module(kind: str, database: Database, instance_id: str | None) -> tuple[QWidget, str, str]:
    """A module app with its data context: (view, title, instance_id) - the shape create_content returns."""
    view, context = create_module(kind, database, instance_id)
    spec = module_spec(kind, database)
    return view, spec.title if spec else kind, context.instance_id


def dock_module_tools(tile: CanvasTile) -> ToolDock:
    """Put a ToolDock in the tile's body (hidden until a module arrives) and make it the content's place, so
    the user can add toolbars around the module. Call right after the tile is built."""
    tool_dock = ToolDock(tile.body_panel)
    tool_dock.hide()                                   # the picker shows first
    tile.body_layout.addWidget(tool_dock)
    tile.body_layout = tool_dock.center_layout
    return tool_dock


def add_toolbar_menu(menu: QMenu, tool_dock: ToolDock) -> None:
    """An "Add toolbar" submenu (top / bottom / left / right / floating) for a module's tool dock."""
    toolbar_menu = menu.addMenu("Add toolbar")
    toolbar_menu.setEnabled(tool_dock.can_hold_tools)
    for side in SIDES:
        toolbar_menu.addAction(SIDE_TITLES[side]).triggered.connect(
            lambda _checked=False, side=side: tool_dock.add_tray(side))


class ModuleTile(CornerControlsTile):
    """A module tile: no title bar; fading corner controls (grip, split, close) over the module; the
    module sits in a ToolDock so the user can add toolbars around it (grip menu → Add toolbar)."""

    NOUN = "module"
    EMPTY_TEXT = "What goes here?"

    def __init__(self, tile_id: int, mime_type: str, choices: list[tuple[str, str, str]]) -> None:
        super().__init__(tile_id, mime_type, choices, "module_tile_panel")
        self.tool_dock = dock_module_tools(self)

    def set_content(self, content_widget: QWidget, title: str, kind: str, instance_id: str | None = None) -> None:
        super().set_content(content_widget, title, kind, instance_id)
        self.tool_dock.bind(content_widget)
        self.tool_dock.show()

    def extend_menu(self, menu: QMenu) -> None:
        add_toolbar_menu(menu, self.tool_dock)
# --- END [2.1] ---


# ==============================================================================
# [3.1] init
# ==============================================================================
class ModuleCanvas(TileCanvas):
    """Page canvas of module apps (see file header)."""

    tile_class = ModuleTile
    keeps_last_tile = False                    # an empty page shows "Create New" (canvas_page.py)

    def __init__(self, database: Database | None = None, page_key: str | None = None,
                 library: tuple[ModuleSpec, ...] | None = None, parent: QWidget | None = None) -> None:
        choices = library_choices(library, database) if database else []
        super().__init__(choices, "module_canvas_panel", parent)
        self.database, self.page_key, self.library = database, page_key, library
        self.layout_changed.connect(self.save_layout)
# --- END [3.1] ---

# ==============================================================================
# [3.2] modules
# ==============================================================================
    def _new_tile(self):
        if self.database is not None and self.library is None:
            self.choices = library_choices(None, self.database)       # templates saved since the page opened
        return super()._new_tile()

    def create_content(self, kind: str, instance_id: str | None, state: dict | None):
        if self.database is None:
            raise RuntimeError("ModuleCanvas needs a database to create library modules")
        return create_library_module(kind, self.database, instance_id)

    def add_library_module(self, module_type_id: str, beside: CanvasTile | None = None,
                           drop_zone: str = DROP_ZONE_RIGHT, instance_id: str | None = None) -> CanvasTile:
        """Create a module app from the library (with its data context) and place it."""
        return self.add_tile(module_type_id, beside, drop_zone, instance_id)

    def add_module(self, beside: CanvasTile | None = None, drop_zone: str = DROP_ZONE_RIGHT) -> CanvasTile:
        """Empty module tile with the module dropdown."""
        return self.add_tile(None, beside, drop_zone)

    def close_tile(self, tile: CanvasTile) -> None:
        if tile.title:
            get_activity_log().info(f"page:{self.page_key}", f"Closed module “{tile.title}” (its data is kept)")
        super().close_tile(tile)
# --- END [3.2] ---

# ==============================================================================
# [3.3] persistence
# ==============================================================================
    def save_layout(self) -> None:
        if self.database is None or not self.page_key:
            return
        if self.tiles:
            self.database.save_layout(self.page_key, self.to_layout())
            get_activity_log().debug(f"page:{self.page_key}", "Layout saved")
        else:
            self.database.delete_layout(self.page_key)

    def load_saved_layout(self) -> bool:
        """Restore this page's saved layout, if any."""
        if self.database is None or not self.page_key:
            return False
        layout = self.database.load_layout(self.page_key)
        if not layout or not layout.get("children"):
            return False
        is_restored = self.restore_layout(layout)
        if not is_restored:
            get_activity_log().warning(f"page:{self.page_key}", "Saved layout could not be restored; starting empty")
        return is_restored
# --- END [3.3] ---
