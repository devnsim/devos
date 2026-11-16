// ==============================================================================
// FILE: core/src/wave_engine.hpp
// OVERVIEW: RF wave engine: ray-mesh tracing and channel synthesis (see wave_engine.cpp).
// PURPOSE:
//   1. Declares trace_segments() and synthesize_response() for bindings.cpp.
// CONTRACT:
//   - Requires: common.hpp
//   - Exposes: trace_segments(...), synthesize_response(...)
// NOTES:
//   - [2026-11-15] Created when devos_core was split into several files.
// ==============================================================================
#pragma once

#include "common.hpp"

py::tuple trace_segments(devos::Array<double> origins, devos::Array<double> ends, devos::Array<double> vertices,
                         devos::Array<int> faces, devos::Array<int> face_body, int body_count,
                         devos::Array<int> skip_face);

devos::Array<devos::Complex> synthesize_response(
    devos::Array<double> path_lengths, devos::Array<double> inside_length, devos::Array<int> crossings,
    devos::Array<int> reflect_body, devos::Array<int> path_node, devos::Array<double> path_weight,
    devos::Array<devos::Complex> refractive_index, devos::Array<devos::Complex> reflection,
    devos::Array<double> frequencies_hz, int node_count);
