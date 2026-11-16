// ==============================================================================
// FILE: core/src/wave_engine.cpp
// OVERVIEW: devos_core - DevOS's native (C++20) engine, exposed to Python with pybind11.
// PURPOSE:
//   1. trace_segments: exact segment-vs-triangle-mesh intersection (Moller-Trumbore) with per-body
//      bounding-box culling. For every ray it returns how far it travels inside each body and how
//      many surfaces of each body it crosses.
//   2. synthesize_response: turns traced paths into the complex channel response H[node, frequency]
//      (free-space spreading 1/r, phase e^{-jkr}, dielectric slowing + absorption inside bodies,
//      interface transmission losses, reflection coefficient of the bouncing surface, and a
//      per-path weight (e.g. Fresnel-zone size of the reflecting patch).
// CONTRACT:
//   - Requires: C++20, pybind11 >= 2.10, NumPy arrays (C-contiguous; converted if not), common.hpp
//   - Exposes: trace_segments(...), synthesize_response(...) (declared in wave_engine.hpp, bound in
//              bindings.cpp)
//   - Python mirror with identical maths: app/native/numpy_engine.py (used when this isn't built).
// NOTES:
//   - [2026-11-15] Created. Build: python -m app.native.build (CMake, see core/CMakeLists.txt).
//   - [2026-11-15] Inside lengths use per-body hit parity: sorted hits toggle inside/outside. Hits
//                  closer than 1e-9 (a ray through a shared triangle edge) count once. A segment that
//                  ends inside a body counts the remainder; segments are assumed to START outside.
//   - [2026-11-15] Hits within EDGE_EPSILON of either end are ignored, so legs that start/end on a
//                  surface (reflection points) don't count that surface.
//   - [2026-11-15] Python bindings moved to bindings.cpp; shared aliases to common.hpp.
// ==============================================================================

#include "wave_engine.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <stdexcept>
#include <vector>

using namespace devos;
using Vec3 = std::array<double, 3>;

namespace {

constexpr double SPEED_OF_LIGHT_M_S = 299'792'458.0;
constexpr double PI = 3.14159265358979323846;
constexpr double EDGE_EPSILON = 1e-7;      // fraction of the segment
constexpr double DUPLICATE_HIT = 1e-9;     // same-body hits closer than this are one crossing

inline Vec3 subtract(const Vec3& a, const Vec3& b) { return {a[0] - b[0], a[1] - b[1], a[2] - b[2]}; }
inline Vec3 cross(const Vec3& a, const Vec3& b) {
    return {a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]};
}
inline double dot(const Vec3& a, const Vec3& b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

// Moller-Trumbore; returns the hit parameter t along origin + t * direction, or -1 if no hit.
inline double intersect(const Vec3& origin, const Vec3& direction, const Vec3& v0, const Vec3& v1, const Vec3& v2) {
    const Vec3 edge1 = subtract(v1, v0), edge2 = subtract(v2, v0);
    const Vec3 p = cross(direction, edge2);
    const double determinant = dot(edge1, p);
    if (std::abs(determinant) < 1e-14) return -1.0;               // parallel to the triangle
    const double inverse = 1.0 / determinant;
    const Vec3 s = subtract(origin, v0);
    const double u = dot(s, p) * inverse;
    if (u < 0.0 || u > 1.0) return -1.0;
    const Vec3 q = cross(s, edge1);
    const double v = dot(direction, q) * inverse;
    if (v < 0.0 || u + v > 1.0) return -1.0;
    return dot(edge2, q) * inverse;
}

struct Box { Vec3 low{1e300, 1e300, 1e300}, high{-1e300, -1e300, -1e300}; };

// Slab test: does origin + t * direction, t in [0, 1], touch the box?
inline bool segment_touches_box(const Vec3& origin, const Vec3& direction, const Box& box) {
    double t_enter = 0.0, t_exit = 1.0;
    for (int axis = 0; axis < 3; ++axis) {
        if (std::abs(direction[axis]) < 1e-15) {
            if (origin[axis] < box.low[axis] || origin[axis] > box.high[axis]) return false;
            continue;
        }
        double t0 = (box.low[axis] - origin[axis]) / direction[axis];
        double t1 = (box.high[axis] - origin[axis]) / direction[axis];
        if (t0 > t1) std::swap(t0, t1);
        t_enter = std::max(t_enter, t0);
        t_exit = std::min(t_exit, t1);
        if (t_enter > t_exit) return false;
    }
    return true;
}

}  // namespace

