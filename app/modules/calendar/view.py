# ==============================================================================
# FILE: app/modules/calendar/view.py
# OVERVIEW: A self-contained month calendar panel that any page can host.
# PURPOSE:
#   1. Shows one month as a 6-row x 7-day grid (Monday first) with weekday headings.
#   2. Lets the user step months (‹ / ›), jump back to today, and click a day to select it.
#   3. Marks today, the selected day, weekends and days spilling in from nearby months.
#   4. Emits date_selected / month_changed so the hosting page can react.
# CONTRACT:
#   - Requires: QSS selectors #module_panel, #module_title_label, #calendar_month_label,
#               #calendar_nav_btn, #calendar_weekday_label, #calendar_day_btn,
#               #calendar_selected_label in app/ui/theme.py
#   - Exposes: CalendarModule(parent=None, context=None) (ModuleView); signals date_selected(QDate), month_changed(int, int);
#              selected_date(), set_selected_date(date), show_month(year, month), show_today()
# NOTES:
#   - [2026-11-15] Takes an optional ModuleContext; when given, the selected day is saved in the
#                  module's settings (SQLite) and restored next time.
#   - [2026-11-15] Health pass: built on ModuleView (shared settings helpers).
#   - [2026-11-15] Created. First "module": a panel with no page-specific logic, so it can later
#                  be placed freely on the snap-grid canvas. Shared objectName module_panel lets
#                  every future module share one frame style.
#   - [2026-11-15] Built from plain buttons rather than QCalendarWidget: QCalendarWidget's internal
#                  table view is hard to style with QSS and fights the dense dark theme.
#   - [2026-11-15] Day state is exposed as dynamic properties (is_today, is_outside_month,
#                  is_weekend) used as QSS selectors; buttons are repolished after each change.
#   - [2026-11-15] Moved from app/ui/modules/calendar_module.py (module apps live in app/modules/<name>/).
#   - [2026-11-15] Header row ends with reserve_controls_space() (module tiles' corner controls).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - DAY_COUNT_PER_WEEK (int): Columns in the grid (7).
# - GRID_WEEK_COUNT (int): Rows in the grid (6), enough for any month, so the panel never jumps size.
# - grid_start_date (QDate): Date in the top-left cell; the Monday on or before the 1st of the month.
# - module (term): A self-contained panel (title + content) that a page hosts; see app/modules/.
# - shown_month (tuple[int, int]): (year, month) currently displayed; may differ from the selected date.
# - TODAY_CHECK_INTERVAL_MS (int | ms): How often the panel checks if the date rolled over (default: 60000).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 pyside_imports
# 2. CALENDAR_MODULE
#    2.1 class_constants
#    2.2 init_module
#    2.3 build_header
#    2.4 build_day_grid
#    2.5 build_footer
#    2.6 public_api
#    2.7 render_month
#    2.8 handlers
# ==============================================================================

# ==============================================================================
# [1.1] pyside_imports
# ==============================================================================
from PySide6.QtCore import QDate, QLocale, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout,
)

from app.modules.base import ModuleView
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.ui_tools import set_style_property
# --- END [1.1] ---


# ==============================================================================
# [2.1] class_constants
# ==============================================================================
class CalendarModule(ModuleView):
    """Month calendar panel: navigate months, select a day."""

    date_selected = Signal(QDate)
    month_changed = Signal(int, int)

    TITLE_TEXT = "Calendar"
    DAY_COUNT_PER_WEEK = 7
    GRID_WEEK_COUNT = 6
    MARGIN_PX = 8
    SPACING_PX = 6
    CELL_SPACING_PX = 2
    MIN_CELL_SIZE_PX = 28
    TODAY_CHECK_INTERVAL_MS = 60_000
    MONTH_LABEL_FORMAT = "MMMM yyyy"
    SELECTED_LABEL_FORMAT = "dddd, d MMMM yyyy"
    PREVIOUS_MONTH_TEXT = "‹"
    NEXT_MONTH_TEXT = "›"
    TODAY_TEXT = "Today"
