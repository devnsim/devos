# ==============================================================================
# FILE: tests/test_knowledge.py
# OVERVIEW: Phase 4 of the engine plan - the Knowledge engine owns the Library: its file service, the view
#           source contract, the note-link graph, the Document and Links views, the Notes and Library modules
#           built on them, the core-engine rule and the check that keeps app/ out of engine internals.
# PURPOSE:
#   1. The done-when: nothing outside Knowledge touches library/ or the content index; everything else asks it
#      (library() client, view requests, change events).
# CONTRACT:
#   - Requires: tests/conftest.py fixtures, engines (kernel, runtime, knowledge over the bus), app.clients.library,
#               app.ui.engine_bridge, app.views, app.modules (notes, library), tools/check_standard.py
#   - Exposes: pytest tests
# NOTES:
#   - [2026-11-17] The Links view draws on GraphCanvas (Phase 5b): clicks go to view.canvas.
#   - [2026-11-17] Phase 5a: records sources are the Data engine's.
#   - [2026-11-17] Created in Phase 4 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - NOTES_SOURCE (dict): The Knowledge engine's notes table, as a view source.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. HELPERS
#    1.1 helpers
# 2. TESTS
#    2.1 file_service
#    2.2 source_contract
#    2.3 links
#    2.4 views
#    2.5 modules
#    2.6 rules
# ==============================================================================

# ==============================================================================
# [1.1] helpers
# ==============================================================================
import sys
from pathlib import Path

import pytest

from engines.kernel import EngineError

ROOT = Path(__file__).resolve().parents[1]
NOTES_SOURCE = {"engine": "knowledge", "collection": "note"}


def rows_of(host) -> dict:
    return {row["title"]: row for row in (host.data.rows if host.data else [])}


def unique(base: str) -> str:
    from app.clients.library import library
    return library().unique_name("note", base)
# --- END [1.1] ---


# ==============================================================================
# [2.1] file_service
# ==============================================================================
def test_file_service_stores_finds_and_announces_changes(qapp, database, wait_for, tmp_path):
    from app.clients.library import LibraryError, library
    from app.ui.engine_bridge import watch_library
    from PySide6.QtCore import QObject
    owner, heard = QObject(), []
    watch_library(owner, heard.append)
    store = library()
    name = unique("Service note")
    note = store.create_text("note", name, "# Service\n\nmentions a zebra\n")
    assert note.extension == ".md" and note.size_bytes > 0
    assert wait_for(lambda: "note" in heard, 5)                           # change events, not listeners
    assert [item.item_id for item in store.search("zebra", kind="note")] == [note.item_id]   # note text is searched
    store.write_text(note.item_id, "# Rewritten")
    assert store.read_text(note.item_id) == "# Rewritten"
    assert store.rename(note.item_id, name + " renamed") == name + " renamed"
    assert store.unique_name("note", name + " renamed") == name + " renamed 2"

    image = store.add_bytes("image", "Service pixels", b"\x89PNG fake", ".png")
    assert store.path(image).read_bytes() == b"\x89PNG fake"
    target = store.new_path("database", ".sqlite3")                      # a tool that writes the file itself
    target.write_bytes(b"SQLite format 3\x00")
    registered = store.register_path("database", "Service db", target)
    assert store.get(registered.item_id).size_bytes == 16
    target.write_bytes(b"SQLite format 3\x00 and more")
    store.refresh_size(registered.item_id)
    assert store.get(registered.item_id).size_bytes == 25
    stray = tmp_path / "stray.sqlite3"
    stray.write_bytes(b"x")
    with pytest.raises(LibraryError, match="new_path"):
        store.register_path("database", "Stray", stray)                  # only paths the engine handed out
    exported = store.export(note.item_id, tmp_path / "out.md")
    assert exported.read_text() == "# Rewritten"
    with pytest.raises(LibraryError, match="Unknown kind"):
        store.create_text("spaceship", "Nope")
    for item in (note, image, registered):
        store.delete(item.item_id)
        assert store.get(item.item_id) is None
    assert not target.exists()
    owner.deleteLater()
# --- END [2.1] ---


