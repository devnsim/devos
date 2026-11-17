# ==============================================================================
# FILE: tests/test_views.py
# OVERVIEW: Phase 2 of the engine plan - data shapes, Query v1, the shape toolkits, the records engine, and
#           the view runtime (ViewHost, channels, shared subscriptions, view builder, Data view module).
# PURPOSE:
#   1. Every shape builds and validates; big tables travel as Arrow and come back intact.
#   2. Queries are validated and run (filter tree, search, time range, group + aggregate, sort, paging).
#   3. The records engine checks values, announces changes, keeps declared properties.
#   4. The phase's "done when": Projects runs entirely through ViewHosts; the builder makes a filtered board;
#      views on a page share a selection and an unlinked one doesn't follow; two views asking the same question
#      cost one engine request; old block settings survive; a Data view works as a page tile.
# CONTRACT:
#   - Requires: tests/conftest.py fixtures, engines (kernel, toolkit, records), app.views, app.modules
#   - Exposes: pytest tests
# NOTES:
#   - [2026-11-17] Record collections are the Data engine's now (Phase 5a).
#   - [2026-11-17] Created in Phase 2 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - FIELDS: A small field list (select, date, number, multi-select) the toolkit tests share.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. HELPERS
#    1.1 helpers
# 2. TESTS
#    2.1 shapes
#    2.2 query_and_toolkits
#    2.3 records_engine
#    2.4 view_runtime
#    2.5 builder_and_data_view
# ==============================================================================

# ==============================================================================
# [1.1] helpers
# ==============================================================================
import numpy as np
import pytest

FIELDS = [{"key": "status", "title": "Status", "kind": "select", "options": ["A", "B"]},
          {"key": "due", "title": "Due", "kind": "date"}, {"key": "n", "title": "N", "kind": "number"},
          {"key": "tags", "title": "Tags", "kind": "multi_select"}]
ROWS = [{"id": i, "title": f"row {i}", "status": "AB"[i % 2], "due": f"2026-03-{i + 1:02d}", "n": i,
         "tags": ["x"] if i % 3 == 0 else []} for i in range(10)]
SOURCE = {"engine": "data", "collection": "projects"}


def project_rows(host) -> dict:
    return {row["title"]: row for row in (host.data.rows if host.data else [])}
# --- END [1.1] ---


# ==============================================================================
# [2.1] shapes
# ==============================================================================
def test_every_shape_builds_and_validates():
    from engines.kernel import shapes
    built = [
        shapes.table(FIELDS, ROWS), shapes.series([{"key": "v", "title": "V", "kind": "number"}], [0.0, 1.0], {"v": [1, 2]}),
        shapes.geo([{"key": "pop", "title": "Population", "kind": "integer"}],
                   [{"id": "06001", "geometry": {"type": "Point", "coordinates": [-122.2, 37.8]}, "pop": 1600000}]),
        shapes.tensor(np.zeros((4, 3, 2, 2)), ["t", "x", "y", "z"]), shapes.image(np.zeros((8, 6, 3), dtype=np.uint8)),
        shapes.image(encoded=b"\x89PNG", format="png", geo_transform=[0, 1, 0, 0, 0, -1], crs="EPSG:3857"),
        shapes.point_cloud({"x": [0.0, 1.0], "y": [0.0, 1.0], "z": [5.0, 6.0], "intensity": [10, 20]}, crs="EPSG:26910"),
        shapes.geometry(np.zeros((3, 3)), np.array([[0, 1, 2]])),
        shapes.graph([], [{"id": 1}], [], [{"id": "e", "source": 1, "target": 1}]),
        shapes.document("# Hi", links=[{"target": "note:2", "label": "two"}]),
        shapes.thread([{"id": 1, "role": "user", "author": "me", "text": "hello", "time": 0.0}]),
    ]
    assert sorted({payload["shape"] for payload, _attachments in built}) == sorted(shapes.SHAPES)
    for payload, attachments in built:
        assert shapes.validate(payload, attachments) == [], payload["shape"]
    assert shapes.validate({"shape": "series", "version": 1, "channels": [], "count": 0}) == ["series: missing attachment “data”"]
    assert shapes.validate({"shape": "lidar"}) == ["unknown shape “lidar”"]
    with pytest.raises(ValueError, match="x, y, z"):
        shapes.point_cloud({"x": [1.0]})


