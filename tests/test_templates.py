# ==============================================================================
# FILE: tests/test_templates.py
# OVERVIEW: Phase 5c of the engine plan - templates: the built-ins that replaced the Data-group modules, tiles
#           saved by those modules opening as templates (their files, layouts and console history carried
#           over), file templates' pickers and actions, Save as template (workspace database), the picker, and
#           collection schemas moved from records_adapter_defaults onto record_collections.
# PURPOSE:
#   1. The done-when: the old modules and RecordsModule are gone, and what they saved opens the same way.
# CONTRACT:
#   - Requires: tests/conftest.py fixtures, app.templates, app.modules.registry, app.ui.canvas.module_canvas,
#               app.data.store (templates, database), engines (runtime, data), app.clients.library, openpyxl
#   - Exposes: pytest tests
# NOTES:
#   - [2026-11-17] Created in Phase 5c of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - old tile: A module instance with the settings an old module saved (made by old_tile()).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. HELPERS
#    1.1 helpers
# 2. TESTS
#    2.1 registry_and_records
#    2.2 file_templates
#    2.3 old_tiles
#    2.4 save_as_template
# ==============================================================================

# ==============================================================================
# [1.1] helpers
# ==============================================================================
import sqlite3

import pytest


def old_tile(database, kind: str, settings: dict) -> str:
    """A tile the way an old module left it: its instance and settings."""
    from app.data.store.database import ModuleSettings
    instance_id = database.register_instance(kind, kind, None)
    store = ModuleSettings(database, instance_id)
    for key, value in settings.items():
        store.set(key, value)
    return instance_id


def open_tile(database, kind: str, instance_id: str | None = None):
    from app.modules.registry import create_module
    view, context = create_module(kind, database, instance_id)
    view.resize(1200, 700)
    view.show()
    return view, context


def kinds_of(layout: dict) -> list[str]:
    if "children" in layout:
        return [kind for child in layout["children"] for kind in kinds_of(child)]
    return [layout["kind"]]


def make_db(name: str, tables: str) -> int:
    from app.clients.library import library
    path = library().new_path("database", ".sqlite3")
    connection = sqlite3.connect(path)
    connection.executescript(tables)
    connection.commit()
    connection.close()
    return library().register_path("database", library().unique_name("database", name), path).item_id
# --- END [1.1] ---


# ==============================================================================
# [2.1] registry_and_records
# ==============================================================================
def test_the_picker_offers_templates_and_modules(qapp, database):
    from app.modules.registry import MODULE_BY_ID
    from app.ui.canvas.module_canvas import library_choices
    choices = {kind: title for kind, title, _description in library_choices(None, database)}
    assert {"projects": "Projects", "agents": "Agents", "calendar": "Events", "db_editor": "Database",
            "spreadsheet": "Spreadsheet", "graph_db": "Graph database", "graph": "Dataset chart"}.items() <= choices.items()
    assert choices["data_view"] == "New view of an engine…" and "notes" in choices
    assert not {"projects", "agents", "calendar", "db_editor", "spreadsheet", "graph_db", "graph"} & set(MODULE_BY_ID)
    for gone in ("graph", "calendar", "db_editor", "spreadsheet", "graph_db", "projects", "records_module"):
        with pytest.raises(ImportError):
            __import__(f"app.modules.{gone}")


def test_record_templates_declare_their_schema(qapp, database, wait_for):
    from engines.runtime import get_bus
    events, _context = open_tile(database, "calendar")             # the Calendar is the Events template now
    assert wait_for(lambda: "date" in {field["key"] for field in get_bus().call("data.schema", {"collection": "events"})["fields"]}, 5)
    assert {block.view_type_id for block in events.block_canvas.blocks()} == {"calendar", "form"}
    calendar = events.block_canvas.blocks("calendar")[0]
    assert calendar.mapping_for("date") == "date"
    events.create_record({"date": "2026-12-01", "kind": "Meeting"}, "Template meeting")
    assert wait_for(lambda: events.focus.selected() is not None and events.focus.selected().title == "Template meeting", 5)
    events.deleteLater()


