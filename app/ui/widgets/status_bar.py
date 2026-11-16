# ==============================================================================
# FILE: app/ui/widgets/status_bar.py
# OVERVIEW: IDE-style status bar along the bottom of the window: a message on the left and
#           live system / app resource tiles on the right.
# PURPOSE:
#   1. DevOS process tiles: CPU (share of the whole machine) and memory (RSS), to spot a module that
#      starts using a lot.
#   2. UI lag: how late the Qt event loop runs a 100 ms timer (worst in the last second). High lag means
#      something is blocking the interface (a heavy module on the UI thread).
#   3. System tiles: total CPU, RAM, network down/up, CPU temperature, GPU load (when the driver
#      exposes it in sysfs - AMD does; Intel/NVIDIA need root tools, so the tile hides).
#   4. Tiles turn amber / red past warning thresholds; tooltips give details. Crossing into red (and
#      back) is announced in the activity log once.
#   5. Log tile (left): opens the System Log, shows the latest announcement, counts unseen warnings.
# CONTRACT:
#   - Requires: psutil, QSS selectors #system_status_bar, #status_message_label, #status_tile
#               (property level = "ok" | "warn" | "high")
#   - Exposes: SystemStatusBar (QWidget) with signal log_toggled(bool); set_log_open(); tiles (dict name -> QLabel);
#              sample() (reads everything once; the timer calls it every second)
# NOTES:
#   - [2026-11-16] CPU temperature reads only the CPU sensor's files (found once) instead of
#                  psutil.sensors_temperatures() every second, which read every device's sensors and caused
#                  60-300 ms UI stalls (found with the new Software Monitor work).
#   - [2026-11-15] Log tile + latest-announcement text (show_message removed: announcements now go
#                  through the activity log); threshold crossings are logged.
#   - [2026-11-15] Created (the old QStatusBar only said "Ready"). Sampling costs well under 1 ms.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - level (str): Tile colour state: "ok" (muted), "warn" (amber), "high" (red).
# - RSS: Resident set size - RAM the DevOS process actually occupies.
# - UI lag (ms): Extra delay of a periodic timer on the UI thread (0 = perfectly responsive).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. STATUS_BAR
#    1.1 constants
#    1.2 init_and_tiles
#    1.3 sampling
#    1.4 helpers
# ==============================================================================

import glob
import os
import time

import psutil
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolButton, QWidget

from app.data.activity_log import INFO, WARNING, get_activity_log
from app.ui.ui_tools import set_style_property
from app.ui.widgets.log_panel import get_log_bridge

# ==============================================================================
# [1.1] constants
# ==============================================================================
SAMPLE_INTERVAL_MS = 1000
LAG_PROBE_INTERVAL_MS = 100
# name: (warn at, high at)
THRESHOLDS = {
    "app_cpu": (25.0, 60.0),        # % of the whole machine
    "app_memory": (1500.0, 4000.0),  # MB
    "ui_lag": (50.0, 150.0),         # ms
    "cpu": (70.0, 90.0),
    "ram": (75.0, 90.0),
    "temperature": (80.0, 92.0),     # deg C
    "gpu": (80.0, 95.0),
}
# --- END [1.1] ---


TILE_NAMES = {"app_cpu": "DevOS CPU", "app_memory": "DevOS memory", "ui_lag": "UI lag", "cpu": "System CPU",
              "ram": "System RAM", "temperature": "CPU temperature", "gpu": "GPU load"}


class SystemStatusBar(QWidget):
    log_toggled = Signal(bool)            # the Log tile was clicked: open / close the System Log
    HEIGHT_PX = 24

