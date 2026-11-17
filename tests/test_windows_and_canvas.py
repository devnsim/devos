# ==============================================================================
# FILE: tests/test_windows_and_canvas.py
# OVERVIEW: Headless tests for page lifecycle, several windows, Canvas pages and web app options.
# PURPOSE:
#   1. Closed pages stop their web content (discarded web views) and wake when opened again; moving a page
#      to another window never reloads it.
#   2. Tabs open in new windows (tab menu, drag-out, drops between windows); renames reach every window;
#      a closing window parks its pages; a page open elsewhere brings its window forward.
#   3. Canvas pages (infinite canvas): New Canvas, cards snap while moving / resizing, zoom, save + restore,
#      closing a card keeps its data, deleting the page forgets its modules' instances.
#   4. Web apps: browser bar hidden by default, options menu, toggle remembered.
# CONTRACT:
#   - Requires: tests/conftest.py fixtures (qapp, database, pump, wait_for), app.ui.main_window,
#               app.ui.pages.infinite_canvas_page, app.modules.web_app, app.data.store.pages
#   - Exposes: pytest tests
# NOTES:
#   - [2026-11-17] Created with page suspension, multi-window tabs, Canvas pages and web app options.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - TICKING_HTML: a data: page whose script keeps changing its title (a stand-in for a page playing music).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. HELPERS
#    1.1 helpers
# 2. TESTS
#    2.1 page_lifecycle
#    2.2 windows
#    2.3 canvas_pages
#    2.4 web_app_options
# ==============================================================================

# ==============================================================================
# [1.1] helpers
# ==============================================================================
TICKING_HTML = "data:text/html,<title>tick</title><script>setInterval(()=>document.title='t'+Date.now(),50)</script>"


def lifecycle(browser):
    from PySide6.QtWebEngineCore import QWebEnginePage
    state = browser.web_view.page().lifecycleState()
    return {QWebEnginePage.LifecycleState.Active: "active", QWebEnginePage.LifecycleState.Frozen: "frozen",
            QWebEnginePage.LifecycleState.Discarded: "discarded"}[state]


def new_window(pump):
    from app.ui.main_window import MainWindow
    window = MainWindow()
    window.resize(1100, 700)
    window.show()
    pump()
    return window
# --- END [1.1] ---


# ==============================================================================
# [2.1] page_lifecycle
# ==============================================================================
def test_closed_pages_stop_their_web_content(qapp, database, pump, wait_for):
    window = new_window(pump)
    browser = window.open_browser_tab(TICKING_HTML)
    page_id = browser.page_id
    assert wait_for(lambda: browser.web_view.title().startswith("t1"), 10)
    assert lifecycle(browser) == "active"

    window.tab_area.close_page(page_id)                        # every tab of it closed: it must go quiet
    assert wait_for(lambda: lifecycle(browser) == "discarded", 3)
    assert window.page_widgets[page_id] is browser             # parked, not deleted

    window.show_page(page_id)                                  # opened again: it wakes and reloads
    assert wait_for(lambda: lifecycle(browser) == "active", 3)
    assert wait_for(lambda: browser.web_view.title().startswith("t1"), 10)

    second = window.workspace.open_in_new_window(page_id, window)   # a move is not a close: no reload
    pump(0.3)
    assert lifecycle(browser) == "active" and second.tab_area.page_indices() == [page_id]
    assert page_id not in window.tab_area.page_indices()
    second.close()                                             # a closing window parks (and stops) its pages
    assert wait_for(lambda: lifecycle(browser) == "discarded", 3)
    assert second not in window.workspace.windows
    window.close()
# --- END [2.1] ---


# ==============================================================================
# [2.2] windows
# ==============================================================================
def test_tabs_move_between_windows(qapp, database, pump, wait_for):
    from app.ui.widgets.tiling_layout import DROP_ZONE_CENTER
    window = new_window(pump)
    workspace = window.workspace
    first = window.create_page()
    second = window.create_page()
    pump()
    pane = window.tab_area.active_tab_group

    pane.new_window_requested.emit(first, False)              # tab menu: "Open in new window"
    assert wait_for(lambda: len(workspace.windows) == 2)
    other = workspace.windows[1]
    assert other.tab_area.page_indices() == [first] and first not in window.tab_area.page_indices()
    assert other.nav_sidebar_panel.pages_tree.topLevelItemCount() == window.nav_sidebar_panel.pages_tree.topLevelItemCount()

    window.rename_page(first, "Moved page")                    # renames reach the other window's tab
    other_pane = other.tab_area.active_tab_group
    assert other_pane.page_tab_bar.tabText(0) == "Moved page"

    window.show_page(first)                                    # open elsewhere: that window comes forward
    assert first in other.tab_area.page_indices() and first not in window.tab_area.page_indices()

    other_pane.page_dropped.emit(second, DROP_ZONE_CENTER, -1)   # a tab dragged in from the first window
    pump()
    assert set(other.tab_area.page_indices()) == {first, second}
    assert second not in window.tab_area.page_indices()

    other_pane.page_tab_bar.dragged_out.emit(second)          # dropped outside every window: a third window
    assert wait_for(lambda: len(workspace.windows) == 3)
    assert workspace.windows[2].tab_area.page_indices() == [second]
    assert workspace.window_with_page(second) is workspace.windows[2]

    workspace.windows[2].nav_sidebar_panel.delete_page(second, ask=False)   # its tab closes everywhere
    pump()
    assert workspace.window_with_page(second) is None and second not in workspace.page_widgets
    for each in list(workspace.windows):
        each.close()
