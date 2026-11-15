# ==============================================================================
# FILE: app/ui/canvas/module_canvas.py
# OVERVIEW: A page-filling grid where module apps tile, move and resize like the tab panes do.
# PURPOSE:
#   1. Holds module tiles (ModuleTile) in a tree of splitters that fills the page.
#   2. Dropping a tile on another tile's edge places it on that side (that tile splits);
#      dropping on the centre swaps the two. Every seam can be dragged to resize.
#   3. Library bar: pick a module from MODULE_LIBRARY and Add it beside the active tile; empty tiles
#      offer the same library in their own picker.
#   4. Saves the layout (tree, sizes, which module + instance in each tile) to SQLite and restores it.
# CONTRACT:
#   - Requires: app.ui.canvas.module_tile (ModuleTile), app.ui.widgets.tiling_layout,
#               app.modules.registry (MODULE_LIBRARY, MODULE_BY_ID, create_module),
#               app.data.store.database (Database), QSS selectors #module_canvas_panel,
#               #module_library_bar, #add_module_btn
#   - Exposes: ModuleCanvas(database=None, page_key=None, library=MODULE_LIBRARY) (QWidget);
#              add_module(...); add_library_module(module_type_id, ...); split_tile(); close_tile();
#              move_tile(); to_layout(); restore_layout(layout) -> bool; save_layout(); tiles; active_tile
# NOTES:
#   - [2026-11-15] Created. Same tiling engine as TabArea (tiling_layout.py), so modules and tab
#                  panes follow identical rules. The canvas is the grid; tiles are its blocks.
#   - [2026-11-15] Moves run on the next event-loop turn: a drop is delivered inside the source
#                  tile's QDrag.exec(), and reparenting the drag source mid-drag is unsafe.
#   - [2026-11-15] Moved from app/ui/modules/ to app/ui/canvas/ (module apps now live in app/modules/).
#   - [2026-11-15] Library bar, active tile, module picker in empty tiles, SQLite layout persistence.
#                  Closing a tile removes it from the layout but keeps its saved data (no silent deletes).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - active tile: The tile last clicked; "Add module" puts the new module beside it (to the right).
# - DEFAULT_TITLE_PREFIX (str): Empty modules are titled "<prefix> <n>" (default: "Module").
# - layout (dict): {"split": "h"|"v", "sizes": [...], "children": [...]} for a splitter, or
#   {"module": module_type_id | None, "instance_id": str | None, "title": str} for a tile.
# - SEAM_WIDTH_PX (int | px): Gap between tiles; also the grab area of each seam (default: 4).
# - tile: One ModuleTile, a leaf of the layout tree (see tiling_layout.py).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. MODULE_CANVAS
#    2.1 class_constants
#    2.2 init_canvas
#    2.3 library_bar
#    2.4 add_and_close
#    2.5 move_tiles
#    2.6 active_tile
#    2.7 layout_persistence
#    2.8 bookkeeping
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QLabel, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from app.data.store.database import Database
from app.modules.registry import MODULE_BY_ID, MODULE_LIBRARY, ModuleSpec, create_module
from app.ui.canvas.module_tile import ModuleTile
from app.ui.widgets.tiling_layout import (
    DROP_ZONE_RIGHT, EDGE_DROP_ZONES, insert_beside, new_splitter, normalize_tree, swap_leaves,
)
# --- END [1.1] ---


# ==============================================================================
# [2.1] class_constants
# ==============================================================================
class ModuleCanvas(QWidget):
    """Tiling grid of module apps, with a library bar and saved layouts."""

    SEAM_WIDTH_PX = 4
    DEFAULT_TITLE_PREFIX = "Module"
    SAVE_DELAY_MS = 400
# --- END [2.1] ---

