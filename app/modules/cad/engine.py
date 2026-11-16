# ==============================================================================
# FILE: app/modules/cad/engine.py
# OVERVIEW: Parametric part design (no Qt): sketches of 2D shapes on XY planes, an ordered list of features
#           (pad, pocket, hole) that rebuild the solid, plus mesh loading and measurements for the CAD module.
# PURPOSE:
#   1. Shape / Sketch: rectangles, circles and polygons (mm) united into a 2D profile (shapely).
#   2. Feature / Part: pad (extrude up), pocket (cut down), hole (round cut down) applied in order; build()
#      returns a trimesh mesh (booleans by manifold3d); to_dict() / from_dict() for saving as JSON.
#   3. Meshes: load_mesh(path), measure(mesh) -> dimensions, volume, area, watertight, triangles.
# CONTRACT:
#   - Requires: trimesh, shapely, mapbox-earcut (extrude), manifold3d (booleans), numpy. No Qt.
#   - Exposes: Shape, Sketch, Feature, Part, CadError, load_mesh(path), measure(mesh, density), MATERIAL_DENSITIES,
#              PART_FORMAT, cad_unavailable_reason()
# NOTES:
#   - [2026-11-16] Created with the CAD module and its Part Design workbench.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - sketch plane: XY plane at height z (mm); new sketches default to the top of the solid so far.
# - pad: Extrude a sketch's profile up by `height`, joined to the solid.
# - pocket: Remove a sketch's profile from `depth` below its plane up to the plane.
# - hole: Remove a cylinder (x, y, diameter) from the top of the solid down `depth` (0 = through).
# - PART_FORMAT (str): "devos-part" - marks a saved part file (JSON) in the library's CAD models.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. MODEL
#    1.1 imports_and_types
#    1.2 sketch
#    1.3 part
# 2. MESHES
#    2.1 load_and_measure
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_types
# ==============================================================================
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

PART_FORMAT = "devos-part"
EPSILON = 1e-3
MATERIAL_DENSITIES = {"PLA": 1.24, "PETG": 1.27, "ABS": 1.04, "Aluminium": 2.70, "Steel": 7.85, "Wood (oak)": 0.75}


class CadError(Exception):
    pass


def cad_unavailable_reason() -> str | None:
    missing = []
    for package in ("trimesh", "shapely", "manifold3d", "mapbox_earcut"):
        try:
            __import__(package)
        except ImportError:
            missing.append(package)
    return f"Missing Python packages: {', '.join(missing)} (in shell.nix)" if missing else None


@dataclass
class Shape:
    kind: str                                      # "rect" | "circle" | "polygon"
    params: dict = field(default_factory=dict)     # rect: x, y, w, h · circle: cx, cy, r · polygon: points [[x, y]...]

    def geometry(self):
        from shapely.geometry import Point, Polygon, box
        p = self.params
        if self.kind == "rect":
            x0, x1 = sorted((p["x"], p["x"] + p["w"]))
            y0, y1 = sorted((p["y"], p["y"] + p["h"]))
            return box(x0, y0, x1, y1)
        if self.kind == "circle":
            return Point(p["cx"], p["cy"]).buffer(abs(p["r"]), quad_segs=24)
        if self.kind == "polygon":
            return Polygon(p["points"]).buffer(0)          # buffer(0) repairs self-touching outlines
        raise CadError(f"unknown shape {self.kind}")

    def describe(self) -> str:
        p = self.params
        if self.kind == "rect":
            return f"Rectangle {abs(p['w']):g} × {abs(p['h']):g} at ({p['x']:g}, {p['y']:g})"
        if self.kind == "circle":
            return f"Circle ⌀{2 * abs(p['r']):g} at ({p['cx']:g}, {p['cy']:g})"
        return f"Polygon, {len(p['points'])} points"
# --- END [1.1] ---

# ==============================================================================
# [1.2] sketch
# ==============================================================================
@dataclass
class Sketch:
    name: str
    z: float = 0.0
    shapes: list[Shape] = field(default_factory=list)

    def profile(self):
        """All shapes united into one 2D profile (shapely); empty geometry if there are none."""
        from shapely.ops import unary_union
        geometries = [shape.geometry() for shape in self.shapes]
        geometries = [geometry for geometry in geometries if not geometry.is_empty and geometry.area > 0]
        return unary_union(geometries) if geometries else None

    def bounds(self) -> tuple[float, float, float, float] | None:
        profile = self.profile()
        return profile.bounds if profile is not None else None
# --- END [1.2] ---

# ==============================================================================
# [1.3] part
# ==============================================================================
@dataclass
class Feature:
    kind: str                                      # "pad" | "pocket" | "hole"
    params: dict = field(default_factory=dict)     # pad: sketch, height · pocket: sketch, depth · hole: x, y, diameter, depth

    def describe(self) -> str:
        p = self.params
        if self.kind == "pad":
            return f"Pad “{p['sketch']}” up {p['height']:g} mm"
        if self.kind == "pocket":
            return f"Pocket “{p['sketch']}” down {p['depth']:g} mm"
        return f"Hole ⌀{p['diameter']:g} at ({p['x']:g}, {p['y']:g}), " + (f"{p['depth']:g} mm deep" if p.get("depth") else "through")


