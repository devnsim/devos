# ==============================================================================
# FILE: app/views/host.py
# OVERVIEW: ViewHost - the frame every view lives in (a block, a page tile, a Canvas card). It holds the
#           view's definition, fetches its data through shared subscriptions, links it to the page's channels
#           and turns the view's actions into engine requests. The view only draws and reacts.
# PURPOSE:
#   1. The host API views use (docs/views-api.md): data, request() (other requests of the source engine),
#      mapping_for(), set_mapping(), set_presentation(),
#      set_query(); selection (select(), selected_id, page-wide or unlinked); open_record() and
#      show_record_menu(); writes (update_record, set_value, create_record, delete_records, duplicate_record,
#      move_record, add_property, remove_property); report(); view_menu_button().
#   2. block_state() / restore_block_state(): the definition is the saved state (old block settings are
#      upgraded), state_changed() tells containers to save.
#   3. Empty / error / unavailable states (an engine detached: a placeholder that keeps the definition and comes
#      back on "runtime.attached"); the filter bar; a form window where no form view is at hand.
# CONTRACT:
#   - Requires: app.views (api, channels, subscriptions, filter_bar), app.ui.engine_bridge.call_engine,
#               app.ui.canvas.block_canvas (BlockCanvas, found by walking up), QSS selectors #view_host_panel,
#               #view_message_label, #view_menu_btn
#   - Exposes: ViewHost(definition, record_actions=None, parent=None) with signals state_changed, status(str, bool)
# NOTES:
#   - [2026-11-17] Phase 5a: a new row's id comes from the reply's "id" when there is one (file tables key rows by "_rowid").
#   - [2026-11-17] request(): a view can ask its source engine for more (a note's text, the link graph) - Phase 4.
#   - [2026-11-17] Phase 3: placeholders when the source engine or the view's engine is detached; back on attach.
#   - [2026-11-17] Created in Phase 2 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - record action: (menu title, handler(record_id)) a module adds to every record's right-click menu.
# - linked: The view follows the page's selection channel for its source; unlinked keeps its own selection.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. VIEW_HOST
#    1.1 imports
#    1.2 init_and_state
#    1.3 data
#    1.4 selection
#    1.5 actions_and_writes
#    1.6 menus
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import copy

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QLabel, QMenu, QMessageBox, QToolButton, QVBoxLayout, QWidget

from app.ui.engine_bridge import call_engine, watch_engine
from app.views.api import TableData, default_mapping, make_definition, upgrade_definition, view_type
from app.views.channels import hub_for, selection_channel
from app.views.filter_bar import FilterBar
from app.views.subscriptions import shared_queries
# --- END [1.1] ---


# ==============================================================================
# [1.2] init_and_state
# ==============================================================================
def _new_id(reply: dict):
    """The id of a row a write made: "id" in the reply (Data), else the record's own "id"."""
    return reply["id"] if "id" in reply else reply["record"]["id"]


