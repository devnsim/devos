# ==============================================================================
# FILE: app/templates/actions.py
# OVERVIEW: The toolbar actions templates name (TemplateSpec.actions): new records, new / imported /
#           exported files, new tables and sheets, sample graphs and sample datasets. Each is a function on a
#           template tile (callable from code and tests) plus a button that asks the user first.
# PURPOSE:
#   1. Functions: create_record, export_json, new_file, import_files, export_file, new_table, add_sheet,
#      load_sample_graph, make_mock_dataset. They ask engines (Data, Knowledge) and report on the tile.
#   2. add_action_buttons(module, row): the buttons for the template's actions (dialogs live here).
# CONTRACT:
#   - Requires: app.ui.engine_bridge.call_engine, app.clients.library, engines.toolkit.series (mock_series),
#               PySide6 dialogs. The module is a template tile (app/templates/module.py).
#   - Exposes: create_record(), export_json(), new_file(), import_files(), export_file(), new_table(),
#              add_sheet(), load_sample_graph(), make_mock_dataset(), add_action_buttons(), IMPORT_FILTERS,
#              SAMPLE_GRAPH
# NOTES:
#   - [2026-11-17] Created in Phase 5c of the engine plan (the old modules' file and record buttons).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - IMPORT_FILTERS (dict): Source kind -> file dialog filter for Import….
# - SAMPLE_GRAPH (list): Cypher statements of the small people / cities / projects graph.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. ACTIONS
#    1.1 records
#    1.2 files
#    1.3 buttons
# ==============================================================================

# ==============================================================================
# [1.1] records
# ==============================================================================
from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QInputDialog, QMenu, QPushButton

from app.clients.library import LibraryError, library
from app.data.activity_log import get_activity_log
from app.ui.engine_bridge import call_engine

IMPORT_FILTERS = {"database": "SQLite databases (*.sqlite *.sqlite3 *.db);;All files (*)",
                  "sheet": "Workbooks (*.xlsx *.xlsm *.ods *.xls *.csv);;All files (*)",
                  "dataset": "CSV files (*.csv *.tsv);;All files (*)", "graph": "All files (*)"}


def create_record(module, values: dict | None = None, title: str | None = None) -> None:
    payload = {"collection": module.source["collection"], "values": values or {}} | ({"title": title} if title else {})

    def answered(reply, error) -> None:
        if error is not None:
            module.report(str(error), is_error=True)
        else:
            module.focus.select(reply["id"])
    call_engine(module, "data.create", payload, answered)


def export_json(module) -> None:
    def answered(reply, error) -> None:
        if error is not None:
            module.report(str(error), is_error=True)
            return
        try:
            with get_activity_log().step(f"template:{module.template.template_id}", f"Export {module.NOUN}s as JSON"):
                item = library().create_text("file", f"{module.template.title} export", reply["json"], ".json")
            module.report(f"Saved “{item.name}” in your Library")
        except LibraryError as error:
            module.report(str(error), is_error=True)
    call_engine(module, "data.export_json", {"collection": module.source["collection"]}, answered)
# --- END [1.1] ---


# ==============================================================================
# [1.2] files
# ==============================================================================
def _then_show(module, success: str):
    """An answer handler: report, then show the collection the answer names."""
    def answered(reply, error) -> None:
        if error is not None:
            module.report(str(error), is_error=True)
            return
        module.report(success)
        if reply.get("collection"):
            module.show_collection(reply["collection"])
    return answered


def new_file(module, name: str) -> None:
    call_engine(module, "data.create_file", {"kind": module.template.source_kind, "name": name},
                _then_show(module, f"Created “{name}”"))


def import_files(module, paths: list[str]) -> int:
    added = []
    for path in paths:
        try:
            added.append(library().add_file(module.template.source_kind, path))
        except LibraryError as error:
            module.report(str(error), is_error=True)
    if added:
        module.report(f"Imported {len(added)} file{'s' if len(added) != 1 else ''}")
        module.show_file(added[-1].item_id)
    return len(added)


