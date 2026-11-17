# ==============================================================================
# FILE: app/views/channels.py
# OVERVIEW: Channels - shared cursors that link the views of a page: what is selected (per source), and
#           later the time cursor, filter and focus. Page-wide by default; a view can unlink itself.
# PURPOSE:
#   1. ChannelHub: named values with a changed(name, value) signal; one per page (CanvasPage,
#      InfiniteCanvasPage set `channel_hub`), so every view on a page - in any module, block or card - follows.
#   2. hub_for(widget): the hub of the page a widget is on (walking up its parents); a widget outside any
#      page (a standalone module, a test) gets a hub on its top-level widget.
#   3. selection_channel(source): the channel name for "what is selected in this source" (selecting a project
#      never clears the selected agent).
# CONTRACT:
#   - Requires: PySide6, app.views.api.source_key
#   - Exposes: ChannelHub, hub_for(widget), selection_channel(source), HUB_ATTRIBUTE
# NOTES:
#   - [2026-11-17] Created in Phase 2 of the engine plan (decision: page-wide, per-view unlink).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - channel (str): "selection:<source key>" today; "time", "filter", "focus" later.
# - HUB_ATTRIBUTE (str): Attribute a page (or any container) sets to share one hub with everything inside it.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. CHANNELS
#    1.1 hub
# ==============================================================================

# ==============================================================================
# [1.1] hub
# ==============================================================================
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from app.views.api import source_key

HUB_ATTRIBUTE = "channel_hub"


class ChannelHub(QObject):
    changed = Signal(str, object)                    # channel name, new value

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._values: dict[str, object] = {}

    def get(self, name: str, default=None):
        return self._values.get(name, default)

    def set(self, name: str, value) -> None:
        if self._values.get(name) != value:
            self._values[name] = value
            self.changed.emit(name, value)


def hub_for(widget: QWidget) -> ChannelHub:
    node = widget
    while node is not None:
        hub = getattr(node, HUB_ATTRIBUTE, None)
        if isinstance(hub, ChannelHub):
            return hub
        if node.parentWidget() is None:
            break
        node = node.parentWidget()
    hub = ChannelHub(node)                           # outside any page: the top-level widget shares one
    setattr(node, HUB_ATTRIBUTE, hub)
    return hub


def selection_channel(source: dict) -> str:
    return f"selection:{source_key(source)}"
# --- END [1.1] ---