# ==============================================================================
# [1.2] init_and_tiles
# ==============================================================================
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="system_status_bar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(self.HEIGHT_PX)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(2)

        # Log tile: opens the System Log; counts warnings / errors since it was last opened.
        self.status_log_btn = QToolButton(objectName="status_log_btn", checkable=True, text="▤ Log",
                                          toolTip="Open the System Log (what DevOS is doing, step by step)")
        self.status_log_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status_log_btn.toggled.connect(self._on_log_toggled)
        layout.addWidget(self.status_log_btn)
        self._unseen_problems = 0
        self.status_message_label = QLabel(objectName="status_message_label")
        self.status_message_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.status_message_label, 1)
        get_log_bridge().entry_added.connect(self._on_log_entry)
        self._high_tiles: set[str] = set()

        self.tiles: dict[str, QLabel] = {}
        for name in ("app_cpu", "app_memory", "ui_lag", "cpu", "ram", "network", "temperature", "gpu"):
            tile = QLabel(objectName="status_tile")
            tile.setProperty("level", "ok")
            self.tiles[name] = tile
            layout.addWidget(tile)

        self.process = psutil.Process(os.getpid())
        self.process.cpu_percent(None)                  # prime: the first call always returns 0
        psutil.cpu_percent(None)
        self.cpu_count = psutil.cpu_count() or 1
        self._last_network = psutil.net_io_counters()
        self._last_sample_time = time.monotonic()
        self._gpu_busy_files = glob.glob("/sys/class/drm/card*/device/gpu_busy_percent")
        self.tiles["gpu"].setVisible(bool(self._gpu_busy_files))

        self._lag_expected = time.monotonic() + LAG_PROBE_INTERVAL_MS / 1000
        self._worst_lag_ms = 0.0
        self.lag_timer = QTimer(self, interval=LAG_PROBE_INTERVAL_MS, timeout=self._probe_lag)
        self.lag_timer.start()
        self.sample_timer = QTimer(self, interval=SAMPLE_INTERVAL_MS, timeout=self.sample)
        self.sample_timer.start()
        self.sample()
# --- END [1.2] ---

# ==============================================================================
# [1.3] sampling
# ==============================================================================
    def _probe_lag(self) -> None:
        now = time.monotonic()
        self._worst_lag_ms = max(self._worst_lag_ms, (now - self._lag_expected) * 1000)
        self._lag_expected = now + LAG_PROBE_INTERVAL_MS / 1000

    def sample(self) -> None:
        now = time.monotonic()
        elapsed = max(now - self._last_sample_time, 1e-3)
        self._last_sample_time = now

        with self.process.oneshot():
            process_cpu = self.process.cpu_percent(None)         # % of one core (can exceed 100)
            rss_mb = self.process.memory_info().rss / 2**20
            threads = self.process.num_threads()
        app_cpu = process_cpu / self.cpu_count
        self._set("app_cpu", f"DevOS {app_cpu:.0f}%", app_cpu,
                  f"DevOS process CPU: {app_cpu:.1f}% of the machine ({process_cpu:.0f}% of one core), {threads} threads")
        self._set("app_memory", f"{rss_mb:,.0f} MB", rss_mb, f"DevOS process memory (RSS): {rss_mb:,.0f} MB")

        lag = max(self._worst_lag_ms, 0.0)
        self._worst_lag_ms = 0.0
        self._set("ui_lag", f"UI {lag:.0f} ms", lag,
                  "UI lag: worst delay of the interface's event loop in the last second.\n"
                  "Over 50 ms feels sluggish; a module doing heavy work on the UI thread raises it.")

        cpu = psutil.cpu_percent(None)
        self._set("cpu", f"CPU {cpu:.0f}%", cpu, f"System CPU: {cpu:.0f}% across {self.cpu_count} logical cores")
        memory = psutil.virtual_memory()
        self._set("ram", f"RAM {memory.percent:.0f}%", memory.percent,
                  f"System RAM: {memory.used / 2**30:.1f} of {memory.total / 2**30:.1f} GB used")

        network = psutil.net_io_counters()
        down = (network.bytes_recv - self._last_network.bytes_recv) / elapsed
        up = (network.bytes_sent - self._last_network.bytes_sent) / elapsed
        self._last_network = network
        self._set("network", f"↓{_rate(down)} ↑{_rate(up)}", None, "Network throughput (all interfaces)")

        temperature = _cpu_temperature()
        self.tiles["temperature"].setVisible(temperature is not None)
        if temperature is not None:
            self._set("temperature", f"{temperature:.0f}°C", temperature, "CPU package temperature")

        if self._gpu_busy_files:
            busy = _read_number(self._gpu_busy_files[0])
            if busy is not None:
                self._set("gpu", f"GPU {busy:.0f}%", busy, "GPU load (from the kernel driver)")
