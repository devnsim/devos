# ==============================================================================
# FILE: app/templates/module.py
# OVERVIEW: TemplateModule - a page tile built from a template: views over engine data laid out on a block
#           canvas, with the template's toolbar. Record templates show their collection; file templates
#           (Database, Spreadsheet, Graph database, Dataset chart) pick one of the Library's files and a table.
# PURPOSE:
#   1. open_template(context): the tile for context.module_type_id's template (a class per template, so the
#      SourceModule machinery reads the template's source, noun, views and layout).
#   2. File templates: a file picker and a table picker; the choice is saved ("source"). Tiles of the old
#      modules carry on: their saved file (database_id + table, item_id, graph.dataset_id) is read once.
#   3. Old block layouts are translated with the template's legacy_blocks (SQL / Cypher history kept); the
#      old Graph module's chosen columns become the chart's mapping.
#   4. Save as template…: the tile's views, their settings and layout as a user template (workspace database).
#   5. An extension (TemplateSpec.extension) adds blocks, buttons and record actions; its methods are
#      reachable on the tile (module.show_chat()).
# CONTRACT:
#   - Requires: app.modules.source_module.SourceModule, app.templates (spec, actions, registry),
#               app.data.store.templates, app.clients.library, app.ui.engine_bridge, app.views.api
#   - Exposes: open_template(parent=None, context=None), TemplateModule (template, item_id, show_file(),
#              show_collection(), save_as_template(), template_spec()), migrate_layout(layout, kinds, legacy)
# NOTES:
#   - [2026-11-17] Created in Phase 5c of the engine plan (replaces the Projects, Agents, Calendar, Database
#                  Editor, Spreadsheet, Graph Database and Graph modules, and RecordsModule).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - SOURCE_SETTING (str): Tile setting holding the chosen source ({"engine": "data", "collection": ...}).
# - item_id: The Library file a file template's tile shows (None for record templates / nothing chosen).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. TEMPLATE_MODULE
#    1.1 layout_migration
#    1.2 module
#    1.3 files
#    1.4 save_as_template
# ==============================================================================

# ==============================================================================
# [1.1] layout_migration
# ==============================================================================
from __future__ import annotations

import copy
import importlib

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QInputDialog, QPushButton

from app.clients.library import library
from app.data.store.templates import get_template_store
from app.modules.source_module import SourceModule
from app.templates.actions import add_action_buttons, export_json
from app.templates.spec import TemplateSpec
from app.ui.canvas.block_canvas import BlockSpec
from app.ui.engine_bridge import call_engine, watch_library
from app.views.api import make_definition, view_type

SOURCE_SETTING = "source"


def migrate_layout(layout: dict | None, known: set[str], legacy: dict, states: dict | None = None) -> dict | None:
    """An old module's block layout in a template's terms: legacy kinds are renamed (their old state replaced by
    states.get(new kind)) or leave, other known kinds stay; splits left with one child collapse (the top stays a
    split, as layouts are)."""
    if not layout:
        return layout

    def walk(node: dict) -> dict | None:
        if "children" in node:
            children = [child for child in (walk(child) for child in node["children"]) if child]
            if not children:
                return None
            if len(children) == 1:
                return children[0]
            kept = {**node, "children": children}
            if len(node.get("sizes") or []) != len(children):
                kept.pop("sizes", None)
            return kept
        kind = node.get("kind", node.get("module"))
        if kind in known and kind not in legacy:            # an old layout: its own names win ("grid" was the cells)
            return node
        new = legacy.get(kind)
        if new is None:
            return None
        return {"kind": new, "title": node.get("title", ""), **({"state": states[new]} if states and new in states else {})}
    migrated = walk(copy.deepcopy(layout))
    return migrated if migrated is None or "children" in migrated else {"split": "h", "children": [migrated]}


def _leaf_kinds(layout: dict | None) -> set[str]:
    if not layout:
        return set()
    if "children" in layout:
        return set().union(*[_leaf_kinds(child) for child in layout["children"]])
    return {layout.get("kind", layout.get("module"))}
# --- END [1.1] ---