# ==============================================================================
# [2.2] source_contract
# ==============================================================================
def test_source_contract_serves_views(database):
    from engines.kernel import shapes
    from engines.runtime import get_bus
    bus = get_bus()
    collections = {entry["collection"]: entry for entry in bus.call("knowledge.collections")["collections"]}
    assert {"library", "note", "dataset"} <= set(collections) and collections["note"]["noun"] == "note"
    schema = bus.call("knowledge.schema", {"collection": "note"})
    assert schema["title_key"] == "title" and all(field["read_only"] for field in schema["fields"])

    created = bus.call("knowledge.create", {"collection": "note", "title": unique("Contract note")})["record"]
    assert created["kind"] == "note"
    with pytest.raises(EngineError) as refused:
        bus.call("knowledge.create", {"collection": "dataset"})
    assert refused.value.code == "not_allowed"
    table = bus.call("knowledge.query", {"collection": "note", "query": {"search": created["title"]}})
    assert not shapes.validate(table) and [row["id"] for row in table["rows"]] == [created["id"]]
    renamed = bus.call("knowledge.update", {"collection": "note", "id": created["id"],
                                            "values": {"title": created["title"] + " (renamed)"}})["record"]
    assert renamed["title"].endswith("(renamed)")
    for values, code in (({"kind": "dataset"}, "not_allowed"), ({"title": "  "}, "invalid_value")):
        with pytest.raises(EngineError) as error:
            bus.call("knowledge.update", {"collection": "note", "id": created["id"], "values": values})
        assert error.value.code == code
    copy = bus.call("knowledge.duplicate", {"collection": "note", "id": created["id"]})["record"]
    assert copy["title"].endswith("(copy)") and copy["id"] != created["id"]
    assert bus.call("knowledge.get", {"collection": "note", "id": copy["id"]})["record"]["title"] == copy["title"]
    gone = bus.call("knowledge.delete", {"collection": "note", "ids": [created["id"], copy["id"]]})
    assert gone["deleted"] == 2
    with pytest.raises(EngineError) as missing:
        bus.call("knowledge.get", {"collection": "note", "id": created["id"]})
    assert missing.value.code == "not_found"
# --- END [2.2] ---


# ==============================================================================
# [2.3] links
# ==============================================================================
def test_note_links_form_a_graph(database):
    from app.clients.library import library
    from engines.kernel import shapes
    from engines.runtime import get_bus
    store = library()
    target_name, source_name, ghost = unique("Link target"), unique("Link source"), "Never written note"
    target = store.create_text("note", target_name, "# Target\n")
    source = store.create_text("note", source_name, f"See [[{target_name}]], [[{target_name}|again]] and [[{ghost}#part]].")
    graph = get_bus().call("knowledge.links")
    assert not shapes.validate(graph)
    edges = {(edge["source"], edge["target"]) for edge in graph["edges"] if edge["source"] == source.item_id}
    assert edges == {(source.item_id, target.item_id), (source.item_id, f"missing:{ghost}")}   # each link once
    assert {"id": f"missing:{ghost}", "title": ghost, "missing": True} in graph["nodes"]
    document = get_bus().call("knowledge.read_text", {"item_id": source.item_id})
    assert not shapes.validate(document) and [link["target"] for link in document["links"]] == [target_name, ghost]
    for item in (target, source):
        store.delete(item.item_id)
# --- END [2.3] ---


# ==============================================================================
# [2.4] views
# ==============================================================================
def test_document_view_edits_saves_and_follows_links(qapp, database, wait_for):
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QWidget

    from app.clients.library import library
    from app.views.api import make_definition
    from app.views.host import ViewHost
    store = library()
    first_name, second_name = unique("Doc first"), unique("Doc second")
    first = store.create_text("note", first_name, f"# First\n\nGo to [[{second_name}]].\n")
    second = store.create_text("note", second_name, "# Second\n")
    page = QWidget()
    page.resize(900, 500)
    host = ViewHost(make_definition("document", NOTES_SOURCE), parent=page)
    page.show()
    assert wait_for(lambda: host.data is not None and first_name in rows_of(host), 5)
    view = host.view
    host.select(first.item_id)
    assert wait_for(lambda: "[[" in view.note_editor.toPlainText(), 5)
    assert second_name in view.note_preview.toPlainText() and "[[" not in view.note_preview.toPlainText()
    view._follow_link(QUrl(f"devos-note:{second_name}"))                 # a [[link]] selects that note
    assert host.selected_id == second.item_id
    assert wait_for(lambda: view.note_editor.toPlainText() == "# Second\n", 5)
    view.note_editor.setPlainText("# Second, typed")
    host.select(first.item_id)                                          # leaving the note saves pending typing
    assert wait_for(lambda: store.read_text(second.item_id) == "# Second, typed", 5)
    assert wait_for(lambda: view.shown_id == first.item_id and "First" in view.note_editor.toPlainText(), 5)

    view._follow_link(QUrl("devos-note:Nowhere at all"))
    assert host.selected_id == first.item_id                            # a missing note leaves the selection

    data = store.create_text("dataset", unique("Doc data"), "a,b\n1,2\n", ".csv")   # other items: read-only text
    library_host = ViewHost(make_definition("document", {"engine": "knowledge", "collection": "library"}), parent=page)
    assert wait_for(lambda: library_host.data is not None and data.name in rows_of(library_host), 5)
    library_host.select(data.item_id)
    assert wait_for(lambda: "1,2" in library_host.view.note_preview.toPlainText(), 5)
    assert library_host.view.note_editor.isHidden() and library_host.view.mode_combo.isHidden()
    for item in (first, second, data):
        store.delete(item.item_id)
    page.deleteLater()