class ViewHost(QWidget):
    state_changed = Signal()                         # the definition changed: containers save it
    status = Signal(str, bool)                       # message, is_error

    def __init__(self, definition: dict, record_actions: list | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="view_host_panel")
        self.definition = upgrade_definition(definition, definition)
        self.record_actions = list(record_actions or [])
        self.data: TableData | None = None
        self.view: QWidget | None = None
        self._key: str | None = None
        self._hub = None
        self._local_selection = None
        self._form_window = None
        self.host_layout = QVBoxLayout(self)
        self.host_layout.setContentsMargins(0, 0, 0, 0)
        self.host_layout.setSpacing(0)
        self.filter_bar = FilterBar(self)
        self.filter_bar.changed.connect(lambda: self.set_query(filter=self.filter_bar.filter()))
        self.filter_bar.setVisible(bool(self.definition["query"].get("filter")))
        self.host_layout.addWidget(self.filter_bar)
        self.view_message_label = QLabel(objectName="view_message_label", alignment=Qt.AlignmentFlag.AlignCenter,
                                         wordWrap=True)
        self.host_layout.addWidget(self.view_message_label, 1)
        self._build_view()
        self._subscribe()
        watch_engine(self, "runtime.*", self._on_runtime)

    def _on_runtime(self, _message) -> None:
        """An engine was attached or detached: a view type it ships may have come or gone."""
        available = view_type(self.view_type_id) is not None
        if available and self.view is None:
            self._build_view()
            self._deliver()
        elif not available and self.view is not None:
            self.view.hide()
            self.view.deleteLater()
            self.view = None
            self._build_view()                          # shows the placeholder message

    @property
    def view_type_id(self) -> str:
        return self.definition["view_type"]

    @property
    def source(self) -> dict:
        return self.definition["source"]

    @property
    def presentation(self) -> dict:
        return self.definition["presentation"]

    @property
    def noun(self) -> str:
        return self.data.noun if self.data is not None else "record"

    def _build_view(self) -> None:
        if self.view is not None:
            self.view.hide()
            self.view.deleteLater()
            self.view = None
        kind = view_type(self.view_type_id)
        if kind is None:
            self._show_message(f"This view needs the “{self.view_type_id}” view, which isn't available. If an engine "
                               "ships it, attach that engine in Settings → Engines. The view is kept.")
            return
        missing = self._missing_requests(kind)
        if missing:
            self._show_message(f"The {kind.title} view needs data that answers {', '.join(missing)}; this data doesn't. "
                               "Choose other data with Edit view….")
            return
        self.view = kind.create(self)
        self.host_layout.insertWidget(1, self.view, 1)
        self.view_message_label.hide()

    def _missing_requests(self, kind) -> list[str]:
        from engines.kernel import EngineError
        from engines.runtime import get_bus
        try:
            manifest = get_bus().manifest(self.source["engine"])
        except EngineError:
            return []                                    # engine detached: the data placeholder says so
        return [name for name in kind.requests if manifest.request(name) is None]

    def _subscribe(self) -> None:
        queries = shared_queries()
        if self._key is not None:
            queries.unsubscribe(self, self._key)
        self._key = queries.subscribe(self, self.source, self.definition["query"])

    def _changed(self) -> None:
        self.state_changed.emit()

    def block_state(self) -> dict:
        return copy.deepcopy(self.definition)

    def restore_block_state(self, state: dict) -> None:
        self.set_definition(upgrade_definition(state, self.definition))

    def set_definition(self, definition: dict) -> None:
        """Replace the whole recipe (the view builder, a restored layout)."""
        old, self.definition = self.definition, upgrade_definition(definition, definition)
        if old["view_type"] != self.definition["view_type"]:
            self._build_view()
        self.filter_bar.set_filter(self.definition["query"].get("filter"), emit=False)
        self.filter_bar.setVisible(bool(self.definition["query"].get("filter")) or self.filter_bar.isVisible())
        if old["source"] != self.definition["source"] or old["query"] != self.definition["query"]:
            self._subscribe()
        elif self.data is not None:
            self._deliver()
        self._changed()
# --- END [1.2] ---

# ==============================================================================
# [1.3] data
# ==============================================================================
    def shared_data(self, data: TableData | None, error) -> None:
        """From the shared subscription: a new answer for this view's question."""
        if error is not None:
            detached = getattr(error, "code", "") in ("not_attached", "detached")
            self._show_message(f"{error}. This view is kept and comes back when it is attached." if detached else
                               f"Couldn't load this view: {error}")
            return
        keys_changed = self.data is None or [f["key"] for f in data.fields] != [f["key"] for f in self.data.fields]
        self.data = data
        if keys_changed:
            self.filter_bar.set_fields(data.fields, self.definition["query"].get("filter"))
        self._deliver()

    def _deliver(self) -> None:
        if self.view is None or self.data is None:
            return
        self.view_message_label.hide()
        self.view.show()
        self.view.set_data(self.data)
        self.view.set_selection(self.selected_id)

    def _show_message(self, text: str) -> None:
        self.view_message_label.setText(text)
        self.view_message_label.show()
        if self.view is not None:
            self.view.hide()

    def mapping_for(self, slot: str) -> str | None:
        """The field playing `slot` (the user's choice, or the first that fits)."""
        kind = view_type(self.view_type_id)
        if kind is None or self.data is None:
            return self.definition["mapping"].get(slot)
        return default_mapping(kind, self.data.fields, self.definition["mapping"]).get(slot)

    def set_mapping(self, slot: str, field_key: str | None) -> None:
        if self.definition["mapping"].get(slot) != field_key:
            self.definition["mapping"][slot] = field_key
            self._deliver()
            self._changed()

    def set_presentation(self, key: str, value) -> None:
        if self.presentation.get(key) != value:
            self.presentation[key] = value
            self._changed()

    def set_query(self, **parts) -> None:
        """Change parts of the question (sort, search, filter ...); None removes a part."""
        question = dict(self.definition["query"])
        for key, value in parts.items():
            if value in (None, "", []):
                question.pop(key, None)
            else:
                question[key] = value
        if question != self.definition["query"]:
            self.definition["query"] = question
            self._subscribe()
            self._changed()
