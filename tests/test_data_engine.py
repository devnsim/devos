# ==============================================================================
# FILE: tests/test_data_engine.py
# OVERVIEW: Phase 5a of the engine plan - the Data engine: one interface for record collections, user SQLite
#           databases, CSV datasets, workbook sheets and Kùzu graphs; Query v1 run where the data lives; checked
#           writes; changes announced with diffs; Engine.ask.
# PURPOSE:
#   1. The SQL pushdown answers exactly like the in-memory toolkit (same rows, same order, same totals).
#   2. Records: JSON values queried in SQL with expression indexes; formula properties; diffs in events.
#   3. Each file source: query, writes that land in the file, refusals that say why.
# CONTRACT:
#   - Requires: tests/conftest.py fixtures, engines (kernel, runtime, toolkit, data), app.clients.library,
#               app.views, openpyxl, kuzu
#   - Exposes: pytest tests
# NOTES:
#   - [2026-11-17] Created in Phase 5a of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - ROWS / FIELDS: A small table with every awkward case (empties, mixed case, lists, ties) for the
#   pushdown-vs-memory comparison.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. HELPERS
#    1.1 helpers
# 2. TESTS
#    2.1 sql_pushdown
#    2.2 records
#    2.3 sqlite_tables
#    2.4 grids
#    2.5 graphs
#    2.6 kernel_and_views
# ==============================================================================

# ==============================================================================
# [1.1] helpers
# ==============================================================================
import json
import sqlite3

import pytest

from engines.kernel import EngineError

FIELDS = [{"key": "title", "title": "Name", "kind": "text"}, {"key": "status", "title": "Status", "kind": "select"},
          {"key": "budget", "title": "Budget", "kind": "number"}, {"key": "tags", "title": "Tags", "kind": "multi_select"},
          {"key": "due", "title": "Due", "kind": "date"}, {"key": "done", "title": "Done", "kind": "checkbox"}]
ROWS = [
    {"id": 1, "title": "Alpha", "status": "Active", "budget": 10, "tags": ["red", "Blue"], "due": "2026-03-01", "done": True},
    {"id": 2, "title": "beta", "status": "active", "budget": 2.5, "tags": ["green"], "due": "2026-01-15", "done": False},
    {"id": 3, "title": "Gamma", "status": "Blocked", "budget": None, "tags": None, "due": None, "done": False},
    {"id": 4, "title": "delta", "status": None, "budget": 10, "tags": ["blue"], "due": "2026-02-10", "done": True},
    {"id": 5, "title": "Epsilon ray", "status": "Done", "budget": -3, "tags": [], "due": "2026-03-01", "done": False},
]
QUESTIONS = [
    {},
    {"filter": {"field": "status", "op": "eq", "value": "ACTIVE"}},
    {"filter": {"field": "budget", "op": "ge", "value": 5}, "sort": [{"field": "title", "descending": True}]},
    {"filter": {"field": "tags", "op": "eq", "value": "blue"}},
    {"filter": {"field": "tags", "op": "in", "value": ["green", "red"]}},
    {"filter": {"field": "tags", "op": "not_in", "value": ["blue"]}},
    {"filter": {"field": "tags", "op": "empty"}},
    {"filter": {"field": "budget", "op": "ne", "value": 10}},
    {"filter": {"field": "due", "op": "between", "value": ["2026-02-01", "2026-03-01"]}},
    {"filter": {"field": "title", "op": "starts_with", "value": "ep"}},
    {"filter": {"field": "title", "op": "contains", "value": "TA"}},
    {"filter": {"any": [{"field": "done", "op": "eq", "value": True}, {"not": {"field": "status", "op": "not_empty"}}]}},
    {"filter": {"field": "status", "op": "in", "value": ["blocked", "Done"]}},
    {"search": "ALPH"},
    {"sort": [{"field": "budget"}], "offset": 1, "limit": 2},
    {"sort": [{"field": "budget", "descending": True}, {"field": "title"}]},
    {"sort": [{"field": "due"}]},
    {"time_range": {"field": "due", "start": "2026-02-01", "end": None}},
]