# --- END [1.3] ---

# ==============================================================================
# [1.4] helpers
# ==============================================================================
    def _set(self, name: str, text: str, value: float | None, tool_tip: str) -> None:
        tile = self.tiles[name]
        tile.setText(text)
        tile.setToolTip(tool_tip)
        level = "ok"
        if value is not None and name in THRESHOLDS:
            warn_at, high_at = THRESHOLDS[name]
            level = "high" if value >= high_at else "warn" if value >= warn_at else "ok"
        set_style_property(tile, "level", level)
        # Announce crossing into red once (not every second while it stays red).
        if level == "high" and name not in self._high_tiles:
            self._high_tiles.add(name)
            get_activity_log().warning("monitor", f"{TILE_NAMES.get(name, name)} is high: {text}")
        elif level != "high" and name in self._high_tiles:
            self._high_tiles.discard(name)
            get_activity_log().info("monitor", f"{TILE_NAMES.get(name, name)} back to normal: {text}")

    def _on_log_entry(self, entry) -> None:
        if entry.level >= INFO:
            self.status_message_label.setText(entry.message)
            self.status_message_label.setToolTip(f"{entry.source}: {entry.message}")
        if entry.level >= WARNING and not self.status_log_btn.isChecked():
            self._unseen_problems += 1
            self._update_log_button()

    def _on_log_toggled(self, is_open: bool) -> None:
        if is_open:
            self._unseen_problems = 0
            self._update_log_button()
        self.log_toggled.emit(is_open)

    def _update_log_button(self) -> None:
        self.status_log_btn.setText(f"▤ Log  ⚠ {self._unseen_problems}" if self._unseen_problems else "▤ Log")
        set_style_property(self.status_log_btn, "has_problems", self._unseen_problems > 0)

    def set_log_open(self, is_open: bool) -> None:
        """Reflect the panel's state without re-emitting (e.g. closed from the panel's own button)."""
        self.status_log_btn.blockSignals(True)
        self.status_log_btn.setChecked(is_open)
        self.status_log_btn.blockSignals(False)
        if is_open:
            self._unseen_problems = 0
            self._update_log_button()


def _rate(bytes_per_second: float) -> str:
    for unit, size in (("GB/s", 2**30), ("MB/s", 2**20), ("KB/s", 2**10)):
        if bytes_per_second >= size:
            return f"{bytes_per_second / size:.1f} {unit}"
    return f"{bytes_per_second:.0f} B/s"


CPU_SENSOR_NAMES = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz")
_cpu_temperature_inputs: list[str] | None = None


def _find_cpu_temperature_inputs() -> list[str]:
    """The temp*_input files of the CPU's hwmon sensor, found once. (psutil.sensors_temperatures() reads
    every sensor of every device - Wi-Fi, NVMe, GPU - each call; some are slow, which lagged the UI.)"""
    by_name: dict[str, list[str]] = {}
    for hwmon in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            with open(os.path.join(hwmon, "name")) as handle:
                name = handle.read().strip()
        except OSError:
            continue
        by_name.setdefault(name, []).extend(sorted(glob.glob(os.path.join(hwmon, "temp*_input"))))
    return next((by_name[name] for name in CPU_SENSOR_NAMES if by_name.get(name)), [])


def _cpu_temperature() -> float | None:
    global _cpu_temperature_inputs
    if _cpu_temperature_inputs is None:
        _cpu_temperature_inputs = _find_cpu_temperature_inputs()
    readings = [value / 1000 for value in (_read_number(path) for path in _cpu_temperature_inputs) if value]
    return max(readings) if readings else None


def _read_number(path: str) -> float | None:
    try:
        with open(path) as handle:
            return float(handle.read().strip())
    except (OSError, ValueError):
        return None
# --- END [1.4] ---
