# ==============================================================================
# FILE: app/modules/map/store.py
# OVERVIEW: Map places in SQLite (no Qt): named points with a note, shared by every Map tile.
# PURPOSE:
#   1. MapStore: list / add / update / delete places; export them as GeoJSON.
# CONTRACT:
#   - Requires: app.data.store.database.Database
#   - Exposes: MapStore(database), Place, places_geojson(places) -> str
#   - Tables (namespace "map"): map_places
# NOTES:
#   - [2026-11-16] Created with the Map module.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - place: A named point (latitude, longitude in WGS84 degrees) with an optional note.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. STORE
#    1.1 map_store
# ==============================================================================

# ==============================================================================
# [1.1] map_store
# ==============================================================================
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from app.data.store.database import Database

MIGRATIONS = [
    """
    CREATE TABLE map_places (
        place_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL,
        latitude   REAL NOT NULL,
        longitude  REAL NOT NULL,
        note       TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    );
    """,
]


@dataclass
class Place:
    place_id: int
    name: str
    latitude: float
    longitude: float
    note: str = ""

    def as_dict(self) -> dict:
        return {"id": self.place_id, "name": self.name, "lat": self.latitude, "lon": self.longitude, "note": self.note}


class MapStore:
    def __init__(self, database: Database) -> None:
        self.database = database
        database.migrate("map", MIGRATIONS)

    def places(self) -> list[Place]:
        return [Place(row["place_id"], row["name"], row["latitude"], row["longitude"], row["note"])
                for row in self.database.fetch_all("SELECT * FROM map_places ORDER BY name COLLATE NOCASE")]

    def get(self, place_id: int) -> Place | None:
        return next((place for place in self.places() if place.place_id == place_id), None)

    def add(self, name: str, latitude: float, longitude: float, note: str = "") -> Place:
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError(f"{latitude}, {longitude} is not a valid position")
        cursor = self.database.execute(
            "INSERT INTO map_places (name, latitude, longitude, note, created_at) VALUES (?, ?, ?, ?, ?)",
            (name.strip() or "Place", latitude, longitude, note, time.time()))
        return self.get(cursor.lastrowid)

    def update(self, place_id: int, **fields) -> None:
        allowed = {key: value for key, value in fields.items() if key in ("name", "latitude", "longitude", "note")}
        if allowed:
            assignments = ", ".join(f"{key} = ?" for key in allowed)
            self.database.execute(f"UPDATE map_places SET {assignments} WHERE place_id = ?", (*allowed.values(), place_id))

    def delete(self, place_id: int) -> None:
        self.database.execute("DELETE FROM map_places WHERE place_id = ?", (place_id,))


def places_geojson(places: list[Place]) -> str:
    return json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [place.longitude, place.latitude]},
         "properties": {"name": place.name, "note": place.note}} for place in places]}, indent=1)
# --- END [1.1] ---
