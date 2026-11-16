# ==============================================================================
# FILE: app/ui/widgets/log_panel.py
# OVERVIEW: The System Log panel: a live, readable feed of what DevOS is doing (the activity log).
# PURPOSE:
#   1. LogBridge: hands activity-log entries to the Qt thread (entries can come from any thread).
#   2. LogPanel: colour-coded entries; filter by level, source and text; browse past sessions saved in
#      the database; copy; auto-scroll; close. Opens docked under the workspace (main_window.py).
# CONTRACT:
#   - Requires: app.data.activity_log, app.ui.theme colours, app.ui.ui_tools, QSS selectors #log_panel,
#               #log_header_panel, #log_title_label, #log_view, #control_label
#   - Exposes: LogBridge (signal entry_added(object)), get_log_bridge(), LogPanel (signal close_requested)
# NOTES:
#   - [2026-11-15] Created. The view keeps the last MAX_LINES lines of the chosen filter; changing a filter
#                  re-renders from the in-memory session (or the chosen past session).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - MAX_LINES (int): Lines kept in the view (default: 3000).
# - LEVEL_FILTERS: "Info and up" (default), "Everything", "Warnings and errors", "Errors only".
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. BRIDGE
#    1.1 log_bridge
# 2. PANEL
#    2.1 init_panel
#    2.2 filtering_and_rendering
#    2.3 sessions
# ==============================================================================

# ==============================================================================
# [1.1] log_bridge
# ==============================================================================
import html
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget,
)

from app.data.activity_log import DEBUG, ERROR, INFO, SUCCESS, WARNING, LogEntry, get_activity_log
from app.ui.theme import ACCENT_HIGHLIGHT_COLOR, MUTED_TEXT_COLOR, TEXT_COLOR
from app.ui.ui_tools import control_label, tool_button

LEVEL_COLORS = {DEBUG: "#6f6f6f", INFO: TEXT_COLOR, SUCCESS: "#4caf7d", WARNING: "#e0a030", ERROR: "#ef5350"}
LEVEL_MARKS = {DEBUG: "·", INFO: "•", SUCCESS: "✓", WARNING: "⚠", ERROR: "✕"}
LEVEL_FILTERS = {"Info and up": INFO, "Everything": DEBUG, "Warnings and errors": WARNING, "Errors only": ERROR}
THIS_SESSION = "This session"


class LogBridge(QObject):
    entry_added = Signal(object)          # LogEntry, always delivered on the Qt (main) thread


_bridge: LogBridge | None = None


def get_log_bridge() -> LogBridge:
    """One bridge per app: subscribes to the activity log once, re-emits as a Qt signal."""
    global _bridge
    if _bridge is None:
        _bridge = LogBridge()
        get_activity_log().add_listener(lambda entry: _bridge.entry_added.emit(entry))
    return _bridge
# --- END [1.1] ---


# ==============================================================================
# [2.1] init_panel
# ==============================================================================
class LogPanel(QWidget):
    close_requested = Signal()
    MAX_LINES = 3000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="log_panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.log = get_activity_log()
        self._shown_entries: list[LogEntry] | None = None          # None = live session
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget(objectName="log_header_panel")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(header)
        row.setContentsMargins(8, 2, 4, 2)
        row.addWidget(QLabel("System Log", objectName="log_title_label"))
        self.session_combo = QComboBox(toolTip="Show this session or a saved one")
        self.session_combo.addItem(THIS_SESSION, None)
        self.session_combo.activated.connect(self._on_session_chosen)
        self.session_combo.installEventFilter(self)                 # refresh the list when opened
        self.level_combo = QComboBox(toolTip="Which levels to show")
        self.level_combo.addItems(LEVEL_FILTERS)
        self.source_combo = QComboBox(toolTip="Show one source only")
        self.source_combo.addItem("All sources", None)
        self.search_edit = QLineEdit(placeholderText="filter text…")
        self.search_edit.setClearButtonEnabled(True)
        self.follow_check = QCheckBox("Auto-scroll", checked=True)
        copy_btn = tool_button("log_tool_btn", text="Copy", tool_tip="Copy the shown lines")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.log_view.toPlainText()))
        close_btn = tool_button("log_tool_btn", "tab_close.svg", "Close the log")
        close_btn.clicked.connect(self.close_requested)
        for widget, stretch in ((self.session_combo, 0), (control_label("show"), 0), (self.level_combo, 0),
                                (self.source_combo, 0), (self.search_edit, 1), (self.follow_check, 0),
                                (copy_btn, 0), (close_btn, 0)):
            row.addWidget(widget, stretch)
        layout.addWidget(header)

        self.log_view = QPlainTextEdit(objectName="log_view", readOnly=True)
        self.log_view.setMaximumBlockCount(self.MAX_LINES)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.log_view, 1)

        for combo in (self.level_combo, self.source_combo):
            combo.currentIndexChanged.connect(self.render)
        self.search_edit.textChanged.connect(self.render)
        get_log_bridge().entry_added.connect(self._on_entry)
        self.render()

    def eventFilter(self, watched, event) -> bool:
        from PySide6.QtCore import QEvent
        if watched is self.session_combo and event.type() == QEvent.Type.MouseButtonPress:
            self._fill_sessions()
        return False
