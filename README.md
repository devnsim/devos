# DevOS
About:

Front End: Python (PySide6 / Qt 6), pyqtgraph + PyOpenGL for plots, NumPy for data
Modules: module apps in app/modules/ (Python UI + engine, C++20 core via pybind11, SQLite data)

Purpose:

# Updates

- **[2026-11-14] Code standard rollout.** Every Python file in `app/` now has a header, a `[DEFINITIONS]` glossary, an `[INDEX]`, and numbered section markers. Names follow the naming standard; see the Lexicon below. Renamed files: `home.py` → `home_page.py`, `placeholder.py` → `placeholder_page.py`, `Cost_Tracker.py` → `cost_dashboard_page.py`, `Cost_Dashboard.ui` → `cost_dashboard.ui`, icons `sidebar-*.svg` → `sidebar_*.svg`. Fixed a `//*` typo in `theme.py` that silently dropped the sidebar styles.
- **[2026-11-14] Collapsible sidebar.** The sidebar slides open and closed, and you can drag the seam to resize it.
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
├── core/                         # native C++20 engine (pybind11 extension "devos_core")
│   ├── CMakeLists.txt
│   ├── src/wave_engine.cpp       # ray-mesh tracing + RF channel synthesis
│   └── build/                    # build output (git-ignored): devos_core*.so
├── app/
│   ├── main.py                   # entry point: main()
│   ├── native/                   # bridge to devos_core (+ identical NumPy fallback, build helper)
│   │   ├── __init__.py           # trace_segments(), synthesize_response(), HAS_NATIVE, self_test()
│   │   ├── numpy_engine.py       # pure-NumPy mirror of the C++ functions
│   │   └── build.py              # python -m app.native.build [--if-missing]
│   ├── data/                     # shared data layer (no Qt)
│   │   ├── tensor4d.py           # Tensor4D: 4D NumPy array with named axes + slicing
│   │   ├── em_materials.py       # EM material library: permittivity / conductivity models, Fresnel
│   │   └── store/database.py     # SQLite: migrations per namespace, module settings, layouts, BLOBs
│   ├── modules/                  # MODULE APPS: each = view (UI) + engine (logic / C++) + store (SQLite)
│   │   ├── registry.py           # MODULE_LIBRARY, ModuleSpec, ModuleContext, create_module()
│   │   ├── rf_imaging/           # RF imaging 2.5-6 GHz: sensor.py, imaging.py, store.py, view.py
│   │   ├── wave_sim/             # RF wave simulator 1-10 GHz: physics.py, scene.py, engine.py, store.py, view.py
│   │   ├── tensor_inspector/     # generic 4D tensor viewer: view.py
│   │   └── calendar/             # month calendar: view.py
│   └── ui/
│       ├── theme.py              # all colors / QSS styling (palette, size tokens, *_QSS sections)
│       ├── main_window.py        # MainWindow: sidebar + tab workspace; page_entries registry
│       ├── gl_support.py         # shared OpenGL probe for 3D views
│       ├── canvas/
│       │   ├── module_canvas.py  # ModuleCanvas: tiling grid + module library bar + saved layouts
│       │   └── module_tile.py    # ModuleTile: draggable block (header + module view / picker)
│       ├── forms/
│       │   └── cost_dashboard.ui # Qt Designer form for the Market Lab page
│       ├── pages/
│       │   ├── home_page.py            # HomePage: live stat cards + activity feed
│       │   ├── overview_page.py        # OverviewPage: Trading Dashboard overview (hosts the calendar)
│       │   ├── placeholder_page.py     # PlaceholderPage: stand-in for unbuilt apps
│       │   ├── project_editor_page.py  # ProjectEditorPage: a ModuleCanvas (layout saved in SQLite)
│       │   └── cost_dashboard_page.py  # CostDashboardPage: loads cost_dashboard.ui
│       └── widgets/
│           ├── sidebar.py        # Sidebar (collapsible navigation) + load_icon()
│           ├── tab_area.py       # TabArea: tiling workspace of tab panes (nested splitters)
│           ├── tab_group.py      # TabGroup: one pane of tabs (+ PageTabBar for drag-and-drop)
│           ├── tiling_layout.py  # shared splitter-tree engine (split, swap, tidy, drop zones)
│           └── stat_card.py      # StatCard: reusable dashboard tile
```

**To add a module app:** create `app/modules/<name>/view.py` with a QWidget whose `__init__` takes `context=None` (a `ModuleContext`: `instance_id`, `database`, `settings`). Put logic in `engine.py` (heavy loops can go to `core/` C++ with a NumPy mirror in `app/native/`), tables in `store.py` (`database.migrate("<name>", [...])`, tables prefixed `<name>_`), then add one `ModuleSpec` to `MODULE_LIBRARY` in `app/modules/registry.py`. It then appears in every canvas's "Add module" dropdown and empty-tile picker.

**Data:** everything lives in one SQLite file, `~/.local/share/devos/devos.sqlite3` (override with `DEVOS_DATA_DIR`). Closing a tile removes it from the layout but keeps its data.

**C++ engine:** `python -m app.native.build` (run.sh does this automatically the first time). Without it, DevOS uses the NumPy mirror (same results, ~3x slower); `python -c "from app import native; print(native.self_test())"` compares the two.

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
| `close_sidebar_btn / open_sidebar_btn` | UI elements (`QToolButton`) | `Sidebar` / `TabArea` tab row | Close the sidebar / reopen it (open button only shows while closed). |
| `*_COLOR` | Constants (`str`, hex) | `app/ui/theme.py` [2.1] | Palette. Use these instead of typing hex codes in widgets. |
| `content_panel` | UI element (`QWidget`) | `MainWindow` | Right-hand side of the splitter: top bar + page stack. |
| `cost_dashboard.ui` | Designer form | `app/ui/forms/` | Layout of the Cost Dashboard page, edited in Qt Designer. |
| `CostDashboardPage` | Class (`QWidget`) | `app/ui/pages/cost_dashboard_page.py` | Page shown as "Market Lab" (in the Finance folder); loads `cost_dashboard.ui`. |
| `DESKTOP_FILE_ID` | Constant (`str`) | `app/main.py` | `.desktop` launcher name without extension; must match the install script. |
| `fill_percent` | Parameter (`float`, %) | `StatCard.set_value()` | How full the progress bar should be (clamped to 0–100). |
| `find_form_widget()` | Method | `CostDashboardPage` | Looks up a widget in the Designer form by its objectName. |
| `form_panel` | UI element (`QWidget`) | `CostDashboardPage` | The widget tree built from the `.ui` file. |
| `FORMS_DIRECTORY` | Constant (`Path`) | `app/ui/pages/cost_dashboard_page.py` | Folder holding all Designer `.ui` files: `app/ui/forms/`. |
| `has_progress_bar` | Parameter (`bool`) | `StatCard()` | `False` builds a card with no bar (e.g. Uptime). |
| `HomePage` | Class (`QWidget`) | `app/ui/pages/home_page.py` | Home dashboard: live system stat cards plus the activity feed. |
| `ICON_SIZE` | Constant (`QSize`, px) | `app/ui/widgets/sidebar.py` | Size of every sidebar / tab icon (16 × 16). |
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
| `page_stack` | UI element (`QStackedWidget`) | `TabGroup` | Shows the pane's selected page, or the empty-pane hint. |
| `page_title_label / page_subtitle_label` | UI elements (`QLabel`) | Every page | Heading and grey sub-line at the top of a page; styled in `theme.py` [3.6]. |
| `OverviewPage` | Class (`QWidget`) | `app/ui/pages/overview_page.py` | Trading Dashboard › Overview page; lays out dashboard modules in `module_grid_layout`. |
| `PlaceholderPage` | Class (`QWidget`) | `app/ui/pages/placeholder_page.py` | Stand-in page showing a title and subtitle until the real page is built. |
| `*_PX (theme)` | Constants (`int`, px) | `app/ui/theme.py` [2.2] | Shared size tokens: `BASE_FONT_SIZE_PX`, `CORNER_RADIUS_PX`. |
| `*_QSS` | Constants (`str`) | `app/ui/theme.py` [3.x] | One group of style rules per widget kind. |
| `refresh_stats()` | Method | `HomePage` | Re-reads CPU, memory, disk and uptime and updates the cards. |
| `select_page()` | Method | `Sidebar` | Highlights an item from code without emitting `page_selected`; opens its folder. |
| `clear_selection()` | Method | `Sidebar` | Removes every highlight (used when no tab is open). |
| `Sidebar` | Class (`QWidget`) | `app/ui/widgets/sidebar.py` | The collapsible navigation sidebar (objectName `nav_sidebar_panel`). |
| `sidebar_header_panel` | UI element (`QWidget`) | `Sidebar` | Row at the top of the sidebar: title + close button. |
| `sidebar_item_btn` | UI element (`QPushButton`) | `Sidebar` | One clickable app in the sidebar (shared objectName). |
| `sidebar_section_label` | UI element (`QLabel`) | `Sidebar` | Grey section heading above a group of apps ("Home", "Workbenches"). |
| `StatCard` | Class (`QFrame`) | `app/ui/widgets/stat_card.py` | Dashboard tile: title, big value, optional progress bar (objectName `stat_card`). |
| `STYLESHEET` | Constant (`str`) | `app/ui/theme.py` [4.1] | All `*_QSS` sections joined; applied once in `app/main.py`. |
| `TabArea` | Class (`QWidget`) | `app/ui/widgets/tab_area.py` | Tiling workspace: panes (`TabGroup`) arranged in any mix of rows and columns; seams resize them. Dropping a tab on a pane's edge splits that pane on that side. A sidebar click opens the page in the active pane, or switches to it wherever it is open. Ctrl+W / Ctrl+Tab / Ctrl+Shift+Tab act on the active pane. |
| `TabGroup` | Class (`QWidget`) | `app/ui/widgets/tab_group.py` | One pane: tab row + page area. × / middle-click close; drag inside the row to reorder, drag out to move the tab to another pane. Split (new pane to the right) and close-pane buttons on the right. |
| module | Term | `app/modules/` | Self-contained panel (title + content) that a page hosts; shares the `#module_panel` frame style. Future building block for the snap-grid canvas. |
| `CalendarModule` | Class (`QFrame`) | `app/modules/calendar/view.py` | Month calendar module: ‹ / Today / › navigation, click to select a day; signals `date_selected(QDate)`, `month_changed(int, int)`. |
| `Tensor4D` | Class | `app/data/tensor4d.py` | 4D NumPy array with named axes (`t, x, y, z`): `take`, `volume`, `plane`, `line`, `value`, `window`, `reduce`, `waterfall`, `to_points`, `push` (streaming), `demo()`, `inspect()`. |
| `TensorInspectorModule` | Class (`QWidget`) | `app/modules/tensor_inspector/view.py` | Live inspector for a `Tensor4D`: pyqtgraph waterfall (time × one axis) beside an OpenGL 3D scatter of one time step; `launch_inspector()` opens it in its own window. |
| `RfInspectorModule` | Class (`QWidget`) | `app/modules/rf_imaging/view.py` | RF imaging inspector: 3D reflectivity / motion / materials, range-time & spectrum waterfalls, slice picker, material spectra; record, save, load, calibrate. |
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
| `WaveSimModule` | Class (`QWidget`) | `app/modules/wave_sim/view.py` | RF Wave Simulator: 3D twin with rays, time x frequency waterfall, dielectric spectroscopy, CAD import, snapshots. |
| `WaveSimEngine` | Class | `app/modules/wave_sim/engine.py` | Direct + first-order reflection paths (image method), dielectric ray tracing, H[node, f], WaveTensor. |
| `WaveTensor` | Class | `app/modules/wave_sim/physics.py` | float32 ring buffer (nodes, time, frequency, [amp, phase]); `as_tensor4d()`. |
| `ModuleCanvas` | Class (`QWidget`) | `app/ui/canvas/module_canvas.py` | Page-filling grid of `ModuleTile`s: drop on an edge to tile beside, centre to swap; seams resize. |
| `ModuleTile` | Class (`QFrame`) | `app/ui/canvas/module_tile.py` | One block on a canvas: draggable header (title, split, close) over the module content. |
| `MODULE_MIME_TYPE` | Constant (`str`) | `app/ui/canvas/module_tile.py` | Drag-and-drop format of a dragged tile; carries only its `tile_id`. |
| `ProjectEditorPage` | Class (`QWidget`) | `app/ui/pages/project_editor_page.py` | Project Manager › Project Editor; a `ModuleCanvas` starting with one empty module. |
| `tiling_layout` | Module | `app/ui/widgets/tiling_layout.py` | Shared tiling engine used by `TabArea` and `ModuleCanvas`: `insert_beside`, `swap_leaves`, `normalize_tree`, `edge_zone_at`. |
| `PageTabBar` | Class (`QTabBar`) | `app/ui/widgets/tab_group.py` | Tab row that turns a tab into a drag-and-drop item once the mouse leaves the row. |
| active pane | Concept | `TabArea` | The pane last clicked, dropped on or opened in; marked by an accent line on its selected tab when 2+ panes exist. |
| `PAGE_MIME_TYPE` | Constant (`str`) | `app/ui/widgets/tab_group.py` | Drag-and-drop format of a dragged tab; carries only the `page_index`. |
| `open_page()` | Method | `TabArea` | Opens a page by `page_index` in the active pane, or selects its existing tab. |
| `split_pane()` | Method | `TabArea` | Adds an empty pane on one side (`left`/`right`/`top`/`bottom`, default right) of a pane, giving it half that pane's space, and makes it active. |
| drop zone | Concept | `TabGroup` | Where a dragged tab lands: tab row (insert there), centre (add as tab), or the outer quarter on each side (split that way). The blue overlay previews it. |
| layout tree | Concept | `tiling_layout.py` (`TabArea`, `ModuleCanvas`) | `root_splitter` and nested `QSplitter`s are branches, panes or module tiles are leaves; tidied after every close so no splitter is empty, single-child or same-direction-nested. |
| `current_page_changed` | Signal (`int`) | `TabArea` | `page_index` shown in the active pane, or `-1` when it has no tab. |
| `page_tab_bar` | UI element (`PageTabBar`) | `TabGroup` | A pane's row of tabs. |
| `root_splitter` | UI element (`QSplitter`) | `TabArea` | Top of the layout tree (objectName `pane_splitter`, shared by every splitter in the tree). |
| `empty_tab_label` | UI element (`QLabel`) | `TabGroup` | Hint in an empty pane: "Pick a page from the sidebar" (one pane) or "Drag a page here" (split). |

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
