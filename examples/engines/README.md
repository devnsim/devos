# Building an engine

An engine is a folder you can build and test on its own, then dock into DevOS without changing DevOS. `sample_data/` is a complete, working example. Design background: [docs/architecture/engine-plan.md](../../docs/architecture/engine-plan.md).

## The package

```
my_engine/
├── engine.json     what it is: id, title, version, entry ("engine:MyEngine"), requires / uses, views, kernel_api
├── engine.py       the engine: no Qt, no imports from app/ui, app/modules, app/views or other engines
└── views.py        optional: view types it brings (Qt allowed here, through app.views.api only)
```

`engine.json`:

```json
{"id": "my_engine", "title": "My engine", "version": "1.0", "entry": "engine:MyEngine",
 "requires": [], "uses": [], "views": "views", "kernel_api": 1}
```

- **requires:** engines that must be attached first. Attaching fails without them, and they can't be detached while yours is attached.
- **uses:** engines it works better with.

## The engine

```python
from engines.kernel import Engine, EngineError, EngineManifest, Job, Reply, RequestSpec, shapes

class MyEngine(Engine):
    MANIFEST = EngineManifest("my_engine", "My engine", "1.0", "What it does",
        requests=(RequestSpec("collections", "Its tables", example={}),
                  RequestSpec("query", "Rows", params=("collection",), optional=("query",), shape="table",
                              example={"collection": "things"})),
        shapes=("table",))

    def start(self): ...                   # open stores here: it runs on the engine's own thread
    def on_collections(self, payload, job): return {"collections": [...]}
    def on_query(self, payload, job): return Reply(*shapes.table(fields, rows))
```

- One `on_<request>(payload, job)` handler per declared request. Return a dict, or a `Reply` with binary attachments. A streaming request's handler **yields** them; check `job.is_cancelled()` in long loops.
- Requests are handled one at a time, in order (the engine is an actor), so the engine never needs locks.
- `self.emit(event, payload)` announces something; `self.log(level, text)` writes to the System Log.
- Fail with `EngineError("what went wrong and what to do", code="…")`.
- Give read-only requests an `example`, so the conformance kit can call them.
- To be a source views can show, answer `collections`, `schema` and `query` (Table shape) and emit `changed` when data changes. See [docs/views-api.md](../../docs/views-api.md).

## Views it ships

```python
from app.views.api import MappingSlot, ViewType, register_view_type

def register_views():
    register_view_type(ViewType("my_view", "My view", "…", ("table",), MyView, engine="my_engine",
                                slots=(MappingSlot("group", "Group by", ("select",), required=True),)))
```

They appear while the engine is attached. When it's detached, views using them show a placeholder and keep their settings.

## Check, then dock

```sh
python -m engines.kernel.conformance examples/engines/sample_data      # runs against a temporary data folder
```

Then **Settings → Engines → Add engine package…**, select the folder, and press **Attach**. Everything it offers (sources in the view builder, its view types) appears. **Detach** switches it off without deleting its data.