def unique(base: str) -> str:
    from app.clients.library import library
    return library().unique_name("database", base)
# --- END [1.1] ---


# ==============================================================================
# [2.1] sql_pushdown
# ==============================================================================
@pytest.mark.parametrize("question", QUESTIONS, ids=[json.dumps(q)[:60] for q in QUESTIONS])
def test_sql_pushdown_answers_like_the_memory_toolkit(question):
    from engines.toolkit.sql import compile_query, json_path_expr, run_compiled
    from engines.toolkit.table import run_query
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE items (id INTEGER, title TEXT, values_json TEXT)")
    for row in ROWS:
        values = {key: value for key, value in row.items() if key not in ("id", "title") and value is not None}
        connection.execute("INSERT INTO items VALUES (?, ?, ?)", (row["id"], row["title"], json.dumps(values)))
    kinds = {field["key"]: field["kind"] for field in FIELDS} | {"id": "integer"}
    columns = {key: json_path_expr("values_json", key) for key in kinds} | {"id": "id", "title": "title"}
    compiled = compile_query(question, columns, kinds, ["title"], "id",
                             [("values_json", lambda needle: json.dumps(needle)[1:-1])])
    found, total = run_compiled(lambda sql, params: connection.execute(sql, params).fetchall(), "items", ("", []),
                                compiled, "id", columns)
    expected, expected_total, _fields = run_query([dict(row) for row in ROWS], FIELDS, question)
    assert [row[0] for row in found] == [row["id"] for row in expected] and total == expected_total


def test_sql_pushdown_groups_and_refuses_bad_questions():
    from engines.toolkit.sql import compile_query, run_compiled
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE t (kind TEXT, qty REAL)")
    connection.executemany("INSERT INTO t VALUES (?, ?)", [("a", 1), ("b", 5), ("a", 2), (None, 7)])
    columns, kinds = {"kind": '"kind"', "qty": '"qty"'}, {"kind": "text", "qty": "number"}
    question = {"group_by": ["kind"], "aggregates": [{"op": "count"}, {"op": "sum", "field": "qty"}],
                "sort": [{"field": "sum_qty", "descending": True}]}
    rows, total = run_compiled(lambda sql, params: connection.execute(sql, params).fetchall(), "t", ("", []),
                               compile_query(question, columns, kinds, []), "*", columns)
    assert total == 3 and [(row["kind"], row["count"], row["sum_qty"]) for row in rows] == [("", 1, 7), ("b", 1, 5), ("a", 2, 3)]
    from engines.toolkit.table import run_query
    fields = [{"key": "kind", "title": "Kind", "kind": "text"}, {"key": "qty", "title": "Qty", "kind": "number"}]
    memory, _total, _fields = run_query([{"id": n, "kind": kind, "qty": qty} for n, (kind, qty) in
                                         enumerate([("a", 1), ("b", 5), ("a", 2), (None, 7)])], fields, question)
    assert [row["id"] for row in memory] == [row["id"] for row in rows]   # both paths agree on groups too
    for bad in ({"filter": {"field": "nope", "op": "eq", "value": 1}}, {"filter": {"field": "qty", "op": "eq", "value": "x"}},
                {"extent": {"field": "qty", "bbox": [0, 0, 1, 1]}}):
        with pytest.raises(EngineError) as error:
            compile_query(bad, columns, kinds, [])
        assert error.value.code == "bad_query"
# --- END [2.1] ---