// ------------------------------------------------------------------------------
// trace_segments
// ------------------------------------------------------------------------------
py::tuple trace_segments(Array<double> origins, Array<double> ends, Array<double> vertices, Array<int> faces,
                         Array<int> face_body, int body_count, Array<int> skip_face) {
    const auto ray_count = origins.shape(0);
    const auto face_count = faces.shape(0);
    if (ends.shape(0) != ray_count || skip_face.shape(0) != ray_count || face_body.shape(0) != face_count)
        throw std::invalid_argument("trace_segments: array lengths don't match");

    const auto o = origins.unchecked<2>();
    const auto e = ends.unchecked<2>();
    const auto v = vertices.unchecked<2>();
    const auto f = faces.unchecked<2>();
    const auto fb = face_body.unchecked<1>();
    const auto skip = skip_face.unchecked<1>();

    // Triangles grouped by body, plus each body's bounding box (cheap rejection).
    std::vector<std::vector<int>> body_faces(body_count);
    std::vector<Box> boxes(body_count);
    for (py::ssize_t face = 0; face < face_count; ++face) {
        const int body = fb(face);
        if (body < 0 || body >= body_count) throw std::invalid_argument("trace_segments: face_body out of range");
        body_faces[body].push_back(static_cast<int>(face));
        for (int corner = 0; corner < 3; ++corner)
            for (int axis = 0; axis < 3; ++axis) {
                const double value = v(f(face, corner), axis);
                boxes[body].low[axis] = std::min(boxes[body].low[axis], value);
                boxes[body].high[axis] = std::max(boxes[body].high[axis], value);
            }
    }

    Array<double> inside_length({static_cast<py::ssize_t>(ray_count), static_cast<py::ssize_t>(body_count)});
    Array<int> crossings({static_cast<py::ssize_t>(ray_count), static_cast<py::ssize_t>(body_count)});
    auto inside = inside_length.mutable_unchecked<2>();
    auto cross_count = crossings.mutable_unchecked<2>();

    std::vector<double> hits;
    for (py::ssize_t ray = 0; ray < ray_count; ++ray) {
        const Vec3 origin{o(ray, 0), o(ray, 1), o(ray, 2)};
        const Vec3 direction{e(ray, 0) - origin[0], e(ray, 1) - origin[1], e(ray, 2) - origin[2]};
        const double length = std::sqrt(dot(direction, direction));
        for (int body = 0; body < body_count; ++body) {
            inside(ray, body) = 0.0;
            cross_count(ray, body) = 0;
            if (!segment_touches_box(origin, direction, boxes[body])) continue;
            hits.clear();
            for (const int face : body_faces[body]) {
                if (face == skip(ray)) continue;
                const Vec3 v0{v(f(face, 0), 0), v(f(face, 0), 1), v(f(face, 0), 2)};
                const Vec3 v1{v(f(face, 1), 0), v(f(face, 1), 1), v(f(face, 1), 2)};
                const Vec3 v2{v(f(face, 2), 0), v(f(face, 2), 1), v(f(face, 2), 2)};
                const double t = intersect(origin, direction, v0, v1, v2);
                if (t > EDGE_EPSILON && t < 1.0 - EDGE_EPSILON) hits.push_back(t);
            }
            if (hits.empty()) continue;
            std::sort(hits.begin(), hits.end());
            // Drop duplicates (ray through an edge shared by two triangles).
            std::vector<double> unique_hits{hits.front()};
            for (std::size_t index = 1; index < hits.size(); ++index)
                if (hits[index] - unique_hits.back() > DUPLICATE_HIT) unique_hits.push_back(hits[index]);
            // Parity: entry at even positions, exit at odd ones; an odd count ends inside the body.
            double inside_fraction = 0.0;
            for (std::size_t index = 0; index < unique_hits.size(); ++index)
                inside_fraction += (index % 2 == 0 ? -unique_hits[index] : unique_hits[index]);
            if (unique_hits.size() % 2 == 1) inside_fraction += 1.0;
            inside(ray, body) = inside_fraction * length;
            cross_count(ray, body) = static_cast<int>(unique_hits.size());
        }
    }
    return py::make_tuple(inside_length, crossings);
}

