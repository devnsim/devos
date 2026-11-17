# ==============================================================================
# FILE: tests/test_data_safety.py
# OVERVIEW: Phase 0 of the engine plan - the safety net: backups and restores, the safety backup before
#           migrations, the frozen data_v0 fixture opening in today's app, and the layer-boundary check.
# PURPOSE:
#   1. A backup is consistent (SQLite WAL content included), skips caches, shares unchanged files with the
#      previous backup, verifies against its manifest, and restores the data folder exactly.
#   2. Restores happen at start-up (scheduled from Settings) or from the command line, never under a
#      running DevOS; the state before a restore is itself backed up.
#   3. Upgrading tables that hold data takes a backup first, and doesn't migrate when that fails.
#   4. tests/fixtures/data_v0 (made by the app before the engine plan) opens with every page, record,
#      chat, file and database intact.
# CONTRACT:
#   - Requires: tests/conftest.py fixtures, app.data.store.backup, app.ui.pages.settings_page,
#               tests/fixtures (data_v0, check_data_fixture.py), tools/check_standard.py
#   - Exposes: pytest tests
# NOTES:
#   - [2026-11-17] check_standard's ROADMAP.md reminder is tested here too.
#   - [2026-11-17] Created in Phase 0 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - FIXTURES (Path): tests/fixtures.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. HELPERS
#    1.1 helpers
# 2. TESTS
#    2.1 backups
#    2.2 restore
#    2.3 migrations
#    2.4 fixture
#    2.5 boundaries_and_settings
# ==============================================================================

# ==============================================================================
# [1.1] helpers
# ==============================================================================
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def make_data_folder(root: Path) -> tuple[Path, sqlite3.Connection]:
    """A small data folder: a WAL database with rows still in the WAL (its connection stays open, like a
    running DevOS), library files, a browser profile with a cache."""
    root.mkdir(parents=True)
    connection = sqlite3.connect(root / "devos.sqlite3")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")              # keep the rows in the WAL file
    connection.execute("CREATE TABLE notes (note_id INTEGER PRIMARY KEY, text TEXT)")
    connection.executemany("INSERT INTO notes (text) VALUES (?)", [("one",), ("two",)])
    connection.commit()
    (root / "library" / "note").mkdir(parents=True)
    (root / "library" / "note" / "a.md").write_text("# A\n")
    (root / "library" / "dataset").mkdir(parents=True)
    (root / "library" / "dataset" / "big.csv").write_text("x\n" * 5000)
    (root / "browser" / "storage").mkdir(parents=True)
    (root / "browser" / "storage" / "Cookies").write_text("cookie")
    (root / "browser" / "cache").mkdir(parents=True)
    (root / "browser" / "cache" / "blob").write_text("rebuildable")
    return root, connection


def rows(path: Path) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return [text for (text,) in connection.execute("SELECT text FROM notes ORDER BY note_id")]
    finally:
        connection.close()
# --- END [1.1] ---


# ==============================================================================
# [2.1] backups
# ==============================================================================
def test_backup_is_consistent_skips_caches_and_shares_unchanged_files(tmp_path):
    from app.data.store.backup import MANIFEST_NAME, create_backup, list_backups, verify_backup
    data, live = make_data_folder(tmp_path / "data")
    assert (data / "devos.sqlite3-wal").stat().st_size > 0        # the rows really are only in the WAL

    first = create_backup("first", data_dir=data)
    copy = first.path
    assert rows(copy / "devos.sqlite3") == ["one", "two"]          # WAL content made it into the copy
    assert not (copy / "devos.sqlite3-wal").exists() and not (copy / "browser" / "cache").exists()
    assert (copy / "browser" / "storage" / "Cookies").read_text() == "cookie"
    assert (copy / MANIFEST_NAME).exists() and verify_backup(first.name, data) == []
    assert first.reason == "manual" and first.label == "first" and first.file_count == 4

    live.execute("INSERT INTO notes (text) VALUES ('three')")
    live.commit()
    time.sleep(1.1)                                                # a different backup name (seconds)
    second = create_backup(data_dir=data)
    assert rows(second.path / "devos.sqlite3") == ["one", "two", "three"]
    big = Path("library/dataset/big.csv")
    assert (second.path / big).stat().st_ino == (first.path / big).stat().st_ino   # unchanged: hard-linked
    assert [info.name for info in list_backups(data)] == [second.name, first.name]

    (first.path / "library" / "note" / "a.md").write_text("tampered")
    assert verify_backup(first.name, data) == ["changed: library/note/a.md"]
    live.close()


def test_old_backups_are_pruned(tmp_path, monkeypatch):
    from app.data.store import backup
    data, live = make_data_folder(tmp_path / "data")
    live.close()
    monkeypatch.setattr(backup, "KEEP_BACKUPS", 2)
    names = [backup.create_backup(f"b{index}", data_dir=data).name for index in range(4)]
    safety = backup.create_backup(reason="before something", data_dir=data)
    kept = [info.name for info in backup.list_backups(data)]
    assert kept == [safety.name, names[3], names[2]]               # 2 manual + the recent safety backup
# --- END [2.1] ---


