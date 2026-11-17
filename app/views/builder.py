# ==============================================================================
# FILE: app/views/builder.py
# OVERVIEW: The view builder - make or edit a view: pick a source, a view type (only those that can draw the
#           source's shape), which field plays which role (filled in from field kinds), a filter, a sort and
#           a name, with a live preview.
# PURPOSE:
#   1. ViewBuilderDialog(parent, definition): starts from a definition (a new one or the view being edited),
#      loads sources and fields from the engine, and returns the finished definition().
#   2. The preview is a real ViewHost (unlinked, so trying things out never moves the page's selection).
# CONTRACT:
#   - Requires: app.views (api, host, filter_bar), app.ui.engine_bridge.call_engine (<engine>.collections,
#               <engine>.schema), engines.runtime.sources (attached source engines), QSS selector #view_builder_dialog
#   - Exposes: ViewBuilderDialog(parent, definition) with definition(), source_combo, type_list, slot_combos,
#              filter_bar, preview
# NOTES:
#   - [2026-11-17] Phase 5b: also hides view types that can't show the chosen data (ViewType.accepts).
#   - [2026-11-17] Only view types the chosen data can serve are shown (ViewType.requests, Phase 4).
#   - [2026-11-17] Phase 3: sources come from every attached engine following the source contract.
#   - [2026-11-17] Created in Phase 2 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - PREVIEW_DELAY_MS (int): Changes wait this long before the preview rebuilds (default: 250).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. BUILDER
#    1.1 dialog
#    1.2 loading
#    1.3 definition
# ==============================================================================

# ==============================================================================
# [1.1] dialog
# ==============================================================================
from __future__ import annotations

import copy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLineEdit, QListWidget,
    QListWidgetItem, QSplitter, QVBoxLayout, QWidget,
)

from app.ui.engine_bridge import call_engine
from app.ui.ui_tools import control_label
from app.views.api import default_mapping, make_definition, upgrade_definition, view_type, view_types
from app.views.filter_bar import FilterBar
from app.views.host import ViewHost

PREVIEW_DELAY_MS = 250


