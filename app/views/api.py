# ==============================================================================
# FILE: app/views/api.py
# OVERVIEW: View type API v1 - how a view plugs into DevOS. Built-in views, views shipped with an engine and
#           (Phase 12) user-written views all use exactly this. The full contract is docs/views-api.md.
# PURPOSE:
#   1. ViewType: id, title, the shapes it draws, its mapping slots (which field plays which role), the engine
#      that ships it, and create(host) -> QWidget. register_view_type() / view_type() / view_types(shape) /
#      unregister_engine_views(engine_id).
#   2. View definitions: the saved recipe of a view (make_definition, upgrade_definition - which also turns
#      the record blocks' old saved settings into definitions, so existing pages keep their views).
#   3. TableData: a Table shape decoded for views (fields, rows, options, lookups); default_mapping() fills
#      slots from field kinds.
#   4. Small helpers every view uses: tag_color(), field_text(), parse_date(), record drag and drop.
# CONTRACT:
#   - Requires: engines.kernel.shapes (table_rows), app.data.store.records (display_text, parse_date),
#               app.ui.theme (TAG_COLORS), PySide6
#   - Exposes: API_VERSION, DEFINITION_VERSION, MappingSlot, ViewType, register_view_type(), view_type(),
#              view_types(), TableData, default_mapping(), make_definition(), upgrade_definition(), source_key(),
#              tag_color(), field_text(), parse_date(), RECORD_MIME, start_record_drag(), dragged_record_id()
# NOTES:
#   - [2026-11-17] field_text(): byte sizes and date-times read naturally (Library tables).
#   - [2026-11-17] Phase 3: ViewType.engine; views shipped by engines load with them and leave when they detach.
#   - [2026-11-17] Created in Phase 2 of the engine plan (helpers moved here from app/ui/record_views.py).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - view type: A kind of view (table, board, timeline, calendar, form, chart, map ...).
# - mapping slot: A role a field plays in a view ("group" of a board, "start" of a timeline); filled
#   automatically from field kinds, changeable by the user.
# - view definition (dict): {version, view_type, title, source {engine, ...}, query, mapping, presentation,
#   channels {selection: "linked" | "unlinked"}}.
# - source (dict): Where the data comes from: {"engine": "records", "collection": "projects"}.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. API
#    1.1 imports
#    1.2 view_types
#    1.3 table_data
#    1.4 definitions
#    1.5 helpers
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QByteArray, QMimeData, Qt
from PySide6.QtGui import QColor, QDrag
from PySide6.QtWidgets import QWidget

from app.data.content_kinds import size_text
from app.data.store.records import PropertySpec, display_text
from app.data.store.records import parse_date as _parse_date
from app.ui.theme import TAG_COLORS
from engines.kernel.shapes import table_rows

API_VERSION = 1
DEFINITION_VERSION = 1
# --- END [1.1] ---


# ==============================================================================
# [1.2] view_types
# ==============================================================================
@dataclass(frozen=True)
class MappingSlot:
    key: str
    title: str
    kinds: tuple[str, ...]
    required: bool = False
    description: str = ""


@dataclass(frozen=True)
class ViewType:
    type_id: str
    title: str
    description: str
    shapes: tuple[str, ...]
    create: Callable[["QWidget"], QWidget]           # create(host) -> the view widget
    slots: tuple[MappingSlot, ...] = ()
    api_version: int = API_VERSION
    engine: str = ""                                 # the engine that ships this view ("" = built into DevOS)
    requests: tuple[str, ...] = ()                   # requests the source engine must answer ("read_text" ...)


_view_types: dict[str, ViewType] = {}


def register_view_type(view_type: ViewType) -> None:
    if view_type.api_version > API_VERSION:
        raise ValueError(f"View type {view_type.type_id} needs view API {view_type.api_version}; this DevOS has {API_VERSION}")
    _view_types[view_type.type_id] = view_type


def unregister_engine_views(engine_id: str) -> list[str]:
    """Remove the view types an engine shipped (it was detached); returns their ids."""
    gone = [type_id for type_id, item in _view_types.items() if item.engine == engine_id]
    for type_id in gone:
        del _view_types[type_id]
    return gone


def _load_all() -> None:
    from app.views import types  # noqa: F401  (registers the built-in view types on first use)
    from app.views.engine_views import ensure_engine_views
    ensure_engine_views()                            # views shipped by attached engines


def view_type(type_id: str) -> ViewType | None:
    _load_all()
    return _view_types.get(type_id)


def view_types(shape: str | None = None) -> list[ViewType]:
    _load_all()
    return [item for item in _view_types.values() if shape is None or shape in item.shapes]
# --- END [1.2] ---


