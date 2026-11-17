# DevOS — guide for agents and contributors

Read this before changing code. It is short on purpose; the README has the full Lexicon.

## 1. How DevOS is built

```
Window ─ sidebar (user pages & folders, SQLite)  +  TabArea (tiling panes of tabs)
           └─ every page is a CanvasPage (app/ui/pages/canvas_page.py)  ← THE page template
                └─ ModuleCanvas: tiles holding module apps (app/modules/<name>/)
                     └─ a module = ModuleView (app/modules/base.py) + optional BlockCanvas of blocks
```

| Layer | Where | Notes |
|---|---|---|
| Tiling engine | `app/ui/widgets/tiling_layout.py`, `app/ui/canvas/tile_canvas.py` | One implementation for tab panes, module tiles and blocks. |
| Module app | `app/modules/<name>/` — `view.py` (UI), `blocks.py` (views as blocks), `engine.py` (logic), `store.py` (SQLite) | Register in `app/modules/registry.py`. |
| Data | `app/data/` — `activity_log.py`, `store/database.py` (SQLite), `store/content.py` (user files), `store/pages.py`, `tensor4d.py`, `em_materials.py` | No Qt in `app/data/`. |
| Native | `core/src/*.cpp` + `app/native/` (bridge + identical NumPy mirror) | Build: `python -m app.native.build`. |
| Physics engine | `core/physics/` — standalone C++20 project: `libdevos_physics.so` (MuJoCo + Jolt behind `PhysicsWorld`), `devos_physics_cli`, tests, thin pybind11 module `devos_physics` | **Strict boundary: nothing in `core/physics/` may reference `app/`.** Build: `cmake -S core/physics -B core/physics/build && cmake --build core/physics/build -j && ctest --test-dir core/physics/build`. See `core/physics/README.md`. |

## 2. Use these — don't rebuild them

| Need | Use |
|---|---|
| Module settings, live timer that pauses when hidden, Live/Pause button | `ModuleView` (`app/modules/base.py`): `setting()`, `save_setting()`, `setup_live()` |
| Split/rearrange/close views inside a module | `ModuleView.build_block_canvas(specs, default_layout)` + `BlockSpec`; blocks implement `refresh()`, optional `block_state()` / `restore_block_state()` |
| Styled pyqtgraph plot | `styled_plot()` / `style_plot_item()` (`app/ui/plot_tools.py`) |
| QSS property change that must repaint | `set_style_property()` (`app/ui/ui_tools.py`) |
| Small muted caption / flat icon button | `control_label()`, `tool_button()` (`app/ui/ui_tools.py`) |
| 3D view | check `opengl_unavailable_reason()` first (`app/ui/gl_support.py`) — a failing GL widget crashes the app |
| Persist anything | SQLite via `ModuleContext.database`; module tables prefixed `<module>_`, created with `database.migrate("<module>", [...])` |
| Passwords, API keys | `get_secret()` / `set_secret()` (`app/data/store/secrets.py`): system keyring, never SQLite |
| Recursive filters (EMA, Wilder, OU…) | `app.native.ema()` (C++, NumPy fallback) |
| Announce what the app does / report failures | `get_activity_log()` (`app/data/activity_log.py`): `with log.step("source", "Load X"):` logs success or FAILED + details; `info` / `warning` / `error(source, msg, error)`. Never fail silently; don't log UI twitches (tab moves, hovers) |
| Store a user file (note, dataset, capture, CAD…) | `get_content_store()` (`app/data/store/content.py`): `create_text`, `add_file`, `add_bytes`, `new_path` + `register_path`. Never ask the user where to save |
| User-level preference | `database.get_preference(key)` / `set_preference(key, value)` |
| Tool settings window (gear icon) | `SettingsDialog` (`app/ui/settings_dialog.py`): `add_form_tab()`, `add_tab()`, `applied` signal |
| Top row of a tool under the corner controls | end it with `reserve_controls_space(row)` (`app/ui/canvas/tile_canvas.py`) |
| Tile with fading corner controls | `CornerControlsTile` (`app/ui/canvas/tile_canvas.py`) |
| React to mouse presses anywhere in a window | `mouse_hub()` (`app/ui/window_events.py`): `pressed` signal / `add_press_handler()`. **Never** `QApplication.installEventFilter` with a Python filter — PySide segfaults on Qt WebEngine's internal objects |
| Pages in several windows / a page's lifecycle | `MainWindow.workspace` (`Workspace`): `open_in_new_window()`, `window_with_page()`; never keep page widgets per window. A page without a tab is suspended by `TabArea` (`set_web_views_suspended()`); anything else that must stop when its page closes should stop when hidden (like `setup_live`) |
| Web content | `BrowserPage` (`app/ui/pages/browser_page.py`, `profile=` / `home_url=` to embed it); `web_support.browser_profile()` or `container_profile(name)` (separate logins) for any other QWebEngineView. App exit calls `shutdown_web_engine()` (pages before profiles, or Qt WebEngine segfaults) |
| Data that is "a list of things with properties" (agents, projects …) | `get_collection(name, [PropertySpec…])` (`app/data/store/records.py`) + views as blocks: `RecordsController` + `record_block_specs()` (`app/ui/record_views.py`: table, board, timeline, calendar, form); or subclass `RecordsModule` (`app/modules/records_module.py`) |
| Actions a user can put on a toolbar / run with Ctrl+K | `register_tools([ToolSpec(...)])` (`app/ui/tool_bag.py`); module tools set `module_type_id` (they only show for that module). Toolbars themselves: `ToolDock` / `ToolTray` (`app/ui/toolbars.py`), already around every module tile |
| Slow work (network, big files, process scans) | `run_in_thread(owner, work, on_done, on_progress=None)` (`app/ui/ui_tools.py`); `on_done(result, error)` runs on the UI thread; with `on_progress`, `work(report)` streams values there too. Never block the UI thread for more than a few ms |
| Open / bring forward a view in a module | `block_canvas.show_block(kind)`; records: `controller.open_form(id)`, `record_menu()` (add module actions via `RecordsModule.record_actions()`) |
| Block skeleton, table view, one-line status | `block_layout()`, `table_view()`, `fit_columns()`, `add_button()`, `status_label()` / `set_status()` (`app/ui/ui_tools.py`) |
| Page-level behaviour that depends on what a page is | page kind (`PageStore`, `PAGE_KIND_*`); `MainWindow.page_widget()` builds the right page class |
| Colours / sizes | tokens in `app/ui/theme.py`; never hard-code colours in widgets |

