# ==============================================================================
# FILE: app/ui/ui_tools.py
# OVERVIEW: Small, dependency-free Qt helpers every widget and module reuses (build once, call often).
# PURPOSE:
#   1. set_style_property(): change a QSS dynamic property and make the stylesheet re-apply it.
#   2. control_label(): the small muted label used next to module controls.
#   3. tool_button(): an icon tool button with the standard look (auto-raise, pointing cursor).
#   4. Blocks: block_layout() (skeleton with a top row), table_view() (+ Ctrl+C copy), copy_selection(),
#      fit_columns(), add_button(), add_menu_action().
#   5. status_label() / set_status(): the muted one-line outcome label of a module (red for errors).
#   6. run_in_thread(owner, work, on_done, on_progress=None): slow work (network, files) off the UI thread;
#      on_done(result, error) (and on_progress(value) for each report) run back on the UI thread.
# CONTRACT:
#   - Requires: PySide6, app.ui.widgets.sidebar.load_icon (imported lazily), QSS selectors #control_label,
#               #module_status_label
#   - Exposes: set_style_property(widget, name, value), control_label(text), tool_button(...),
#              block_layout(widget), table_view(object_name), copy_selection(view), fit_columns(view),
#              add_button(row, text, handler), add_menu_action(menu, text, handler), status_label(),
#              set_status(label, text, is_error), run_in_thread(owner, work, on_done, on_progress=None)
# NOTES:
#   - [2026-11-17] run_in_thread() takes on_progress: work(report) streams values to the UI thread (agent chat).
#   - [2026-11-16] Block helpers moved here from the Database Editor (records views, spreadsheet, monitor,
#                  graph database, comms use them); status_label() / set_status() and run_in_thread() added.
#   - [2026-11-15] Created in the health pass: the unpolish/polish dance was copied into 4 files and the
#                  control label into 3 modules.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - dynamic property: A Qt property set at runtime and matched in QSS as #name[property="value"].
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. HELPERS
#    1.1 set_style_property
#    1.2 control_label
#    1.3 tool_button
#    1.4 block_helpers
#    1.5 status_label
#    1.6 run_in_thread
# ==============================================================================

import threading

from PySide6.QtCore import QObject, QSize, Qt, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QLabel, QMenu, QPushButton, QTableView, QToolButton, QVBoxLayout, QWidget,
)


# ==============================================================================
# [1.1] set_style_property
# ==============================================================================
def set_style_property(widget: QWidget, name: str, value) -> None:
    """Set a QSS dynamic property; booleans are stored as "true"/"false" strings to match QSS."""
    if isinstance(value, bool):
        value = "true" if value else "false"
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    widget.style().unpolish(widget)              # dynamic properties only restyle after a re-polish
    widget.style().polish(widget)
    widget.update()
# --- END [1.1] ---

# ==============================================================================
# [1.2] control_label
# ==============================================================================
def control_label(text: str = "") -> QLabel:
    """Muted caption for a control row (QSS #control_label)."""
    return QLabel(text, objectName="control_label")
# --- END [1.2] ---

# ==============================================================================
# [1.3] tool_button
# ==============================================================================
def tool_button(object_name: str, icon_file: str | None = None, tool_tip: str = "", text: str = "",
                icon_px: int = 16, checkable: bool = False) -> QToolButton:
    """Flat icon (and/or text) button with a pointing-hand cursor."""
    from app.ui.widgets.sidebar import load_icon          # lazy: sidebar imports theme only
    button = QToolButton(objectName=object_name, toolTip=tool_tip, text=text, autoRaise=True, checkable=checkable)
    if icon_file:
        button.setIcon(load_icon(icon_file))
        button.setIconSize(QSize(icon_px, icon_px))
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button
# --- END [1.3] ---

# ==============================================================================
# [1.4] block_helpers
# ==============================================================================
def block_layout(widget: QWidget) -> tuple[QVBoxLayout, QHBoxLayout]:
    """Block skeleton: a vertical layout with a top row that leaves room for the corner controls."""
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(4)
    row = QHBoxLayout()
    layout.addLayout(row)
    return layout, row


