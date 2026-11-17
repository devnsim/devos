# ==============================================================================
# FILE: app/ui/canvas/block_canvas.py
# OVERVIEW: The module-level canvas: a module's views as blocks the user can split, rearrange, resize,
#           close or replace - like pages do with modules, one level down.
# PURPOSE:
#   1. BlockSpec: one kind of block a module offers (id, title, description, factory).
#   2. BlockTile: a CanvasTile without a header. A small control cluster in its top-right corner (grip,
#      split, close) fades almost transparent and reappears on hover, or for a few seconds after the
#      block gets focus (click) or appears. The grip drags the block; clicking it opens a menu
#      (replace with another block, split down, close).
#   3. BlockCanvas: TileCanvas of BlockTiles; saves its layout through a callback (usually the module's
#      settings) and starts from the module's default layout.
# CONTRACT:
#   - Requires: app.ui.canvas.tile_canvas, app.ui.ui_tools, QSS selectors #block_canvas_panel,
#               #block_tile_panel
#   - Exposes: BlockSpec, BlockTile, reserve_controls_space(row_layout), BlockCanvas(specs, save_layout=None, saved_layout=None,
#              default_layout=None); blocks(kind=None) -> content widgets; show_block(kind) -> content widget;
#              plus TileCanvas API.
#              Block content widgets may implement block_state() -> dict and restore_block_state(dict).
# NOTES:
#   - [2026-11-17] show_block(kind): bring a view forward or open it beside the active block (open a record's form).
#   - [2026-11-15] The fading corner controls moved to tile_canvas.CornerControlsTile (module tiles use
#                  them too); BlockTile is now a thin subclass. reserve_controls_space is re-exported.
#   - [2026-11-15] reserve_controls_space(): blocks end their top row with it - the corner controls were
#                  covering (and catching clicks on) the waterfall's pair picker.
#   - [2026-11-15] Blocks with block_state() get their combos / sliders / toggles / fields watched, so a
#                  change in a block's own controls saves the layout (state included) automatically.
#   - [2026-11-15] Created for modules-as-canvases: modules stop hand-laying out their views (tabs,
#                  fixed splitters) and declare blocks instead.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - block: One view inside a module (e.g. a 3D view, a waterfall, a slice).
# - IDLE_OPACITY (float): Opacity of the corner controls when not hovered or focused (default: 0.12).
# - FLASH_MS (int | ms): How long the controls stay fully visible after focus (default: 2500).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. BLOCK_SPEC
#    2.1 block_spec
# 3. BLOCK_TILE
#    3.1 block_tile
# 4. BLOCK_CANVAS
#    4.1 block_canvas
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtWidgets import (
    QAbstractButton, QAbstractSlider, QAbstractSpinBox, QComboBox, QLineEdit, QWidget,
)

from app.ui.canvas.tile_canvas import CornerControlsTile, TileCanvas, reserve_controls_space  # noqa: F401 (re-export)
# --- END [1.1] ---


# ==============================================================================
# [2.1] block_spec
# ==============================================================================
@dataclass(frozen=True)
class BlockSpec:
    kind: str
    title: str
    description: str
    factory: Callable[[], QWidget]             # builds a new block content widget
# --- END [2.1] ---


# ==============================================================================
# [3.1] block_tile
# ==============================================================================
class BlockTile(CornerControlsTile):
    NOUN = "block"
    MIN_WIDTH_PX = 140
    MIN_HEIGHT_PX = 90
    EMPTY_TEXT = "Empty block — pick a view"

    def __init__(self, tile_id: int, mime_type: str, choices: list[tuple[str, str, str]]) -> None:
        super().__init__(tile_id, mime_type, choices, "block_tile_panel")
# --- END [3.1] ---


# ==============================================================================
# [4.1] block_canvas
# ==============================================================================
class BlockCanvas(TileCanvas):
    """A module's canvas of blocks (see file header)."""

    tile_class = BlockTile

    def __init__(self, specs: list[BlockSpec], save_layout: Callable[[dict], None] | None = None,
                 saved_layout: dict | None = None, default_layout: dict | None = None, parent: QWidget | None = None) -> None:
        super().__init__([(spec.kind, spec.title, spec.description) for spec in specs], "block_canvas_panel", parent)
        self.specs = {spec.kind: spec for spec in specs}
        self._save_layout = save_layout
        if not self.restore_layout(saved_layout) and not self.restore_layout(default_layout):
            self.add_tile(specs[0].kind if specs else None)
        if save_layout is not None:
            self.layout_changed.connect(lambda: self._save_layout(self.to_layout()))

    def create_content(self, kind: str, instance_id: str | None, state: dict | None):
        spec = self.specs[kind]
        widget = spec.factory()
        restore = getattr(widget, "restore_block_state", None)
        if state and callable(restore):
            restore(state)
        return widget, spec.title, None

    def _fill_tile(self, tile, kind: str, instance_id: str | None = None, state: dict | None = None) -> None:
        super()._fill_tile(tile, kind, instance_id, state)
        if callable(getattr(tile.content_widget, "block_state", None)):
            self._watch_state_controls(tile.content_widget)

    def _watch_state_controls(self, widget: QWidget) -> None:
        """A block's own controls (combos, sliders, toggles, fields) changing = its state changed: save.
        Generic, so blocks never have to remember to report changes."""
        save_soon = lambda *_: None if self._is_restoring else self._change_timer.start()     # noqa: E731
        for combo in widget.findChildren(QComboBox):
            combo.currentIndexChanged.connect(save_soon)
        for slider in widget.findChildren(QAbstractSlider):
            slider.valueChanged.connect(save_soon)
        for button in widget.findChildren(QAbstractButton):
            if button.isCheckable():
                button.toggled.connect(save_soon)
        for field in widget.findChildren(QLineEdit) + widget.findChildren(QAbstractSpinBox):
            field.editingFinished.connect(save_soon)

    def blocks(self, kind: str | None = None) -> list[QWidget]:
        """Content widgets of all blocks (optionally only one kind)."""
        return [tile.content_widget for tile in self.tiles.values()
                if tile.content_widget is not None and (kind is None or tile.kind == kind)]

    def show_block(self, kind: str) -> QWidget:
        """Bring a block of `kind` forward (active, controls flash), or open one beside the active block.
        Returns its content widget."""
        tile = next((tile for tile in self.tiles.values() if tile.kind == kind and tile.content_widget is not None), None)
        if tile is None:
            beside = self.active_tile if self.active_tile in self.tiles.values() else next(iter(self.tiles.values()), None)
            return self.add_tile(kind, beside=beside).content_widget
        self._set_active_tile(tile)
        tile.flash_controls()
        return tile.content_widget
# --- END [4.1] ---
