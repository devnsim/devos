# ==============================================================================
# FILE: app/ui/pages/home_page.py
# OVERVIEW: The home dashboard page: live system stats and an activity feed.
# PURPOSE:
#   1. Shows the page title and a clock that updates every second.
#   2. Shows four stat cards: CPU use, memory use, disk use and uptime.
#   3. Shows an activity feed where the app can post timestamped messages.
#   4. Re-reads the system numbers on a timer so the cards stay live.
# CONTRACT:
#   - Requires: psutil, app.ui.widgets.stat_card.StatCard (set_value(text, percent), has_progress_bar),
#               QSS selectors #page_title_label, #page_subtitle_label, #activity_title_label
#   - Exposes: HomePage (QWidget), log_activity(message) for posting to the feed
# NOTES:
#   - [2026-11-14] Renamed home.py -> home_page.py; names, objectNames and methods renamed
#                  to the standard (refresh -> refresh_stats, log -> log_activity).
#   - [2026-11-14] "Activity" heading has its own objectName (activity_title_label), styled
#                  together with #page_title_label in theme.py [3.6].
#   - [2026-11-14] Spacing tightened for the dense "pro tool" look (margins 32/28 -> 8,
#                  spacing 18 -> 6, card gaps 16 -> 4); see *_PX constants.
#   - [2026-11-14] psutil.cpu_percent() is called once before the timer starts: the first call
#                  always returns 0.0 because it measures since the previous call.
#   - [2026-11-14] Uptime card uses has_progress_bar=False (renamed from show_bar in stat_card.py).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - BYTES_PER_GIB (int | bytes): 1024**3. Memory is divided by this, so the card's "GB" is
#   really GiB (binary gigabytes).
# - cpu_percent (float | %): Whole-system CPU use since the previous reading (0.0-100.0).
# - DISK_MOUNT_PATH (str): Mount point whose usage the Disk card shows (default: "/").
# - REFRESH_INTERVAL_MS (int | ms): Time between stat refreshes (default: 1000).
# - uptime_seconds (int | s): Time since the computer booted.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. DESIGN_NOTES
#    1.1 design_notes_docstring
# 2. IMPORTS
#    2.1 standard_library_imports
#    2.2 third_party_imports
#    2.3 devos_imports
# 3. HOME_PAGE
#    3.1 class_constants
#    3.2 init_page
#    3.3 build_header
#    3.4 build_stat_cards
#    3.5 build_activity_feed
#    3.6 start_refresh_timer
#    3.7 refresh_stats
#    3.8 log_activity
# ==============================================================================

# ==============================================================================
# [1.1] design_notes_docstring
# ==============================================================================
"""Home / main dashboard page."""
# --- END [1.1] ---

# ==============================================================================
# [2.1] standard_library_imports
# ==============================================================================
import platform
import time
# --- END [2.1] ---

# ==============================================================================
# [2.2] third_party_imports
# ==============================================================================
import psutil
from PySide6.QtCore import QDateTime, QTimer
from PySide6.QtWidgets import (
    QGridLayout, QLabel, QListWidget, QVBoxLayout, QWidget,
)
# --- END [2.2] ---

# ==============================================================================
# [2.3] devos_imports
# ==============================================================================
from app.ui.widgets.stat_card import StatCard
# --- END [2.3] ---


# ==============================================================================
# [3.1] class_constants
# ==============================================================================
class HomePage(QWidget):
    REFRESH_INTERVAL_MS = 1000
    PAGE_MARGIN_PX = 8
    SECTION_SPACING_PX = 6
    CARD_SPACING_PX = 4
    BYTES_PER_GIB = 2**30
    DISK_MOUNT_PATH = "/"
    SUBTITLE_DATETIME_FORMAT = "dddd, d MMMM yyyy  ·  hh:mm:ss"
    ACTIVITY_TIME_FORMAT = "hh:mm:ss"
# --- END [3.1] ---

