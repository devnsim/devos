# DevOS
About:

Front End: Python (PySide6 / Qt 6), pyqtgraph + PyOpenGL for plots, NumPy for data
Modules: module apps in app/modules/ (Python UI + engine, C++20 core via pybind11, SQLite data)

Purpose:

# Updates

- **[2026-11-16] Toolbars, records & views, nine new modules.**
  - **Toolbars:** every module tile can hold toolbars (grip ⠿ → *Add toolbar*). A toolbar arrives blank; right-click it to add tools from the tool bag, dock it to an edge or float it, resize, pin or remove it. Drag the grip to snap it to another edge; drag tools to reorder. Global tools (calculator, quick note, capture, timestamp, colour picker, web search, new browser tab, System Log) work everywhere; module tools only show for their module. **Ctrl+K** searches and runs any tool.
  - **Records & views:** a reusable "every line is a record with properties" model (SQLite, JSON values). The same records show as a table, a board, a timeline (Gantt), a calendar or a form; users can add their own properties. **Agents** (role, type, model, permissions, tools, instructions; export an agent file) and **Projects** use it.
  - **New modules:** Spreadsheet, Graph Database (Kùzu), Map (GIS), CAD with the Part Design workbench, Comms (email; text / calls are placeholders), Software Monitor, Web App (containerised sites, n8n preset).
  - **Fixes:** the status bar's temperature reading stalled the UI every second; Qt WebEngine crashed on exit.
- **[2026-11-16] Database Editor, browser pages, empty pages.**
  - **Pages:** a **+** right after the last tab (or Ctrl+T) creates a page, like the sidebar's +. New pages start empty, with **Create New**: *New Module* adds a tile with the module dropdown, and *New Browser* turns the page into a web browser in the same tab. Closing a page's last module brings Create New back.
  - **Browser pages:** back / forward / reload / home, an address bar that also searches, find in page, zoom, and a persistent profile (logins kept). Downloads go to your Library. A page is named after the website until you rename it, and links that open a new window become new browser pages.
  - **Database Editor (module):** create, import, export, rename and delete your own SQLite databases, which are kept in the Library. Its blocks are an explorer tree, a data grid (edit in place, sort, SQL filter, paging, NULLs, copy, save as Graph dataset), a record form, the table structure (columns, indexes, foreign keys, CREATE SQL), an SQL console (Ctrl+Enter, timing, history) and properties (integrity check, compact, journal mode, user version). The DevOS system database opens read-only.
  - **Fixes:** mouse handling moved from app-wide event filters to `window_events.py`, because PySide crashed on Qt WebEngine's internal objects. `check_standard.py` now accepts NOTES dated on or after the last commit.