// ------------------------------------------------------------------------------
// synthesize_response
// ------------------------------------------------------------------------------
Array<Complex> synthesize_response(Array<double> path_lengths, Array<double> inside_length, Array<int> crossings,
                                   Array<int> reflect_body, Array<int> path_node, Array<double> path_weight, Array<Complex> refractive_index,
                                   Array<Complex> reflection, Array<double> frequencies_hz, int node_count) {
    const auto path_count = path_lengths.shape(0);
    const auto body_count = inside_length.shape(1);
    const auto frequency_count = frequencies_hz.shape(0);
    if (refractive_index.shape(0) != body_count || refractive_index.shape(1) != frequency_count ||
        reflection.shape(0) != body_count || reflection.shape(1) != frequency_count)
        throw std::invalid_argument("synthesize_response: material arrays must be (body_count, frequency_count)");

    const auto L = path_lengths.unchecked<1>();
    const auto d = inside_length.unchecked<2>();
    const auto c = crossings.unchecked<2>();
    const auto rb = reflect_body.unchecked<1>();
    const auto node = path_node.unchecked<1>();
    const auto weight = path_weight.unchecked<1>();
    const auto n = refractive_index.unchecked<2>();
    const auto gamma = reflection.unchecked<2>();
    const auto freq = frequencies_hz.unchecked<1>();

    Array<Complex> response({static_cast<py::ssize_t>(node_count), static_cast<py::ssize_t>(frequency_count)});
    auto H = response.mutable_unchecked<2>();
    for (int each_node = 0; each_node < node_count; ++each_node)
        for (py::ssize_t k = 0; k < frequency_count; ++k) H(each_node, k) = Complex(0.0, 0.0);

    const Complex j(0.0, 1.0);
    for (py::ssize_t p = 0; p < path_count; ++p) {
        const int target = node(p);
        if (target < 0 || target >= node_count) continue;
        for (py::ssize_t k = 0; k < frequency_count; ++k) {
            const double k0 = 2.0 * PI * freq(k) / SPEED_OF_LIGHT_M_S;
            Complex amplitude = weight(p) * std::exp(-j * k0 * L(p)) / L(p);    // spherical wave, 1/r
            for (py::ssize_t b = 0; b < body_count; ++b) {
                if (d(p, b) > 0.0) amplitude *= std::exp(-j * k0 * (n(b, k) - 1.0) * d(p, b));
                const int interfaces = c(p, b);
                if (interfaces > 0) {
                    const Complex index = n(b, k);
                    const Complex pair = 4.0 * index / ((1.0 + index) * (1.0 + index));   // in + out
                    amplitude *= std::pow(pair, interfaces / 2);
                    if (interfaces % 2 == 1) amplitude *= 2.0 / (1.0 + index);              // ends inside
                }
            }
            if (rb(p) >= 0) amplitude *= gamma(rb(p), k);
            H(target, k) += amplitude;
        }
    }
    return response;
}
