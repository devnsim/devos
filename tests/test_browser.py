# ==============================================================================
# FILE: tests/test_browser.py
# OVERVIEW: Tests for the tab row's +, empty pages turning into browser pages, and browser behaviour.
# PURPOSE:
#   1. address_to_url(): URLs, host names, local addresses and searches.
#   2. The + after the last tab creates a page in that pane; New Browser converts the page in place.
#   3. Browser pages follow the website title until renamed, open new-window links as new pages, keep
#      their state, and survive clicks through the window mouse hub (the PySide / Qt WebEngine crash).
# CONTRACT:
#   - Requires: tests/conftest.py fixtures (qapp, database, pump, wait_for), app.ui (main_window, web_support)
#   - Exposes: pytest tests
# NOTES:
#   - [2026-11-16] Created with browser pages and the tab row's +.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - PAGE_HTML (str): A data: URL page with a title and a link that opens a new window.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. TESTS
#    1.1 address_bar
#    1.2 tabs_and_browser_pages
# ==============================================================================

PAGE_HTML = ("data:text/html,<title>Hello DevOS</title>"
             "<a id=a href='data:text/html,<title>Two</title>' target=_blank style='font-size:80px'>open</a>")


# ==============================================================================
# [1.1] address_bar
# ==============================================================================
def test_address_to_url():
    from app.ui.web_support import address_to_url
    assert address_to_url("example.com") == "https://example.com"
    assert address_to_url("localhost:8000/x") == "http://localhost:8000/x"
    assert address_to_url("192.168.1.4") == "http://192.168.1.4"
    assert address_to_url("https://x.org/a?b=1") == "https://x.org/a?b=1"
    assert address_to_url("how do magnets work").endswith("?q=how+do+magnets+work")
# --- END [1.1] ---

# ==============================================================================
# [1.2] tabs_and_browser_pages
# ==============================================================================
def test_tab_plus_and_browser_pages(qapp, database, pump, wait_for):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from app.ui.main_window import MainWindow
    from app.ui.pages.browser_page import BrowserPage
    window = MainWindow()
    window.resize(1100, 700)
    window.show()
    pump()
    tabs = window.tab_area
    pane = tabs.active_tab_group
    before = pane.tab_count()
    assert pane.new_tab_btn.geometry().left() >= pane.page_tab_bar.geometry().right()   # + right after the last tab
    pane.new_tab_btn.click()
    pump()
    page_id = pane.current_page_index()
    assert pane.tab_count() == before + 1 and window.page_store.page(page_id).title.startswith("Untitled")
    page = window.page_widget(page_id)
    assert not page.create_new_panel.isHidden() and not page.module_canvas.tiles

    page.new_browser_btn.click()                               # New Browser: same tab, now a browser
    pump()
    browser = window.page_widgets[page_id]
    assert isinstance(browser, BrowserPage) and pane.current_page_index() == page_id
    assert window.page_store.page(page_id).kind == "browser" and window.page_icon(page_id) == "globe.svg"
    browser.navigate(PAGE_HTML)
    assert wait_for(lambda: window.page_title(page_id) == "Hello DevOS", 10)
    assert pane.page_tab_bar.tabText(pane.page_tab_bar.currentIndex()) == "Hello DevOS"

    # A real click inside the page goes through the window mouse hub (an app-wide filter crashed here).
    center = browser.web_view.mapTo(window, browser.web_view.rect().center())
    QTest.mouseClick(window.windowHandle(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
    pump(0.3)
    assert tabs.active_tab_group is pane

    pages_before = len(window.page_store.pages())
    browser.web_view.page().runJavaScript("document.getElementById('a').click()")
    assert wait_for(lambda: len(window.page_store.pages()) == pages_before + 1, 10)   # new-window link -> new page

    window.rename_page(page_id, "My research")                 # a chosen name stops automatic naming
    assert not browser.auto_title and window.page_store.page_state(page_id)["auto_title"] is False
    browser.step_zoom(1)
    window.close()

    reopened = MainWindow()
    reopened.show_page(page_id)
    restored = reopened.page_widget(page_id)
    assert isinstance(restored, BrowserPage) and restored.state["zoom"] == 1.1
    assert restored.state["url"].startswith("data:text/html")
    reopened.close()
# --- END [1.2] ---
