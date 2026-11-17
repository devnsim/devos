# ==============================================================================
# FILE: app/views/types/document.py
# OVERVIEW: Document view - the selected Library item: notes as Markdown with a live preview (Edit / Split /
#           Preview), saved automatically; text files and datasets shown as text; anything else as its details.
# PURPOSE:
#   1. Follows the page's selection; asks its engine for the text ("read_text") and saves edits ("write_text")
#      after a pause in typing, and before switching to another item or hiding.
#   2. [[Note name]] links become clickable in the preview and select that note on the page.
# CONTRACT:
#   - Requires: app.views.api, app.ui.ui_tools, app.ui.canvas.tile_canvas.reserve_controls_space, QSS selectors
#               #note_editor, #note_preview, #document_title_label, #empty_tile_label
#   - Exposes: DocumentView(host), DOCUMENT_VIEW (registered as "document"; needs read_text + write_text), MODES,
#              linkify(text)
# NOTES:
#   - [2026-11-17] Created in Phase 4 of the engine plan (replaces the Notes module's editor and the Library
#                  preview).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - MODES (tuple): "Edit", "Split", "Preview"; saved in the definition's presentation ("mode").
# - AUTOSAVE_DELAY_MS (int): Typing pauses this long before the text is saved (default: 700).
# - PREVIEW_LINES (int): Lines of a non-note text item shown (default: 200).
# - TEXT_EXTENSIONS (tuple): Files shown as text besides notes.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. DOCUMENT_VIEW
#    1.1 helpers
#    1.2 view
#    1.3 loading_and_saving
# ==============================================================================

# ==============================================================================
# [1.1] helpers
# ==============================================================================
from __future__ import annotations

import re
import urllib.parse

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QComboBox, QLabel, QPlainTextEdit, QSplitter, QTextBrowser, QWidget

from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.ui_tools import block_layout
from app.views.api import TableData, ViewType, field_text, register_view_type

MODES = ("Edit", "Split", "Preview")
AUTOSAVE_DELAY_MS = 700
PREVIEW_LINES = 200
TEXT_EXTENSIONS = (".md", ".markdown", ".txt", ".csv", ".json", ".geojson", ".xml", ".yaml", ".yml", ".log")
NOTE_SCHEME = "devos-note"
_LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")


def linkify(text: str) -> str:
    """[[Note]] / [[Note|label]] as Markdown links the preview can follow."""
    return _LINK.sub(lambda match: f"[{(match.group(2) or match.group(1)).strip()}]"
                                   f"({NOTE_SCHEME}:{urllib.parse.quote(match.group(1).strip())})", text)
# --- END [1.1] ---


# ==============================================================================
# [1.2] view
# ==============================================================================
class DocumentView(QWidget):
    def __init__(self, host) -> None:
        super().__init__()
        self.host = host
        self.data: TableData | None = None
        self.shown_id = None
        self.is_note = False
        self.is_dirty = False
        self._loading = False
        layout, row = block_layout(self)
        self.document_title_label = QLabel(objectName="document_title_label")
        row.addWidget(self.document_title_label, 1)
        self.mode_combo = QComboBox(toolTip="Edit, Split (edit + preview) or Preview")
        self.mode_combo.addItems(MODES)
        self.mode_combo.setCurrentText(host.presentation.get("mode", "Split"))
        self.mode_combo.currentTextChanged.connect(self._apply_mode)
        row.addWidget(self.mode_combo)
        row.addWidget(host.view_menu_button())
        reserve_controls_space(row)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.note_editor = QPlainTextEdit(objectName="note_editor", placeholderText="Write Markdown…")
        self.note_editor.textChanged.connect(self._on_text_changed)
        self.note_preview = QTextBrowser(objectName="note_preview", openLinks=False)
        self.note_preview.anchorClicked.connect(self._follow_link)
        self.splitter.addWidget(self.note_editor)
        self.splitter.addWidget(self.note_preview)
        layout.addWidget(self.splitter, 1)
        self.empty_label = QLabel("Select an item to read or write it.", objectName="empty_tile_label",
                                  alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label, 1)
        self._save_timer = QTimer(self, singleShot=True, interval=AUTOSAVE_DELAY_MS, timeout=self.save)
        self._show_empty()

    def set_data(self, data: TableData) -> None:
        self.data = data
        row = data.row(self.shown_id) if self.shown_id is not None else None
        if self.shown_id is not None and row is None:
            self._show_empty()                            # deleted elsewhere
        elif row is not None:
            self.document_title_label.setText(data.title(row))
            if not self.is_note:
                self._load(self.shown_id, force=True)      # details may have changed (size, name)
        if self.shown_id is None and self.host.selected_id is not None:
            self.set_selection(self.host.selected_id)

    def set_selection(self, record_id) -> None:
        if record_id != self.shown_id:
            self._load(record_id)

    def _apply_mode(self, mode: str) -> None:
        editable = self.is_note
        self.note_editor.setVisible(editable and mode in ("Edit", "Split"))
        self.note_preview.setVisible(not editable or mode in ("Split", "Preview"))
        self.mode_combo.setVisible(editable)
        self.host.set_presentation("mode", mode)

    def _show_empty(self) -> None:
        self.save()
        self.shown_id, self.is_note = None, False
        self.splitter.hide()
        self.mode_combo.hide()
        self.document_title_label.setText("")
        self.empty_label.show()

    def _follow_link(self, url: QUrl) -> None:
        if url.scheme() == NOTE_SCHEME and self.data is not None:
            name = urllib.parse.unquote(url.path() or url.toString().split(":", 1)[1]).casefold()
            target = next((row for row in self.data.rows if self.data.title(row).casefold() == name), None)
            if target is not None:
                self.host.select(target[self.data.key])
            else:
                self.host.report(f"There is no note called “{name}” here", is_error=True)
        elif url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