def test_collection_schemas_moved_onto_record_collections(qapp, database):
    import json

    from engines import runtime
    from engines.runtime import get_bus
    database.execute("CREATE TABLE IF NOT EXISTS records_adapter_defaults (collection TEXT PRIMARY KEY, "
                     "noun TEXT NOT NULL DEFAULT 'record', properties_json TEXT NOT NULL DEFAULT '[]')")
    database.execute("INSERT OR REPLACE INTO records_adapter_defaults VALUES ('p5c_legacy', 'gadget', ?)",
                     (json.dumps([{"key": "size", "title": "Size", "kind": "number"}]),))
    runtime.detach("data")                                          # a restart of the engine copies them
    runtime.attach("data")
    schema = get_bus().call("data.schema", {"collection": "p5c_legacy"})
    assert schema["noun"] == "gadget" and [field["key"] for field in schema["fields"]] == ["size"]
    row = database.fetch_one("SELECT noun, defaults_json FROM record_collections WHERE collection = 'p5c_legacy'")
    assert row["noun"] == "gadget"
    assert database.fetch_one("SELECT 1 FROM records_adapter_defaults WHERE collection = 'p5c_legacy'")   # kept as it was
# --- END [2.1] ---


# ==============================================================================
# [2.2] file_templates
# ==============================================================================
def test_database_template_makes_files_and_tables_and_switches_views(qapp, database, wait_for, tmp_path):
    from app.clients.library import library
    from app.templates.actions import export_file, new_file, new_table
    tile, context = open_tile(database, "db_editor")
    new_file(tile, "P5c shop")
    assert wait_for(lambda: tile.item_id is not None and tile.source["collection"] == f"db:{tile.item_id}/items", 5)
    grid = tile.block_canvas.blocks("grid")[0]
    assert wait_for(lambda: grid.source == tile.source and grid.data is not None, 5)
    assert wait_for(lambda: tile.file_combo.currentData() == tile.item_id and tile.table_combo.currentText() == "items", 5)
    new_table(tile, "orders", "item TEXT NOT NULL, qty INTEGER")
    assert wait_for(lambda: tile.source["collection"].endswith("/orders"), 5)
    assert all(host.source == tile.source for host in tile.block_canvas.blocks())     # every view follows
    assert wait_for(lambda: grid.data is not None and [field["key"] for field in grid.data.fields] == ["item", "qty"], 5)
    assert context.settings.get("source") == tile.source                               # remembered
    assert wait_for(lambda: tile.table_combo.count() == 2, 5)
    export_file(tile, str(tmp_path / "shop.sqlite3"))
    with sqlite3.connect(tmp_path / "shop.sqlite3") as copy:
        assert copy.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall() == [("items",), ("orders",)]
    again, _context = open_tile(database, "db_editor", context.instance_id)            # the page opened again
    assert again.source == tile.source
    assert library().get(tile.item_id).kind == "database"
    for each in (tile, again):
        each.deleteLater()


def test_spreadsheet_and_graph_templates(qapp, database, wait_for):
    from app.templates.actions import add_sheet, load_sample_graph, new_file
    sheet, _context = open_tile(database, "spreadsheet")
    new_file(sheet, "P5c budget")
    assert wait_for(lambda: sheet.source["collection"].endswith("/Sheet1"), 5)
    cells = sheet.block_canvas.blocks("sheet")[0]
    assert wait_for(lambda: cells.view is not None and cells.view.sheet_table.item(0, 0) is not None
                    and cells.view.sheet_table.item(0, 0).text() == "Name", 5)
    add_sheet(sheet, "Costs")
    assert wait_for(lambda: sheet.source["collection"].endswith("/Costs"), 5)

    graph, _context = open_tile(database, "graph_db")
    new_file(graph, "P5c people")
    assert wait_for(lambda: graph.source["collection"].endswith("/Thing"), 5)
    load_sample_graph(graph)
    assert wait_for(lambda: graph.source["collection"].endswith("/Person"), 5)
    network = graph.block_canvas.blocks("network")[0]
    assert wait_for(lambda: network.view is not None and len(network.view.canvas.nodes) >= 7, 5)
    for each in (sheet, graph):
        each.deleteLater()
# --- END [2.2] ---


