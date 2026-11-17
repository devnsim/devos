# ==============================================================================
# FILE: app/ui/theme.py
# OVERVIEW: The single source of colors, sizes and Qt styles for every DevOS screen.
# PURPOSE:
#   1. Defines the color palette and size tokens (the "paint and rulers" of the app).
#   2. Holds the Qt Style Sheet (QSS) rules, one section per kind of widget.
#   3. Joins all sections into STYLESHEET, which app/main.py applies to the whole app.
# CONTRACT:
#   - Requires: nothing (no imports). Widgets set objectName to match the #selectors in the *_QSS
#               sections below (one section per area). `python tools/check_standard.py` lists any
#               #selector no widget uses, so this list can't silently go stale.
#   - Exposes: STYLESHEET (str) and every *_COLOR / *_PX token, for widgets that need a color in code.
# NOTES:
#   - [2026-11-17] Agents chat styles (#chat_*); the Part Design step list styles went (it is a painted step bar now).
#   - [2026-11-17] Styles for Canvas pages (#infinite_canvas_*, #module_card_panel, #card_*, #canvas_control*),
#                  the New Canvas button and the web app's floating options button (#web_app_options_btn).
#   - [2026-11-16] ERROR / WARNING / SUCCESS colour tokens and TAG_COLORS; styles for toolbars, the tool bag,
#                  record views, the module status line, Spreadsheet, Comms and Part Design.
#   - [2026-11-16] Styles for the tab row's +, the empty page's Create New panel, browser pages and the
#                  Database Editor (#db_*, #sql_*, #structure_*, #record_form_panel).
#   - [2026-11-14] Fixed "//*" comment typo before the sidebar rules; QSS only understands /* */,
#                  and the stray "/" made Qt discard the sidebar styles (centered, outlined items).
#   - [2026-11-14] Split the single STYLESHEET string into one *_QSS constant per section,
#                  joined in [4.1], so each widget group can be edited surgically.
#   - [2026-11-14] Renamed palette to *_COLOR and sizes to *_PX (ints); objectName selectors renamed
#                  to snake_case (e.g. #Sidebar -> #nav_sidebar_panel); widgets must match.
#   - [2026-11-14] Hard-coded "#2c2c2c", "#4a4a4a" and "white" moved into named palette colors.
#   - [2026-11-14] #activity_title_label (home page "Activity" heading) styled with #page_title_label.
#   - [2026-11-15] Added #sidebar_folder_btn (collapsible folder row) and indented nested items
#                  with #sidebar_item_btn[is_nested="true"].
#   - [2026-11-15] Nested-item indent now uses the nest_level property (levels 1-3) on items and folders.
#   - [2026-11-15] Split panes: #split_pane_btn, #close_pane_btn, #drop_overlay_panel, DROP_HIGHLIGHT_COLOR,
#                  and the active-pane accent #page_tab_bar[is_active_group="true"].
#   - [2026-11-15] TOP_BAR_QSS (#top_bar, #top_bar_title_label) replaced by TAB_AREA_QSS for the tab row.
#   - [2026-11-15] Module title bar + library bar styles removed; corner controls are #tile_controls_panel /
#                  #tile_control_btn (modules and blocks); System Log (#log_*) and #status_log_btn added.
#   - [2026-11-15] Health pass: module selectors made generic (#control_label, #module_controls_panel,
#                  #module_source_label, #module_stats_label, #module_summary_label) + tile / block canvas
#                  selectors (#tile_*, #block_*); rf_*/inspector_* selectors removed.
#   - [2026-11-15] Health pass: removed unused sections (group box, dock, progress bar, menu bar,
#                  tool bar, QStatusBar) and selectors (#activity_title_label, QTextEdit, QDateEdit ...).
#   - [2026-11-15] User pages: sidebar trees (#sidebar_tree), editable page title, Create New Page
#                  button; STAT_CARD_QSS (home page) replaced by STATUS_BAR_QSS.
#   - [2026-11-15] Integrated title bar: TITLE_BAR_HEIGHT_PX, window controls, #window_frame_panel edge.
#   - [2026-11-15] Added MODULE_QSS [3.8]: shared #module_panel frame plus the calendar module's rules
#                  (day cells use is_today / is_outside_month / is_weekend properties). Later sections
#                  renumbered 3.9-3.17.
#   - [2026-11-15] MODULE_QSS: module canvas tiles (#module_tile_panel, #module_header_panel...).
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
#    3.2 menu_qss
#    3.3 sidebar_qss
#    3.4 tab_area_qss
#    3.5 splitter_qss
#    3.6 page_heading_qss
#    3.7 status_bar_qss
#    3.8 module_qss
#    3.9 button_qss
#    3.10 input_qss
#    3.11 item_view_qss
#    3.12 tab_qss
#    3.13 scroll_bar_qss
#    3.14 tooltip_qss
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
DROP_HIGHLIGHT_COLOR   = "rgba(58, 121, 173, 40)"   # see-through ACCENT_HIGHLIGHT_COLOR over drop targets
ERROR_COLOR            = "#ef5350"   # failures, error text
WARNING_COLOR          = "#d9a441"   # warnings, "worth a look"
SUCCESS_COLOR          = "#66a866"   # ok / running
# Muted tag colours for select options, cards, bars and graph node labels (picked by a stable hash)
TAG_COLORS = ("#4f7cac", "#6b9a5b", "#b07d3c", "#9a5b8f", "#4f9a9a", "#a85656", "#7d6bb0", "#8a8a52")
# --- END [2.1] ---

