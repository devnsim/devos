# ==============================================================================
# FILE: app/ui/window_events.py
# OVERVIEW: One place that sees every mouse press in DevOS windows, without an application-wide event
#           filter. Title-bar dragging, the active tab pane and the active tile all listen here.
# PURPOSE:
#   1. watch(widget): filters the mouse events of that widget's top-level window at its QWindow (where
#      Qt delivers them before routing them to a child widget).
#   2. add_press_handler(handler): handler(target, local_position, event) -> True consumes the press
#      (title bar: start a window move / toggle maximise); remove_press_handler(handler) when its window goes.
#   3. pressed(QWidget) signal: the deepest widget under any press (active pane / active tile).
# CONTRACT:
#   - Requires: PySide6
#   - Exposes: WindowMouseHub, mouse_hub() (shared instance)
# NOTES:
#   - [2026-11-17] remove_press_handler(): windows come and go now (a tab can open in a new window).
#   - [2026-11-16] Created. PySide 6.11 segfaults when a Python event filter installed on the
#                  QApplication receives events for Qt WebEngine's internal QtQuick objects (it fails to
#                  wrap them), which made browser pages crash the app on focus. Filtering at the window
#                  level only ever hands Python QWindows and QWidgets, which wrap safely.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - press handler: callable(target QWidget, local QPoint, QMouseEvent) -> bool; True = press consumed.
# - target: The deepest visible widget under the mouse (QWidget.childAt), else the window itself.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. HUB
#    1.1 window_mouse_hub
# ==============================================================================

# ==============================================================================
# [1.1] window_mouse_hub
# ==============================================================================
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Signal
from PySide6.QtWidgets import QWidget

PRESS_EVENTS = (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick)


class WindowMouseHub(QObject):
    pressed = Signal(QWidget)                     # deepest widget under a mouse press, in any watched window

    def __init__(self) -> None:
        super().__init__()
        self._windows: dict[int, tuple[QObject, QWidget]] = {}    # id(QWindow) -> (QWindow, top-level widget)
        self._handlers: list = []

    def watch(self, widget: QWidget) -> None:
        """Watch the top-level window that holds `widget` (call once it is shown; repeats are ignored)."""
        top = widget.window()
        handle = top.windowHandle()
        if handle is None or id(handle) in self._windows:
            return
        key = id(handle)
        self._windows[key] = (handle, top)
        handle.installEventFilter(self)
        handle.destroyed.connect(lambda *_: self._windows.pop(key, None))

    def add_press_handler(self, handler) -> None:
        self._handlers.append(handler)

    def remove_press_handler(self, handler) -> None:
        if handler in self._handlers:
            self._handlers.remove(handler)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() not in PRESS_EVENTS:
            return False
        entry = self._windows.get(id(watched))
        if entry is None or entry[0] is not watched:
            return False
        top = entry[1]
        window_position = event.position().toPoint()
        target = top.childAt(window_position) or top
        local_position = target.mapFrom(top, window_position) if target is not top else window_position
        for handler in list(self._handlers):
            if handler(target, local_position, event):
                return True
        if event.type() == QEvent.Type.MouseButtonPress:
            self.pressed.emit(target)
        return False


_hub: WindowMouseHub | None = None


def mouse_hub() -> WindowMouseHub:
    global _hub
    if _hub is None:
        _hub = WindowMouseHub()
    return _hub
# --- END [1.1] ---
