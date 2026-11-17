# DevOS Roadmap

Last updated: **2026-11-17** (Phase 5 done, v0.3.0: Data engine, views, templates; Phase 6 Spatial is next — see Now)

The future-facing companion to [CHANGELOG.md](CHANGELOG.md): the changelog records what changed, and this file records where DevOS stands and what comes next. The design behind it (engines, data shapes, protocol, contracts) is in [docs/architecture/engine-plan.md](docs/architecture/engine-plan.md); what is stored where is in [docs/architecture/data-ownership.md](docs/architecture/data-ownership.md).

## How this file is kept

- **Updated at the end of every session, in the same commit as the work.** "Last updated" changes, and `tools/check_standard.py` warns when files changed but this one didn't.
- **When work is finished:** its phase is marked `done <date>` with a short "what was built" list, its details go into `CHANGELOG.md` → [Unreleased], and Now / Next move on. Finishing a phase tags a minor version (the changelog's [Unreleased] becomes that version).
- **New decisions** go into the plan's Decisions table (dated). **Ideas** the user floats go to *Future ideas*. **Questions** go to *Open questions*, tagged with the phase that needs the answer.
- Phase numbers never change once work has started on a phase; insert new phases only after the current one, and update references (`grep -rn "Phase [0-9]"`).

## Current state

- **Version:** v0.3.0 (Phases 0–5 of the engine plan; see CHANGELOG).
- **What DevOS is today:**
  - **Shell:** pages and folders, tabs, several windows, Canvas pages, Settings.
  - **Tiles:** 7 built-in templates (Projects, Agents, Events, Database, Spreadsheet, Graph database, Dataset chart) and 12 modules on the module / block canvases; your own templates (Save as template…).
  - **Physics:** the standalone C++ engine (`core/physics/`).
  - **Phase 0–5 work:**
    - backups and the migration fixture;
    - the engine kernel (bus, in-process host, UI bridge);
    - data shapes and Query v1;
    - the Knowledge engine, sole owner of the Library (core engine; `library()` client; Notes and Library are views over it, with Document and Links views);
    - the Data engine (5a): records, user databases, CSV datasets, sheets and graphs behind one source contract;
    - views over it (5b): Grid, Chart, Network, and Data's Sheet, SQL / Cypher consoles and Structure;
    - templates (5c) in place of the Data-group modules, and Save as template;
    - the view runtime (ViewHost, page-wide channels, shared subscriptions, view builder, Data view tiles);
    - docking: engine packages, attach / detach with placeholders, conformance kit, Settings → Engines, bus inspector.
- **Engine plan:** Phases 0–5 done. Projects, Agents, Events, Database, Spreadsheet, Graph database and Dataset chart are templates over the Data engine; Notes and Library are views over Knowledge. The other modules still run as modules; they move into engines phase by phase.

## Now

### Phase 6 — Spatial engine (XL) · first after Data
Built on GDAL / OGR (formats), PROJ (coordinate systems), GEOS / shapely (geometry), GeoPandas and pyogrio (tables with geometry), rasterio (rasters) and PDAL (lidar). Layers are stored as **GeoPackage** in the Library (one SQLite file that QGIS opens natively). `shell.nix` pins these and QGIS (with a `withQgis` switch, like n8n). QGIS is used as an outside program (`qgis`, `qgis_process`) and through its file formats, which keeps its GPL licence separate from DevOS. Importing PyQGIS into DevOS would only happen out of process (13a), if ever.

- **6a Map visualisation and cartography (first)**
  - Import vector layers (Shapefile, GeoPackage, GeoJSON, KML, CSV with latitude / longitude) into GeoPackages; keep each layer's CRS and display in Web Mercator.
  - **US data connectors:**
    - Census Bureau cartographic boundary files: states, counties, tracts, block groups, ZCTAs.
    - American Community Survey 5-year estimates via `api.census.gov`, joined to boundaries on GEOID. The free API key goes in the keyring.
    - County parcel / assessor open data as an import path.
    - Housing price indexes: FHFA HPI, Zillow research CSVs, with each source's terms checked.
  - **Styling model** (saved as data, versioned):
    - single symbol, categorised, and graduated / choropleth;
    - classification by equal interval, quantile or natural breaks (Jenks);
    - colour ramps, including colour-blind-safe ramps;
    - labels, opacity, layer order and scale-dependent visibility.
  - **Map view** (generic, Geo shape):
    - MapLibre GL in a web view, with vector tiles that the engine produces for large layers;
    - basemaps (OpenStreetMap-based, within their usage terms);
    - legend;
    - click / hover identify that sets the page's selection, so a tract selects its row in a linked table and a chart.
  - **Cartographic output:**
    - map layouts (title, legend, scale bar, north arrow, source credits) exported to PNG / PDF / SVG at print resolution;
    - **Open in QGIS:** writes the GeoPackage plus a QGIS style (QML), so the styling carries over.
  - Templates: Map, Census explorer, Places (the old Map module; its places are copied into a GeoPackage layer). Removes the `map` module.
  - **Done when** you can load a county's tracts with ACS median household income, style a natural-breaks choropleth with a legend, select a tract to see its row, export a print-quality PDF, and open the same map, styled, in QGIS.
- **6b Processing (later)**
  - QGIS processing algorithms through `qgis_process`, as Spatial requests with progress streams, including the algorithm catalogue.
  - Spatial joins, buffers, dissolve and zonal statistics.
  - Geocoding (US Census Geocoder).
  - Rasters: elevation, imagery.
  - Lidar (LAS / LAZ through PDAL) as georeferenced point clouds.

**Start here (6a).** First the Spatial engine package (`engines/spatial/`, uses Knowledge for GeoPackages in the Library) with GDAL / PROJ / GEOS / pyogrio pinned in `shell.nix`, the Geo shape's toolkit (reproject, bbox, simplify), and vector import into GeoPackage layers answering the source contract (a layer is a Table with a geometry field, so Grid / Chart / Structure work at once). Then the MapLibre map view (answer the open question first) and the Map / Places templates (the Map module's places copied into a layer).