def test_big_tables_travel_as_arrow_and_come_back_intact():
    from engines.kernel import shapes
    rows = ROWS * 300
    payload, attachments = shapes.table(FIELDS, rows)
    assert payload["encoding"] == "arrow" and "rows" not in payload and isinstance(attachments["rows"], bytes)
    assert shapes.table_rows(payload, attachments) == rows               # lists (multi-select) survive too
    small, small_attachments = shapes.table(FIELDS, ROWS[:3])
    assert small["encoding"] == "json" and small_attachments == {}
# --- END [2.1] ---


# ==============================================================================
# [2.2] query_and_toolkits
# ==============================================================================
def test_queries_are_validated_and_run_by_the_table_toolkit():
    from engines.kernel import EngineError
    from engines.kernel.query import all_of, any_of, condition, not_, query, query_key, validate_query
    from engines.toolkit.table import run_query
    page, total, _fields = run_query(ROWS, FIELDS, query(filter=all_of(condition("status", "eq", "A"), condition("n", "ge", 2)),
                                                        sort=[("n", True)], limit=2))
    assert [row["id"] for row in page] == [8, 6] and total == 4
    assert run_query(ROWS, FIELDS, query(filter=condition("tags", "contains", "x")))[1] == 4
    assert run_query(ROWS, FIELDS, query(filter=not_(any_of(condition("n", "lt", 3), condition("n", "gt", 7)))))[1] == 5
    assert run_query(ROWS, FIELDS, query(search="ROW 1"))[1] == 1
    assert run_query(ROWS, FIELDS, query(time_range={"field": "due", "start": "2026-03-03", "end": "2026-03-05"}))[1] == 3
    grouped, groups, group_fields = run_query(ROWS, FIELDS, query(group_by=["status"], aggregates=[{"op": "count"},
                                                                                                  {"op": "avg", "field": "n"}]))
    assert groups == 2 and grouped[0] == {"id": "A", "status": "A", "count": 5, "avg_n": 4.0}
    assert [field["key"] for field in group_fields] == ["status", "count", "avg_n"]
    assert validate_query({"filter": {"field": "zz", "op": "eq", "value": 1}, "limit": -1}, {"n"}) == [
        "unknown field “zz”", "“limit” must be a whole number ≥ 0"]
    with pytest.raises(EngineError, match="Bad query"):
        run_query(ROWS, FIELDS, {"sort": [{"field": "nope"}]})
    assert query_key(SOURCE, query(limit=1, search="a")) == query_key(dict(reversed(SOURCE.items())), {"search": "a", "limit": 1})


def test_series_toolkit_windows_resamples_and_keeps_peaks():
    from engines.toolkit import series
    times = np.linspace(0, 10, 10_001)
    values = np.sin(times)
    values[5_000] = 9.0
    t, v = series.window(times, values, 2.0, 3.0)
    assert t[0] == pytest.approx(2.0) and t[-1] == pytest.approx(3.0)
    small_t, small_v = series.decimate(times, values, 400)
    assert len(small_t) <= 400 and small_v.max() == 9.0 and np.all(np.diff(small_t) > 0)
    step_t, step_v = series.resample([0, 0.5, 1.2, 2.9], [1, 3, 5, 7], 1.0)
    assert list(step_t) == [0.0, 1.0, 2.0] and list(step_v) == [2.0, 5.0, 7.0]
# --- END [2.2] ---


