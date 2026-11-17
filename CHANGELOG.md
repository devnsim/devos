# Changelog

All notable changes to DevOS. Versions follow [Semantic Versioning](https://semver.org).
Newest first. Each version has a matching git tag (`v0.1.0` = version 0.1.0).

## [Unreleased]
### Added
- Engine plan Phase 5b (views over Data):
  - View types `grid` (paged by the engine, editable, copy / paste, + Column), `chart` (line / bar / scatter,
    dates and categories, totals grouped in the engine) and `network` (a graph table's graph);
    `app/views/graph_canvas.py` shared with the Links view.
  - Data engine views (`engines/data/views.py`, engine.json `views`): Sheet, SQL console, Cypher console,
    Structure; requests `structure`, `cells`, `set_cell`, `sql`, `cypher`; `graph` takes a collection.
  - `Engine.watch()`: events of engines in `uses`, handled on the engine's thread; Data watches
    `knowledge.changed` (op "external" when another tool changed a file).
  - `ViewType.accepts`, `MappingSlot.auto`, `host.add_property(formula=…)`; `tests/test_data_views.py`.
- Engine plan Phase 5a (Data engine, `engines/data/`):
  - One source contract for record collections, user SQLite databases (`db:<item>/<table>`), CSV datasets
    (`csv:<item>`), workbook sheets (`sheet:<item>/<sheet>`) and Kùzu node tables (`graph:<item>/<table>`);
    `collections` lists every table in the Library; `graph` returns a database as a Graph shape.
  - Query v1 runs where the data lives: SQLite (`engines/toolkit/sql.py`, also for records through
    `json_extract` + expression indexes), Cypher for Kùzu, in memory for CSV / sheets and formula questions.
  - Formula properties (`=[Price] * [Quantity]`; `[Field]` references in the formula language); sheet columns of
    formulas are computed fields.
  - Writes checked by field kind; `changed` events with `op` and per-row diffs; update replies carry `changes`.
  - `Engine.ask()` and `EngineManifest.uses` (the kit checks engine.json lists them).
  - `tests/test_data_engine.py` (SQL pushdown compared with the memory toolkit on 18 questions, every source).

### Changed
- Knowledge's `refresh_size` announces the change (`by` names who made it); the Graph Database module reports
  its writes. Optional mapping slots never reuse a field another slot took. A table view whose title column is
  also a field shows it once.
- Record collections moved from the records engine to the Data engine (same tables); saved views are upgraded
  (`ENGINE_RENAMES`); RecordsModule, the Data view module and tests use `data.*`.
- `formulas.py`, the spreadsheet workbook logic and `GraphDb` moved to `engines/toolkit/` (shared by the Data
  engine and the modules).
- Grouped questions may sort by their aggregates, which sort as numbers (memory toolkit too).
- New rows are selected by the reply's `id` (file tables key rows by `_rowid`).

### Removed
- The Phase 2 records engine (`engines/records/`).

## [0.2.0] - 2026-11-17
Engine plan Phases 0–4: safety net, kernel, view runtime, docking, Knowledge engine.

### Added
- Engine plan Phase 4 (Knowledge engine):
  - Knowledge 0.2 owns the Library (`engines/knowledge/`, a core engine): file service, source contract
    (`library` + one table per kind), `links` (Graph shape), Document shape with a note's links, `changed` events.
  - `app/clients/library.py` (`library()`, `LibraryError`) and `watch_library()`; `app/data/content_kinds.py`.
  - `SourceModule` (`app/modules/source_module.py`): modules made of views over one engine source.
  - View types `document` (Markdown editor + preview, autosave, `[[links]]`) and `links` (note graph);
    `ViewType.requests`, `host.request()`, read-only fields.
  - Settings → Engines marks core engines and keeps Detach off for them.
  - `check_standard.py`: `app/` never imports an engine package's internals.
  - `tests/test_knowledge.py`.
- Engine plan Phase 3 (docking and standards):
  - Engine packages (engine.json) discovered in `engines/` and `<data>/engine-packages/`.
  - Attach / detach (remembered, requires checked), capability queries.
  - Conformance kit (`python -m engines.kernel.conformance`).
  - Request timeouts, busy engines, stream back-pressure (credit), `stream_engine(window, latest)`.
  - Engine-bundled view types; placeholders that come back on attach.
  - Settings → Engines; Bus inspector (window and tool).
  - Example package `examples/engines/sample_data`; `tests/test_docking.py`.
- Engine plan Phase 2:
  - Data shapes v1 (`engines/kernel/shapes.py`, Arrow attachments, `docs/shapes.md`).
  - Query v1 (`engines/kernel/query.py`) and shape toolkits (`engines/toolkit/`).
  - Records engine (`engines/records/`).
  - View runtime (`app/views/`: view type API, ViewHost, page-wide channels with unlink, shared subscriptions, filter bar, view builder; `docs/views-api.md`).
  - "New view…" in record modules; Data view module; `tests/test_views.py`.
- `ROADMAP.md`: current state, Now / Next / Later, Done, direction, future ideas, open questions; updated every
  session (AGENTS.md routine, `check_standard.py` warning). The phases moved there from the engine plan, which
  keeps the design.
- Engine plan revised: seven engines (Spatial added: US census / parcel data, cartography first, then QGIS
  processing), ten data shapes with Arrow attachments, communication principles, new phases Docking and standards
  (3), Spatial (6), Displays (7), Signals and Streams (9); phases renumbered.
- Engine plan Phase 1: the kernel (`engines/kernel/`: messages, Engine, InProcessHost, Bus), `engines/runtime.py`
  (shared bus, engine logs into the System Log, shutdown at exit), `app/ui/engine_bridge.py` (call / stream / watch
  from widgets, answers on the UI thread), Knowledge engine stub; `tests/test_engine_kernel.py`.
- Engine plan Phase 0: `docs/architecture/engine-plan.md` and `data-ownership.md`.
- Backups (`app/data/store/backup.py`): consistent SQLite copies, unchanged files hard-linked, SHA-256 manifest,
  check, prune, restore at the next start (current state backed up first), command line
  (`python -m app.data.store.backup`). Settings → Data page and a "Back up data" tool.
- `Database.migrate()` backs up before upgrading tables that hold data and refuses to migrate without a backup.
- Migration fixture `tests/fixtures/data_v0` (+ expectations, make / check scripts); `tests/test_data_safety.py`.
- `check_standard.py` layer boundaries: `engines/` and `app/data/` never import Qt, views or modules.
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
- The bus was rewritten for Phase 3 (one pending record per request: deadlines, stats, taps); `Bus.call()` uses
  the request's own timeout unless given one. The mock market-feed test compares bars by open time (it failed when
  a minute boundary fell between two calls).
- Projects and Agents run on the view runtime over the records engine (old block settings are upgraded); the
  Agents chat follows `RecordFocus`. `app/ui/record_views.py` (RecordsController and the record blocks) removed.
- The Library module lists and previews items through the Knowledge engine.
- Phase 4: Notes and Library are SourceModules made of views over Knowledge (Notes keeps its `note_id` and `mode`
  settings); `RecordsModule` is a thin SourceModule; `BlockCanvas(spec_factory=…)` restores views of kinds a
  module didn't list. Every former content-store caller uses `library()`. `knowledge.get {item_id}` is now
  `knowledge.item` (`get` is the source contract's). Library import reports every file it couldn't import (the
  last failure used to be overwritten by the summary).
- Wave Simulator and RF Imaging: a local `library` variable no longer shadows `library()`.
- The Settings page is real (`SettingsPage`); `PlaceholderPage` removed.
- `Tensor4D.inspect()` removed (it pulled Qt into the data layer); call `launch_inspector(tensor)` instead.
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
- `app/data/store/content.py` / `get_content_store()` (moved into the Knowledge engine as `engines/knowledge/store.py`).
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
