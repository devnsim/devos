# ==============================================================================
# FILE: app/modules/wave_sim/engine.py
# OVERVIEW: The wave simulator engine: finds propagation paths through the CAD scene, computes each
#           receiver's complex channel response across the sweep, and streams it into the 4D tensor.
# PURPOSE:
#   1. Paths: the direct TX -> RX ray plus every first-order specular reflection (image method,
#      one per mesh triangle whose mirror point lands inside that triangle).
#   2. Ray-mesh tracing of every path leg (C++ devos_core, NumPy fallback): length inside each body
#      and the surfaces crossed -> dielectric phase slowing + absorption + interface losses.
#   3. Channel synthesis H[node, f] (C++ / NumPy) + receiver noise -> WaveTensor.
#   4. Per-path gain at the centre frequency for drawing colour-coded rays.
# CONTRACT:
#   - Requires: numpy, app.native (trace_segments, synthesize_response, ENGINE_NAME),
#               app.data.em_materials.Material, wave_sim.physics, wave_sim.scene
#   - Exposes: WaveSimEngine(scene, sweep, materials, history_steps); step(dt) -> FrameResult;
#              set_sweep(); set_materials(); FrameResult; PathBundle
# NOTES:
#   - [2026-11-15] Created. Physics per path p at frequency f (k0 = 2 pi f / c):
#                      A_p(f) = exp(-j k0 L) / L                       spherical spreading + phase
#                             * prod_b exp(-j k0 (n_b(f) - 1) d_b)     slowing (Re n) + absorption (Im n)
#                             * prod_b (4 n_b / (1 + n_b)^2)^(crossings/2)   interface transmission
#                             * Gamma_m(f) for a reflected path        Fresnel, normal incidence
#   - [2026-11-15] Reflections are weighted by min(1, patch area / first Fresnel zone area) at the
#                  centre frequency, so a tiny facet can't reflect like a whole wall (heuristic for
#                  finite-surface scattering). A patch = coplanar triangles of one body.
#                  Bounces landing on a shared triangle edge are de-duplicated.
#   - [2026-11-15] Approximations: normal-incidence Fresnel for every angle; reflections only from
#                  the air side of a surface; first-order bounces only; no diffraction.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - excess loss (dB): Path gain relative to free space over the same length; 0 dB = nothing in the way.
# - leg: One straight segment of a path (direct = 1 leg, reflection = 2 legs).
# - NOISE_FLOOR (float): Std-dev of complex receiver noise added to H (default: 1e-5, ~-100 dB).
# - path gain (dB): 20 log10 |A_p(f_c)|, including 1/r spreading.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. RESULTS
#    1.1 path_bundle_and_frame_result
# 2. ENGINE
#    2.1 init_and_settings
#    2.2 find_paths
#    2.3 step
# ==============================================================================

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from app import native
from app.data.em_materials import SPEED_OF_LIGHT_M_S, Material
from app.modules.wave_sim.physics import SweepSettings, WaveTensor
from app.modules.wave_sim.scene import Scene

NO_FACE = -1


# ==============================================================================
# [1.1] path_bundle_and_frame_result
# ==============================================================================
@dataclass
class PathBundle:
    """Every path found this frame (P paths)."""

    node: np.ndarray                 # (P,) receiver index
    points: np.ndarray               # (P, 3, 3) TX, bounce point (= TX for direct), RX
    length_m: np.ndarray             # (P,)
    reflect_body: np.ndarray         # (P,) body index or -1 (direct)
    weight: np.ndarray               # (P,) Fresnel-zone weight (1 for direct paths)
    inside_m: np.ndarray             # (P, B)
    crossings: np.ndarray            # (P, B)
    gain_db: np.ndarray | None = None          # (P,) at the centre frequency
    excess_loss_db: np.ndarray | None = None   # (P,)


@dataclass
class FrameResult:
    timestamp_s: float
    response: np.ndarray             # (nodes, F) complex
    paths: PathBundle
    trace_ms: float
    synth_ms: float
# --- END [1.1] ---


