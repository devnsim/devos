# ==============================================================================
# FILE: app/ui/tool_bag.py
# OVERVIEW: The tool bag - every tool DevOS offers, searchable. Global tools work anywhere; module tools
#           declare the module they depend on and only show up for that module. Tools go on toolbars
#           (app/ui/toolbars.py) or run straight from the search (Ctrl+K).
# PURPOSE:
#   1. ToolSpec + registry: register_tools(), get_tool(), tools_for(module_type_id), search_tools().
#   2. ToolBagPopup: search field + list ("This module" first, then "Global"); picks add a tool to a
#      toolbar (stays open to add more) or run it (closes).
#   3. run_tool(): runs a tool against a module view, logs failures (never silent).
#   4. Active module: the last module the user clicked (mouse_hub), so Ctrl+K knows its context.
#   5. The global tools: calculator, quick note, capture, timestamp, colour picker, web search, new
#      browser tab, System Log.
# CONTRACT:
#   - Requires: app.data (activity_log, store.content), app.ui.window_events (via note_pressed caller),
#               app.ui.web_support (address_to_url), QSS selectors #tool_bag_popup, #tool_bag_list,
#               #tool_bag_search_edit
#   - Exposes: ToolSpec, register_tools(specs), get_tool(tool_id), tools_for(module_type_id),
#              search_tools(text, module_type_id), run_tool(spec, module_view), ToolBagPopup,
#              note_pressed(widget), active_module(), module_type_of(widget), main_window()
# NOTES:
#   - [2026-11-16] Created with module toolbars (global tool bag + module-dependent tools).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - tool: A small action with a glyph, a title and a description; run(module_view) does the work.
# - global tool: module_type_id None - offered everywhere. module tool: offered only to that module type.
# - glyph (str): One or two characters drawn on the toolbar button (no icon files needed).
# - active module: The ModuleView that last received a mouse press (weak reference).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. REGISTRY
#    1.1 imports_and_spec
#    1.2 registry
#    1.3 active_module
# 2. POPUP
#    2.1 tool_bag_popup
# 3. GLOBAL_TOOLS
#    3.1 global_tools
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_spec
# ==============================================================================
from __future__ import annotations

import datetime as dt
import weakref
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPoint, Qt
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QDialog, QFrame, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout,
    QWidget,
)

from app.data.activity_log import get_activity_log


@dataclass(frozen=True)
class ToolSpec:
    tool_id: str                                   # unique; module tools are "<module_type_id>.<name>"
    title: str
    glyph: str
    description: str
    run: Callable[[QWidget | None], None]          # run(module_view or None)
    module_type_id: str | None = None              # None = global
    keywords: str = ""
# --- END [1.1] ---


# ==============================================================================
# [1.2] registry
# ==============================================================================
_tools: dict[str, ToolSpec] = {}


def register_tools(specs: list[ToolSpec]) -> None:
    for spec in specs:
        _tools[spec.tool_id] = spec


def get_tool(tool_id: str) -> ToolSpec | None:
    return _tools.get(tool_id)


def tools_for(module_type_id: str | None) -> list[ToolSpec]:
    """Tools offered to a module: its own first, then the global ones."""
    own = [spec for spec in _tools.values() if module_type_id and spec.module_type_id == module_type_id]
    return own + [spec for spec in _tools.values() if spec.module_type_id is None]


def search_tools(text: str, module_type_id: str | None) -> list[ToolSpec]:
    words = text.lower().split()
    return [spec for spec in tools_for(module_type_id)
            if all(word in f"{spec.title} {spec.description} {spec.keywords}".lower() for word in words)]


def run_tool(spec: ToolSpec, module_view: QWidget | None) -> None:
    log = get_activity_log()
    try:
        spec.run(module_view)
        log.debug("tools", f"Ran {spec.title}")
    except Exception as error:                      # a tool must never take the app down
        log.error("tools", f"{spec.title} failed", error)
# --- END [1.2] ---

# ==============================================================================
# [1.3] active_module
# ==============================================================================
_active_module: weakref.ref | None = None


def module_view_of(widget: QWidget | None) -> QWidget | None:
    """The ModuleView holding a widget (walking up its parents), if any."""
    from app.modules.base import ModuleView
    while widget is not None and not isinstance(widget, ModuleView):
        widget = widget.parentWidget()
    return widget