# ==============================================================================
# [2.2] restore
# ==============================================================================
def test_restore_puts_back_exactly_what_was_backed_up(tmp_path):
    from app.data.store.backup import (
        apply_pending_restore, create_backup, list_backups, pending_restore, schedule_restore,
    )
    data, live = make_data_folder(tmp_path / "data")
    original = create_backup(data_dir=data)
    live.execute("DELETE FROM notes")
    live.commit()
    live.close()
    (data / "library" / "note" / "a.md").unlink()
    (data / "library" / "note" / "new.md").write_text("made after the backup")

    schedule_restore(original.name, data)
    assert pending_restore(data) == original.name
    restored = apply_pending_restore(data)                         # what app.main does at start-up
    assert restored.name == original.name and pending_restore(data) is None
    assert rows(data / "devos.sqlite3") == ["one", "two"]
    assert (data / "library" / "note" / "a.md").read_text() == "# A\n"
    assert not (data / "library" / "note" / "new.md").exists()
    safety = next(info for info in list_backups(data) if info.reason.startswith("before restore"))
    assert (safety.path / "library" / "note" / "new.md").exists()  # the replaced state is kept
    (data / "library" / "note" / "a.md").write_text("edited after restore")
    assert (original.path / "library" / "note" / "a.md").read_text() == "# A\n"   # restored as copies


def test_restore_refuses_while_devos_runs_and_rejects_damaged_backups(tmp_path):
    from app.data.store.backup import BackupError, LOCK_NAME, create_backup, restore_backup
    data, live = make_data_folder(tmp_path / "data")
    live.close()
    info = create_backup(data_dir=data)
    holder = subprocess.Popen([sys.executable, "-c", (
        "import fcntl, sys, time; handle = open(sys.argv[1], 'w'); fcntl.flock(handle, fcntl.LOCK_EX); "
        "print('locked', flush=True); time.sleep(30)"), str(data / LOCK_NAME)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(BackupError, match="DevOS is running"):
            restore_backup(info.name, data)
    finally:
        holder.kill()
        holder.wait()
    (info.path / "library" / "note" / "a.md").unlink()
    with pytest.raises(BackupError, match="damaged"):
        restore_backup(info.name, data)
    assert (data / "library" / "note" / "a.md").exists()           # nothing was changed
# --- END [2.2] ---


# ==============================================================================
# [2.3] migrations
# ==============================================================================
def test_upgrading_tables_backs_up_first_and_stops_if_it_cannot(database, monkeypatch):
    from app.data.store import backup
    from app.data.store.database import DATABASE_FILE_NAME, Database, data_directory
    version_1 = "CREATE TABLE phase0_items (item_id INTEGER PRIMARY KEY)"
    version_2 = "ALTER TABLE phase0_items ADD COLUMN name TEXT"
    database.migrate("phase0", [version_1])                        # new tables: nothing to protect yet
    assert not [info for info in backup.list_backups() if "phase0" in info.reason]

    def broken(*_args, **_kwargs):
        raise backup.BackupError("disk full")
    monkeypatch.setattr(backup, "create_backup", broken)
    with pytest.raises(backup.BackupError):
        Database(data_directory() / DATABASE_FILE_NAME).migrate("phase0", [version_1, version_2])
    assert database.schema_version("phase0") == 1                  # not migrated without a backup
    monkeypatch.undo()

    Database(data_directory() / DATABASE_FILE_NAME).migrate("phase0", [version_1, version_2])
    assert database.schema_version("phase0") == 2
    assert [info for info in backup.list_backups() if info.reason == "before migrating phase0 to version 2"]
# --- END [2.3] ---


# ==============================================================================
# [2.4] fixture
# ==============================================================================
def test_data_v0_fixture_opens_in_the_current_app(tmp_path):
    expected = json.loads((FIXTURES / "data_v0.expected.json").read_text())
    copy = tmp_path / "data_v0"
    shutil.copytree(FIXTURES / "data_v0", copy)                    # never open the frozen original
    result = subprocess.run([sys.executable, str(FIXTURES / "check_data_fixture.py"), str(copy)],
                            capture_output=True, text=True, timeout=240)
    report_line = [line for line in result.stdout.splitlines() if line.startswith("{")]
    assert report_line, f"the check didn't finish:\n{result.stderr[-3000:]}"
    report = json.loads(report_line[-1])
    for key, value in expected.items():
        if key not in ("generation", "made_at"):
            assert report[key] == value, key
    assert report["errors"] == []
# --- END [2.4] ---


# ==============================================================================
# [2.5] boundaries_and_settings
# ==============================================================================
def test_engines_and_the_data_layer_stay_free_of_qt():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from check_standard import boundary_errors
    lines = ["from PySide6.QtCore import Qt", "    import app.ui.theme", "from app.data.store import records", "import numpy"]
    assert len(boundary_errors("engines/data/engine.py", lines)) == 2
    assert boundary_errors("app/data/x.py", ["    from app.modules.notes.view import NotesModule"])
    assert boundary_errors("app/ui/x.py", lines) == []


def test_check_standard_warns_when_the_roadmap_is_left_behind():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from check_standard import roadmap_is_stale
    assert roadmap_is_stale({"app/x.py"}) and not roadmap_is_stale({"app/x.py", "ROADMAP.md"})
    assert not roadmap_is_stale(set()) and not roadmap_is_stale({"ROADMAP.md"})


def test_settings_page_backs_up_and_schedules_a_restore(qapp, database, wait_for, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from app.data.store.backup import cancel_restore, list_backups, pending_restore
    from app.ui.pages.settings_page import SettingsPage
    page = SettingsPage()
    page.show()
    before = len(list_backups())
    page.back_up_now()
    assert wait_for(lambda: len(list_backups()) == before + 1 and page.back_up_btn.isEnabled(), 20)
    assert page.backups_tree.topLevelItemCount() == len(list_backups())
    page.backups_tree.topLevelItem(0).setSelected(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    page.restore_selected()
    assert pending_restore() == list_backups()[0].name and not page.settings_banner_label.isHidden()
    page._cancel_restore()
    assert pending_restore() is None and page.settings_banner_label.isHidden()
    cancel_restore()
    page.close()
# --- END [2.5] ---