# ==============================================================================
# [2.1] init_and_settings
# ==============================================================================
class WaveSimEngine:
    NOISE_FLOOR = 1e-5

    def __init__(self, scene: Scene, sweep: SweepSettings, materials: dict[str, Material], history_steps: int = 160,
                 seed: int = 11) -> None:
        self.scene, self.materials, self.history_steps = scene, materials, history_steps
        self.time_s = 0.0
        self.is_animating = True
        self._rng = np.random.default_rng(seed)
        self.set_sweep(sweep)

    def set_sweep(self, sweep: SweepSettings) -> None:
        """New sweep -> new frequency grid -> the tensor restarts."""
        self.sweep = sweep
        self.frequencies_hz = sweep.frequencies_hz()
        self.reset_tensor()

    def reset_tensor(self) -> None:
        self.tensor = WaveTensor(self.scene.node_count, self.history_steps, len(self.frequencies_hz))

    def set_materials(self, materials: dict[str, Material]) -> None:
        self.materials = materials

    def material_arrays(self) -> tuple[np.ndarray, np.ndarray]:
        """(refractive index n (B, F), reflection coefficient Gamma (B, F)) for the scene's bodies."""
        indices, reflections = [], []
        for body in self.scene.bodies:
            material = self.materials[body.material_name]
            if material.is_conductor:
                # Perfect conductor: nothing gets through, everything reflects.
                indices.append(np.full(len(self.frequencies_hz), 1e4 - 1e4j))
            else:
                indices.append(material.refractive_index(self.frequencies_hz))
            reflections.append(material.reflection_coefficient(self.frequencies_hz))
        if not indices:
            empty = np.zeros((0, len(self.frequencies_hz)), complex)
            return empty, empty
        return np.array(indices), np.array(reflections)
# --- END [2.1] ---

# ==============================================================================
# [2.2] find_paths
# ==============================================================================
    def find_paths(self, vertices: np.ndarray, faces: np.ndarray, face_body: np.ndarray) -> PathBundle:
        """Direct paths + first-order specular reflections (image method), with every leg traced."""
        tx = np.asarray(self.scene.tx_position_m, float)
        rx = np.asarray(self.scene.rx_positions_m, float)
        node_count = len(rx)

        # --- reflection candidates: every triangle x node ---
        triangles = vertices[faces]                                          # (T, 3, 3)
        v0 = triangles[:, 0]
        normals = np.cross(triangles[:, 1] - v0, triangles[:, 2] - v0)
        normal_length = np.linalg.norm(normals, axis=1)
        usable = normal_length > 1e-12
        normals[usable] /= normal_length[usable, None]
        tx_side = np.einsum("tk,tk->t", tx[None, :] - v0, normals)                          # (T,)
        rx_side = np.einsum("tnk,tk->tn", rx[None, :, :] - v0[:, None, :], normals)         # (T, N)
        image = tx[None, :] - 2 * tx_side[:, None] * normals                                 # (T, 3) mirrored TX
        toward_rx = rx[None, :, :] - image[:, None, :]                                       # (T, N, 3)
        denominator = np.einsum("tnk,tk->tn", toward_rx, normals)
        with np.errstate(divide="ignore", invalid="ignore"):
            s = np.einsum("tk,tk->t", v0 - image, normals)[:, None] / denominator
        bounce = image[:, None, :] + s[..., None] * toward_rx                                # (T, N, 3)
        # Inside the triangle? (barycentric)
        edge1, edge2 = triangles[:, 1] - v0, triangles[:, 2] - v0
        offset = bounce - v0[:, None, :]
        d00, d01, d11 = (np.einsum("tk,tk->t", a, b) for a, b in ((edge1, edge1), (edge1, edge2), (edge2, edge2)))
        d20 = np.einsum("tnk,tk->tn", offset, edge1)
        d21 = np.einsum("tnk,tk->tn", offset, edge2)
        with np.errstate(divide="ignore", invalid="ignore"):
            inverse = 1.0 / (d00 * d11 - d01 * d01)
            bary_v = (d11[:, None] * d20 - d01[:, None] * d21) * inverse[:, None]
            bary_w = (d00[:, None] * d21 - d01[:, None] * d20) * inverse[:, None]
        is_valid = (usable[:, None] & (tx_side[:, None] * rx_side > 1e-9) & (s > 0) & (s < 1)
                    & (bary_v >= 0) & (bary_w >= 0) & (bary_v + bary_w <= 1))
        face_index, node_index = np.nonzero(is_valid)
        bounce_points = bounce[face_index, node_index]                                       # (R, 3)
        # One bounce per (node, body, point): a point on a shared triangle edge is found twice.
        keys = np.column_stack([node_index, face_body[face_index], np.round(bounce_points, 6)])
        _, first = np.unique(keys, axis=0, return_index=True)
        first = np.sort(first)
        face_index, node_index, bounce_points = face_index[first], node_index[first], bounce_points[first]
        reflection_count = len(face_index)

        # Fresnel-zone weight: patch area vs first Fresnel zone (pi * lambda * d1 d2 / (d1 + d2)).
        patch_area = self._patch_areas(triangles, normals, face_body)[face_index]
        d1 = np.linalg.norm(bounce_points - tx, axis=1)
        d2 = np.linalg.norm(rx[node_index] - bounce_points, axis=1)
        fresnel_area = np.pi * (SPEED_OF_LIGHT_M_S / self.sweep.center_hz) * d1 * d2 / np.maximum(d1 + d2, 1e-9)
        reflection_weight = np.minimum(1.0, patch_area / np.maximum(fresnel_area, 1e-12))

        # --- legs: direct (N), then TX->bounce (R), bounce->RX (R) ---
        origins = np.vstack([np.repeat(tx[None, :], node_count, 0), np.repeat(tx[None, :], reflection_count, 0), bounce_points])
        ends = np.vstack([rx, bounce_points, rx[node_index]])
        skip = np.concatenate([np.full(node_count, NO_FACE), face_index, face_index]).astype(np.int32)
        body_count = len(self.scene.bodies)
        inside, crossings = native.trace_segments(origins, ends, vertices, faces, face_body, body_count, skip)

        direct = slice(0, node_count)
        first_leg = slice(node_count, node_count + reflection_count)
        second_leg = slice(node_count + reflection_count, None)
        path_points = np.concatenate([
            np.stack([np.repeat(tx[None, :], node_count, 0), np.repeat(tx[None, :], node_count, 0), rx], axis=1),
            np.stack([np.repeat(tx[None, :], reflection_count, 0), bounce_points, rx[node_index]], axis=1),
        ]).reshape(-1, 3, 3)
        lengths = np.concatenate([
            np.linalg.norm(rx - tx, axis=1),
            np.linalg.norm(bounce_points - tx, axis=1) + np.linalg.norm(rx[node_index] - bounce_points, axis=1),
        ])
        return PathBundle(
            node=np.concatenate([np.arange(node_count), node_index]).astype(np.int32),
            points=path_points,
            length_m=lengths,
            reflect_body=np.concatenate([np.full(node_count, -1), face_body[face_index]]).astype(np.int32),
            weight=np.concatenate([np.ones(node_count), reflection_weight]),
            inside_m=np.vstack([inside[direct], inside[first_leg] + inside[second_leg]]),
            crossings=np.vstack([crossings[direct], crossings[first_leg] + crossings[second_leg]]).astype(np.int32),
        )

    @staticmethod
    def _patch_areas(triangles: np.ndarray, normals: np.ndarray, face_body: np.ndarray) -> np.ndarray:
        """For every triangle, the total area of the coplanar triangles of the same body (its patch)."""
        areas = 0.5 * np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1)
        plane_offset = np.einsum("tk,tk->t", normals, triangles[:, 0])
        keys = np.column_stack([face_body, np.round(normals, 3), np.round(plane_offset, 3)])
        group = np.unique(keys, axis=0, return_inverse=True)[1].ravel()
        return np.bincount(group, weights=areas)[group]
