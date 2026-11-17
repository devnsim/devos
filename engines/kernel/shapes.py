# ==============================================================================
# FILE: engines/kernel/shapes.py
# OVERVIEW: Data shapes v1 - the common data format on every connection (sources -> engines -> views): Table,
#           Series, Geo, Tensor, Image, PointCloud, Geometry, Graph, Document, Thread. Builders, validation and
#           the Arrow encoding for big data. The full contract is docs/shapes.md.
# PURPOSE:
#   1. Field: a typed column (key, title, kind, options, unit) shared by Table, Geo, Graph and PointCloud.
#   2. One builder per shape returning (payload, attachments) ready for a Message; small data stays JSON,
#      big columns travel as Apache Arrow IPC bytes (attachments), arrays as arrays.
#   3. validate(payload, attachments) -> problems, so engines, views and the conformance kit agree on what a
#      shape must contain. table_rows() / arrow_columns() read a shape back whatever its encoding.
# CONTRACT:
#   - Requires: standard library; pyarrow and numpy only when Arrow / array data is built or read.
#   - Exposes: SHAPES, SHAPE_VERSION, FIELD_KINDS, Field, table(), series(), geo(), tensor(), image(),
#              point_cloud(), geometry(), graph(), document(), thread(), validate(), table_rows(),
#              encode_arrow(), decode_arrow(), ARROW_ROWS_THRESHOLD
# NOTES:
#   - [2026-11-17] Created in Phase 2 of the engine plan (ROADMAP.md).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - shape payload (dict): {"shape": name, "version": SHAPE_VERSION, ...shape keys}; binary parts are named
#   attachments of the same Message.
# - ARROW_ROWS_THRESHOLD (int): A Table / Geo with more rows than this sends them as an Arrow attachment
#   ("rows") instead of JSON (default: 2000).
# - CRS (str): Coordinate reference system of Geo / PointCloud data ("EPSG:4326" = longitude / latitude).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SHAPES
#    1.1 imports_and_fields
#    1.2 arrow
#    1.3 table_series_geo
#    1.4 arrays
#    1.5 graph_document_thread
#    1.6 validate
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_fields
# ==============================================================================
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SHAPE_VERSION = 1
SHAPES = ("table", "series", "geo", "tensor", "image", "point_cloud", "geometry", "graph", "document", "thread")
FIELD_KINDS = ("text", "long_text", "number", "integer", "select", "multi_select", "date", "datetime", "checkbox",
               "url", "geometry", "json")
ARROW_ROWS_THRESHOLD = 2000


@dataclass(frozen=True)
class Field:
    key: str
    title: str
    kind: str = "text"
    options: tuple[str, ...] = ()
    unit: str = ""

    def to_payload(self) -> dict:
        return {"key": self.key, "title": self.title, "kind": self.kind, "options": list(self.options), "unit": self.unit}

    @staticmethod
    def from_payload(data: dict) -> "Field":
        return Field(str(data["key"]), str(data.get("title") or data["key"]), str(data.get("kind", "text")),
                     tuple(data.get("options") or ()), str(data.get("unit", "")))


def _fields(fields) -> list[dict]:
    """Field payloads; extra keys an engine adds (e.g. "is_user") are kept."""
    return [field.to_payload() if isinstance(field, Field) else {**field, **Field.from_payload(field).to_payload()}
            for field in fields]


def _head(shape: str, **values) -> dict:
    return {"shape": shape, "version": SHAPE_VERSION, **values}
# --- END [1.1] ---


# ==============================================================================
# [1.2] arrow
# ==============================================================================
def encode_arrow(columns: dict[str, Any]) -> bytes:
    """Columns (lists or 1-D arrays) as Arrow IPC stream bytes: language-neutral, cheap to pass between
    processes, readable without copying."""
    import pyarrow as pa
    table = pa.table({name: pa.array(values) for name, values in columns.items()})
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue().to_pybytes()


def decode_arrow(data: bytes, as_numpy: bool = False) -> dict[str, Any]:
    """Arrow IPC bytes back to columns (Python lists, or NumPy arrays where the type allows)."""
    import pyarrow as pa
    table = pa.ipc.open_stream(pa.py_buffer(data)).read_all()
    if as_numpy:
        return {name: table.column(name).to_numpy(zero_copy_only=False) for name in table.column_names}
    return {name: table.column(name).to_pylist() for name in table.column_names}


