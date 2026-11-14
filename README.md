# DevOS
About:

Front End: Python (PySide6 / Qt 6)
Modules: C++, Python

Purpose:

# Updates

- **[2026-11-14] Code standard rollout.** Every Python file in `app/` now has a header, a `[DEFINITIONS]` glossary, an `[INDEX]`, and numbered section markers. Names follow the naming standard; see the Lexicon below. Renamed files: `home.py` → `home_page.py`, `placeholder.py` → `placeholder_page.py`, `Cost_Tracker.py` → `cost_dashboard_page.py`, `Cost_Dashboard.ui` → `cost_dashboard.ui`, icons `sidebar-*.svg` → `sidebar_*.svg`. Fixed a `//*` typo in `theme.py` that silently dropped the sidebar styles.
- **[2026-11-14] Collapsible sidebar.** The sidebar slides open and closed, and you can drag the seam to resize it.
- **[2026-11-14] Desktop launcher.** `run.sh` + `install-desktop.sh` add DevOS to the app menu and desktop.


## Tools/Startup

To start the app:

1. CD into the project folder devos, currently the folder location is: projects/devos project/devos
2. Run nix-shell
3. run python -m app.main

To add DevOS to the app menu and desktop (one time, from the same folder):
```sh
./install-desktop.sh
```


## Architecture

```
devos/
├── shell.nix                     # dev environment (Python, Qt6, gcc, cmake, pybind11)
├── run.sh                        # launcher used by the desktop icon (runs nix-shell + the app)
├── install-desktop.sh            # one-time: adds DevOS to the app menu and desktop
├── assets/
│   ├── devos.svg                 # app icon
│   └── icons/                    # sidebar + page icons (snake_case .svg)
├── app/
│   ├── main.py                   # entry point: main()
│   └── ui/
│       ├── theme.py              # all colors / QSS styling (palette, size tokens, *_QSS sections)
│       ├── main_window.py        # MainWindow: sidebar + top bar + page stack; page_entries registry
│       ├── forms/
│       │   └── cost_dashboard.ui # Qt Designer form for the Cost Tracking page
│       ├── pages/
│       │   ├── home_page.py            # HomePage: live stat cards + activity feed
│       │   ├── placeholder_page.py     # PlaceholderPage: stand-in for unbuilt apps
│       │   └── cost_dashboard_page.py  # CostDashboardPage: loads cost_dashboard.ui
│       └── widgets/
│           ├── sidebar.py        # Sidebar (collapsible navigation) + load_icon()
│           └── stat_card.py      # StatCard: reusable dashboard tile
└── core/                         # future C++ modules (pybind11)
```

**To add a new app/page:** create `app/ui/pages/<name>_page.py` with a `<Name>Page` class, put an icon in `assets/icons/`, and add one line to `page_entries` in `main_window.py` [3.2].


## Code Standards (summary)

Every code file follows: **Header → `[DEFINITIONS]` → `[INDEX]` → numbered sections** (`# [X.Y] NAME` … `# --- END [X.Y] ---`).
Names: files `snake_case`, classes `PascalCase`, functions `verb_noun`, booleans `is_/has_/can_/should_`, constants `SCREAMING_SNAKE_CASE` with units (`_PX`, `_MS`), widgets `snake_case` + type suffix (`_btn`, `_label`, `_panel` …). A widget's objectName equals its variable name, except reusable widgets, which share one name (`stat_card`).
Before inventing a name, check the Lexicon below; when adding a file, class, constant or shared term, add its row.


## PROJECT LEXICON & NAMING DICTIONARY

Alphabetical. **Where** = the file or the class that owns it.

