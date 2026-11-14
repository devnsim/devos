# ==============================================================================
# FILE: app/ui/theme.py
# OVERVIEW: The single source of colors, sizes and Qt styles for every DevOS screen.
# PURPOSE:
#   1. Defines the color palette and size tokens (the "paint and rulers" of the app).
#   2. Holds the Qt Style Sheet (QSS) rules, one section per kind of widget.
#   3. Joins all sections into STYLESHEET, which app/main.py applies to the whole app.
# CONTRACT:
#   - Requires: nothing (no imports). Widgets must set objectName to match the #selectors below:
#               nav_sidebar_panel, sidebar_header_panel, app_title_label, sidebar_section_label,
#               sidebar_item_btn, open_sidebar_btn, close_sidebar_btn, top_bar, top_bar_title_label,
#               page_title_label, page_subtitle_label, stat_card, card_title_label, card_value_label
#   - Exposes: STYLESHEET (str) and every *_COLOR / *_PX token, for widgets that need a color in code.
# NOTES:
#   - [2026-11-14] Fixed "//*" comment typo before the sidebar rules; QSS only understands /* */,
#                  and the stray "/" made Qt discard the sidebar styles (centered, outlined items).
#   - [2026-11-14] Split the single STYLESHEET string into one *_QSS constant per section,
#                  joined in [4.1], so each widget group can be edited surgically.
#   - [2026-11-14] Renamed palette to *_COLOR and sizes to *_PX (ints); objectName selectors renamed
#                  to snake_case (e.g. #Sidebar -> #nav_sidebar_panel); widgets must match.
#   - [2026-11-14] Hard-coded "#2c2c2c", "#4a4a4a" and "white" moved into named palette colors.
#   - [2026-11-14] #activity_title_label (home page "Activity" heading) styled with #page_title_label.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - ACCENT_COLOR (str | hex): Selection / active blue for checked items and highlights.
# - ACCENT_HIGHLIGHT_COLOR (str | hex): Brighter accent for focus borders, tab tops, progress fill.
# - BASE_FONT_SIZE_PX (int | px): Default text size for every widget (default: 12).
# - CORNER_RADIUS_PX (int | px): Corner rounding for buttons and inputs (default: 2; 0 = square).
# - EDGE_COLOR (str | hex): Light top edge on buttons/inputs that gives a subtle bevel.
# - QSS (abbr.): Qt Style Sheets, Qt's CSS-like styling language. Inside QSS, "#name" selects a
#   widget by objectName and only /* ... */ is a comment.
# - *_QSS (str): One section of QSS rules; all sections are joined into STYLESHEET in [4.1].
# - STYLESHEET (str): The complete QSS applied by app/main.py via app.setStyleSheet().
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. DESIGN_NOTES
#    1.1 design_notes_docstring
# 2. PALETTE
#    2.1 color_palette
#    2.2 size_tokens
# 3. STYLESHEET_SECTIONS
#    3.1 base_qss
#    3.2 menu_and_status_qss
#    3.3 sidebar_qss
#    3.4 top_bar_qss
#    3.5 splitter_qss
#    3.6 page_heading_qss
#    3.7 stat_card_qss
#    3.8 button_qss
#    3.9 input_qss
#    3.10 item_view_qss
#    3.11 group_box_qss
#    3.12 tab_qss
#    3.13 dock_qss
#    3.14 progress_bar_qss
#    3.15 scroll_bar_qss
#    3.16 tooltip_qss
# 4. STYLESHEET_ASSEMBLY
#    4.1 stylesheet
# ==============================================================================

# ==============================================================================
# [1.1] design_notes_docstring
# ==============================================================================
"""
Central Qt stylesheet (QSS)

Styling and Theme North Star:
-"Pro Tool" Look, Flat, dark, workbench style tooling. (Think Cad, QGIS, unity type software UI)

Page Overview:
This page will dictate the coloring and styling across all UI elements.
Make Changes to colors or theme properties here instead of in individual ui pages.

General Notes:
- Everything between the triple quotes of each *_QSS = f block is stylesheet (QSS) text, not Python.
-- Braces: {{ }} are literal QSS braces; single { } insert a Python variable from the palette (e.g. {ACCENT_COLOR})
-- Comments inside QSS: only /* ... */ works. "//" is not a comment, and "#" means a widget's objectName.
"""
# --- END [1.1] ---