@dataclass
class Part:
    name: str = "Part"
    sketches: list[Sketch] = field(default_factory=list)
    features: list[Feature] = field(default_factory=list)

    def sketch(self, name: str) -> Sketch | None:
        return next((sketch for sketch in self.sketches if sketch.name == name), None)

    def new_sketch_name(self) -> str:
        number = len(self.sketches) + 1
        while self.sketch(f"Sketch {number}"):
            number += 1
        return f"Sketch {number}"

    def build(self, upto: int | None = None):
        """The solid after the first `upto` features (all by default); None if nothing is padded yet."""
        import trimesh
        solid = None
        for index, feature in enumerate(self.features[:upto]):
            try:
                solid = self._apply(solid, feature)
            except (KeyError, ValueError, TypeError) as error:
                raise CadError(f"feature {index + 1} ({feature.describe()}): {error}") from error
        return solid if solid is None or isinstance(solid, trimesh.Trimesh) else None

    def _extrude(self, sketch_name: str, bottom: float, height: float):
        import trimesh
        sketch = self.sketch(sketch_name)
        if sketch is None:
            raise CadError(f"sketch “{sketch_name}” doesn't exist")
        profile = sketch.profile()
        if profile is None:
            raise CadError(f"sketch “{sketch_name}” has no closed shapes")
        polygons = list(profile.geoms) if hasattr(profile, "geoms") else [profile]
        meshes = [trimesh.creation.extrude_polygon(polygon, height) for polygon in polygons]
        mesh = trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]
        mesh.apply_translation([0, 0, bottom])
        return mesh

    def _apply(self, solid, feature: Feature):
        import trimesh
        p = feature.params
        if feature.kind == "pad":
            if p["height"] <= 0:
                raise CadError("pad height must be positive")
            body = self._extrude(p["sketch"], self.sketch(p["sketch"]).z if self.sketch(p["sketch"]) else 0, p["height"])
            return body if solid is None else trimesh.boolean.union([solid, body], engine="manifold")
        if solid is None:
            raise CadError("pad something first — there's nothing to cut")
        if feature.kind == "pocket":
            sketch = self.sketch(p["sketch"])
            if sketch is None:
                raise CadError(f"sketch “{p['sketch']}” doesn't exist")
            cutter = self._extrude(p["sketch"], sketch.z - p["depth"], p["depth"] + EPSILON * 10)
            return trimesh.boolean.difference([solid, cutter], engine="manifold")
        if feature.kind == "hole":
            top = float(solid.bounds[1][2])
            depth = p.get("depth") or (top - float(solid.bounds[0][2]))
            radius = p["diameter"] / 2
            if radius <= 0:
                raise CadError("hole diameter must be positive")
            cutter = trimesh.creation.cylinder(radius=radius, height=depth + EPSILON * 20, sections=48)
            cutter.apply_translation([p["x"], p["y"], top - depth / 2 + EPSILON * 10])
            return trimesh.boolean.difference([solid, cutter], engine="manifold")
        raise CadError(f"unknown feature {feature.kind}")

    def top_z(self) -> float:
        try:
            solid = self.build()
        except CadError:
            return 0.0
        return float(solid.bounds[1][2]) if solid is not None else 0.0

    def to_dict(self) -> dict:
        return {"format": PART_FORMAT, "version": 1, "name": self.name, "units": "mm",
                "sketches": [{"name": s.name, "z": s.z, "shapes": [{"kind": sh.kind, "params": sh.params} for sh in s.shapes]}
                             for s in self.sketches],
                "features": [{"kind": f.kind, "params": f.params} for f in self.features]}

    @staticmethod
    def from_dict(data: dict) -> "Part":
        if data.get("format") != PART_FORMAT:
            raise CadError("not a DevOS part file")
        return Part(data.get("name", "Part"),
                    [Sketch(s["name"], float(s.get("z", 0)), [Shape(sh["kind"], dict(sh["params"])) for sh in s.get("shapes", [])])
                     for s in data.get("sketches", [])],
                    [Feature(f["kind"], dict(f["params"])) for f in data.get("features", [])])

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=1)
# --- END [1.3] ---


# ==============================================================================
# [2.1] load_and_measure
# ==============================================================================
def is_part_file(path: str | Path) -> bool:
    path = Path(path)
    if path.suffix.lower() != ".json":
        return False
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("format") == PART_FORMAT
    except (OSError, ValueError, AttributeError):
        return False


def load_mesh(path: str | Path):
    """A mesh from an .stl / .obj / .ply / .off / .glb file, or a DevOS part (built)."""
    import trimesh
    path = Path(path)
    if is_part_file(path):
        mesh = Part.from_dict(json.loads(path.read_text(encoding="utf-8"))).build()
        if mesh is None:
            raise CadError("the part has no solid yet")
        return mesh
    try:
        loaded = trimesh.load(str(path), force="mesh")
    except Exception as error:                         # trimesh raises many types for bad files
        raise CadError(f"can't read {path.name}: {error}") from error
    if not isinstance(loaded, trimesh.Trimesh) or loaded.is_empty:
        raise CadError(f"{path.name} has no mesh")
    return loaded


def measure(mesh, density_g_cm3: float | None = None) -> dict:
    size = mesh.bounds[1] - mesh.bounds[0]
    info = {"size_mm": tuple(float(value) for value in size), "triangles": int(len(mesh.faces)),
            "is_watertight": bool(mesh.is_watertight), "area_mm2": float(mesh.area),
            "volume_mm3": float(abs(mesh.volume)) if mesh.is_watertight else math.nan}
    if density_g_cm3 and mesh.is_watertight:
        info["mass_g"] = info["volume_mm3"] / 1000 * density_g_cm3
    info["centre_mm"] = tuple(float(value) for value in np.asarray(mesh.bounds).mean(axis=0))
    return info
# --- END [2.1] ---