def export_file(module, destination: str) -> None:
    if module.item_id is None:
        module.report("Nothing to export yet", is_error=True)
        return
    try:
        module.report(f"Exported to {library().export(module.item_id, destination)}")
    except LibraryError as error:
        module.report(str(error), is_error=True)


def new_table(module, name: str, columns: str) -> None:
    """A table in the current database (columns: "name TEXT, qty INTEGER") or a node table in the current
    graph (columns: "name STRING, PRIMARY KEY(name)")."""
    if module.item_id is None or not module.source.get("collection"):
        module.report("Open or create a file first", is_error=True)
        return
    graph = module.template.source_kind == "graph"
    statement = f"CREATE NODE TABLE {name}({columns})" if graph else f'CREATE TABLE "{name}" ({columns})'
    request, key = ("data.cypher", "cypher") if graph else ("data.sql", "sql")

    def answered(reply, error) -> None:
        failed = error or next((result["error"] for result in (reply or {}).get("results", []) if result["error"]), None)
        if failed:
            module.report(f"New table: {failed}", is_error=True)
            return
        module.report(f"Created table “{name}”")
        module.show_collection(f"{'graph' if graph else 'db'}:{module.item_id}/{name}")
    call_engine(module, request, {"collection": module.source["collection"], key: statement}, answered)


def add_sheet(module, name: str) -> None:
    call_engine(module, "data.add_sheet", {"collection": module.source["collection"], "name": name},
                _then_show(module, f"Added sheet “{name}”"))


SAMPLE_GRAPH = [
    "CREATE NODE TABLE IF NOT EXISTS Person(name STRING, role STRING, PRIMARY KEY(name))",
    "CREATE NODE TABLE IF NOT EXISTS City(name STRING, country STRING, PRIMARY KEY(name))",
    "CREATE REL TABLE IF NOT EXISTS Knows(FROM Person TO Person, since INT64)",
    "CREATE REL TABLE IF NOT EXISTS LivesIn(FROM Person TO City)",
] + [f"MERGE (p:Person {{name: '{name}'}}) SET p.role = '{role}'"
     for name, role in (("Ada", "engineer"), ("Grace", "researcher"), ("Linus", "maintainer"), ("Margaret", "lead"))] + \
    [f"MERGE (c:City {{name: '{name}'}}) SET c.country = '{country}'"
     for name, country in (("London", "UK"), ("Helsinki", "Finland"), ("Boston", "USA"))] + \
    [f"MATCH (a:Person {{name: '{a}'}}), (b:Person {{name: '{b}'}}) MERGE (a)-[k:Knows]->(b) SET k.since = {since}"
     for a, b, since in (("Ada", "Grace", 1950), ("Grace", "Margaret", 1965), ("Linus", "Ada", 1991))] + \
    [f"MATCH (p:Person {{name: '{person}'}}), (c:City {{name: '{city}'}}) MERGE (p)-[:LivesIn]->(c)"
     for person, city in (("Ada", "London"), ("Linus", "Helsinki"), ("Margaret", "Boston"), ("Grace", "Boston"))]


def load_sample_graph(module) -> None:
    if module.item_id is None:
        module.report("Open or create a graph database first", is_error=True)
        return

    def answered(reply, error) -> None:
        failed = error or next((result["error"] for result in (reply or {}).get("results", []) if result["error"]), None)
        if failed:
            module.report(f"Sample graph: {failed}", is_error=True)
        else:
            module.report("Loaded the sample graph (Person, City)")
            module.show_collection(f"graph:{module.item_id}/Person")
    call_engine(module, "data.cypher", {"collection": module.source["collection"], "cypher": ";\n".join(SAMPLE_GRAPH) + ";"},
                answered)


