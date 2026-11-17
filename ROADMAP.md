# DevOS Roadmap

Last updated: **2026-11-17** (Phase 5 done, v0.3.0. Direction change: DevOS moves to the OS layer on NixOS + Hyprland; phases from 6 renumbered — see Now and Direction)

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
- **Runs as:** one Qt application that hosts the engines in-process (this becomes *embedded mode*, kept as a fallback when the DevOS desktop arrives in Phases 6–7).

## Now

### Phase 6 — OS-layer foundation: flake, devos-core, processes (XL)
DevOS moves down a layer (see *Direction*): the engines run as a headless service, `devos-core`; DevOS windows and native apps become tiles that Hyprland arranges. This phase builds the ground for that without changing how DevOS looks today. Nothing in it touches the running desktop: everything is tried in a nested session or a VM.

- **6.0 Spike (S) — go / no-go before the rest.** In a nested Hyprland session (a window on the current desktop):
  - QGIS starts chromeless from a customization profile (`QGISCUSTOMIZATION3.ini`), with a small PyQGIS adapter plugin that joins the bus;
  - a DevOS grid window shows the same GeoPackage layer; selecting a feature in either selects it in the other;
  - Hyprland window rules place both (matched by app id / window title);
  - a PySide6 window runs as a layer-shell panel (LayerShellQt), as the sidebar will;
  - Zenoh from Python: a request / reply and an Arrow table through shared memory, timed against a plain Unix socket.

  **Done when** each item has a written result (works / works with caveats / doesn't) in `docs/architecture/os-layer-spike.md`, and the open questions below that depend on it are answered.
- **6a Flake and the development loop (M)**
  - `flake.nix` with `packages` (`devos`, `devos-core`), `devShells.default` (today's `shell.nix`, which stays as a thin flake-compat wrapper so `nix-shell` keeps working), `checks` (tests, `check_standard.py`, conformance kits).
  - **Three-speed iteration, never on the host session:**
    1. *seconds* — nested Hyprland in a window, configs from `./configs/`, its own `XDG_CONFIG_HOME` / `XDG_RUNTIME_DIR`;
    2. *minutes* — a `devos-desktop` `nixosConfiguration` run as a VM (`nixos-rebuild build-vm`, virtio-gpu), a whole session to log into and break;
    3. *automatic* — `pkgs.testers.runNixOSTest` VM tests (headless Hyprland) in `nix flake check`.
  - Agent rules in AGENTS.md: never `sudo nixos-rebuild switch` or edit `/etc/nixos`; validate with `nix flake check`, `nix build .#<pkg>`, nested sessions, `build-vm` and VM tests; raw configs live in `./configs/` until they are promoted into `./modules/` and `./pkgs/`.
- **6b Engines as processes (L)** (was 13a)
  - `devos-core`: the engines in their own process, started as a `systemd --user` service (a slice per engine group).
  - Out-of-process hosts on the transport the spike picks (Zenoh recommended), Arrow tables in shared memory; the conformance kit runs against process hosts too. **`core/physics` docks first**, as its own process.
  - Selection and time channels move from the page (`ChannelHub`) onto the bus, so windows in different processes share a selection.
  - **Services through systemd:** start, stop, status and logs of n8n, Ollama, Jupyter … as `systemd --user` units over D-Bus (replaces the old Phase 11 supervisor; the Services engine becomes a thin client of systemd).
- **6c The front end as a bus client (M)**
  - The DevOS app connects to `devos-core` when it runs, and hosts the engines itself when it doesn't (embedded mode, unchanged).
  - **Tile windows:** any tile can open as its own borderless window (title `devos:tile:<id>` for compositor rules), all from one Qt process: a template (several views on its block canvas), a single view, a form, a button panel.
- **Done when** Data, Knowledge and physics run in `devos-core` under systemd; the DevOS app (embedded) and a tile window in a nested Hyprland session show the same data with linked selection; and the flake builds, its VM test passes, and today's test suite still passes in embedded mode.

**Start here (6.0).** The spike, in a scratch folder (`spikes/os-layer/`), nothing wired into DevOS yet: nested Hyprland, QGIS customization profile + adapter plugin, a minimal grid window on the bus, LayerShellQt from PySide6, Zenoh shm timing. Write the results down before 6a.


## Next

### Phase 7 — The DevOS desktop on Hyprland (XL) (absorbs the old Displays phase)
The goal is your daily desktop: it should feel like DevOS does today (sidebar, pages, tabs, tiles), with the parts becoming desktop panels and any tile able to stand on its own like an app.

- **7a Session and workspace manager**
  - A NixOS module (`programs.devos-desktop` / `services.devos-core`) and a Hyprland configuration generated from Nix, pinned through the flake (Hyprland updates are deliberate).
  - A **workspace manager** service on the bus: a page is a Hyprland workspace; it opens and restores tiles and native apps, learns which window is which (Hyprland IPC events), and saves layouts.
  - New contracts: **tile manifests** (what a tile shows, which shapes it takes and gives, which channels it follows) and **route definitions** (which data goes to which tile; *pipelines are data*).
- **7b Desktop panels:** the sidebar (pages, folders, favourites), the tab strip, the status bar and the System Log become layer-shell panels; tabs map to Hyprland groups (tabbed windows); Ctrl+K works across the desktop; Settings is a tile.
- **7c Tile kinds:** template tiles (several views on a block canvas), view tiles, form tiles, control tiles (buttons and tool bags bound to actions), native app tiles (wrapped). Any tile floats, tiles, groups into tabs, and moves between workspaces and monitors.
- **7d Display profiles:** named arrangements by monitor identity (desk; vehicle cockpit), bare full-screen surfaces, graceful unplugging, per-view redraw rates.
- **7e Desktop essentials** for a daily driver: launcher, notifications, lock and idle, screenshots, clipboard, portals (`xdg-desktop-portal-hyprland`), polkit agent, network / audio / bluetooth.
- **Done when** you log into the DevOS session instead of KDE for a week without falling back; a saved workspace restores its pages, tiles and app windows across monitors; unplugging a monitor degrades gracefully; and embedded mode still passes its tests.

## Later

**Order:** 6 → 7 in order (7 needs 6's processes and tile windows). After 7, 8–12 can go in any order; Spatial (8) is first by priority. 13 needs 6b. Sizes are relative: S is a session or two, M a handful, L a dozen or so, XL is split into sub-phases. Each phase ends with a minor version tag.

### Phase 8 — Spatial engine and the QGIS tile (XL) (was Phase 6)
Built on GDAL / OGR (formats), PROJ (coordinate systems), GEOS / shapely (geometry), GeoPandas and pyogrio (tables with geometry), rasterio (rasters) and PDAL (lidar). Layers are stored as **GeoPackage** in the Library. **The map is QGIS itself**, as a native tile: a Nix wrapper (`devos-qgis-tile`) starts it chromeless from a customization profile, and a separate, GPL-licensed adapter plugin connects it to the bus (layers from the Library, selection both ways, project outputs into the Library). DevOS never imports PyQGIS; it talks to QGIS over the bus and through its files and `qgis_process`.

- **8a Data, styling and the QGIS tile (first)**
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
  - **QGIS tile:** the engine writes each layer's style as QML, so QGIS shows it styled; clicking a tract in QGIS selects its row in a DevOS grid and chart, and the other way round. In embedded mode (no compositor), QGIS opens as an ordinary window with the same adapter.
  - **Cartographic output:** print layouts (title, legend, scale bar, north arrow, credits) rendered by QGIS through `qgis_process`, to PNG / PDF / SVG.
  - Templates: Map (the QGIS tile + a layer table), Census explorer, Places (the old Map module; its places are copied into a GeoPackage layer). Removes the `map` module.
  - **Done when** you can load a county's tracts with ACS median household income, style a natural-breaks choropleth, see it in the QGIS tile, select a tract there to see its row in DevOS, and export a print-quality PDF.
- **8b Processing (later)**
  - QGIS processing algorithms through `qgis_process`, as Spatial requests with progress streams, including the algorithm catalogue.
  - Spatial joins, buffers, dissolve and zonal statistics.
  - Geocoding (US Census Geocoder).
  - Rasters: elevation, imagery.
  - Lidar (LAS / LAZ through PDAL) as georeferenced point clouds.


### Phase 9 — Simulation engine and the FreeCAD tile (XL) (was Phase 8)
- Wraps `core/physics`, out of process since 6b: load, step, seek, branch and controls; worldline as Series / Tensor streams; contact events.
- Wave Simulator, RF Imaging and Tensor4D move in; their views (waterfall, 3D scatter, slice, heatmap; 3D scene with a timeline scrubber; RF controls) are DevOS micro-tiles shipped with the engine.
- **FreeCAD as a native tile** (`devos-freecad-tile`: a minimal workbench profile and a bus adapter): parts and assemblies, STEP export into the Library, "part → scene" for physics. Replaces the CAD module and its Part Design workbench.
- Templates: Physics scene, RF lab, Part design (FreeCAD tile + parameters form + physics scrubber).
- **Done when** the physics demo runs with scrub / seek / branch from a DevOS tile, a FreeCAD part becomes a physics scene, and the CAD, Wave Sim, RF Imaging and Tensor Inspector modules are gone.

### Phase 10 — Signals, streams and edge nodes (L) (was Phase 9)
- **Series store** (market bars, system history) with retention rules. Indicators work on any series. Strategies and backtests run as jobs.
- **Stream sources:** tiny engines that only produce streams; manifest with shape, rate, units and schema. Connectors: serial, UDP, MQTT, WebSocket, file replay, market feeds, simulation output.
- **Edge nodes:** RP2350 / ESP32 sensors publishing through `zenoh-pico` (or serial / MQTT bridges) straight onto the bus.
- **Routing** uses the route definitions from 7a; a patch-bay tile edits them. One common clock and time channel. Record and replay. Decimation for display.
- Views: candles with drawings, line / area, indicator panes, gauge / KPI, sparkline, backtest report, patch bay. Templates: Market chart, System dashboard, Sensor console.
- **Done when** a live sensor stream from an edge node docks, records, replays in sync with another stream, and an indicator works on it as easily as on a price.

### Phase 11 — Conversation engine and agent runtime (L) (was Phase 10)
- Threads, participants, messages and attachments. Connectors: Claude, Gemini, Ollama, IMAP / SMTP.
- **Agent runtime:** tools are engine and tile requests from the manifests (QGIS and FreeCAD adapters included); permissions follow each request's effect; anything that writes, sends or spends asks first; every tool call is logged.
- Views: chat, inbox, reader, composer, thread list, tool approvals. Templates: Agents, Inbox.
- **Done when** an agent answers "which projects are blocked?" by querying the Data engine, and "which tracts have the highest rent burden?" by asking Spatial and showing them in the QGIS tile.

### Phase 12 — User-written views and tile packages (S)
- The plugin mechanism built-in and engine-bundled views use, opened to users: view and tile packages in `<data>/views/<name>/` (a manifest + a Python module); a permission prompt for actions; a kit with a guide, an example, a fake-engine harness and reload on save.
- Python views run with the user's rights, so they load only from the user's own data folder.

### Phase 13 — Distributed cluster and edge fleet (XL)
- **13a** Network transport (the bus from 6b across machines) over a WireGuard mesh. Per-engine placement: local, remote, or auto with local fallback.
- **13b** Multi-host NixOS: `nixosConfigurations` for `workstation`, `vehicle-cockpit` and `compute-worker`, deployed with deploy-rs or Colmena; k3s workers for heavy engines; GPU / AVX2 node labels. Workers keep no data, and secrets stay local.
- **13c** The edge fleet (from Phase 10) provisioned and updated the same way.
- **Done when** a physics run on a cluster node passes the same bit-exact `--verify` as a local run, and the vehicle-cockpit configuration deploys and restores its display profile.

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

DevOS is a **host platform** for organising and visualising any kind of data: charts, sensors, images, lidar, census and real estate, simulations. **From Phase 6 it moves down to the OS layer** (decided 2026-11-17): instead of rebuilding GIS, CAD or office tools inside Qt, DevOS orchestrates data and arranges native apps and its own tiles on a NixOS + Hyprland desktop.

- **`devos-core`:** the engines (Knowledge, Data, Spatial, Simulation, Signals, Conversation), headless, as `systemd --user` services; docked, undocked and tested against the conformance kit as today.
- **The bus:** the same messages in-process, between processes on one machine (shared-memory Arrow) and across machines (WireGuard) — one transport family from edge sensors to cluster workers (Zenoh is the likely choice).
- **The desktop:** Hyprland arranges every window. DevOS's sidebar, tabs and status bar become desktop panels; pages become workspaces; a workspace manager restores them.
- **Tiles:** DevOS tiles (templates with several views, single views, forms, button panels) and native app tiles (QGIS, FreeCAD) wrapped by Nix and joined to the bus by small adapters. Tiles share selection and time through the bus, and **route definitions** say which data goes where.
- **Embedded mode stays:** the DevOS app as it is today — one window with its own tabs and tiling, engines in-process — for other desktops, other machines and as a fallback.
- **Nix all the way:** a flake builds DevOS, its tiles and the desktop; development happens in nested sessions and VMs, never on the running system.
- **Standards:** contracts are standardised early and kept small (now also tile manifests and route definitions); internals stay free.

## Future ideas

Ideas raised but not yet placed in a phase:
- Grid: fetch the next page as you scroll, instead of ‹ › page buttons.
- Chart: more than two series (one per group of a select field).
- Data: formula properties that refer to other collections (relations, rollups).
- Document view: reload a note that another view or window changed while it is open (today only the item you switch to is re-read).
- Backlinks in the Document view (which notes link here), from Knowledge's `links`.
- Shareable template files (held: templates stay local for now).
- Views rendered in a browser on another machine or a tablet (after Phase 7).
- A GPU-side renderer for very large point clouds and many live views (when display profiles or lidar need it).
- Sandboxed user views for views you didn't write yourself (decided in Phase 12).
- Comms text and calls (placeholders today; a provider is still to be chosen).
- Real estate listings APIs, beyond public parcel and price-index data (licences to check).
- A small MapLibre map tile for live tracks (vehicle GPS, sensors) where a full QGIS tile is too heavy.
- A LibreOffice tile through UNO (held: the Data engine's sheet view covers workbooks).
- Web App tiles as wrapped browser windows with their own profiles (`--app`, separate user-data dirs), like QGIS and FreeCAD tiles.

## Open questions

- **Phase 6.0 (spike):** Zenoh for the local process transport too (recommended if shared memory works well from Python), or a plain Unix socket first and Zenoh only across machines?
- **Phase 6.0:** desktop panels from PySide6 need layer-shell (LayerShellQt has no Python bindings): write a small binding (recommended), fall back to Hyprland window rules (pinned, no focus), or build panels in another toolkit (Quickshell / QML)?
- **Phase 6a:** which GPU does this machine (and the vehicle rig) have? NVIDIA needs extra care with Hyprland and in VMs (virtio-gpu GL); it decides how far the VM loop can go for graphics.
- **Phase 6b:** when the DevOS app starts and `devos-core` isn't running, should it start the service (recommended) or host the engines itself (embedded mode)?
- **Phase 7a:** a page = one Hyprland workspace (recommended), or several pages share a workspace? And who owns pages and layouts then: a new "desktop" engine (recommended), or the front end as today?
- **Phase 7e:** desktop essentials from nixpkgs (fuzzel, mako, hyprlock, hypridle …) or DevOS-native panels where they overlap (launcher = Ctrl+K, notifications = System Log)?
- **Phase 8:** the QGIS adapter plugin is GPL: keep it in its own repository / package (recommended), or a folder of this repository with its own licence?
- **Phase 9:** retiring the Part Design workbench for FreeCAD loses its step-by-step guide; keep a guided "Part design" template over the FreeCAD tile, or rely on FreeCAD's own?
- **Phase 7d / 13:** are all monitors on one machine, or can some belong to other machines (the vehicle rig)?
- **Phase 12:** Python-only user views, or also a sandboxed option?
- **Phase 13:** is this machine a k3s node or a client?
- **Phase 13:** the Library's `path` / `new_path` only work on this machine; a remote Knowledge (or a remote engine using files) needs a file-transfer request. Stream bytes as attachments, or share a volume?
