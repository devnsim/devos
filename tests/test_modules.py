# ==============================================================================
# FILE: tests/test_modules.py
# OVERVIEW: Tests for the record model + views, toolbars / tool bag, and the module apps added with them:
#           Agents, Projects, Spreadsheet, Software Monitor, Graph Database, Map, Comms, CAD, Web App.
# PURPOSE:
#   1. Logic without Qt: records, formulas, workbook files, monitor insights, Kùzu engine, map store,
#      mail parsing, part building.
#   2. Qt: record views stay in sync, toolbars save / restore, every new module starts from the registry.
# CONTRACT:
#   - Requires: tests/conftest.py fixtures (qapp, database, pump, wait_for); kuzu, trimesh, shapely,
#               manifold3d, openpyxl (shell.nix)
#   - Exposes: pytest tests
# NOTES:
#   - [2026-11-16] Created with the records model, toolbars and nine new modules.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - NEW_MODULES: module type ids added in this round (each must start without an error panel).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. LOGIC
#    1.1 records_and_formulas
#    1.2 engines
# 2. QT
#    2.1 record_views
#    2.2 toolbars
#    2.3 modules
# ==============================================================================

import datetime as dt
import json

import pytest

NEW_MODULES = ("agents", "projects", "spreadsheet", "monitor", "graph_db", "map", "comms", "cad", "web_app")


# ==============================================================================
# [1.1] records_and_formulas
# ==============================================================================
def test_record_collection(qapp, database):
    from app.data.store.records import PropertySpec, RecordCollection, coerce_value
    specs = [PropertySpec("status", "Status", "select", ("A", "B")), PropertySpec("due", "Due", "date"),
             PropertySpec("n", "N", "number"), PropertySpec("tags", "Tags", "multi_select")]
    collection = RecordCollection(database, "test_records", specs)
    first = collection.create("First", {"status": "A", "n": 3})
    second = collection.create("Second", {"status": "B", "tags": ["x", "y"]})
    assert [record.title for record in collection.records()] == ["First", "Second"]
    collection.move(second.record_id, first.record_id)
    assert [record.title for record in collection.records()] == ["Second", "First"]
    assert [record.title for record in collection.records(search="x")] == ["Second"]
    assert [record.title for record in collection.records(sort_key="n", descending=True)][0] == "First"
    collection.update(first.record_id, values={"n": None})
    assert "n" not in collection.get(first.record_id).values           # None clears a value
    spec = collection.add_property("Budget €", "number")
    assert spec.key == "budget" and spec.is_user and collection.property("budget") is not None
    collection.remove_property("budget")
    assert collection.property("budget") is None
    assert coerce_value(specs[1], "2026-01-05") == "2026-01-05" and coerce_value(specs[2], "2.0") == 2
    assert coerce_value(specs[3], "a, b,") == ["a", "b"]
    with pytest.raises(ValueError):
        coerce_value(specs[1], "soon")
    assert '"title": "Second"' in collection.to_json()


def test_formulas():
    from app.modules.spreadsheet.engine import Sheet
    from app.modules.spreadsheet.formulas import column_index, column_name, evaluate_expression
    assert evaluate_expression("2*(3+4)^2/7") == 14 and evaluate_expression("ROUND(PI(), 3)") == 3.142
    assert evaluate_expression('IF(2>1, "yes", 1/0)') == "yes"                 # IF skips the unused branch
    assert column_name(27) == "AB" and column_index("AB") == 27
    sheet = Sheet()
    for (row, col), text in {(0, 0): "4", (1, 0): "6", (2, 0): "=SUM(A1:A2)/2", (3, 0): "=A4", (4, 0): "=1/0",
                             (5, 0): "=A5+1", (6, 0): "=NOPE(1)", (7, 0): "=$A$1*2"}.items():
        sheet.set_raw(row, col, text)
    assert sheet.value(2, 0) == 5 and sheet.value(7, 0) == 8
    assert sheet.value(3, 0) == "#CYCLE!" and sheet.value(4, 0) == "#DIV/0!" and sheet.value(5, 0) == "#DIV/0!"
    assert sheet.value(6, 0) == "#NAME?"
    sheet.set_raw(0, 0, "10")
    assert sheet.value(2, 0) == 8                                              # dependants recompute


def test_workbook_files(tmp_path):
    from app.modules.spreadsheet.engine import Sheet, Workbook
    sheet = Sheet("Data")
    sheet.set_raw(0, 0, "x")
    sheet.set_raw(1, 0, "2")
    sheet.set_raw(2, 0, "=A2*3")
    book = Workbook([sheet, Sheet("Other")])
    book.save(tmp_path / "book.xlsx")
    loaded = Workbook.load(tmp_path / "book.xlsx")
    assert [s.name for s in loaded.sheets] == ["Data", "Other"]
    assert loaded.sheets[0].raw(2, 0) == "=A2*3" and loaded.sheets[0].value(2, 0) == 6
    book.save(tmp_path / "book.csv")
    assert Workbook.load(tmp_path / "book.csv").sheets[0].value(2, 0) == 6