- **[2026-11-15] Tools, content library and System Log.** New pages open with one empty tile and its "What goes here?" dropdown (no library bar, no module title bar; the split / close controls sit in the fading corner). New tools: **Graph** (datasets, CSV import, mock data, settings window), **Notes** (Markdown, Obsidian-style, autosaved), **Library** (browse / rename / import / export everything DevOS keeps for you); the Trading Chart is now the **Financial Chart** with a settings window (data source, speed, chart type, theme, indicators, strategy). User files (notes, datasets, RF captures, CAD models) live in the content library inside the data folder, so there is no save dialog. User preferences go in SQLite. **System Log**: the *Log* tile in the status bar opens a resizable log panel under the pages. It shows readable, real-time announcements of what the app does (start-up, database, modules, datasets, saves, failures with details), with filters, past sessions and a warning badge, and it is saved to the database every 15 s and at exit.
- **[2026-11-14] Code standard rollout.** Every Python file in `app/` now has a header, a `[DEFINITIONS]` glossary, an `[INDEX]`, and numbered section markers. Names follow the naming standard; see the Lexicon below. Renamed files: `home.py` → `home_page.py`, `placeholder.py` → `placeholder_page.py`, `Cost_Tracker.py` → `cost_dashboard_page.py`, `Cost_Dashboard.ui` → `cost_dashboard.ui`, icons `sidebar-*.svg` → `sidebar_*.svg`. Fixed a `//*` typo in `theme.py` that silently dropped the sidebar styles.
- **[2026-11-14] Collapsible sidebar.** The sidebar slides open and closed, and you can drag the seam to resize it.
- **[2026-11-15] Health pass + modules as canvases.** One tiling engine for tab panes, module tiles and blocks (`tile_canvas.py`); modules are canvases of blocks with fading corner controls; shared `ModuleView`, `ui_tools`, `plot_tools`; unused code and styles removed; `tools/check_standard.py`, `tests/` (pytest) and `AGENTS.md` for agents.
- **[2026-11-15] User pages.** Pages and folders are user data (SQLite): create (+, folder+, *Create New Page* in empty panes), rename (page heading, sidebar, tabs), drag to rearrange, favourites, delete. Every page is a `CanvasPage` (the template). Sidebar: Favorites / All Pages, settings gear bottom-right. Removed: Home, Trading Dashboard pages, fixed Project Editor (migrated into a user page). IDE-style status bar with resource monitors.
- **[2026-11-15] Integrated title bar.** Frameless window: the sidebar header and top tab rows are the title bar (drag empty space to move, double-click to maximise), window controls sit in the top-right tab row, thin edge resizes. Works on Wayland (system move/resize).
- **[2026-11-15] Trading Chart module.** Candles / Heikin-Ashi / line, 6 timeframes, volume, indicator registry (SMA, EMA, Bollinger, VWAP, RSI, MACD, ATR), drawings, crosshair legend, lookahead-free backtester with strategies, live mock BTC-USD feed (deterministic, adjustable speed); C++ `ema` in devos_core.
- **[2026-11-15] Module apps.** Module library + "Add module" on canvases, SQLite data layer (settings, layouts, module tables), C++20 engine (`core/`, pybind11) with NumPy fallback, RF Wave Simulator module (CAD import, dielectric ray tracing, 4D CSI tensor).
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
├── shell.nix                     # dev environment (Python, Qt6, NumPy, pyqtgraph, trimesh, gcc, cmake, pybind11)
├── run.sh                        # launcher: builds the C++ engine on first run, then starts the app
├── install-desktop.sh            # one-time: adds DevOS to the app menu and desktop
├── assets/
│   ├── devos.svg                 # app icon
│   └── icons/                    # sidebar + page icons (snake_case .svg)
├── AGENTS.md                     # START HERE: architecture, shared tools, recipes, rules (CLAUDE.md points here)
├── tools/check_standard.py       # checks the code standard (headers, index, sections, dated notes, QSS)
├── tests/                        # pytest suite (offscreen Qt, temporary database): python -m pytest -q
├── core/                         # native C++20 engine (pybind11 extension "devos_core")
│   ├── CMakeLists.txt
│   ├── src/bindings.cpp          # the devos_core Python module (binds everything below)
│   ├── src/wave_engine.cpp       # ray-mesh tracing + RF channel synthesis
│   ├── src/series.cpp            # time-series filters (ema) for indicators and simulations
│   └── build/                    # build output (git-ignored): devos_core*.so
├── app/
│   ├── main.py                   # entry point: main()
│   ├── native/                   # bridge to devos_core (+ identical NumPy fallback, build helper)
│   │   ├── __init__.py           # trace_segments(), synthesize_response(), HAS_NATIVE, self_test()
│   │   ├── numpy_engine.py       # pure-NumPy mirror of the C++ functions
│   │   └── build.py              # python -m app.native.build [--if-missing]
│   ├── data/                     # shared data layer (no Qt)
│   │   ├── activity_log.py       # ActivityLog: global announcements (log.step), hooks, saved per session
│   │   ├── tensor4d.py           # Tensor4D: 4D NumPy array with named axes + slicing
│   │   ├── em_materials.py       # EM material library: permittivity / conductivity models, Fresnel
│   │   └── store/
│   │       ├── database.py       # SQLite: migrations per namespace, module settings, layouts, preferences
│   │       ├── content.py        # ContentStore: the user's files (notes, datasets, captures, CAD, databases, sheets …) in <data>/library/
│   │       ├── pages.py          # PageStore: the user's pages (canvas / browser), folders, favourites
│   │       └── records.py        # RecordCollection: records with declared properties (agents, projects …)
│   ├── modules/                  # MODULE APPS: each = view (UI) + engine (logic / C++) + store (SQLite)
│   │   ├── registry.py           # MODULE_LIBRARY, ModuleSpec, ModuleContext, create_module()
│   │   ├── base.py               # ModuleView: base class of every module view (settings, live loop, blocks)
│   │   ├── records_module.py     # RecordsModule: base of record modules (toolbar + record views)
│   │   ├── agents/, projects/    # record modules: view.py
│   │   ├── spreadsheet/          # formulas.py (no eval), engine.py (sheets, xlsx / csv, LibreOffice), view.py
│   │   ├── graph_db/             # Kùzu graph databases: engine.py, view.py
│   │   ├── map/                  # Leaflet map: store.py (places), view.py
│   │   ├── cad/                  # engine.py (parts), mesh_view.py (3D), workbench.py (Part Design window), view.py
│   │   ├── comms/                # email: engine.py (IMAP / SMTP / keyring), view.py (+ text / calls placeholders)
│   │   ├── monitor/              # Software Monitor: engine.py (sampling, insights), view.py
│   │   ├── web_app/              # Web App: a site in a tile with its own container: view.py
│   │   ├── rf_imaging/           # RF imaging 2.5-6 GHz: sensor.py, imaging.py, blocks.py, view.py
│   │   ├── wave_sim/             # RF wave simulator 1-10 GHz: physics.py, scene.py, engine.py, store.py, blocks.py, view.py
│   │   ├── db_editor/            # Database Editor: engine.py (no Qt), view.py (toolbar + state), blocks.py, dialogs.py
│   │   ├── graph/                # Graph tool: datasets.py (load / import / mock), view.py (plot + settings window)
│   │   ├── notes/                # Markdown notes tool: view.py
│   │   ├── library/              # content library browser: view.py
│   │   ├── trading_chart/        # Financial Chart: market_data.py, indicators.py, backtest.py, chart_items.py, store.py, view.py
│   │   ├── tensor_inspector/     # generic 4D tensor viewer (waterfall + scatter blocks): view.py
│   │   └── calendar/             # month calendar: view.py
│   └── ui/
│       ├── theme.py              # all colors / QSS styling (palette, size tokens, *_QSS sections)
│       ├── main_window.py        # MainWindow: sidebar + tab workspace + status bar; page provider for TabArea
│       ├── gl_support.py         # shared OpenGL probe for 3D views
│       ├── ui_tools.py           # set_style_property(), control_label(), tool_button(), block helpers, run_in_thread()
│       ├── tool_bag.py           # ToolSpec registry, ToolBagPopup (search), global tools, active module
│       ├── toolbars.py           # ToolDock / ToolTray: toolbars around every module tile
│       ├── record_views.py       # RecordsController + table / board / timeline / calendar / form blocks
│       ├── plot_tools.py         # styled_plot(), style_plot_item() for pyqtgraph
│       ├── settings_dialog.py    # SettingsDialog: the separate settings window tools open from their gear
│       ├── web_support.py        # Qt WebEngine: start-up flag, availability, browser profile, downloads -> Library
│       ├── window_events.py      # mouse_hub(): window-level mouse presses (title bar, active pane / tile)
│       ├── canvas/
│       │   ├── tile_canvas.py    # TileCanvas / CanvasTile: the one tiling canvas (tiles, drag, split, save)
│       │   ├── module_canvas.py  # ModuleCanvas / ModuleTile: page level, tiles with the module dropdown
│       │   └── block_canvas.py   # BlockCanvas / BlockTile: module level, fading corner controls
│       ├── pages/
│       │   ├── canvas_page.py          # CanvasPage: THE page template (editable title + Create New / module canvas)
│       │   ├── browser_page.py         # BrowserPage: a page that is a web browser
│       │   └── placeholder_page.py     # PlaceholderPage: stand-in (used by Settings for now)
│       └── widgets/
│           ├── sidebar.py        # Sidebar (collapsible navigation) + load_icon()
│           ├── tab_area.py       # TabArea: tiling workspace of tab panes (nested splitters)
│           ├── tab_group.py      # TabGroup: one pane of tabs (+ PageTabBar for drag-and-drop)
│           ├── tiling_layout.py  # shared splitter-tree engine (split, swap, tidy, drop zones)
│           ├── status_bar.py     # SystemStatusBar: IDE-style bottom bar with resource tiles + Log tile
│           ├── log_panel.py      # LogPanel: the System Log view (filters, search, past sessions)
│           └── title_bar.py      # integrated title bar: window controls, resize frame, drag-to-move
```

**To add a module app:** see AGENTS.md. In short: create `app/modules/<name>/view.py` with a `ModuleView` subclass whose `__init__` takes `context=None` (a `ModuleContext`: `instance_id`, `database`, `settings`). Put logic in `engine.py` (heavy loops can go to `core/` C++ with a NumPy mirror in `app/native/`), tables in `store.py` (`database.migrate("<name>", [...])`, tables prefixed `<name>_`), then add one `ModuleSpec` to `MODULE_LIBRARY` in `app/modules/registry.py`. It then appears in every empty tile's "What goes here?" dropdown. Announce what it does with `get_activity_log()` (see AGENTS.md).

**Data:** everything lives in the data folder `~/.local/share/devos/` (override with `DEVOS_DATA_DIR`): `devos.sqlite3` (pages, settings, preferences, layouts, logs, the library index) and `library/<kind>/` (the user's files). Closing a tile removes it from the layout but keeps its data.

**C++ engine:** `python -m app.native.build` (run.sh does this automatically the first time). Without it, DevOS uses the NumPy mirror (same results, ~3x slower); `python -c "from app import native; print(native.self_test())"` compares the two.

**Pages are user data:** create them with the sidebar's **+** (or *Create New Page* in an empty pane), organise them in folders by drag and drop, rename them in the page heading, the sidebar (F2 / double-click) or the tab (double-click / right-click). Every page is a `CanvasPage` — **change `app/ui/pages/canvas_page.py` to change all pages.**


## Code Standards (summary)

Every code file follows: **Header → `[DEFINITIONS]` → `[INDEX]` → numbered sections** (`# [X.Y] NAME` … `# --- END [X.Y] ---`).
Names: files `snake_case`, classes `PascalCase`, functions `verb_noun`, booleans `is_/has_/can_/should_`, constants `SCREAMING_SNAKE_CASE` with units (`_PX`, `_MS`), widgets `snake_case` + type suffix (`_btn`, `_label`, `_panel` …). A widget's objectName equals its variable name, except reusable widgets, which share one name (`status_tile`).
Before inventing a name, check the Lexicon below; when adding a file, class, constant or shared term, add its row.


