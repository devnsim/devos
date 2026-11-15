# ==============================================================================
# FILE: app/native/numpy_engine.py
# OVERVIEW: Pure NumPy versions of devos_core's functions (identical maths), used when C++ isn't built.
# PURPOSE:
#   1. trace_segments: vectorised Moller-Trumbore over all rays x triangles, then per-body hit parity.
#   2. synthesize_response: complex channel response from traced paths.
# CONTRACT:
#   - Requires: numpy
#   - Exposes: trace_segments(), synthesize_response() - same signatures and results as devos_core
# NOTES:
#   - [2026-11-15] Created alongside core/src/wave_engine.cpp. Keep the two in step: the test in
#                  app/native/__init__.py's self_test() compares them.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - See core/src/wave_engine.cpp for argument meanings; shapes: R rays, T triangles, B bodies,
#   P paths, F frequencies, N nodes.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. ENGINE
#    1.1 trace_segments
#    1.2 synthesize_response
# ==============================================================================

import numpy as np

SPEED_OF_LIGHT_M_S = 299_792_458.0
EDGE_EPSILON = 1e-7
DUPLICATE_HIT = 1e-9


# ==============================================================================
# [1.1] trace_segments
# ==============================================================================
def trace_segments(origins, ends, vertices, faces, face_body, body_count, skip_face):
    origins = np.asarray(origins, float)
    direction = np.asarray(ends, float) - origins                                  # (R, 3)
    triangles = np.asarray(vertices, float)[np.asarray(faces)]                      # (T, 3, 3)
    face_body = np.asarray(face_body)
    skip_face = np.asarray(skip_face)
    ray_count = len(origins)
    inside = np.zeros((ray_count, body_count))
    crossings = np.zeros((ray_count, body_count), np.int32)
    if ray_count == 0 or len(triangles) == 0:
        return inside, crossings

    v0 = triangles[:, 0]
    edge1 = triangles[:, 1] - v0
    edge2 = triangles[:, 2] - v0
    p = np.cross(direction[:, None, :], edge2[None, :, :])                          # (R, T, 3)
    determinant = np.einsum("tk,rtk->rt", edge1, p)
    with np.errstate(divide="ignore", invalid="ignore"):
        inverse = 1.0 / determinant
        s = origins[:, None, :] - v0[None, :, :]
        u = np.einsum("rtk,rtk->rt", s, p) * inverse
        q = np.cross(s, edge1[None, :, :])
        v = np.einsum("rk,rtk->rt", direction, q) * inverse
        t = np.einsum("tk,rtk->rt", edge2, q) * inverse
    with np.errstate(invalid="ignore"):                                     # NaN where parallel
        is_hit = (np.abs(determinant) >= 1e-14) & (u >= 0) & (u <= 1) & (v >= 0) & (u + v <= 1)
        is_hit &= (t > EDGE_EPSILON) & (t < 1 - EDGE_EPSILON)
    is_hit &= np.arange(len(triangles))[None, :] != skip_face[:, None]
    lengths = np.linalg.norm(direction, axis=1)

    for body in range(body_count):
        body_faces = np.flatnonzero(face_body == body)
        if len(body_faces) == 0:
            continue
        body_t = np.where(is_hit[:, body_faces], t[:, body_faces], np.inf)
        body_t.sort(axis=1)
        # Drop near-duplicate hits (ray through a shared edge): mark repeats as inf and re-sort.
        with np.errstate(invalid="ignore"):                                 # inf - inf between misses
            repeat = np.diff(body_t, axis=1) <= DUPLICATE_HIT
        body_t[:, 1:][repeat & np.isfinite(body_t[:, 1:])] = np.inf
        body_t.sort(axis=1)
        is_valid = np.isfinite(body_t)
        counts = is_valid.sum(axis=1)
        signs = np.where(np.arange(body_t.shape[1]) % 2 == 0, -1.0, 1.0)
        inside_fraction = np.where(is_valid, body_t * signs, 0.0).sum(axis=1) + (counts % 2)
        inside[:, body] = inside_fraction * lengths
        crossings[:, body] = counts
    return inside, crossings
# --- END [1.1] ---


# ==============================================================================
# [1.2] synthesize_response
# ==============================================================================
def synthesize_response(path_lengths, inside_length, crossings, reflect_body, path_node, path_weight,
                        refractive_index, reflection, frequencies_hz, node_count):
    path_lengths = np.asarray(path_lengths, float)
    inside_length = np.asarray(inside_length, float)                                # (P, B)
    crossings = np.asarray(crossings)
    reflect_body = np.asarray(reflect_body)
    refractive_index = np.asarray(refractive_index, complex)                         # (B, F)
    k0 = 2 * np.pi * np.asarray(frequencies_hz, float) / SPEED_OF_LIGHT_M_S        # (F,)

    amplitude = (np.asarray(path_weight, float)[:, None] * np.exp(-1j * k0[None, :] * path_lengths[:, None])
                 / path_lengths[:, None])                                                         # (P, F)
    # Dielectric slowing + absorption: exp(-j k0 (n - 1) d), summed over bodies in the exponent.
    amplitude *= np.exp(-1j * k0[None, :] * np.einsum("pb,bf->pf", inside_length, refractive_index - 1))
    pair = 4 * refractive_index / (1 + refractive_index) ** 2                       # (B, F)
    single = 2 / (1 + refractive_index)
    for body in np.flatnonzero(crossings.any(axis=0)):
        interfaces = crossings[:, body][:, None]
        amplitude *= pair[body][None, :] ** (interfaces // 2)
        amplitude *= np.where(interfaces % 2 == 1, single[body][None, :], 1.0)
    has_reflection = reflect_body >= 0
    amplitude[has_reflection] *= np.asarray(reflection)[reflect_body[has_reflection]]

    response = np.zeros((node_count, len(k0)), complex)
    np.add.at(response, np.asarray(path_node), amplitude)
    return response
# --- END [1.2] ---