## Next

### Phase 7 — Displays (L) · any time after Phase 3
- **Pop-out views:** any view leaves its page as its own window on another monitor and stays linked to its page.
- **Display profiles:** named arrangements of windows across monitors ("Trading – 6 screens"). They are restored by monitor identity and adapt when a monitor is missing.
- **Bare display surfaces:** a chromeless, full-screen window on one monitor showing one view or a wall of views.
- **Wayland (KDE):** apps can't place windows at exact positions. Profiles rely on full-screen surfaces per monitor, with optional KWin rules for windowed placement.
- Per-view redraw rates and shared subscriptions (from Phase 2) keep ten monitors from multiplying the load. A GPU-side renderer for very large point clouds comes when it's needed.
- Later: the same view definitions rendered in a browser on another machine or a tablet.
- **Done when** a three-monitor profile restores (a map on one, a table and chart on another, a bare wall on the third), selection stays linked across them, and unplugging a monitor degrades gracefully.

## Later

**Order:** phases 0–5 go in order. After that, 6–11 can go in any order: Spatial is first by priority, and Displays can start any time after Phase 3. Moving physics into its own process (13a) can start any time after Phase 3. Sizes are relative: S is a session or two, M a handful, L a dozen or so, XL is split into sub-phases. Each phase ends with a minor version tag.

### Phase 8 — Simulation engine, with Geometry (XL)
- Wraps `core/physics`: load, step, seek, branch and controls; worldline as Series / Tensor streams; contact events.
- Wave Simulator, RF Imaging and Tensor4D move in.
- Geometry: parts, sketches, features, build, measure, export, and a "part → scene" request.
- Views: waterfall, 3D scatter, slice, heatmap; 3D scene with a timeline scrubber; sketch canvas; Part Design window; RF controls (the specialist views ship with the engine).
- Templates: Physics scene, RF lab, Part Design.
- **Done when** the physics demo runs in DevOS with scrub / seek / branch, and the CAD, Wave Sim, RF Imaging and Tensor Inspector modules are gone.