# ==============================================================================
# [1.2] module
# ==============================================================================
class TemplateModule(SourceModule):
    TEMPLATE: TemplateSpec

    @property
    def template(self) -> TemplateSpec:
        return self.TEMPLATE

    def __getattr__(self, name: str):
        extension = self.__dict__.get("_extension")       # extension methods on the tile (module.show_chat())
        if extension is not None and not name.startswith("_") and hasattr(extension, name):
            return getattr(extension, name)
        raise AttributeError(name)

    def before_views(self) -> None:
        self.item_id: int | None = None
        self._extension = None
        self._legacy_mapping: dict = {}
        template = self.template
        if template.extension:
            module_path, class_name = template.extension.split(":")
            self._extension = getattr(importlib.import_module(module_path), class_name)(self)
        if template.is_file_kind:
            saved = self.setting(SOURCE_SETTING)
            self.source = dict(saved) if isinstance(saved, dict) and saved.get("collection") else \
                {"engine": "data", "collection": ""}
            self.item_id = _item_of(self.source)
            if not self.source["collection"]:
                self._choose_first_file()                     # async; the views wait until it is known
        else:
            self.source = {"engine": "data", "collection": template.collection}
            call_engine(self, "data.register", {"collection": template.collection, "noun": template.noun,   # before any query
                                                "properties": list(template.properties)})
        self._migrate_old_settings()

    def _migrate_old_settings(self) -> None:
        """Tiles saved by the old modules: their block layout (and console history), the Graph's columns."""
        from app.templates.builtin import LEGACY_SETTINGS
        template = self.template
        saved = self.setting(self.BLOCKS_SETTING)
        known = set(self.VIEWS) | {spec.kind for spec in self.extra_block_specs()}
        unknown = {kind for kind in _leaf_kinds(saved) if kind not in known and view_type(kind) is None}
        if saved and unknown:
            states = {"sql_console": {"view_type": "sql_console", "presentation": {"history": self.setting("sql_history", [])}},
                      "cypher_console": {"view_type": "cypher_console", "presentation": {"history": self.setting("history", [])}}}
            migrated = migrate_layout(saved, known | {kind for kind in _leaf_kinds(saved) if view_type(kind) is not None},
                                      template.legacy_blocks, states)
            self.save_setting(self.BLOCKS_SETTING, migrated)
        if template.template_id == "graph" and not saved and isinstance(self.setting("graph"), dict):
            config = self.setting("graph")
            columns = list(config.get("y_columns") or [])
            self._legacy_mapping = {key: value for key, value in (("x", config.get("x_column")),
                                                                  ("y", columns[0] if columns else None),
                                                                  ("y2", columns[1] if len(columns) > 1 else None)) if value}
        if template.is_file_kind and not self.source["collection"] and template.template_id in LEGACY_SETTINGS:
            key, table_key = LEGACY_SETTINGS[template.template_id]
            value = self.setting(key.split(".")[0])
            item_id = value.get(key.split(".")[1]) if isinstance(value, dict) and "." in key else value
            if item_id is not None:
                self.show_file(int(item_id), self.setting(table_key) if table_key else None)

    def block_definition(self, type_id: str) -> dict:
        parts = copy.deepcopy(self.template.definitions.get(type_id, {}))
        if type_id == "chart" and self._legacy_mapping:
            parts["mapping"] = {**parts.get("mapping", {}), **self._legacy_mapping}
        return make_definition(type_id, self.source, parts.get("query"), parts.get("mapping"), parts.get("presentation"),
                               parts.get("title", ""))

    def extra_block_specs(self) -> list[BlockSpec]:
        extension = self.__dict__.get("_extension")
        return extension.extra_block_specs() if extension is not None and hasattr(extension, "extra_block_specs") else []

    def record_actions(self) -> list[tuple[str, object]]:
        extension = self.__dict__.get("_extension")
        return extension.record_actions() if extension is not None and hasattr(extension, "record_actions") else []

    def export_actions(self) -> list[tuple[str, object]]:
        return [(f"Export all {self.NOUN}s as JSON (to Library)", lambda: export_json(self))] \
            if "export_json" in self.template.actions else []

    def toolbar_start(self, row: QHBoxLayout) -> None:
        if self.template.is_file_kind:
            self.file_combo = QComboBox(toolTip=f"Which {self.template.title.lower()} this tile shows")
            self.file_combo.setMinimumWidth(160)
            self.file_combo.activated.connect(lambda _index: self._file_chosen())
            row.addWidget(self.file_combo)
            if self.template.source_kind != "dataset":
                self.table_combo = QComboBox(toolTip="Which table (sheet) of the file")
                self.table_combo.activated.connect(lambda _index: self._table_chosen())
                row.addWidget(self.table_combo)
            watch_library(self, lambda kind: kind == self.template.source_kind and self._fill_files())
            self._fill_files()
        add_action_buttons(self, row)

    def add_toolbar_actions(self, row: QHBoxLayout) -> None:
        extension = self.__dict__.get("_extension")
        if extension is not None and hasattr(extension, "add_toolbar_actions"):
            extension.add_toolbar_actions(row)
        save_btn = QPushButton("Save as template…", toolTip="Keep these views and their layout as a template of your own")
        save_btn.clicked.connect(self._ask_save_as_template)
        row.addWidget(save_btn)

    def create_record(self, values: dict | None = None, title: str | None = None) -> None:
        from app.templates.actions import create_record
        create_record(self, values, title)
# --- END [1.2] ---