# --- END [1.3] ---

# ==============================================================================
# [1.4] selection
# ==============================================================================
    @property
    def is_linked(self) -> bool:
        return self.definition["channels"].get("selection", "linked") != "unlinked"

    def set_linked(self, is_linked: bool) -> None:
        self.definition["channels"]["selection"] = "linked" if is_linked else "unlinked"
        if is_linked:
            self._local_selection = None
        self._sync_selection()
        self._changed()

    def _bind_hub(self):
        hub = hub_for(self)
        if hub is not self._hub:
            if self._hub is not None:
                self._hub.changed.disconnect(self._on_channel)
            self._hub = hub
            hub.changed.connect(self._on_channel)
        return hub

    def showEvent(self, event) -> None:                                  # noqa: N802
        super().showEvent(event)
        self._bind_hub()                              # the page this view now lives on
        self._sync_selection()

    def _on_channel(self, name: str, _value) -> None:
        if self.is_linked and name == selection_channel(self.source):
            self._sync_selection()

    def _sync_selection(self) -> None:
        if self.view is not None and self.data is not None:
            self.view.set_selection(self.selected_id)

    @property
    def selected_id(self):
        if not self.is_linked:
            return self._local_selection
        value = self._bind_hub().get(selection_channel(self.source))
        return value.get("id") if isinstance(value, dict) else None

    def select(self, record_id) -> None:
        if self.is_linked:
            self._bind_hub().set(selection_channel(self.source), None if record_id is None else {"id": record_id})
        else:
            self._local_selection = record_id
            self._sync_selection()
# --- END [1.4] ---

# ==============================================================================
# [1.5] actions_and_writes
# ==============================================================================
    def report(self, text: str, is_error: bool = False) -> None:
        self.status.emit(text, is_error)

    def _write(self, request: str, payload: dict, on_done=None) -> None:
        params = {key: value for key, value in self.source.items() if key != "engine"}

        def answered(reply, error) -> None:
            if error is not None:
                self.report(str(error), is_error=True)
            elif on_done is not None:
                on_done(reply)
        call_engine(self, f"{self.source['engine']}.{request}", {**params, **payload}, answered)

    def update_record(self, record_id, values: dict) -> None:
        self._write("update", {"id": record_id, "values": values})

    def set_value(self, record_id, key: str, value) -> None:
        self.update_record(record_id, {key: value})

    def create_record(self, values: dict | None = None, title: str | None = None) -> None:
        payload = {"values": values or {}} | ({"title": title} if title else {})
        self._write("create", payload, lambda reply: self.select(_new_id(reply)))

    def duplicate_record(self, record_id) -> None:
        self._write("duplicate", {"id": record_id}, lambda reply: self.select(_new_id(reply)))

    def move_record(self, record_id, before_id=None) -> None:
        self._write("move", {"id": record_id, "before_id": before_id})

    def delete_records(self, record_ids: list, ask: bool = True) -> None:
        if not record_ids:
            return
        names = [self.data.title(row) for row in (self.data.row(item) for item in record_ids) if row] if self.data else []
        what = f"“{names[0]}”" if len(names) == 1 else f"{len(record_ids)} {self.noun}s"
        if ask and QMessageBox.question(self, f"Delete {self.noun}", f"Delete {what}? This can't be undone.") \
                != QMessageBox.StandardButton.Yes:
            return
        self._write("delete", {"ids": list(record_ids)}, lambda reply: self.report(f"Deleted {reply['what']}"))

    def add_property(self, title: str, kind: str, options: tuple[str, ...] = ()) -> None:
        self._write("add_property", {"title": title, "kind": kind, "options": list(options)},
                    lambda reply: self.report(f"Added property “{reply['field']['title']}”"))

    def remove_property(self, key: str) -> None:
        self._write("remove_property", {"key": key}, lambda _reply: self.report("Property removed (values are kept)"))

    def request(self, name: str, payload: dict | None = None, on_reply=None, attachments: dict | None = None) -> None:
        """Another request of this view's source engine (e.g. "read_text", "links"); on_reply(payload, error) on
        the UI thread. The payload goes as given (add the source's keys yourself if the request needs them)."""
        call_engine(self, f"{self.source['engine']}.{name}", payload or {}, on_reply, attachments)

    def open_record(self, record_id) -> None:
        """Double-click / Open form: select it and show it in a form view."""
        self.select(record_id)
        if self.view_type_id == "form":
            return
        from app.ui.canvas.block_canvas import BlockCanvas
        node = self.parentWidget()
        while node is not None and not isinstance(node, BlockCanvas):
            node = node.parentWidget()
        if node is not None and "form" in node.specs:
            node.show_block("form")
            return
        if self._form_window is None:                 # no form view at hand: a small linked window
            hub = self._bind_hub()
            window = QWidget(self.window(), objectName="view_host_panel")
            window.setWindowFlag(Qt.WindowType.Tool, True)
            window.setWindowTitle(f"{self.noun.capitalize()}")
            window.channel_hub = hub
            layout = QVBoxLayout(window)
            layout.addWidget(ViewHost(make_definition("form", self.source), parent=window))
            window.resize(420, 520)
            self._form_window = window
        self._form_window.show()
        self._form_window.raise_()