# ==============================================================================
# [2.2] size_tokens
# ==============================================================================
BASE_FONT_SIZE_PX = 12
CORNER_RADIUS_PX  = 2         # set to 0 for fully square
TITLE_BAR_HEIGHT_PX = 36      # integrated title bar: sidebar header + top tab rows
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
# [3.2] menu_qss
# ==============================================================================
MENU_QSS = f"""
/* ---------- Menus and tool buttons ---------- */
QMenu {{ background: {PANEL_COLOR}; border: 1px solid {BORDER_COLOR}; padding: 2px; }}
QMenu::item {{ padding: 4px 24px 4px 12px; }}
QMenu::item:selected {{ background: {ACCENT_COLOR}; color: {SELECTED_TEXT_COLOR}; }}
QMenu::separator {{ height: 1px; background: {EDGE_COLOR}; margin: 3px 6px; }}
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
#sidebar_header_panel {{ background: {PANEL_COLOR}; border-bottom: 1px solid {BORDER_COLOR}; }}
#app_title_label {{ background: transparent; font-size: 12px; font-weight: 600; letter-spacing: 1px; }}
#sidebar_section_label {{ background: transparent; color: {MUTED_TEXT_COLOR}; font-size: 11px; padding: 10px 12px 4px 12px; }}
#sidebar_section_row {{ background: transparent; }}
#sidebar_tool_btn {{ background: transparent; border: none; border-radius: {CORNER_RADIUS_PX}px; padding: 3px; }}
#sidebar_tool_btn:hover {{ background: {HOVER_COLOR}; }}
/* Page / folder trees (Favorites, All Pages) */
#sidebar_tree {{ background: transparent; border: none; outline: 0; padding: 0px 4px; }}
#sidebar_tree::item {{ padding: 4px 4px; border-radius: {CORNER_RADIUS_PX}px; color: {TEXT_COLOR}; }}
#sidebar_tree::item:hover {{ background: {HOVER_COLOR}; }}
#sidebar_tree::item:selected {{ background: {ACCENT_COLOR}; color: {SELECTED_TEXT_COLOR}; }}
#sidebar_tree QLineEdit {{ padding: 0px 2px; }}
#sidebar_footer_panel {{ background: transparent; }}
#open_settings_btn {{ background: transparent; border: none; border-radius: {CORNER_RADIUS_PX}px; padding: 4px; }}
#open_settings_btn:hover {{ background: {HOVER_COLOR}; }}
#open_sidebar_btn, #close_sidebar_btn {{ background: transparent; border: none; padding: 3px; }}
#open_sidebar_btn:hover, #close_sidebar_btn:hover {{ background: {HOVER_COLOR}; }}
"""
# --- END [3.3] ---