# --- END [2.1] ---

# ==============================================================================
# [2.2] init_module
# ==============================================================================
    def __init__(self, parent=None, context=None) -> None:
        super().__init__(parent, context, "module_panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)   # QSS frame on a QWidget subclass
        self.today_date = QDate.currentDate()
        self._selected_date = self.today_date
        self.shown_month = (self.today_date.year(), self.today_date.month())
        saved_date = QDate.fromString(self.setting("selected_date", ""), Qt.DateFormat.ISODate)
        if saved_date.isValid():
            self._selected_date = saved_date
            self.shown_month = (saved_date.year(), saved_date.month())
        self.day_btns: list[QToolButton] = []
        self.cell_dates: list[QDate] = []

        self.module_layout = QVBoxLayout(self)
        self.module_layout.setContentsMargins(self.MARGIN_PX, self.MARGIN_PX, self.MARGIN_PX, self.MARGIN_PX)
        self.module_layout.setSpacing(self.SPACING_PX)

        self._build_header()
        self._build_day_grid()
        self._build_footer()

        self.today_check_timer = QTimer(self, interval=self.TODAY_CHECK_INTERVAL_MS, timeout=self._check_today)
        self.today_check_timer.start()
        self._render_month()
# --- END [2.2] ---

# ==============================================================================
# [2.3] build_header
# ==============================================================================
    def _build_header(self) -> None:
        header_layout = QHBoxLayout()
        header_layout.setSpacing(self.CELL_SPACING_PX)

        self.module_title_label = QLabel(self.TITLE_TEXT, objectName="module_title_label")
        self.calendar_month_label = QLabel(objectName="calendar_month_label")
        self.previous_month_btn = self._nav_btn(self.PREVIOUS_MONTH_TEXT, "Previous month", lambda: self._step_month(-1))
        self.today_btn = self._nav_btn(self.TODAY_TEXT, "Go to today", self.show_today)
        self.next_month_btn = self._nav_btn(self.NEXT_MONTH_TEXT, "Next month", lambda: self._step_month(1))

        header_layout.addWidget(self.module_title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.calendar_month_label)
        header_layout.addSpacing(self.SPACING_PX)
        header_layout.addWidget(self.previous_month_btn)
        header_layout.addWidget(self.today_btn)
        header_layout.addWidget(self.next_month_btn)
        reserve_controls_space(header_layout)
        self.module_layout.addLayout(header_layout)

    def _nav_btn(self, text: str, tool_tip: str, on_click) -> QToolButton:
        nav_btn = QToolButton(objectName="calendar_nav_btn", text=text, toolTip=tool_tip)
        nav_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        nav_btn.clicked.connect(on_click)
        return nav_btn
# --- END [2.3] ---

# ==============================================================================
# [2.4] build_day_grid
# ==============================================================================
    def _build_day_grid(self) -> None:
        self.day_grid_layout = QGridLayout()
        self.day_grid_layout.setSpacing(self.CELL_SPACING_PX)

        # Weekday headings, Monday first (Qt day numbers 1-7 = Mon-Sun)
        locale = QLocale()
        for column_index in range(self.DAY_COUNT_PER_WEEK):
            weekday_label = QLabel(
                locale.dayName(column_index + 1, QLocale.FormatType.ShortFormat),
                objectName="calendar_weekday_label",
                alignment=Qt.AlignmentFlag.AlignCenter,
            )
            set_style_property(weekday_label, "is_weekend", column_index >= 5)
            self.day_grid_layout.addWidget(weekday_label, 0, column_index)

        # Exclusive group: checking one day unchecks the previous one
        self.day_btn_group = QButtonGroup(self)
        self.day_btn_group.setExclusive(True)
        for cell_index in range(self.GRID_WEEK_COUNT * self.DAY_COUNT_PER_WEEK):
            day_btn = QToolButton(objectName="calendar_day_btn", checkable=True)
            day_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            day_btn.setMinimumSize(self.MIN_CELL_SIZE_PX, self.MIN_CELL_SIZE_PX)
            day_btn.clicked.connect(lambda _checked=False, i=cell_index: self._on_day_clicked(i))
            self.day_btn_group.addButton(day_btn)
            self.day_btns.append(day_btn)
            row_index, column_index = divmod(cell_index, self.DAY_COUNT_PER_WEEK)
            self.day_grid_layout.addWidget(day_btn, row_index + 1, column_index)

        self.module_layout.addLayout(self.day_grid_layout, 1)
# --- END [2.4] ---

# ==============================================================================
# [2.5] build_footer
# ==============================================================================
    def _build_footer(self) -> None:
        self.calendar_selected_label = QLabel(objectName="calendar_selected_label")
        self.module_layout.addWidget(self.calendar_selected_label)
# --- END [2.5] ---

# ==============================================================================
# [2.6] public_api
# ==============================================================================
    def selected_date(self) -> QDate:
        return self._selected_date

    def set_selected_date(self, date: QDate) -> None:
        """Select a day and show its month. Emits date_selected if the day changed."""
        if not date.isValid():
            return
        has_changed = date != self._selected_date
        self._selected_date = date
        self.show_month(date.year(), date.month())
        if has_changed:
            self.save_setting("selected_date", date.toString(Qt.DateFormat.ISODate))
            self.date_selected.emit(date)

    def show_month(self, year: int, month: int) -> None:
        """Display a month without changing the selection. Emits month_changed if it changed."""
        if (year, month) == self.shown_month:
            self._render_month()
            return
        self.shown_month = (year, month)
        self._render_month()
        self.month_changed.emit(year, month)

    def show_today(self) -> None:
        self.set_selected_date(QDate.currentDate())
# --- END [2.6] ---

# ==============================================================================
# [2.7] render_month
# ==============================================================================
    def _render_month(self) -> None:
        year, month = self.shown_month
        first_of_month = QDate(year, month, 1)
        # Step back to Monday (dayOfWeek: 1 = Mon ... 7 = Sun)
        grid_start_date = first_of_month.addDays(1 - first_of_month.dayOfWeek())

        self.calendar_month_label.setText(first_of_month.toString(self.MONTH_LABEL_FORMAT))
        self.calendar_selected_label.setText(self._selected_date.toString(self.SELECTED_LABEL_FORMAT))

        self.cell_dates = []
        for cell_index, day_btn in enumerate(self.day_btns):
            cell_date = grid_start_date.addDays(cell_index)
            self.cell_dates.append(cell_date)
            day_btn.setText(str(cell_date.day()))
            day_btn.setToolTip(cell_date.toString(self.SELECTED_LABEL_FORMAT))
            day_btn.setChecked(cell_date == self._selected_date)
            set_style_property(day_btn, "is_today", cell_date == self.today_date)
            set_style_property(day_btn, "is_outside_month", cell_date.month() != month)
            set_style_property(day_btn, "is_weekend", cell_date.dayOfWeek() >= 6)

        # Exclusive groups can't uncheck everything; drop exclusivity if the selection is off-grid
        if self._selected_date not in self.cell_dates:
            self.day_btn_group.setExclusive(False)
            for day_btn in self.day_btns:
                day_btn.setChecked(False)
            self.day_btn_group.setExclusive(True)
# --- END [2.7] ---

# ==============================================================================
# [2.8] handlers
# ==============================================================================
    def _on_day_clicked(self, cell_index: int) -> None:
        # Clicking a greyed day from a neighbouring month also moves to that month
        self.set_selected_date(self.cell_dates[cell_index])

    def _step_month(self, month_offset: int) -> None:
        year, month = self.shown_month
        target_date = QDate(year, month, 1).addMonths(month_offset)
        self.show_month(target_date.year(), target_date.month())

    def _check_today(self) -> None:
        current_date = QDate.currentDate()
        if current_date != self.today_date:
            self.today_date = current_date
            self._render_month()
# --- END [2.8] ---