### Phase 9 — Signals and Streams (L)
- **Series store** (market bars, system history) with retention rules. Indicators work on any series. Strategies and backtests run as jobs.
- **Stream sources:** tiny engines that only produce streams, dockable like engines. Their manifest gives shape, rate, units and schema. Connectors: serial, UDP, MQTT, WebSocket, file replay, market feeds, simulation output.
- **Routing:** source → engines → views as saved pipelines; a patch-bay view edits them. One common clock and time channel. Record and replay. Decimation for display.
- Views: candles with drawings, line / area, indicator panes, gauge / KPI, sparkline, backtest report, patch bay. Templates: Market chart, System dashboard, Sensor console.
- **Done when** a live sensor stream docks, records, replays in sync with another stream, and an indicator works on it as easily as on a price.

### Phase 10 — Conversation engine (L)
- Threads, participants, messages and attachments. Connectors: Claude, Gemini, Ollama, IMAP / SMTP.
- **Agent runtime:**
  - tools are engine requests from the manifests, and permissions follow each request's effect;
  - anything that writes, sends or spends asks first;
  - every tool call is logged.
- Views: chat, inbox, reader, composer, thread list, tool approvals. Templates: Agents, Inbox.
- **Done when** an agent answers "which projects are blocked?" by querying the Data engine, and "which tracts have the highest rent burden?" by asking Spatial.

### Phase 11 — Services engine (M)
- A supervisor for n8n, Ollama, Jupyter and QGIS tools: start, stop, health, ports, output. Machine telemetry is published to Signals. Web app tiles use it for "Start service".
- Views: process table, service cards, insights. Templates: Services, System monitor.

### Phase 12 — User-written views (S)
- By now the plugin mechanism exists: built-in and engine-bundled views use it. This phase opens it to users:
  - view packages in `<data>/views/<name>/` (a manifest + a Python module);
  - a permission prompt for actions;
  - a kit with a guide, an example, a fake-engine harness and reload on save.
- Python views run with the user's rights, so they load only from the user's own data folder.

### Phase 13 — Engines in other processes and on k3s (XL)
- **13a** Subprocess host: the same messages over a local socket, with Arrow data in shared memory. Physics first; also the way engines written in other languages dock.
- **13b** NATS transport (its subjects are our topics; it offers request / reply, publish / subscribe and persistent streams). Per-engine placement: local, remote, or auto with local fallback.
- **13c** Nix-built images, k3s manifests, GPU / AVX2 node labels. Workers keep no data, and secrets stay local.
- **Done when** a physics run on a cluster node passes the same bit-exact `--verify` as a local run.

## Done

### Phase 5 — Data engine and templates (XL) · done 2026-11-17
- **5a Engine.** One interface for record collections, user SQLite databases, workbooks, Kùzu graphs and CSV datasets. Query v1 runs per source (SQL, Cypher, in memory). Spreadsheet formulas become computed fields. Writes are validated, and changes are announced with diffs.
- **5b Views.** Generic: editable grid, chart, network. Specialist: sheet, SQL / Cypher consoles, structure.
- **5c Templates and migration.** Projects, Agents (definitions), Events, Database, Spreadsheet, Graph DB. "Save as template". The picker shows Templates and "New view of an engine". Old module tiles become template instances, and records are copied into the Data store.
- **Done when** the old Data-group modules, `RecordsModule` and the Phase 2 adapter are deleted, and the fixture's pages open with the same data and layouts.