# ==============================================================================
# [2.1] color_palette
# ==============================================================================
# ---- Palette -------------------------------------------------------------
BACKGROUND_COLOR       = "#1e1e1e"   # window background
PANEL_COLOR            = "#282828"   # panels, cards, inputs
HEADER_COLOR           = "#323232"   # panel title bars, table headers, menu bar
HOVER_COLOR            = "#3a3a3a"
BORDER_COLOR           = "#141414"   # dark separators between panels
EDGE_COLOR             = "#3c3c3c"   # light edge on inputs / buttons
TEXT_COLOR             = "#c8c8c8"
MUTED_TEXT_COLOR       = "#8a8a8a"
ACCENT_COLOR           = "#2c5d87"   # selection blue
ACCENT_HIGHLIGHT_COLOR = "#3a79ad"
ALTERNATE_ROW_COLOR    = "#2c2c2c"   # every other row in tables / lists
SCROLLBAR_HOVER_COLOR  = "#4a4a4a"
SELECTED_TEXT_COLOR    = "#ffffff"   # text on ACCENT_COLOR backgrounds
# --- END [2.1] ---

# ==============================================================================
# [2.2] size_tokens
# ==============================================================================
BASE_FONT_SIZE_PX = 12
CORNER_RADIUS_PX  = 2         # set to 0 for fully square
# --- END [2.2] ---


# ==============================================================================
# [3.1] base_qss
# ==============================================================================
BASE_QSS = f"""
/* ---------- Base ---------- */
QWidget {{
    background: {BACKGROUND_COLOR};
    color: {TEXT_COLOR};
    font-size: {BASE_FONT_SIZE_PX}px;
}}
QLabel {{ background: transparent; }}
"""
# --- END [3.1] ---

# ==============================================================================
# [3.2] menu_and_status_qss
# ==============================================================================
MENU_AND_STATUS_QSS = f"""
/* ---------- Menu bar / menus / status bar ---------- */
QMenuBar {{ background: {HEADER_COLOR}; border-bottom: 1px solid {BORDER_COLOR}; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {HOVER_COLOR}; }}
QMenu {{ background: {PANEL_COLOR}; border: 1px solid {BORDER_COLOR}; padding: 2px; }}
QMenu::item {{ padding: 4px 24px 4px 12px; }}
QMenu::item:selected {{ background: {ACCENT_COLOR}; color: {SELECTED_TEXT_COLOR}; }}
QMenu::separator {{ height: 1px; background: {EDGE_COLOR}; margin: 3px 6px; }}
QStatusBar {{ background: {HEADER_COLOR}; border-top: 1px solid {BORDER_COLOR}; color: {MUTED_TEXT_COLOR}; }}
QToolBar {{ background: {HEADER_COLOR}; border-bottom: 1px solid {BORDER_COLOR}; spacing: 2px; padding: 2px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: {CORNER_RADIUS_PX}px; padding: 3px 6px; }}
QToolButton:hover {{ background: {HOVER_COLOR}; border-color: {EDGE_COLOR}; }}
QToolButton:checked {{ background: {ACCENT_COLOR}; }}
"""
# --- END [3.2] ---

# ==============================================================================
# [3.3] sidebar_qss
# ==============================================================================
SIDEBAR_QSS = f"""
/* ---------- Sidebar (navigation) ---------- */
#nav_sidebar_panel {{ background: {PANEL_COLOR}; }}
#sidebar_header_panel {{ background: transparent; }}
#app_title_label {{ background: transparent; font-size: 12px; font-weight: 600; letter-spacing: 1px; }}
#sidebar_section_label {{ background: transparent; color: {MUTED_TEXT_COLOR}; font-size: 11px; padding: 10px 12px 4px 12px; }}
#sidebar_item_btn {{
    background: transparent;
    border: none;
    border-radius: {CORNER_RADIUS_PX}px;
    padding: 5px 8px;
    margin: 0px 6px;
    text-align: left;
    color: {TEXT_COLOR};
}}
#sidebar_item_btn:hover   {{ background: {HOVER_COLOR}; }}
#sidebar_item_btn:checked {{ background: {ACCENT_COLOR}; color: {SELECTED_TEXT_COLOR}; }}
#open_sidebar_btn, #close_sidebar_btn {{ background: transparent; border: none; padding: 3px; }}
#open_sidebar_btn:hover, #close_sidebar_btn:hover {{ background: {HOVER_COLOR}; }}
"""
# --- END [3.3] ---