# ==============================================================================
# [2.2] init_canvas
# ==============================================================================
    def __init__(
        self, database: Database | None = None, page_key: str | None = None,
        library: tuple[ModuleSpec, ...] = MODULE_LIBRARY, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, objectName="module_canvas_panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.database, self.page_key, self.library = database, page_key, library
        self.tiles: dict[int, ModuleTile] = {}
        self.active_tile: ModuleTile | None = None
        self._next_tile_id = 1
        self._is_restoring = False

        canvas_layout = QVBoxLayout(self)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(4)
        self._build_library_bar()
        canvas_layout.addWidget(self.module_library_bar)
        self.root_splitter = new_splitter(Qt.Orientation.Horizontal, self.SEAM_WIDTH_PX)
        canvas_layout.addWidget(self.root_splitter, 1)

        self.save_timer = QTimer(self, singleShot=True, interval=self.SAVE_DELAY_MS, timeout=self.save_layout)
        QApplication.instance().installEventFilter(self)     # click inside a tile -> active tile
# --- END [2.2] ---

# ==============================================================================
# [2.3] library_bar
# ==============================================================================
    def _build_library_bar(self) -> None:
        self.module_library_bar = QWidget(objectName="module_library_bar")
        bar_layout = QHBoxLayout(self.module_library_bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(6)
        self.module_library_combo = QComboBox(objectName="module_library_combo")
        last_category = None
        for spec in self.library:
            if spec.category != last_category:
                if last_category is not None:
                    self.module_library_combo.insertSeparator(self.module_library_combo.count())
                last_category = spec.category
            self.module_library_combo.addItem(f"{spec.title}", spec.module_type_id)
            self.module_library_combo.setItemData(
                self.module_library_combo.count() - 1, f"{spec.category} · {spec.description}", Qt.ItemDataRole.ToolTipRole
            )
        self.add_module_btn = QPushButton("+ Add module", objectName="add_module_btn")
        self.add_module_btn.setToolTip("Add the selected module beside the active tile")
        self.add_module_btn.clicked.connect(
            lambda: self.add_library_module(self.module_library_combo.currentData(), beside_tile=self.active_tile)
        )
        self.module_library_hint_label = QLabel(objectName="module_library_hint_label")
        self.module_library_combo.currentIndexChanged.connect(self._update_library_hint)
        bar_layout.addWidget(QLabel("Module library", objectName="module_library_label"))
        bar_layout.addWidget(self.module_library_combo)
        bar_layout.addWidget(self.add_module_btn)
        bar_layout.addWidget(self.module_library_hint_label, 1)
        self._update_library_hint()

    def _update_library_hint(self) -> None:
        spec = MODULE_BY_ID.get(self.module_library_combo.currentData())
        self.module_library_hint_label.setText(spec.description if spec else "")

    def _module_choices(self) -> list[tuple[str, str, str]]:
        return [(spec.module_type_id, spec.title, spec.description) for spec in self.library]
# --- END [2.3] ---

# ==============================================================================
# [2.4] add_and_close
# ==============================================================================
    def add_module(
        self, title_text: str | None = None, content_widget: QWidget | None = None,
        beside_tile: ModuleTile | None = None, drop_zone: str = DROP_ZONE_RIGHT,
    ) -> ModuleTile:
        """Add a tile (empty, or holding content_widget). With beside_tile, it takes half of that
        tile on the drop_zone side; otherwise it is appended to the top-level row."""
        tile = self._new_tile(title_text, content_widget)
        self._place_tile(tile, beside_tile, drop_zone)
        return tile

    def add_library_module(
        self, module_type_id: str, beside_tile: ModuleTile | None = None, drop_zone: str = DROP_ZONE_RIGHT,
        instance_id: str | None = None,
    ) -> ModuleTile:
        """Create a module app from the library (with its data context) and place it."""
        view, context = self._create_view(module_type_id, instance_id)
        tile = self._new_tile(MODULE_BY_ID[module_type_id].title, view, module_type_id, context.instance_id)
        self._place_tile(tile, beside_tile, drop_zone)
        self._set_active_tile(tile)
        return tile

    def split_tile(self, tile: ModuleTile, drop_zone: str = DROP_ZONE_RIGHT) -> ModuleTile:
        """New empty module (with a picker) on one side of tile."""
        return self.add_module(beside_tile=tile, drop_zone=drop_zone)

    def close_tile(self, tile: ModuleTile) -> None:
        """Remove a tile from the layout. The last tile can't be closed. Saved data is kept."""
        if len(self.tiles) <= 1 or tile.tile_id not in self.tiles:
            return
        del self.tiles[tile.tile_id]
        if self.active_tile is tile:
            self.active_tile = None
        tile.hide()
        tile.setParent(None)                        # leaves its splitter
        normalize_tree(self.root_splitter)
        self._after_structure_change()
        tile.deleteLater()

    def _fill_tile(self, tile: ModuleTile, module_type_id: str) -> None:
        """An empty tile's picker chose a module: create it inside that tile."""
        view, context = self._create_view(module_type_id, None)
        tile.set_content(view, MODULE_BY_ID[module_type_id].title, module_type_id, context.instance_id)
        self._set_active_tile(tile)
        self._schedule_save()

    def _create_view(self, module_type_id: str, instance_id: str | None):
        if self.database is None:
            raise RuntimeError("ModuleCanvas needs a database to create library modules")
        return create_module(module_type_id, self.database, instance_id)

    def _place_tile(self, tile: ModuleTile, beside_tile: ModuleTile | None, drop_zone: str) -> None:
        if beside_tile is None or beside_tile.tile_id not in self.tiles or beside_tile is tile:
            self.root_splitter.addWidget(tile)
        else:
            insert_beside(beside_tile, tile, drop_zone, self.SEAM_WIDTH_PX)
        self._after_structure_change()
# --- END [2.4] ---

# ==============================================================================
# [2.5] move_tiles
# ==============================================================================
    def move_tile(self, source_tile: ModuleTile, target_tile: ModuleTile, drop_zone: str) -> None:
        """Centre: swap the two tiles. Edge: take source out and tile it on that side of target."""
        if source_tile is target_tile:
            return
        if drop_zone not in EDGE_DROP_ZONES:
            swap_leaves(source_tile, target_tile)
        else:
            source_tile.setParent(None)                 # leaves its splitter (and hides)
            normalize_tree(self.root_splitter)          # target_tile is a leaf, so it survives the tidy
            insert_beside(target_tile, source_tile, drop_zone, self.SEAM_WIDTH_PX)
            source_tile.show()
        self._after_structure_change()

    def _on_module_dropped(self, target_tile: ModuleTile, source_tile_id: int, drop_zone: str) -> None:
        source_tile = self.tiles.get(source_tile_id)
        if source_tile is None:
            return
        # Deferred: see NOTES (the drop arrives inside the source tile's drag loop).
        QTimer.singleShot(0, lambda: self.move_tile(source_tile, target_tile, drop_zone))
# --- END [2.5] ---

# ==============================================================================
# [2.6] active_tile
# ==============================================================================
    def _set_active_tile(self, tile: ModuleTile | None) -> None:
        self.active_tile = tile
        for each_tile in self.tiles.values():
            each_tile.set_is_active(each_tile is tile and len(self.tiles) > 1)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
            widget = watched
            while widget is not None and not isinstance(widget, ModuleTile):
                widget = widget.parentWidget()
            if widget is not None and widget.tile_id in self.tiles and self.tiles[widget.tile_id] is widget:
                if widget is not self.active_tile:
                    self._set_active_tile(widget)
        return False
# --- END [2.6] ---

# ==============================================================================
# [2.7] layout_persistence
# ==============================================================================
    def to_layout(self) -> dict:
        def describe(widget: QWidget) -> dict:
            if isinstance(widget, QSplitter):
                return {
                    "split": "h" if widget.orientation() == Qt.Orientation.Horizontal else "v",
                    "sizes": widget.sizes(),
                    "children": [describe(widget.widget(index)) for index in range(widget.count())],
                }
            return {"module": widget.module_type_id, "instance_id": widget.instance_id,
                    "title": widget.module_title_label.text()}
        return describe(self.root_splitter)

    def restore_layout(self, layout: dict) -> bool:
        """Rebuild tiles from a saved layout. Returns False (canvas left empty) if it can't be used."""
        if not layout or "children" not in layout or not layout["children"]:
            return False
        self._is_restoring = True
        try:
            self._build_from_layout(self.root_splitter, layout)
        except (KeyError, TypeError, ValueError) as error:
            print(f"[DevOS] could not restore layout '{self.page_key}': {error}")
            for tile in list(self.tiles.values()):
                tile.setParent(None)
                tile.deleteLater()
            self.tiles.clear()
            return False
        finally:
            self._is_restoring = False
        normalize_tree(self.root_splitter)
        self._after_structure_change(save=False)
        return bool(self.tiles)

    def _build_from_layout(self, splitter: QSplitter, layout: dict) -> None:
        splitter.setOrientation(Qt.Orientation.Horizontal if layout["split"] == "h" else Qt.Orientation.Vertical)
        for child in layout["children"]:
            if "children" in child:
                child_splitter = new_splitter(Qt.Orientation.Horizontal, self.SEAM_WIDTH_PX)
                splitter.addWidget(child_splitter)
                self._build_from_layout(child_splitter, child)
            elif child.get("module") in MODULE_BY_ID:
                view, context = self._create_view(child["module"], child.get("instance_id"))
                splitter.addWidget(self._new_tile(child.get("title"), view, child["module"], context.instance_id))
            else:
                splitter.addWidget(self._new_tile(child.get("title"), None))     # empty, or unknown module
        if layout.get("sizes") and len(layout["sizes"]) == splitter.count():
            splitter.setSizes(layout["sizes"])

    def save_layout(self) -> None:
        if self.database is not None and self.page_key and self.tiles:
            self.database.save_layout(self.page_key, self.to_layout())

    def load_saved_layout(self) -> bool:
        """Restore this page's saved layout, if any."""
        if self.database is None or not self.page_key:
            return False
        return self.restore_layout(self.database.load_layout(self.page_key))

    def _schedule_save(self) -> None:
        if not self._is_restoring:
            self.save_timer.start()

    def _hook_splitters(self, splitter: QSplitter) -> None:
        """Save after seam drags too (each splitter connected once)."""
        if not splitter.property("is_save_hooked"):
            splitter.splitterMoved.connect(self._schedule_save)
            splitter.setProperty("is_save_hooked", True)
        for index in range(splitter.count()):
            child = splitter.widget(index)
            if isinstance(child, QSplitter):
                self._hook_splitters(child)
# --- END [2.7] ---

# ==============================================================================
# [2.8] bookkeeping
# ==============================================================================
    def _new_tile(
        self, title_text: str | None, content_widget: QWidget | None,
        module_type_id: str | None = None, instance_id: str | None = None,
    ) -> ModuleTile:
        tile_id = self._next_tile_id
        self._next_tile_id += 1
        tile = ModuleTile(
            tile_id, title_text or f"{self.DEFAULT_TITLE_PREFIX} {tile_id}", content_widget,
            module_type_id, instance_id, self._module_choices() if self.database is not None else None,
        )
        tile.split_requested.connect(lambda: self.split_tile(tile, DROP_ZONE_RIGHT))
        tile.close_requested.connect(lambda: self.close_tile(tile))
        tile.module_dropped.connect(
            lambda source_tile_id, drop_zone: self._on_module_dropped(tile, source_tile_id, drop_zone)
        )
        tile.module_chosen.connect(lambda module_type_id: self._fill_tile(tile, module_type_id))
        self.tiles[tile_id] = tile
        return tile

    def _after_structure_change(self, save: bool = True) -> None:
        can_close = len(self.tiles) > 1
        for tile in self.tiles.values():
            tile.set_can_close(can_close)
        self._set_active_tile(self.active_tile if self.active_tile in self.tiles.values() else None)
        self._hook_splitters(self.root_splitter)
        if save:
            self._schedule_save()
# --- END [2.8] ---
