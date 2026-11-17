# ==============================================================================
# FILE: app/ui/pages/browser_page.py
# OVERVIEW: A page that is a web browser: navigation bar, address / search field, find-in-page, zoom and
#           a Qt WebEngine view. Made from an empty page's "New Browser" button (or a link that opens a
#           new window); remembers its address and zoom with the page.
# PURPOSE:
#   1. Back / forward / reload-stop / home, address bar (URL, host name or search), thin load progress line.
#   2. Page naming: while the user hasn't renamed the page, it follows the website's title (title_changed).
#   3. Find bar (Ctrl+F), zoom (Ctrl+= / Ctrl+- / Ctrl+0), ⋯ menu (copy address, open in system browser,
#      set as home page); links that open a new window open a new browser page (open_new_tab callback).
#   4. Load failures are announced in the log; without Qt WebEngine the page explains why instead of crashing.
#   5. For hosts (the Web App module): set_toolbar_visible(), fill_menu(menu, ...) (the ⋯ menu's actions, plus
#      navigation when the bar is hidden), extend_context_menu (a hook that adds to the page's right-click menu).
# CONTRACT:
#   - Requires: app.ui.web_support (browser_profile, web_engine_unavailable_reason, address_to_url, HOME_URL),
#               app.ui.ui_tools (tool_button, set_style_property), app.data.activity_log, app.data.store.database
#               (get_database, for the home page preference), QSS selectors #browser_page_panel,
#               #browser_toolbar_panel, #browser_address_edit, #browser_nav_btn, #browser_progress_bar,
#               #find_bar_panel, #browser_unavailable_label
#   - Exposes: BrowserPage(page_id, state, save_state, open_new_tab=None, profile=None, home_url=None) (QWidget) with signal title_changed(str);
#              navigate(text); set_title(title); start_title_edit(); auto_title; web_view (None if unavailable);
#              set_toolbar_visible(is_visible); fill_menu(menu, with_navigation=False, with_set_home=True);
#              extend_context_menu (callable(QMenu) | None)
# NOTES:
#   - [2026-11-17] fill_menu() (the ⋯ menu's actions, shared with web apps), set_toolbar_visible() and the
#                  extend_context_menu hook, so a web app can hide the browser bar and still offer its options.
#   - [2026-11-16] profile= (a container's own profile) and home_url= (fixed home) so the Web App module
#                  reuses this page inside a module tile.
#   - [2026-11-16] Created ("New Browser" on empty pages). State saved with the page: url, zoom, auto_title.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - auto_title (bool): The page's name follows the website title until the user renames the page.
# - state (dict): {"url", "zoom", "auto_title"} kept with the page (PageStore.page_state).
# - ZOOM_STEPS (tuple[float]): Zoom levels Ctrl+= / Ctrl+- step through.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. WEB_VIEW
#    2.1 web_view
# 3. BROWSER_PAGE
#    3.1 init
#    3.2 navigation
#    3.3 page_events
#    3.4 find_and_zoom
#    3.5 naming_and_state
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QMenu, QProgressBar, QVBoxLayout, QWidget,
)

from app.data.activity_log import get_activity_log
from app.data.store.database import get_database
from app.ui.ui_tools import tool_button
from app.ui.web_support import HOME_URL, address_to_url, browser_profile, web_engine_unavailable_reason

ZOOM_STEPS = (0.5, 0.67, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)
HOME_PREFERENCE = "browser_home_url"
# --- END [1.1] ---


# ==============================================================================
# [2.1] web_view
# ==============================================================================
def _web_view_class():
    """QWebEngineView subclass, built lazily so this file imports without Qt WebEngine."""
    from PySide6.QtWebEngineWidgets import QWebEngineView

    class WebView(QWebEngineView):
        def __init__(self, browser_page: "BrowserPage") -> None:
            super().__init__(browser_page)
            self.browser_page = browser_page

        def createWindow(self, window_type):
            """Links that open a new window / tab open a new browser page; without a handler, stay here."""
            handler = self.browser_page.open_new_tab
            new_page = handler() if handler is not None else None
            return new_page.web_view if new_page is not None and new_page.web_view is not None else self

        def contextMenuEvent(self, event) -> None:
            """The page's own right-click menu, plus whatever the host adds (extend_context_menu)."""
            menu = self.createStandardContextMenu()
            if self.browser_page.extend_context_menu is not None:
                self.browser_page.extend_context_menu(menu)
            menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            menu.popup(event.globalPos())
    return WebView
# --- END [2.1] ---