# ==============================================================================
# [3.4] top_bar_qss
# ==============================================================================
TOP_BAR_QSS = f"""
/* ---------- Top bar above pages ---------- */
#top_bar {{ background: {BACKGROUND_COLOR}; border-bottom: 1px solid {BORDER_COLOR}; }}
#top_bar_title_label {{ background: transparent; font-weight: 600; }}
"""
# --- END [3.4] ---

# ==============================================================================
# [3.5] splitter_qss
# ==============================================================================
SPLITTER_QSS = f"""
/* ---------- Seam between sidebar and pages ---------- */
QSplitter::handle {{ background: {BORDER_COLOR}; }}
QSplitter::handle:hover {{ background: {ACCENT_COLOR}; }}
"""
# --- END [3.5] ---

# ==============================================================================
# [3.6] page_heading_qss
# ==============================================================================
PAGE_HEADING_QSS = f"""
/* ---------- Page headings ---------- */
#page_title_label, #activity_title_label {{
    font-size: 13px;
    font-weight: 600;
    color: {TEXT_COLOR};
}}
#page_subtitle_label {{ color: {MUTED_TEXT_COLOR}; font-size: 11px; }}
"""
# --- END [3.6] ---

# ==============================================================================
# [3.7] stat_card_qss
# ==============================================================================
STAT_CARD_QSS = f"""
/* ---------- Stat cards (flat panels) ---------- */
#stat_card {{
    background: {PANEL_COLOR};
    border: 1px solid {BORDER_COLOR};
    border-radius: 0px;
}}
#stat_card QLabel {{ background: transparent; }}
#card_title_label {{ color: {MUTED_TEXT_COLOR}; font-size: 11px; }}
#card_value_label {{ font-size: 18px; font-weight: 600; }}
"""
# --- END [3.7] ---

# ==============================================================================
# [3.8] button_qss
# ==============================================================================
BUTTON_QSS = f"""
/* ---------- Buttons ---------- */
QPushButton {{
    background: {HEADER_COLOR};
    border: 1px solid {BORDER_COLOR};
    border-top-color: {EDGE_COLOR};
    border-radius: {CORNER_RADIUS_PX}px;
    padding: 3px 12px;
    min-height: 18px;
}}
QPushButton:hover   {{ background: {HOVER_COLOR}; }}
QPushButton:pressed {{ background: {BORDER_COLOR}; }}
QPushButton:checked {{ background: {ACCENT_COLOR}; color: {SELECTED_TEXT_COLOR}; }}
QPushButton:disabled {{ color: {MUTED_TEXT_COLOR}; }}
"""
# --- END [3.8] ---

# ==============================================================================
# [3.9] input_qss
# ==============================================================================
INPUT_QSS = f"""
/* ---------- Text inputs, spin boxes, combo boxes ---------- */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit {{
    background: {BACKGROUND_COLOR};
    border: 1px solid {BORDER_COLOR};
    border-radius: {CORNER_RADIUS_PX}px;
    padding: 2px 4px;
    selection-background-color: {ACCENT_COLOR};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QDateEdit:focus {{
    border: 1px solid {ACCENT_HIGHLIGHT_COLOR};
}}
QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox QAbstractItemView {{
    background: {PANEL_COLOR};
    border: 1px solid {BORDER_COLOR};
    selection-background-color: {ACCENT_COLOR};
}}

QCheckBox, QRadioButton {{ background: transparent; spacing: 6px; }}
"""
# --- END [3.9] ---

# ==============================================================================
# [3.10] item_view_qss
# ==============================================================================
ITEM_VIEW_QSS = f"""
/* ---------- Tables, trees, lists ---------- */
QTableView, QTreeView, QListView, QTableWidget, QTreeWidget, QListWidget {{
    background: {PANEL_COLOR};
    alternate-background-color: {ALTERNATE_ROW_COLOR};
    border: 1px solid {BORDER_COLOR};
    border-radius: 0px;
    gridline-color: {BORDER_COLOR};
    selection-background-color: {ACCENT_COLOR};
    selection-color: {SELECTED_TEXT_COLOR};
    outline: none;
}}
QListWidget::item, QTreeView::item {{ padding: 2px 4px; }}
QListWidget::item:hover, QTreeView::item:hover {{ background: {HOVER_COLOR}; }}
QHeaderView::section {{
    background: {HEADER_COLOR};
    color: {TEXT_COLOR};
    border: none;
    border-right: 1px solid {BORDER_COLOR};
    border-bottom: 1px solid {BORDER_COLOR};
    padding: 3px 6px;
}}
QTableCornerButton::section {{ background: {HEADER_COLOR}; border: none; }}
"""
# --- END [3.10] ---