# ==============================================================================
# [1.3] table_data
# ==============================================================================
class TableData:
    """A Table shape as views use it."""

    def __init__(self, payload: dict, attachments: dict | None = None) -> None:
        self.payload = payload
        self.fields: list[dict] = payload.get("fields", [])
        self.rows: list[dict] = table_rows(payload, attachments)
        self.total: int = payload.get("total", len(self.rows))
        self.key: str = payload.get("key", "id")
        self.title_key: str = payload.get("title_key", "title")
        self.source: dict = payload.get("source", {})
        self._by_id = {row.get(self.key): row for row in self.rows}

    @property
    def noun(self) -> str:
        return self.source.get("noun", "record")

    def field(self, key: str) -> dict | None:
        return next((field for field in self.fields if field["key"] == key), None)

    def fields_of(self, *kinds: str) -> list[dict]:
        return [field for field in self.fields if field["kind"] in kinds]

    def row(self, row_id) -> dict | None:
        return self._by_id.get(row_id)

    def title(self, row: dict) -> str:
        return str(row.get(self.title_key) or "")

    def options(self, key: str) -> list[str]:
        field = self.field(key)
        return list(field.get("options", [])) if field else []


def default_mapping(view: ViewType, fields: list[dict], mapping: dict | None = None) -> dict:
    """Every slot filled: the user's choice when it still fits, else the first unused field of a fitting kind."""
    chosen, used = {}, set()
    mapping = mapping or {}
    by_key = {field["key"]: field for field in fields}
    for slot in view.slots:
        current = mapping.get(slot.key)
        if current in by_key and by_key[current]["kind"] in slot.kinds:
            chosen[slot.key] = current
            used.add(current)
    for slot in view.slots:
        if slot.key in chosen:
            continue
        candidates = [field["key"] for field in fields if field["kind"] in slot.kinds]
        fresh = [key for key in candidates if key not in used]
        chosen[slot.key] = (fresh or candidates or [None])[0]
        if chosen[slot.key]:
            used.add(chosen[slot.key])
    return chosen
# --- END [1.3] ---


# ==============================================================================
# [1.4] definitions
# ==============================================================================
def make_definition(type_id: str, source: dict, query: dict | None = None, mapping: dict | None = None,
                    presentation: dict | None = None, title: str = "", linked: bool = True) -> dict:
    return {"version": DEFINITION_VERSION, "view_type": type_id, "title": title, "source": dict(source),
            "query": dict(query or {}), "mapping": dict(mapping or {}), "presentation": dict(presentation or {}),
            "channels": {"selection": "linked" if linked else "unlinked"}}


_LEGACY = {"group_key": ("mapping", "group"), "start_key": ("mapping", "start"), "end_key": ("mapping", "end"),
           "color_key": ("mapping", "color"), "date_key": ("mapping", "date"), "day_px": ("presentation", "day_px")}


def upgrade_definition(state: dict | None, default: dict) -> dict:
    """A saved block state as a current definition. States saved before Phase 2 (group_key, start_key,
    sort_key ...) keep their meaning; anything unreadable falls back to `default`."""
    definition = copy.deepcopy(default)
    if not state:
        return definition
    if "view_type" in state:
        merged = {**definition, **copy.deepcopy(state)}
        for part in ("query", "mapping", "presentation", "channels", "source"):
            merged[part] = dict(state.get(part) or definition.get(part) or {})
        merged["version"] = DEFINITION_VERSION
        return merged
    for old_key, (part, new_key) in _LEGACY.items():
        if state.get(old_key) is not None:
            definition[part][new_key] = state[old_key]
    if state.get("sort_key"):
        definition["query"]["sort"] = [{"field": state["sort_key"], "descending": bool(state.get("descending"))}]
    return definition


def source_key(source: dict) -> str:
    return json.dumps(source, sort_keys=True, separators=(",", ":"))
# --- END [1.4] ---


# ==============================================================================
# [1.5] helpers
# ==============================================================================
RECORD_MIME = "application/x-devos-record"


def tag_color(option: str) -> QColor:
    """Stable colour for a select option (same option, same colour, in every view)."""
    return QColor(TAG_COLORS[sum(map(ord, option or "")) % len(TAG_COLORS)])


def field_text(field: dict, value) -> str:
    """A value as text for its field: sizes in bytes as "12.3 MB", date-times without seconds."""
    if value is not None and field.get("unit") == "bytes":
        return size_text(int(value))
    if value and field.get("kind") == "datetime":
        return str(value).replace("T", " ")[:16]
    return display_text(PropertySpec(field["key"], field.get("title", field["key"]), field.get("kind", "text")), value)


def parse_date(value):
    return _parse_date(value)


def start_record_drag(source: QWidget, record_id, label: str) -> None:
    mime = QMimeData()
    mime.setData(RECORD_MIME, QByteArray(json.dumps(record_id).encode()))
    mime.setText(label)
    drag = QDrag(source)
    drag.setMimeData(mime)
    drag.exec(Qt.DropAction.MoveAction)


def dragged_record_id(event):
    if not event.mimeData().hasFormat(RECORD_MIME):
        return None
    return json.loads(bytes(event.mimeData().data(RECORD_MIME)).decode())
# --- END [1.5] ---