# --- END [1.5] ---

# ==============================================================================
# [1.6] menus
# ==============================================================================
    def record_menu(self, record_id, parent: QWidget | None = None) -> QMenu:
        """The right-click menu of a record in any view: Open form, module actions, Duplicate, Delete."""
        self.select(record_id)
        menu = QMenu(parent or self)
        menu.addAction("Open form").triggered.connect(lambda: self.open_record(record_id))
        for title, handler in self.record_actions:
            menu.addAction(title).triggered.connect(lambda _checked=False, handler=handler: handler(record_id))
        menu.addSeparator()
        menu.addAction("Duplicate").triggered.connect(lambda: self.duplicate_record(record_id))
        menu.addAction(f"Delete {self.noun}…").triggered.connect(lambda: self.delete_records([record_id]))
        return menu

    def show_record_menu(self, record_id, global_pos: QPoint, parent: QWidget | None = None) -> None:
        menu = self.record_menu(record_id, parent)
        menu.exec(global_pos)
        menu.deleteLater()

    def fill_view_menu(self, menu: QMenu) -> None:
        """The view's own menu: the view's actions (extend_view_menu), filter, linking, editing the view."""
        extend = getattr(self.view, "extend_view_menu", None)
        if callable(extend):
            extend(menu)
            menu.addSeparator()
        filter_action = menu.addAction("Filter")
        filter_action.setCheckable(True)
        filter_action.setChecked(self.filter_bar.isVisible())
        filter_action.toggled.connect(self._toggle_filter)
        linked_action = menu.addAction("Follow the page's selection")
        linked_action.setCheckable(True)
        linked_action.setChecked(self.is_linked)
        linked_action.setToolTip("Off: this view keeps its own selection")
        linked_action.toggled.connect(self.set_linked)
        menu.addSeparator()
        menu.addAction("Edit view…").triggered.connect(self.edit_view)

    def _toggle_filter(self, is_on: bool) -> None:
        self.filter_bar.setVisible(is_on)
        if not is_on and self.definition["query"].get("filter"):
            self.filter_bar.set_filter(None)

    def view_menu_button(self) -> QToolButton:
        """A ⋯ button with the view menu, for a view's top row."""
        button = QToolButton(text="⋯", objectName="view_menu_btn", toolTip="Filter, linking, edit this view",
                             popupMode=QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        menu.aboutToShow.connect(lambda: (menu.clear(), self.fill_view_menu(menu)))
        button.setMenu(menu)
        return button

    def edit_view(self) -> None:
        from app.views.builder import ViewBuilderDialog
        dialog = ViewBuilderDialog(self, self.definition)
        if dialog.exec():
            self.set_definition(dialog.definition())
# --- END [1.6] ---
