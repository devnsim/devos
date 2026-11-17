# ==============================================================================
# FILE: app/data/store/backup.py
# OVERVIEW: Backups of the DevOS data folder - a consistent copy of everything the user would lose (the
#           workspace database, the Library, user databases, browser logins), restorable as a whole.
# PURPOSE:
#   1. create_backup(): SQLite files are copied with SQLite's online backup (consistent even while DevOS
#      writes, WAL included); other files are copied, or hard-linked to the previous backup when unchanged
#      (so ten backups of a large Library don't cost ten times the space). A manifest lists every file
#      with its size and SHA-256.
#   2. Restore happens at start-up, before anything opens the data: schedule_restore() from the app, then
#      apply_pending_restore() in app.main. The current state is backed up first ("before restore").
#   3. verify_backup() checks a backup against its manifest; old backups are pruned (newest KEEP_BACKUPS
#      kept, plus every "before ..." safety backup of the last KEEP_SAFETY_DAYS days).
#   4. Command line: python -m app.data.store.backup [list | create [label] | verify NAME | restore NAME].
# CONTRACT:
#   - Requires: app.data.store.database.data_directory, app.clients.library.size_text, app.data.activity_log,
#               fcntl (optional: the running-DevOS lock). No Qt.
#   - Exposes: create_backup(label="", reason="manual", data_dir=None) -> BackupInfo, list_backups(),
#              verify_backup(name) -> list[str], schedule_restore(name), pending_restore(), cancel_restore(),
#              apply_pending_restore() -> BackupInfo | None, restore_backup(name) (only while DevOS is closed),
#              hold_running_lock(), prune_backups(), BackupInfo, BackupError, BACKUPS_FOLDER, KEEP_BACKUPS
# NOTES:
#   - [2026-11-17] Library access goes through the Knowledge engine (app.clients.library), not the content store (Phase 4).
#   - [2026-11-17] Created in Phase 0 of the engine plan (docs/architecture/engine-plan.md): every data
#                  migration from here on is protected by a backup.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - backup: A folder <data>/backups/<YYYY-MM-DD_HHMMSS>[_label]/ holding a copy of the data folder and
#   MANIFEST_NAME (what was copied, sizes, SHA-256, why the backup was taken).
# - reason (str): Why a backup exists: "manual", "before restore", "before migrating <namespace>" ...
# - safety backup: One DevOS took by itself before changing data (reason starts with "before").
# - EXCLUDED_NAMES: Rebuildable or transient things never copied (browser caches, SQLite side files,
#   the backups folder itself, the pending-restore note).
# - RESTORE_NOTE (str): File in the data folder naming the backup to restore at the next start.
# - LOCK_NAME (str): File a running DevOS holds an advisory lock on (restores refuse while it is held).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. BACKUP
#    1.1 imports_and_constants
#    1.2 types
#    1.3 copying
#    1.4 create_and_list
#    1.5 verify_and_prune
# 2. RESTORE
#    2.1 restore
#    2.2 command_line
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_constants
# ==============================================================================
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from app.data.activity_log import get_activity_log
from app.data.content_kinds import size_text
from app.data.store.database import data_directory

try:
    import fcntl                                      # Linux / macOS: advisory lock while DevOS runs
except ImportError:                                   # pragma: no cover - other platforms run without the guard
    fcntl = None

BACKUPS_FOLDER = "backups"
MANIFEST_NAME = "backup.json"
RESTORE_NOTE = "restore-pending.json"
LOCK_NAME = "devos.lock"
KEEP_BACKUPS = 5
KEEP_SAFETY_DAYS = 14
FORMAT_VERSION = 1
SQLITE_HEADER = b"SQLite format 3\x00"
EXCLUDED_NAMES = {BACKUPS_FOLDER, RESTORE_NOTE, LOCK_NAME, "cache", "__pycache__"}
EXCLUDED_SUFFIXES = ("-wal", "-shm", "-journal")
SOURCE = "backup"
# --- END [1.1] ---


# ==============================================================================
# [1.2] types
# ==============================================================================
class BackupError(Exception):
    """A backup or restore couldn't be done; the message says why and what was kept."""


@dataclass(frozen=True)
class BackupInfo:
    name: str
    path: Path
    created_at: float
    label: str
    reason: str
    file_count: int
    size_bytes: int

    @property
    def is_safety(self) -> bool:
        return self.reason.startswith("before")

    def describe(self) -> str:
        when = dt.datetime.fromtimestamp(self.created_at).strftime("%Y-%m-%d %H:%M")
        return f"{when} · {self.reason}" + (f" · {self.label}" if self.label else "")


def _backups_root(data_dir: Path) -> Path:
    return data_dir / BACKUPS_FOLDER


def _is_excluded(relative: Path) -> bool:
    return bool(EXCLUDED_NAMES & set(relative.parts)) or relative.name.endswith(EXCLUDED_SUFFIXES)
# --- END [1.2] ---