def module_type_of(module_view: QWidget | None) -> str | None:
    context = getattr(module_view, "context", None)
    return context.module_type_id if context is not None else None


def note_pressed(widget: QWidget) -> None:
    """mouse_hub().pressed handler: remember the module the user is working in."""
    global _active_module
    module_view = module_view_of(widget)
    if module_view is not None:
        _active_module = weakref.ref(module_view)


def active_module() -> QWidget | None:
    module_view = _active_module() if _active_module is not None else None
    try:
        return module_view if module_view is not None and module_view.isVisible() else None
    except RuntimeError:                             # its C++ object was deleted
        return None


def main_window() -> QWidget | None:
    return next((widget for widget in QApplication.topLevelWidgets() if hasattr(widget, "open_browser_tab")), None)
# --- END [1.3] ---


# ==============================================================================
# [2.1] tool_bag_popup
# ==============================================================================
class ToolBagPopup(QFrame):
    """Search the tool bag. mode "add": picking calls on_pick and keeps the list open (picked tools get a
    ✓); mode "run": picking runs the tool on the module and closes."""

    def __init__(self, module_view: QWidget | None, on_pick: Callable[[ToolSpec], None] | None = None,
                 mode: str = "add", chosen_ids: set[str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="tool_bag_popup")
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.module_view, self.on_pick, self.mode = module_view, on_pick, mode
        self.module_type_id = module_type_of(module_view)
        self.chosen_ids = set(chosen_ids or ())
        self.resize(360, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        title = "Add tools to this toolbar" if mode == "add" else "Run a tool"
        layout.addWidget(QLabel(title, objectName="control_label"))
        self.tool_bag_search_edit = QLineEdit(objectName="tool_bag_search_edit", placeholderText="Search tools…",
                                              clearButtonEnabled=True)
        self.tool_bag_search_edit.textChanged.connect(self.refresh)
        self.tool_bag_search_edit.returnPressed.connect(self._pick_current)
        layout.addWidget(self.tool_bag_search_edit)
        self.tool_bag_list = QListWidget(objectName="tool_bag_list")
        self.tool_bag_list.itemClicked.connect(self._pick)
        self.tool_bag_list.itemActivated.connect(self._pick)
        layout.addWidget(self.tool_bag_list, 1)
        self.refresh()

    def refresh(self) -> None:
        self.tool_bag_list.clear()
        found = search_tools(self.tool_bag_search_edit.text(), self.module_type_id)
        sections = (("This module", [spec for spec in found if spec.module_type_id]),
                    ("Global", [spec for spec in found if not spec.module_type_id]))
        for caption, specs in sections:
            if not specs:
                continue
            header = QListWidgetItem(caption)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.tool_bag_list.addItem(header)
            for spec in specs:
                mark = "  ✓" if spec.tool_id in self.chosen_ids else ""
                item = QListWidgetItem(f"{spec.glyph}   {spec.title}{mark}")
                item.setToolTip(spec.description)
                item.setData(Qt.ItemDataRole.UserRole, spec.tool_id)
                self.tool_bag_list.addItem(item)
        for row in range(self.tool_bag_list.count()):
            if self.tool_bag_list.item(row).flags() & Qt.ItemFlag.ItemIsEnabled:
                self.tool_bag_list.setCurrentRow(row)
                break
        if not found:
            self.tool_bag_list.addItem(QListWidgetItem("No tool matches"))

    def _pick_current(self) -> None:
        if self.tool_bag_list.currentItem() is not None:
            self._pick(self.tool_bag_list.currentItem())

    def _pick(self, item: QListWidgetItem) -> None:
        spec = get_tool(item.data(Qt.ItemDataRole.UserRole) or "")
        if spec is None:
            return
        if self.mode == "run":
            self.close()
            run_tool(spec, self.module_view)
            return
        if self.on_pick is not None:
            self.on_pick(spec)
        self.chosen_ids.add(spec.tool_id)
        self.refresh()

    def show_at(self, global_position: QPoint) -> None:
        screen = QApplication.screenAt(global_position) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(area.left(), global_position.x()), area.right() - self.width())
        y = min(max(area.top(), global_position.y()), area.bottom() - self.height())
        self.move(x, y)
        self.show()
        self.tool_bag_search_edit.setFocus()
# --- END [2.1] ---


# ==============================================================================
# [3.1] global_tools
# ==============================================================================
def _calculator(_module_view) -> None:
    from app.modules.spreadsheet.formulas import evaluate_expression
    dialog = QDialog(main_window(), windowTitle="Calculator")
    layout = QVBoxLayout(dialog)
    expression_edit = QLineEdit(placeholderText="e.g. 2*(3+4)^2 / 7, SQRT(2), ROUND(PI()*3, 2)")
    result_label = QLabel(objectName="module_summary_label")
    result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def update() -> None:
        try:
            value = evaluate_expression(expression_edit.text()) if expression_edit.text().strip() else ""
            result_label.setText(f"= {value}" if value != "" else "")
        except ValueError as error:
            result_label.setText(f"… {error}")
    expression_edit.textChanged.connect(update)
    expression_edit.returnPressed.connect(lambda: QApplication.clipboard().setText(result_label.text().removeprefix("= ")))
    layout.addWidget(expression_edit)
    layout.addWidget(result_label)
    layout.addWidget(QLabel("Enter copies the result", objectName="control_label"))
    dialog.resize(380, 110)
    dialog.show()


def _quick_note(_module_view) -> None:
    from app.data.store.content import get_content_store
    text, is_ok = QInputDialog.getMultiLineText(main_window(), "Quick note", "Saved to your Library (Notes):")
    if is_ok and text.strip():
        item = get_content_store().create_text("note", f"Quick note {dt.datetime.now():%Y-%m-%d %H:%M}", text)
        get_activity_log().success("tools", f"Saved quick note “{item.name}”")


def _capture(module_view) -> None:
    from app.data.store.content import get_content_store
    target = module_view or main_window()
    if target is None:
        return
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    target.grab().save(buffer, "PNG")
    name = f"Capture {dt.datetime.now():%Y-%m-%d %H.%M.%S}"
    item = get_content_store().add_bytes("image", name, bytes(data), ".png")
    get_activity_log().success("tools", f"Captured “{item.name}” into your Library (Images)")


def _pick_color(_module_view) -> None:
    color = QColorDialog.getColor(parent=main_window(), title="Pick a colour (copied as hex)")
    if color.isValid():
        QApplication.clipboard().setText(color.name())
        get_activity_log().info("tools", f"Copied colour {color.name()}")


def _web_search(_module_view) -> None:
    from app.ui.web_support import address_to_url
    text, is_ok = QInputDialog.getText(main_window(), "Search the web", "Search or address:")
    window = main_window()
    if is_ok and text.strip() and window is not None:
        window.open_browser_tab(address_to_url(text))


def _new_browser(_module_view) -> None:
    window = main_window()
    if window is not None:
        window.open_browser_tab()


def _toggle_log(_module_view) -> None:
    window = main_window()
    if window is not None:
        window.set_log_visible(not window.log_panel.isVisible())


register_tools([
    ToolSpec("calculator", "Calculator", "🖩", "Evaluate an expression (same functions as the Spreadsheet)", _calculator,
             keywords="math compute"),
    ToolSpec("quick_note", "Quick note", "✎", "Write a note; it's saved to your Library", _quick_note, keywords="text memo"),
    ToolSpec("capture", "Capture", "📷", "Screenshot this module (or the window) into your Library", _capture,
             keywords="screenshot image png"),
    ToolSpec("timestamp", "Copy timestamp", "🕒", "Copy the current date and time (ISO 8601)",
             lambda _view: QApplication.clipboard().setText(dt.datetime.now().isoformat(timespec="seconds")),
             keywords="date time clock"),
    ToolSpec("color_picker", "Colour picker", "🎨", "Pick a colour; its hex code is copied", _pick_color, keywords="color hex"),
    ToolSpec("web_search", "Web search", "🔎", "Search the web or open an address in a new browser page", _web_search,
             keywords="browser internet google"),
    ToolSpec("new_browser", "New browser tab", "🌐", "Open a new browser page", _new_browser, keywords="web internet"),
    ToolSpec("system_log", "System Log", "≣", "Show or hide the System Log", _toggle_log, keywords="activity errors"),
])
# --- END [3.1] ---