# ==============================================================================
# [2.3] records_engine
# ==============================================================================
def test_records_engine_checks_values_and_announces_changes(database):
    from engines.kernel import EngineError, shapes
    from engines.runtime import get_bus
    bus = get_bus()
    heard = []
    subscription = bus.subscribe("data.changed", lambda message: heard.append(message.payload))
    properties = [{"key": "stage", "title": "Stage", "kind": "select", "options": ["Draft"]},
                  {"key": "budget", "title": "Budget", "kind": "number"}]
    bus.call("data.register", {"collection": "phase2_items", "noun": "item", "properties": properties})
    first = bus.call("data.create", {"collection": "phase2_items", "title": "One", "values": {"budget": "12.5", "stage": "Live"}})
    assert first["record"]["budget"] == 12.5
    with pytest.raises(EngineError, match="Budget: “lots” is not a number"):
        bus.call("data.update", {"collection": "phase2_items", "id": first["record"]["id"], "values": {"budget": "lots"}})
    with pytest.raises(EngineError, match="name can't be empty"):
        bus.call("data.update", {"collection": "phase2_items", "id": first["record"]["id"], "values": {"title": " "}})
    with pytest.raises(EngineError) as unknown:
        bus.call("data.query", {"collection": "no_such_thing"})
    assert unknown.value.code == "not_found"
    reply = bus.request("data.query", {"collection": "phase2_items"}).result(5)
    stage = next(field for field in reply.payload["fields"] if field["key"] == "stage")
    assert stage["options"] == ["Draft", "Live"] and shapes.validate(reply.payload, reply.attachments) == []
    assert reply.payload["source"] == {"engine": "data", "collection": "phase2_items", "noun": "item"}
    with pytest.raises(EngineError) as builtin:
        bus.call("data.remove_property", {"collection": "phase2_items", "key": "budget"})
    assert builtin.value.code == "not_allowed"
    added = bus.call("data.add_property", {"collection": "phase2_items", "title": "Owner", "kind": "text"})["field"]
    bus.call("data.remove_property", {"collection": "phase2_items", "key": added["key"]})
    assert any(event["collection"] == "phase2_items" and event["ids"] == [first["record"]["id"]] and event["op"] == "create"
               and not event["schema"] for event in heard)
    assert any(event["schema"] for event in heard)
    subscription.unsubscribe()
# --- END [2.3] ---


# ==============================================================================
# [2.4] view_runtime
# ==============================================================================
def test_projects_runs_through_view_hosts_sharing_one_subscription(qapp, database, pump, wait_for):
    from app.modules.registry import create_module
    from app.views.host import ViewHost
    from app.views.subscriptions import shared_queries
    module, _context = create_module("projects", database)
    module.resize(1200, 700)
    module.show()
    hosts = module.block_canvas.blocks()
    assert hosts and all(isinstance(host, ViewHost) for host in hosts)
    assert not hasattr(module, "collection") and not hasattr(module, "controller")     # no back door to the data
    module.create_record({"status": "Active", "start": "2026-04-01"}, "Runtime one")
    board, form = module.block_canvas.blocks("board")[0], module.block_canvas.blocks("form")[0]
    assert wait_for(lambda: "Runtime one" in project_rows(board) and "Runtime one" in project_rows(form), 5)
    assert board._key == form._key                                     # same question: one shared answer

    queries = shared_queries()
    before = queries.request_count
    twin_a = ViewHost(board.block_state())
    twin_b = ViewHost(board.block_state() | {"view_type": "table"})
    other = ViewHost(board.block_state() | {"query": {"search": "Runtime"}})
    assert wait_for(lambda: twin_a.data is not None and twin_b.data is not None and other.data is not None, 5)
    assert queries.request_count - before == 1                         # the twins reused the board's answer; only `other` asked

    page = module.block_canvas.blocks("timeline")[0].parentWidget()
    linked = ViewHost(board.block_state() | {"view_type": "table"}, parent=module)
    unlinked = ViewHost(board.block_state() | {"view_type": "table", "channels": {"selection": "unlinked"}}, parent=module)
    record_id = project_rows(board)["Runtime one"]["id"]
    board.select(record_id)
    assert linked.selected_id == record_id and form.selected_id == record_id and unlinked.selected_id is None
    assert wait_for(lambda: form.view.shown_id == record_id, 5)
    unlinked.select(None)
    assert board.selected_id == record_id                              # an unlinked view never moves the page
    unlinked.set_linked(True)
    assert unlinked.selected_id == record_id and page is not None
    for host in (twin_a, twin_b, other, linked, unlinked):
        host.deleteLater()
    module.deleteLater()