# ==============================================================================
# [1.3] copying
# ==============================================================================
def _is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(16) == SQLITE_HEADER
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_sqlite(source: Path, target: Path) -> None:
    """A consistent copy of a live SQLite database (committed data only, WAL folded in)."""
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
        target_connection.execute("PRAGMA journal_mode=DELETE")     # standalone file: no side files needed
    finally:
        target_connection.close()
        source_connection.close()


def _data_files(root: Path) -> list[Path]:
    """Every file under root that belongs in a backup, as paths relative to root."""
    files = []
    for directory, folder_names, file_names in os.walk(root):
        relative_directory = Path(directory).relative_to(root)
        folder_names[:] = sorted(name for name in folder_names if not _is_excluded(relative_directory / name))
        for name in sorted(file_names):
            relative = relative_directory / name
            if not _is_excluded(relative):
                files.append(relative)
    return files
# --- END [1.3] ---


# ==============================================================================
# [1.4] create_and_list
# ==============================================================================
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def create_backup(label: str = "", reason: str = "manual", data_dir: Path | None = None) -> BackupInfo:
    """Copy the data folder into a new backup and return it. Raises BackupError (nothing half-made is kept)."""
    data_dir = Path(data_dir or data_directory())
    root = _backups_root(data_dir)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    name = stamp + (f"_{_slug(label)}" if _slug(label) else "")
    target = root / name
    suffix = 1
    while target.exists():
        suffix += 1
        target = root / f"{name}-{suffix}"
    previous = next(iter(list_backups(data_dir)), None)
    previous_files = _read_manifest(previous.path).get("files", {}) if previous else {}
    log = get_activity_log()
    try:
        target.mkdir(parents=True)
        entries: dict[str, dict] = {}
        for relative in _data_files(data_dir):
            source, copy = data_dir / relative, target / relative
            copy.parent.mkdir(parents=True, exist_ok=True)
            stat = source.stat()
            key = relative.as_posix()
            earlier = previous_files.get(key)
            if _is_sqlite(source):
                _copy_sqlite(source, copy)
                kind = "sqlite"
            elif earlier and earlier.get("kind") == "file" and earlier["size"] == stat.st_size \
                    and earlier.get("mtime_ns") == stat.st_mtime_ns and (previous.path / relative).exists():
                os.link(previous.path / relative, copy)               # unchanged: share the earlier copy
                entries[key] = dict(earlier)
                continue
            else:
                shutil.copy2(source, copy)
                kind = "file"
            entries[key] = {"kind": kind, "size": copy.stat().st_size, "mtime_ns": stat.st_mtime_ns,
                            "sha256": _sha256(copy)}
        manifest = {"format": FORMAT_VERSION, "name": target.name, "created_at": time.time(), "label": label,
                    "reason": reason, "files": entries}
        (target / MANIFEST_NAME).write_text(json.dumps(manifest, indent=1))
    except (OSError, sqlite3.Error) as error:
        shutil.rmtree(target, ignore_errors=True)
        log.error(SOURCE, f"Backup failed ({reason}); nothing was changed", error)
        raise BackupError(f"Backup failed: {error}") from error
    info = _info(target)
    log.success(SOURCE, f"Backed up the data folder ({info.file_count} files, {size_text(info.size_bytes)}) — {reason}")
    prune_backups(data_dir)
    return info


def _read_manifest(path: Path) -> dict:
    try:
        return json.loads((path / MANIFEST_NAME).read_text())
    except (OSError, ValueError):
        return {}


def _info(path: Path) -> BackupInfo | None:
    manifest = _read_manifest(path)
    if not manifest:
        return None
    files = manifest.get("files", {})
    return BackupInfo(path.name, path, float(manifest.get("created_at", 0)), manifest.get("label", ""),
                      manifest.get("reason", ""), len(files), sum(item["size"] for item in files.values()))


def list_backups(data_dir: Path | None = None) -> list[BackupInfo]:
    """Complete backups, newest first (folders without a manifest are unfinished and ignored)."""
    root = _backups_root(Path(data_dir or data_directory()))
    if not root.is_dir():
        return []
    backups = [info for path in root.iterdir() if path.is_dir() and (info := _info(path)) is not None]
    return sorted(backups, key=lambda info: (info.created_at, info.name), reverse=True)
# --- END [1.4] ---


# ==============================================================================
# [1.5] verify_and_prune
# ==============================================================================
def verify_backup(name: str, data_dir: Path | None = None) -> list[str]:
    """Problems with a backup ([] = every file is there with the size and checksum the manifest records)."""
    path = _backups_root(Path(data_dir or data_directory())) / name
    manifest = _read_manifest(path)
    if not manifest:
        return [f"{name}: no readable {MANIFEST_NAME}"]
    problems = []
    for key, entry in manifest.get("files", {}).items():
        file = path / key
        if not file.is_file():
            problems.append(f"missing: {key}")
        elif file.stat().st_size != entry["size"] or _sha256(file) != entry["sha256"]:
            problems.append(f"changed: {key}")
    return problems