If you write the same helper a second time, move it into one of these files instead.

## 3. Recipes

- **New module:** `app/modules/<name>/view.py` with `class XModule(ModuleView)` taking `context=None`; views as blocks in `blocks.py`; one `ModuleSpec` in `registry.py`.
- **New block in a module:** a QWidget with `refresh()`; add a `BlockSpec` to the module's `block_specs()`.
- **New tool:** one `ToolSpec` in `register_tools([...])` at the bottom of the module's `view.py` (or in `tool_bag.py` for global tools); `run(module_view)` does the work.
- **New records module:** subclass `RecordsModule` with `COLLECTION`, `NOUN`, `PROPERTIES`, `DEFAULT_LAYOUT` (see `app/modules/projects/view.py`).
- **New indicator / strategy:** one entry in `INDICATORS` / `STRATEGIES` (`app/modules/trading_chart/`).
- **Physics engine change:** C++ in `core/physics/src/` (public API in `include/devos/physics/`, no Jolt types there); add a check to `tests/test_physics.cpp`; rebuild and run `ctest` plus `devos_physics_cli core/physics/scenes/demo --verify` (determinism, seek, branch must stay bit-exact).
- **New native function:** implement in `core/src/`, declare in a header, bind in `core/src/bindings.cpp`, mirror in `app/native/numpy_engine.py`, dispatch in `app/native/__init__.py`, compare in `self_test()`.
- **Page behaviour for every page:** edit `CanvasPage` only (browser pages: `BrowserPage`).
- **New page kind:** a `PAGE_KIND_*` + icon + noun in `pages.py`, a page class in `app/ui/pages/`, a branch in `MainWindow._build_page()`, a button on the Create New panel that emits `kind_requested(kind)` (see `InfiniteCanvasPage`).

## 4. The code standard (checked by a tool)

Every file: **header** (FILE / OVERVIEW / PURPOSE / CONTRACT / NOTES) → `[DEFINITIONS]` → `[INDEX]` → numbered sections `# [X.Y] name` … `# --- END [X.Y] ---`. Naming rules and the Lexicon are in the README.

- **Every edit adds a dated NOTES line** at the top of the file: what changed and why (`#   - [YYYY-MM-DD] ...`).
- Keep `[INDEX]` in step with the section markers; keep CONTRACT honest (what it needs, what it exposes).
- New files, classes, constants or shared terms get a README Lexicon row.

Run before finishing any task:

```sh
python tools/check_standard.py          # header / index / sections / dated notes / unused QSS
python -m pytest -q                     # tests (offscreen Qt, temporary database)
```

## 5. Working rules

- Run things inside `nix-shell` (Python, Qt, numpy, pyqtgraph, trimesh, cmake, pybind11 live there).
- Headless runs: `QT_QPA_PLATFORM=offscreen`; use `DEVOS_DATA_DIR=$(mktemp -d)` so tests never touch the user's database (`~/.local/share/devos/devos.sqlite3`).
- **Commits: one commit per session, sequenced at the end** (more only for a major version). Don't commit mid-session.
- Delete code that nothing uses and nothing plans to use; git history keeps it.
- Never delete user data silently (closing a tile keeps its data; deleting a folder keeps its pages).
