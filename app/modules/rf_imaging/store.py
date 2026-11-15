# ==============================================================================
# FILE: app/modules/rf_imaging/store.py
# OVERVIEW: RF imaging's SQLite tables: an index of saved test captures.
# PURPOSE:
#   1. Creates / migrates the rf_imaging tables in the shared DevOS database.
#   2. Records every saved capture (file, frames, sweep, source) so the module can offer recent tests.
# CONTRACT:
#   - Requires: app.data.store.database.Database
#   - Exposes: RfImagingStore(database); record_capture(); recent_captures(limit)
# NOTES:
#   - [2026-11-15] Created. Raw frames stay in their .npz files (large, portable); the database
#                  only indexes them. Rows whose file was deleted are skipped by recent_captures().
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - NAMESPACE (str): "rf_imaging"; prefix of this module's tables and its migration namespace.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. STORE
#    1.1 migrations
#    1.2 rf_imaging_store
# ==============================================================================

# ==============================================================================
# [1.1] migrations
# ==============================================================================
import json
import time
from pathlib import Path

from app.data.store.database import Database

NAMESPACE = "rf_imaging"
MIGRATIONS = [
    """
    CREATE TABLE rf_imaging_captures (
        capture_id  INTEGER PRIMARY KEY AUTOINCREMENT,
        instance_id TEXT,
        file_path   TEXT NOT NULL,
        frame_count INTEGER NOT NULL,
        sweep_json  TEXT NOT NULL,
        source      TEXT NOT NULL,
        saved_at    REAL NOT NULL
    );
    CREATE INDEX rf_imaging_captures_saved ON rf_imaging_captures (saved_at DESC);
    """,
]
# --- END [1.1] ---


# ==============================================================================
# [1.2] rf_imaging_store
# ==============================================================================
class RfImagingStore:
    def __init__(self, database: Database, instance_id: str | None = None) -> None:
        self.database, self.instance_id = database, instance_id
        database.migrate(NAMESPACE, MIGRATIONS)

    def record_capture(self, file_path: Path, frame_count: int, sweep: dict, source: str) -> None:
        self.database.execute(
            "INSERT INTO rf_imaging_captures (instance_id, file_path, frame_count, sweep_json, source, saved_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (self.instance_id, str(file_path), frame_count, json.dumps(sweep), source, time.time()),
        )

    def recent_captures(self, limit: int = 8) -> list[dict]:
        """Newest first; one entry per file; missing files skipped."""
        rows = self.database.fetch_all(
            "SELECT file_path, MAX(saved_at) AS saved_at, frame_count FROM rf_imaging_captures "
            "GROUP BY file_path ORDER BY saved_at DESC LIMIT ?", (limit * 2,),
        )
        captures = [dict(row) for row in rows if Path(row["file_path"]).exists()]
        return captures[:limit]
# --- END [1.2] ---
