# ==============================================================================
# FILE: app/modules/wave_sim/scene.py
# OVERVIEW: The simulated space: triangle-mesh bodies with materials, the transmitter and receivers.
# PURPOSE:
#   1. Body: a closed triangle mesh (metres) with a material name and optional walking animation.
#   2. Scene: bodies + TX position + RX node positions; flattens everything into one triangle soup
#      (vertices, faces, face -> body) for the ray tracer.
#   3. default_room(): a 6 x 5 m room - concrete floor, drywall walls, concrete pillar, water tank,
#      PTFE block, steel cabinet, and a walking person (water-like body).
#   4. load_mesh_file(): imports .stl / .obj (any format trimesh reads) as a body, converting mm to m.
# CONTRACT:
#   - Requires: numpy, trimesh
#   - Exposes: Body, Scene, default_room(), load_mesh_file()
# NOTES:
#   - [2026-11-15] Created. Meshes should be closed (watertight) for inside/outside to be exact;
#                  open meshes still work but a ray may count a single crossing as "inside".
#   - [2026-11-15] Imported meshes larger than 50 units are assumed to be in millimetres (CAD default).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - body: One closed mesh with one material (a wall, a tank, an imported part).
# - face_body (ndarray int): For every triangle, the index of the body it belongs to.
# - node: A receiver (RX) position; the 4D tensor has one row per node.
# - walk path: Ellipse the animated body follows (centre, radii, period).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SCENE
#    1.1 body
#    1.2 scene
#    1.3 default_room
#    1.4 load_mesh_file
# ==============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh

MILLIMETRE_GUESS_EXTENT = 50.0


# ==============================================================================
# [1.1] body
# ==============================================================================
@dataclass
class Body:
    name: str
    vertices: np.ndarray                         # (V, 3) metres, at rest
    faces: np.ndarray                            # (T, 3) int
    material_name: str
    walk_center_m: tuple[float, float] | None = None   # animated bodies only
    walk_radii_m: tuple[float, float] = (1.0, 0.6)
    walk_period_s: float = 12.0
    source_path: str | None = None               # imported file, if any
    place_at_m: tuple[float, float] | None = None    # where an imported mesh was placed (x, y)

    @property
    def is_animated(self) -> bool:
        return self.walk_center_m is not None

    def offset_at(self, time_s: float) -> np.ndarray:
        """Translation (x, y, 0) of an animated body at time_s (zero for static bodies)."""
        if not self.is_animated:
            return np.zeros(3)
        phase = 2 * np.pi * time_s / self.walk_period_s
        center = np.array([self.walk_center_m[0] + self.walk_radii_m[0] * np.cos(phase),
                           self.walk_center_m[1] + self.walk_radii_m[1] * np.sin(phase), 0.0])
        return center - self.vertices.mean(axis=0) * np.array([1, 1, 0])

    @classmethod
    def from_trimesh(cls, name: str, mesh: trimesh.Trimesh, material_name: str, **kwargs) -> Body:
        return cls(name, np.asarray(mesh.vertices, float), np.asarray(mesh.faces, np.int32), material_name, **kwargs)
# --- END [1.1] ---

