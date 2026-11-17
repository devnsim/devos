# ==============================================================================
# FILE: app/ui/bus_inspector.py
# OVERVIEW: Bus inspector - a window that shows how engines are doing (requests, errors, timeouts, latency,
#           queue depth, running or not) and the live messages on the bus.
# PURPOSE:
#   1. Engines table refreshed twice a second from bus.stats().
#   2. Live messages through a bus tap - installed only while the window is open, so a closed inspector
#      costs nothing; Pause, Clear and a topic filter.
# CONTRACT:
#   - Requires: engines.runtime.get_bus (stats, add_tap, remove_tap), PySide6, app.ui.ui_tools,
#               QSS selector #bus_inspector_window
#   - Exposes: BusInspector(parent=None), open_bus_inspector(parent) -> BusInspector (one per app)
# NOTES:
#   - [2026-11-17] Created in Phase 3 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - MAX_ROWS (int): Messages kept in the list (oldest dropped; default: 1000).
# - tap record (dict): time, kind, topic, id, ms (latency of answers), error.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. BUS_INSPECTOR
#    1.1 window
#    1.2 refresh
# ==============================================================================

# ==============================================================================
# [1.1] window
# ==============================================================================
from __future__ import annotations

import collections
import threading
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app.ui.ui_tools import control_label
from engines.runtime import get_bus, package

MAX_ROWS = 1000


class BusInspector(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="bus_inspector_window")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowTitle("Bus inspector")
        self.resize(900, 600)
        self._incoming: collections.deque = collections.deque(maxlen=MAX_ROWS)
        self._lock = threading.Lock()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Engines", objectName="settings_section_label"))
        self.engines_tree = QTreeWidget()
        self.engines_tree.setHeaderLabels(["Engine", "State", "Requests", "Errors", "Timeouts", "Avg ms", "Last ms",
                                           "Max ms", "Queue"])
        self.engines_tree.setRootIsDecorated(False)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.engines_tree)
        messages_panel = QWidget()
        messages_layout = QVBoxLayout(messages_panel)
        messages_layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(control_label("Live messages"))
        self.filter_edit = QLineEdit(placeholderText="Filter by topic (e.g. records.)", clearButtonEnabled=True)
        row.addWidget(self.filter_edit, 1)
        self.pause_check = QCheckBox("Pause")
        row.addWidget(self.pause_check)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: self.messages_tree.clear())
        row.addWidget(clear_btn)
        messages_layout.addLayout(row)
        self.messages_tree = QTreeWidget()
        self.messages_tree.setHeaderLabels(["Time", "Kind", "Topic", "ms", "Error"])
        self.messages_tree.setRootIsDecorated(False)
        messages_layout.addWidget(self.messages_tree, 1)
        splitter.addWidget(messages_panel)
        splitter.setSizes([200, 400])
        layout.addWidget(splitter, 1)
        self._stats_timer = QTimer(self, interval=500, timeout=self.refresh_engines)
        self._messages_timer = QTimer(self, interval=200, timeout=self.drain_messages)

    def _on_tap(self, record: dict) -> None:            # any thread
        with self._lock:
            self._incoming.append(record)

    def showEvent(self, event) -> None:                                  # noqa: N802
        super().showEvent(event)
        get_bus().add_tap(self._on_tap)
        self._stats_timer.start()
        self._messages_timer.start()
        self.refresh_engines()

    def hideEvent(self, event) -> None:                                  # noqa: N802
        super().hideEvent(event)
        get_bus().remove_tap(self._on_tap)
        self._stats_timer.stop()
        self._messages_timer.stop()
# --- END [1.1] ---

# ==============================================================================
# [1.2] refresh
# ==============================================================================
    def refresh_engines(self) -> None:
        bus = get_bus()
        self.engines_tree.clear()
        for engine_id, stats in bus.stats().items():
            state = "running" if stats["running"] else "attached" if stats["attached"] else "detached"
            item = package(engine_id)
            title = item.title if item else engine_id
            self.engines_tree.addTopLevelItem(QTreeWidgetItem([
                title, state, str(stats["requests"]), str(stats["errors"]), str(stats["timeouts"]),
                f"{stats['average_ms']:.1f}", f"{stats['last_ms']:.1f}", f"{stats['max_ms']:.1f}", str(stats["queue_depth"])]))
        for column in range(9):
            self.engines_tree.resizeColumnToContents(column)

    def drain_messages(self) -> None:
        with self._lock:
            records, self._incoming = list(self._incoming), collections.deque(maxlen=MAX_ROWS)
        if self.pause_check.isChecked() or not records:
            return
        needle = self.filter_edit.text().strip()
        for record in records:
            if needle and needle not in record["topic"]:
                continue
            ms = f"{record['ms']:.1f}" if record.get("ms") is not None else ""
            self.messages_tree.addTopLevelItem(QTreeWidgetItem([
                time.strftime("%H:%M:%S", time.localtime(record["time"])), record["kind"], record["topic"], ms,
                record.get("error") or ""]))
        while self.messages_tree.topLevelItemCount() > MAX_ROWS:
            self.messages_tree.takeTopLevelItem(0)
        self.messages_tree.resizeColumnToContents(2)
        self.messages_tree.scrollToBottom()


_inspector: BusInspector | None = None


def open_bus_inspector(parent: QWidget | None = None) -> BusInspector:
    global _inspector
    if _inspector is None:
        _inspector = BusInspector(parent)
    _inspector.show()
    _inspector.raise_()
    return _inspector
# --- END [1.2] ---
