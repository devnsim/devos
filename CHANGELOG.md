# Changelog

All notable changes to DevOS. Versions follow [Semantic Versioning](https://semver.org).
Newest first. Each version has a matching git tag (`v0.1.0` = version 0.1.0).

## [Unreleased]
### Added
- Toolbars in every module tile (grip menu → Add toolbar): dock top / bottom / left / right or float, snap by
  dragging, sizes, pin, drag tools to reorder; the tool bag (global tools + module tools) and Ctrl+K to run a tool.
- Record model (`records.py`) with reusable views (table, board, timeline / Gantt, calendar, form, + Property);
  Agents and Projects modules built on it; agents export as agent definition files.
- Modules: Spreadsheet (formulas, sheets, .xlsx / .csv, LibreOffice for .ods / .xls / .pdf), Graph Database (Kùzu:
  schema, Cypher, graph view, inspector), Map (Leaflet: places, base layers, GeoJSON), CAD with the Part Design
  workbench (sketch → pad / pocket / hole → check → export), Comms (email over IMAP / SMTP; text and calls are
  placeholders), Software Monitor (processes, insights, DevOS footprint), Web App (a site in a tile with its own
  container; n8n preset).
- Database Editor module: user SQLite databases in the Library; explorer, data grid, record form, structure,
  SQL console and properties blocks; create table / add column / add index forms; read-only system database.
- Browser pages (Qt WebEngine): New Browser on empty pages, address / search bar, find, zoom, persistent
  profile, downloads into the Library, new-window links open new browser pages.
- Tab row "+" (and Ctrl+T) to create a page; empty pages show Create New (New Module / New Browser).
- Tiling tab workspace (split panes by dragging tabs to edges) and an integrated, frameless title bar.
- User pages: pages and folders in SQLite, favourites, create / rename / delete / drag in the sidebar,
  *Create New Page* in empty panes; every page is a `CanvasPage` (the page template).
- Module library and module apps on page canvases: RF Imaging, RF Wave Simulator, Trading Chart, Tensor Inspector, Calendar.
- Modules as canvases of blocks (split / rearrange / resize / close / replace), with fading corner controls.
- SQLite data layer (per-module tables, settings, layouts) and a C++20 engine (`devos_core`, pybind11) with a NumPy mirror.
- IDE-style status bar with app / system resource monitors (incl. UI lag).
- `AGENTS.md`, `tools/check_standard.py`, pytest suite in `tests/`.
- Tools: Graph (datasets, CSV import, mock data, settings window), Notes (Markdown), Library (content browser);
  Financial Chart settings window (data source, speed, chart type, theme, indicators, strategy).
- Content library (`content.py`): user files live in the data folder, indexed in SQLite; user preferences table.
- System Log (`activity_log.py`, `log_panel.py`): global announcements and failure details, Log tile in the status bar,
  docked resizable panel, filters, past sessions; saved to SQLite every 15 s and at exit; Python / thread / Qt hooks.

### Changed
- Status bar reads only the CPU temperature sensor (reading every hardware sensor each second stalled the UI 60-300 ms).
- Qt WebEngine is shut down in order on exit (quitting and the test run no longer end in a segfault).
- Pages show their canvas as soon as it holds a module, however it was added.
- Block helpers moved from the Database Editor into `ui_tools.py`; new content kinds graph, sheet, geo, image.
- `shell.nix`: kuzu, shapely, mapbox-earcut, manifold3d, networkx, openpyxl, keyring.
- One shared tiling canvas (`tile_canvas.py`) for module tiles and blocks; shared `ModuleView`, `ui_tools`, `plot_tools`.
- RF captures and imported CAD models go to the content library (no save dialogs); `*.npz` is git-ignored.
- New pages start with one empty tile and its module dropdown; module tiles have no title bar (corner controls only).
- A module that fails to start shows an error panel and a logged error instead of breaking the page.
- New pages no longer open with a module tile; closing the last module returns the page to Create New.
- Mouse presses are watched per window (`window_events.py`) instead of with app-wide event filters.
- `check_standard.py`: changed files need a NOTES entry dated on/after the last commit (not exactly today).

### Removed
- Home page (system monitors), Trading Dashboard pages (Overview, Market Lab, Co-Pilot, Account Manager,
  Simulation Sandbox, System Monitor), fixed Project Editor page (migrated into a user page), `StatCard`,
  unused theme sections and dead helpers, module library bar, `rf_imaging/store.py`.

## [0.1.0] - 2026-11-14
### Added
- PySide6 main window with collapsible, resizable sidebar and page stack.
- Home dashboard with live CPU / memory / disk / uptime cards and activity feed.
- Cost Tracking page loaded from a Qt Designer form (`cost_dashboard.ui`).
- Flat dark "pro tool" theme in `theme.py`.
- Desktop launcher (`run.sh`, `install-desktop.sh`).
- Code standard (header, definitions, index, sections) and naming standard across `app/`.
- README Lexicon & naming dictionary.

### Fixed
- `//*` typo in `theme.py` that silently dropped the sidebar styles.
