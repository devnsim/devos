# ==============================================================================
# FILE: app/modules/base.py
# OVERVIEW: ModuleView - the base class every module app's view builds on (build once, reuse).
# PURPOSE:
#   1. Settings: setting() / save_setting() that work with or without a ModuleContext.
#   2. Live loop: a timer that runs only while the module is visible and live (pauses with hidden tabs,
#      parked pages, collapsed tiles), plus a standard Live / Pause toggle button.
#   3. Blocks: build_block_canvas() gives the module a BlockCanvas whose layout is saved in the module's
#      settings, so its views can be split, rearranged, resized, closed and replaced.
# CONTRACT:
#   - Requires: app.modules.registry.ModuleContext (optional), app.ui.canvas.block_canvas,
#               QSS selector #live_toggle_btn
#   - Exposes: ModuleView(parent=None, context=None, object_name="module_view") with setting(),
#              save_setting(), setup_live(interval_ms, on_tick), set_live(), live_toggle_btn,
#              build_block_canvas(specs, default_layout) -> BlockCanvas, is_live
# NOTES:
#   - [2026-11-15] Created in the health pass: four modules each re-implemented settings helpers, the
#                  live timer, show/hide gating and the toggle button.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - live: The module updates itself on a timer (live data, simulations).
# - BLOCKS_SETTING (str): Settings key holding the module's block layout (default: "blocks").
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. MODULE_VIEW
#    1.1 init_and_settings
#    1.2 live_loop
#    1.3 blocks
# ==============================================================================

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QToolButton, QWidget

from app.ui.canvas.block_canvas import BlockCanvas, BlockSpec


# ==============================================================================
# [1.1] init_and_settings
# ==============================================================================
class ModuleView(QWidget):
    BLOCKS_SETTING = "blocks"

    def __init__(self, parent: QWidget | None = None, context=None, object_name: str = "module_view") -> None:
        super().__init__(parent, objectName=object_name)
        self.context = context
        self.is_live = False
        self.live_timer: QTimer | None = None
        self.live_toggle_btn: QToolButton | None = None

    def setting(self, key: str, default: Any = None) -> Any:
        return self.context.settings.get(key, default) if self.context is not None else default

    def save_setting(self, key: str, value: Any) -> None:
        if self.context is not None:
            self.context.settings.set(key, value)
# --- END [1.1] ---

# ==============================================================================
# [1.2] live_loop
# ==============================================================================
    def setup_live(self, interval_ms: int, on_tick: Callable[[], None], start_live: bool = True,
                   live_text: str = "❚❚ Pause", paused_text: str = "▶ Live") -> QToolButton:
        """Create the live timer and its toggle button (returned, for the module's toolbar)."""
        self.live_timer = QTimer(self, interval=interval_ms, timeout=on_tick)
        self._live_texts = (live_text, paused_text)
        self.live_toggle_btn = QToolButton(objectName="live_toggle_btn", checkable=True)
        self.live_toggle_btn.toggled.connect(self.set_live)
        self.set_live(start_live)
        return self.live_toggle_btn

    def set_live(self, is_live: bool) -> None:
        self.is_live = is_live
        if self.live_toggle_btn is not None:
            self.live_toggle_btn.blockSignals(True)
            self.live_toggle_btn.setChecked(is_live)
            self.live_toggle_btn.blockSignals(False)
            self.live_toggle_btn.setText(self._live_texts[0] if is_live else self._live_texts[1])
        self._update_live_timer()

    def can_run_live(self) -> bool:
        """Extra condition for the timer (override, e.g. "engine ready")."""
        return True

    def _update_live_timer(self) -> None:
        if self.live_timer is None:
            return
        if self.is_live and self.isVisible() and self.can_run_live():
            self.live_timer.start()
        else:
            self.live_timer.stop()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_live_timer()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._update_live_timer()
# --- END [1.2] ---

# ==============================================================================
# [1.3] blocks
# ==============================================================================
    def build_block_canvas(self, specs: list[BlockSpec], default_layout: dict | None = None) -> BlockCanvas:
        """A BlockCanvas whose layout is saved in this module's settings."""
        return BlockCanvas(
            specs, save_layout=lambda layout: self.save_setting(self.BLOCKS_SETTING, layout),
            saved_layout=self.setting(self.BLOCKS_SETTING), default_layout=default_layout,
        )
# --- END [1.3] ---
