# ==============================================================================
# FILE: app/data/store/database.py
# OVERVIEW: DevOS's SQLite data layer: one database file shared by the app and every module.
# PURPOSE:
#   1. Opens (and creates) the database in the DevOS data folder, in WAL mode.
#   2. Runs versioned migrations per namespace, so each module owns and evolves its own tables.
#   3. Core tables: module_instances, module_settings (per-instance JSON key/values), canvas_layouts,
#      user_preferences (app-wide get_preference / set_preference).
#   4. Helpers to store NumPy arrays as BLOBs (np.save format) and JSON values.
#   5. ModuleSettings: the small per-instance key/value store every module gets through its context.
# CONTRACT:
#   - Requires: sqlite3 (stdlib), numpy
#   - Exposes: Database (get_preference, set_preference, delete_layout, ...), get_database(), data_directory(), ModuleSettings,
#              array_to_blob(), blob_to_array()
# NOTES:
#   - [2026-11-17] migrate() takes a safety backup (app.data.store.backup) before upgrading tables that hold data.
#   - [2026-11-15] Created. Location: $DEVOS_DATA_DIR, else $XDG_DATA_HOME/devos, else
#                  ~/.local/share/devos; file devos.sqlite3. Tests set DEVOS_DATA_DIR to a temp folder.
#   - [2026-11-15] One connection, used from the Qt (main) thread only. Writes commit immediately
#                  (autocommit-style helpers) so a crash never loses settings.
#   - [2026-11-15] Core v2: user_preferences table. Opening the database and every migration is
#                  announced in the activity log.
#   - [2026-11-15] Module tables are prefixed with the module's namespace (e.g. wave_sim_snapshots)
#                  so modules can't collide.
#   - [2026-11-16] delete_layout(): a page whose last module tile was closed goes back to "Create New".
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - instance_id (str): Unique id of one placed module (one tile); settings and data hang off it.
# - migration: An SQL script that moves a namespace's tables from version n to n + 1.
# - namespace (str): Owner of a group of tables ("core", "wave_sim", "rf_imaging", ...).
# - page_key (str): Which canvas a saved layout belongs to (e.g. "project_editor").
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS_AND_LOCATION
#    1.1 imports
#    1.2 data_directory
# 2. DATABASE
#    2.1 open_and_migrate
#    2.2 query_helpers
#    2.3 core_tables
# 3. HELPERS
#    3.1 array_blobs
#    3.2 module_settings
#    3.3 shared_instance
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import io
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from app.data.activity_log import get_activity_log
# --- END [1.1] ---

# ==============================================================================
# [1.2] data_directory
# ==============================================================================
DATABASE_FILE_NAME = "devos.sqlite3"


def data_directory() -> Path:
    """Folder for DevOS data (created if missing)."""
    if os.environ.get("DEVOS_DATA_DIR"):
        directory = Path(os.environ["DEVOS_DATA_DIR"])
    else:
        directory = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "devos"
    directory.mkdir(parents=True, exist_ok=True)
    return directory
# --- END [1.2] ---


# ==============================================================================
# [2.1] open_and_migrate
# ==============================================================================
CORE_MIGRATIONS = [
    """
    CREATE TABLE module_instances (
        instance_id    TEXT PRIMARY KEY,
        module_type_id TEXT NOT NULL,
        title          TEXT NOT NULL,
        created_at     REAL NOT NULL
    );
    CREATE TABLE module_settings (
        instance_id TEXT NOT NULL,
        key         TEXT NOT NULL,
        value_json  TEXT NOT NULL,
        updated_at  REAL NOT NULL,
        PRIMARY KEY (instance_id, key)
    );
    CREATE TABLE canvas_layouts (
        page_key    TEXT PRIMARY KEY,
        layout_json TEXT NOT NULL,
        updated_at  REAL NOT NULL
    );
    """,
    """
    CREATE TABLE user_preferences (
        key        TEXT PRIMARY KEY,
        value_json TEXT NOT NULL,
        updated_at REAL NOT NULL
    );
    """,
]