| Term | Kind | Where | Meaning |
|---|---|---|---|
| `activity_list` | UI element (`QListWidget`) | `HomePage` | Newest-first log of app events on the home page. |
| `activity_title_label` | UI element (`QLabel`) | `HomePage` | "Activity" heading; styled like `page_title_label`. |
| `APP_ICON_PATH` | Constant (`Path`) | `app/main.py` | Absolute path to the app icon `assets/devos.svg`. |
| `APP_NAME` | Constant (`str`) | `app/main.py` | Display name of the application: `"DevOS"`. |
| `app_title_label` | UI element (`QLabel`) | `Sidebar` | "DevOS" title at the top of the sidebar. |
| `card_progress_bar` | UI element (`QProgressBar`) | `StatCard` | Thin bar under the value showing a 0–100% fill. |
| `card_title_label / card_value_label` | UI elements (`QLabel`) | `StatCard` | Small grey card name / large current value (shared objectNames). |
| `close_requested` | Signal | `Sidebar` | Emitted when the close button is clicked; `MainWindow` collapses the sidebar. |
| `close_sidebar_btn / open_sidebar_btn` | UI elements (`QToolButton`) | `Sidebar` / `MainWindow` top bar | Close the sidebar / reopen it (open button only shows while closed). |
| `*_COLOR` | Constants (`str`, hex) | `app/ui/theme.py` [2.1] | Palette. Use these instead of typing hex codes in widgets. |
| `content_panel` | UI element (`QWidget`) | `MainWindow` | Right-hand side of the splitter: top bar + page stack. |
| `cost_dashboard.ui` | Designer form | `app/ui/forms/` | Layout of the Cost Dashboard page, edited in Qt Designer. |
| `CostDashboardPage` | Class (`QWidget`) | `app/ui/pages/cost_dashboard_page.py` | Page shown as "Cost Tracking"; loads `cost_dashboard.ui`. |
| `DESKTOP_FILE_ID` | Constant (`str`) | `app/main.py` | `.desktop` launcher name without extension; must match the install script. |
| `fill_percent` | Parameter (`float`, %) | `StatCard.set_value()` | How full the progress bar should be (clamped to 0–100). |
| `find_form_widget()` | Method | `CostDashboardPage` | Looks up a widget in the Designer form by its objectName. |
| `form_panel` | UI element (`QWidget`) | `CostDashboardPage` | The widget tree built from the `.ui` file. |
| `FORMS_DIRECTORY` | Constant (`Path`) | `app/ui/pages/cost_dashboard_page.py` | Folder holding all Designer `.ui` files: `app/ui/forms/`. |
| `has_progress_bar` | Parameter (`bool`) | `StatCard()` | `False` builds a card with no bar (e.g. Uptime). |
| `HomePage` | Class (`QWidget`) | `app/ui/pages/home_page.py` | Home dashboard: live system stat cards plus the activity feed. |
| `ICON_SIZE` | Constant (`QSize`, px) | `app/ui/widgets/sidebar.py` | Size of every sidebar / top-bar icon (16 × 16). |
| `ICONS_DIRECTORY` | Constant (`Path`) | `app/ui/widgets/sidebar.py` | `assets/icons/`, where all SVG icons live. |
| `load_icon()` | Function | `app/ui/widgets/sidebar.py` | Loads an SVG from `ICONS_DIRECTORY`; warns instead of crashing if missing. |
| `log_activity()` | Method | `HomePage` | Adds a timestamped line to the top of `activity_list`. |
| `main()` | Function | `app/main.py` | Program entry point: builds the app, shows `MainWindow`, returns the exit code. |
| `main_splitter` | UI element (`QSplitter`) | `MainWindow` | Holds sidebar and content side by side; its handle is the draggable seam. |
| `MainWindow` | Class (`QMainWindow`) | `app/ui/main_window.py` | Top-level window: sidebar, top bar and page stack. |
| `nav_item_group` | `QButtonGroup` | `Sidebar` | Keeps exactly one sidebar item checked; maps items to page indexes. |
| `nav_sidebar_panel` | UI element (`Sidebar`) | `MainWindow` | The collapsible navigation sidebar. |
| `page_entries` | Data (`list[tuple]`) | `MainWindow` | Registry of apps: `(sidebar title, icon file, page widget)`. **Add new apps here.** |
| `page_selected` | Signal (`int`) | `Sidebar` | Emitted with the page index when an app is clicked. |
| `page_stack` | UI element (`QStackedWidget`) | `MainWindow` | Shows one page at a time; index matches `page_entries`. |
| `page_title_label / page_subtitle_label` | UI elements (`QLabel`) | Every page | Heading and grey sub-line at the top of a page; styled in `theme.py` [3.6]. |
| `PlaceholderPage` | Class (`QWidget`) | `app/ui/pages/placeholder_page.py` | Stand-in page showing a title and subtitle until the real page is built. |
| `*_PX (theme)` | Constants (`int`, px) | `app/ui/theme.py` [2.2] | Shared size tokens: `BASE_FONT_SIZE_PX`, `CORNER_RADIUS_PX`. |
| `*_QSS` | Constants (`str`) | `app/ui/theme.py` [3.x] | One group of style rules per widget kind. |
| `refresh_stats()` | Method | `HomePage` | Re-reads CPU, memory, disk and uptime and updates the cards. |
| `select_page()` | Method | `Sidebar` | Highlights an item from code without emitting `page_selected`. |
| `Sidebar` | Class (`QWidget`) | `app/ui/widgets/sidebar.py` | The collapsible navigation sidebar (objectName `nav_sidebar_panel`). |
| `sidebar_header_panel` | UI element (`QWidget`) | `Sidebar` | Row at the top of the sidebar: title + close button. |
| `sidebar_item_btn` | UI element (`QPushButton`) | `Sidebar` | One clickable app in the sidebar (shared objectName). |
| `sidebar_section_label` | UI element (`QLabel`) | `Sidebar` | Grey section heading above the app list ("Menu"). |
| `StatCard` | Class (`QFrame`) | `app/ui/widgets/stat_card.py` | Dashboard tile: title, big value, optional progress bar (objectName `stat_card`). |
| `STYLESHEET` | Constant (`str`) | `app/ui/theme.py` [4.1] | All `*_QSS` sections joined; applied once in `app/main.py`. |
| `top_bar` | UI element (`QWidget`) | `MainWindow` | Strip above the pages: open-sidebar button + page name. |
| `top_bar_title_label` | UI element (`QLabel`) | `MainWindow` | Name of the current page, shown in the top bar. |

