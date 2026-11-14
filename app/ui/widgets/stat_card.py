# ==============================================================================
# FILE: app/ui/widgets/stat_card.py
# OVERVIEW: A reusable dashboard tile showing a title, a big value and an optional bar.
# PURPOSE:
#   1. Draws a small flat panel with a grey title and a large value underneath.
#   2. Optionally adds a thin progress bar that fills from 0 to 100%.
#   3. Lets pages update the value text and bar with one call: set_value().
# CONTRACT:
#   - Requires: QSS selectors #stat_card, #card_title_label, #card_value_label in app/ui/theme.py
#   - Exposes: StatCard(title_text, has_progress_bar=True) (QFrame),
#              set_value(value_text, fill_percent=None)
# NOTES:
#   - [2026-11-14] Renamed show_bar -> has_progress_bar (boolean prefix rule); callers must use the
#                  new keyword (home_page.py [3.4]).
#   - [2026-11-14] All cards share objectName "stat_card" (and card_title_label / card_value_label)
#                  so one QSS selector styles every copy; see standard 2.4.
#   - [2026-11-14] Card made denser: min height 120 -> 64, padding 18/16 -> 8/6.
#   - [2026-11-14] fill_percent is clamped to 0-100 before reaching the progress bar.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - EMPTY_VALUE_TEXT (str): Shown in the value label before the first update (default: "—").
# - fill_percent (float | % | None): Progress bar fill, clamped to 0-100; None leaves the bar unchanged.
# - has_progress_bar (bool): True adds the progress bar under the value; False omits it.
# - MARGIN_HORIZONTAL_PX / MARGIN_VERTICAL_PX (int | px): Inner padding (default: 8 / 6).
# - MIN_HEIGHT_PX (int | px): Smallest height the card may shrink to (default: 64).
# - PROGRESS_MAX_PERCENT (int | %): Value of a full bar (default: 100).
# - value_text (str): Already-formatted text for the big label, e.g. "17%" or "5.6 / 23 GB".
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 pyside_imports
# 2. STAT_CARD
#    2.1 class_constants
#    2.2 init_card
#    2.3 set_value
# ==============================================================================

# ==============================================================================
# [1.1] pyside_imports
# ==============================================================================
from PySide6.QtWidgets import QFrame, QLabel, QProgressBar, QVBoxLayout
# --- END [1.1] ---


# ==============================================================================
# [2.1] class_constants
# ==============================================================================
class StatCard(QFrame):
    """A dashboard tile: title, big value, optional progress bar."""

    MIN_HEIGHT_PX = 64
    MARGIN_HORIZONTAL_PX = 8
    MARGIN_VERTICAL_PX = 6
    PROGRESS_MAX_PERCENT = 100
    EMPTY_VALUE_TEXT = "—"
# --- END [2.1] ---

# ==============================================================================
# [2.2] init_card
# ==============================================================================
    def __init__(self, title_text: str, has_progress_bar: bool = True) -> None:
        super().__init__(objectName="stat_card")
        self.setMinimumHeight(self.MIN_HEIGHT_PX)

        self.card_layout = QVBoxLayout(self)
        self.card_layout.setContentsMargins(
            self.MARGIN_HORIZONTAL_PX, self.MARGIN_VERTICAL_PX,
            self.MARGIN_HORIZONTAL_PX, self.MARGIN_VERTICAL_PX,
        )

        self.card_title_label = QLabel(title_text, objectName="card_title_label")
        self.card_value_label = QLabel(self.EMPTY_VALUE_TEXT, objectName="card_value_label")
        self.card_layout.addWidget(self.card_title_label)
        self.card_layout.addWidget(self.card_value_label)
        self.card_layout.addStretch()

        self.card_progress_bar = None
        if has_progress_bar:
            self.card_progress_bar = QProgressBar(objectName="card_progress_bar", textVisible=False)
            self.card_progress_bar.setRange(0, self.PROGRESS_MAX_PERCENT)
            self.card_layout.addWidget(self.card_progress_bar)
# --- END [2.2] ---

# ==============================================================================
# [2.3] set_value
# ==============================================================================
    def set_value(self, value_text: str, fill_percent: float | None = None) -> None:
        self.card_value_label.setText(value_text)
        if self.card_progress_bar is not None and fill_percent is not None:
            clamped_percent = max(0, min(self.PROGRESS_MAX_PERCENT, fill_percent))
            self.card_progress_bar.setValue(int(clamped_percent))
# --- END [2.3] ---
