# ==============================================================================
# FILE: app/native/__init__.py
# OVERVIEW: Bridge to DevOS's C++ engine (devos_core), with a NumPy fallback of identical maths.
# PURPOSE:
#   1. Imports devos_core from core/build/ if it has been built (python -m app.native.build).
#   2. Exposes trace_segments(), synthesize_response() and ema() that use C++ when available, NumPy
#      otherwise (per function, so an older build missing a function still works).
#   3. self_test(): runs both on the same random scene and reports the largest difference.
# CONTRACT:
#   - Requires: numpy; optionally core/build/devos_core*.so
#   - Exposes: HAS_NATIVE, ENGINE_NAME, trace_segments(), synthesize_response(), ema(), self_test()
# NOTES:
#   - [2026-11-15] Created. Set DEVOS_DISABLE_NATIVE=1 to force the NumPy engine (comparisons, debugging).
#   - [2026-11-15] Added ema(); dispatch is per function, so an older build without a new native
#                  function falls back to NumPy for just that function.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - NATIVE_BUILD_DIRECTORY (Path): core/build, where CMake puts devos_core*.so.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. BRIDGE
#    1.1 load_native
#    1.2 dispatch
#    1.3 self_test
# ==============================================================================

# ==============================================================================
# [1.1] load_native
# ==============================================================================
import os
import sys
from pathlib import Path

import numpy as np

from app.native import numpy_engine

NATIVE_BUILD_DIRECTORY = Path(__file__).resolve().parents[2] / "core" / "build"

devos_core = None
if not os.environ.get("DEVOS_DISABLE_NATIVE"):
    if str(NATIVE_BUILD_DIRECTORY) not in sys.path:
        sys.path.insert(0, str(NATIVE_BUILD_DIRECTORY))
    try:
        import devos_core  # noqa: F401  (built by python -m app.native.build)
    except ImportError:
        devos_core = None

HAS_NATIVE = devos_core is not None
ENGINE_NAME = devos_core.version() if HAS_NATIVE else "NumPy fallback (run: python -m app.native.build)"
# --- END [1.1] ---

# ==============================================================================
# [1.2] dispatch
# ==============================================================================
def trace_segments(origins, ends, vertices, faces, face_body, body_count, skip_face):
    """Per ray and body: (inside length m (R, B), surface crossings (R, B))."""
    engine = devos_core if HAS_NATIVE else numpy_engine
    return engine.trace_segments(
        np.ascontiguousarray(origins, float), np.ascontiguousarray(ends, float),
        np.ascontiguousarray(vertices, float), np.ascontiguousarray(faces, np.int32),
        np.ascontiguousarray(face_body, np.int32), int(body_count), np.ascontiguousarray(skip_face, np.int32),
    )


def synthesize_response(path_lengths, inside_length, crossings, reflect_body, path_node,
                        refractive_index, reflection, frequencies_hz, node_count, path_weight=None):
    """Complex channel response H[node, frequency] from traced paths."""
    engine = devos_core if HAS_NATIVE else numpy_engine
    return engine.synthesize_response(
        np.ascontiguousarray(path_lengths, float), np.ascontiguousarray(inside_length, float),
        np.ascontiguousarray(crossings, np.int32), np.ascontiguousarray(reflect_body, np.int32),
        np.ascontiguousarray(path_node, np.int32),
        np.ascontiguousarray(np.ones(len(path_lengths)) if path_weight is None else path_weight, float),
        np.ascontiguousarray(refractive_index, complex),
        np.ascontiguousarray(reflection, complex), np.ascontiguousarray(frequencies_hz, float), int(node_count),
    )


def ema(values, alpha: float, initial: float = float("nan")) -> np.ndarray:
    """Exponential moving average (NaNs skipped); see core/src/series.hpp."""
    if HAS_NATIVE and hasattr(devos_core, "ema"):
        return devos_core.ema(np.ascontiguousarray(values, float), float(alpha), float(initial))
    return numpy_engine.ema(values, alpha, initial)
# --- END [1.2] ---

# ==============================================================================
# [1.3] self_test
# ==============================================================================
def self_test(seed: int = 3) -> dict:
    """Compare C++ and NumPy on a random box scene. Returns the max differences (needs HAS_NATIVE)."""
    rng = np.random.default_rng(seed)
    import trimesh
    meshes = [trimesh.creation.box(extents=rng.uniform(0.3, 1.0, 3)).apply_translation(rng.uniform(-1, 1, 3))
              for _ in range(4)]
    vertices = np.vstack([mesh.vertices for mesh in meshes])
    offsets = np.cumsum([0] + [len(mesh.vertices) for mesh in meshes[:-1]])
    faces = np.vstack([mesh.faces + offset for mesh, offset in zip(meshes, offsets)])
    face_body = np.concatenate([np.full(len(mesh.faces), index) for index, mesh in enumerate(meshes)])
    origins, ends = rng.uniform(-3, 3, (400, 3)), rng.uniform(-3, 3, (400, 3))
    skip = np.full(400, -1)
    numpy_inside, numpy_cross = numpy_engine.trace_segments(origins, ends, vertices, faces, face_body, 4, skip)
    result = {"engine": ENGINE_NAME, "rays_hitting": int((numpy_cross > 0).any(axis=1).sum())}
    if HAS_NATIVE:
        native_inside, native_cross = trace_segments(origins, ends, vertices, faces, face_body, 4, skip)
        result["max_inside_difference_m"] = float(np.abs(native_inside - numpy_inside).max())
        result["crossing_mismatches"] = int((native_cross != numpy_cross).sum())
        frequencies = np.linspace(1e9, 10e9, 32)
        index = np.sqrt(rng.uniform(2, 60, (4, 32)) - 1j * rng.uniform(0, 20, (4, 32)))
        gamma = (1 - index) / (1 + index)
        lengths = np.linalg.norm(ends - origins, axis=1)
        reflect = rng.integers(-1, 4, 400)
        nodes = rng.integers(0, 3, 400)
        weights = rng.uniform(0.1, 1.0, 400)
        native_h = synthesize_response(lengths, numpy_inside, numpy_cross, reflect, nodes, index, gamma, frequencies, 3, weights)
        numpy_h = numpy_engine.synthesize_response(lengths, numpy_inside, numpy_cross, reflect, nodes, weights, index, gamma, frequencies, 3)
        result["max_response_relative_difference"] = float(np.abs(native_h - numpy_h).max() / np.abs(numpy_h).max())
        series = np.cumsum(rng.normal(size=20_000)) + 100
        series[:5] = np.nan
        worst = 0.0
        for alpha in (0.5, 0.1, 2 / 201, 1 / 14, 0.95):
            native_e, numpy_e = ema(series, alpha), numpy_engine.ema(series, alpha)
            worst = max(worst, float(np.nanmax(np.abs(native_e - numpy_e) / np.abs(numpy_e))))
        result["max_ema_relative_difference"] = worst
    return result
# --- END [1.3] ---