**5a done (2026-11-17).** What was built:
- **Data engine** (`engines/data/`, replaces the Phase 2 records engine, same tables): one source contract for record collections, user SQLite databases (`db:<item>/<table>`), CSV datasets (`csv:<item>`), workbook sheets (`sheet:<item>/<sheet>`) and Kùzu node tables (`graph:<item>/<table>`); `collections` lists every table in the Library; `graph` gives a database as a Graph shape.
- **Query v1 per source:** SQL (`engines/toolkit/sql.py`; records through `json_extract` with expression indexes - the decision: JSON values with indexes), Cypher for Kùzu, in memory for CSV / sheets and questions about formulas. A test compares the SQL answers with the memory toolkit on 18 questions.
- **Computed fields:** formula properties on records (`=[Price] * [Quantity]`); sheet columns of formulas are computed and read-only.
- **Checked writes, announced with diffs:** values checked by kind; formula cells, node keys and views refused with the reason; `.xlsx` edited in place (formatting kept); `changed` events carry `op` and `{id: {field: [old, new]}}`.
- **Kernel:** `Engine.ask()` + `EngineManifest.uses` (Data asks Knowledge for files; the kit checks engine.json). Grouped questions sort by their aggregates.
- Shared logic moved to `engines/toolkit/` (formulas, workbook, kuzu_db); saved views of the records engine upgrade on load (`ENGINE_RENAMES`).
- Tests: `tests/test_data_engine.py`; the kit passes 9 of 9; the fixture opens unchanged.

