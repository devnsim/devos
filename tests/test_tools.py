# ==============================================================================
# FILE: tests/test_tools.py
# OVERVIEW: Tests for the activity log, the content library, preferences and the tool modules.
# PURPOSE:
#   1. Activity log: levels, step() success / failure, persistence per session.
#   2. Content library and preferences: add / read / rename / delete; values survive.
#   3. Tools: a new page shows Create New (New Module -> the module dropdown); Graph, Notes, Library and Financial Chart work;
#      a broken module becomes an error panel instead of crashing.
# CONTRACT:
#   - Requires: tests/conftest.py fixtures (qapp, database, pump), app.data, app.modules, app.ui
#   - Exposes: pytest tests
# NOTES:
#   - [2026-11-16] New pages start with the Create New panel (no automatic module tile).
#   - [2026-11-15] Created with the System Log, content library and tools.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - none beyond tests/conftest.py.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. TESTS
#    1.1 activity_log
#    1.2 content_and_preferences
#    1.3 tools
# ==============================================================================

import pytest


# ==============================================================================
# [1.1] activity_log
# ==============================================================================
def test_activity_log_steps_and_persistence(database):
    from app.data.activity_log import ERROR, SUCCESS, ActivityLog
    log = ActivityLog()
    log.attach_database(database)
    with log.step("test", "Do a thing"):
        pass
    with pytest.raises(ValueError):
        with log.step("test", "Break a thing"):
            raise ValueError("boom")
    assert log.entries[-2].level == SUCCESS and "success" in log.entries[-2].message
    assert log.entries[-1].level == ERROR and "boom" in log.entries[-1].message and "ValueError" in log.entries[-1].details
    assert log.flush() == 2
    assert [entry.message for entry in log.load_session(log.session_id)][-1].startswith("Break a thing: FAILED")
# --- END [1.1] ---

# ==============================================================================
# [1.2] content_and_preferences
# ==============================================================================
def test_content_library_and_preferences(database, tmp_path):
    from app.data.store.content import get_content_store
    store = get_content_store()
    note = store.create_text("note", store.unique_name("note", "Test note"), "# hi")
    assert store.read_text(note.item_id) == "# hi"
    store.write_text(note.item_id, "# changed")
    assert store.read_text(note.item_id) == "# changed"
    assert store.rename(note.item_id, "Renamed") == "Renamed"
    outside = tmp_path / "data.csv"
    outside.write_text("x,y\n1,2\n")
    imported = store.add_file("dataset", outside)
    assert store.path(imported).parent.name == "dataset" and store.path(imported).exists()
    store.delete(imported.item_id)
    assert store.get(imported.item_id) is None
    database.set_preference("test_preference", {"a": 1})
    assert database.get_preference("test_preference") == {"a": 1}
# --- END [1.2] ---

# ==============================================================================
# [1.3] tools
# ==============================================================================
def test_new_page_starts_empty_with_create_new(qapp, database, pump, wait_for):
    from app.ui.pages.canvas_page import CanvasPage
    page = CanvasPage(9999, "Blank", database)
    assert not page.module_canvas.tiles and not page.create_new_panel.isHidden() and page.module_canvas.isHidden()
    page.new_module_btn.click()                                # New Module: one empty tile with the dropdown
    tiles = list(page.module_canvas.tiles.values())
    assert len(tiles) == 1 and tiles[0].kind is None and tiles[0].tile_choice_combo.count() >= 6
    assert page.create_new_panel.isHidden() and not hasattr(page.module_canvas, "module_library_bar")
    assert wait_for(lambda: database.load_layout("page:9999") is not None)
    tiles[0].close_tile_btn.click()                            # closing the last module: back to Create New
    assert not page.module_canvas.tiles and not page.create_new_panel.isHidden()
    assert wait_for(lambda: database.load_layout("page:9999") is None)


def test_graph_with_mock_data(qapp, database, pump):
    from app.modules.registry import create_module
    graph, _ = create_module("graph", database)
    graph.open_settings()
    graph.mock_kind_combo.setCurrentText("Sine wave")
    graph._on_create_mock()
    assert graph.dataset is not None and graph.dataset.data.shape == (500, 2)
    assert "500 points" in graph.dataset_label.text()
    graph.settings_window.hide()


def test_notes_autosave(qapp, database, pump, wait_for):
    from app.data.store.content import get_content_store
    from app.modules.registry import create_module
    notes, _ = create_module("notes", database)
    notes.note_editor.setPlainText("# Saved by the test")
    assert wait_for(lambda: "Saved by the test" in get_content_store().read_text(notes.note_id))


def test_financial_chart_settings(qapp, database, pump):
    from app.modules.registry import create_module
    chart, context = create_module("trading_chart", database)
    chart.open_settings()
    chart.theme_combo.setCurrentText("Mono")
    chart.chart_type_combo.setCurrentText("Line")
    chart._apply_settings_from_fields()
    assert (chart.theme_name, chart.chart_type) == ("Mono", "Line")
    assert context.settings.get("theme") == "Mono"
    assert not chart.source_combo.model().item(1).isEnabled()        # live feeds listed but not connected
    chart.settings_window.hide()


def test_broken_module_becomes_error_panel(qapp, database, monkeypatch):
    from app.data.activity_log import get_activity_log
    from app.modules import registry
    broken = registry.ModuleSpec("calendar", "Calendar", "Planning", "", "app.modules.not_a_module:Nope")
    monkeypatch.setitem(registry.MODULE_BY_ID, "calendar", broken)
    view, _ = registry.create_module("calendar", database)
    assert view.objectName() == "module_error_label"
    assert any("Start Calendar: FAILED" in entry.message for entry in get_activity_log().entries)
# --- END [1.3] ---