# --- END [1.2] ---

# ==============================================================================
# [1.3] loading_and_saving
# ==============================================================================
    def _load(self, record_id, force: bool = False) -> None:
        if record_id != self.shown_id:
            self.save()                                    # pending typing belongs to the item being left
        row = self.data.row(record_id) if self.data is not None and record_id is not None else None
        if row is None:
            self._show_empty()
            return
        self.shown_id = record_id
        self.document_title_label.setText(self.data.title(row))
        self.is_note = row.get("kind") == "note"
        self.empty_label.hide()
        self.splitter.show()
        self._apply_mode(self.mode_combo.currentText())
        is_text = self.is_note or str(row.get("extension", "")).lower() in TEXT_EXTENSIONS
        if not is_text:
            details = [f"**{self.data.title(row)}**", ""] + [
                f"- {field['title']}: {field_text(field, row.get(field['key']))}" for field in self.data.fields]
            self.note_preview.setMarkdown("\n".join(details))
            return
        if force and self.is_note:
            return
        payload = {"item_id": record_id} if self.is_note else {"item_id": record_id, "max_lines": PREVIEW_LINES}
        self.host.request("read_text", payload, lambda reply, error, record_id=record_id: self._show_text(record_id, reply, error))

    def _show_text(self, record_id, reply: dict | None, error) -> None:
        if record_id != self.shown_id:
            return                                         # the selection moved on meanwhile
        if error is not None:
            self.note_preview.setPlainText(str(error))
            return
        text = reply["text"]
        if self.is_note:
            self._loading = True
            self.note_editor.setPlainText(text)
            self._loading = False
            self.is_dirty = False
            self.note_preview.setMarkdown(linkify(text))
        else:
            more = reply.get("more_lines", 0)
            self.note_preview.setPlainText(text + (f"\n… {more:,} more lines" if more else ""))

    def _on_text_changed(self) -> None:
        if self._loading or not self.is_note:
            return
        self.is_dirty = True
        self.note_preview.setMarkdown(linkify(self.note_editor.toPlainText()))
        self._save_timer.start()

    def save(self) -> None:
        """Save typing now (autosave timer, switching items, hiding)."""
        self._save_timer.stop()
        if self.is_dirty and self.shown_id is not None and self.is_note:
            self.is_dirty = False
            self.host.request("write_text", {"item_id": self.shown_id, "text": self.note_editor.toPlainText()},
                              lambda _reply, error: error and self.host.report(f"Not saved: {error}", is_error=True))

    def hideEvent(self, event) -> None:                                  # noqa: N802
        self.save()
        super().hideEvent(event)


DOCUMENT_VIEW = ViewType("document", "Document", "The selected item: notes in Markdown with a live preview, text and details",
                         ("table",), DocumentView, requests=("read_text", "write_text"))
register_view_type(DOCUMENT_VIEW)
# --- END [1.3] ---