# ==============================================================================
# [3.4] tab_area_qss
# ==============================================================================
TAB_AREA_QSS = f"""
/* ---------- Tab area (Obsidian-style tabs above pages) ---------- */
#tab_bar_panel {{ background: {PANEL_COLOR}; border-bottom: 1px solid {BORDER_COLOR}; }}
#page_tab_bar {{ background: transparent; }}
#page_tab_bar::tab {{
    background: transparent;
    border: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 6px 6px 10px;
    margin: 4px 1px 0px 1px;
    min-width: 80px;
    max-width: 200px;
    border-top: 2px solid transparent;
    color: {MUTED_TEXT_COLOR};
}}
#page_tab_bar::tab:selected {{ background: {BACKGROUND_COLOR}; color: {TEXT_COLOR}; }}
/* With 2+ panes, the active pane's selected tab gets an accent line (set in tab_group.py) */
#page_tab_bar[is_active_group="true"]::tab:selected {{ border-top: 2px solid {ACCENT_HIGHLIGHT_COLOR}; }}
#page_tab_bar::tab:hover:!selected {{ background: {HOVER_COLOR}; }}
#tab_close_btn {{ background: transparent; border: none; border-radius: {CORNER_RADIUS_PX}px; padding: 1px; }}
#tab_close_btn:hover {{ background: {HOVER_COLOR}; }}
#split_pane_btn, #close_pane_btn, #new_tab_btn {{ background: transparent; border: none; border-radius: {CORNER_RADIUS_PX}px; padding: 3px; }}
#split_pane_btn:hover, #close_pane_btn:hover, #new_tab_btn:hover {{ background: {HOVER_COLOR}; }}
/* Shown over a pane's page area while a dragged tab hovers it */
#drop_overlay_panel {{ background: {DROP_HIGHLIGHT_COLOR}; border: 1px solid {ACCENT_HIGHLIGHT_COLOR}; }}
#empty_tab_label {{ color: {MUTED_TEXT_COLOR}; font-size: 12px; background: transparent; }}
#empty_pane_panel {{ background: transparent; }}
#create_page_btn {{
    background: transparent; border: 1px dashed {EDGE_COLOR}; border-radius: 4px; padding: 10px 22px;
    font-size: 13px; color: {TEXT_COLOR};
}}
#create_page_btn:hover {{ background: {HOVER_COLOR}; border-color: {ACCENT_HIGHLIGHT_COLOR}; }}
/* Integrated title bar (frameless window): controls in the top-right tab row, resize edge around */
#window_frame_panel {{ background: {BORDER_COLOR}; }}
#window_controls_panel {{ background: transparent; border-left: 1px solid {BORDER_COLOR}; margin-left: 4px; }}
#window_minimize_btn, #window_maximize_btn, #window_close_btn {{
    background: transparent; border: none; border-radius: 0px; padding: 0px;
}}
#window_minimize_btn:hover, #window_maximize_btn:hover {{ background: {HOVER_COLOR}; }}
#window_close_btn:hover {{ background: #c42b1c; }}
#window_close_btn:pressed {{ background: #9b2216; }}
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
#page_title_label {{
    font-size: 13px;
    font-weight: 600;
    color: {TEXT_COLOR};
}}
#page_subtitle_label {{ color: {MUTED_TEXT_COLOR}; font-size: 11px; }}
/* Editable page title (page template): looks like a heading until focused */
#page_title_edit {{
    background: transparent; border: 1px solid transparent; border-radius: {CORNER_RADIUS_PX}px;
    font-size: 15px; font-weight: 600; color: {TEXT_COLOR}; padding: 1px 4px;
}}
#page_title_edit:hover {{ border-color: {EDGE_COLOR}; }}
#page_title_edit:focus {{ background: {BACKGROUND_COLOR}; border-color: {ACCENT_HIGHLIGHT_COLOR}; }}
/* Empty page: "Create New" with New Module / New Browser / New Canvas */
#create_new_panel {{ background: transparent; }}
#create_new_label {{ font-size: 18px; font-weight: 600; color: {TEXT_COLOR}; background: transparent; padding-bottom: 6px; }}
#new_module_btn, #new_browser_btn, #new_canvas_btn {{
    background: transparent; border: 1px dashed {EDGE_COLOR}; border-radius: 4px; padding: 10px 22px;
    font-size: 13px; color: {TEXT_COLOR}; min-width: 160px;
}}
#new_module_btn:hover, #new_browser_btn:hover, #new_canvas_btn:hover {{ background: {HOVER_COLOR}; border-color: {ACCENT_HIGHLIGHT_COLOR}; }}
/* Canvas pages (infinite_canvas_page.py): module cards on an endless grid */
#infinite_canvas_page_panel, #infinite_canvas_panel {{ background: {BACKGROUND_COLOR}; }}
#module_card_panel {{ background: {PANEL_COLOR}; border: 1px solid {EDGE_COLOR}; border-radius: 4px; }}
#module_card_panel[is_active="true"] {{ border-color: {ACCENT_HIGHLIGHT_COLOR}; }}
#card_header_panel {{ background: {HEADER_COLOR}; border-bottom: 1px solid {BORDER_COLOR}; border-top-left-radius: 4px; border-top-right-radius: 4px; }}
#card_title_label {{ color: {TEXT_COLOR}; font-weight: 600; background: transparent; }}
#card_resize_grip {{ background: transparent; }}
#canvas_controls_panel {{ background: {HEADER_COLOR}; border: 1px solid {EDGE_COLOR}; border-radius: 4px; }}
#canvas_control_btn {{ background: transparent; border: none; border-radius: 3px; padding: 2px 7px; color: {TEXT_COLOR}; }}
#canvas_control_btn:hover {{ background: {HOVER_COLOR}; }}
/* Browser pages */
#browser_page_panel {{ background: {BACKGROUND_COLOR}; }}
#browser_toolbar_panel, #find_bar_panel {{ background: {PANEL_COLOR}; border-bottom: 1px solid {BORDER_COLOR}; }}
#browser_nav_btn {{ background: transparent; border: none; border-radius: {CORNER_RADIUS_PX}px; padding: 2px 7px; font-size: 14px; }}
#browser_nav_btn:hover {{ background: {HOVER_COLOR}; }}
#browser_nav_btn:disabled {{ color: {SCROLLBAR_HOVER_COLOR}; }}
#browser_address_edit {{ border-radius: 12px; padding: 3px 10px; background: {BACKGROUND_COLOR}; }}
#browser_progress_bar {{ background: transparent; border: none; }}
#browser_progress_bar::chunk {{ background: {ACCENT_HIGHLIGHT_COLOR}; }}
#browser_unavailable_label {{ color: {MUTED_TEXT_COLOR}; qproperty-alignment: AlignCenter; padding: 20px; }}
/* Web app module: options button floating over the page while the browser bar is hidden */
#web_app_options_btn {{ background: {HEADER_COLOR}; border: 1px solid {EDGE_COLOR}; border-radius: 4px; padding: 1px 7px; font-size: 14px; }}
#web_app_options_btn:hover {{ background: {HOVER_COLOR}; }}
"""
# --- END [3.6] ---

