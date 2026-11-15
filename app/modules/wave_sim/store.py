# ==============================================================================
# FILE: app/modules/wave_sim/store.py
# OVERVIEW: The wave simulator's SQLite tables: its material library and saved tensor snapshots.
# PURPOSE:
#   1. wave_sim_materials: every material the simulator can assign (seeded from the shared library;
#      users can add custom ones with constant eps' and sigma).
#   2. wave_sim_snapshots: frozen copies of the 4D wave tensor (BLOB) with sweep + scene metadata,
#      for later analysis or C++ / PyTorch processing.
# CONTRACT:
#   - Requires: app.data.store.database (Database, array_to_blob, blob_to_array),
#               app.data.em_materials (Material, ALL_MATERIALS)
#   - Exposes: WaveSimStore(database, instance_id); materials() -> dict[name, Material];
#              add_custom_material(); save_snapshot(); list_snapshots(); load_snapshot()
# NOTES:
#   - [2026-11-15] Created. Built-in rows are re-synced from code on every start (so model fixes
#                  reach existing databases); custom rows are never touched.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - built-in material: Seeded from app/data/em_materials.py (is_builtin = 1).
# - custom material: Added by the user: eps' and sigma constant over frequency (b = d = 0).
# - snapshot: One saved WaveTensor (nodes x time x frequency x [amp, phase]) plus metadata.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. STORE
#    1.1 migrations
#    1.2 materials
#    1.3 snapshots
# ==============================================================================

import json
import time

import numpy as np

from app.data.em_materials import ALL_MATERIALS, Material
from app.data.store.database import Database, array_to_blob, blob_to_array

NAMESPACE = "wave_sim"

# ==============================================================================
# [1.1] migrations
# ==============================================================================
MIGRATIONS = [
    """
    CREATE TABLE wave_sim_materials (
        name           TEXT PRIMARY KEY,
        permittivity_a REAL NOT NULL,
        permittivity_b REAL NOT NULL,
        conductivity_c REAL NOT NULL,
        conductivity_d REAL NOT NULL,
        color_json     TEXT NOT NULL,
        is_conductor   INTEGER NOT NULL,
        source         TEXT NOT NULL,
        is_builtin     INTEGER NOT NULL
    );
    CREATE TABLE wave_sim_snapshots (
        snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
        instance_id TEXT NOT NULL,
        created_at  REAL NOT NULL,
        label       TEXT NOT NULL,
        sweep_json  TEXT NOT NULL,
        scene_json  TEXT NOT NULL,
        shape_json  TEXT NOT NULL,
        tensor_blob BLOB NOT NULL
    );
    CREATE INDEX wave_sim_snapshots_instance ON wave_sim_snapshots (instance_id, created_at DESC);
    """,
]
# --- END [1.1] ---


class WaveSimStore:
    def __init__(self, database: Database, instance_id: str) -> None:
        self.database, self.instance_id = database, instance_id
        database.migrate(NAMESPACE, MIGRATIONS)
        self._sync_builtin_materials()

# ==============================================================================
# [1.2] materials
# ==============================================================================
    def _sync_builtin_materials(self) -> None:
        with self.database.transaction():
            for material in ALL_MATERIALS:
                self.database.execute(
                    "INSERT INTO wave_sim_materials VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1) ON CONFLICT(name) DO UPDATE SET "
                    "permittivity_a = excluded.permittivity_a, permittivity_b = excluded.permittivity_b, "
                    "conductivity_c = excluded.conductivity_c, conductivity_d = excluded.conductivity_d, "
                    "color_json = excluded.color_json, is_conductor = excluded.is_conductor, source = excluded.source "
                    "WHERE wave_sim_materials.is_builtin = 1",
                    (material.name, material.permittivity_a, material.permittivity_b, material.conductivity_c,
                     material.conductivity_d, json.dumps(material.color), int(material.is_conductor), material.source),
                )

    def materials(self) -> dict[str, Material]:
        rows = self.database.fetch_all("SELECT * FROM wave_sim_materials ORDER BY is_builtin DESC, name")
        return {
            row["name"]: Material(
                row["name"], row["permittivity_a"], row["permittivity_b"], row["conductivity_c"], row["conductivity_d"],
                tuple(json.loads(row["color_json"])), bool(row["is_conductor"]), row["source"],
            )
            for row in rows
        }

    def add_custom_material(self, name: str, permittivity: float, conductivity_s_m: float,
                            color: tuple[float, float, float] = (0.7, 0.5, 0.9)) -> None:
        """Constant eps' and sigma. Overwrites an existing custom material of that name; refuses built-ins."""
        row = self.database.fetch_one("SELECT is_builtin FROM wave_sim_materials WHERE name = ?", (name,))
        if row is not None and row["is_builtin"]:
            raise ValueError(f"'{name}' is a built-in material")
        self.database.execute(
            "INSERT INTO wave_sim_materials VALUES (?, ?, 0, ?, 0, ?, 0, 'custom', 0) ON CONFLICT(name) DO UPDATE SET "
            "permittivity_a = excluded.permittivity_a, conductivity_c = excluded.conductivity_c",
            (name, permittivity, conductivity_s_m, json.dumps(color)),
        )
# --- END [1.2] ---

# ==============================================================================
# [1.3] snapshots
# ==============================================================================
    def save_snapshot(self, tensor: np.ndarray, label: str, sweep: dict, scene: dict) -> int:
        cursor = self.database.execute(
            "INSERT INTO wave_sim_snapshots (instance_id, created_at, label, sweep_json, scene_json, shape_json, tensor_blob) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.instance_id, time.time(), label, json.dumps(sweep), json.dumps(scene),
             json.dumps(list(tensor.shape)), array_to_blob(tensor)),
        )
        return cursor.lastrowid

    def list_snapshots(self) -> list[dict]:
        rows = self.database.fetch_all(
            "SELECT snapshot_id, created_at, label, shape_json, length(tensor_blob) AS size_bytes "
            "FROM wave_sim_snapshots WHERE instance_id = ? ORDER BY created_at DESC", (self.instance_id,),
        )
        return [dict(row) for row in rows]

    def load_snapshot(self, snapshot_id: int) -> tuple[np.ndarray, dict, dict]:
        row = self.database.fetch_one("SELECT * FROM wave_sim_snapshots WHERE snapshot_id = ?", (snapshot_id,))
        if row is None:
            raise KeyError(snapshot_id)
        return blob_to_array(row["tensor_blob"]), json.loads(row["sweep_json"]), json.loads(row["scene_json"])
# --- END [1.3] ---