# ==============================================================================
# [1.2] scene
# ==============================================================================
@dataclass
class Scene:
    bodies: list[Body] = field(default_factory=list)
    tx_position_m: np.ndarray = field(default_factory=lambda: np.array([-2.0, -1.6, 1.2]))
    rx_positions_m: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    rx_names: list[str] = field(default_factory=list)

    @property
    def node_count(self) -> int:
        return len(self.rx_positions_m)

    def triangle_soup(self, time_s: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(vertices (V,3), faces (T,3), face_body (T,)) for every body, animated ones moved to time_s."""
        vertices, faces, face_body, offset = [], [], [], 0
        for body_index, body in enumerate(self.bodies):
            vertices.append(body.vertices + body.offset_at(time_s))
            faces.append(body.faces + offset)
            face_body.append(np.full(len(body.faces), body_index, np.int32))
            offset += len(body.vertices)
        if not vertices:
            return np.zeros((0, 3)), np.zeros((0, 3), np.int32), np.zeros(0, np.int32)
        return np.vstack(vertices), np.vstack(faces).astype(np.int32), np.concatenate(face_body)

    def body_index(self, name: str) -> int:
        return [body.name for body in self.bodies].index(name)
# --- END [1.2] ---

# ==============================================================================
# [1.3] default_room
# ==============================================================================
def _box(name: str, extents, center, material: str) -> Body:
    return Body.from_trimesh(name, trimesh.creation.box(extents=extents).apply_translation(center), material)


def default_room() -> Scene:
    """6 x 5 x 2.8 m room (x across, y deep, z up; floor at z = 0)."""
    width, depth, height, wall = 6.0, 5.0, 2.8, 0.12
    bodies = [
        _box("Floor", (width + 2 * wall, depth + 2 * wall, 0.15), (0, 0, -0.075), "Concrete"),
        _box("Wall north", (width + 2 * wall, wall, height), (0, depth / 2 + wall / 2, height / 2), "Plasterboard"),
        _box("Wall south", (width + 2 * wall, wall, height), (0, -depth / 2 - wall / 2, height / 2), "Plasterboard"),
        _box("Wall east", (wall, depth, height), (width / 2 + wall / 2, 0, height / 2), "Plasterboard"),
        _box("Wall west", (wall, depth, height), (-width / 2 - wall / 2, 0, height / 2), "Plasterboard"),
        _box("Pillar", (0.4, 0.4, height), (1.2, 0.6, height / 2), "Concrete"),
        _box("Steel cabinet", (0.6, 0.5, 1.8), (-1.9, 1.6, 0.9), "Metal"),
        _box("PTFE block", (0.5, 0.35, 0.4), (0.6, -1.4, 0.95), "PTFE / plastic"),
        Body.from_trimesh("Water tank", trimesh.creation.cylinder(radius=0.3, height=1.0, sections=24)
                          .apply_translation((-1.0, -1.2, 0.5)), "Water"),
        Body.from_trimesh("Person", trimesh.creation.cylinder(radius=0.18, height=1.7, sections=16)
                          .apply_translation((0.0, 0.0, 0.85)), "Human tissue",
                          walk_center_m=(0.2, 0.2), walk_radii_m=(1.4, 0.9), walk_period_s=14.0),
    ]
    rx_positions = np.array([
        [2.4, 1.9, 1.2],        # far corner, line of sight
        [2.4, -1.9, 1.2],       # opposite corner
        [-2.6, 2.1, 1.2],       # tucked behind the steel cabinet
        [3.6, 0.0, 1.2],        # outside the east wall (through-wall)
    ])
    return Scene(bodies, np.array([-2.2, -1.8, 1.2]), rx_positions,
                 ["RX1 corner", "RX2 corner", "RX3 behind cabinet", "RX4 outside wall"])
# --- END [1.3] ---

# ==============================================================================
# [1.4] load_mesh_file
# ==============================================================================
def load_mesh_file(path: str | Path, material_name: str, place_at_m: tuple[float, float] | None = (0.0, 0.0)) -> Body:
    """Import a CAD mesh as a body. Units: metres, or millimetres if it is bigger than 50 units.
    place_at_m: put the mesh's footprint centre here, resting on the floor (None = keep coordinates)."""
    mesh = trimesh.load(Path(path), force="mesh")
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ValueError(f"No triangles found in {Path(path).name}")
    if mesh.extents.max() > MILLIMETRE_GUESS_EXTENT:
        mesh.apply_scale(0.001)
    if place_at_m is not None:
        low, high = mesh.bounds
        mesh.apply_translation((place_at_m[0] - (low[0] + high[0]) / 2, place_at_m[1] - (low[1] + high[1]) / 2, -low[2]))
    return Body.from_trimesh(Path(path).stem, mesh, material_name, source_path=str(Path(path).resolve()),
                             place_at_m=None if place_at_m is None else tuple(place_at_m))
# --- END [1.4] ---