def make_mock_dataset(module, kind: str = "Sine wave", points: int = 500) -> int:
    """A made-up x / y dataset (engines.toolkit.series.mock_series) in the Library; shows it."""
    from engines.toolkit.series import mock_series
    x, y = mock_series(kind, points)
    text = "x,y\n" + "\n".join(f"{a:.6g},{b:.6g}" for a, b in zip(x, y)) + "\n"
    item = library().create_text("dataset", library().unique_name("dataset", f"{kind} (sample)"), text, ".csv")
    module.report(f"Made “{item.name}”")
    module.show_file(item.item_id)
    return item.item_id
# --- END [1.2] ---


# ==============================================================================
# [1.3] buttons
# ==============================================================================
def _ask(module, title: str, label: str, text: str = "") -> str | None:
    value, ok = QInputDialog.getText(module, title, label, text=text)
    return value.strip() if ok and value.strip() else None


def _new_record_button(module) -> QPushButton:
    button = QPushButton(f"+ New {module.NOUN}")
    if module.template.presets:
        menu = QMenu(button)
        for title, values in module.template.presets:
            menu.addAction(title).triggered.connect(
                lambda _checked=False, title=title, values=values: create_record(module, dict(values), f"New {title}"))
        button.setMenu(menu)
    else:
        button.clicked.connect(lambda: create_record(module))
    return button


def _on_new_file(module) -> None:
    name = _ask(module, "New", f"Name of the new {module.template.title.lower()}:")
    if name:
        new_file(module, name)


def _on_import(module) -> None:
    paths, _filter = QFileDialog.getOpenFileNames(module, "Import into your Library",
                                                  filter=IMPORT_FILTERS.get(module.template.source_kind, ""))
    if paths:
        import_files(module, paths)


def _on_export(module) -> None:
    if module.item_id is not None:
        item = library().get(module.item_id)
        destination, _filter = QFileDialog.getSaveFileName(module, "Export", (item.name + item.extension) if item else "")
        if destination:
            export_file(module, destination)


def _on_new_table(module) -> None:
    graph = module.template.source_kind == "graph"
    name = _ask(module, "New table", "Table name:")
    if not name:
        return
    columns = _ask(module, "New table", "Columns:", "name STRING, PRIMARY KEY(name)" if graph else "name TEXT NOT NULL, notes TEXT")
    if columns:
        new_table(module, name, columns)


def _on_add_sheet(module) -> None:
    name = _ask(module, "Add sheet", "Sheet name:")
    if name:
        add_sheet(module, name)


def _on_mock(module) -> None:
    from engines.toolkit.series import MOCK_GENERATORS
    kind, ok = QInputDialog.getItem(module, "Sample data", "Shape:", list(MOCK_GENERATORS), 0, False)
    if ok:
        make_mock_dataset(module, kind)


BUTTONS = {
    "new_file": ("New…", "Make a new one in your Library", _on_new_file),
    "import_file": ("Import…", "Copy a file into your Library and open it", _on_import),
    "export_file": ("Export…", "Copy this file to a folder of your choice", _on_export),
    "new_table": ("+ Table", "Add a table", _on_new_table),
    "add_sheet": ("+ Sheet", "Add a sheet to this workbook", _on_add_sheet),
    "sample_graph": ("Sample graph", "Add a small people / cities graph to try things with", load_sample_graph),
    "mock_dataset": ("Sample data…", "Make a dataset of made-up numbers", _on_mock),
}


def add_action_buttons(module, row: QHBoxLayout) -> None:
    """The template's toolbar buttons (export_json goes to the ⋯ menu instead)."""
    for action in module.template.actions:
        if action == "new_record":
            row.addWidget(_new_record_button(module))
        elif action in BUTTONS:
            title, tip, handler = BUTTONS[action]
            button = QPushButton(title, toolTip=tip)
            button.clicked.connect(lambda _checked=False, handler=handler: handler(module))
            row.addWidget(button)
# --- END [1.3] ---
