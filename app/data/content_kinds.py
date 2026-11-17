# ==============================================================================
# FILE: app/data/content_kinds.py
# OVERVIEW: What kinds of things the Library holds, and small helpers about them - shared by the Knowledge
#           engine (which owns the Library) and the front end (import dialogs, labels).
# PURPOSE:
#   1. CONTENT_KINDS: kind -> (display name, default extension); EXTENSION_KINDS / kind_for_path(): which kind a
#      file is by its extension.
#   2. size_text(): "12.3 MB" for a byte count.
# CONTRACT:
#   - Requires: standard library only (no store, no Qt).
#   - Exposes: CONTENT_KINDS, EXTENSION_KINDS, kind_for_path(path), size_text(size_bytes)
# NOTES:
#   - [2026-11-17] Split out of app/data/store/content.py in Phase 4 of the engine plan: the store itself moved
#                  into the Knowledge engine (engines/knowledge/store.py); these constants are shared.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - kind (str): Category of a Library item ("note", "dataset", "capture", "cad", "database", "graph", "sheet",
#   "geo", "image", "file").
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. KINDS
#    1.1 kinds
# ==============================================================================

# ==============================================================================
# [1.1] kinds
# ==============================================================================
from __future__ import annotations

from pathlib import Path

CONTENT_KINDS = {               # kind: (display name, default extension)
    "note": ("Notes", ".md"),
    "dataset": ("Datasets", ".csv"),
    "capture": ("RF captures", ".npz"),
    "cad": ("CAD models", ".stl"),
    "database": ("Databases", ".sqlite3"),
    "graph": ("Graph databases", ".kuzu"),
    "sheet": ("Spreadsheets", ".xlsx"),
    "geo": ("Map layers", ".geojson"),
    "image": ("Images", ".png"),
    "file": ("Other files", ""),
}
EXTENSION_KINDS = {".md": "note", ".markdown": "note", ".txt": "note", ".csv": "dataset", ".npz": "capture",
                   ".stl": "cad", ".obj": "cad", ".ply": "cad", ".off": "cad", ".glb": "cad", ".gltf": "cad",
                   ".sqlite": "database", ".sqlite3": "database", ".db": "database", ".kuzu": "graph",
                   ".xlsx": "sheet", ".xlsm": "sheet", ".ods": "sheet", ".xls": "sheet", ".geojson": "geo",
                   ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image", ".svg": "image"}


def kind_for_path(path: str | Path) -> str:
    """Content kind for a file name by its extension ("file" if unknown)."""
    return EXTENSION_KINDS.get(Path(path).suffix.lower(), "file")


def size_text(size_bytes: int) -> str:
    for unit, size in (("GB", 2**30), ("MB", 2**20), ("KB", 2**10)):
        if size_bytes >= size:
            return f"{size_bytes / size:.1f} {unit}"
    return f"{size_bytes} B"
# --- END [1.1] ---