# --- END [2.2] ---

# ==============================================================================
# [2.3] step
# ==============================================================================
    def step(self, dt_s: float = 0.05) -> FrameResult:
        if self.is_animating:
            self.time_s += dt_s
        started = time.perf_counter()
        vertices, faces, face_body = self.scene.triangle_soup(self.time_s)
        paths = self.find_paths(vertices, faces, face_body)
        traced = time.perf_counter()

        refractive_index, reflection = self.material_arrays()
        response = native.synthesize_response(
            paths.length_m, paths.inside_m, paths.crossings, paths.reflect_body, paths.node,
            refractive_index, reflection, self.frequencies_hz, self.scene.node_count, paths.weight,
        )
        response += self.NOISE_FLOOR * (self._rng.normal(size=response.shape) + 1j * self._rng.normal(size=response.shape))
        self.tensor.push(response, self.time_s)
        synthesized = time.perf_counter()

        # Per-path gain at the centre frequency (for colouring rays).
        center = np.array([self.sweep.center_hz])
        center_index = np.argmin(np.abs(self.frequencies_hz - self.sweep.center_hz))
        per_path = native.synthesize_response(
            paths.length_m, paths.inside_m, paths.crossings, paths.reflect_body, np.arange(len(paths.length_m)),
            refractive_index[:, [center_index]], reflection[:, [center_index]], center, len(paths.length_m), paths.weight,
        )[:, 0]
        magnitude = np.abs(per_path) + 1e-30
        paths.gain_db = 20 * np.log10(magnitude)
        paths.excess_loss_db = 20 * np.log10(magnitude * paths.length_m)
        return FrameResult(self.time_s, response, paths, (traced - started) * 1000, (synthesized - traced) * 1000)
# --- END [2.3] ---
