# Data shapes v1

The common data format on every connection in DevOS: sources → engines → views. An engine that produces a shape, and a view that draws it, never need to know about each other. Code: `engines/kernel/shapes.py` (builders, `validate()`, Arrow helpers). Design background: [architecture/engine-plan.md](architecture/engine-plan.md).

## Rules

- A shape is a **payload** `{"shape": name, "version": 1, …}` plus named binary **attachments** on the same message.
- Small data is JSON. Big data is **Apache Arrow** IPC bytes (columns), or arrays for grids and meshes. A Table or Geo with more than 2000 rows (`ARROW_ROWS_THRESHOLD`) sends its rows as the `rows` attachment, with `"encoding": "arrow"`. Use `table_rows(payload, attachments)` to read rows in either encoding.
- `validate(payload, attachments)` lists what's wrong; `[]` means a valid v1 shape. The conformance kit (Phase 3) uses it.
- Changes within v1 only add (new optional keys); anything that breaks needs version 2.

## Fields

A typed column, shared by Table, Geo, Graph and Series channels: `{"key", "title", "kind", "options", "unit"}`. Engines may add keys of their own (the Data engine adds `is_user`, `read_only`, `computed`, `formula`, `primary_key`).

Kinds: `text`, `long_text`, `number`, `integer`, `select`, `multi_select`, `date` (YYYY-MM-DD), `datetime` (ISO 8601), `checkbox`, `url`, `geometry`, `json`.

## The ten shapes

| Shape | Payload keys | Attachments | Built with |
|---|---|---|---|
| `table` | `fields`, `key` (row id field, "id"), `title_key`, `total` (rows matching before paging), `offset`, `source` (where it came from, incl. `noun`), `rows` or `encoding: "arrow"` | `rows` (Arrow, big tables) | `table(fields, rows, total, offset, key, title_key, source)` |
| `series` | `channels` (fields), `count`, `time_unit` | `data` (Arrow: `t` + one column per channel) | `series(channels, times, values)` |
| `geo` | `fields`, `crs` ("EPSG:4326"), `bbox`, `geometry_type`, `total`, `source`, rows with `id` + `geometry` (GeoJSON geometry) + values | `rows` (Arrow, big layers) | `geo(fields, features, crs, bbox)` |
| `tensor` | `axes` (names), `dims`, `dtype`, `unit`, `coordinates` | `data` (array) | `tensor(array, axes, coordinates)` |
| `image` | `format` ("array", "png", "jpeg"), `width`, `height`, `bands`, `dtype`; georeferenced rasters add `geo_transform` + `crs` | `data` (array or encoded bytes) | `image(array=…)` / `image(encoded=…, format=…)` |
| `point_cloud` | `count`, `attributes` (beyond x, y, z), `crs`, `unit` | `points` (Arrow: x, y, z + attributes) | `point_cloud(columns, crs)` |
| `geometry` | `name`, `unit`, `vertex_count`, `face_count` | `vertices` (n × 3), `faces` (m × 3) | `geometry(vertices, faces)` |
| `graph` | `node_fields`, `nodes` (rows with `id`), `edge_fields`, `edges` (rows with `id`, `source`, `target`), `directed` | — | `graph(...)` |
| `document` | `title`, `format` ("markdown", "plain"), `text`, `links` [{target, label}] | — | `document(text, format, title, links)` |
| `thread` | `title`, `messages` [{id, role, author, text, time}] | — | `thread(messages, title)` |

## Toolkits

Everyday operations live in one place per shape, so engines don't rewrite them:

- `engines/toolkit/table.py`: `run_query(rows, fields, query)` runs Query v1 in memory (filter tree, search, time range, group + aggregate, sort with empty values last, paging, field selection); `matches()`, `sort_rows()`.
- `engines/toolkit/series.py`: `window()`, `resample()` (mean / last / min / max / sum per step), `decimate()` (min / max per bucket, so peaks survive).

## Query v1

The question a view asks (`engines/kernel/query.py`): `{fields, filter, search, sort [{field, descending}], group_by, aggregates [{field, op, as}], offset, limit, time_range {field, start, end}, extent {field, bbox}}`. Every key is optional.

- **Filter tree:** `{"all": […]}`, `{"any": […]}`, `{"not": node}`, or a condition `{"field", "op", "value"}`.
- **Operators:** `eq ne lt le gt ge in not_in contains starts_with empty not_empty between`.
- **Aggregates:** `count sum avg min max`.

`validate_query()` checks a query; `query_key(source, query)` is its stable identity, which shared subscriptions use.