# ==============================================================================
# [2.2] records
# ==============================================================================
def test_records_query_in_sql_with_indexes_formulas_and_diffs(database):
    from engines.runtime import get_bus
    bus, heard = get_bus(), []
    subscription = bus.subscribe("data.changed", lambda message: heard.append(message.payload))
    properties = [{"key": "status", "title": "Status", "kind": "select"}, {"key": "price", "title": "Price", "kind": "number"},
                  {"key": "qty", "title": "Quantity", "kind": "number"}]
    bus.call("data.register", {"collection": "p5_orders", "noun": "order", "properties": properties})
    made = [bus.call("data.create", {"collection": "p5_orders", "title": title, "values": values})["record"]
            for title, values in (("Bolts", {"status": "Open", "price": 2, "qty": 10}),
                                  ("Nuts", {"status": "Shipped", "price": 0.5, "qty": 100}),
                                  ("Washers", {"status": "Open", "price": 1, "qty": None}))]
    plan = " ".join(str(row[3]) for row in database.fetch_all(
        "EXPLAIN QUERY PLAN SELECT record_id FROM record_items WHERE collection = ? AND "
        "json_extract(values_json, '$.\"status\"') = ?", ("p5_orders", "Open")))
    assert "record_items_value_" in plan                                 # the expression index is used

    query = lambda question: bus.call("data.query", {"collection": "p5_orders", "query": question})   # noqa: E731
    assert [row["title"] for row in query({"filter": {"field": "status", "op": "eq", "value": "open"},
                                           "sort": [{"field": "qty", "descending": True}]})["rows"]] == ["Bolts", "Washers"]
    grouped = query({"group_by": ["status"], "aggregates": [{"op": "sum", "field": "qty"}]})
    assert {row["status"]: row["sum_qty"] for row in grouped["rows"]} == {"Open": 10, "Shipped": 100}

    total = bus.call("data.add_property", {"collection": "p5_orders", "title": "Total", "kind": "formula",
                                           "formula": "=[Price] * [Quantity]"})["field"]
    answer = query({"sort": [{"field": total["key"], "descending": True}]})          # sorts by a computed value
    assert [(row["title"], row[total["key"]]) for row in answer["rows"]] == [("Nuts", 50), ("Bolts", 20), ("Washers", 0)]
    field = next(item for item in answer["fields"] if item["key"] == total["key"])
    assert field["kind"] == "number" and field["read_only"] and field["formula"] == "=[Price] * [Quantity]"
    label = bus.call("data.add_property", {"collection": "p5_orders", "title": "Label", "kind": "formula",
                                           "formula": "=[Name] & \" x\" & [Quantity]"})["field"]
    assert bus.call("data.get", {"collection": "p5_orders", "id": made[0]["id"]})["record"][label["key"]] == "Bolts x10"
    with pytest.raises(EngineError) as computed:
        bus.call("data.update", {"collection": "p5_orders", "id": made[0]["id"], "values": {total["key"]: 3}})
    assert computed.value.code == "not_allowed"
    for formula in ("=[Colour] * 2", "=(1 +"):
        with pytest.raises(EngineError) as bad:
            bus.call("data.add_property", {"collection": "p5_orders", "title": "Bad", "kind": "formula", "formula": formula})
        assert bad.value.code == "invalid_value"

    heard.clear()
    reply = bus.call("data.update", {"collection": "p5_orders", "id": made[0]["id"], "values": {"qty": 12, "status": "Open"}})
    assert reply["changes"]["qty"] == [10, 12] and "status" not in reply["changes"]
    assert reply["changes"][total["key"]] == [20, 24]                     # computed values change too
    event = next(item for item in heard if item["op"] == "update")
    assert event["ids"] == [made[0]["id"]] and event["changes"][str(made[0]["id"])]["qty"] == [10, 12]
    assert bus.call("data.update", {"collection": "p5_orders", "id": made[0]["id"], "values": {"qty": 12}})["changes"] == {}
    bus.call("data.delete", {"collection": "p5_orders", "ids": [row["id"] for row in made]})
    subscription.unsubscribe()
# --- END [2.2] ---