# ==============================================================================
# [2.3] old_tiles
# ==============================================================================
def test_old_database_editor_tiles_open_with_their_table_layout_and_history(qapp, database, wait_for):
    item_id = make_db("P5c inventory", "CREATE TABLE a (x TEXT); CREATE TABLE parts (name TEXT, qty INTEGER);"
                                       "INSERT INTO parts VALUES ('bolt', 3)")
    layout = {"split": "h", "sizes": [1, 3, 2], "children": [
        {"kind": "explorer", "instance_id": None, "title": "Explorer", "state": {"expanded": ["parts"]}},
        {"kind": "data", "instance_id": None, "title": "Data grid", "state": {"page": 2}},
        {"split": "v", "sizes": [1, 1], "children": [{"kind": "sql", "title": "SQL"}, {"kind": "properties", "title": "P"}]}]}
    instance = old_tile(database, "db_editor", {"database_id": item_id, "table": "parts", "blocks": layout,
                                                "sql_history": ["SELECT * FROM parts;"]})
    tile, context = open_tile(database, "db_editor", instance)
    assert kinds_of(context.settings.get("blocks")) == ["grid", "sql_console"]          # explorer / properties left
    assert wait_for(lambda: tile.source["collection"] == f"db:{item_id}/parts", 5)
    grid = tile.block_canvas.blocks("grid")[0]
    assert wait_for(lambda: grid.data is not None and grid.data.rows and grid.data.rows[0]["name"] == "bolt", 5)
    console = tile.block_canvas.blocks("sql_console")[0]
    assert console.view.console_edit.toPlainText() == "SELECT * FROM parts;"            # its history came along
    tile.deleteLater()


def test_old_spreadsheet_and_graph_tiles_find_their_files(qapp, database, wait_for, tmp_path):
    import openpyxl

    from app.clients.library import library
    book = openpyxl.Workbook()
    book.active.title = "Q1"
    book.active.append(["Month", "Sales"])
    book.active.append(["Jan", 5])
    book.save(tmp_path / "q1.xlsx")
    workbook = library().add_file("sheet", tmp_path / "q1.xlsx", library().unique_name("sheet", "P5c q1"))
    sheet, _context = open_tile(database, "spreadsheet", old_tile(database, "spreadsheet", {
        "item_id": workbook.item_id, "blocks": {"split": "h", "children": [{"kind": "grid"}, {"kind": "selection"}]}}))
    assert wait_for(lambda: sheet.source["collection"] == f"sheet:{workbook.item_id}/Q1", 5)
    assert [block.view_type_id for block in sheet.block_canvas.blocks()] == ["sheet"]

    data = library().create_text("dataset", library().unique_name("dataset", "P5c wave"), "t,a,b\n1,2,3\n2,4,5\n", ".csv")
    chart_tile, _context = open_tile(database, "graph", old_tile(database, "graph", {
        "graph": {"dataset_id": data.item_id, "x_column": "t", "y_columns": ["b", "a"], "grid": True}}))
    assert wait_for(lambda: chart_tile.source["collection"] == f"csv:{data.item_id}", 5)
    chart = chart_tile.block_canvas.blocks("chart")[0]
    assert (chart.mapping_for("x"), chart.mapping_for("y"), chart.mapping_for("y2")) == ("t", "b", "a")   # its columns
    assert wait_for(lambda: chart.view is not None and chart.view.summary_label.text() == "4 points of 2 rows", 5)
    for each in (sheet, chart_tile):
        each.deleteLater()
# --- END [2.3] ---


# ==============================================================================
# [2.4] save_as_template
# ==============================================================================
def test_save_as_template_keeps_views_and_layout_in_the_workspace(qapp, database, wait_for):
    from app.data.store.templates import get_template_store
    from app.modules.registry import create_module
    from app.ui.canvas.module_canvas import library_choices
    projects, _context = open_tile(database, "projects")
    board = projects.block_canvas.blocks("board")[0]
    board.set_mapping("group", "priority")
    template_id = projects.save_as_template("Triage board")
    stored = get_template_store(database).get(template_id)
    assert stored.title == "Triage board" and stored.spec["category"] == "Your templates"
    assert "source" not in str(stored.spec["layout"])                    # views, not the data they showed
    assert ("Triage board" in [title for _kind, title, _description in library_choices(None, database)])

    copy, _context = open_tile(database, template_id)
    assert sorted(kinds_of(copy.block_canvas.to_layout())) == ["board", "form", "timeline"]
    assert copy.block_canvas.blocks("board")[0].mapping_for("group") == "priority"
    assert copy.source == {"engine": "data", "collection": "projects"}

    get_template_store(database).delete(template_id)                     # its tiles keep their data, and say so
    gone, _context = create_module(template_id, database, _context.instance_id)
    assert "no longer exists" in gone.text()
    for each in (projects, copy):
        each.deleteLater()
# --- END [2.4] ---