def test_links_view_draws_notes_and_selects_them(qapp, database, wait_for):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QWidget

    from app.clients.library import library
    from app.views.api import make_definition
    from app.views.host import ViewHost
    store = library()
    target_name = unique("Graph target")
    target = store.create_text("note", target_name, "# T\n")
    source = store.create_text("note", unique("Graph source"), f"[[{target_name}]]")
    page = QWidget()
    page.resize(800, 600)
    host = ViewHost(make_definition("links", NOTES_SOURCE), parent=page)
    page.show()
    assert wait_for(lambda: host.view is not None and any(node["id"] == target.item_id for node in host.view.nodes), 5)
    view = host.view
    assert "links" in view.summary_label.text() and view.grab().width() > 0          # paints without errors
    point = view.canvas.point_of(target.item_id)
    press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(point), QPointF(view.mapToGlobal(point.toPoint())),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    view.canvas.mousePressEvent(press)
    assert host.selected_id == target.item_id
    store.delete(target.item_id)
    assert wait_for(lambda: all(node["id"] != target.item_id for node in view.nodes), 5)   # follows changes
    store.delete(source.item_id)
    page.deleteLater()


def test_views_say_when_their_data_cant_serve_them(qapp, database):
    from app.views.api import make_definition
    from app.views.host import ViewHost
    host = ViewHost(make_definition("document", {"engine": "data", "collection": "projects"}))
    assert host.view is None and "read_text" in host.view_message_label.text()
    host.deleteLater()
# --- END [2.4] ---


# ==============================================================================
# [2.5] modules
# ==============================================================================
def test_notes_module_makes_and_remembers_notes(qapp, database, wait_for):
    from app.clients.library import library
    from app.modules.registry import create_module
    notes, context = create_module("notes", database)
    notes.resize(1000, 600)
    notes.show()
    assert {"table", "document"} <= {block.view_type_id for block in notes.block_canvas.blocks()}
    notes.new_note()
    assert wait_for(lambda: notes.note_id is not None and context.settings.get("note_id") == notes.note_id, 5)
    created = library().get(notes.note_id)
    assert created is not None and created.name.startswith("Untitled note")
    table = notes.block_canvas.blocks("table")[0]
    assert wait_for(lambda: created.name in rows_of(table) and table.selected_id == notes.note_id, 5)
    assert wait_for(lambda: notes.count_label.text().endswith(("note", "notes")), 5)
    notes.deleteLater()
    again, _context = create_module("notes", database, context.instance_id)   # the page opened again
    again.resize(1000, 600)
    again.show()
    assert wait_for(lambda: again.note_id == created.item_id, 5)
    library().delete(created.item_id)
    again.deleteLater()


def test_library_module_imports_and_exports(qapp, database, wait_for, tmp_path):
    from app.clients.library import library
    from app.modules.registry import create_module
    module, _context = create_module("library", database)
    module.resize(1000, 600)
    module.show()
    source = tmp_path / "Imported table.csv"
    source.write_text("x,y\n3,4\n")
    assert module.import_files([str(source), str(tmp_path / "missing.csv")]) == 1
    status = module.module_status_label.text()                         # the failure is reported, not hidden
    assert status.startswith("Imported 1 file") and "missing.csv" in status and "no file" in status
    item = next(item for item in library().items("dataset") if item.name == "Imported table")
    table = module.block_canvas.blocks("table")[0]
    assert wait_for(lambda: "Imported table" in rows_of(table), 5)
    module.focus.select(item.item_id)
    module.export_selected(str(tmp_path / "back out.csv"))
    assert (tmp_path / "back out.csv").read_text() == "x,y\n3,4\n" and "Exported" in module.module_status_label.text()
    library().delete(item.item_id)
    module.deleteLater()
# --- END [2.5] ---


# ==============================================================================
# [2.6] rules
# ==============================================================================
def test_knowledge_is_a_core_engine_and_passes_the_kit(qapp, database):
    from engines import runtime
    from engines.kernel.conformance import check_package
    with pytest.raises(EngineError) as refused:
        runtime.detach("knowledge")
    assert refused.value.code == "core" and runtime.engine_state("knowledge")["attached"]
    failed = [f"{check.name}: {check.detail}" for check in check_package(ROOT / "engines" / "knowledge") if not check.ok]
    assert not failed, failed


def test_app_never_imports_engine_internals():
    sys.path.insert(0, str(ROOT / "tools"))
    from check_standard import boundary_errors
    assert boundary_errors("app/modules/notes/view.py", ["from engines.knowledge.store import ContentStore"])
    assert boundary_errors("app/ui/main_window.py", ["import engines.data.engine"])
    assert not boundary_errors("app/clients/library.py", ["from engines.kernel import EngineError",
                                                          "from engines.runtime import get_bus",
                                                          "from engines.toolkit.table import run_query"])
# --- END [2.6] ---