# ==============================================================================
# [3.1] init
# ==============================================================================
class BrowserPage(QWidget):
    title_changed = Signal(str)                    # website title (the page follows it while auto_title)

    def __init__(self, page_id: int, state: dict, save_state: Callable[[dict], None],
                 open_new_tab: Callable[[], "BrowserPage | None"] | None = None, profile=None,
                 home_url: str | None = None) -> None:
        super().__init__(objectName="browser_page_panel")
        self.page_id, self.save_state, self.open_new_tab = page_id, save_state, open_new_tab
        self._home_url = home_url                       # fixed home (web apps); None = the user's preference
        self.state = {"url": state.get("url") or self.home_url(), "zoom": float(state.get("zoom", 1.0)),
                      "auto_title": bool(state.get("auto_title", True))}
        self._last_auto_title = ""
        self.log = get_activity_log()
        self.web_view = None
        self.extend_context_menu: Callable[[QMenu], None] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_toolbar())
        self.browser_progress_bar = QProgressBar(objectName="browser_progress_bar", textVisible=False, maximumHeight=2)
        self.browser_progress_bar.hide()
        layout.addWidget(self.browser_progress_bar)
        layout.addWidget(self._build_find_bar())

        reason = web_engine_unavailable_reason()
        if reason:
            layout.addWidget(QLabel(f"The browser can't start here.\n{reason}", objectName="browser_unavailable_label",
                                    wordWrap=True), 1)
            self.browser_toolbar_panel.setEnabled(False)
            return
        from PySide6.QtWebEngineCore import QWebEnginePage
        self.web_view = _web_view_class()(self)
        self.web_view.setPage(QWebEnginePage(profile or browser_profile(), self.web_view))
        self.web_view.urlChanged.connect(self._on_url_changed)
        self.web_view.titleChanged.connect(self._on_title_changed)
        self.web_view.loadStarted.connect(self._on_load_started)
        self.web_view.loadProgress.connect(self.browser_progress_bar.setValue)
        self.web_view.loadFinished.connect(self._on_load_finished)
        self.web_view.page().renderProcessTerminated.connect(self._on_crashed)
        layout.addWidget(self.web_view, 1)
        self._build_shortcuts()
        self.web_view.setZoomFactor(self.state["zoom"])
        self.web_view.load(QUrl(self.state["url"]))

    def _build_toolbar(self) -> QWidget:
        self.browser_toolbar_panel = QWidget(objectName="browser_toolbar_panel")
        row = QHBoxLayout(self.browser_toolbar_panel)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(2)
        self.back_btn = tool_button("browser_nav_btn", text="←", tool_tip="Back (Alt+Left)")
        self.forward_btn = tool_button("browser_nav_btn", text="→", tool_tip="Forward (Alt+Right)")
        self.reload_btn = tool_button("browser_nav_btn", text="⟳", tool_tip="Reload (F5)")
        self.home_btn = tool_button("browser_nav_btn", text="⌂", tool_tip="Home")
        self.back_btn.clicked.connect(lambda: self.web_view.back())
        self.forward_btn.clicked.connect(lambda: self.web_view.forward())
        self.reload_btn.clicked.connect(self._reload_or_stop)
        self.home_btn.clicked.connect(lambda: self.navigate(self.home_url()))
        self.browser_address_edit = QLineEdit(objectName="browser_address_edit", placeholderText="Search or enter an address")
        self.browser_address_edit.returnPressed.connect(lambda: self.navigate(self.browser_address_edit.text()))
        self.zoom_label = tool_button("browser_nav_btn", text="", tool_tip="Reset zoom (Ctrl+0)")
        self.zoom_label.clicked.connect(lambda: self.set_zoom(1.0))
        self.zoom_label.hide()
        self.menu_btn = tool_button("browser_nav_btn", text="⋯", tool_tip="More")
        self.menu_btn.clicked.connect(self._show_menu)
        for widget in (self.back_btn, self.forward_btn, self.reload_btn, self.home_btn):
            row.addWidget(widget)
        row.addWidget(self.browser_address_edit, 1)
        row.addWidget(self.zoom_label)
        row.addWidget(self.menu_btn)
        return self.browser_toolbar_panel

    def _build_find_bar(self) -> QWidget:
        self.find_bar_panel = QWidget(objectName="find_bar_panel")
        row = QHBoxLayout(self.find_bar_panel)
        row.setContentsMargins(6, 2, 6, 2)
        self.find_edit = QLineEdit(placeholderText="Find in page")
        self.find_edit.textChanged.connect(lambda: self.find(forward=True))
        self.find_edit.returnPressed.connect(lambda: self.find(forward=True))
        self.find_count_label = QLabel(objectName="control_label")
        previous_btn = tool_button("browser_nav_btn", text="↑", tool_tip="Previous match (Shift+Enter)")
        previous_btn.clicked.connect(lambda: self.find(forward=False))
        next_btn = tool_button("browser_nav_btn", text="↓", tool_tip="Next match (Enter)")
        next_btn.clicked.connect(lambda: self.find(forward=True))
        close_btn = tool_button("browser_nav_btn", text="✕", tool_tip="Close (Esc)")
        close_btn.clicked.connect(self.close_find)
        for widget, stretch in ((self.find_edit, 1), (self.find_count_label, 0), (previous_btn, 0), (next_btn, 0), (close_btn, 0)):
            row.addWidget(widget, stretch)
        QShortcut(QKeySequence("Shift+Return"), self.find_edit).activated.connect(lambda: self.find(forward=False))
        QShortcut(QKeySequence("Escape"), self.find_edit).activated.connect(self.close_find)
        self.find_bar_panel.hide()
        return self.find_bar_panel

    def _build_shortcuts(self) -> None:
        actions = (("Ctrl+L", self.start_title_edit), ("F6", self.start_title_edit), ("F5", self._reload_or_stop),
                   ("Ctrl+R", self._reload_or_stop), ("Alt+Left", self.web_view.back), ("Alt+Right", self.web_view.forward),
                   ("Ctrl+F", self.open_find), ("Ctrl+=", lambda: self.step_zoom(1)), ("Ctrl++", lambda: self.step_zoom(1)),
                   ("Ctrl+-", lambda: self.step_zoom(-1)), ("Ctrl+0", lambda: self.set_zoom(1.0)))
        for keys, action in actions:
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action)
# --- END [3.1] ---