# --- END [1.1] ---

# ==============================================================================
# [1.2] engines
# ==============================================================================
def test_monitor_insights():
    from app.modules.monitor.engine import ProcessRow, SystemSample, find_insights
    system = SystemSample(0, 50, [50] * 4, 92, 15 * 2**30, 16 * 2**30, 60, 0, 0, 0, 0, (9.0, 1, 1))
    busy = ProcessRow(10, "spinner", "me", "running", 99, 2**20, 0.1, 4, 0, 0, 0, "spin", [95, 97, 99, 98, 99])
    hog = ProcessRow(11, "hog", "me", "sleeping", 1, 4 * 2**30, 25, 900, 0, 0, 0, "hog", [1] * 5)
    workers = [ProcessRow(100 + i, "worker", "me", "sleeping", 0, 2**20, 0, 1, 0, 0, 0, "w", [0]) for i in range(12)]
    titles = [insight.title for insight in find_insights([busy, hog] + workers, system, own_pid=1)]
    assert titles[0] in ("spinner keeps a core busy", "Memory pressure: swapping")
    assert "hog runs 900 threads" in titles and "12 × worker" in titles and "More work than cores" in titles


def test_graph_db_engine(tmp_path):
    from app.modules.graph_db.engine import GraphDb, describe, split_statements
    assert split_statements("RETURN 'a;b'; RETURN 1") == ["RETURN 'a;b'", "RETURN 1"]
    graph = GraphDb(tmp_path / "g.kuzu")
    graph.load_sample_data()
    graph.load_sample_data()                                  # MERGE / IF NOT EXISTS: running twice is safe
    assert set(graph.node_tables()) == {"Person", "City", "Project"} and graph.count("Person", "NODE") == 6
    sample = graph.graph_sample()
    assert len(sample.nodes) == 12 and len(sample.rels) == 16
    outcomes = graph.run("MATCH (p:Person {name: 'Ada'}) RETURN p; MATCH (x:Nope) RETURN x")
    assert describe(outcomes[0].rows[0][0]).startswith("(:Person") and outcomes[1].error
    graph.close()


def test_map_store_and_parsing(database):
    from app.modules.map.store import MapStore, places_geojson
    from app.modules.map.view import parse_lat_lon
    store = MapStore(database)
    place = store.add("Here", 10.5, -20.25, "note")
    feature = json.loads(places_geojson([place]))["features"][0]
    assert store.get(place.place_id).note == "note" and feature["geometry"]["coordinates"] == [-20.25, 10.5]
    with pytest.raises(ValueError):
        store.add("Nowhere", 91, 0)
    assert parse_lat_lon("48.85, 2.29") == (48.85, 2.29) and parse_lat_lon("200 1") is None


def test_comms_parsing_and_store(database):
    from app.modules.comms.engine import CommsError, CommsStore, MailAccount, MailSummary, build_message, parse_message
    raw = (b"From: A <a@x.com>\r\nSubject: Hi\r\nMIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary=b\r\n\r\n"
           b"--b\r\nContent-Type: text/html\r\n\r\n<p>Hello<br>there</p>\r\n--b--\r\n")
    headers, body = parse_message(raw)
    assert headers["subject"] == "Hi" and body == "Hello\nthere"
    account = MailAccount(0, "Me", "me@x.com", "me", "imap.x.com", 993, "smtp.x.com", 465)
    assert build_message(account, "b@y.com; c@z.com", "S", "body")["To"] == "b@y.com, c@z.com"
    with pytest.raises(CommsError):
        build_message(account, "not-an-address", "S", "body")
    store = CommsStore(database)
    saved = store.save_account(account)
    store.cache_messages(saved.account_id, "INBOX", [MailSummary("7", "a", "s", 1.0, False)])
    store.cache_body(saved.account_id, "INBOX", "7", "text")
    assert store.cached_messages(saved.account_id)[0].is_seen and store.cached_body(saved.account_id, "INBOX", "7") == "text"


def test_cad_part_build():
    from app.modules.cad.engine import CadError, Feature, Part, Shape, Sketch, measure
    part = Part("Plate", [Sketch("S1", 0, [Shape("rect", {"x": 0, "y": 0, "w": 20, "h": 10})])])
    with pytest.raises(CadError):
        Part("Bad", [], [Feature("hole", {"x": 0, "y": 0, "diameter": 2, "depth": 0})]).build()
    part.features.append(Feature("pad", {"sketch": "S1", "height": 5}))
    part.sketches.append(Sketch("S2", 5, [Shape("rect", {"x": 2, "y": 2, "w": 4, "h": 4})]))
    part.features.append(Feature("pocket", {"sketch": "S2", "depth": 2}))
    info = measure(part.build(), 1.0)
    assert info["is_watertight"] and abs(info["volume_mm3"] - (20 * 10 * 5 - 4 * 4 * 2)) < 1
    assert part.build(upto=1).volume == pytest.approx(1000, abs=1)              # rollback to the first feature
    assert len(Part.from_dict(part.to_dict()).build().faces) == len(part.build().faces)
