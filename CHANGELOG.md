# Changelog

All notable changes to DevOS. Versions follow [Semantic Versioning](https://semver.org).
Newest first. Each version has a matching git tag (`v0.1.0` = version 0.1.0).

## [Unreleased]
### Added
- Agents chat: + New agent → Claude / Gemini / Ollama agent; a Chat view that streams answers from the selected
  agent (its Instructions are the system prompt), chats kept per agent (rename, save to Library, delete), provider
  and model switchable in the view; ⚙ settings for API keys (system keyring) and the Ollama address; Provider and
  Max output tokens properties.
- Record views: double-click a record on the board, timeline (name or bar) or calendar to open its form view;
  right-click a record in any view for Open form, module actions (Agents: Chat with agent), Duplicate, Delete.
- `app/data/store/secrets.py` (keyring secrets shared by Comms and Agents); `run_in_thread(on_progress=…)`;
  `BlockCanvas.show_block(kind)`; `StepProgressBar`.
- Canvas pages: New Canvas on an empty page; an endless grid of module cards that snap while moving / resizing,
  pan / zoom / fit, double-click or right-click to add, saved with the page (`InfiniteCanvasPage`).
- Several windows: "Open in new window" in the tab menu, or drag a tab out of every window; tabs drag between
  windows; renames / deletions reach every window (`Workspace`).
- Web apps: browser bar hidden by default; options menu (floating ⋯, the bar's ⋯, right-click → Web app) with
  the bar toggle, navigation, find / zoom and Change site; a "Show / hide browser bar" tool.
- Physics: `ControllerSnapshot` in every worldline row: seek / branch restore commands, PID integrators and the
  running program's cursor bit for bit; `devos_physics_cli --pid actuator=kp,ki,kd`.
- Physics engine groundwork (`core/physics/`, standalone C++20 project): `libdevos_physics.so` with PhysicsWorld
  (facade, master clock, Z-up frame, step / reset / seek / branch), SceneLoader (MJCF bundles: machine → MuJoCo,
  room → Jolt, convex meshes), CoupledStepper (1 kHz lock-step, kinematic mirroring, contact forces into
  `xfrc_applied`), WorldlineBuffer (flat ring of per-tick rows, zero-copy, exact seek via Jolt keyframes) and
  ControllerPort (setpoints, PID, S-curves, G-code, springs, sensor frames); `devos_physics_cli`, CTest suite,
  pybind11 module, demo scene. Eigen3 and MuJoCo added to `shell.nix`; Jolt fetched at a pinned tag.
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
- Part Design: the 1-4 step list on the left is now a progress bar across the top (name, steps, Back / Next in
  one row; the work area uses the full width). Clicking a locked step says why it can't start.
- `shell.nix` provides n8n (the only unfree package allowed; `--arg withN8n false` skips it); the n8n web app
  preset points there instead of `npm install -g`.
- Tests use keyring's null backend and drop provider API keys from the environment.
- Pages whose tab is closed (or whose window closes) are suspended: their web views are discarded, so sound and
  scripts stop; they reload when opened again. (Closing every tab used to leave web pages playing.)
- Jolt is fetched as the v5.7.0 release archive verified by SHA256 (`URL_HASH`) instead of a git tag.
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