**5b done (2026-11-17).** What was built:
- **Engine.watch** (the 5b open question, answered: engines watch the engines they declare in `uses`; events are handled on the engine's own thread, in order with requests). Knowledge's `refresh_size` announces in-place changes with `by`; Data watches `knowledge.changed` and refreshes views of files other tools changed (op "external"), skipping its own writes. The Graph Database module reports its writes.
- **Generic views:** `grid` (paged by the engine, 200 rows; edit in place; copy / paste blocks; + Column with formulas; computed columns read-only), `chart` (line / bar / scatter, dates and categories on X, Totals by X grouped in the engine), `network` (a graph table's graph on the shared `GraphCanvas`, also used by Links).
- **Data's own views** (`engines/data/views.py`): Sheet (cells, formula bar), SQL console, Cypher console (history, results, what changed), Structure (columns, keys, indexes, foreign keys, relationships; + Column / Remove). Requests `structure`, `cells`, `set_cell`, `sql`, `cypher`.
- **View API:** `ViewType.accepts(source)` (the builder offers only views the data fits), `MappingSlot.auto`, `host.add_property(formula=…)`.
- Tests: `tests/test_data_views.py` (8), watch tests in `tests/test_data_engine.py`; 125 pass; Data kit 9 of 9.

**5c done (2026-11-17).** What was built:
- **Templates** (`app/templates/`): `TemplateSpec` (source kind, schema, views and their settings, layout, actions, extension); built-ins Projects, Agents, Events, Database, Spreadsheet, Graph database, Dataset chart under the ids of the modules they replace; `TemplateModule` (file and table pickers; New… / Import… / Export… / + Table / + Sheet / Sample graph / Sample data…); the Agents chat as `AgentsExtension`.
- **Migration:** old tiles open as templates with the same file and table (`database_id` + `table`, `item_id`, the Graph's `dataset_id` and columns), their block layouts translated (`legacy_blocks`) and SQL / Cypher history kept. Collection schemas moved onto `record_collections` (`noun`, `defaults_json`), copied from `records_adapter_defaults` (kept one version, no longer written).
- **Save as template…** (decision: in the workspace database, `templates_user`); the picker lists templates, then modules, then "New view of an engine…".
- Removed: the Projects, Agents, Calendar, Database Editor, Spreadsheet, Graph Database and Graph modules, `RecordsModule`. Gone with them: the Database Editor's explorer and properties panels, the Spreadsheet's selection statistics, the Graph tool's appearance settings.
- Tests: `tests/test_templates.py` (8); 130 pass; the fixture opens with the same pages, tiles, records, chats, files and database rows.
- **Done when** the old Data-group modules, `RecordsModule` and the Phase 2 adapter are deleted, and the fixture's pages open with the same data and layouts. ✔

### Phase 4 — Knowledge engine (M) · done 2026-11-17
What was built:
- **Knowledge 0.2** (`engines/knowledge/`, a **core** engine: engine.json `"core": true`, `runtime.detach` refuses it, Settings → Engines shows "· core" and keeps Detach off):
  - the content store moved in (`engines/knowledge/store.py`); kinds in `app/data/content_kinds.py`;
  - file service: list, item, read_text, search (names and note text), create_text, add_file, add_bytes, write_text, rename, export, path / new_path / register_path, unique_name, refresh_size;
  - source contract: `library` plus one table per kind; create = new note, update = rename, duplicate, delete; read-only fields;
  - `links` (Graph shape; links to missing notes too) and a note's links in the Document shape;
  - `changed` events {kind, ids, content}, replacing the content-store listeners.
- **Front end:**
  - `app/clients/library.py`: `library()` with the old store's calls; `watch_library()`. Every former `get_content_store()` caller moved.
  - `SourceModule` + `RecordFocus` (`app/modules/source_module.py`); `RecordsModule` is a thin subclass; `BlockCanvas(spec_factory=…)`.
  - View types `document` (Markdown editor / preview, autosave, `[[links]]`) and `links` (note graph); `ViewType.requests`, `host.request()`.
  - Notes and Library rebuilt as SourceModules over Knowledge (Notes keeps "note_id" and "mode"). Library import now reports every file it couldn't import.
- `check_standard.py`: `app/` never imports an engine package except kernel / runtime / toolkit.
- Tests: `tests/test_knowledge.py` (file service, source contract, links, Document / Links views, Notes / Library modules, core engine + kit, the check rule); older tests moved to `library()` / `knowledge.item`; the fixture check reads the Library through `library()`.
- Decisions: core engines; the Library through a client; `app/` never imports engine internals; view types declare the requests they need.
- **Done when** nothing outside Knowledge touches `library/` or the content index directly. ✔ (grep: only `engines/knowledge/` and backups, which copy the whole data folder; the kit passes 14 of 14.)

### Phase 3 — Docking and standards (M) · done 2026-11-17
What was built:
- **Engine packages** (`engines/kernel/packages.py`): an `engine.json` (id, title, version, entry, kernel_api, requires, uses, views) next to the code. Packages are discovered in `engines/` and `<data>/engine-packages/` without running them, and imported on attach (outside packages under a private name).
- **Runtime** (`engines/runtime.py`):
  - attach / detach, remembered in the `engines.detached` preference;
  - requires checked on both sides, and attaching in dependency order at start;
  - `runtime.attached` / `runtime.detached` events;
  - capability queries: `producers(shape)`, `answering(request)`, `sources()`;
  - `add_package(folder)`.
- **Bus** (rewritten):
  - detach, with a `not_attached` error ("X isn't attached – attach it in Settings → Engines");
  - request deadlines (watchdog; RequestSpec `timeout_s`, default 60 s);
  - stream windows with `credit` messages (back-pressure);
  - per-engine stats and taps;
  - `health()` (a built-in `_health` request).
- **Host:** a bounded inbox (`busy`), credit waits, queue depth.
- **Conformance kit** (`engines/kernel/conformance.py`, `python -m engines.kernel.conformance FOLDER`):
  - manifest valid and matching engine.json;
  - handlers present, starts with health, refuses unknown requests;
  - examples answer with valid shapes, streams can be cancelled;
  - the source contract holds, and it stops cleanly.

  It found a real bug on its first run: Knowledge declared its shapes as "Table" / "Document".
- **Front end:**
  - engine-bundled views (`app/views/engine_views.py`, ViewType `engine`), kept in line with what is attached on every lookup;
  - ViewHost placeholders that keep the definition and come back on attach;
  - shared subscriptions refresh on attach / detach;
  - the builder lists sources from every attached source engine;
  - `stream_engine(window, latest)`;
  - Settings → Engines (attach, detach, check, add package, inspector) and the Bus inspector window and tool.
- `check_standard.py`: engines never import each other (only kernel / toolkit); an engine package's views.py / views/ may use Qt; `examples/` is checked too.
- **Example:** `examples/engines/sample_data` (made-up orders, a ticker stream, a bundled "Totals" view) and `examples/engines/README.md` (how to build and dock an engine).
- Decisions:
  - Packaging metadata lives in engine.json, the protocol in the code's manifest; the kit checks they agree.
  - An explicit caller timeout wins over the request's own.
  - A timed-out handler keeps running until it checks `job.is_cancelled()`, because an actor never preempts.
- **Done when** an engine built in a separate folder passes the kit and docks without changes to DevOS, and detaching it leaves its pages showing placeholders that come back to life when it is re-attached. ✔ (`tests/test_docking.py`)

### Phase 2 — View runtime, shapes and view API v1 (L) · done 2026-11-17
What was built:
- **Shapes v1** (`engines/kernel/shapes.py`, [docs/shapes.md](docs/shapes.md)): all ten shapes with builders and `validate()`. Arrow IPC attachments for big tables (over 2000 rows) and point clouds, `pyarrow` in `shell.nix`. Images use `bands` (not `channels`, which Series uses).
- **Query v1** (`engines/kernel/query.py`) and the first toolkits: `engines/toolkit/table.py` (`run_query`) and `engines/toolkit/series.py` (window, resample, decimate).
- **Records engine** (`engines/records/engine.py`, temporary until the Data engine): the only writer of record collections. It handles register, collections, schema, query (Table shape), get, create, update, duplicate, delete, move and add / remove property, and emits a `changed` event on every write. Declared default properties are stored in `records_adapter_defaults`.
- **View runtime** (`app/views/`, [docs/views-api.md](docs/views-api.md)):
  - view type API v1 and registry;
  - `ViewHost` (definition, data, writes, record menu, view menu, form fallback window);
  - `ChannelHub` per page, page-wide per source with per-view unlink;
  - `SharedQueries` (one request per question, refresh on `changed`);
  - `FilterBar` and the view builder (source, type, mapping, filter, sort, name, live preview).
- **Views:** table, board, timeline, calendar and form ported to `app/views/types/`; `app/ui/record_views.py` and `RecordsController` deleted.
- **Modules:** Projects and Agents run entirely through ViewHosts, with "New view…" in their toolbar. `RecordFocus` gives module code (the Agents chat) the page's selected record. Old block settings are upgraded, so existing pages keep their views.
- **Data view** module: a ViewHost as a page tile or a Canvas card.
- Decisions:
  - A view's saved state *is* its definition.
  - Selection channels are per source, so selecting a project never clears the selected agent.
  - Engines serving views follow the "source contract" in views-api.md.
  - Builder sources come from `records.collections` until the Data engine lists more.
- **Done when** Projects runs entirely through ViewHost, the builder makes a new filtered board, views on a page share a selection (one unlinked view doesn't follow), and two views of the same query cost one subscription. ✔ (`tests/test_views.py`)

### Phase 1 — Kernel: bus, engine contract, in-process host (M) · done 2026-11-17
- `engines/kernel/`: `messages.py` (Message, EngineManifest, RequestSpec, EngineError, `plain_data`), `engine.py` (Engine, Job, Reply), `hosts.py` (InProcessHost), `bus.py` (Bus, StreamHandle, Subscription).
- `engines/runtime.py`: the shared bus (`get_bus()`, `ENGINES`), `*.log` events into the System Log, shutdown at exit.
- `app/ui/engine_bridge.py`: `call_engine`, `stream_engine`, `watch_engine`. Answers arrive on the UI thread and are dropped for deleted widgets.
- Pilot: Knowledge engine stub (list, get, read_text, search) behind the Library module's list and preview.
- Decisions:
  - Payloads are **always** copied through JSON, in every run. Measured cost: about 0.17 ms for a small round trip and 1.2 ms for a 200-row table.
  - Engines open their own database connection on their own thread.
  - Requests are strict: unknown keys are a `bad_request`.
  - A ToolSpec that needs an engine calls `call_engine`.
- **Done when** bus tests cover ordering, cancel, errors and JSON round-trip, and one real feature runs over the bus. ✔

### Phase 0 — Ground rules and safety net (S) · done 2026-11-17
- This plan and [data-ownership.md](data-ownership.md).
- Backups (`app/data/store/backup.py`): SQLite copied with the online backup API (consistent while DevOS runs), unchanged files hard-linked to the previous backup, a SHA-256 manifest, pruning (newest 5 manual backups, 14 days of safety backups). Restores run at start-up after backing up the current state. The command line refuses while DevOS holds the data folder.
- `Database.migrate()` backs up before upgrading tables that hold data, and does not migrate if that backup fails.
- Settings → Data: data folder, Back up now, Check backup, Restore at next start. The "Back up data" tool is in Ctrl+K.
- Migration fixture `tests/fixtures/data_v0` (made by the app before this plan) with frozen expectations; `check_data_fixture.py` reads it with the current app.
- `check_standard.py`: `engines/` and `app/data/` never import PySide6, `app.ui`, `app.modules` or `app.views`.
- **Done when** a backup restores to an identical, working workspace and the fixture opens in today's app. ✔

## Direction

DevOS is a **host platform** for organising and visualising any kind of data: charts, sensors, images, lidar, census and real estate, simulations.
- **Front end:** pages, views and windows across 1–10+ monitors. It knows data shapes, not engines.
- **Engines:** specialists (Knowledge, Data, Spatial, Simulation, Signals, Conversation, Services) that are docked and undocked, built in isolation and tested against a conformance kit.
- **Data streams:** dock the same way, and are routed from sources through engines into views.
- **Distribution:** later, engines run in other processes and on other machines (k3s) while the interface stays local.
- **Standards:** contracts are standardised early and kept small; internals stay free.

## Future ideas

Ideas raised but not yet placed in a phase:
- Grid: fetch the next page as you scroll, instead of ‹ › page buttons.
- Chart: more than two series (one per group of a select field).
- Data: formula properties that refer to other collections (relations, rollups).
- Document view: reload a note that another view or window changed while it is open (today only the item you switch to is re-read).
- Backlinks in the Document view (which notes link here), from Knowledge's `links`.
- Shareable template files (held: templates stay local for now).
- Views rendered in a browser on another machine or a tablet (after Displays).
- A GPU-side renderer for very large point clouds and many live views (when Displays or lidar need it).
- Sandboxed user views for views you didn't write yourself (decided in Phase 12).
- Comms text and calls (placeholders today; a provider is still to be chosen).
- Real estate listings APIs, beyond public parcel and price-index data (licences to check).
- KWin integration for exact window placement on KDE Wayland.

## Open questions

- **Phase 6:** MapLibre GL for the interactive map (recommended), or keep Leaflet? For print layouts, render them in the engine, or hand them to QGIS (`qgis_process` with a layout)?
- **Phase 7:** are all monitors on one machine, or can some belong to other machines from the start?
- **Phase 12:** Python-only user views, or also a sandboxed option?
- **Phase 13:** NATS (recommended) or gRPC; is this machine a k3s node or a client?
- **Phase 13:** the Library's `path` / `new_path` only work on this machine; a remote Knowledge (or a remote engine using files) needs a file-transfer request. Stream bytes as attachments, or share a volume?
