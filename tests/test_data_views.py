# ==============================================================================
# FILE: tests/test_data_views.py
# OVERVIEW: Phase 5b of the engine plan - views over the Data engine: the generic Grid, Chart and Network views
#           and the engine's specialist views (Sheet, SQL console, Cypher console, Structure), plus the requests
#           behind them (structure, cells, set_cell, sql, cypher) and ViewType.accepts.
# PURPOSE:
#   1. Each view works on real files in a temporary Library, driven the way a user would (cells edited, pages
#      turned, statements run), and the result is checked in the file or the engine.
# CONTRACT:
#   - Requires: tests/conftest.py fixtures, app.views, engines (runtime, data, toolkit.kuzu_db), app.clients.library,
#               openpyxl, kuzu
#   - Exposes: pytest tests
# NOTES:
#   - [2026-11-17] Created in Phase 5b of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - parts database: A SQLite file with 1000 parts, an index and a foreign key (make_parts_db()).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. HELPERS
#    1.1 helpers
# 2. TESTS
#    2.1 requests
#    2.2 generic_views
#    2.3 specialist_views
# ==============================================================================

# ==============================================================================
# [1.1] helpers
# ==============================================================================
import sqlite3

import pytest

from engines.kernel import EngineError


def make_parts_db(name: str = "P5b parts") -> tuple[str, object]:
    from app.clients.library import library
    path = library().new_path("database", ".sqlite3")
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE makers (maker_id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE parts (part_id INTEGER PRIMARY KEY, name TEXT NOT NULL, qty INTEGER, price REAL,
                                maker_id INTEGER REFERENCES makers(maker_id));
            CREATE INDEX parts_by_name ON parts (name);
            INSERT INTO makers (name) VALUES ('Acme');
        """)
        connection.executemany("INSERT INTO parts (name, qty, price, maker_id) VALUES (?, ?, ?, 1)",
                               [(f"Part {n:04}", n, n / 4) for n in range(1, 1001)])
    item = library().register_path("database", library().unique_name("database", name), path)
    return f"db:{item.item_id}/parts", item


def host_for(view_type: str, source: dict, parent, **parts):
    from app.views.api import make_definition
    from app.views.host import ViewHost
    host = ViewHost(make_definition(view_type, source, **parts), parent=parent)
    return host


@pytest.fixture
def page(qapp):
    from PySide6.QtWidgets import QWidget
    widget = QWidget()
    widget.resize(1100, 700)
    widget.show()
    yield widget
    widget.deleteLater()
# --- END [1.1] ---


# ==============================================================================
# [2.1] requests
# ==============================================================================
def test_structure_sql_and_cells_requests(database, tmp_path):
    import openpyxl

    from app.clients.library import library
    from engines.runtime import get_bus
    bus = get_bus()
    collection, item = make_parts_db()
    structure = bus.call("data.structure", {"collection": collection})
    assert structure["rows"] == 1000 and structure["indexes"][0]["columns"] == ["name"]
    assert structure["foreign_keys"] == [{"column": "maker_id", "table": "makers", "to": "maker_id"}]
    assert "CREATE TABLE parts" in structure["sql"] and structure["columns"][0]["primary_key"]
    answer = bus.call("data.sql", {"collection": collection, "sql": "SELECT count(*) AS n FROM parts;\n"
                                   "UPDATE parts SET qty = 0 WHERE qty < 11;\nSELECT nope FROM parts;\nSELECT 1;"})
    assert [result["error"] != "" for result in answer["results"]] == [False, False, True]   # stops at the error
    assert answer["results"][0]["rows"] == [[1000]] and answer["results"][1]["changed"] == 10 and answer["wrote"]
    with pytest.raises(EngineError) as records:
        bus.call("data.sql", {"collection": "projects", "sql": "SELECT 1"})
    assert records.value.code == "not_allowed"

    book = openpyxl.Workbook()
    book.active.title = "Calc"
    book.active.append(["Item", "Cost"])
    book.active.append(["Desk", 120])
    book.save(tmp_path / "calc.xlsx")
    sheet_item = library().add_file("sheet", tmp_path / "calc.xlsx", library().unique_name("sheet", "P5b calc"))
    sheet = f"sheet:{sheet_item.item_id}/Calc"
    bus.call("data.set_cell", {"collection": sheet, "row": 1, "col": 2, "raw": "=B2*2"})
    cells = {(row, col): (raw, shown) for row, col, raw, shown, _problem in bus.call("data.cells", {"collection": sheet})["cells"]}
    assert cells[(1, 2)] == ("=B2*2", "240") and cells[(0, 0)] == ("Item", "Item")
    with pytest.raises(EngineError) as bad:
        bus.call("data.set_cell", {"collection": sheet, "row": 1, "col": 3, "raw": "=(1+"})
    assert bad.value.code == "invalid_value"
    assert bus.call("data.structure", {"collection": sheet})["kind"] == "sheet"
# --- END [2.1] ---


# ==============================================================================
# [2.2] generic_views
# ==============================================================================
def test_grid_pages_edits_pastes_and_adds_columns(qapp, database, wait_for, page):
    from PySide6.QtCore import Qt
    collection, _item = make_parts_db("P5b grid")
    host = host_for("grid", {"engine": "data", "collection": collection}, page)
    host.resize(1000, 600)
    assert wait_for(lambda: host.data is not None and host.definition["query"].get("limit") == 200 and
                    len(host.data.rows) == 200, 5)
    grid = host.view
    assert grid.page_label.text() == "1–200 of 1,000" and not grid.previous_btn.isEnabled()
    assert [column["key"] for column in grid.model.columns] == ["name", "part_id", "qty", "price", "maker_id"]
    grid.next_btn.click()
    assert wait_for(lambda: grid.page_label.text() == "201–400 of 1,000" and grid.model.rows[0]["qty"] == 201, 5)
    assert grid.model.headerData(0, Qt.Orientation.Vertical) == "201"

    qty = grid.model.index(0, 2)
    grid.model.setData(qty, "7")                                         # a cell edited in place
    assert wait_for(lambda: grid.model.rows and grid.model.rows[0]["qty"] == 7, 5)
    grid.grid_table.setCurrentIndex(grid.model.index(1, 2))
    assert grid.paste_text("70\t1.5\n71\t2.5\n") == 2                  # a block pasted from a spreadsheet
    assert wait_for(lambda: [(row["qty"], row["price"]) for row in grid.model.rows[1:3]] == [(70, 1.5), (71, 2.5)], 5)
    grid.grid_table.selectionModel().clearSelection()
    for column in (2, 3):
        grid.grid_table.selectionModel().select(grid.model.index(1, column),
                                                grid.grid_table.selectionModel().SelectionFlag.Select)
    assert grid.copied_text() == "70\t1.5"
    host.add_property("Bin", "text")
    assert wait_for(lambda: "Bin" in [column["key"] for column in grid.model.columns], 5)


def test_grid_shows_computed_columns_read_only(qapp, database, wait_for, page):
    from PySide6.QtCore import Qt

    from engines.runtime import get_bus
    bus = get_bus()
    bus.call("data.register", {"collection": "p5b_lines", "noun": "line", "properties": [
        {"key": "price", "title": "Price", "kind": "number"}, {"key": "qty", "title": "Qty", "kind": "number"}]})
    bus.call("data.create", {"collection": "p5b_lines", "title": "Cable", "values": {"price": 3, "qty": 4}})
    total = bus.call("data.add_property", {"collection": "p5b_lines", "title": "Total", "kind": "formula",
                                           "formula": "=[Price]*[Qty]"})["field"]["key"]
    host = host_for("grid", {"engine": "data", "collection": "p5b_lines"}, page)
    assert wait_for(lambda: host.data is not None and host.data.rows and total in host.data.rows[0], 5)
    grid = host.view
    column = next(index for index, field in enumerate(grid.model.columns) if field["key"] == total)
    index = grid.model.index(0, column)
    assert index.data() == "12" and not grid.model.flags(index) & Qt.ItemFlag.ItemIsEditable
    assert grid.model.data(index, Qt.ItemDataRole.ToolTipRole) == "=[Price]*[Qty]"


def test_chart_draws_rows_and_totals_from_the_engine(qapp, database, wait_for, page):
    from engines.runtime import get_bus
    bus = get_bus()
    bus.call("data.register", {"collection": "p5b_sales", "noun": "sale", "properties": [
        {"key": "region", "title": "Region", "kind": "select"}, {"key": "amount", "title": "Amount", "kind": "number"},
        {"key": "day", "title": "Day", "kind": "date"}]})
    for number, region in enumerate(["North", "South", "North", "East", "North"], start=1):
        bus.call("data.create", {"collection": "p5b_sales", "title": f"Sale {number}",
                                 "values": {"region": region, "amount": number * 10, "day": f"2026-03-0{number}"}})
    host = host_for("chart", {"engine": "data", "collection": "p5b_sales"}, page, mapping={"x": "day", "y": "amount"})
    assert wait_for(lambda: host.view is not None and host.view.summary_label.text() == "5 points of 5 rows", 5)
    assert host.view._axis_kind == "date"
    host.set_mapping("x", "region")
    host.view.total_combo.setCurrentText("Sum")                         # totals: grouped in the engine
    assert wait_for(lambda: host.definition["query"].get("group_by") == ["region"] and
                    host.view.summary_label.text() == "3 points (totals)", 5)
    totals = {row["region"]: row["sum_amount"] for row in host.data.rows}
    assert totals == {"North": 90, "South": 20, "East": 40}
    host.view.style_combo.setCurrentText("Bar")
    assert host.view.grab().width() > 0


def test_network_view_draws_the_graph_and_selects_rows(qapp, database, wait_for, page):
    from app.clients.library import library
    from app.views.api import view_type
    from engines.toolkit.kuzu_db import GraphDb
    path = library().new_path("graph", ".kuzu")
    db = GraphDb(path)
    db.load_sample_data()
    db.close()
    item = library().register_path("graph", library().unique_name("graph", "P5b graph"), path)
    source = {"engine": "data", "collection": f"graph:{item.item_id}/Person"}
    network = view_type("network")
    assert network.can_show(source) and not network.can_show({"engine": "data", "collection": "projects"})
    host = host_for("network", source, page)
    assert wait_for(lambda: host.view is not None and len(host.view.canvas.nodes) >= 12, 5)
    ada = next(node_id for node_id, row_id in host.view.row_of_node.items() if row_id == "Ada")
    host.view._clicked(ada)
    assert host.selected_id == "Ada" and host.view.canvas.selected_id == ada
    city = next(node["id"] for node in host.view.canvas.nodes if node["group"] == "City")
    host.view._clicked(city)                                            # another table's node: highlight only
    assert host.selected_id == "Ada"
    refused = host_for("network", {"engine": "data", "collection": "projects"}, page)
    assert refused.view is None and "can't show" in refused.view_message_label.text()
# --- END [2.2] ---


# ==============================================================================
# [2.3] specialist_views
# ==============================================================================
def test_sheet_view_types_values_and_formulas(qapp, database, wait_for, page, tmp_path):
    import openpyxl

    from app.clients.library import library
    book = openpyxl.Workbook()
    book.active.title = "Plan"
    for row in (["Task", "Hours", "Rate", "Cost"], ["Design", 10, 50, "=B2*C2"]):
        book.active.append(row)
    book.save(tmp_path / "plan.xlsx")
    item = library().add_file("sheet", tmp_path / "plan.xlsx", library().unique_name("sheet", "P5b plan"))
    host = host_for("sheet", {"engine": "data", "collection": f"sheet:{item.item_id}/Plan"}, page)
    assert wait_for(lambda: host.view is not None and host.view.sheet_table.item(1, 3) is not None and
                    host.view.sheet_table.item(1, 3).text() == "500", 5)
    sheet = host.view
    sheet.sheet_table.setCurrentCell(1, 3)
    assert sheet.formula_edit.text() == "=B2*C2" and sheet.cell_label.text() == "D2"
    sheet.sheet_table.item(1, 1).setText("12")                          # typing a value
    assert wait_for(lambda: sheet.sheet_table.item(1, 3).text() == "600", 5)
    sheet.sheet_table.setCurrentCell(1, 4)
    sheet.formula_edit.setText("=D2/2")
    sheet.formula_edit.returnPressed.emit()                             # a formula through the formula bar
    assert wait_for(lambda: sheet.sheet_table.item(1, 4) is not None and sheet.sheet_table.item(1, 4).text() == "300", 5)
    saved = openpyxl.load_workbook(library().path(item))["Plan"]
    assert saved["B2"].value == 12 and saved["E2"].value == "=D2/2"


def test_consoles_run_statements_and_views_follow(qapp, database, wait_for, page):
    from app.clients.library import library
    from app.views.api import view_type
    from engines.toolkit.kuzu_db import GraphDb
    collection, _item = make_parts_db("P5b console")
    source = {"engine": "data", "collection": collection}
    assert view_type("sql_console").can_show(source) and not view_type("sql_console").can_show(
        {"engine": "data", "collection": "projects"})
    grid = host_for("grid", source, page)
    console = host_for("sql_console", source, page)
    assert wait_for(lambda: grid.data is not None and console.view is not None, 5)
    assert console.view.console_edit.toPlainText() == 'SELECT * FROM "parts" LIMIT 100;'
    console.view.console_edit.setPlainText("UPDATE parts SET name = 'Renamed' WHERE part_id = 1;\n"
                                           "SELECT name, qty FROM parts WHERE part_id <= 2;")
    console.view.run()
    assert wait_for(lambda: console.view.results_table.rowCount() == 2, 5)
    assert console.view.results_table.item(0, 0).text() == "Renamed" and "1 changed" in console.view.outcome_label.text()
    assert wait_for(lambda: grid.data.rows[0]["name"] == "Renamed", 5)   # the grid heard about it
    assert console.definition["presentation"]["history"][0].startswith("UPDATE")

    path = library().new_path("graph", ".kuzu")
    db = GraphDb(path)
    db.load_sample_data()
    db.close()
    item = library().register_path("graph", library().unique_name("graph", "P5b cypher"), path)
    cypher = host_for("cypher_console", {"engine": "data", "collection": f"graph:{item.item_id}/City"}, page)
    assert wait_for(lambda: cypher.view is not None, 5)
    cypher.view.console_edit.setPlainText("MATCH (c:City) RETURN c.name ORDER BY c.name;")
    cypher.view.run()
    assert wait_for(lambda: cypher.view.results_table.rowCount() == 3, 5)
    assert cypher.view.results_table.item(0, 0).text() == "Boston"


def test_structure_view_lists_columns_and_removes_yours(qapp, database, wait_for, page):
    from engines.runtime import get_bus
    collection, _item = make_parts_db("P5b structure")
    host = host_for("structure", {"engine": "data", "collection": collection}, page)
    assert wait_for(lambda: host.view is not None and host.view.structure_tree.topLevelItemCount() >= 4, 5)
    groups = [host.view.structure_tree.topLevelItem(i).text(0) for i in range(host.view.structure_tree.topLevelItemCount())]
    assert groups == ["Columns (5)", "Indexes (1)", "Foreign keys (1)", "Definition"]
    bus = get_bus()
    bus.call("data.register", {"collection": "p5b_people", "noun": "person", "properties": []})
    bus.call("data.add_property", {"collection": "p5b_people", "title": "Team", "kind": "text"})
    people = host_for("structure", {"engine": "data", "collection": "p5b_people"}, page)
    assert wait_for(lambda: people.view is not None and people.view.structure_tree.topLevelItemCount() == 1 and
                    people.view.structure_tree.topLevelItem(0).childCount() == 1, 5)
    people.view.structure_tree.setCurrentItem(people.view.structure_tree.topLevelItem(0).child(0))
    assert people.view.remove_btn.isEnabled()
    people.view.remove_selected()
    assert wait_for(lambda: people.view.structure_tree.topLevelItem(0).childCount() == 0, 5)
# --- END [2.3] ---