def prune_backups(data_dir: Path | None = None) -> list[str]:
    """Delete old backups: keep the newest KEEP_BACKUPS manual ones and safety backups younger than
    KEEP_SAFETY_DAYS. Returns the names deleted (each is logged)."""
    data_dir = Path(data_dir or data_directory())
    backups = list_backups(data_dir)
    regular = [info for info in backups if not info.is_safety]
    cutoff = time.time() - KEEP_SAFETY_DAYS * 86400
    doomed = regular[KEEP_BACKUPS:] + [info for info in backups if info.is_safety and info.created_at < cutoff]
    for info in doomed:
        shutil.rmtree(info.path, ignore_errors=True)
        get_activity_log().info(SOURCE, f"Removed old backup {info.name} ({info.describe()})")
    return [info.name for info in doomed]
# --- END [1.5] ---


# ==============================================================================
# [2.1] restore
# ==============================================================================
def schedule_restore(name: str, data_dir: Path | None = None) -> None:
    """Restore `name` the next time DevOS starts (nothing may hold the data open while it is replaced)."""
    data_dir = Path(data_dir or data_directory())
    if name not in [info.name for info in list_backups(data_dir)]:
        raise BackupError(f"No backup called {name}")
    (data_dir / RESTORE_NOTE).write_text(json.dumps({"backup": name, "requested_at": time.time()}))
    get_activity_log().info(SOURCE, f"Backup {name} will be restored when DevOS next starts")


def pending_restore(data_dir: Path | None = None) -> str | None:
    note = Path(data_dir or data_directory()) / RESTORE_NOTE
    try:
        return json.loads(note.read_text()).get("backup")
    except (OSError, ValueError):
        return None


def cancel_restore(data_dir: Path | None = None) -> None:
    (Path(data_dir or data_directory()) / RESTORE_NOTE).unlink(missing_ok=True)


def apply_pending_restore(data_dir: Path | None = None) -> BackupInfo | None:
    """Called by app.main before anything opens the data folder."""
    data_dir = Path(data_dir or data_directory())
    name = pending_restore(data_dir)
    if name is None:
        return None
    cancel_restore(data_dir)                          # one attempt only: a failing restore must not loop
    return restore_backup(name, data_dir)


_running_lock = None


def hold_running_lock(data_dir: Path | None = None) -> bool:
    """Called by app.main once start-up restores are done: marks the data folder as in use, so a restore
    from the command line refuses to pull it out from under a running DevOS. False if already held."""
    global _running_lock
    if fcntl is None or _running_lock is not None:
        return _running_lock is not None
    handle = open(Path(data_dir or data_directory()) / LOCK_NAME, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _running_lock = handle
    return True


def _is_in_use(data_dir: Path) -> bool:
    if fcntl is None or _running_lock is not None:
        return False                                  # this process holds it: we are the one restoring
    lock_path = data_dir / LOCK_NAME
    if not lock_path.exists():
        return False
    with open(lock_path, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle, fcntl.LOCK_UN)
            return False
        except OSError:
            return True


def restore_backup(name: str, data_dir: Path | None = None) -> BackupInfo:
    """Replace the data folder's contents with backup `name`, after backing up the current state.
    Only while DevOS is closed (start-up, or the command line); refuses if a running DevOS holds the folder."""
    data_dir = Path(data_dir or data_directory())
    if _is_in_use(data_dir):
        raise BackupError("DevOS is running: use Settings → Data → Restore (it restores at the next start), "
                          "or close DevOS first; nothing was changed")
    source = _backups_root(data_dir) / name
    problems = verify_backup(name, data_dir)
    if problems:
        raise BackupError(f"Backup {name} is damaged ({len(problems)} problems, first: {problems[0]}); nothing was changed")
    safety = create_backup(f"before restoring {name}", f"before restore of {name}", data_dir)
    log = get_activity_log()
    for entry in data_dir.iterdir():                  # everything a backup covers is replaced
        if entry.name in EXCLUDED_NAMES:
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    for key in _read_manifest(source)["files"]:
        target = data_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / key, target)            # a copy, never a link: later edits mustn't touch the backup
    log.success(SOURCE, f"Restored backup {name}; the state before it is kept as {safety.name}")
    return _info(source)
# --- END [2.1] ---


# ==============================================================================
# [2.2] command_line
# ==============================================================================
def main(argv: list[str]) -> int:
    command = argv[0] if argv else "list"
    try:
        if command == "list":
            for info in list_backups():
                print(f"{info.name:40} {info.describe():60} {info.file_count:5} files  {size_text(info.size_bytes)}")
            return 0
        if command == "create":
            info = create_backup(" ".join(argv[1:]))
            print(f"Created {info.name}")
            return 0
        if command == "verify" and len(argv) == 2:
            problems = verify_backup(argv[1])
            print("\n".join(problems) or "OK: every file matches the manifest")
            return 1 if problems else 0
        if command == "restore" and len(argv) == 2:
            print("Close DevOS before restoring. ", end="")
            info = restore_backup(argv[1])
            print(f"Restored {info.name}.")
            return 0
    except BackupError as error:
        print(error, file=sys.stderr)
        return 1
    print("usage: python -m app.data.store.backup [list | create [label] | verify NAME | restore NAME]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
# --- END [2.2] ---
