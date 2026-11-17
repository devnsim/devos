# View API v1

How a view plugs into DevOS. Built-in views, views shipped with an engine (Phase 3) and user-written views (Phase 12) all use exactly this API. Code: `app/views/` (`api.py`, `host.py`, `channels.py`, `subscriptions.py`, `builder.py`); built-in views live in `app/views/types/`.

## The pieces

```
ViewHost (app/views/host.py)        one per view: definition, data, channels, actions
 ├─ the view widget                 made by ViewType.create(host): draws the data, reacts to the user
 ├─ FilterBar                       the shared filter editor (shown on demand)
 └─ shared_queries()                one engine request per (source, query), answers fanned out
ChannelHub (one per page)           page-wide selection; any view can unlink itself
```

A ViewHost can be a block inside a module, a page tile, or a Canvas card (the **Data view** module).

## A view type

```python
from app.views.api import MappingSlot, ViewType, register_view_type

class MyView(QWidget):
    def __init__(self, host): ...            # build the widget; keep `host`
    def set_data(self, data): ...            # a TableData: fields, rows, total, row(id), options(key), title(row)
    def set_selection(self, record_id): ...  # highlight the selected row (None = nothing selected)
    # optional: def extend_view_menu(self, menu): add your actions to the view's ⋯ menu

register_view_type(ViewType("my_view", "My view", "What it shows", ("table",), MyView,
                            slots=(MappingSlot("group", "Group by", ("select",), required=True),)))
```

- **requests:** other requests the view needs from its source engine besides the query (`ViewType(..., requests=("read_text", "write_text"))`). The builder only offers the view for data whose engine answers them; a saved view over data that doesn't explains why in its place ("The Document view needs data that answers read_text…").

- **shapes:** the data shapes the view can draw (see [shapes.md](shapes.md)). The builder only offers views that can draw the chosen data.
- **slots:** the roles fields play in the view. They're filled in automatically from field kinds (`default_mapping`), and the user can change them in the builder or in the view's own controls.
- A view **never** touches an engine, a store or the database. Everything goes through `host`.

## What the host offers a view

| Area | API |
|---|---|
| Data | `host.data` (latest TableData), `host.mapping_for(slot)`, `host.presentation`, `host.noun` |
| Changing the view | `host.set_mapping(slot, field_key)`, `host.set_presentation(key, value)`, `host.set_query(sort=…, search=…, filter=…)` (changes the question; the engine runs it) |
| Selection | `host.selected_id`, `host.select(id)`. Page-wide unless unlinked; `host.is_linked`, `host.set_linked(bool)` |
| Records | `host.open_record(id)` (selects, then shows a form: the module's form block, or a small linked window), `host.show_record_menu(id, global_pos)` / `host.record_menu(id)` (Open form, module actions, Duplicate, Delete) |
| Writes | `host.update_record(id, values)`, `host.set_value(id, key, value)`, `host.create_record(values, title)`, `host.duplicate_record(id)`, `host.move_record(id, before_id)`, `host.delete_records(ids)` (asks first), `host.add_property(title, kind, options)`, `host.remove_property(key)` |
| Other requests | `host.request(name, payload, on_reply)`: any request of the source engine (`read_text`, `write_text`, `links` …); `on_reply(payload, error)` on the UI thread. Declare it in `ViewType.requests` |
| UI | `host.view_menu_button()` (put it in your top row: filter, follow the page's selection, Edit view…), `host.report(text, is_error)` |

The host turns writes into engine requests. Failures come back as `report()` messages (the module's status line), and every view refreshes when the engine announces the change.

## View definitions

A view is saved as its definition (the block state, or the Data view module's setting):

```json
{"version": 1, "view_type": "board", "title": "Blocked work",
 "source": {"engine": "records", "collection": "projects"},
 "query": {"filter": {"all": [{"field": "status", "op": "eq", "value": "Blocked"}]}},
 "mapping": {"group": "priority"}, "presentation": {},
 "channels": {"selection": "linked"}}
```

`make_definition()` builds one; `upgrade_definition(state, default)` reads any saved state. That includes the record blocks' settings from before Phase 2 (`group_key`, `start_key`, `sort_key`, `day_px` …), so existing pages keep their views.

## Channels

- Each page (`CanvasPage`, `InfiniteCanvasPage`) has a `channel_hub`. `hub_for(widget)` finds it by walking up the parents.
- Selection is per source (`selection_channel(source)`), so selecting a project never clears the selected agent.
- **Page-wide by default; any view can unlink itself** from the view menu.
- Module code that needs "the selected record" uses `RecordFocus` (`app/modules/source_module.py`).

## Views shipped by engines

An engine package can bring view types: its `views` module calls `register_view_type(ViewType(…, engine="<id>"))`, either on import or in `register_views()`. They exist while the engine is attached (`app/views/engine_views.py` keeps the registry in line on every lookup). When the engine is detached, hosts using them show a placeholder and keep their definitions. A detached **source** engine does the same: the host shows "<Engine> isn't attached – attach it in Settings → Engines. This view is kept…", and the view comes back on `runtime.attached`.

## Source contract (engines that serve views)

An engine whose data views can show answers `<engine>.query` with `{**source params, "query": Query v1}` and returns a Table shape, and emits `<engine>.changed` with the source params of what changed. Editable sources also answer `update`, `create`, `duplicate`, `delete`, `move`, `add_property` and `remove_property`. Sources are listed through `<engine>.collections` and `<engine>.schema`. Read-only fields carry `"read_only": true` in the schema (forms show them, never edit them). The builder lists the collections of every attached engine that follows this contract (`engines.runtime.sources()`): today the records engine, the Knowledge engine (`library` and one table per kind; `create` makes a note, `update` renames) and the example `sample_data` engine.

**Library views.** `document` (Markdown editor with live preview and autosave; text files read-only; `[[Note]]` links select that note) and `links` (notes as a graph) need `read_text` / `write_text` and `links`: Knowledge answers them. `SourceModule` (`app/modules/source_module.py`) is a module made of such views over one source: Notes and Library are built on it, like the record modules.
