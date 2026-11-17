# ==============================================================================
# FILE: app/ui/pages/settings_page.py
# OVERVIEW: SettingsPage - the page behind the sidebar's gear: Data (where DevOS keeps your data, backups) and
#           Engines (attach / detach, check with the conformance kit, add engine packages, the bus inspector).
# PURPOSE:
#   1. Show the data folder and what lives there (and what doesn't: keyring secrets).
#   2. Backups: list (newest first, with why each was taken), Back up now (off the UI thread), Check (every
#      file against the manifest), Restore at next start (the current state is backed up first), cancel a
#      pending restore.
#   3. Engines: every engine package with its version and state (attached / running / detached / why not
#      attached); Attach, Detach, Check (conformance kit, off the UI thread), Add engine package…, Bus inspector.
# CONTRACT:
#   - Requires: app.data.store.backup, app.data.store.database.data_directory, app.ui.ui_tools, engines.runtime,
#               engines.kernel (conformance, packages), app.ui.bus_inspector,
#               QSS selectors #page_title_label, #page_subtitle_label, #settings_section_label,
#               #settings_banner_label
#   - Exposes: SettingsPage(parent=None) with refresh(), back_up_now(), backups_tree, engines_tree, refresh_engines(),
#              check_selected_engine()
# NOTES:
#   - [2026-11-17] Core engines (Knowledge) show "· core" and can't be detached from here (the button is off).
#   - [2026-11-17] Library access goes through the Knowledge engine (app.clients.library), not the content store (Phase 4).
#   - [2026-11-17] Engines section (engine plan Phase 3): attach / detach, check, add packages, bus inspector.
#   - [2026-11-17] Created (replaces the Settings placeholder) in Phase 0 of the engine plan: backups first.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - pending restore: A backup chosen here that DevOS restores when it next starts (backup.RESTORE_NOTE).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SETTINGS_PAGE
#    1.1 imports
#    1.2 settings_page
#    1.3 backups
#    1.4 engines
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import datetime as dt
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from app.data.store.backup import (
    BACKUPS_FOLDER, KEEP_BACKUPS, KEEP_SAFETY_DAYS, BackupError, cancel_restore, create_backup, list_backups,
    pending_restore, schedule_restore, verify_backup,
)
from app.data.content_kinds import size_text
from app.data.store.database import data_directory
from app.ui.bus_inspector import open_bus_inspector
from app.ui.engine_bridge import watch_engine
from app.ui.ui_tools import control_label, run_in_thread, set_status, status_label
from engines.kernel import EngineError
from engines.kernel.conformance import check_package, report
from engines.kernel.packages import PackageError
from engines.runtime import add_package, attach, detach, engine_state, package, packages, user_packages_folder
# --- END [1.1] ---


# ==============================================================================
# [1.2] settings_page
# ==============================================================================
class SettingsPage(QWidget):
    PAGE_MARGIN_PX = 16

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(self.PAGE_MARGIN_PX,) * 4)
        layout.setSpacing(8)
        layout.addWidget(QLabel("Settings", objectName="page_title_label"))
        layout.addWidget(QLabel("How this DevOS installation keeps your data.", objectName="page_subtitle_label"))

        layout.addWidget(QLabel("Data", objectName="settings_section_label"))
        folder_row = QHBoxLayout()
        self.folder_label = QLabel(str(data_directory()), textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse)
        folder_row.addWidget(control_label("Data folder"))
        folder_row.addWidget(self.folder_label, 1)
        open_btn = QPushButton("Open folder")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_directory()))))
        folder_row.addWidget(open_btn)
        layout.addLayout(folder_row)
        about = control_label("Everything DevOS keeps for you lives in this folder: the workspace database (pages, "
                              "layouts, records, chats), your Library (notes, datasets, databases, CAD …) and browser "
                              "logins. Passwords and API keys are in the system keyring, not here, so they don't move "
                              "with a copy of the folder.")
        about.setWordWrap(True)
        layout.addWidget(about)

        layout.addWidget(QLabel("Backups", objectName="settings_section_label"))
        hint = control_label(f"A backup copies the whole data folder (browser caches excluded). DevOS also backs up by "
                             f"itself before it changes how your data is stored. The newest {KEEP_BACKUPS} backups you "
                             f"make are kept, and DevOS's own for {KEEP_SAFETY_DAYS} days.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.settings_banner_label = QLabel(objectName="settings_banner_label", wordWrap=True)
        layout.addWidget(self.settings_banner_label)
        self.backups_tree = QTreeWidget()
        self.backups_tree.setHeaderLabels(["When", "Why", "Files", "Size"])
        self.backups_tree.setRootIsDecorated(False)
        self.backups_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.backups_tree.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.backups_tree, 1)
        buttons = QHBoxLayout()
        self.back_up_btn = QPushButton("Back up now")
        self.back_up_btn.clicked.connect(self.back_up_now)
        self.verify_btn = QPushButton("Check backup", toolTip="Compare every file of the selected backup with its manifest")
        self.verify_btn.clicked.connect(self.verify_selected)
        self.restore_btn = QPushButton("Restore at next start…")
        self.restore_btn.clicked.connect(self.restore_selected)
        self.cancel_restore_btn = QPushButton("Cancel restore")
        self.cancel_restore_btn.clicked.connect(self._cancel_restore)
        folder_btn = QPushButton("Open backups folder")
        folder_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_directory() / BACKUPS_FOLDER))))
        for button in (self.back_up_btn, self.verify_btn, self.restore_btn, self.cancel_restore_btn):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(folder_btn)
        layout.addLayout(buttons)
        self.backup_status_label = status_label()
        layout.addWidget(self.backup_status_label)
        self._build_engines_section(layout)
        self.refresh()

    def showEvent(self, event) -> None:                                 # noqa: N802
        super().showEvent(event)
        self.refresh()                                # DevOS may have backed up by itself meanwhile
        self.refresh_engines()