class ViewBuilderDialog(QDialog):
    def __init__(self, parent: QWidget | None, definition: dict) -> None:
        super().__init__(parent, objectName="view_builder_dialog")
        self.setWindowTitle("View")
        self.resize(1000, 620)
        self.base = upgrade_definition(definition, definition)
        self.fields: list[dict] = []
        self.slot_combos: dict[str, QComboBox] = {}
        self._loading = True
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        settings = QWidget()
        form = QFormLayout(settings)
        self.title_edit = QLineEdit(self.base.get("title", ""), placeholderText="Name (optional)")
        form.addRow("Name", self.title_edit)
        self.source_combo = QComboBox()
        self.source_combo.currentIndexChanged.connect(self._load_schema)
        form.addRow("Data", self.source_combo)
        self.type_list = QListWidget()
        self.type_list.setMaximumHeight(130)
        for kind in view_types("table"):
            item = QListWidgetItem(f"{kind.title} — {kind.description}")
            item.setData(Qt.ItemDataRole.UserRole + 1, kind.requests)
            item.setData(Qt.ItemDataRole.UserRole, kind.type_id)
            self.type_list.addItem(item)
            if kind.type_id == self.base["view_type"]:
                self.type_list.setCurrentItem(item)
        self.type_list.currentItemChanged.connect(self._build_slots)
        form.addRow("Show as", self.type_list)
        self.slots_form = QFormLayout()
        form.addRow(self.slots_form)
        sort_row = QHBoxLayout()
        self.sort_combo, self.direction_combo = QComboBox(), QComboBox()
        self.direction_combo.addItem("ascending", False)
        self.direction_combo.addItem("descending", True)
        for combo in (self.sort_combo, self.direction_combo):
            combo.currentIndexChanged.connect(self._schedule_preview)
            sort_row.addWidget(combo)
        form.addRow("Sort by", sort_row)
        self.filter_bar = FilterBar()
        self.filter_bar.changed.connect(self._schedule_preview)
        form.addRow(control_label("Show only rows that match"))
        form.addRow(self.filter_bar)
        self.linked_check = QCheckBox("Follow the page's selection",
                                      checked=self.base["channels"].get("selection", "linked") != "unlinked")
        form.addRow(self.linked_check)
        splitter.addWidget(settings)
        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.addWidget(control_label("Preview"))
        self.preview = ViewHost(self._current(linked=False))
        preview_layout.addWidget(self.preview, 1)
        splitter.addWidget(preview_panel)
        splitter.setSizes([420, 580])
        layout.addWidget(splitter, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._preview_timer = QTimer(self, singleShot=True, interval=PREVIEW_DELAY_MS, timeout=self._update_preview)
        self._load_sources()
# --- END [1.1] ---

# ==============================================================================
# [1.2] loading
# ==============================================================================
    def _load_sources(self) -> None:
        """Sources: the collections of every attached engine that follows the source contract (records today;
        the Data, Spatial ... engines add theirs)."""
        from engines.runtime import get_bus, sources
        engines = sources()
        answers: dict[str, list] = {}

        def answered(engine_id: str, reply, _error) -> None:
            answers[engine_id] = (reply or {}).get("collections", [])
            if len(answers) == len(engines):
                self._fill_sources(answers, get_bus())
        for engine_id in engines:
            call_engine(self, f"{engine_id}.collections", {},
                        lambda reply, error, engine_id=engine_id: answered(engine_id, reply, error))
        if not engines:
            self._fill_sources({}, get_bus())

    def _fill_sources(self, answers: dict[str, list], bus) -> None:
        self.source_combo.blockSignals(True)
        several = len(answers) > 1
        for engine_id in sorted(answers):
            title = bus.manifest(engine_id).title
            for item in answers[engine_id]:
                label = f"{item.get('title') or item['collection'].capitalize()} ({item.get('count', 0)} {item.get('noun', 'record')}s)"
                self.source_combo.addItem(f"{title} · {label}" if several else label,
                                          {"engine": engine_id, "collection": item["collection"]})
        wanted = {key: value for key, value in self.base["source"].items()}
        index = next((i for i in range(self.source_combo.count()) if self.source_combo.itemData(i) == wanted), -1)
        if index < 0 and wanted.get("collection"):           # e.g. its engine is detached: keep it, marked
            self.source_combo.insertItem(0, f"{wanted['collection']} (not available)", wanted)
            index = 0
        self.source_combo.setCurrentIndex(max(0, index))
        self.source_combo.blockSignals(False)
        self._load_schema()

    def _load_schema(self) -> None:
        source = self.source_combo.currentData()
        if not source:
            return

        def answered(reply, error) -> None:
            self.fields = (reply or {}).get("fields", [])
            same_source = source == {key: value for key, value in self.base["source"].items()}
            question = self.base["query"] if same_source else {}
            self.sort_combo.blockSignals(True)
            self.sort_combo.clear()
            self.sort_combo.addItem("(source order)", None)
            self.sort_combo.addItem("Name", "title")
            for field in self.fields:
                self.sort_combo.addItem(field["title"], field["key"])
            sort = (question.get("sort") or [{}])[0]
            self.sort_combo.setCurrentIndex(max(0, self.sort_combo.findData(sort.get("field"))))
            self.direction_combo.setCurrentIndex(1 if sort.get("descending") else 0)
            self.sort_combo.blockSignals(False)
            self.filter_bar.set_fields(self.fields, question.get("filter"))
            self._show_fitting_types(source)
            self._build_slots()
            self._loading = False
            self._schedule_preview()
        call_engine(self, f"{source['engine']}.schema", {"collection": source["collection"]}, answered)

    def _show_fitting_types(self, source: dict) -> None:
        """Only view types the source can serve (a document view needs read_text ...)."""
        from engines.runtime import get_bus
        manifest = get_bus().manifest(source["engine"])
        for index in range(self.type_list.count()):
            item = self.type_list.item(index)
            needs = item.data(Qt.ItemDataRole.UserRole + 1) or ()
            kind = view_type(item.data(Qt.ItemDataRole.UserRole))
            item.setHidden(any(manifest.request(name) is None for name in needs) or kind is None or not kind.can_show(source))
        current = self.type_list.currentItem()
        if current is None or current.isHidden():
            visible = [self.type_list.item(i) for i in range(self.type_list.count()) if not self.type_list.item(i).isHidden()]
            if visible:
                self.type_list.setCurrentItem(visible[0])

    def _build_slots(self, *_args) -> None:
        while self.slots_form.rowCount():
            self.slots_form.removeRow(0)
        self.slot_combos = {}
        kind = view_type(self._type_id())
        if kind is None:
            return
        chosen = default_mapping(kind, self.fields, self.base["mapping"])
        for slot in kind.slots:
            combo = QComboBox()
            if not slot.required:
                combo.addItem("(none)", None)
            for field in self.fields:
                if field["kind"] in slot.kinds:
                    combo.addItem(field["title"], field["key"])
            combo.setCurrentIndex(max(0, combo.findData(chosen.get(slot.key))))
            combo.currentIndexChanged.connect(self._schedule_preview)
            self.slots_form.addRow(slot.title, combo)
            self.slot_combos[slot.key] = combo
        self._schedule_preview()
# --- END [1.2] ---

# ==============================================================================
# [1.3] definition
# ==============================================================================
    def _type_id(self) -> str:
        item = self.type_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else self.base["view_type"]

    def _current(self, linked: bool | None = None) -> dict:
        source = self.source_combo.currentData() if hasattr(self, "source_combo") and self.source_combo.currentData() \
            else self.base["source"]
        if not hasattr(self, "sort_combo"):
            return copy.deepcopy(self.base)
        question = {}
        if self.sort_combo.currentData():
            question["sort"] = [{"field": self.sort_combo.currentData(), "descending": bool(self.direction_combo.currentData())}]
        if self.filter_bar.filter():
            question["filter"] = self.filter_bar.filter()
        mapping = {slot: combo.currentData() for slot, combo in self.slot_combos.items()}
        is_linked = self.linked_check.isChecked() if linked is None else linked
        definition = make_definition(self._type_id(), source, question, mapping, dict(self.base.get("presentation", {})),
                                     self.title_edit.text().strip(), is_linked)
        return definition

    def _schedule_preview(self, *_args) -> None:
        if not self._loading:
            self._preview_timer.start()

    def _update_preview(self) -> None:
        self.preview.set_definition(self._current(linked=False))

    def definition(self) -> dict:
        """The finished view (call after the dialog was accepted)."""
        return self._current()
# --- END [1.3] ---
