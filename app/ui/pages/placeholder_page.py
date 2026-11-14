# ==============================================================================
# FILE: app/ui/pages/placeholder_page.py
# OVERVIEW: A simple stand-in page that shows only a title and a subtitle.
# PURPOSE:
#   1. Fills a sidebar slot for an app that hasn't been built yet.
#   2. Shows the app's name and a one-line description at the top of the page.
# CONTRACT:
#   - Requires: QSS selectors #page_title_label, #page_subtitle_label in app/ui/theme.py
#   - Exposes: PlaceholderPage(title_text, subtitle_text="") (QWidget)
# NOTES:
#   - [2026-11-14] Renamed placeholder.py -> placeholder_page.py; objectNames renamed to
#                  page_title_label / page_subtitle_label; margins tightened to PAGE_MARGIN_PX.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - PAGE_MARGIN_PX (int | px): Empty space around the page content on all sides (default: 8).
# - subtitle_text (str): One-line description under the title; may be empty ("").
# - title_text (str): Page name shown as the heading.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 pyside_imports
# 2. PLACEHOLDER_PAGE
#    2.1 class_constants
#    2.2 init_page
# ==============================================================================

# ==============================================================================
# [1.1] pyside_imports
# ==============================================================================
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
# --- END [1.1] ---


# ==============================================================================
# [2.1] class_constants
# ==============================================================================
class PlaceholderPage(QWidget):
    """Simple stand-in until a real page is built."""

    PAGE_MARGIN_PX = 8
# --- END [2.1] ---

# ==============================================================================
# [2.2] init_page
# ==============================================================================
    def __init__(self, title_text: str, subtitle_text: str = "") -> None:
        super().__init__()
        self.page_layout = QVBoxLayout(self)
        self.page_layout.setContentsMargins(
            self.PAGE_MARGIN_PX, self.PAGE_MARGIN_PX, self.PAGE_MARGIN_PX, self.PAGE_MARGIN_PX
        )

        self.page_title_label = QLabel(title_text, objectName="page_title_label")
        self.page_subtitle_label = QLabel(subtitle_text, objectName="page_subtitle_label")
        self.page_layout.addWidget(self.page_title_label)
        self.page_layout.addWidget(self.page_subtitle_label)
        self.page_layout.addStretch()
# --- END [2.2] ---