# --- END [2.1] ---

# ==============================================================================
# [2.2] filtering_and_rendering
# ==============================================================================
    def _passes(self, entry: LogEntry) -> bool:
        if entry.level < LEVEL_FILTERS[self.level_combo.currentText()]:
            return False
        source = self.source_combo.currentData()
        if source and entry.source != source:
            return False
        text = self.search_edit.text().strip().lower()
        return not text or text in entry.message.lower() or text in entry.source.lower()

    def _html(self, entry: LogEntry) -> str:
        stamp = time.strftime("%H:%M:%S", time.localtime(entry.time)) + f".{int(entry.time % 1 * 1000):03d}"
        color = LEVEL_COLORS.get(entry.level, TEXT_COLOR)
        message = html.escape(entry.message)
        if entry.details:
            message += f" <span style='color:{MUTED_TEXT_COLOR}'>(details: {html.escape(entry.details.strip().splitlines()[-1])})</span>"
        return (f"<span style='color:{MUTED_TEXT_COLOR}'>{stamp}</span>&nbsp;&nbsp;"
                f"<span style='color:{color}'>{LEVEL_MARKS.get(entry.level, '•')}</span>&nbsp;&nbsp;"
                f"<span style='color:{ACCENT_HIGHLIGHT_COLOR}'>{html.escape(entry.source)}</span>&nbsp;&nbsp;"
                f"<span style='color:{color}'>{message}</span>")

    def _note_source(self, source: str) -> None:
        if self.source_combo.findData(source) < 0:
            self.source_combo.addItem(source, source)

    def render(self) -> None:
        entries = list(self.log.entries) if self._shown_entries is None else self._shown_entries
        for entry in entries:
            self._note_source(entry.source)
        lines = [self._html(entry) for entry in entries if self._passes(entry)][-self.MAX_LINES:]
        self.log_view.clear()
        self.log_view.appendHtml("<br>".join(lines))
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _on_entry(self, entry: LogEntry) -> None:
        self._note_source(entry.source)
        if self._shown_entries is not None or not self._passes(entry):
            return
        scrollbar = self.log_view.verticalScrollBar()
        self.log_view.appendHtml(self._html(entry))
        if self.follow_check.isChecked():
            scrollbar.setValue(scrollbar.maximum())
# --- END [2.2] ---

# ==============================================================================
# [2.3] sessions
# ==============================================================================
    def _fill_sessions(self) -> None:
        current = self.session_combo.currentData()
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        self.session_combo.addItem(THIS_SESSION, None)
        for session in self.log.past_sessions():
            started = time.strftime("%Y-%m-%d %H:%M", time.localtime(session["started_at"]))
            self.session_combo.addItem(f"{started} · {session['entry_count']} entries", session["session_id"])
        index = self.session_combo.findData(current)
        self.session_combo.setCurrentIndex(max(index, 0))
        self.session_combo.blockSignals(False)

    def _on_session_chosen(self) -> None:
        session_id = self.session_combo.currentData()
        self._shown_entries = None if session_id is None else self.log.load_session(session_id)
        self.render()
# --- END [2.3] ---
