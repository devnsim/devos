# ==============================================================================
# FILE: tests/test_ui.py
# OVERVIEW: Headless UI tests: pages, tiling canvases, blocks, title bar and status bar.
# PURPOSE:
#   1. Pages: create / rename everywhere / folders / favourites / delete, and they survive a restart.
#   2. Module canvas and block canvases: split, fill, move, replace, close, and restore with block state.
#   3. Window chrome: window controls follow the top-right pane; the status bar reports.
# CONTRACT:
#   - Requires: tests/conftest.py fixtures (qapp, database, pump), app.ui.main_window, app.modules.registry
#   - Exposes: pytest tests
# NOTES:
#   - [2026-11-15] Created in the health pass from the scratch scripts used while building each feature.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - tree(widget): compact text form of a splitter tree, e.g. "H(V(waterfall, slice), scene)".
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. HELPERS
#    1.1 tree
# 2. TESTS
#    2.1 pages
#    2.2 canvases_and_blocks
#    2.3 window_chrome
# ==============================================================================

import pytest
from PySide6.QtWidgets import QSplitter


# ==============================================================================
# [1.1] tree
# ==============================================================================
def tree(widget) -> str:
    if isinstance(widget, QSplitter):
        orientation = "H" if widget.orientation().value == 1 else "V"
        return f"{orientation}({', '.join(tree(widget.widget(index)) for index in range(widget.count()))})"
    return widget.kind or "empty"
# --- END [1.1] ---


# ==============================================================================
# [2.1] pages
# ==============================================================================
def test_pages_lifecycle(qapp, database, pump):
    from app.ui.main_window import MainWindow
    window = MainWindow()
    window.show()
    pump()
    store, sidebar, tabs = window.page_store, window.nav_sidebar_panel, window.tab_area
    assert [page.title for page in store.favorites()][0] == "Project Editor"

    sidebar.new_page_btn.click()
    pump()
    page_id = tabs.active_tab_group.current_page_index()
    assert store.page(page_id).title.startswith("Untitled")
    window.rename_page(page_id, "Signal Lab")
    tab_bar = tabs.active_tab_group.page_tab_bar
    assert tab_bar.tabText(tab_bar.currentIndex()) == "Signal Lab"
    assert sidebar.pages_tree.find_item("page", page_id).text(0) == "Signal Lab"
    assert window.page_widgets[page_id].page_title_edit.text() == "Signal Lab"
    assert window.windowTitle().endswith("Signal Lab")

    folder_id = store.create_folder("Research")
    store.apply_tree([("folder", folder_id, None, 0), ("page", page_id, folder_id, 0)])
    sidebar.refresh()
    assert store.page(page_id).folder_id == folder_id
    sidebar.delete_folder(folder_id, ask=False)
    assert store.page(page_id).folder_id is None            # folders never delete pages

    split = tabs.split_pane(tabs.active_tab_group, "right")
    split.create_page_btn.click()
    pump()
    created = split.current_page_index()
    sidebar.delete_page(created, ask=False)
    pump(0.3)
    assert store.page(created) is None and not any(group.has_page(created) for group in tabs.tab_groups)
    window.close()

    reopened = MainWindow()
    assert any(page.title == "Signal Lab" for page in reopened.page_store.pages())
    reopened.close()
# --- END [2.1] ---

# ==============================================================================
# [2.2] canvases_and_blocks
# ==============================================================================
@pytest.mark.parametrize("module_id", ["tensor_inspector", "rf_imaging", "wave_sim"])
def test_block_canvas(qapp, database, pump, wait_for, module_id):
    from app.modules.registry import create_module
    view, context = create_module(module_id, database)
    view.resize(1200, 700)
    view.show()
    pump(0.5)
    canvas = view.block_canvas
    default = tree(canvas.root_splitter)
    first = list(canvas.tiles.values())[0]
    new_tile = canvas.split_tile(first, "bottom")
    assert "empty" in tree(canvas.root_splitter)
    new_tile.kind_chosen.emit(canvas.choices[-1][0])
    assert "empty" not in tree(canvas.root_splitter)
    new_tile.kind_chosen.emit(canvas.choices[0][0])          # replace
    assert new_tile.kind == canvas.choices[0][0]
    canvas.close_tile(new_tile)
    assert tree(canvas.root_splitter) == default
    assert wait_for(lambda: context.settings.get("blocks") is not None)      # debounced save
    view.hide()                                               # stop its live timer for later tests
    restored, _ = create_module(module_id, database, context.instance_id)
    assert tree(restored.block_canvas.root_splitter) == default


def test_block_state_survives_restart(qapp, database, pump, wait_for):
    from app.modules.registry import create_module
    view, context = create_module("wave_sim", database)
    view.show()
    pump(0.3)
    waterfall = view.block_canvas.blocks("waterfall")[0]
    waterfall.node_combo.setCurrentIndex(2)
    waterfall.display_combo.setCurrentIndex(1)
    assert wait_for(lambda: "display" in str(context.settings.get("blocks")))
    view.hide()
    restored, _ = create_module("wave_sim", database, context.instance_id)
    restored_waterfall = restored.block_canvas.blocks("waterfall")[0]
    assert (restored_waterfall.node_combo.currentIndex(), restored_waterfall.display_combo.currentIndex()) == (2, 1)


def test_tiles_only_drop_into_their_own_canvas(qapp, database):
    from app.modules.registry import create_module
    first, _ = create_module("tensor_inspector", database)
    second, _ = create_module("tensor_inspector", database)
    assert first.block_canvas.mime_type != second.block_canvas.mime_type
# --- END [2.2] ---

# ==============================================================================
# [2.3] window_chrome
# ==============================================================================
def test_window_controls_follow_top_right_pane(qapp, database, pump):
    from app.ui.main_window import MainWindow
    window = MainWindow()
    window.show()
    pump()
    tabs, controls = window.tab_area, window.window_controls
    right = tabs.split_pane(tabs.tab_groups[0], "right")
    assert controls.parentWidget() is right.tab_bar_panel and controls.isVisible()
    tabs.split_pane(right, "bottom")
    assert controls.parentWidget() is right.tab_bar_panel
    window.system_status_bar.sample()
    assert window.system_status_bar.tiles["cpu"].text().startswith("CPU")
    window.close()
# --- END [2.3] ---