# --- END [1.2] ---

# ==============================================================================
# [1.3] backups
# ==============================================================================
    def refresh(self) -> None:
        selected = self._selected_name()
        self.backups_tree.clear()
        for info in list_backups():
            when = dt.datetime.fromtimestamp(info.created_at).strftime("%Y-%m-%d %H:%M")
            item = QTreeWidgetItem([when, info.reason + (f" · {info.label}" if info.label else ""), str(info.file_count),
                                    size_text(info.size_bytes)])
            item.setData(0, Qt.ItemDataRole.UserRole, info.name)
            self.backups_tree.addTopLevelItem(item)
            if info.name == selected:
                item.setSelected(True)
        for column in range(4):
            self.backups_tree.resizeColumnToContents(column)
        pending = pending_restore()
        self.settings_banner_label.setText(f"Backup {pending} will be restored when DevOS next starts "
                                           "(your current data is backed up first)." if pending else "")
        self.settings_banner_label.setVisible(bool(pending))
        self.cancel_restore_btn.setVisible(bool(pending))
        self._update_buttons()

    def _selected_name(self) -> str | None:
        items = self.backups_tree.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None

    def _update_buttons(self) -> None:
        has_selection = self._selected_name() is not None
        self.verify_btn.setEnabled(has_selection)
        self.restore_btn.setEnabled(has_selection)

    def back_up_now(self) -> None:
        self.back_up_btn.setEnabled(False)
        set_status(self.backup_status_label, "Backing up…")

        def done(info, error) -> None:
            self.back_up_btn.setEnabled(True)
            if error is not None:
                set_status(self.backup_status_label, str(error), is_error=True)
            else:
                set_status(self.backup_status_label, f"Backed up {info.file_count} files ({size_text(info.size_bytes)})")
            self.refresh()
        run_in_thread(self, lambda: create_backup(), done)

    def verify_selected(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        set_status(self.backup_status_label, f"Checking {name}…")

        def done(problems, error) -> None:
            if error is not None:
                set_status(self.backup_status_label, f"Check failed: {error}", is_error=True)
            elif problems:
                set_status(self.backup_status_label, f"{len(problems)} problem(s): {problems[0]}", is_error=True)
            else:
                set_status(self.backup_status_label, f"{name}: every file matches")
        run_in_thread(self, lambda: verify_backup(name), done)

    def restore_selected(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        answer = QMessageBox.question(self, "Restore backup", f"Restore backup {name}?\n\nDevOS first backs up your "
                                      "current data, then restores this backup the next time it starts. Close and "
                                      "reopen DevOS to finish.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            schedule_restore(name)
            set_status(self.backup_status_label, "Restore scheduled: close and reopen DevOS to finish")
        except BackupError as error:
            set_status(self.backup_status_label, str(error), is_error=True)
        self.refresh()

    def _cancel_restore(self) -> None:
        cancel_restore()
        set_status(self.backup_status_label, "Restore cancelled")
        self.refresh()
# --- END [1.3] ---


# ==============================================================================
# [1.4] engines
# ==============================================================================
    def _build_engines_section(self, layout: QVBoxLayout) -> None:
        layout.addWidget(QLabel("Engines", objectName="settings_section_label"))
        hint = control_label("Engines do DevOS's specialist work. Detach one to switch it off: views that need it show "
                             "a placeholder and come back when you attach it again, and its data is kept. Engines you "
                             f"add live in {user_packages_folder()}.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.engines_tree = QTreeWidget()
        self.engines_tree.setHeaderLabels(["Engine", "Version", "State", "Needs", "From"])
        self.engines_tree.setRootIsDecorated(False)
        self.engines_tree.itemSelectionChanged.connect(self._update_engine_buttons)
        layout.addWidget(self.engines_tree, 1)
        buttons = QHBoxLayout()
        self.attach_btn, self.detach_btn = QPushButton("Attach"), QPushButton("Detach")
        self.check_btn = QPushButton("Check", toolTip="Run the conformance kit on the selected engine")
        self.attach_btn.clicked.connect(lambda: self._attach_or_detach(True))
        self.detach_btn.clicked.connect(lambda: self._attach_or_detach(False))
        self.check_btn.clicked.connect(self.check_selected_engine)
        add_btn = QPushButton("Add engine package…")
        add_btn.clicked.connect(self._add_package)
        inspector_btn = QPushButton("Bus inspector")
        inspector_btn.clicked.connect(lambda: open_bus_inspector(self.window()))
        for button in (self.attach_btn, self.detach_btn, self.check_btn):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(add_btn)
        buttons.addWidget(inspector_btn)
        layout.addLayout(buttons)
        self.engine_status_label = status_label()
        layout.addWidget(self.engine_status_label)
        watch_engine(self, "runtime.*", lambda _message: self.refresh_engines())
        self.refresh_engines()

    def _selected_engine(self) -> str | None:
        items = self.engines_tree.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None

    def refresh_engines(self) -> None:
        selected = self._selected_engine()
        self.engines_tree.clear()
        for item in packages():
            state = engine_state(item.engine_id)
            text = ("Attached · running" if state["running"] else "Attached") if state["attached"] else \
                f"Not attached: {state['failure']}" if state["failure"] and not state["user_detached"] else "Detached"
            row = QTreeWidgetItem([item.title, item.version, text, ", ".join(item.requires) or "—",
                                   ("built in" if item.is_built_in else "your engine packages") +
                                   (" · core" if state["core"] else "")])
            row.setToolTip(0, item.summary)
            row.setData(0, Qt.ItemDataRole.UserRole, item.engine_id)
            self.engines_tree.addTopLevelItem(row)
            if item.engine_id == selected:
                row.setSelected(True)
        for column in range(5):
            self.engines_tree.resizeColumnToContents(column)
        self._update_engine_buttons()

    def _update_engine_buttons(self) -> None:
        engine_id = self._selected_engine()
        state = engine_state(engine_id) if engine_id is not None else {"attached": False, "core": False}
        self.attach_btn.setEnabled(engine_id is not None and not state["attached"])
        self.detach_btn.setEnabled(state["attached"] and not state["core"])
        self.detach_btn.setToolTip(f"{package(engine_id).title} is a core engine: DevOS keeps it attached"
                                   if state["core"] else "Detach the selected engine (its data stays where it is)")
        self.check_btn.setEnabled(engine_id is not None)

    def _attach_or_detach(self, is_attach: bool) -> None:
        engine_id = self._selected_engine()
        if engine_id is None:
            return
        try:
            attach(engine_id) if is_attach else detach(engine_id)
            set_status(self.engine_status_label, f"{'Attached' if is_attach else 'Detached'} {package(engine_id).title}")
        except EngineError as error:
            set_status(self.engine_status_label, str(error), is_error=True)
        self.refresh_engines()

    def check_selected_engine(self) -> None:
        engine_id = self._selected_engine()
        if engine_id is None:
            return
        set_status(self.engine_status_label, f"Checking {package(engine_id).title}…")

        def done(checks, error) -> None:
            if error is not None:
                set_status(self.engine_status_label, f"Check failed: {error}", is_error=True)
                return
            failed = [check for check in checks if not check.ok]
            text = f"{len(checks) - len(failed)} of {len(checks)} checks passed"
            set_status(self.engine_status_label, text + (f" — first problem: {failed[0].name}: {failed[0].detail}"
                                                         if failed else ""), is_error=bool(failed))
            self.engine_status_label.setToolTip(report(checks))
        run_in_thread(self, lambda: check_package(package(engine_id).folder), done)

    def _add_package(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Engine package (a folder with engine.json)")
        if not folder:
            return
        try:
            added = add_package(Path(folder))
            set_status(self.engine_status_label, f"Added {added.title} — select it and Attach")
        except (PackageError, OSError) as error:
            set_status(self.engine_status_label, str(error), is_error=True)
        self.refresh_engines()
# --- END [1.4] ---