# ==============================================================================
# [3.2] init_page
# ==============================================================================
    def __init__(self) -> None:
        super().__init__()
        self.page_layout = QVBoxLayout(self)
        self.page_layout.setContentsMargins(
            self.PAGE_MARGIN_PX, self.PAGE_MARGIN_PX, self.PAGE_MARGIN_PX, self.PAGE_MARGIN_PX
        )
        self.page_layout.setSpacing(self.SECTION_SPACING_PX)

        # Order matters: refresh_stats() needs the header label and cards to exist.
        self._build_header()
        self._build_stat_cards()
        self._build_activity_feed()
        self._start_refresh_timer()
# --- END [3.2] ---

# ==============================================================================
# [3.3] build_header
# ==============================================================================
    def _build_header(self) -> None:
        # Header
        self.page_title_label = QLabel("Dashboard", objectName="page_title_label")
        self.page_layout.addWidget(self.page_title_label)
        self.page_subtitle_label = QLabel(objectName="page_subtitle_label")
        self.page_layout.addWidget(self.page_subtitle_label)
# --- END [3.3] ---

# ==============================================================================
# [3.4] build_stat_cards
# ==============================================================================
    def _build_stat_cards(self) -> None:
        # Stat cards in a grid
        self.cpu_card = StatCard("CPU")
        self.memory_card = StatCard("Memory")
        self.disk_card = StatCard("Disk /")
        self.uptime_card = StatCard("Uptime", has_progress_bar=False)

        stat_card_grid_layout = QGridLayout()
        stat_card_grid_layout.setSpacing(self.CARD_SPACING_PX)
        stat_cards = (self.cpu_card, self.memory_card, self.disk_card, self.uptime_card)
        for column_index, stat_card in enumerate(stat_cards):
            stat_card_grid_layout.addWidget(stat_card, 0, column_index)
        self.page_layout.addLayout(stat_card_grid_layout)
# --- END [3.4] ---

# ==============================================================================
# [3.5] build_activity_feed
# ==============================================================================
    def _build_activity_feed(self) -> None:
        # Activity feed
        self.activity_title_label = QLabel("Activity", objectName="activity_title_label")
        self.page_layout.addWidget(self.activity_title_label)
        self.activity_list = QListWidget(objectName="activity_list")
        self.page_layout.addWidget(self.activity_list, 1)
        self.log_activity(f"DevOS started on {platform.node()} ({platform.system()} {platform.release()})")
# --- END [3.5] ---

# ==============================================================================
# [3.6] start_refresh_timer
# ==============================================================================
    def _start_refresh_timer(self) -> None:
        # Live refresh
        psutil.cpu_percent()  # first call primes the counter
        self.refresh_timer = QTimer(self, interval=self.REFRESH_INTERVAL_MS, timeout=self.refresh_stats)
        self.refresh_timer.start()
        self.refresh_stats()
# --- END [3.6] ---

# ==============================================================================
# [3.7] refresh_stats
# ==============================================================================
    def refresh_stats(self) -> None:
        current_datetime_text = QDateTime.currentDateTime().toString(self.SUBTITLE_DATETIME_FORMAT)
        self.page_subtitle_label.setText(current_datetime_text)

        cpu_percent = psutil.cpu_percent()
        self.cpu_card.set_value(f"{cpu_percent:.0f}%", cpu_percent)

        virtual_memory = psutil.virtual_memory()
        used_memory_gib = virtual_memory.used / self.BYTES_PER_GIB
        total_memory_gib = virtual_memory.total / self.BYTES_PER_GIB
        self.memory_card.set_value(f"{used_memory_gib:.1f} / {total_memory_gib:.0f} GB", virtual_memory.percent)

        disk_usage = psutil.disk_usage(self.DISK_MOUNT_PATH)
        self.disk_card.set_value(f"{disk_usage.percent:.0f}%", disk_usage.percent)

        uptime_seconds = int(time.time() - psutil.boot_time())
        uptime_hours, uptime_minutes = divmod(uptime_seconds // 60, 60)
        self.uptime_card.set_value(f"{uptime_hours}h {uptime_minutes:02d}m")
# --- END [3.7] ---

# ==============================================================================
# [3.8] log_activity
# ==============================================================================
    def log_activity(self, message: str) -> None:
        timestamp_text = QDateTime.currentDateTime().toString(self.ACTIVITY_TIME_FORMAT)
        self.activity_list.insertItem(0, f"[{timestamp_text}]  {message}")
# --- END [3.8] ---