# ==============================================================================
# [1.3] files
# ==============================================================================
    def _fill_files(self) -> None:
        if not hasattr(self, "file_combo"):
            return
        self.file_combo.clear()
        for item in library().items(self.template.source_kind):
            self.file_combo.addItem(item.name, item.item_id)
        if self.file_combo.count() == 0:
            self.file_combo.addItem(f"No {self.template.title.lower()} yet: New… or Import…", None)
        self.file_combo.setCurrentIndex(max(0, self.file_combo.findData(self.item_id)))
        self._fill_tables()

    def _fill_tables(self) -> None:
        if not hasattr(self, "table_combo") or self.item_id is None:
            return

        def answered(reply, error) -> None:
            if error is not None:
                self.report(str(error), is_error=True)
                return
            self.table_combo.clear()
            for entry in reply["collections"]:
                self.table_combo.addItem(entry["collection"].rsplit("/", 1)[-1], entry["collection"])
            self.table_combo.setCurrentIndex(max(0, self.table_combo.findData(self.source.get("collection"))))
        call_engine(self, "data.collections", {"kinds": [self.template.source_kind], "item_id": self.item_id}, answered)

    def _file_chosen(self) -> None:
        item_id = self.file_combo.currentData()
        if item_id is not None and item_id != self.item_id:
            self.show_file(item_id)

    def _table_chosen(self) -> None:
        collection = self.table_combo.currentData()
        if collection:
            self.show_collection(collection)

    def _choose_first_file(self) -> None:
        items = library().items(self.template.source_kind)
        if items:
            self.show_file(items[0].item_id)

    def show_file(self, item_id: int, table: str | None = None) -> None:
        """Show a Library file of the template's kind: the named table, else its first one."""
        def answered(reply, error) -> None:
            if error is not None:
                self.report(str(error), is_error=True)
                return
            collections = [entry["collection"] for entry in reply["collections"]]
            wanted = next((name for name in collections if table and name.rsplit("/", 1)[-1] == table), None)
            if collections:
                self.show_collection(wanted or collections[0])
            else:
                self.report("This file has no tables yet: + Table", is_error=True)
        call_engine(self, "data.collections", {"kinds": [self.template.source_kind], "item_id": int(item_id)}, answered)

    def show_collection(self, collection: str) -> None:
        """Show another table (of this file or another of the same kind) in every view of the tile."""
        source = {"engine": "data", "collection": collection}
        self.item_id = _item_of(source)
        self.save_setting(SOURCE_SETTING, source)
        self.set_source(source)
        if hasattr(self, "file_combo"):
            if self.file_combo.findData(self.item_id) < 0:
                self._fill_files()
            else:
                self.file_combo.setCurrentIndex(self.file_combo.findData(self.item_id))
                self._fill_tables()
# --- END [1.3] ---


# ==============================================================================
# [1.4] save_as_template
# ==============================================================================
    def template_spec(self, title: str) -> dict:
        """This tile as a template: its views (with their settings, without its data) and their layout."""
        def strip(node: dict) -> dict:
            if "children" in node:
                return {**node, "children": [strip(child) for child in node["children"]]}
            entry = {"kind": node["kind"], "title": node.get("title", "")}
            if isinstance(node.get("state"), dict) and "view_type" in node["state"]:
                state = {key: value for key, value in node["state"].items() if key != "source"}
                state["query"] = {key: value for key, value in state.get("query", {}).items() if key != "offset"}
                entry["state"] = state
            return entry
        layout = strip(self.block_canvas.to_layout())
        spec = self.template.to_dict()
        spec.update(title=title, category="Your templates", layout=layout, legacy_blocks={}, is_user=True,
                    views=list(dict.fromkeys(list(self.template.views) + sorted(_leaf_kinds(layout)))),
                    description=f"Made from {self.template.title}")
        return spec

    def save_as_template(self, title: str) -> str:
        stored = get_template_store(self.context.database if self.context else None).save(title, self.template_spec(title))
        self.report(f"Saved template “{title}”: it's in every tile's picker")
        return stored.template_id

    def _ask_save_as_template(self) -> None:
        title, ok = QInputDialog.getText(self, "Save as template", "Template name:", text=f"My {self.template.title.lower()}")
        if ok and title.strip():
            self.save_as_template(title.strip())


def _item_of(source: dict) -> int | None:
    collection = str(source.get("collection") or "")
    if ":" not in collection:
        return None
    try:
        return int(collection.split(":", 1)[1].split("/", 1)[0])
    except ValueError:
        return None


_classes: dict[str, type] = {}


def open_template(parent=None, context=None):
    """The tile for the template named by context.module_type_id (KeyError when there is no such template)."""
    from app.templates import template
    spec = template(context.module_type_id, context.database if context else None)
    cls = _classes.get(spec.template_id)
    if cls is None or cls.TEMPLATE != spec:
        cls = _classes[spec.template_id] = type(f"Template_{spec.template_id.replace(':', '_')}", (TemplateModule,), {
            "TEMPLATE": spec, "NOUN": spec.noun, "VIEWS": tuple(spec.views), "DEFAULT_LAYOUT": spec.layout,
            "SOURCE": {"engine": "data", "collection": spec.collection}})
    return cls(parent, context)
# --- END [1.4] ---