def _rows_part(rows: list[dict], keys: list[str]) -> tuple[dict, dict]:
    """Rows as JSON, or as an Arrow attachment above ARROW_ROWS_THRESHOLD (values JSON-encoded per cell when
    a column mixes types, so any row survives)."""
    if len(rows) <= ARROW_ROWS_THRESHOLD:
        return {"rows": rows, "encoding": "json"}, {}
    import json
    columns, encoded = {}, []
    for key in keys:
        values = [row.get(key) for row in rows]
        kinds = {type(value) for value in values if value is not None}
        if kinds and kinds <= {int, float} and len(kinds) == 2:
            columns[key] = [None if value is None else float(value) for value in values]
        elif len(kinds) > 1 or kinds & {list, dict}:                   # mixed or nested: JSON text per cell
            columns[key] = [None if value is None else json.dumps(value) for value in values]
            encoded.append(key)
        else:
            columns[key] = values
    return {"encoding": "arrow", "json_columns": encoded}, {"rows": encode_arrow(columns)}


def table_rows(payload: dict, attachments: dict | None = None) -> list[dict]:
    """The rows of a Table / Geo shape, whatever its encoding."""
    if payload.get("encoding", "json") == "json":
        return payload.get("rows", [])
    import json
    columns = decode_arrow((attachments or {})["rows"])
    for key in payload.get("json_columns", []):
        columns[key] = [None if value is None else json.loads(value) for value in columns[key]]
    keys = list(columns)
    count = len(columns[keys[0]]) if keys else 0
    return [{key: columns[key][index] for key in keys} for index in range(count)]
# --- END [1.2] ---


# ==============================================================================
# [1.3] table_series_geo
# ==============================================================================
def table(fields, rows: list[dict], total: int | None = None, offset: int = 0, key: str = "id",
          title_key: str = "title", source: dict | None = None) -> tuple[dict, dict]:
    """Rows of typed fields. Every row has `key` (stable id); `total` = rows matching before paging."""
    field_payloads = _fields(fields)
    keys = [key] + ([title_key] if title_key and title_key != key else []) + \
        [field["key"] for field in field_payloads if field["key"] not in (key, title_key)]
    rows_part, attachments = _rows_part(rows, keys)
    return _head("table", fields=field_payloads, key=key, title_key=title_key, offset=offset,
                 total=len(rows) + offset if total is None else total, source=source or {}, **rows_part), attachments


def series(channels, times, values: dict[str, Any], time_unit: str = "s") -> tuple[dict, dict]:
    """Values over time: `times` (seconds since the epoch, or since the start for relative series) and one
    column per channel. Sent as Arrow (times + channels), so a 10 kHz sensor costs no JSON."""
    channel_payloads = _fields(channels)
    columns = {"t": list(times) if not hasattr(times, "tolist") else times.tolist()}
    for channel in channel_payloads:
        column = values[channel["key"]]
        columns[channel["key"]] = column.tolist() if hasattr(column, "tolist") else list(column)
    return _head("series", channels=channel_payloads, count=len(columns["t"]), time_unit=time_unit), \
        {"data": encode_arrow(columns)}


def geo(fields, features: list[dict], crs: str = "EPSG:4326", bbox: list[float] | None = None,
        geometry_type: str = "", source: dict | None = None) -> tuple[dict, dict]:
    """Features with a location or outline: rows with "id", "geometry" (GeoJSON geometry) and field values.
    Rasters are an Image with a georeference (geo_transform + crs) - see image()."""
    field_payloads = _fields(fields)
    keys = ["id", "geometry"] + [field["key"] for field in field_payloads]
    rows_part, attachments = _rows_part(features, keys)
    return _head("geo", fields=field_payloads, crs=crs, bbox=bbox or [], geometry_type=geometry_type,
                 total=len(features), source=source or {}, **rows_part), attachments
# --- END [1.3] ---


# ==============================================================================
# [1.4] arrays
# ==============================================================================
def tensor(array, axes: list[str], coordinates: dict[str, list] | None = None, unit: str = "") -> tuple[dict, dict]:
    """An n-dimensional array with named axes (Tensor4D, RF fields, simulation grids)."""
    if len(axes) != array.ndim:
        raise ValueError(f"{array.ndim}-D array needs {array.ndim} axis names, got {len(axes)}")
    return _head("tensor", axes=list(axes), dims=list(array.shape), dtype=str(array.dtype), unit=unit,
                 coordinates=coordinates or {}), {"data": array}