## PROJECT LEXICON & NAMING DICTIONARY

Alphabetical. **Where** = the file or the class that owns it.

| Term | Kind | Where | Meaning |
|---|---|---|---|
| `APP_ICON_PATH` | Constant (`Path`) | `app/main.py` | Absolute path to the app icon `assets/devos.svg`. |
| `APP_NAME` | Constant (`str`) | `app/main.py` | Display name of the application: `"DevOS"`. |
| `app_title_label` | UI element (`QLabel`) | `Sidebar` | "DevOS" title at the top of the sidebar. |
| `close_requested` | Signal | `Sidebar` | Emitted when the close button is clicked; `MainWindow` collapses the sidebar. |
| `close_sidebar_btn / open_sidebar_btn` | UI elements (`QToolButton`) | `Sidebar` / `TabArea` tab row | Close the sidebar / reopen it (open button only shows while closed). |
| `*_COLOR` | Constants (`str`, hex) | `app/ui/theme.py` [2.1] | Palette. Use these instead of typing hex codes in widgets. |
| `content_panel` | UI element (`QWidget`) | `MainWindow` | Right-hand side of the splitter: top bar + page stack. |
| `DESKTOP_FILE_ID` | Constant (`str`) | `app/main.py` | `.desktop` launcher name without extension; must match the install script. |
| `ICON_SIZE` | Constant (`QSize`, px) | `app/ui/widgets/sidebar.py` | Size of every sidebar / tab icon (16 × 16). |
| `ICONS_DIRECTORY` | Constant (`Path`) | `app/ui/widgets/sidebar.py` | `assets/icons/`, where all SVG icons live. |
| `load_icon()` | Function | `app/ui/widgets/sidebar.py` | Loads an SVG from `ICONS_DIRECTORY`; warns instead of crashing if missing. |
| `main()` | Function | `app/main.py` | Program entry point: builds the app, shows `MainWindow`, returns the exit code. |
| `main_splitter` | UI element (`QSplitter`) | `MainWindow` | Holds sidebar and content side by side; its handle is the draggable seam. |
| `MainWindow` | Class (`QMainWindow`) | `app/ui/main_window.py` | Top-level window: sidebar, top bar and page stack. |
| `nav_item_group` | `QButtonGroup` | `Sidebar` | Keeps exactly one sidebar item checked; maps items to page indexes. |
| `nav_sidebar_panel` | UI element (`Sidebar`) | `MainWindow` | The collapsible navigation sidebar. |
| `page_selected` | Signal (`int`) | `Sidebar` | Emitted with the page index when an app is clicked. |
| `page_stack` | UI element (`QStackedWidget`) | `TabGroup` | Shows the pane's selected page, or the empty-pane hint. |
| `page_title_label / page_subtitle_label` | UI elements (`QLabel`) | Every page | Heading and grey sub-line at the top of a page; styled in `theme.py` [3.6]. |
| `PlaceholderPage` | Class (`QWidget`) | `app/ui/pages/placeholder_page.py` | Stand-in page showing a title and subtitle until the real page is built. |
| `*_PX (theme)` | Constants (`int`, px) | `app/ui/theme.py` [2.2] | Shared size tokens: `BASE_FONT_SIZE_PX`, `CORNER_RADIUS_PX`. |
| `*_QSS` | Constants (`str`) | `app/ui/theme.py` [3.x] | One group of style rules per widget kind. |
| `select_page()` | Method | `Sidebar` | Highlights a page in Favorites and All Pages from code (opens its folders). |
| `clear_selection()` | Method | `Sidebar` | Removes every highlight (used when no tab is open). |
| `Sidebar` | Class (`QWidget`) | `app/ui/widgets/sidebar.py` | The collapsible navigation sidebar (objectName `nav_sidebar_panel`). |
| `sidebar_header_panel` | UI element (`QWidget`) | `Sidebar` | Row at the top of the sidebar: title + close button. |
| `sidebar_section_label` | UI element (`QLabel`) | `Sidebar` | Grey section heading: "Favorites", "All Pages". |
| `STYLESHEET` | Constant (`str`) | `app/ui/theme.py` [4.1] | All `*_QSS` sections joined; applied once in `app/main.py`. |
| `TabArea` | Class (`QWidget`) | `app/ui/widgets/tab_area.py` | Tiling workspace: panes (`TabGroup`) arranged in any mix of rows and columns; seams resize them. Dropping a tab on a pane's edge splits that pane on that side. A sidebar click opens the page in the active pane, or switches to it wherever it is open. Ctrl+W / Ctrl+Tab / Ctrl+Shift+Tab act on the active pane. |
| `TabGroup` | Class (`QWidget`) | `app/ui/widgets/tab_group.py` | One pane: tab row + page area. × / middle-click close; drag inside the row to reorder, drag out to move the tab to another pane. Split (new pane to the right) and close-pane buttons on the right. |
| module | Term | `app/modules/` | A small app a page hosts in a tile: `ModuleView` (UI) + engine + store; its views are blocks. |
| block | Term | `app/ui/canvas/block_canvas.py` | One view inside a module (3D scene, waterfall, slice…); split / rearrange / resize / close / replace like modules on a page. |
| `CalendarModule` | Class (`ModuleView`) | `app/modules/calendar/view.py` | Month calendar module: ‹ / Today / › navigation, click to select a day; signals `date_selected(QDate)`, `month_changed(int, int)`. |
| `Tensor4D` | Class | `app/data/tensor4d.py` | 4D NumPy array with named axes (`t, x, y, z`): `take`, `volume`, `plane`, `line`, `value`, `window`, `reduce`, `waterfall`, `to_points`, `push` (streaming), `demo()`, `inspect()`. |
| `TensorInspectorModule` | Class (`ModuleView`) | `app/modules/tensor_inspector/view.py` | Live inspector for a `Tensor4D` as blocks: waterfalls (time × one axis) and 3D scatters of one time step; `launch_inspector()` opens it in its own window. |
| `RfInspectorModule` | Class (`ModuleView`) | `app/modules/rf_imaging/view.py` | RF imaging module: toolbar (live, calibrate, save/load, view mode, timeline) + blocks (3D scene, range-time, spectrum, slice, materials in `blocks.py`). |
| `RfSensor` | Protocol | `app/modules/rf_imaging/sensor.py` | Interface every data source implements: `config`, `array`, `description`, `read_frame()`, `calibration_measurements()`. Real hardware drivers go here. |
| `MockRfSensor` | Class | `app/modules/rf_imaging/sensor.py` | Simulated 2.5–6 GHz MIMO sensor: room scene + walking/breathing person, with unknown per-channel hardware response, leakage and noise. |
| `RfFrame` / `RfCapture` | Classes | `app/modules/rf_imaging/sensor.py` | One sweep (`response[pair, bin]`, complex) / a recorded test (raw frames + calibration), saved as `.npz`. |
| `RfImagingPipeline` | Class | `app/modules/rf_imaging/imaging.py` | Per frame: calibrate → backproject → clutter/motion → track → materials; keeps rolling `Tensor4D` histories. |
| `SparseReflectorJob` | Class | `app/modules/rf_imaging/imaging.py` | Orthogonal matching pursuit: finds the few reflectors that explain the data and fits each one's per-band reflectivity (for material ID). |
| `MATERIALS` | Constant (tuple) | `app/data/em_materials.py` | Material library (ITU-R P.2040 models + approx. tissue/water + metal); position = material id. |
| `MODULE_LIBRARY` | Constant (tuple) | `app/modules/registry.py` | Every module app a canvas can add; one `ModuleSpec` (id, title, category, description, view path) each. |
| `ModuleContext` | Class | `app/modules/registry.py` | Given to a module view on creation: `instance_id`, `database`, `settings` (its persisted key/values). |
| `Database` | Class | `app/data/store/database.py` | SQLite data layer: `migrate(namespace, scripts)`, query helpers, layouts, instances; `get_database()` = app-wide one. |
| `ModuleSettings` | Class | `app/data/store/database.py` | Per-instance JSON key/value store (`get`, `set`, `all`). |
| `devos_core` | C++ module | `core/src/wave_engine.cpp` | Native engine: `trace_segments` (ray-mesh), `synthesize_response` (RF channel). Mirrored in `app/native/numpy_engine.py`. |
| `WaveSimModule` | Class (`ModuleView`) | `app/modules/wave_sim/view.py` | RF Wave Simulator: 3D twin with rays, time x frequency waterfall, dielectric spectroscopy, CAD import, snapshots. |
| `WaveSimEngine` | Class | `app/modules/wave_sim/engine.py` | Direct + first-order reflection paths (image method), dielectric ray tracing, H[node, f], WaveTensor. |
| `WaveTensor` | Class | `app/modules/wave_sim/physics.py` | float32 ring buffer (nodes, time, frequency, [amp, phase]); `as_tensor4d()`. |
| `TradingChartModule` | Class (`ModuleView`) | `app/modules/trading_chart/view.py` | Trading chart module: price/volume/indicator/equity panes, timeframes, drawings, live feed, backtest overlay. |
| `MarketDataSource` | Protocol | `app/modules/trading_chart/market_data.py` | Interface every price feed implements: `now()`, `history(days)`, `bars_since(time)`. Real exchange connectors go here. |
| `MockBitcoinFeed` | Class | `app/modules/trading_chart/market_data.py` | Deterministic mock BTC-USD 1-minute feed from 2026-01-01 (volatility clustering, jumps, session volume), live partial bar. |
| `BarSeries` | Class | `app/modules/trading_chart/market_data.py` | Columnar OHLCV bars (UTC open times): `resample()`, `merge()`, `slice()`. |
| `INDICATORS` | Constant (dict) | `app/modules/trading_chart/indicators.py` | Indicator registry (`IndicatorSpec`: compute, pane, lines, levels); add one to make it available in the chart. |
| `STRATEGIES` / `run_backtest` | Registry / function | `app/modules/trading_chart/backtest.py` | Strategies return target positions per bar; the backtester fills at the next open with fees and reports equity, trades and stats. |
| integrated title bar | Concept | `app/ui/widgets/title_bar.py` | Frameless window chrome: `WindowControls` (top-right tab row), `WindowFrame` (resize edge), `TitleBarDragFilter` (empty space in regions marked with `mark_title_bar_region()` moves the window). |
| `TITLE_BAR_HEIGHT_PX` | Constant (`int` px) | `app/ui/theme.py` | Height shared by the sidebar header and every tab row, so they line up as one bar (default: 36). |
| `CanvasPage` | Class (`QWidget`) | `app/ui/pages/canvas_page.py` | The page template: editable title (`PageTitleEdit`) + `ModuleCanvas` keyed `page:<id>`. Every user page is one. |
| `PageStore` | Class | `app/data/store/pages.py` | Pages and folders in SQLite: create, rename, move (`apply_tree`), favourites, delete. |
| page id | Concept (`int`) | `MainWindow` / `TabArea` | A page's database id; `0` is Settings. Used for tabs, sidebar items and drag-and-drop. |
| `Sidebar` | Class (`QWidget`) | `app/ui/widgets/sidebar.py` | Favorites + All Pages trees (drag to reorder / into folders, F2 rename, context menus), + page / + folder, settings gear. |
| `SystemStatusBar` | Class (`QWidget`) | `app/ui/widgets/status_bar.py` | Bottom bar: DevOS CPU / memory, UI lag, system CPU / RAM, network, CPU temperature (GPU when exposed); amber/red past thresholds. |
| `TileCanvas` / `CanvasTile` | Classes | `app/ui/canvas/tile_canvas.py` | The one tiling canvas: add / split / close / move (edge = beside, centre = swap), empty-tile picker, active tile, `to_layout()` / `restore_layout()`. |
| `ModuleCanvas` / `ModuleTile` | Classes | `app/ui/canvas/module_canvas.py` | Page-level canvas of module tiles (no title bar; corner controls); a new page starts with one empty tile whose dropdown picks the module; layout saved per page. |
| `CornerControlsTile` | Class (`CanvasTile`) | `app/ui/canvas/tile_canvas.py` | Tile with fading corner controls (⠿ grip / menu, split, close); base of `ModuleTile` and `BlockTile`. |
| `reserve_controls_space()` | Function | `app/ui/canvas/tile_canvas.py` | Adds `CONTROLS_RESERVE_PX` of space at the right of a tool's top row so the corner controls don't cover its widgets. |
| `ActivityLog` / `get_activity_log()` | Class / function | `app/data/activity_log.py` | The System Log: `info` / `success` / `warning` / `error`, `step(source, action)` (announces success or FAILED + details), listeners, saved per session to SQLite (`log_sessions`, `log_entries`). |
| `LogPanel` | Class (`QWidget`) | `app/ui/widgets/log_panel.py` | System Log view docked under the tab area: session, level, source and text filters, auto-scroll, copy. Opened by `status_log_btn`. |
| `status_log_btn` | UI element (`QToolButton`) | `SystemStatusBar` | "▤ Log" tile: toggles the log panel; shows "⚠ N" for unseen warnings / errors. |
| `ContentStore` / `get_content_store()` | Class / function | `app/data/store/content.py` | The user's content library: files in `<data dir>/library/<kind>/`, indexed in `content_items`; create, read, write, rename, delete, import, export. |
| content kind | Concept (`str`) | `CONTENT_KINDS` | `note`, `dataset`, `capture`, `cad`, `database`, `graph`, `sheet`, `geo`, `image`, `file`. |
| preferences | Concept | `Database.get_preference()` / `set_preference()` | User-level settings (JSON) in `user_preferences`, e.g. `log_panel_open`. |
| `SettingsDialog` | Class (`QDialog`) | `app/ui/settings_dialog.py` | Separate, non-modal settings window with tabs and Apply / OK / Close; signal `applied`. |
| `GraphModule` | Class (`ModuleView`) | `app/modules/graph/view.py` | 2D graph tool; gear opens data (datasets, import CSV, mock) and appearance settings. |
| `Dataset` / `make_mock()` | Class / function | `app/modules/graph/datasets.py` | Table of numbers from a library CSV / generated mock data (`MOCK_GENERATORS`). |
| `NotesModule` | Class (`ModuleView`) | `app/modules/notes/view.py` | Markdown notes: pick / create / rename / delete, Edit / Split / Preview, autosave. |
| `LibraryModule` | Class (`ModuleView`) | `app/modules/library/view.py` | Browser for the content library: filter by kind, rename, import, export, delete, preview. |
| page kind | Concept (`str`) | `app/data/store/pages.py` | `"canvas"` (modules, `CanvasPage`) or `"browser"` (`BrowserPage`); pages also keep a small JSON state (`page_state()`). |
| Create New panel | UI element (`create_new_panel`) | `CanvasPage` | What an empty page shows: New Module / New Browser. |
| `new_tab_btn` | UI element (`QToolButton`) | `TabGroup` | The + right after the last tab: new page (Ctrl+T). |
| `BrowserPage` | Class (`QWidget`) | `app/ui/pages/browser_page.py` | Web browser page: navigation, address / search bar, find, zoom; named after the site until renamed. |
| `web_support` | Module | `app/ui/web_support.py` | `prepare_web_engine()` (before QApplication), `web_engine_unavailable_reason()`, `browser_profile()`, `address_to_url()`. |
| `mouse_hub()` / `WindowMouseHub` | Function / class | `app/ui/window_events.py` | Mouse presses of watched windows: `pressed(QWidget)` signal, `add_press_handler()`. Use instead of app-wide event filters. |
| `DbEditorModule` | Class (`ModuleView`) | `app/modules/db_editor/view.py` | Database Editor: toolbar + shared state (connection, table, query, row) for its blocks. |
| `UserDatabases` / `DbConnection` | Classes | `app/modules/db_editor/engine.py` | The user's SQLite databases (Library kind `database`, plus the read-only system database) / one open database: rows, schema, SQL, properties. |
| row key | Concept (`dict`) | `db_editor/engine.py` | How an edit finds its row again: `{"rowid": n}`, or the primary-key columns for WITHOUT ROWID tables. |
| `CHART_THEMES` | Constant (dict) | `app/modules/trading_chart/chart_items.py` | Up / down colour pairs for the Financial Chart. |
| `BlockCanvas` / `BlockSpec` | Classes | `app/ui/canvas/block_canvas.py` | Module-level canvas of blocks; corner controls fade in on hover / focus; layout + block state saved in module settings. |
| `ModuleView` | Class (`QWidget`) | `app/modules/base.py` | Base of every module view: `setting()` / `save_setting()`, `setup_live()` (pauses when hidden), `build_block_canvas()`. |
| `check_standard.py` | Tool | `tools/` | Verifies the code standard; run before finishing any task. |
| `tiling_layout` | Module | `app/ui/widgets/tiling_layout.py` | Shared tiling engine used by `TabArea` and `ModuleCanvas`: `insert_beside`, `swap_leaves`, `normalize_tree`, `edge_zone_at`. |
| `PageTabBar` | Class (`QTabBar`) | `app/ui/widgets/tab_group.py` | Tab row that turns a tab into a drag-and-drop item once the mouse leaves the row. |
| active pane | Concept | `TabArea` | The pane last clicked, dropped on or opened in; marked by an accent line on its selected tab when 2+ panes exist. |
| `PAGE_MIME_TYPE` | Constant (`str`) | `app/ui/widgets/tab_group.py` | Drag-and-drop format of a dragged tab; carries only the page id. |
| `open_page()` | Method | `TabArea` | Opens a page by id in the active pane, or selects its existing tab. |
| `split_pane()` | Method | `TabArea` | Adds an empty pane on one side (`left`/`right`/`top`/`bottom`, default right) of a pane, giving it half that pane's space, and makes it active. |
| drop zone | Concept | `TabGroup` | Where a dragged tab lands: tab row (insert there), centre (add as tab), or the outer quarter on each side (split that way). The blue overlay previews it. |
| layout tree | Concept | `tiling_layout.py` (`TabArea`, `ModuleCanvas`) | `root_splitter` and nested `QSplitter`s are branches, panes or module tiles are leaves; tidied after every close so no splitter is empty, single-child or same-direction-nested. |
| `current_page_changed` | Signal (`int`) | `TabArea` | Page id shown in the active pane, or `-1` when it has no tab. |
| `page_tab_bar` | UI element (`PageTabBar`) | `TabGroup` | A pane's row of tabs. |
| `root_splitter` | UI element (`QSplitter`) | `TabArea` | Top of the layout tree (objectName `pane_splitter`, shared by every splitter in the tree). |
| `empty_tab_label` | UI element (`QLabel`) | `TabGroup` | Hint in an empty pane: "Pick a page from the sidebar" (one pane) or "Drag a page here" (split). |
| record / `RecordCollection` | Concept / class | `app/data/store/records.py` | "Every line is a record with properties": records of one collection in SQLite (JSON values); `get_collection(name, defaults)` shares one per name; users can add properties. |
| `PropertySpec` | Class | `app/data/store/records.py` | One declared property: key, title, kind (text, long_text, number, select, multi_select, date, checkbox, url), options. |
| view (record view) | Concept | `app/ui/record_views.py` | One picture of the same records: `TableBlock`, `BoardBlock`, `TimelineBlock` (Gantt), `CalendarBlock`, `FormBlock`; shared selection / search via `RecordsController`. |
| `RecordsModule` | Class (`ModuleView`) | `app/modules/records_module.py` | Base of record modules (set `COLLECTION`, `NOUN`, `PROPERTIES`, `DEFAULT_LAYOUT`); `AgentsModule`, `ProjectsModule` use it. |
| `agent_definition()` | Function | `app/modules/agents/view.py` | An agent record as Markdown with front matter (name, description, model, tools from permissions) + instructions. |
| tool / `ToolSpec` | Concept / class | `app/ui/tool_bag.py` | A small action (glyph, title, description, `run(module_view)`); global, or bound to one module type (`module_type_id`). `register_tools()`, `tools_for()`, `search_tools()`. |
| `ToolBagPopup` | Class | `app/ui/tool_bag.py` | Searchable tool list: adds tools to a toolbar ("add") or runs one on the active module ("run", Ctrl+K). |
| `ToolDock` / `ToolTray` | Classes | `app/ui/toolbars.py` | Toolbars around every module tile: trays dock top / bottom / left / right or float, sizes, pin; saved in the module's `toolbars` setting. |
| `run_in_thread()` | Function | `app/ui/ui_tools.py` | Slow work on a worker thread; `on_done(result, error)` back on the UI thread. |
| `status_label()` / `set_status()` | Functions | `app/ui/ui_tools.py` | A module's one-line outcome label (red when `is_error`). |
| `SpreadsheetModule` / `Workbook` / `Sheet` | Classes | `app/modules/spreadsheet/` | Spreadsheet: grid with formula bar and sheet tabs; formulas in `formulas.py` (no eval); xlsx / csv, LibreOffice for ods / xls / pdf. |
| `GraphDbModule` / `GraphDb` | Classes | `app/modules/graph_db/` | Kùzu graph databases (Library kind `graph`): schema, Cypher console, graph view, inspector. |
| `MapModule` / `MapStore` | Classes | `app/modules/map/` | Leaflet map: base layers, places (SQLite `map_places`), GeoJSON layers (Library kind `geo`). |
| `CadModule` / `Part` / `PartDesignWindow` | Classes | `app/modules/cad/` | CAD models in 3D (`MeshViewer`); parametric parts (sketches + pad / pocket / hole) designed in the Part Design window. |
| `CommsModule` / `CommsStore` | Classes | `app/modules/comms/` | Email over IMAP / SMTP (passwords in the system keyring); text and calls are placeholders. |
| `MonitorModule` / `find_insights()` | Class / function | `app/modules/monitor/` | Software Monitor: system history, processes (scanned off the UI thread), inefficiency insights, DevOS footprint. |
| `WebAppModule` / container | Class / concept | `app/modules/web_app/view.py`, `web_support.container_profile()` | A site in a module tile with its own persistent browser profile (separate logins); presets for local tools (n8n). |
| `shutdown_web_engine()` | Function | `app/ui/web_support.py` | Deletes web pages before their profiles when the app (or test run) ends; avoids a Qt WebEngine segfault. |

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

1. **`__init__.py` files** — Each folder under `app/` contains an `__init__.py`, which marks it as a Python package (a folder you can import from, e.g. `from app.ui.pages.canvas_page import CanvasPage`). They're currently blank, which is normal. Keep them even though they're empty: without them, Python can confuse our `app` folder with other folders of the same name, and tools like pytest and Nix packaging expect them. Later they can hold code that runs whenever the package is imported, such as a version number (`__version__ = "0.1.0"`) or shortcut imports.

2. On Launching from the desktop icon:
    - The app will take a few seconds to launch as it launches a nix-shell session each time.
    - If clicking the icon does nothing there may be an error. To check the log, run: cat ~/.cache/devos.log
    - If you move or rename the project folder, run ./install-desktop.sh again to update the paths.

3. **QSS comments** — Inside `theme.py` stylesheet text, only `/* ... */` is a comment. `//` is not a comment and breaks the next rule, and `#name` selects a widget by its objectName.