# ==============================================================================
# [3.2] navigation
# ==============================================================================
    def home_url(self) -> str:
        return self._home_url or get_database().get_preference(HOME_PREFERENCE, HOME_URL)

    def navigate(self, text: str) -> None:
        if self.web_view is None:
            return
        url = address_to_url(text)
        self.web_view.load(QUrl(url))
        self.web_view.setFocus()

    def _reload_or_stop(self) -> None:
        if self.web_view is None:
            return
        if self.browser_progress_bar.isVisible():
            self.web_view.stop()
        else:
            self.web_view.reload()

    def _show_menu(self) -> None:
        menu = QMenu(self)
        self.fill_menu(menu)
        menu.exec(self.menu_btn.mapToGlobal(self.menu_btn.rect().bottomLeft()))

    def fill_menu(self, menu: QMenu, with_navigation: bool = False, with_set_home: bool = True) -> None:
        """The ⋯ menu's actions (find, zoom, address); with_navigation adds back / forward / reload / home for
        hosts that hide the bar."""
        view = self.web_view
        url = view.url().toString() if view else ""
        if with_navigation and view is not None:
            back = menu.addAction("Back  (Alt+Left)")
            back.triggered.connect(view.back)
            back.setEnabled(view.history().canGoBack())
            forward = menu.addAction("Forward  (Alt+Right)")
            forward.triggered.connect(view.forward)
            forward.setEnabled(view.history().canGoForward())
            menu.addAction("Reload  (F5)").triggered.connect(view.reload)
            menu.addAction("Home").triggered.connect(lambda: self.navigate(self.home_url()))
            menu.addSeparator()
        menu.addAction("Find in page  (Ctrl+F)").triggered.connect(self.open_find)
        menu.addAction("Zoom in  (Ctrl+=)").triggered.connect(lambda: self.step_zoom(1))
        menu.addAction("Zoom out  (Ctrl+-)").triggered.connect(lambda: self.step_zoom(-1))
        menu.addAction("Reset zoom  (Ctrl+0)").triggered.connect(lambda: self.set_zoom(1.0))
        menu.addSeparator()
        menu.addAction("Copy address").triggered.connect(lambda: QApplication.clipboard().setText(url))
        menu.addAction("Open in system browser").triggered.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        if with_set_home:
            menu.addAction("Use as home page").triggered.connect(self._set_home)

    def set_toolbar_visible(self, is_visible: bool) -> None:
        """Show / hide the navigation bar (a web app looks like part of DevOS without it)."""
        self.browser_toolbar_panel.setVisible(is_visible)

    def _set_home(self) -> None:
        url = self.web_view.url().toString()
        get_database().set_preference(HOME_PREFERENCE, url)
        self.log.info("browser", f"Home page set to {url}")