def image(array=None, encoded: bytes | None = None, format: str = "", geo_transform: list[float] | None = None,
          crs: str = "") -> tuple[dict, dict]:
    """A raster: a height x width (x bands) array, or encoded bytes (format "png" / "jpeg"). With
    geo_transform + crs it is a georeferenced raster (Spatial)."""
    if (array is None) == (encoded is None):
        raise ValueError("give either an array or encoded bytes")
    if array is not None:
        height, width = array.shape[:2]
        bands = 1 if array.ndim == 2 else array.shape[2]
        payload = _head("image", width=width, height=height, bands=bands, dtype=str(array.dtype), format="array")
        attachment = array
    else:
        payload = _head("image", format=format, width=0, height=0, bands=0, dtype="")
        attachment = encoded
    if geo_transform:
        payload |= {"geo_transform": list(geo_transform), "crs": crs}
    return payload, {"data": attachment}


def point_cloud(columns: dict[str, Any], crs: str = "", unit: str = "m") -> tuple[dict, dict]:
    """Points in 3D: columns x, y, z plus any attributes (intensity, classification, rgb ...), as Arrow."""
    missing = [axis for axis in ("x", "y", "z") if axis not in columns]
    if missing:
        raise ValueError(f"a point cloud needs columns x, y, z (missing {', '.join(missing)})")
    plain = {name: values.tolist() if hasattr(values, "tolist") else list(values) for name, values in columns.items()}
    return _head("point_cloud", count=len(plain["x"]), attributes=[name for name in plain if name not in "xyz"],
                 crs=crs, unit=unit), {"points": encode_arrow(plain)}


def geometry(vertices, faces, unit: str = "mm", name: str = "") -> tuple[dict, dict]:
    """A triangle mesh: vertices (n x 3 floats) and faces (m x 3 vertex indices)."""
    return _head("geometry", name=name, unit=unit, vertex_count=int(vertices.shape[0]), face_count=int(faces.shape[0])), \
        {"vertices": vertices, "faces": faces}
# --- END [1.4] ---


# ==============================================================================
# [1.5] graph_document_thread
# ==============================================================================
def graph(node_fields, nodes: list[dict], edge_fields, edges: list[dict], directed: bool = True) -> tuple[dict, dict]:
    """Nodes (rows with "id") and edges (rows with "id", "source", "target")."""
    return _head("graph", node_fields=_fields(node_fields), nodes=nodes, edge_fields=_fields(edge_fields), edges=edges,
                 directed=directed), {}


def document(text: str, format: str = "markdown", title: str = "", links: list[dict] | None = None) -> tuple[dict, dict]:
    """Text with structure; links = [{"target", "label"}] (note links, citations)."""
    return _head("document", title=title, format=format, text=text, links=links or []), {}


def thread(messages: list[dict], title: str = "") -> tuple[dict, dict]:
    """Messages over time: [{"id", "role", "author", "text", "time"}] (chats, mail)."""
    return _head("thread", title=title, messages=messages), {}
# --- END [1.5] ---


# ==============================================================================
# [1.6] validate
# ==============================================================================
_REQUIRED = {
    "table": ("fields", "key", "total"), "series": ("channels", "count"), "geo": ("fields", "crs"),
    "tensor": ("axes", "dims", "dtype"), "image": ("format",), "point_cloud": ("count", "attributes"),
    "geometry": ("vertex_count", "face_count"), "graph": ("nodes", "edges"), "document": ("text", "format"),
    "thread": ("messages",),
}
_ATTACHED = {"series": ("data",), "tensor": ("data",), "image": ("data",), "point_cloud": ("points",),
             "geometry": ("vertices", "faces")}


def validate(payload: dict, attachments: dict | None = None) -> list[str]:
    """What is wrong with a shape payload ([] = valid v1 shape)."""
    attachments = attachments or {}
    shape = payload.get("shape")
    if shape not in SHAPES:
        return [f"unknown shape “{shape}”"]
    problems = []
    if payload.get("version") != SHAPE_VERSION:
        problems.append(f"{shape}: version {payload.get('version')} (this kernel reads {SHAPE_VERSION})")
    problems += [f"{shape}: missing “{key}”" for key in _REQUIRED[shape] if key not in payload]
    problems += [f"{shape}: missing attachment “{name}”" for name in _ATTACHED.get(shape, ()) if name not in attachments]
    for key in ("fields", "channels", "node_fields", "edge_fields"):
        for field in payload.get(key, []):
            if field.get("kind") not in FIELD_KINDS:
                problems.append(f"{shape}: field “{field.get('key')}” has unknown kind “{field.get('kind')}”")
    if shape in ("table", "geo"):
        if payload.get("encoding", "json") == "json" and "rows" not in payload:
            problems.append(f"{shape}: missing “rows”")
        if payload.get("encoding") == "arrow" and "rows" not in attachments:
            problems.append(f"{shape}: Arrow encoding without a “rows” attachment")
    return problems
# --- END [1.6] ---
