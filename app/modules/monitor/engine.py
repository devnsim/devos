# ==============================================================================
# FILE: app/modules/monitor/engine.py
# OVERVIEW: Software Monitor logic (no Qt): samples the system and every process, keeps a short history,
#           and finds inefficiencies worth a look (sustained CPU hogs, memory hogs, swap pressure, zombies,
#           thread explosions, swarms of the same program, DevOS busy while idle).
# PURPOSE:
#   1. SystemSampler.sample() -> SystemSample (CPU per core, memory, swap, disk and network rates, load).
#   2. ProcessSampler.sample() -> list[ProcessRow] with CPU % (between samples), memory, threads, I/O rates,
#      and a per-process CPU history for "sustained" checks.
#   3. find_insights(rows, system, history, own_pid) -> list[Insight], most serious first.
# CONTRACT:
#   - Requires: psutil. No Qt.
#   - Exposes: SystemSampler, SystemSample, ProcessSampler, ProcessRow, Insight, find_insights(),
#              group_by_name(rows), human_bytes(n)
# NOTES:
#   - [2026-11-16] Created with the Software Monitor module.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - cpu_percent (float): Share of ONE core (a process using 2 full cores shows 200), like `top`.
# - sustained: Over the threshold in every one of the last SUSTAIN_SAMPLES samples.
# - insight: A finding with a level ("high" | "warn" | "info"), a title, details and maybe a pid.
# - swarm: Many processes with the same name (browser tabs, workers) - shown with their total memory.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SAMPLING
#    1.1 imports_and_types
#    1.2 system_sampler
#    1.3 process_sampler
# 2. INSIGHTS
#    2.1 find_insights
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_types
# ==============================================================================
from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field

import psutil

SUSTAIN_SAMPLES = 5
HIGH_CPU_PERCENT = 80.0         # of one core, sustained
MEMORY_HOG_FRACTION = 0.10      # of total RAM
MANY_THREADS = 300
SWARM_COUNT = 12
IDLE_SELF_CPU_PERCENT = 15.0    # DevOS using this much of a core while nothing is live


def human_bytes(count: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(count) < 1024 or unit == "TB":
            return f"{count:,.0f} {unit}" if unit in ("B", "KB") else f"{count:,.1f} {unit}"
        count /= 1024
    return f"{count:.1f} TB"


@dataclass
class SystemSample:
    time: float
    cpu_percent: float
    per_core: list[float]
    memory_percent: float
    memory_used: int
    memory_total: int
    swap_percent: float
    disk_read_rate: float
    disk_write_rate: float
    net_down_rate: float
    net_up_rate: float
    load_average: tuple[float, float, float]


@dataclass
class ProcessRow:
    pid: int
    name: str
    user: str
    status: str
    cpu_percent: float
    memory_rss: int
    memory_percent: float
    threads: int
    read_rate: float
    write_rate: float
    started: float
    command: str
    cpu_history: list[float] = field(default_factory=list)


@dataclass
class Insight:
    level: str                  # "high" | "warn" | "info"
    title: str
    detail: str
    pid: int | None = None
# --- END [1.1] ---


# ==============================================================================
# [1.2] system_sampler
# ==============================================================================
class SystemSampler:
    def __init__(self) -> None:
        psutil.cpu_percent(None, percpu=True)           # prime: the first call measures nothing
        self._last_time = time.monotonic()
        self._last_disk = psutil.disk_io_counters()
        self._last_net = psutil.net_io_counters()

    def sample(self) -> SystemSample:
        now = time.monotonic()
        elapsed = max(now - self._last_time, 1e-3)
        self._last_time = now
        per_core = psutil.cpu_percent(None, percpu=True)
        memory, swap = psutil.virtual_memory(), psutil.swap_memory()
        disk, net = psutil.disk_io_counters(), psutil.net_io_counters()

        def rate(new, old, name: str) -> float:
            return max(0.0, (getattr(new, name) - getattr(old, name)) / elapsed) if new and old else 0.0
        sample = SystemSample(
            time.time(), sum(per_core) / max(1, len(per_core)), per_core, memory.percent, memory.used, memory.total,
            swap.percent, rate(disk, self._last_disk, "read_bytes"), rate(disk, self._last_disk, "write_bytes"),
            rate(net, self._last_net, "bytes_recv"), rate(net, self._last_net, "bytes_sent"),
            os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0))
        self._last_disk, self._last_net = disk, net
        return sample
# --- END [1.2] ---