# --- END [3.2] ---

# ==============================================================================
# [3.3] page_events
# ==============================================================================
    def _on_url_changed(self, url: QUrl) -> None:
        if not self.browser_address_edit.hasFocus():
            self.browser_address_edit.setText(url.toString())
            self.browser_address_edit.setCursorPosition(0)
        self.back_btn.setEnabled(self.web_view.history().canGoBack())
        self.forward_btn.setEnabled(self.web_view.history().canGoForward())
        if url.toString() not in ("", "about:blank"):
            self.state["url"] = url.toString()
            self.save_state(dict(self.state))

    def _on_load_started(self) -> None:
        self.browser_progress_bar.setValue(0)
        self.browser_progress_bar.show()
        self.reload_btn.setText("✕")
        self.reload_btn.setToolTip("Stop")

    def _on_load_finished(self, is_ok: bool) -> None:
        self.browser_progress_bar.hide()
        self.reload_btn.setText("⟳")
        self.reload_btn.setToolTip("Reload (F5)")
        url = self.web_view.url()
        if url.toString() in ("", "about:blank"):
            return
        where = url.host() or url.toString()[:80]
        if is_ok:
            self.log.info("browser", f"Loaded {where}")
        else:
            self.log.warning("browser", f"Couldn't load {where} (offline, blocked or stopped)")

    def _on_crashed(self, status, exit_code: int) -> None:
        self.log.error("browser", f"Web page process ended ({status.name}, code {exit_code}); reload to try again")
# --- END [3.3] ---

# ==============================================================================
# [3.4] find_and_zoom
# ==============================================================================
    def open_find(self) -> None:
        self.find_bar_panel.show()
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def close_find(self) -> None:
        self.find_bar_panel.hide()
        if self.web_view is not None:
            self.web_view.findText("")                  # clears the highlights
            self.web_view.setFocus()

    def find(self, forward: bool = True) -> None:
        if self.web_view is None:
            return
        from PySide6.QtWebEngineCore import QWebEnginePage
        flags = QWebEnginePage.FindFlag(0) if forward else QWebEnginePage.FindFlag.FindBackward
        self.web_view.findText(self.find_edit.text(), flags, self._on_find_result)

    def _on_find_result(self, result) -> None:
        text = self.find_edit.text()
        matches = result.numberOfMatches()
        self.find_count_label.setText("" if not text else (f"{result.activeMatch()} / {matches}" if matches else "no matches"))

    def step_zoom(self, step: int) -> None:
        current = self.state["zoom"]
        if step > 0:
            self.set_zoom(next((zoom for zoom in ZOOM_STEPS if zoom > current + 1e-3), ZOOM_STEPS[-1]))
        else:
            self.set_zoom(next((zoom for zoom in reversed(ZOOM_STEPS) if zoom < current - 1e-3), ZOOM_STEPS[0]))

    def set_zoom(self, zoom: float) -> None:
        self.state["zoom"] = zoom
        if self.web_view is not None:
            self.web_view.setZoomFactor(zoom)
        self.zoom_label.setText(f"{zoom:.0%}")
        self.zoom_label.setVisible(abs(zoom - 1.0) > 1e-3)
        self.save_state(dict(self.state))
# --- END [3.4] ---

# ==============================================================================
# [3.5] naming_and_state
# ==============================================================================
    @property
    def auto_title(self) -> bool:
        return self.state["auto_title"]

    def _on_title_changed(self, title: str) -> None:
        url = self.web_view.url()
        addresses = {url.toString(), url.toDisplayString()}
        addresses |= {address.split("://", 1)[-1] for address in addresses}
        if not title or any(address.startswith(title) for address in addresses):
            title = url.host()                         # no <title> (yet): Qt reports the address; use the site name
        title = " ".join(title.split())[:80]
        if title and self.auto_title and title != self._last_auto_title:
            self._last_auto_title = title
            self.title_changed.emit(title)

    def set_title(self, title: str) -> None:
        """The page was renamed (sidebar, tab, or automatically). A name the user chose stops auto-naming."""
        if title != self._last_auto_title and self.auto_title:
            self.state["auto_title"] = False
            self.save_state(dict(self.state))

    def start_title_edit(self) -> None:
        """"Name it now" for a browser means "where to?": focus the address bar."""
        self.browser_address_edit.setFocus()
        self.browser_address_edit.selectAll()
# --- END [3.5] ---
