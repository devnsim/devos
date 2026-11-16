# ==============================================================================
# FILE: app/modules/notes/view.py
# OVERVIEW: Markdown notes tool (Obsidian-style): pick or create a note, name it, write Markdown with a
#           live preview. Notes are files in the user's content library, saved automatically.
# PURPOSE:
#   1. Note picker (all notes in the library), name field (renames the note), New, Delete.
#   2. Edit / Split / Preview modes; the preview renders Markdown (headings, lists, links, tables, code).
#   3. Autosave AUTOSAVE_DELAY_MS after typing stops; the open note is remembered in module settings.
# CONTRACT:
#   - Requires: app.modules.base.ModuleView, app.data.store.content (get_content_store),
#               app.data.activity_log, app.ui (ui_tools, canvas.tile_canvas), QSS selectors #notes_panel,
#               #note_editor, #note_preview, #note_name_edit
#   - Exposes: NotesModule(parent=None, context=None); open_note(item_id); new_note(); note_id
# NOTES:
#   - [2026-11-15] Created (reusable tools). Create / rename / delete are announced in the log; autosaves
#                  are debug-level so typing doesn't flood it.
#   - [2026-11-15] Autosave tracks a dirty flag: the timer-fired save was skipped (timer no longer active,
#                  and programmatic edits don't set the document's modified flag).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - AUTOSAVE_DELAY_MS (int | ms): Quiet time after the last keystroke before saving (default: 800).
# - mode (str): "Edit", "Split" (editor + preview) or "Preview".
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. NOTES_MODULE
#    2.1 init
#    2.2 notes
#    2.3 editing
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTextBrowser,
    QVBoxLayout, QWidget,
)

from app.data.activity_log import get_activity_log
from app.data.store.content import get_content_store
from app.modules.base import ModuleView
from app.ui.canvas.tile_canvas import reserve_controls_space

MODES = ("Edit", "Split", "Preview")
NEW_NOTE_TEXT = "# {title}\n\n"
# --- END [1.1] ---


# ==============================================================================
# [2.1] init
# ==============================================================================
class NotesModule(ModuleView):
    AUTOSAVE_DELAY_MS = 800

    def __init__(self, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, context, "notes_panel")
        self.store, self.log = get_content_store(), get_activity_log()
        self.note_id: int | None = None
        self.dirty = False                                  # unsaved edits in the editor
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        self.note_combo = QComboBox(toolTip="Your notes")
        self.note_combo.activated.connect(lambda: self.open_note(self.note_combo.currentData()))
        self.note_name_edit = QLineEdit(objectName="note_name_edit", placeholderText="note name")
        self.note_name_edit.editingFinished.connect(self._on_rename)
        new_btn = QPushButton("New", toolTip="Start a new note")
        new_btn.clicked.connect(self.new_note)
        delete_btn = QPushButton("Delete", toolTip="Delete this note")
        delete_btn.clicked.connect(self._on_delete)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(MODES)
        self.mode_combo.setCurrentText(self.setting("mode", "Split"))
        self.mode_combo.currentTextChanged.connect(self._apply_mode)
        for widget, stretch in ((self.note_combo, 1), (self.note_name_edit, 1), (new_btn, 0), (delete_btn, 0),
                                (self.mode_combo, 0)):
            row.addWidget(widget, stretch)
        reserve_controls_space(row)
        layout.addLayout(row)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, objectName="pane_splitter")
        self.note_editor = QPlainTextEdit(objectName="note_editor", placeholderText="Write Markdown here…")
        self.note_editor.setFont(QFont("monospace", 10))
        self.note_editor.textChanged.connect(self._on_text_changed)
        self.note_preview = QTextBrowser(objectName="note_preview", openExternalLinks=True)
        self.splitter.addWidget(self.note_editor)
        self.splitter.addWidget(self.note_preview)
        layout.addWidget(self.splitter, 1)
        self.autosave_timer = QTimer(self, singleShot=True, interval=self.AUTOSAVE_DELAY_MS, timeout=self.save)

        self.store.add_listener(self._on_content_changed)
        self._fill_note_combo()
        saved_id = self.setting("note_id")
        if saved_id is not None and self.store.get(saved_id) is not None:
            self.open_note(saved_id)
        elif self.store.items("note"):
            self.open_note(self.store.items("note")[0].item_id)
        else:
            self.new_note()
        self._apply_mode(self.mode_combo.currentText())