# --- END [2.2] ---


# ==============================================================================
# [2.3] canvas_pages
# ==============================================================================
def test_canvas_page_cards_snap_save_and_restore(qapp, database, pump, wait_for):
    from PySide6.QtCore import QPoint

    from app.data.store.pages import PAGE_KIND_INFINITE_CANVAS, _instance_ids, canvas_key
    from app.ui.pages.infinite_canvas_page import GRID_PX, InfiniteCanvasPage
    window = new_window(pump)
    page_id = window.create_page()
    pump()
    window.page_widget(page_id).new_canvas_btn.click()         # Create New → New Canvas: same tab, new kind
    pump()
    page = window.page_widgets[page_id]
    assert isinstance(page, InfiniteCanvasPage) and window.page_store.page(page_id).kind == PAGE_KIND_INFINITE_CANVAS
    assert window.page_icon(page_id) == "canvas.svg"
    canvas = page.infinite_canvas
    canvas.resize(1000, 600)

    notes = canvas.add_card("notes", cell=(3, 4), size=(20, 12))
    empty = canvas.add_card(cell=(3, 4))                       # same spot: steps aside, shows the picker
    assert canvas.cells[empty.tile_id][:2] == [5, 6] and empty.kind is None
    assert notes.geometry() == canvas.screen_rect([3, 4, 20, 12])

    header = notes.card_header_panel                           # move: whole cells only
    header.started.emit()
    header.dragged.emit(QPoint(int(GRID_PX * 2.4), int(GRID_PX * -0.6)))
    header.finished.emit()
    assert canvas.cells[notes.tile_id] == [5, 3, 20, 12] and canvas.cards[-1] is notes   # moved + in front
    grip = notes.card_resize_grip                              # resize: snapped, never below the minimum
    grip.started.emit()
    grip.dragged.emit(QPoint(-10_000, GRID_PX * 3))
    grip.finished.emit()
    assert canvas.cells[notes.tile_id] == [5, 3, canvas.MIN_CARD_CELLS[0], 15]

    canvas.zoom_to(0.5, anchor=canvas.screen_rect([0, 0, 1, 1]).topLeft().toPointF())
    assert notes.geometry() == canvas.screen_rect(canvas.cells[notes.tile_id])
    assert abs(notes.width() - canvas.MIN_CARD_CELLS[0] * GRID_PX * 0.5) <= 1

    empty.kind_chosen.emit("calendar")                         # the picker fills a card
    canvas.save()
    saved = database.load_layout(canvas_key(page_id))
    assert [card["kind"] for card in saved["cards"]] == ["calendar", "notes"] and saved["view"]["zoom"] == 0.5
    instance_ids = _instance_ids(saved)
    assert len(instance_ids) == 2

    restored = InfiniteCanvasPage(page_id, "again", database)   # a restart rebuilds the same canvas
    again = restored.infinite_canvas
    assert [card.kind for card in again.cards] == ["calendar", "notes"] and again.zoom == 0.5
    assert [again.cells[card.tile_id] for card in again.cards] == [canvas.cells[card.tile_id] for card in canvas.cards]
    assert [card.instance_id for card in again.cards] == [card.instance_id for card in canvas.cards]

    canvas.close_card(notes)                                   # closing a card keeps the module's data
    canvas.save()
    assert [card["kind"] for card in database.load_layout(canvas_key(page_id))["cards"]] == ["calendar"]
    window.close()
# --- END [2.3] ---


# ==============================================================================
# [2.4] web_app_options
# ==============================================================================
def test_web_app_hides_the_browser_bar_and_offers_options(qapp, database, pump, wait_for):
    from PySide6.QtWidgets import QMenu

    from app.modules.registry import create_module
    from app.modules.web_app.view import OWN_CONTAINER
    view, context = create_module("web_app", database)
    view.resize(800, 500)
    view.show()
    view.open_app("data:text/html,<title>App</title><p>hello</p>", OWN_CONTAINER)
    pump()
    browser = view.browser
    assert browser.browser_toolbar_panel.isHidden() and not view.options_btn.isHidden()   # looks like part of DevOS

    menu = QMenu()
    view.fill_options_menu(menu)
    titles = [action.text() for action in menu.actions()]
    assert titles[0] == "Show browser bar" and not menu.actions()[0].isChecked()
    assert "Back  (Alt+Left)" in titles and "Change site or container…" in titles and "Use as home page" not in titles
    page_menu = QMenu()
    view._extend_page_menu(page_menu)                          # right-click on the page: a "Web app" submenu
    assert page_menu.actions()[-1].menu() is not None and page_menu.actions()[-1].text() == "Web app"

    menu.actions()[0].trigger()                                # turn the bar on: remembered with the tile
    assert not browser.browser_toolbar_panel.isHidden() and view.options_btn.isHidden()
    assert view.setting("show_browser_bar") is True
    reopened, _context = create_module("web_app", database, context.instance_id)
    reopened.show()
    pump()
    assert not reopened.browser.browser_toolbar_panel.isHidden()
    view.set_browser_bar_visible(False)
    assert view.setting("show_browser_bar") is False
    view.close()
    reopened.close()
# --- END [2.4] ---
