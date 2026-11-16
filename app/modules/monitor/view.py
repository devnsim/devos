# ==============================================================================
# FILE: app/modules/monitor/view.py
# OVERVIEW: Software Monitor module - what the machine and DevOS are doing: live CPU / memory / I/O plots,
#           every process (sortable, filterable, grouped by program), insights that point at
#           inefficiencies, and DevOS's own footprint (threads, live modules, log warnings).
# PURPOSE:
#   1. One live loop (ModuleView.setup_live, pauses when hidden) samples the system every second and the
#      processes every PROCESS_EVERY ticks on a worker thread; blocks redraw from the module's latest samples.
#   2. Blocks: Overview (plots + numbers), Processes (table, end / kill with confirmation), Insights,
#      DevOS (own process, live modules, threads, warnings this session).
#   3. Tools for the tool bag: pause / resume sampling, copy a process report.
# CONTRACT:
#   - Requires: app.modules.base.ModuleView, app.modules.monitor.engine, psutil, app.ui (plot_tools, ui_tools,
#               canvas, tool_bag, theme), app.data.activity_log, QSS selector #monitor_panel
#   - Exposes: MonitorModule(parent=None, context=None) with system_history, processes, insights, sampled
#              signal, focus_process(pid); OverviewBlock, ProcessesBlock, InsightsBlock, DevosBlock
# NOTES:
#   - [2026-11-16] Created (Software Monitor).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - HISTORY_SAMPLES (int): Seconds of system history the plots keep (default: 120).
# - PROCESS_EVERY (int): Process table refresh every N ticks (default: 2) - scanning all processes costs more.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SHARED
#    1.1 imports
#    1.2 process_model
# 2. BLOCKS
#    2.1 overview_block
#    2.2 processes_block
#    2.3 insights_block
#    2.4 devos_block
# 3. MODULE
#    3.1 monitor_module
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import datetime as dt
import os
import signal
import threading
import time
from collections import deque

import psutil
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QVBoxLayout, QWidget,
)

from app.data.activity_log import WARNING, get_activity_log
from app.modules.base import ModuleView
from app.modules.monitor.engine import (
    Insight, ProcessRow, ProcessSampler, SystemSample, SystemSampler, find_insights, group_by_name, human_bytes,
)
from app.ui.canvas.block_canvas import BlockSpec
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.plot_tools import styled_plot
from app.ui.theme import ACCENT_HIGHLIGHT_COLOR, ERROR_COLOR, SUCCESS_COLOR, TAG_COLORS, WARNING_COLOR
from app.ui.tool_bag import ToolSpec, register_tools
from app.ui.ui_tools import block_layout, control_label, run_in_thread, table_view

HISTORY_SAMPLES = 120
PROCESS_EVERY = 2
LEVEL_COLORS = {"high": ERROR_COLOR, "warn": WARNING_COLOR, "info": ACCENT_HIGHLIGHT_COLOR}
COLUMNS = ("PID", "Name", "CPU %", "Memory", "Mem %", "Threads", "Read/s", "Write/s", "User", "Status", "Started", "Command")
DEFAULT_BLOCKS = {"split": "h", "sizes": [3, 2], "children": [
    {"split": "v", "sizes": [2, 3], "children": [{"kind": "overview"}, {"kind": "processes"}]},
    {"split": "v", "sizes": [3, 2], "children": [{"kind": "insights"}, {"kind": "devos"}]},
]}
# --- END [1.1] ---


