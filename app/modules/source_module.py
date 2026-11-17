# ==============================================================================
# FILE: app/modules/source_module.py
# OVERVIEW: SourceModule - a module made of views over one engine source (a toolbar over a canvas of
#           ViewHosts), and RecordFocus, "the selected record" of that source on the page. Records modules
#           (Projects, Agents) and Library modules (Notes, Library) are built on it; Phase 5 turns such
#           compositions into templates.
# PURPOSE:
#   1. Subclasses declare SOURCE, NOUN, VIEWS (view type ids offered as blocks), DEFAULT_LAYOUT; hooks:
#      before_views() (e.g. register a collection), toolbar_start(row) / add_toolbar_actions(row),
#      extra_block_specs(), record_actions(), export_actions(), block_definition(type_id).
#   2. Toolbar: the module's own buttons, New view… (the builder), ⋯ (export actions), count, status line.
#   3. RecordFocus follows the page's selection channel for the source and fetches the record from the
#      engine ("<engine>.get"); the selection is remembered in the module's settings.
# CONTRACT:
#   - Requires: app.modules.base.ModuleView, app.views (api, host, channels, subscriptions, builder, types),
#               app.ui.engine_bridge, app.data.store.records (Record, TITLE_KEY), app.ui.ui_tools,
#               QSS selector #source_module_panel
#   - Exposes: SourceModule(parent=None, context=None) with source, focus, block_canvas, make_host(),
#              module_status_label, count_label; RecordFocus(module, source); record_from_row(row)
# NOTES:
#   - [2026-11-17] Created in Phase 4 of the engine plan from RecordsModule's generic half (RecordFocus moved
#                  here; it matches change events by the source's keys, so Library sources work too).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - SELECTED_SETTING (str): Module setting holding the last selected record id ("selected_id").
# - source (dict): {"engine": ..., "collection": ...} - where the views' data lives.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SOURCE_MODULE
#    1.1 imports
#    1.2 record_focus
#    1.3 source_module
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from app.data.store.records import TITLE_KEY, Record
from app.modules.base import ModuleView
from app.ui.canvas.block_canvas import BlockSpec
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.engine_bridge import call_engine, watch_engine
from app.ui.ui_tools import set_status, status_label, tool_button
from app.views.api import make_definition, view_type
from app.views.channels import hub_for, selection_channel
from app.views.host import ViewHost
from app.views.subscriptions import shared_queries
# --- END [1.1] ---


# ==============================================================================
# [1.2] record_focus
# ==============================================================================
def record_from_row(row: dict) -> Record:
    meta = ("id", TITLE_KEY, "created_at", "updated_at")
    return Record(row["id"], row.get(TITLE_KEY, ""), {key: value for key, value in row.items() if key not in meta},
                  0.0, row.get("created_at", 0.0), row.get("updated_at", 0.0))


class RecordFocus(QObject):
    """The selected record of one collection on this page, kept fresh from the engine."""
    selection_changed = Signal()                     # another record (or none) is selected; selected() is ready
    records_changed = Signal()                       # the collection changed (the selected record maybe too)
    status = Signal(str, bool)

    def __init__(self, module: QWidget, source: dict) -> None:
        super().__init__(module)
        self.module, self.source = module, source
        self.channel = selection_channel(source)
        self.selected_id = None
        self._record: Record | None = None
        self._hub = None
        watch_engine(self, f"{source['engine']}.changed", self._on_changed)

    def bind(self) -> None:
        hub = hub_for(self.module)
        if hub is not self._hub:
            if self._hub is not None:
                self._hub.changed.disconnect(self._on_channel)
            self._hub = hub
            hub.changed.connect(self._on_channel)
            self._on_channel(self.channel, hub.get(self.channel))

    def selected(self) -> Record | None:
        return self._record if self._record is not None and self._record.record_id == self.selected_id else None

    def select(self, record_id) -> None:
        self.bind()
        self._hub.set(self.channel, None if record_id is None else {"id": record_id})

    def update(self, record_id, values: dict) -> None:
        call_engine(self, f"{self.source['engine']}.update", {"collection": self.source["collection"], "id": record_id,
                                                              "values": values}, self._answered)

    def set_value(self, record_id, key: str, value) -> None:
        self.update(record_id, {key: value})

    def _answered(self, _reply, error) -> None:
        if error is not None:
            self.status.emit(str(error), True)

    def _on_channel(self, name: str, value) -> None:
        if name != self.channel:
            return
        record_id = value.get("id") if isinstance(value, dict) else None
        if record_id != self.selected_id:
            self.selected_id = record_id
            self._fetch(self.selection_changed)

    def _on_changed(self, message) -> None:
        params = {key: value for key, value in self.source.items() if key != "engine"}
        if all(message.payload.get(key, value) == value for key, value in params.items()):
            ids = message.payload.get("ids")
            if self.selected_id is not None and (ids is None or self.selected_id in ids):
                self._fetch(self.records_changed)
            else:
                self.records_changed.emit()

    def _fetch(self, signal) -> None:
        if self.selected_id is None:
            self._record = None
            signal.emit()
            return
        wanted = self.selected_id

        def answered(reply, error) -> None:
            if wanted != self.selected_id:
                return
            self._record = record_from_row(reply["record"]) if error is None else None
            signal.emit()
        call_engine(self, f"{self.source['engine']}.get", {"collection": self.source["collection"], "id": wanted}, answered)