# ==============================================================================
# [3.7] status_bar_qss
# ==============================================================================
STATUS_BAR_QSS = f"""
/* ---------- Status bar (bottom, IDE-style resource tiles) ---------- */
#system_status_bar {{ background: {HEADER_COLOR}; border-top: 1px solid {BORDER_COLOR}; }}
#status_message_label {{ background: transparent; color: {MUTED_TEXT_COLOR}; font-size: 11px; }}
#status_tile {{ background: transparent; color: {MUTED_TEXT_COLOR}; font-size: 11px; padding: 0px 7px; }}
#status_tile:hover {{ background: {HOVER_COLOR}; color: {TEXT_COLOR}; }}
#status_tile[level="warn"] {{ color: #e0a030; }}
#status_tile[level="high"] {{ color: {SELECTED_TEXT_COLOR}; background: #a33a2a; }}
#status_log_btn {{ background: transparent; border: none; border-radius: 0px; padding: 0px 8px; color: {TEXT_COLOR}; font-size: 11px; }}
#status_log_btn:hover {{ background: {HOVER_COLOR}; }}
#status_log_btn:checked {{ background: {ACCENT_COLOR}; color: {SELECTED_TEXT_COLOR}; }}
#status_log_btn[has_problems="true"] {{ color: #e0a030; }}
/* System Log panel (docked under the tab panes) */
#log_panel {{ background: {BACKGROUND_COLOR}; border-top: 1px solid {BORDER_COLOR}; }}
#log_header_panel {{ background: {PANEL_COLOR}; border-bottom: 1px solid {BORDER_COLOR}; }}
#log_title_label {{ font-weight: 600; background: transparent; padding-right: 8px; }}
#log_view {{ background: {BACKGROUND_COLOR}; border: none; font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; font-size: 11px; }}
#log_tool_btn {{ background: transparent; border: none; border-radius: {CORNER_RADIUS_PX}px; padding: 2px 6px; }}
#log_tool_btn:hover {{ background: {HOVER_COLOR}; }}
"""
# --- END [3.7] ---