# --- END [1.2] ---


# ==============================================================================
# [2.1] record_views
# ==============================================================================
def test_record_views_stay_in_sync(qapp, database, pump):
    from app.modules.projects.view import ProjectsModule
    module = ProjectsModule()
    module.resize(1200, 700)
    module.show()
    pump()
    record = module.controller.new_record({"status": "Planned", "start": "2026-03-01", "due": "2026-03-10"}, title="Ship")
    pump()
    board = module.block_canvas.blocks("board")[0]
    board.move_card(record.record_id, "Active", None)
    pump()
    assert module.collection.get(record.record_id).values["status"] == "Active"
    timeline = module.block_canvas.blocks("timeline")[0]
    assert any(row[0].record_id == record.record_id for row in timeline.canvas.rows)
    timeline.commit_dates(record.record_id, dt.date(2026, 3, 2), dt.date(2026, 3, 12))
    pump()
    assert module.collection.get(record.record_id).values["due"] == "2026-03-12"
    form = module.block_canvas.blocks("form")[0]
    assert form.shown_id == record.record_id and form.title_edit.text() == "Ship"
    assert not module.controller.set_value(record.record_id, "progress", "lots")   # rejected, reported
    module.deleteLater()


def test_agent_definition(qapp, database):
    from app.data.store.records import Record
    from app.modules.agents.view import agent_definition
    text = agent_definition(Record(1, "Code Reviewer", {"role": "Reviews PRs", "permissions": ["Read files", "Run commands"],
                                                        "model": "claude-sonnet-5-5", "instructions": "Be kind."}))
    assert "name: code-reviewer" in text and "tools: Read, Grep, Glob, Bash" in text and text.rstrip().endswith("Be kind.")
# --- END [2.1] ---

# ==============================================================================
# [2.2] toolbars
# ==============================================================================
def test_toolbars_save_and_restore(qapp, database, pump):
    from app.ui.canvas.module_canvas import ModuleCanvas
    from app.ui.tool_bag import get_tool, search_tools
    canvas = ModuleCanvas(database, "page:toolbar-test")
    canvas.resize(900, 600)
    canvas.show()
    tile = canvas.add_library_module("spreadsheet")
    pump()
    assert [spec.tool_id for spec in search_tools("sum", "spreadsheet")] == ["spreadsheet.sum_below"]
    assert search_tools("sum", None) == []                    # module tools stay out of the global search
    tray = tile.tool_dock.add_tray("top")
    tray.add_tool(get_tool("spreadsheet.sum_below"))
    tray.add_tool(get_tool("timestamp"))
    tile.tool_dock.move_tray(tray, "right")
    tray.set_icon_px(32)
    saved = tile.content_widget.context.settings.get("toolbars")
    assert saved[0]["side"] == "right" and saved[0]["tools"] == ["spreadsheet.sum_below", "timestamp"]
    tile.tool_dock.bind(tile.content_widget)                  # as when the page opens again
    restored = tile.tool_dock.trays[0]
    assert (restored.side, restored.icon_px, restored.tool_ids) == ("right", 32, ["spreadsheet.sum_below", "timestamp"])
    tile.content_widget.set_cells({(0, 0): "2", (1, 0): "3"})
    tile.content_widget.set_selection([(0, 0), (1, 0)])
    restored.buttons[0].click()                               # the tool runs on its module
    assert tile.content_widget.sheet.value(2, 0) == 5
    canvas.deleteLater()
# --- END [2.2] ---

# ==============================================================================
# [2.3] modules
# ==============================================================================
def test_new_modules_start(qapp, database, pump):
    from app.modules.registry import create_module
    for module_type_id in NEW_MODULES:
        view, _context = create_module(module_type_id, database)
        assert view.objectName() != "module_error_label", module_type_id
        view.deleteLater()
    pump()


def test_web_app_container(qapp, database, wait_for):
    from app.modules.registry import create_module
    view, context = create_module("web_app", database)
    view.show()
    view.open_app("data:text/html,<title>Bank</title>ok", "Test container")
    assert wait_for(lambda: view.browser.web_view.title() == "Bank", 10)
    assert context.settings.get("container") == "Test container"
    assert view.browser.web_view.page().profile().storageName() == "devos-test-container"
    view.show_setup()
    assert view.browser is None
    view.deleteLater()
# --- END [2.3] ---