# ==============================================================================
# [2.3] sqlite_tables
# ==============================================================================
def test_user_database_tables_query_and_write_through_data(database):
    from app.clients.library import library
    from engines.runtime import get_bus
    bus, store = get_bus(), library()
    path = store.new_path("database", ".sqlite3")
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE parts (part_id INTEGER PRIMARY KEY, name TEXT NOT NULL, qty INTEGER, price REAL, ok BOOLEAN);
        CREATE VIEW cheap AS SELECT name, price FROM parts WHERE price < 1;
    """)
    connection.executemany("INSERT INTO parts (name, qty, price, ok) VALUES (?, ?, ?, ?)",
                           [(f"Part {n:03}", n, n / 10, n % 2) for n in range(1, 251)])
    connection.commit()
    connection.close()
    item = store.register_path("database", unique("P5 parts"), path)
    collection = f"db:{item.item_id}/parts"
    listed = {entry["collection"]: entry for entry in bus.call("data.collections", {"kinds": ["database"]})["collections"]}
    assert collection in listed and f"db:{item.item_id}/cheap" in listed

    page = bus.call("data.query", {"collection": collection, "query": {
        "filter": {"field": "qty", "op": "between", "value": [100, 200]}, "sort": [{"field": "price", "descending": True}],
        "offset": 10, "limit": 5}})
    assert page["total"] == 101 and [row["qty"] for row in page["rows"]] == [190, 189, 188, 187, 186]
    assert page["key"] == "_rowid" and page["title_key"] == "name" and page["rows"][0]["ok"] is False
    kinds = {field["key"]: field["kind"] for field in page["fields"]}
    assert kinds == {"part_id": "integer", "name": "text", "qty": "integer", "price": "number", "ok": "checkbox"}

    row_id = page["rows"][0]["_rowid"]
    changed = bus.call("data.update", {"collection": collection, "id": row_id, "values": {"qty": "7", "ok": True}})
    assert changed["changes"]["qty"] == [190, 7] and changed["record"]["ok"] is True
    with pytest.raises(EngineError) as wrong:
        bus.call("data.update", {"collection": collection, "id": row_id, "values": {"qty": "seven"}})
    assert wrong.value.code == "invalid_value"
    with pytest.raises(EngineError) as missing_name:
        bus.call("data.create", {"collection": collection, "values": {"qty": 1}})          # name is NOT NULL
    assert missing_name.value.code == "invalid_value"
    created = bus.call("data.create", {"collection": collection, "title": "Widget", "values": {"qty": 3}})
    assert created["record"]["name"] == "Widget" and created["id"] == created["record"]["_rowid"]
    copy = bus.call("data.duplicate", {"collection": collection, "id": created["id"]})
    assert copy["record"]["name"] == "Widget" and copy["record"]["part_id"] != created["record"]["part_id"]
    assert bus.call("data.delete", {"collection": collection, "ids": [created["id"], copy["id"]]})["deleted"] == 2
    bus.call("data.add_property", {"collection": collection, "title": "Supplier", "kind": "text"})
    assert "supplier" not in kinds and "Supplier" in {field["key"] for field in bus.call("data.schema", {"collection": collection})["fields"]}

    with sqlite3.connect(path) as other_tool:                             # e.g. the Database Editor adds a column
        other_tool.execute("ALTER TABLE parts ADD COLUMN bin TEXT")
    assert "bin" in {field["key"] for field in bus.call("data.schema", {"collection": collection})["fields"]}
    with sqlite3.connect(path) as check:                                  # the writes are in the file
        assert check.execute("SELECT qty FROM parts WHERE rowid = ?", (row_id,)).fetchone() == (7,)
    assert store.get(item.item_id).size_bytes == path.stat().st_size      # Knowledge heard about it
    view = f"db:{item.item_id}/cheap"
    assert bus.call("data.query", {"collection": view, "query": {"limit": 3}})["total"] == 9
    with pytest.raises(EngineError) as read_only:
        bus.call("data.delete", {"collection": view, "ids": [1]})
    assert read_only.value.code == "not_allowed"
    with pytest.raises(EngineError) as gone:
        bus.call("data.query", {"collection": f"db:{item.item_id}/nope"})
    assert gone.value.code == "not_found"
# --- END [2.3] ---


# ==============================================================================
# [2.4] grids
# ==============================================================================
def test_csv_datasets_are_tables(database):
    from app.clients.library import library
    from engines.runtime import get_bus
    bus, store = get_bus(), library()
    item = store.create_text("dataset", unique("P5 readings"), "time,value,label\n1,0.5,low\n2,1.5,high\n3,2.5,high\n", ".csv")
    collection = f"csv:{item.item_id}"
    answer = bus.call("data.query", {"collection": collection, "query": {"filter": {"field": "label", "op": "eq", "value": "high"}}})
    assert [row["value"] for row in answer["rows"]] == [1.5, 2.5] and answer["rows"][0]["_rowid"] == 3
    assert {field["key"]: field["kind"] for field in answer["fields"]} == {"time": "integer", "value": "number", "label": "text"}
    bus.call("data.update", {"collection": collection, "id": 3, "values": {"value": 9}})
    bus.call("data.create", {"collection": collection, "values": {"time": 4, "value": 0.25, "label": "low"}})
    bus.call("data.delete", {"collection": collection, "ids": [2]})
    assert store.read_text(item.item_id).splitlines() == ["time,value,label", "2,9,high", "3,2.5,high", "4,0.25,low"]
    store.delete(item.item_id)


def test_workbook_sheets_keep_formulas_computed_and_formatting(database, tmp_path):
    import openpyxl
    from openpyxl.styles import Font

    from app.clients.library import library
    from engines.runtime import get_bus
    bus, store = get_bus(), library()
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Budget"
    sheet.append(["Item", "Cost", "Count", "Total"])
    for row, (name, cost, count) in enumerate((("Desk", 120, 2), ("Lamp", 30, 3)), start=2):
        sheet.append([name, cost, count, f"=B{row}*C{row}"])
    sheet["A2"].font = Font(bold=True)
    source = tmp_path / "budget.xlsx"
    book.save(source)
    item = store.add_file("sheet", source, unique("P5 budget"))
    collection = f"sheet:{item.item_id}/Budget"
    assert collection in {entry["collection"] for entry in bus.call("data.collections", {"kinds": ["sheet"]})["collections"]}
    answer = bus.call("data.query", {"collection": collection, "query": {"sort": [{"field": "Total", "descending": True}]}})
    assert [(row["Item"], row["Total"]) for row in answer["rows"]] == [("Desk", 240), ("Lamp", 90)]
    total = next(field for field in answer["fields"] if field["key"] == "Total")
    assert total["computed"] and total["read_only"] and total["formula"] == "=B2*C2"

    changed = bus.call("data.update", {"collection": collection, "id": 2, "values": {"Count": 5}})
    assert changed["record"]["Total"] == 600 and changed["changes"]["Total"] == [240, 600]
    with pytest.raises(EngineError) as formula:
        bus.call("data.update", {"collection": collection, "id": 2, "values": {"Total": 1}})
    assert formula.value.code == "not_allowed"
    with pytest.raises(EngineError) as shifting:
        bus.call("data.delete", {"collection": collection, "ids": [3]})
    assert shifting.value.code == "not_allowed" and "formulas" in str(shifting.value)
    saved = openpyxl.load_workbook(store.path(item))["Budget"]
    assert saved["C2"].value == 5 and saved["D2"].value == "=B2*C2" and saved["A2"].font.bold   # formatting kept
    store.delete(item.item_id)
# --- END [2.4] ---


# ==============================================================================
# [2.5] graphs
# ==============================================================================
def test_graph_node_tables_and_graph_shape(database):
    from app.clients.library import library
    from engines.kernel import shapes
    from engines.runtime import get_bus
    from engines.toolkit.kuzu_db import GraphDb
    bus, store = get_bus(), library()
    path = store.new_path("graph", ".kuzu")
    db = GraphDb(path)
    db.load_sample_data()
    db.close()
    item = store.register_path("graph", unique("P5 graph"), path)
    collection = f"graph:{item.item_id}/Person"
    listed = {entry["collection"] for entry in bus.call("data.collections", {"kinds": ["graph"]})["collections"]}
    assert {collection, f"graph:{item.item_id}/City"} <= listed
    answer = bus.call("data.query", {"collection": collection, "query": {
        "filter": {"field": "role", "op": "in", "value": ["ENGINEER", "lead"]}, "sort": [{"field": "name"}]}})
    assert [row["name"] for row in answer["rows"]] == ["Ada", "Margaret"] and answer["key"] == "name"
    assert bus.call("data.query", {"collection": collection, "query": {"search": "grac"}})["total"] == 1
    grouped = bus.call("data.query", {"collection": collection, "query": {"group_by": ["role"]}})
    assert grouped["total"] == 6
    assert bus.call("data.update", {"collection": collection, "id": "Ada", "values": {"role": "mathematician"}}
                    )["changes"] == {"role": ["engineer", "mathematician"]}
    with pytest.raises(EngineError) as key:
        bus.call("data.update", {"collection": collection, "id": "Ada", "values": {"name": "Augusta"}})
    assert key.value.code == "not_allowed"
    assert bus.call("data.create", {"collection": collection, "title": "Barbara", "values": {"role": "engineer"}})["id"] == "Barbara"
    bus.call("data.delete", {"collection": collection, "ids": ["Barbara"]})
    with pytest.raises(EngineError) as needs_key:
        bus.call("data.create", {"collection": f"graph:{item.item_id}/City", "values": {"country": "NL"}})
    assert needs_key.value.code == "invalid_value"
    graph = bus.call("data.graph", {"collection": collection})
    assert not shapes.validate(graph) and len(graph["edges"]) >= 10 and {"Person", "City", "Project"} <= {
        node["label"] for node in graph["nodes"]}
# --- END [2.5] ---


# ==============================================================================
# [2.6] kernel_and_views
# ==============================================================================
def test_engines_ask_only_engines_they_declare():
    from engines.kernel import Bus, Engine, EngineManifest, RequestSpec

    class Asker(Engine):
        MANIFEST = EngineManifest("asker", "Asker", "1", "Asks", requests=(RequestSpec("go", "Ask", params=("topic",)),),
                                  uses=("helper",))

        def on_go(self, payload, _job):
            return self.ask(payload["topic"], {"n": 2})

    class Helper(Engine):
        MANIFEST = EngineManifest("helper", "Helper", "1", "Helps", requests=(RequestSpec("double", "Double", params=("n",)),))

        def on_double(self, payload, _job):
            return {"n": payload["n"] * 2}

    bus = Bus()
    bus.register(Asker)
    with pytest.raises(EngineError) as absent:
        bus.call("asker.go", {"topic": "helper.double"}, timeout_s=5)
    assert absent.value.code == "unknown_engine"                         # the helper isn't there: the asker can carry on
    bus.register(Helper)
    assert bus.call("asker.go", {"topic": "helper.double"}, timeout_s=5) == {"n": 4}
    with pytest.raises(EngineError) as undeclared:
        bus.call("asker.go", {"topic": "knowledge.list"}, timeout_s=5)
    assert undeclared.value.code == "undeclared_use"
    bus.shutdown()


def test_engines_watch_engines_they_declare_on_their_own_thread():
    import threading
    import time

    from engines.kernel import Bus, Engine, EngineManifest, RequestSpec

    class Talker(Engine):
        MANIFEST = EngineManifest("talker", "Talker", "1", "Talks", requests=(RequestSpec("say", "Say", params=("n",)),),
                                  events=("said",))

        def on_say(self, payload, _job):
            self.emit("said", {"n": payload["n"]})
            return {}

    class Listener(Engine):
        MANIFEST = EngineManifest("listener", "Listener", "1", "Listens", requests=(RequestSpec("heard", "Heard"),),
                                  uses=("talker",))

        def start(self):
            self.heard, self.threads = [], set()
            self.watch("talker.said", self._said)

        def _said(self, message):
            self.heard.append(message.payload["n"])
            self.threads.add(threading.current_thread().name)

        def on_heard(self, _payload, _job):
            return {"heard": self.heard, "threads": sorted(self.threads)}

    class Nosy(Engine):
        MANIFEST = EngineManifest("nosy", "Nosy", "1", "Watches without saying so")

        def start(self):
            self.watch("talker.said", lambda message: None)

    bus = Bus()
    for engine in (Talker, Listener, Nosy):
        bus.register(engine)
    bus.call("listener.heard", timeout_s=5)                           # started: watching
    for number in range(20):
        bus.call("talker.say", {"n": number}, timeout_s=5)
    deadline = time.time() + 5
    while len(bus.call("listener.heard", timeout_s=5)["heard"]) < 20 and time.time() < deadline:
        time.sleep(0.01)
    answer = bus.call("listener.heard", timeout_s=5)
    assert answer["heard"] == list(range(20)) and answer["threads"] == ["engine:listener"]   # in order, on its thread
    with pytest.raises(EngineError) as nosy:
        bus.call("nosy._health", timeout_s=5)
    assert nosy.value.code == "start_failed" and "doesn't declare" in str(nosy.value)
    bus.shutdown()


def test_data_hears_files_changed_by_other_tools(database, wait_for):
    from app.clients.library import library
    from engines.runtime import get_bus
    bus, heard = get_bus(), []
    subscription = bus.subscribe("data.changed", lambda message: heard.append(message.payload))
    path = library().new_path("database", ".sqlite3")
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE logs (line TEXT)")
    item = library().register_path("database", unique("P5 watched"), path)
    collection = f"db:{item.item_id}/logs"
    bus.call("data.create", {"collection": collection, "title": "by data"})          # Data's own write ...
    with sqlite3.connect(path) as other_tool:
        other_tool.execute("INSERT INTO logs VALUES ('by another tool')")
    library().refresh_size(item.item_id)                              # ... and another tool's, as it reports it
    assert wait_for(lambda: any(event["op"] == "external" and event["collection"] == collection for event in heard), 5)
    assert [event["op"] for event in heard if event["collection"] == collection] == ["create", "external"]
    assert bus.call("data.query", {"collection": collection})["total"] == 2
    subscription.unsubscribe()


def test_saved_record_views_move_to_data_and_file_tables_show_in_views(qapp, database, wait_for):
    from app.clients.library import library
    from app.views.api import make_definition, upgrade_definition
    from app.views.host import ViewHost
    old = make_definition("board", {"engine": "records", "collection": "projects"}, mapping={"group": "status"})
    assert upgrade_definition(old, old)["source"] == {"engine": "data", "collection": "projects"}
    path = library().new_path("database", ".sqlite3")
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE notes (topic TEXT, words INTEGER)")
        connection.execute("INSERT INTO notes VALUES ('first', 3)")
    item = library().register_path("database", unique("P5 view db"), path)
    host = ViewHost(make_definition("table", {"engine": "data", "collection": f"db:{item.item_id}/notes"}))
    assert wait_for(lambda: host.data is not None and host.data.key == "_rowid" and len(host.data.rows) == 1, 5)
    host.create_record({"words": 5}, "second")                          # selects the new row by its reply id
    assert wait_for(lambda: host.data is not None and len(host.data.rows) == 2 and host.selected_id is not None, 5)
    assert host.data.title(host.data.row(host.selected_id)) == "second"
    host.deleteLater()
# --- END [2.6] ---