def table_view(object_name: str) -> QTableView:
    view = QTableView(objectName=object_name)
    view.setAlternatingRowColors(True)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
    view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    view.setWordWrap(False)
    view.verticalHeader().setDefaultSectionSize(22)
    view.horizontalHeader().setStretchLastSection(False)        # columns keep their width; numbers stay close
    copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, view, context=Qt.ShortcutContext.WidgetShortcut)
    copy_shortcut.activated.connect(lambda: copy_selection(view))
    return view


def copy_selection(view: QTableView) -> None:
    """Selected cells as tab-separated text (rows on lines), like a spreadsheet."""
    indexes = sorted(view.selectedIndexes(), key=lambda index: (index.row(), index.column()))
    if not indexes:
        return
    lines: dict[int, list[str]] = {}
    for index in indexes:
        lines.setdefault(index.row(), []).append(str(index.data(Qt.ItemDataRole.EditRole) or ""))
    QApplication.clipboard().setText("\n".join("\t".join(cells) for cells in lines.values()))


def fit_columns(view: QTableView, max_px: int = 280) -> None:
    view.resizeColumnsToContents()
    for column in range(view.model().columnCount() if view.model() else 0):
        view.setColumnWidth(column, min(view.columnWidth(column), max_px))


def add_button(row: QHBoxLayout, text: str, handler, tool_tip: str = "") -> QPushButton:
    button = QPushButton(text, toolTip=tool_tip)
    button.clicked.connect(handler)
    row.addWidget(button)
    return button


def add_menu_action(menu: QMenu, text: str, handler, is_enabled: bool = True) -> None:
    action = menu.addAction(text)
    action.setEnabled(is_enabled)
    action.triggered.connect(handler)
# --- END [1.4] ---

# ==============================================================================
# [1.5] status_label
# ==============================================================================
def status_label() -> QLabel:
    """One-line outcome of the module's last action (QSS #module_status_label; red when is_error)."""
    return QLabel(objectName="module_status_label", wordWrap=False)


def set_status(label: QLabel, text: str, is_error: bool = False) -> None:
    label.setText(text)
    label.setToolTip(text)
    set_style_property(label, "is_error", is_error)
# --- END [1.5] ---

# ==============================================================================
# [1.6] run_in_thread
# ==============================================================================
class _ThreadJob(QObject):
    finished = Signal(object, object)                    # result, error (emitted from the worker thread)
    progressed = Signal(object)                          # a piece of progress (emitted from the worker thread)

    def __init__(self, owner: QObject, on_done, on_progress=None) -> None:
        super().__init__(owner)                          # lives in the UI thread: the slots below run there
        self.on_done, self.on_progress = on_done, on_progress
        self.finished.connect(self.deliver, Qt.ConnectionType.QueuedConnection)
        self.progressed.connect(self.deliver_progress, Qt.ConnectionType.QueuedConnection)

    @Slot(object, object)
    def deliver(self, result, error) -> None:
        self.on_done(result, error)
        self.deleteLater()

    @Slot(object)
    def deliver_progress(self, value) -> None:
        if self.on_progress is not None:
            self.on_progress(value)


def run_in_thread(owner: QObject, work, on_done, on_progress=None) -> None:
    """Run work() on a worker thread; on_done(result, error) is called on the UI thread afterwards
    (skipped if `owner` was deleted meanwhile). Never touch widgets inside work().
    With on_progress, work is called as work(report): every report(value) reaches on_progress(value) on the
    UI thread, in order, before on_done (streamed answers, download progress)."""
    job = _ThreadJob(owner, on_done, on_progress)

    def report(value) -> None:
        try:
            job.progressed.emit(value)
        except RuntimeError:                              # owner (and job) deleted while working
            pass

    def target() -> None:
        try:
            result, error = (work(report) if on_progress is not None else work()), None
        except Exception as caught:                       # reported to on_done, never lost
            result, error = None, caught
        try:
            job.finished.emit(result, error)
        except RuntimeError:                              # owner (and job) deleted while working
            pass
    threading.Thread(target=target, daemon=True).start()
# --- END [1.6] ---