class Database:
    """SQLite database with per-namespace migrations."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else data_directory() / DATABASE_FILE_NAME
        self._has_safety_backup = False
        self.connection = sqlite3.connect(self.path, isolation_level=None)   # autocommit; use transaction()
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_versions (namespace TEXT PRIMARY KEY, version INTEGER NOT NULL)"
        )
        self.migrate("core", CORE_MIGRATIONS)

    def schema_version(self, namespace: str) -> int:
        row = self.connection.execute("SELECT version FROM schema_versions WHERE namespace = ?", (namespace,)).fetchone()
        return row["version"] if row else 0

    def migrate(self, namespace: str, migrations: list[str]) -> None:
        """Apply migrations[current_version:] for this namespace, each in its own transaction. Upgrading
        tables that already hold data (the user's own data folder) takes a safety backup first; if the
        backup fails, nothing is migrated (BackupError)."""
        current_version = self.schema_version(namespace)
        if 0 < current_version < len(migrations):
            self._back_up_before_migrating(namespace, len(migrations))
        for version, script in enumerate(migrations[current_version:], start=current_version + 1):
            get_activity_log().info("database", f"Migrating '{namespace}' tables to version {version}")
            with self.transaction():
                for statement in (part.strip() for part in script.split(";")):
                    if statement:
                        self.connection.execute(statement)
                self.connection.execute(
                    "INSERT INTO schema_versions (namespace, version) VALUES (?, ?) "
                    "ON CONFLICT(namespace) DO UPDATE SET version = excluded.version",
                    (namespace, version),
                )

    def _back_up_before_migrating(self, namespace: str, target_version: int) -> None:
        if self._has_safety_backup or self.path.resolve() != (data_directory() / DATABASE_FILE_NAME).resolve():
            return                                     # once per run, and only for the user's data folder
        from app.data.store.backup import create_backup     # lazy: backup imports this module
        create_backup(reason=f"before migrating {namespace} to version {target_version}", data_dir=self.path.parent)
        self._has_safety_backup = True

    def close(self) -> None:
        self.connection.close()
# --- END [2.1] ---

# ==============================================================================
# [2.2] query_helpers
# ==============================================================================
    def transaction(self):
        """`with db.transaction():` groups statements; rolls back on error."""
        return _Transaction(self.connection)

    def execute(self, sql: str, parameters: tuple | dict = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, parameters)

    def fetch_all(self, sql: str, parameters: tuple | dict = ()) -> list[sqlite3.Row]:
        return self.connection.execute(sql, parameters).fetchall()

    def fetch_one(self, sql: str, parameters: tuple | dict = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, parameters).fetchone()
# --- END [2.2] ---

# ==============================================================================
# [2.3] core_tables
# ==============================================================================
    def register_instance(self, module_type_id: str, title: str, instance_id: str | None = None) -> str:
        instance_id = instance_id or uuid.uuid4().hex
        self.execute(
            "INSERT OR IGNORE INTO module_instances (instance_id, module_type_id, title, created_at) VALUES (?, ?, ?, ?)",
            (instance_id, module_type_id, title, time.time()),
        )
        return instance_id

    def forget_instance(self, instance_id: str) -> None:
        """Remove an instance and its settings (its module's own tables clean up themselves)."""
        with self.transaction():
            self.execute("DELETE FROM module_settings WHERE instance_id = ?", (instance_id,))
            self.execute("DELETE FROM module_instances WHERE instance_id = ?", (instance_id,))

    def save_layout(self, page_key: str, layout: dict) -> None:
        self.execute(
            "INSERT INTO canvas_layouts (page_key, layout_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(page_key) DO UPDATE SET layout_json = excluded.layout_json, updated_at = excluded.updated_at",
            (page_key, json.dumps(layout), time.time()),
        )

    def get_preference(self, key: str, default: Any = None) -> Any:
        """App-wide user preference (window, sidebar, log panel ...)."""
        row = self.fetch_one("SELECT value_json FROM user_preferences WHERE key = ?", (key,))
        return json.loads(row["value_json"]) if row else default

    def set_preference(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO user_preferences (key, value_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
            (key, json.dumps(value), time.time()),
        )

    def delete_layout(self, page_key: str) -> None:
        self.execute("DELETE FROM canvas_layouts WHERE page_key = ?", (page_key,))

    def load_layout(self, page_key: str) -> dict | None:
        row = self.fetch_one("SELECT layout_json FROM canvas_layouts WHERE page_key = ?", (page_key,))
        return json.loads(row["layout_json"]) if row else None


class _Transaction:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def __enter__(self):
        self.connection.execute("BEGIN")
        return self.connection

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.connection.execute("ROLLBACK" if exc_type else "COMMIT")
# --- END [2.3] ---


# ==============================================================================
# [3.1] array_blobs
# ==============================================================================
def array_to_blob(array: np.ndarray) -> bytes:
    """NumPy array -> bytes (np.save format: keeps dtype and shape)."""
    buffer = io.BytesIO()
    np.save(buffer, np.ascontiguousarray(array), allow_pickle=False)
    return buffer.getvalue()


def blob_to_array(blob: bytes) -> np.ndarray:
    return np.load(io.BytesIO(blob), allow_pickle=False)
# --- END [3.1] ---

# ==============================================================================
# [3.2] module_settings
# ==============================================================================
class ModuleSettings:
    """JSON key/value settings for one module instance (persisted immediately)."""

    def __init__(self, database: Database, instance_id: str) -> None:
        self.database, self.instance_id = database, instance_id

    def get(self, key: str, default: Any = None) -> Any:
        row = self.database.fetch_one(
            "SELECT value_json FROM module_settings WHERE instance_id = ? AND key = ?", (self.instance_id, key)
        )
        return json.loads(row["value_json"]) if row else default

    def set(self, key: str, value: Any) -> None:
        self.database.execute(
            "INSERT INTO module_settings (instance_id, key, value_json, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(instance_id, key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
            (self.instance_id, key, json.dumps(value), time.time()),
        )

    def all(self) -> dict[str, Any]:
        rows = self.database.fetch_all("SELECT key, value_json FROM module_settings WHERE instance_id = ?", (self.instance_id,))
        return {row["key"]: json.loads(row["value_json"]) for row in rows}
# --- END [3.2] ---

# ==============================================================================
# [3.3] shared_instance
# ==============================================================================
_shared_database: Database | None = None


def get_database() -> Database:
    """The app-wide Database (opened on first use)."""
    global _shared_database
    if _shared_database is None:
        with get_activity_log().step("database", "Open user database"):
            _shared_database = Database()
        get_activity_log().info("database", f"Database: {_shared_database.path}")
    return _shared_database
# --- END [3.3] ---