### Approved abbreviations

| Abbreviation | Meaning |
|---|---|
| `app` | The `QApplication` instance; also the top-level Python package. |
| `btn` | Button (UI suffix). |
| `cpu` | Central processing unit. |
| `gib` | Gibibyte, 1024³ bytes. |
| `id` | Identifier. |
| `ms` | Milliseconds (unit suffix). |
| `px` | Pixels (unit suffix). |
| `qss` | Qt Style Sheets, Qt's CSS-like styling language. |
| `svg` | Scalable Vector Graphics (icon file format). |
| `ui` | User interface; also Qt Designer's `.ui` form files. |


## Notes

1. **`__init__.py` files** — Each folder under `app/` contains an `__init__.py`, which marks it as a Python package (a folder you can import from, e.g. `from app.ui.pages.home_page import HomePage`). They're currently blank, which is normal. Keep them even though they're empty: without them, Python can confuse our `app` folder with other folders of the same name, and tools like pytest and Nix packaging expect them. Later they can hold code that runs whenever the package is imported, such as a version number (`__version__ = "0.1.0"`) or shortcut imports.

2. On Launching from the desktop icon:
    - The app will take a few seconds to launch as it launches a nix-shell session each time.
    - If clicking the icon does nothing there may be an error. To check the log, run: cat ~/.cache/devos.log
    - If you move or rename the project folder, run ./install-desktop.sh again to update the paths.

3. **QSS comments** — Inside `theme.py` stylesheet text, only `/* ... */` is a comment. `//` is not a comment and breaks the next rule, and `#name` selects a widget by its objectName.