def test_view_definitions_are_saved_and_old_block_settings_survive(qapp, database, pump, wait_for):
    from app.modules.registry import create_module
    from app.views.api import make_definition, upgrade_definition
    board_definition = upgrade_definition({"group_key": "priority"}, make_definition("board", SOURCE))
    assert board_definition["mapping"] == {"group": "priority"}
    table_definition = upgrade_definition({"sort_key": "due", "descending": True}, make_definition("table", SOURCE))
    assert table_definition["query"]["sort"] == [{"field": "due", "descending": True}]
    timeline = upgrade_definition({"start_key": "start", "end_key": "due", "day_px": 20}, make_definition("timeline", SOURCE))
    assert timeline["mapping"] == {"start": "start", "end": "due"} and timeline["presentation"] == {"day_px": 20}

    module, context = create_module("projects", database)
    module.show()
    board = module.block_canvas.blocks("board")[0]
    assert wait_for(lambda: board.data is not None, 5)
    board.set_mapping("group", "priority")                             # e.g. the board's own Group by combo
    saved = lambda: [child for child in [module.setting(module.BLOCKS_SETTING) or {}] if child]   # noqa: E731
    assert wait_for(lambda: '"group": "priority"' in str(saved()).replace("'", '"'), 5)
    again, _context = create_module("projects", database, context.instance_id)
    assert again.block_canvas.blocks("board")[0].mapping_for("group") == "priority"
    module.deleteLater()
    again.deleteLater()
# --- END [2.4] ---


# ==============================================================================
# [2.5] builder_and_data_view
# ==============================================================================
def test_builder_makes_a_filtered_board_and_a_data_view_shows_it(qapp, database, pump, wait_for):
    from app.modules.projects.view import ProjectsModule
    from app.modules.registry import create_module
    from app.views.api import make_definition
    from app.views.builder import ViewBuilderDialog
    module = ProjectsModule()
    module.resize(1200, 700)
    module.show()
    module.create_record({"status": "Blocked"}, "Builder blocked")
    module.create_record({"status": "Done"}, "Builder done")
    board = module.block_canvas.blocks("board")[0]
    assert wait_for(lambda: {"Builder blocked", "Builder done"} <= set(project_rows(board)), 5)

    dialog = ViewBuilderDialog(None, make_definition("table", SOURCE))
    assert wait_for(lambda: dialog.fields and not dialog._loading, 5)
    board_item = next(dialog.type_list.item(i) for i in range(dialog.type_list.count())
                      if dialog.type_list.item(i).data(0x0100) == "board")
    dialog.type_list.setCurrentItem(board_item)
    assert list(dialog.slot_combos) == ["group"] and dialog.slot_combos["group"].currentData() == "status"
    condition_row = dialog.filter_bar.add_row({"field": "status", "op": "eq", "value": "Blocked"})
    assert condition_row.condition() == {"field": "status", "op": "eq", "value": "Blocked"}
    dialog.title_edit.setText("Blocked work")
    definition = dialog.definition()
    assert definition["view_type"] == "board" and definition["title"] == "Blocked work"
    assert definition["query"]["filter"] == {"all": [{"field": "status", "op": "eq", "value": "Blocked"}]}
    assert wait_for(lambda: dialog.preview.data is not None and
                    {row["status"] for row in dialog.preview.data.rows} == {"Blocked"}, 5)
    dialog.deleteLater()

    tile = module.block_canvas.add_tile(definition["view_type"], beside=module.block_canvas.active_tile, state=definition)
    new_board = tile.content_widget
    assert wait_for(lambda: new_board.data is not None and set(project_rows(new_board)) >= {"Builder blocked"}, 5)
    assert all(row["status"] == "Blocked" for row in new_board.data.rows) and not new_board.filter_bar.isHidden()

    data_view, context = create_module("data_view", database)          # the same view as a page tile / Canvas card
    data_view.show()
    assert data_view.host is None and not data_view.empty_panel.isHidden()
    data_view._show(definition)
    assert wait_for(lambda: data_view.host.data is not None and "Builder blocked" in project_rows(data_view.host), 5)
    data_view.host.set_mapping("group", "priority")
    assert wait_for(lambda: (data_view.setting("definition") or {}).get("mapping", {}).get("group") == "priority", 5)
    reopened, _context = create_module("data_view", database, context.instance_id)
    assert reopened.host is not None and reopened.host.definition["query"]["filter"] == definition["query"]["filter"]
    for widget in (module, data_view, reopened):
        widget.deleteLater()
# --- END [2.5] ---
