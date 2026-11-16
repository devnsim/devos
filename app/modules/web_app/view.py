# ==============================================================================
# FILE: app/modules/web_app/view.py
# OVERVIEW: Web App module - a web page that lives in a module tile: a browser set to one site (your
#           bank, n8n, a dashboard) with its own container, so its logins stay separate from every other
#           site. Put it on a page in a folder and it opens straight at that site.
# PURPOSE:
#   1. Setup panel (first run, or ⚙): address, container (own / shared / a named one), presets for local
#      tools (n8n, Home Assistant, Grafana, Jupyter) with a hint or a Start button when they aren't running.
#   2. The site itself: a BrowserPage with the container's profile and the site as home; new-window links
#      stay inside the app; zoom and last address are remembered.
# CONTRACT:
#   - Requires: app.modules.base.ModuleView, app.ui.pages.browser_page.BrowserPage, app.ui.web_support
#               (container_profile, browser_profile, container_names, address_to_url), app.data.activity_log,
#               QSS selectors #web_app_panel, #web_app_setup_panel, #create_new_label
#   - Exposes: WebAppModule(parent=None, context=None) with open_app(url, container), show_setup(), browser;
#              PRESETS, OWN_CONTAINER, SHARED_CONTAINER
# NOTES:
#   - [2026-11-16] Created ("containerised web pages"; n8n as the first preset).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - container: A named browser profile (cookies, storage, logins) - see web_support.container_profile().
# - OWN_CONTAINER: This tile gets a container of its own (named after its instance id).
# - SHARED_CONTAINER: Use the normal browser pages' profile (shares their logins).
# - preset: A known local web tool: (title, address, start command, how to install).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. WEB_APP
#    1.1 imports_and_presets
#    1.2 setup_panel
#    1.3 web_app_module
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_presets
# ==============================================================================
from __future__ import annotations

import shutil
import subprocess

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from app.data.activity_log import get_activity_log
from app.modules.base import ModuleView
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.pages.browser_page import BrowserPage
from app.ui.tool_bag import ToolSpec, register_tools
from app.ui.ui_tools import tool_button
from app.ui.web_support import address_to_url, browser_profile, container_names, container_profile

OWN_CONTAINER = "(own container)"
SHARED_CONTAINER = "(shared browser)"
PRESETS = (
    ("n8n (workflows)", "http://localhost:5678", ["n8n", "start"],
     "Install: npm install -g n8n  ·  or: docker run -it --rm -p 5678:5678 n8nio/n8n"),
    ("Home Assistant", "http://localhost:8123", None, "Runs as its own service / container"),
    ("Grafana", "http://localhost:3000", None, "Install grafana and start its service"),
    ("Jupyter", "http://localhost:8888", ["jupyter", "lab", "--no-browser"], "Install: pip install jupyterlab"),
)
# --- END [1.1] ---

# ==============================================================================
# [1.2] setup_panel
# ==============================================================================
class SetupPanel(QWidget):
    def __init__(self, module: "WebAppModule") -> None:
        super().__init__(objectName="web_app_setup_panel")
        self.module = module
        outer = QVBoxLayout(self)
        outer.addStretch()
        box = QWidget()
        box.setMaximumWidth(520)
        layout = QVBoxLayout(box)
        layout.addWidget(QLabel("Web App", objectName="create_new_label", alignment=Qt.AlignmentFlag.AlignCenter))
        layout.addWidget(QLabel("A site that lives in this tile, with its own logins.", objectName="control_label",
                                alignment=Qt.AlignmentFlag.AlignCenter))
        form = QFormLayout()
        self.url_edit = QLineEdit(module.setting("url", ""), placeholderText="bank.example.com, http://localhost:5678 …")
        self.url_edit.returnPressed.connect(self._open)
        self.container_combo = QComboBox(editable=True, toolTip="Sites in the same container share logins")
        self.container_combo.addItems([OWN_CONTAINER, SHARED_CONTAINER] + container_names())
        self.container_combo.setCurrentText(module.setting("container", OWN_CONTAINER))
        form.addRow("Address", self.url_edit)
        form.addRow("Container", self.container_combo)
        layout.addLayout(form)
        presets_row = QHBoxLayout()
        for title, url, _command, _hint in PRESETS:
            button = QPushButton(title, flat=True)
            button.clicked.connect(lambda _checked=False, url=url: self.url_edit.setText(url))
            presets_row.addWidget(button)
        layout.addLayout(presets_row)
        open_btn = QPushButton("Open", objectName="new_module_btn")
        open_btn.clicked.connect(self._open)
        layout.addWidget(open_btn)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(box)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()

    def _open(self) -> None:
        if self.url_edit.text().strip():
            self.module.open_app(address_to_url(self.url_edit.text()), self.container_combo.currentText().strip() or OWN_CONTAINER)
# --- END [1.2] ---