# ==============================================================================
# [1.3] process_sampler
# ==============================================================================
class ProcessSampler:
    ATTRIBUTES = ["pid", "name", "username", "status", "memory_info", "memory_percent", "num_threads", "create_time", "cmdline"]

    def __init__(self) -> None:
        self._processes: dict[int, psutil.Process] = {}
        self._io: dict[int, tuple[float, int, int]] = {}
        self._history: dict[int, deque] = {}

    def sample(self) -> list[ProcessRow]:
        rows, seen = [], set()
        now = time.monotonic()
        for process in psutil.process_iter(self.ATTRIBUTES):
            pid = process.info["pid"]
            seen.add(pid)
            known = self._processes.get(pid)
            if known is None or known.info.get("create_time") != process.info.get("create_time"):
                self._processes[pid] = process
                known = process
                try:
                    known.cpu_percent(None)                 # prime this process's CPU counter
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            known.info = process.info
            try:
                cpu = known.cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                cpu = 0.0
            read_rate = write_rate = 0.0
            try:
                counters = known.io_counters()
                last = self._io.get(pid)
                if last is not None:
                    elapsed = max(now - last[0], 1e-3)
                    read_rate = max(0.0, (counters.read_bytes - last[1]) / elapsed)
                    write_rate = max(0.0, (counters.write_bytes - last[2]) / elapsed)
                self._io[pid] = (now, counters.read_bytes, counters.write_bytes)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, AttributeError):
                pass
            history = self._history.setdefault(pid, deque(maxlen=SUSTAIN_SAMPLES))
            history.append(cpu)
            info = process.info
            memory = info.get("memory_info")
            rows.append(ProcessRow(
                pid, info.get("name") or "?", info.get("username") or "", info.get("status") or "", cpu,
                memory.rss if memory else 0, info.get("memory_percent") or 0.0, info.get("num_threads") or 0,
                read_rate, write_rate, info.get("create_time") or 0.0, " ".join(info.get("cmdline") or [])[:400],
                list(history)))
        for pid in set(self._processes) - seen:              # forget processes that ended
            self._processes.pop(pid, None)
            self._io.pop(pid, None)
            self._history.pop(pid, None)
        return rows


def group_by_name(rows: list[ProcessRow]) -> dict[str, list[ProcessRow]]:
    groups: dict[str, list[ProcessRow]] = {}
    for row in rows:
        groups.setdefault(row.name, []).append(row)
    return groups
# --- END [1.3] ---


# ==============================================================================
# [2.1] find_insights
# ==============================================================================
def find_insights(rows: list[ProcessRow], system: SystemSample | None, own_pid: int,
                  is_self_live: bool = False) -> list[Insight]:
    """What deserves a look right now, most serious first."""
    insights: list[Insight] = []
    for row in rows:
        history = row.cpu_history
        if len(history) >= SUSTAIN_SAMPLES and min(history) >= HIGH_CPU_PERCENT and row.pid != own_pid:
            insights.append(Insight("high", f"{row.name} keeps a core busy",
                                    f"PID {row.pid} has used ≥{HIGH_CPU_PERCENT:.0f}% of a core for {len(history)} samples "
                                    f"(now {row.cpu_percent:.0f}%). Stuck loop, runaway build or busy-waiting?", row.pid))
        if system is not None and row.memory_rss >= MEMORY_HOG_FRACTION * system.memory_total:
            insights.append(Insight("warn", f"{row.name} holds {human_bytes(row.memory_rss)}",
                                    f"PID {row.pid} uses {row.memory_percent:.1f}% of RAM.", row.pid))
        if row.status == psutil.STATUS_ZOMBIE:
            insights.append(Insight("warn", f"Zombie process {row.name}",
                                    f"PID {row.pid} has exited but its parent never collected it.", row.pid))
        if row.threads >= MANY_THREADS:
            insights.append(Insight("warn", f"{row.name} runs {row.threads} threads",
                                    f"PID {row.pid}: that many threads usually means a leak or an oversized pool.", row.pid))
    for name, members in group_by_name(rows).items():
        if len(members) >= SWARM_COUNT:
            total = sum(member.memory_rss for member in members)
            insights.append(Insight("info", f"{len(members)} × {name}",
                                    f"Together {human_bytes(total)} RAM and {sum(m.cpu_percent for m in members):.0f}% CPU."))
    if system is not None:
        if system.swap_percent >= 50 and system.memory_percent >= 85:
            insights.append(Insight("high", "Memory pressure: swapping",
                                    f"RAM {system.memory_percent:.0f}% used and swap {system.swap_percent:.0f}% — "
                                    "everything slows down. Close the biggest memory users."))
        elif system.memory_percent >= 90:
            insights.append(Insight("warn", "RAM almost full", f"{system.memory_percent:.0f}% of RAM in use."))
        cores = len(system.per_core) or 1
        if system.load_average[0] > cores * 1.5:
            insights.append(Insight("warn", "More work than cores",
                                    f"Load average {system.load_average[0]:.1f} on {cores} cores: tasks wait for CPU time."))
    own = next((row for row in rows if row.pid == own_pid), None)
    if own is not None and not is_self_live and len(own.cpu_history) >= SUSTAIN_SAMPLES \
            and min(own.cpu_history) >= IDLE_SELF_CPU_PERCENT:
        insights.append(Insight("warn", "DevOS is busy while idle",
                                f"DevOS uses {own.cpu_percent:.0f}% of a core with no live module here — a hidden "
                                "timer or loop may be running. Check the System Log.", own_pid))
    order = {"high": 0, "warn": 1, "info": 2}
    return sorted(insights, key=lambda insight: order[insight.level])
# --- END [2.1] ---