# ==============================================================================
# [3.11] group_box_qss
# ==============================================================================
GROUP_BOX_QSS = f"""
/* ---------- Group boxes (panel with a title) ---------- */
QGroupBox {{
    background: {PANEL_COLOR};
    border: 1px solid {BORDER_COLOR};
    border-radius: 0px;
    margin-top: 18px;
    padding: 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 0px;
    padding: 2px 6px;
    color: {TEXT_COLOR};
    font-weight: 600;
}}
"""
# --- END [3.11] ---

# ==============================================================================
# [3.12] tab_qss
# ==============================================================================
TAB_QSS = f"""
/* ---------- Tabs ---------- */
QTabWidget::pane {{ border: 1px solid {BORDER_COLOR}; background: {PANEL_COLOR}; top: -1px; }}
QTabBar::tab {{
    background: {HEADER_COLOR};
    border: 1px solid {BORDER_COLOR};
    border-bottom: none;
    padding: 4px 12px;
    margin-right: 1px;
    color: {MUTED_TEXT_COLOR};
}}
QTabBar::tab:selected {{ background: {PANEL_COLOR}; color: {TEXT_COLOR}; border-top: 2px solid {ACCENT_HIGHLIGHT_COLOR}; }}
QTabBar::tab:hover:!selected {{ background: {HOVER_COLOR}; }}
"""
# --- END [3.12] ---

# ==============================================================================
# [3.13] dock_qss
# ==============================================================================
DOCK_QSS = f"""
/* ---------- Dock widgets (Unity-style panels) ---------- */
QDockWidget::title {{
    background: {HEADER_COLOR};
    padding: 4px 6px;
    border-bottom: 1px solid {BORDER_COLOR};
}}
QMainWindow::separator {{ background: {BORDER_COLOR}; width: 3px; height: 3px; }}
QMainWindow::separator:hover {{ background: {ACCENT_COLOR}; }}
"""
# --- END [3.13] ---

# ==============================================================================
# [3.14] progress_bar_qss
# ==============================================================================
PROGRESS_BAR_QSS = f"""
/* ---------- Progress bars ---------- */
QProgressBar {{
    background: {BACKGROUND_COLOR};
    border: 1px solid {BORDER_COLOR};
    border-radius: 0px;
    max-height: 4px;
}}
QProgressBar::chunk {{ background: {ACCENT_HIGHLIGHT_COLOR}; }}
"""
# --- END [3.14] ---

# ==============================================================================
# [3.15] scroll_bar_qss
# ==============================================================================
SCROLL_BAR_QSS = f"""
/* ---------- Scroll bars (thin, flat) ---------- */
QScrollBar:vertical   {{ background: {BACKGROUND_COLOR}; width: 10px;  margin: 0; }}
QScrollBar:horizontal {{ background: {BACKGROUND_COLOR}; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {HOVER_COLOR}; min-height: 20px; min-width: 20px; }}
QScrollBar::handle:hover {{ background: {SCROLLBAR_HOVER_COLOR}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
"""
# --- END [3.15] ---

# ==============================================================================
# [3.16] tooltip_qss
# ==============================================================================
TOOLTIP_QSS = f"""
/* ---------- Tooltips ---------- */
QToolTip {{ background: {PANEL_COLOR}; color: {TEXT_COLOR}; border: 1px solid {EDGE_COLOR}; padding: 3px; }}
"""
# --- END [3.16] ---


# ==============================================================================
# [4.1] stylesheet
# ==============================================================================
# Order matters only when two rules are equally specific: later sections win.
STYLESHEET = "".join([
    BASE_QSS,
    MENU_AND_STATUS_QSS,
    SIDEBAR_QSS,
    TOP_BAR_QSS,
    SPLITTER_QSS,
    PAGE_HEADING_QSS,
    STAT_CARD_QSS,
    BUTTON_QSS,
    INPUT_QSS,
    ITEM_VIEW_QSS,
    GROUP_BOX_QSS,
    TAB_QSS,
    DOCK_QSS,
    PROGRESS_BAR_QSS,
    SCROLL_BAR_QSS,
    TOOLTIP_QSS,
])
# --- END [4.1] ---