# ==============================================================================
# [1.3] web_app_module
# ==============================================================================
class WebAppModule(ModuleView):
    def __init__(self, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, context, "web_app_panel")
        self.log = get_activity_log()
        self.browser: BrowserPage | None = None
        self.body_layout = QVBoxLayout(self)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(0)
        self.hint_panel: QWidget | None = None
        self.current: QWidget | None = None
        if self.setting("url"):
            self.open_app(self.setting("url"), self.setting("container", OWN_CONTAINER), is_restore=True)
        else:
            self.show_setup()

    def _replace(self, widget: QWidget) -> None:
        if self.current is not None:
            self.body_layout.removeWidget(self.current)
            self.current.deleteLater()
        self.current = widget
        self.body_layout.addWidget(widget, 1)

    def show_setup(self) -> None:
        self.browser, self.hint_panel = None, None
        self._replace(SetupPanel(self))

    def container_title(self, container: str) -> str:
        return {OWN_CONTAINER: "own container", SHARED_CONTAINER: "shared browser"}.get(container, container)

    def profile_for(self, container: str):
        if container == SHARED_CONTAINER:
            return browser_profile()
        if container == OWN_CONTAINER:
            instance = self.context.instance_id if self.context is not None else "web-app"
            return container_profile(f"app-{instance}")
        return container_profile(container)

    def open_app(self, url: str, container: str, is_restore: bool = False) -> None:
        if not is_restore:
            self.save_setting("url", url)
            self.save_setting("container", container)
            self.save_setting("state", {})
            self.log.info("web_app", f"Web app → {url} ({self.container_title(container)})")
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        state = dict(self.setting("state", {}) or {})
        state.setdefault("url", url)
        self.browser = BrowserPage(0, state, lambda new_state: self.save_setting("state", new_state),
                                   open_new_tab=None, profile=self.profile_for(container), home_url=url)
        toolbar = self.browser.browser_toolbar_panel.layout()
        container_label = QLabel(f"🔒 {self.container_title(container)}", objectName="control_label",
                                 toolTip="Logins in this app are kept in its container")
        toolbar.addWidget(container_label)
        setup_btn = tool_button("browser_nav_btn", text="⚙", tool_tip="Change the site or container")
        setup_btn.clicked.connect(self.show_setup)
        toolbar.addWidget(setup_btn)
        reserve_controls_space(toolbar)
        self.hint_panel = QWidget()
        hint_row = QHBoxLayout(self.hint_panel)
        hint_row.setContentsMargins(8, 4, 8, 4)
        self.hint_label = QLabel(objectName="module_summary_label", wordWrap=True)
        self.start_btn = QPushButton("Start it")
        self.start_btn.clicked.connect(self.start_preset_service)
        hint_row.addWidget(self.hint_label, 1)
        hint_row.addWidget(self.start_btn)
        self.hint_panel.hide()
        layout.addWidget(self.hint_panel)
        layout.addWidget(self.browser, 1)
        self._replace(panel)
        if self.browser.web_view is not None:
            self.browser.web_view.loadFinished.connect(lambda is_ok: self._after_load(is_ok, url))

    def _after_load(self, is_ok: bool, url: str) -> None:
        """A local tool that isn't running: say how to start it (or offer to)."""
        preset = next((preset for preset in PRESETS if url.startswith(preset[1])), None)
        if self.hint_panel is None:
            return
        if is_ok or preset is None:
            self.hint_panel.hide()
            return
        title, _url, command, hint = preset
        can_start = command is not None and shutil.which(command[0]) is not None
        self.hint_label.setText(f"{title} isn't answering at {url}. " + ("" if can_start else hint))
        self.start_btn.setVisible(can_start)
        self.hint_panel.show()

    def start_preset_service(self) -> None:
        url = self.setting("url", "")
        preset = next((preset for preset in PRESETS if url.startswith(preset[1])), None)
        if preset is None or preset[2] is None or shutil.which(preset[2][0]) is None:
            self.log.warning("web_app", "Nothing to start for this site (no known local tool installed)")
            return
        with self.log.step("web_app", f"Start {preset[0]}"):
            subprocess.Popen(preset[2], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        QTimer.singleShot(4000, lambda: self.browser and self.browser.web_view and self.browser.web_view.reload())


register_tools([
    ToolSpec("web_app.home", "Go home", "⌂", "Back to this web app's start page",
             lambda module: module.browser and module.browser.navigate(module.browser.home_url()), "web_app", "start"),
    ToolSpec("web_app.start_service", "Start local service", "▶", "Start the local tool behind this app (n8n, Jupyter) if installed",
             lambda module: module.start_preset_service(), "web_app", "n8n jupyter run"),
    ToolSpec("web_app.setup", "Change site", "⚙", "Pick another address or container", lambda module: module.show_setup(),
             "web_app", "settings url"),
])
# --- END [1.3] ---