# ==============================================================================
# [3.8] module_qss
# ==============================================================================
MODULE_QSS = f"""
/* ---------- Modules (self-contained panels hosted by pages) ---------- */
#module_panel {{ background: {PANEL_COLOR}; border: 1px solid {BORDER_COLOR}; border-radius: 0px; }}
#module_panel QLabel {{ background: transparent; }}
#module_title_label {{ font-weight: 600; }}
/* Calendar module */
#calendar_month_label {{ color: {TEXT_COLOR}; }}
#calendar_selected_label {{ color: {MUTED_TEXT_COLOR}; font-size: 11px; }}
#calendar_nav_btn {{ background: transparent; border: none; border-radius: {CORNER_RADIUS_PX}px; padding: 1px 6px; }}
#calendar_nav_btn:hover {{ background: {HOVER_COLOR}; }}
#calendar_weekday_label {{ color: {MUTED_TEXT_COLOR}; font-size: 11px; padding: 2px 0px; }}
#calendar_weekday_label[is_weekend="true"] {{ color: {SCROLLBAR_HOVER_COLOR}; }}
#calendar_day_btn {{
    background: {BACKGROUND_COLOR};
    border: 1px solid transparent;
    border-radius: {CORNER_RADIUS_PX}px;
    color: {TEXT_COLOR};
}}
#calendar_day_btn[is_weekend="true"] {{ color: {MUTED_TEXT_COLOR}; }}
#calendar_day_btn[is_outside_month="true"] {{ background: transparent; color: {SCROLLBAR_HOVER_COLOR}; }}
#calendar_day_btn:hover {{ background: {HOVER_COLOR}; }}
#calendar_day_btn[is_today="true"] {{ border: 1px solid {ACCENT_HIGHLIGHT_COLOR}; font-weight: 600; }}
#calendar_day_btn:checked {{ background: {ACCENT_COLOR}; color: {SELECTED_TEXT_COLOR}; }}
/* Tile canvases (tile_canvas.py): pages hold module tiles, modules hold block tiles */
#module_canvas_panel, #block_canvas_panel {{ background: transparent; }}
#tile_body_panel, #tile_picker_panel {{ background: transparent; }}
#module_tile_panel {{ background: {PANEL_COLOR}; border: 1px solid {BORDER_COLOR}; }}
#block_tile_panel {{ background: {BACKGROUND_COLOR}; border: 1px solid {BORDER_COLOR}; }}
/* Corner controls of module tiles and blocks (fade in on hover / focus) */
#tile_controls_panel {{ background: {HEADER_COLOR}; border: 1px solid {EDGE_COLOR}; border-radius: 4px; }}
#tile_control_btn {{ background: transparent; border: none; border-radius: 3px; padding: 2px 4px; color: {TEXT_COLOR}; }}
#tile_control_btn:hover {{ background: {HOVER_COLOR}; }}
#module_error_label {{ color: {ERROR_COLOR}; background: transparent; padding: 12px; }}
#empty_tile_label {{ color: {MUTED_TEXT_COLOR}; font-size: 13px; background: transparent; }}
/* Shared module chrome (app/modules/base.py, app/ui/ui_tools.py) */
#rf_inspector_panel, #wave_sim_panel, #trading_chart_panel, #tensor_inspector_panel, #module_controls_panel {{ background: transparent; }}
#control_label {{ color: {MUTED_TEXT_COLOR}; font-size: 11px; background: transparent; }}
#module_source_label, #module_stats_label {{ color: {MUTED_TEXT_COLOR}; font-size: 11px; background: transparent; }}
#module_summary_label {{ color: {TEXT_COLOR}; font-size: 11px; background: transparent; padding: 4px; }}
#scatter_unavailable_label {{ color: {MUTED_TEXT_COLOR}; background: {BACKGROUND_COLOR}; }}
#live_toggle_btn {{ background: {HEADER_COLOR}; border: 1px solid {BORDER_COLOR}; border-radius: {CORNER_RADIUS_PX}px; padding: 2px 8px; }}
#live_toggle_btn:hover {{ background: {HOVER_COLOR}; }}
#live_toggle_btn:checked {{ background: {ACCENT_COLOR}; color: {SELECTED_TEXT_COLOR}; }}
/* Tools: Graph, Notes, Library (+ their gear button and settings windows) */
#graph_panel, #notes_panel, #library_panel {{ background: transparent; }}
#module_settings_btn {{ background: transparent; border: none; border-radius: {CORNER_RADIUS_PX}px; padding: 3px; }}
#module_settings_btn:hover {{ background: {HOVER_COLOR}; }}
#note_name_edit {{ font-weight: 600; }}
#note_editor, #note_preview {{ background: {BACKGROUND_COLOR}; border: 1px solid {BORDER_COLOR}; padding: 6px; }}
#settings_dialog {{ background: {PANEL_COLOR}; }}
/* Database Editor */
#db_editor_panel, #record_form_panel {{ background: transparent; }}
#db_status_label {{ color: {MUTED_TEXT_COLOR}; font-size: 11px; background: transparent; padding: 0px 6px; }}
#db_status_label[is_error="true"] {{ color: {ERROR_COLOR}; }}
#db_tree {{ border: none; }}
#structure_title_label {{ font-weight: 600; background: transparent; }}
#sql_editor, #sql_preview_view, #structure_sql_view {{
    background: {BACKGROUND_COLOR}; border: 1px solid {BORDER_COLOR};
    font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; font-size: 12px;
}}
/* Shared module status line (ui_tools.status_label) */
#module_status_label {{ color: {MUTED_TEXT_COLOR}; font-size: 11px; background: transparent; padding: 0px 6px; }}
#module_status_label[is_error="true"] {{ color: {ERROR_COLOR}; }}
/* Module panels without their own background */
#records_module_panel, #spreadsheet_panel, #monitor_panel, #graph_db_panel, #map_panel, #web_app_panel, #comms_panel,
#cad_panel, #web_app_setup_panel {{ background: transparent; }}
/* Record views (record_views.py): board, form */
#board_column_panel {{ background: {PANEL_COLOR}; border: 1px solid {BORDER_COLOR}; border-radius: 4px; }}
#board_column_label {{ font-weight: 600; padding: 2px 6px; background: transparent; }}
#record_title_edit {{ font-size: 15px; font-weight: 600; background: transparent; border: 1px solid transparent; }}
#record_title_edit:focus {{ border: 1px solid {ACCENT_HIGHLIGHT_COLOR}; }}
#record_meta_label {{ color: {MUTED_TEXT_COLOR}; font-size: 11px; background: transparent; }}
#record_long_text_edit, #cypher_editor, #mail_body_view {{ background: {BACKGROUND_COLOR}; border: 1px solid {BORDER_COLOR}; }}
#cypher_editor, #formula_edit {{ font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; font-size: 12px; }}
#menu_glyph_btn::menu-indicator {{ image: none; width: 0px; }}
/* Agents chat (app/modules/agents/blocks.py) */
#chat_messages_panel {{ background: transparent; }}
#chat_message_panel {{ background: {PANEL_COLOR}; border: 1px solid {BORDER_COLOR}; border-radius: 6px; }}
#chat_message_panel[role="user"] {{ background: {HEADER_COLOR}; border-left: 3px solid {ACCENT_HIGHLIGHT_COLOR}; }}
#chat_caption_label {{ color: {MUTED_TEXT_COLOR}; font-size: 11px; font-weight: 600; background: transparent; }}
#chat_message_label {{ background: transparent; font-size: 13px; }}
#chat_agent_label {{ font-weight: 600; background: transparent; padding-right: 4px; }}
#chat_input_edit {{ background: {BACKGROUND_COLOR}; border: 1px solid {EDGE_COLOR}; border-radius: 4px; font-size: 13px; }}
#chat_input_edit:focus {{ border-color: {ACCENT_HIGHLIGHT_COLOR}; }}
/* Toolbars (toolbars.py) and the tool bag (tool_bag.py) */
#tool_tray_panel {{ background: {HEADER_COLOR}; border: 1px solid {BORDER_COLOR}; border-radius: 3px; }}
#tray_grip_label {{ color: {MUTED_TEXT_COLOR}; background: transparent; padding: 0px 3px; }}
#tray_tool_btn {{ background: transparent; border: 1px solid transparent; border-radius: 3px; padding: 0px; }}
#tray_tool_btn:hover {{ background: {HOVER_COLOR}; border-color: {EDGE_COLOR}; }}
#tray_empty_label {{ color: {MUTED_TEXT_COLOR}; font-size: 11px; background: transparent; padding: 0px 6px; }}
#tool_dock_panel {{ background: transparent; }}
#tool_bag_popup {{ background: {PANEL_COLOR}; border: 1px solid {EDGE_COLOR}; }}
#tool_bag_list {{ border: none; font-size: 13px; }}
#tool_bag_list::item {{ padding: 4px 6px; }}
/* Spreadsheet */
#cell_name_label {{ font-weight: 600; background: {HEADER_COLOR}; border: 1px solid {BORDER_COLOR}; padding: 2px 4px; }}
#sheet_tab_bar::tab {{ padding: 3px 12px; }}
/* Comms, Part Design */
#comms_banner_label {{ color: {WARNING_COLOR}; background: {HEADER_COLOR}; border-left: 3px solid {WARNING_COLOR}; padding: 6px; }}
#dial_btn {{ font-size: 16px; padding: 8px; }}
#part_design_window {{ background: {BACKGROUND_COLOR}; }}
/* Trading chart */
#chart_legend_label {{ font-size: 11px; background: transparent; padding: 0px 2px; }}
#timeframe_btn {{ padding: 2px 6px; }}
"""
# --- END [3.8] ---

