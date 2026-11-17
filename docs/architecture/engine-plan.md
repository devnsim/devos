# DevOS Engine Plan

DevOS becomes a **host platform** for organising and visualising any kind of data. The front end (Python + Qt) is the platform: pages, views, windows across many monitors. Behind it, **engines** do the specialist work: computation, storage, simulation, mapping. Engines and data streams can be docked and undocked; DevOS keeps working without any one of them. Templates replace hard-coded modules, and users build many views of each engine's data.

This is the design. Status and the phases are in [ROADMAP.md](../../ROADMAP.md); what is stored where is in [data-ownership.md](data-ownership.md).

## Decisions

| Decision | Made |
|---|---|
| **Seven engines:** Knowledge, Data, Spatial, Simulation (Geometry inside), Signals, Conversation, Services | 2026-11-17 |
| **Engines + templates:** the picker offers templates (Projects, Agents, Census map, Inbox …) and "new view of an engine", not modules | 2026-11-17 |
| **Templates stay local:** built-ins ship in code; user templates live in the workspace database. No shareable template files for now | 2026-11-17 |
| **User-written views later**, so the view API is designed, versioned and documented from the first view onward. Every built-in view uses the same API | 2026-11-17 |
| **The physics engine is the model** for engines: no Qt, owns its data, declares its requests / events / streams, runs anywhere, is tested alone | 2026-11-17 |
| **Dockable engines:** an engine can be attached or detached; nothing breaks when one is missing (its views show a placeholder and keep their settings). Engines are built in isolation and docked after passing the conformance kit | 2026-11-17 |
| **The front end is the host:** largely independent of any engine, it only knows data shapes. Specialist views ship *with* their engine | 2026-11-17 |
| **Displays from 1 to 10+ monitors:** modular windowing, pop-out views, display profiles (from Phase 7: through Hyprland) | 2026-11-17 |
| **Dockable data streams** (later): sources linked to engines and to views | 2026-11-17 |
| **Linked views are page-wide**, with an option to unlink a single view | 2026-11-17 |
| **Standardise the contracts early** (envelope, manifests, shapes, query, errors, versions, conformance kit), never the internals | 2026-11-17 |
| **Spatial: US first, cartography first, processing later.** Built on the libraries QGIS uses; QGIS is used through its own tools and files, pinned in `shell.nix` for reproducibility (a global QGIS also works) | 2026-11-17 |
| **Core engines** (`"core": true` in engine.json) stay attached: Knowledge is one, because every other engine stores its files through it. `detach()` refuses them | 2026-11-17 |
| **The Library through a client:** modules and tools use `library()`, small blocking calls with the old store's names; slow work uses `call_engine`. Tools that open a file with their own library ask for its `path` (local only; remote engines will need a transfer request) | 2026-11-17 |
| **`app/` never imports an engine's internals**, only `engines.kernel` / `runtime` / `toolkit` (checked by `check_standard.py`) | 2026-11-17 |
| **Records keep JSON values, with indexes** (not typed columns): a new property never needs a migration; filters and sorts run in SQLite through `json_extract`, backed by expression indexes for select / number / date / checkbox properties; formula properties are computed, never stored | 2026-11-17 |
| **Engines may ask engines they declare** (`EngineManifest.uses` + engine.json): `Engine.ask()` waits for the answer; no cycles; the asker carries on without it when it's missing. Data asks Knowledge for files | 2026-11-17 |
| **Engines hear the engines they use** (`Engine.watch`, declared in `uses`): events join the engine's inbox and are handled on its thread, in order with requests. Knowledge's `refresh_size` announces in-place changes (`by` names who made them), so Data refreshes views of files other tools changed | 2026-11-17 |
| **Templates replace modules under the same ids**, so saved pages open them unchanged; old block layouts are translated (legacy_blocks), old settings read once. User templates live in the workspace database (`templates_user`), like pages; collection schemas move onto `record_collections` (from `records_adapter_defaults`, kept one version) | 2026-11-17 |
| **Specialist views ship with their engine** (Data: Sheet, SQL / Cypher consoles, Structure); generic ones (Grid, Chart, Network) are built in. `ViewType.accepts` keeps a view to the sources it fits | 2026-11-17 |
| **One Data collection id per table:** record collections by name; file tables `db:<item>/<table>`, `csv:<item>`, `sheet:<item>/<sheet>`, `graph:<item>/<node table>`; the shape names the row key (`_rowid` for file tables) | 2026-11-17 |
| **Writes keep the file's own format:** sheets are edited in place (openpyxl, formatting kept); formula cells, a node's key and views are refused, with the reason | 2026-11-17 |
| **View types declare the requests they need** besides the query (`ViewType.requests`); the builder offers a view only for data that can serve it | 2026-11-17 |
| **DevOS moves to the OS layer** (after v0.3.0): engines run headless as `devos-core` (`systemd --user`); a NixOS flake builds DevOS, its tiles and the desktop; the compositor arranges DevOS tile windows and native apps. Supersedes rebuilding GIS / CAD views in Qt and the KWin placement idea | 2026-11-17 |
| **Hyprland** is the compositor (over Niri: exact placement, scripting and IPC win over a built-in scrolling layout; pinned through the flake because its config changes between releases) | 2026-11-17 |
| **The DevOS desktop will replace KDE** as the daily driver once stable; until then, and for all development, it runs in nested sessions and VMs — never `nixos-rebuild switch` from an agent | 2026-11-17 |
| **Embedded mode stays** as a fallback: today's single-window app with its own tabs, tiling and in-process engines | 2026-11-17 |
| **A tile can hold several views** (a template on its block canvas); tiles also come as single views, forms, button panels and native apps. The desktop keeps today's feel: sidebar, tabs and status bar become desktop panels, pages become workspaces | 2026-11-17 |
| **Native apps are tiles, not rebuilds:** QGIS and FreeCAD run as Nix-wrapped, chromeless apps joined to the bus by small adapters (QGIS's is GPL, kept separate). LibreOffice is not bridged (the Data engine covers workbooks) | 2026-11-17 |
| **Phases renumbered from 6** (none had started): 6 OS-layer foundation (with old 13a), 7 DevOS desktop (with old Displays), 8 Spatial + QGIS, 9 Simulation + FreeCAD, 10 Signals + edge nodes, 11 Conversation, 12 user views and tiles, 13 cluster and fleet; the Services supervisor becomes systemd units | 2026-11-17 |

## Architecture

```
Desktop          Hyprland (from Phase 7): workspaces = pages · DevOS panels (sidebar, tabs, status) · display profiles
                 tiles = DevOS tile windows (templates, views, forms, buttons) + native app tiles (QGIS, FreeCAD)
                 embedded mode: the same front end as one window with its own tabs and tiling
Views            generic, by data shape (grid, board, timeline, calendar, form, chart, map, network, waterfall,
                 image, point cloud, chat) + specialist (shipped with their engine) + user-written
   ↕ view definitions · channels (page-wide; a view can unlink) · actions
View runtime     ViewHost · view-type registry · shared subscriptions · view builder · templates
   ↕ events · requests · streams      (control: small JSON · data: Arrow attachments)
Bus              publish / subscribe · request / reply · streams with cancel and back-pressure · inspector
   ↕
Engine hosts     in-process (Phase 1) · own process under systemd, shared-memory Arrow (6b) · remote worker (13)
   ↕
Engines          Knowledge · Data · Spatial · Simulation · Signals · Conversation · Services   (dockable)
Stream sources   sensors, feeds, files on replay, simulation output                            (dockable)
   ↕ each engine owns its store
Storage          workspace database · one store per engine · library files · system keyring · backups
```

### How data flows

Shapes are the common format on **every** connection, not only what engines produce:

```
 source (sensor, file, API, feed) ── already speaks a shape ──────────────────► view
                                  └─► engine ─► engine ─► … (shape in, shape out) ─► view
```

A source that already speaks a shape (a sensor's Series) can feed a view directly. Engines are for work: cleaning, joining, analysing, storing, simulating, mapping. The front end only sees shapes, so it never depends on which engine was involved.

## The seven engines

Engines specialise in kinds of **work**; shapes describe kinds of **data**.

| Engine | Knows about | Absorbs | Produces | Remote later? |
|---|---|---|---|---|
| Knowledge | Files and documents, Markdown, note links, search, the Library index. Always attached (others store files through it) | Notes, Library, content store | Document, Table, Graph | No |
| Data | Tables, records, typed fields, queries, formulas, relations | Database Editor, Spreadsheet, Graph Database, Projects, Agents (definitions), Calendar, Graph | Table, Graph, Series | Later, for heavy queries |
| Spatial | Things on the earth: coordinate systems, vector layers, rasters, styling and cartography, spatial joins, geocoding, georeferenced point clouds; US census and parcel data | Map | Geo, Table, Image (rasters, map exports), PointCloud | Processing jobs, yes |
| Simulation | Worlds over time (physics, RF ray tracing, RF imaging, seek / branch) and geometry (sketches, parts, meshes) | core/physics, Wave Simulator, RF Imaging, Tensor Inspector, CAD + Part Design | Tensor, Series, Geometry, PointCloud, Table | Yes: the first candidate |
| Signals | Anything over time: series store, indicators, strategies, backtests; stream sources, routing, record and replay | Financial Chart, Monitor history | Series, Table | Backtests, yes |
| Conversation | Threads between you, agents and contacts; providers; agent runtime and tools | Agents (chat), Comms | Thread, Table | Model calls can go to an Ollama node |
| Services | Outside services and processes (n8n, Ollama, Jupyter, QGIS tools), this machine's telemetry, later k3s workers | Software Monitor, Web App service start | Table, Series | No: it watches this machine |

Browser pages, Canvas pages, the page shell, toolbars and the System Log stay UI.

## Data shapes v1

Ten shapes cover the domains in view: census, real estate, lidar, sensors, markets, RF and physics, documents. Each shape has a schema, a payload, paging or windowing, change notices and a version. Small data travels as JSON; big data travels as **Apache Arrow** attachments (**GeoArrow** for geometry), which are columnar, language-neutral and shareable between processes without copying.

| Shape | What it is | Examples | Generic views |
|---|---|---|---|
| Table | Rows of typed fields | Records, census tables, parcels | Grid, board, form, cards, Gantt, calendar, chart |
| Series | Values over time (live or recorded) | Sensors, prices, CPU, physics observations | Line, area, candles, gauge, sparkline |
| Geo | Features with a location or outline (+ CRS), and rasters | Tracts, parcels, roads, elevation | Map (points, choropleth, heatmap), layer table |
| Tensor | Named-axis arrays | RF fields, simulation grids | Waterfall, slice, heatmap |
| Image | Raster images and frames | Photos, cameras, map exports | Image viewer, tiles, histogram |
| PointCloud | Points in 3D with attributes | Lidar, depth sensors | 3D points (colour by height / intensity), slices |
| Geometry | Meshes, sketches, solids | Parts, scenes, floor plans | 3D viewer, 2D sketch |
| Graph | Nodes and edges | Relations, ownership, note links | Network, tree |
| Document | Text with structure | Notes, listings | Editor, preview |
| Thread | Messages over time | Chats, mail | Chat, inbox |

**Shape toolkit:** one shared library per shape holds the everyday operations (filter, sort, resample, slice, decimate, reproject, summarise), so no engine rewrites them. Engines bring the domain knowledge.

## Communication principles

**Four layers, each replaceable without touching the others:**

| Layer | Question | Today | Later |
|---|---|---|---|
| Transport | How do bytes move? | In-process queues | Local sockets + shared memory; NATS across machines |
| Envelope | Who's talking, about what, in reply to what? | `Message` | Same |
| Contract | What can I ask, and what does it do? | Manifest + RequestSpec | + dependencies, capabilities |
| Content | What does the data mean? | Plain JSON | Shapes v1, Arrow attachments |

**Only three ways to talk, plus cancel:** request → reply ("do this"), stream ("keep sending"), event ("this happened"). If something doesn't fit these three, the design is wrong, not the bus.

**Rules:**
1. **No back doors.** Engines never import each other or the UI; they only talk over the bus (checked).
2. **One owner per piece of data.** Others ask the owner.
3. **Events are small facts** ("item 42 changed"), not data dumps. Whoever cares asks for details.
4. **Control apart from data.** Requests, manifests and events are small JSON; tables, series, images and point clouds go as Arrow attachments.
5. **Back-pressure.** Queues never grow without limit. Streams use credit, or keep only the latest value for displays.
6. **Every wait has a limit; every failure is data.** Timeouts, cancel and error codes everywhere.
7. **Safe to retry.** Request ids, and writes designed so a retry doesn't do the work twice.
8. **Observable.** Ids let you follow any message; a bus inspector shows the live traffic, latency and queue depth per engine.
9. **Share identical subscriptions.** Many views asking the same question cost one subscription.
10. **Pipelines are data.** Source → engines → views is a saved description that the runtime builds, the way a view definition becomes a view.

## Contracts (versioned)

| Contract | Fields |
|---|---|
| Engine manifest | id · version · title · requests {name, params, result, effect: none / writes / external / spends} · events · streams · shapes · owns (tables, files) · requires / uses (other engines) · bundled views · placement: local / remote-capable |
| Message | id · kind: event / request / reply / stream-item / stream-end / error / cancel / credit · topic ("data.query") · correlation · payload (JSON) · attachments (named binary: Arrow / arrays / bytes) · window (streams) · wire version |
| Engine package | engine.json: id · title · version · entry ("module:Class") · kernel_api · requires · uses · views; RequestSpec adds shape · example · timeout_s |
| Engine as an actor | One inbox, one message at a time on its own thread; long work becomes a job with progress and cancel; `EngineError` carries a message the user can act on |
| Shapes v1 | The ten shapes above: schema, payload, paging, change notices, version |
| Query v1 | source · fields · filter tree · sort · group by · aggregates · window (offset, limit) · time range · spatial extent. Executed by the engine |
| View definition v1 | id · title · view type · source {engine, query} · mapping {title, group, start, end, x, y, colour, size, location …} · presentation · interactions · channels (linked or unlinked) · version |
| View type API v1 | id · title · shapes accepted · mapping slots · settings schema · create(host) → set_data / set_channel / state / restore. The host offers request(action), emit(channel), open_view and status. **The plugin API** for built-in, engine-bundled and user-written views |
| Channels | selection (one channel per source) · time (moment or tick) · filter · focus. Page-wide; any view can unlink |
| Source contract | An engine whose data views can show answers `<engine>.query` {source params, query} with a Table shape and emits `<engine>.changed` with the source params; editable sources also answer update / create / duplicate / delete / move / add_property / remove_property ([docs/views-api.md](../views-api.md)) |
| Template | id · title · engines · schema · view definitions · block layout · toolbars · default actions · version |
| Conformance kit | The standard tests every engine passes before it docks: valid manifest, every declared request answers, unknown requests fail cleanly, cancel works, payloads are plain data, start / stop / health behave |

**Versioning:** within a major version, changes are additive only (new optional fields, new requests). A breaking change needs a new major version, and both versions run side by side for a while. No contract is generalised until two real engines need it.

## Phases

The phases, their status, what comes now and next, and the open questions live in [ROADMAP.md](../../ROADMAP.md), which is updated every session. This document holds the design they implement.

## Where every current module goes

| Today | Engine | Becomes | Phase |
|---|---|---|---|
| Notes, Library | Knowledge | Notes, Library templates | 4 |
| Projects, Calendar, Database Editor, Spreadsheet, Graph Database, Graph | Data | Templates Projects, Events, Database, Spreadsheet, Graph database, Dataset chart (same ids); view types grid, chart, network, sheet, consoles, structure | 5 ✔ |
| Agents | Data + Conversation | Agents template: definitions (5 ✔), chat and runtime (11) | 5 · 11 |
| Map | Spatial | QGIS tile; Map, Places, Census explorer templates | 8 |
| Physics, CAD + Part Design, Wave Simulator, RF Imaging, Tensor Inspector | Simulation | FreeCAD tile; Physics scene, Part design, RF lab templates; waterfall / scatter / slice view types | 9 |
| Financial Chart | Signals | Market chart template | 10 |
| Comms | Conversation | Inbox template | 11 |
| Software Monitor | systemd + Signals | System monitor template | 6b · 10 |
| Web App | Desktop (+ systemd) | A wrapped browser tile; services started as systemd units | 6b · 7 |

## Protecting user data

- **Backup first:** every migration takes an automatic backup and refuses to run without one.
- **Copy, don't move:** old tables stay readable for one version, then are removed with a log entry and a changelog note.
- **The fixture decides:** every phase opens `tests/fixtures/data_v0` with the same pages, layouts, records, chats and files (`tests/test_data_safety.py`).
- **Old pages keep working:** saved layouts naming an old module map to the matching template, with settings carried over.
- **Detaching is not deleting:** a detached engine's data stays in its store.
- **Every step is announced** in the System Log.