# --- END [2.1] ---

# ==============================================================================
# [2.2] notes
# ==============================================================================
    def _fill_note_combo(self) -> None:
        self.note_combo.blockSignals(True)
        self.note_combo.clear()
        for item in self.store.items("note"):
            self.note_combo.addItem(item.name, item.item_id)
        index = self.note_combo.findData(self.note_id)
        self.note_combo.setCurrentIndex(max(index, 0))
        self.note_combo.blockSignals(False)

    def open_note(self, item_id: int | None) -> None:
        if item_id is None:
            return
        self.save()                                         # don't lose the note being left
        item = self.store.get(item_id)
        if item is None:
            self.log.warning("notes", f"Note #{item_id} no longer exists")
            return
        self.note_id = item_id
        self.note_editor.blockSignals(True)
        self.note_editor.setPlainText(self.store.read_text(item_id))
        self.note_editor.blockSignals(False)
        self.dirty = False
        self.note_name_edit.setText(item.name)
        self._render_preview()
        self._fill_note_combo()
        self.save_setting("note_id", item_id)
        self.log.debug("notes", f"Opened note “{item.name}”")

    def new_note(self) -> None:
        name = self.store.unique_name("note", "Untitled note")
        item = self.store.create_text("note", name, NEW_NOTE_TEXT.format(title=name))
        self.open_note(item.item_id)
        self.note_name_edit.setFocus()
        self.note_name_edit.selectAll()

    def _on_rename(self) -> None:
        if self.note_id is not None:
            name = self.store.rename(self.note_id, self.note_name_edit.text())
            self.note_name_edit.setText(name)

    def _on_delete(self) -> None:
        if self.note_id is None:
            return
        name = self.note_name_edit.text()
        if QMessageBox.question(self, "Delete note", f"Delete “{name}”? This can't be undone.") != QMessageBox.StandardButton.Yes:
            return
        self.autosave_timer.stop()
        deleted, self.note_id = self.note_id, None
        self.store.delete(deleted)
        remaining = self.store.items("note")
        if remaining:
            self.open_note(remaining[0].item_id)
        else:
            self.new_note()

    def _on_content_changed(self, kind: str) -> None:
        if kind == "note":
            self._fill_note_combo()
# --- END [2.2] ---

# ==============================================================================
# [2.3] editing
# ==============================================================================
    def _on_text_changed(self) -> None:
        self.dirty = True
        self.autosave_timer.start()
        if self.note_preview.isVisible():
            self._render_preview()

    def _render_preview(self) -> None:
        self.note_preview.setMarkdown(self.note_editor.toPlainText())

    def save(self) -> None:
        if self.note_id is None or not self.dirty:
            return
        self.autosave_timer.stop()
        try:
            self.store.write_text(self.note_id, self.note_editor.toPlainText())
            self.dirty = False
            self.log.debug("notes", f"Saved note “{self.note_name_edit.text()}”")
        except OSError as error:
            self.log.error("notes", f"Could not save note “{self.note_name_edit.text()}”", error)

    def _apply_mode(self, mode: str) -> None:
        self.note_editor.setVisible(mode in ("Edit", "Split"))
        self.note_preview.setVisible(mode in ("Split", "Preview"))
        if self.note_preview.isVisible():
            self._render_preview()
        self.save_setting("mode", mode)

    def hideEvent(self, event) -> None:
        self.save()                                         # leaving the page / tab: save now
        super().hideEvent(event)
# --- END [2.3] ---