# ==============================================================================
# [3.9] button_qss
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
# --- END [3.9] ---

# ==============================================================================
# [3.10] input_qss
# ==============================================================================
INPUT_QSS = f"""
/* ---------- Text inputs, spin boxes, combo boxes ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {BACKGROUND_COLOR};
    border: 1px solid {BORDER_COLOR};
    border-radius: {CORNER_RADIUS_PX}px;
    padding: 2px 4px;
    selection-background-color: {ACCENT_COLOR};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {ACCENT_HIGHLIGHT_COLOR};
}}
QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox QAbstractItemView {{
    background: {PANEL_COLOR};
    border: 1px solid {BORDER_COLOR};
    selection-background-color: {ACCENT_COLOR};
}}

QCheckBox {{ background: transparent; spacing: 6px; }}
"""
# --- END [3.10] ---

# ==============================================================================
# [3.11] item_view_qss
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
# [3.13] scroll_bar_qss
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
# --- END [3.13] ---

# ==============================================================================
# [3.14] tooltip_qss
# ==============================================================================
TOOLTIP_QSS = f"""
/* ---------- Tooltips ---------- */
QToolTip {{ background: {PANEL_COLOR}; color: {TEXT_COLOR}; border: 1px solid {EDGE_COLOR}; padding: 3px; }}
"""
# --- END [3.14] ---


# ==============================================================================
# [4.1] stylesheet
# ==============================================================================
# Order matters only when two rules are equally specific: later sections win.
STYLESHEET = "".join([
    BASE_QSS,
    MENU_QSS,
    SIDEBAR_QSS,
    TAB_AREA_QSS,
    SPLITTER_QSS,
    PAGE_HEADING_QSS,
    STATUS_BAR_QSS,
    MODULE_QSS,
    BUTTON_QSS,
    INPUT_QSS,
    ITEM_VIEW_QSS,
    TAB_QSS,
    SCROLL_BAR_QSS,
    TOOLTIP_QSS,
])
# --- END [4.1] ---
