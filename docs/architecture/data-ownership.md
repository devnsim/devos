# Data ownership map

What DevOS stores today, where, who writes it, and which engine of the [engine plan](engine-plan.md) will own it. Update this file in the same session as any change that adds, moves or removes stored data.

**Owner** is the engine that will be the only code allowed to read and write that data directly. **Workspace** means it describes how your desk is arranged, and stays in the workspace database (`devos.sqlite3`) after the engine phases.

As of 2026-11-17 (Phase 4: Knowledge owns the Library; phase numbers follow the plan's renumbering with Docking, Spatial and Displays).

## The data folder

`~/.local/share/devos/` (override: `DEVOS_DATA_DIR`). Everything in it is covered by backups except browser caches, SQLite side files (`-wal`, `-shm`, `-journal`), `backups/` itself, `devos.lock` and `restore-pending.json`.

### `devos.sqlite3`: tables

| Namespace | Tables | Written by today | What it holds | Owner | Moves in |
|---|---|---|---|---|---|
| (database) | `schema_versions` | `Database.migrate` | Migration version per namespace | Workspace | stays |
| core | `module_instances`, `module_settings` | `ModuleContext`, every module's `save_setting()` | Each module tile's identity and settings (JSON). Settings include view state *and* some data references (selected record, dataset, note, site URL) | Workspace; data references are re-pointed at engine ids when their module becomes a template | Phases 4–11 |
| core | `canvas_layouts` | `ModuleCanvas`, `InfiniteCanvas`, `BlockCanvas` (via settings) | Page and Canvas layouts: which module sits where | Workspace; module kinds map to templates in Phase 5c (Spatial's in 6a) | 5c |
| core | `user_preferences` | Window, sidebar, log panel, `agents.ollama_url`, `engines.detached` … | App-wide preferences (incl. which engines you detached) | Workspace; engine settings (e.g. the Ollama address) go to their engine | 10, 11 |
| pages | `page_folders`, `pages` | `PageStore` | Your pages, folders, favourites, page kind and state | Workspace | stays |
| activity_log | `log_sessions`, `log_entries` | `ActivityLog.flush()` | System Log history | Workspace | stays |
| content | `content_items` | The Knowledge engine only (`engines/knowledge/store.py`, its own connection) since Phase 4; everything else asks it (`app.clients.library`) | Index of Library files: name, kind, file name, size, metadata | Knowledge | done (4) |
| records | `record_collections`, `record_items` | The records engine (`engines/records/`, its own connection) since Phase 2 | Records with JSON values; user-added properties | Data | 5 |
| records_adapter | `records_adapter_defaults` | The records engine (`register`) | Each collection's declared default properties and noun | Data (replaced by template schemas) | 5 |
| map | `map_places` | `MapStore` | Your places | Spatial (copied into a GeoPackage layer) | 6a |
| agents | `agents_conversations`, `agents_messages` | `ChatStore` | Agent chats | Conversation | 10 |
| comms | `comms_accounts`, `comms_messages`, `comms_threads`, `comms_texts` | `CommsStore` | Mail accounts, cached mail headers and bodies, local text threads | Conversation | 10 |
| trading_chart | `trading_chart_bars`, `trading_chart_backtests` | Financial Chart store | Cached market bars, saved backtests | Signals | 9 |
| wave_sim | `wave_sim_materials`, `wave_sim_snapshots` | Wave Simulator store | Custom materials, saved simulation snapshots | Simulation | 8 |

### `library/<kind>/`: files

Files have random names on disk; `content_items` maps them to the names you see. **Knowledge keeps every file** and is the only code that creates, renames or deletes them (since Phase 4); the engine in the last column is the one that *uses* that kind and asks Knowledge for it. Tools that open a file with their own library (SQLite, Kùzu, openpyxl) ask Knowledge for its `path`, or for a `new_path` to write and then `register_path`.

| Kind | Extensions | Written today by | Used by (engine) |
|---|---|---|---|
| note | `.md` `.txt` | Notes, Quick note tool, agent file export, chat "Save to Library" | Knowledge |
| dataset | `.csv` | Graph (import, mock data), Database Editor "save as dataset" | Data |
| capture | `.npz` | RF Imaging | Simulation |
| cad | `.stl` `.obj` … `.json` (parts) | CAD import, Part Design (part JSON + STL), Wave Simulator scene import | Simulation |
| database | `.sqlite3` `.db` | Database Editor (create, import) | Data |
| graph | `.kuzu` | Graph Database | Data |
| sheet | `.xlsx` `.ods` `.csv` | Spreadsheet | Data |
| geo | `.geojson`; `.gpkg` from Phase 6 | Map (import, "My places" export) | Spatial |
| image | `.png` … | Capture tool, browser downloads | Knowledge |
| file | anything else | Records "Export as JSON", browser downloads, Library import | Knowledge |

User databases are edited in place by the Database Editor (writes go straight to the file); `library().refresh_size()` (Knowledge's `refresh_size` request) keeps the index in step.

### Other entries

| Path | What | Owner |
|---|---|---|
| `browser/`, `browser-containers/<name>/` | Qt WebEngine profiles: cookies and logins (`storage/`, backed up), caches (`cache/`, not backed up) | Shell (browser views); not an engine |
| `backups/<time>/` | Backups with `backup.json` manifests | Backup service (`app/data/store/backup.py`) |
| `restore-pending.json` | Backup to restore at the next start | Backup service |
| `devos.lock` | Held while DevOS runs (restores refuse to replace the folder under it) | Backup service |
| `engine-packages/<package>/` | Engine packages you added (code + engine.json; backed up with everything else) | Runtime (`engines/runtime.py`) |

## Outside the data folder (never in backups)

| Where | What | Owner |
|---|---|---|
| System keyring, service `devos-comms` | Mail passwords | Conversation |
| System keyring, service `devos-agents` | Claude / Gemini API keys | Conversation |
| System keyring (from Phase 6) | Census API key | Spatial |
| `~/.cache/devos.log` | Warnings and errors echoed by `run.sh` | Shell |
| `~/.n8n/` | n8n workflows and credentials | n8n itself; Services supervises it |
| Ollama's model folder | Pulled models | Ollama itself; Services supervises it |
| Memory only | Physics worldlines, live RF frames, streamed chat text before it is saved | Simulation, Conversation |
| Repo `build/` folders | Native builds (`app.native`, `core/physics/build`) | Rebuilt on each machine |

## Rules

1. One owner per table and per Library kind. From an engine's phase onward, only that engine touches its data; everything else asks over the bus. Done for the Library (Knowledge, Phase 4: `check_standard.py` stops `app/` importing engine internals) and record collections (records engine, Phase 2); `get_collection` remains only in the Phase 0 fixture check.
2. Data moves to its engine's store during that engine's phase: copied, verified with the fixture, and old tables kept for one version.
3. Secrets never go into SQLite or backups (`app/data/store/secrets.py`).