# ==============================================================================
# [1.2] process_model
# ==============================================================================
class ProcessModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[ProcessRow] = []

    def set_rows(self, rows: list[ProcessRow]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:                     # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:                  # noqa: N802
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        row = self.rows[index.row()]
        column = index.column()
        sort_values = (row.pid, row.name.lower(), row.cpu_percent, row.memory_rss, row.memory_percent, row.threads,
                       row.read_rate, row.write_rate, row.user, row.status, row.started, row.command)
        if role == Qt.ItemDataRole.UserRole:
            return sort_values[column]
        if role == Qt.ItemDataRole.DisplayRole:
            return (str(row.pid), row.name, f"{row.cpu_percent:.1f}", human_bytes(row.memory_rss), f"{row.memory_percent:.1f}",
                    str(row.threads), human_bytes(row.read_rate), human_bytes(row.write_rate), row.user, row.status,
                    dt.datetime.fromtimestamp(row.started).strftime("%m-%d %H:%M") if row.started else "", row.command)[column]
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (0, 2, 3, 4, 5, 6, 7):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and column == 2 and row.cpu_percent >= 50:
            return QColor(WARNING_COLOR if row.cpu_percent < 90 else ERROR_COLOR)
        if role == Qt.ItemDataRole.ToolTipRole and column == 11:
            return row.command
        return None
# --- END [1.2] ---


# ==============================================================================
# [2.1] overview_block
# ==============================================================================
class OverviewBlock(QWidget):
    def __init__(self, module: "MonitorModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        self.numbers_label = QLabel(objectName="module_stats_label")
        row.addWidget(self.numbers_label, 1)
        reserve_controls_space(row)
        self.usage_plot = styled_plot(y_label="%")
        self.usage_plot.setYRange(0, 100)
        self.usage_plot.addLegend(offset=(6, 6))
        self.curves = {name: self.usage_plot.plot(name=name, pen={"color": color, "width": 2})
                       for name, color in (("CPU", ACCENT_HIGHLIGHT_COLOR), ("RAM", SUCCESS_COLOR), ("Swap", WARNING_COLOR))}
        self.io_plot = styled_plot(y_label="MB/s")
        self.io_plot.addLegend(offset=(6, 6))
        self.io_curves = {name: self.io_plot.plot(name=name, pen={"color": TAG_COLORS[index], "width": 1.5})
                          for index, name in enumerate(("Net ↓", "Net ↑", "Disk read", "Disk write"))}
        layout.addWidget(self.usage_plot, 3)
        layout.addWidget(self.io_plot, 2)
        module.sampled.connect(self.refresh)

    def refresh(self) -> None:
        history = list(self.module.system_history)
        if not history:
            return
        start = history[0].time
        x = [sample.time - start for sample in history]
        self.curves["CPU"].setData(x, [sample.cpu_percent for sample in history])
        self.curves["RAM"].setData(x, [sample.memory_percent for sample in history])
        self.curves["Swap"].setData(x, [sample.swap_percent for sample in history])
        for name, attribute in (("Net ↓", "net_down_rate"), ("Net ↑", "net_up_rate"),
                                ("Disk read", "disk_read_rate"), ("Disk write", "disk_write_rate")):
            self.io_curves[name].setData(x, [getattr(sample, attribute) / 2**20 for sample in history])
        last = history[-1]
        self.numbers_label.setText(
            f"CPU {last.cpu_percent:.0f}% ({len(last.per_core)} cores) · RAM {human_bytes(last.memory_used)} / "
            f"{human_bytes(last.memory_total)} · swap {last.swap_percent:.0f}% · load {last.load_average[0]:.2f}")
# --- END [2.1] ---

# ==============================================================================
# [2.2] processes_block
# ==============================================================================
class ProcessesBlock(QWidget):
    def __init__(self, module: "MonitorModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        self.filter_edit = QLineEdit(placeholderText="Filter by name, command, user or PID", clearButtonEnabled=True)
        self.filter_edit.textChanged.connect(self.refresh)
        row.addWidget(self.filter_edit, 1)
        self.group_check = QCheckBox("Group by program", toolTip="One row per program name with summed CPU and memory")
        self.group_check.toggled.connect(self.refresh)
        row.addWidget(self.group_check)
        self.mine_check = QCheckBox("Mine only", toolTip="Only processes of your user")
        self.mine_check.toggled.connect(self.refresh)
        row.addWidget(self.mine_check)
        reserve_controls_space(row)
        self.model = ProcessModel()
        self.proxy = QSortFilterProxyModel(sortRole=Qt.ItemDataRole.UserRole)
        self.proxy.setSourceModel(self.model)
        self.process_table = table_view("process_table")
        self.process_table.setModel(self.proxy)
        self.process_table.setSortingEnabled(True)
        self.process_table.sortByColumn(2, Qt.SortOrder.DescendingOrder)
        self.process_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.process_table.verticalHeader().hide()
        self.process_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.process_table.customContextMenuRequested.connect(self._menu)
        layout.addWidget(self.process_table, 1)
        self.count_label = control_label()
        layout.addWidget(self.count_label)
        self._fitted = False
        module.processes_sampled.connect(self.refresh)
        module.focus_requested.connect(self.focus_pid)

    def refresh(self) -> None:
        rows = self.module.processes
        user = psutil.Process().username()
        if self.mine_check.isChecked():
            rows = [row for row in rows if row.user == user]
        needle = self.filter_edit.text().strip().lower()
        if needle:
            rows = [row for row in rows if needle in f"{row.pid} {row.name} {row.command} {row.user}".lower()]
        if self.group_check.isChecked():
            rows = [ProcessRow(members[0].pid if len(members) == 1 else 0, f"{name} ×{len(members)}" if len(members) > 1 else name,
                               members[0].user, "", sum(m.cpu_percent for m in members), sum(m.memory_rss for m in members),
                               sum(m.memory_percent for m in members), sum(m.threads for m in members),
                               sum(m.read_rate for m in members), sum(m.write_rate for m in members),
                               min(m.started for m in members), members[0].command)
                    for name, members in group_by_name(rows).items()]
        selected = self.selected_pid()
        self.model.set_rows(rows)
        if not self._fitted and rows:
            self.process_table.resizeColumnsToContents()
            self._fitted = True
        if selected:
            self.focus_pid(selected, scroll=False)
        self.count_label.setText(f"{len(rows)} shown · {len(self.module.processes)} processes")

    def selected_pid(self) -> int | None:
        index = self.process_table.currentIndex()
        if not index.isValid():
            return None
        return self.model.rows[self.proxy.mapToSource(index).row()].pid or None

    def focus_pid(self, pid: int, scroll: bool = True) -> None:
        for row_index, row in enumerate(self.model.rows):
            if row.pid == pid:
                index = self.proxy.mapFromSource(self.model.index(row_index, 0))
                self.process_table.selectRow(index.row())
                if scroll:
                    self.process_table.scrollTo(index)
                return

    def _menu(self, position) -> None:
        pid = self.selected_pid()
        if pid is None:
            return
        row = next(row for row in self.model.rows if row.pid == pid)
        menu = QMenu(self)
        menu.addAction("Copy command line").triggered.connect(lambda: QApplication.clipboard().setText(row.command))
        menu.addAction("Copy PID").triggered.connect(lambda: QApplication.clipboard().setText(str(pid)))
        menu.addSeparator()
        is_self = pid == os.getpid()
        for title, sig in (("End process (SIGTERM)…", signal.SIGTERM), ("Kill process (SIGKILL)…", signal.SIGKILL)):
            action = menu.addAction(title)
            action.setEnabled(not is_self)
            action.triggered.connect(lambda _checked=False, sig=sig: self.module.signal_process(row, sig))
        menu.exec(self.process_table.viewport().mapToGlobal(position))
# --- END [2.2] ---

# ==============================================================================
# [2.3] insights_block
# ==============================================================================
class InsightsBlock(QWidget):
    def __init__(self, module: "MonitorModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        row.addWidget(control_label("Insights — what's worth a look"))
        row.addStretch()
        reserve_controls_space(row)
        self.insight_list = QListWidget(objectName="insight_list", wordWrap=True)
        self.insight_list.itemDoubleClicked.connect(self._open)
        layout.addWidget(self.insight_list, 1)
        layout.addWidget(control_label("Double-click an insight to find its process"))
        module.processes_sampled.connect(self.refresh)

    def refresh(self) -> None:
        self.insight_list.clear()
        insights: list[Insight] = self.module.insights
        if not insights:
            item = QListWidgetItem("✓  Nothing stands out right now.")
            item.setForeground(QColor(SUCCESS_COLOR))
            self.insight_list.addItem(item)
            return
        for insight in insights:
            item = QListWidgetItem(f"●  {insight.title}\n    {insight.detail}")
            item.setForeground(QColor(LEVEL_COLORS[insight.level]))
            item.setData(Qt.ItemDataRole.UserRole, insight.pid)
            self.insight_list.addItem(item)

    def _open(self, item: QListWidgetItem) -> None:
        pid = item.data(Qt.ItemDataRole.UserRole)
        if pid:
            self.module.focus_process(pid)
# --- END [2.3] ---

# ==============================================================================
# [2.4] devos_block
# ==============================================================================
class DevosBlock(QWidget):
    """DevOS's own footprint: CPU, memory, threads, files, live modules, warnings this session."""

    def __init__(self, module: "MonitorModule") -> None:
        super().__init__()
        self.module = module
        self.process = psutil.Process()
        layout, row = block_layout(self)
        row.addWidget(control_label("DevOS itself"))
        row.addStretch()
        reserve_controls_space(row)
        self.summary_label = QLabel(objectName="module_summary_label", wordWrap=True)
        self.summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary_label)
        self.self_plot = styled_plot(y_label="% of a core")
        self.self_curve = self.self_plot.plot(pen={"color": ACCENT_HIGHLIGHT_COLOR, "width": 2})
        layout.addWidget(self.self_plot, 1)
        self.cpu_history: deque = deque(maxlen=HISTORY_SAMPLES)
        module.processes_sampled.connect(self.refresh)

    def refresh(self) -> None:
        own = next((row for row in self.module.processes if row.pid == os.getpid()), None)
        if own is None:
            return
        self.cpu_history.append(own.cpu_percent)
        self.self_curve.setData(list(self.cpu_history))
        try:
            open_files = len(self.process.open_files())
        except psutil.AccessDenied:
            open_files = 0
        live = live_modules()
        log = get_activity_log()
        warnings = sum(1 for entry in log.entries if entry.level >= WARNING)
        uptime = dt.timedelta(seconds=int(time.time() - self.process.create_time()))
        self.summary_label.setText(
            f"CPU {own.cpu_percent:.1f}% of a core · RAM {human_bytes(own.memory_rss)} · {own.threads} threads "
            f"({threading.active_count()} Python) · {open_files} open files · up {uptime}\n"
            f"Live modules: {', '.join(live) if live else 'none'}\n"
            f"Warnings / errors this session: {warnings}")


def live_modules() -> list[str]:
    """Titles of modules whose live timer is running right now."""
    names = []
    for widget in QApplication.allWidgets():
        if isinstance(widget, ModuleView) and widget.live_timer is not None and widget.live_timer.isActive():
            context = widget.context
            names.append(context.module_type_id if context is not None else type(widget).__name__)
    return names
# --- END [2.4] ---


# ==============================================================================
# [3.1] monitor_module
# ==============================================================================
class MonitorModule(ModuleView):
    sampled = Signal()                   # new system sample
    processes_sampled = Signal()         # new process list + insights
    focus_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, context, "monitor_panel")
        self.system_sampler = SystemSampler()
        self.process_sampler = ProcessSampler()
        self.system_history: deque[SystemSample] = deque(maxlen=HISTORY_SAMPLES)
        self.processes: list[ProcessRow] = []
        self.insights: list[Insight] = []
        self._tick_count = 0
        self._is_scanning = False
        self.log = get_activity_log()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        row = QHBoxLayout()
        row.addWidget(self.setup_live(1000, self.tick))
        self.headline_label = QLabel(objectName="module_stats_label")
        row.addWidget(self.headline_label, 1)
        reserve_controls_space(row)
        layout.addLayout(row)
        self.block_canvas = self.build_block_canvas([
            BlockSpec("overview", "Overview", "CPU, RAM, swap, network and disk over the last two minutes", lambda: OverviewBlock(self)),
            BlockSpec("processes", "Processes", "Every process: CPU, memory, threads, I/O; end or kill", lambda: ProcessesBlock(self)),
            BlockSpec("insights", "Insights", "Inefficiencies worth a look: hogs, swapping, zombies, swarms", lambda: InsightsBlock(self)),
            BlockSpec("devos", "DevOS", "DevOS's own CPU, memory, threads, live modules and warnings", lambda: DevosBlock(self)),
        ], DEFAULT_BLOCKS)
        layout.addWidget(self.block_canvas, 1)
        self.tick()

    def tick(self) -> None:
        sample = self.system_sampler.sample()
        self.system_history.append(sample)
        self.sampled.emit()
        if self._tick_count % PROCESS_EVERY == 0 and not self._is_scanning:
            # Scanning ~hundreds of processes takes 100+ ms: do it off the UI thread (a monitor mustn't cause lag).
            self._is_scanning = True
            is_self_live = bool(live_modules())

            def scan():
                rows = self.process_sampler.sample()
                return rows, find_insights(rows, sample, os.getpid(), is_self_live=is_self_live)
            run_in_thread(self, scan, self._on_scanned)
        self._tick_count += 1

    def _on_scanned(self, result, error) -> None:
        self._is_scanning = False
        if error is not None:
            self.log.error("monitor", "Process scan failed", error)
            return
        self.processes, self.insights = result
        self.processes_sampled.emit()
        serious = sum(1 for insight in self.insights if insight.level == "high")
        self.headline_label.setText(f"{len(self.processes)} processes · {len(self.insights)} insights"
                                    + (f" · {serious} serious" if serious else ""))

    def focus_process(self, pid: int) -> None:
        self.focus_requested.emit(pid)

    def signal_process(self, row: ProcessRow, sig: signal.Signals) -> None:
        verb = "Kill" if sig == signal.SIGKILL else "End"
        answer = QMessageBox.question(self, f"{verb} process", f"{verb} {row.name} (PID {row.pid})?\n\n{row.command[:300]}\n\n"
                                      "Unsaved work in that program will be lost.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            with self.log.step("monitor", f"{verb} {row.name} (PID {row.pid})", reraise=False):
                os.kill(row.pid, sig)
        except OSError:
            pass                                       # logged by step(); the status shows in the System Log

    def process_report(self) -> str:
        top = sorted(self.processes, key=lambda row: row.cpu_percent, reverse=True)[:15]
        lines = [f"{row.pid:>7}  {row.cpu_percent:6.1f}%  {human_bytes(row.memory_rss):>10}  {row.name}" for row in top]
        insights = [f"[{insight.level}] {insight.title}: {insight.detail}" for insight in self.insights]
        return "\n".join(["Top processes (CPU):", *lines, "", "Insights:", *(insights or ["none"])])


register_tools([
    ToolSpec("monitor.toggle_live", "Pause / resume sampling", "⏯", "Stop or restart the monitor's sampling",
             lambda module: module.set_live(not module.is_live), "monitor", "live pause"),
    ToolSpec("monitor.copy_report", "Copy process report", "⎘", "Copy the top processes and insights as text",
             lambda module: QApplication.clipboard().setText(module.process_report()), "monitor", "clipboard"),
])
# --- END [3.1] ---