# --- END [1.2] ---


# ==============================================================================
# [1.3] source_module
# ==============================================================================
class SourceModule(ModuleView):
    SOURCE: dict = {}
    NOUN = "item"
    VIEWS: tuple[str, ...] = ()
    DEFAULT_LAYOUT: dict | None = None
    SELECTED_SETTING = "selected_id"

    def __init__(self, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, context, "source_module_panel")
        self.source = dict(self.SOURCE)
        self.before_views()
        self.focus = RecordFocus(self, self.source)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        row = QHBoxLayout()
        self.toolbar_start(row)
        self.add_toolbar_actions(row)
        view_btn = QPushButton("New view…", toolTip=f"Make another view of the {self.NOUN}s (type, fields, filter)")
        view_btn.clicked.connect(self.new_view)
        row.addWidget(view_btn)
        if self.export_actions():
            export_btn = tool_button("module_settings_btn", text="⋯", tool_tip="Export")
            export_btn.clicked.connect(lambda: self._export_menu().exec(export_btn.mapToGlobal(export_btn.rect().bottomLeft())))
            row.addWidget(export_btn)
        self.count_label = QLabel(objectName="control_label")
        row.addWidget(self.count_label)
        self.module_status_label = status_label()
        row.addWidget(self.module_status_label, 1)
        reserve_controls_space(row)
        layout.addLayout(row)
        self.block_canvas = self.build_block_canvas(self._block_specs(), self.DEFAULT_LAYOUT, spec_factory=self._view_spec)
        layout.addWidget(self.block_canvas, 1)
        self.focus.status.connect(lambda text, is_error: set_status(self.module_status_label, text, is_error))
        self.focus.selection_changed.connect(lambda: self.save_setting(self.SELECTED_SETTING, self.focus.selected_id))
        shared_queries().subscribe(self, self.source, {"limit": 0})        # just the count

    def _block_specs(self) -> list[BlockSpec]:
        specs = []
        for type_id in self.VIEWS:
            kind = view_type(type_id)
            if kind is not None:
                specs.append(BlockSpec(type_id, kind.title, kind.description,
                                       lambda type_id=type_id: self.make_host(self.block_definition(type_id))))
        return specs + self.extra_block_specs()

    def _view_spec(self, type_id: str) -> BlockSpec:
        """Any view type (even one not available now: its host shows a placeholder until its engine is back)."""
        kind = view_type(type_id)
        return BlockSpec(type_id, kind.title if kind else type_id, kind.description if kind else "",
                         lambda: self.make_host(self.block_definition(type_id)))

    def block_definition(self, type_id: str) -> dict:
        """Hook: the definition a new block of this view type starts from."""
        return make_definition(type_id, self.source)

    def make_host(self, definition: dict) -> ViewHost:
        host = ViewHost(definition, record_actions=self.record_actions())
        host.status.connect(lambda text, is_error: set_status(self.module_status_label, text, is_error))
        return host

    def showEvent(self, event) -> None:                                  # noqa: N802
        super().showEvent(event)
        self.focus.bind()                                                # the page this module now lives on
        saved = self.setting(self.SELECTED_SETTING)
        if self.focus.selected_id is None and saved is not None:
            self.focus.select(saved)

    def shared_data(self, data, error) -> None:
        if data is not None:
            noun = data.noun or self.NOUN
            self.count_label.setText(f"{data.total} {noun}{'' if data.total == 1 else 's'}")

    def before_views(self) -> None:
        """Hook: runs before any view asks for data (e.g. register a collection with its engine)."""

    def toolbar_start(self, row: QHBoxLayout) -> None:
        """Hook: the first buttons of the toolbar (e.g. + New)."""

    def add_toolbar_actions(self, row: QHBoxLayout) -> None:
        """Hook: more buttons, before New view…"""

    def extra_block_specs(self) -> list[BlockSpec]:
        """Hook: the module's own blocks, offered next to its views."""
        return []

    def record_actions(self) -> list[tuple[str, object]]:
        """Hook: (menu title, handler(record_id)) added to the right-click menu of a record in every view."""
        return []

    def export_actions(self) -> list[tuple[str, object]]:
        """Hook: (menu title, handler) pairs for the ⋯ menu (no ⋯ when empty)."""
        return []

    def report(self, text: str, is_error: bool = False) -> None:
        set_status(self.module_status_label, text, is_error)

    def new_view(self) -> None:
        """The view builder, starting from this source; the result opens beside the active view."""
        from app.views.builder import ViewBuilderDialog
        dialog = ViewBuilderDialog(self, make_definition(self.VIEWS[0] if self.VIEWS else "table", self.source))
        if dialog.exec():
            definition = dialog.definition()
            self.block_canvas.add_tile(definition["view_type"], beside=self.block_canvas.active_tile, state=definition)

    def _export_menu(self) -> QMenu:
        menu = QMenu(self)
        for title, handler in self.export_actions():
            menu.addAction(title).triggered.connect(handler)
        return menu
# --- END [1.3] ---
