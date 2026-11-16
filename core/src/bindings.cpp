// ==============================================================================
// FILE: core/src/bindings.cpp
// OVERVIEW: The devos_core Python module: binds every native function in one place.
// PURPOSE:
//   1. The only PYBIND11_MODULE: add a binding here when adding a native function.
// CONTRACT:
//   - Exposes (Python module devos_core): trace_segments, synthesize_response, ema, version
// NOTES:
//   - [2026-11-15] Created (bindings moved out of wave_engine.cpp; ema added for market data).
// ==============================================================================
#include "series.hpp"
#include "wave_engine.hpp"

#include <limits>
#include <string>

PYBIND11_MODULE(devos_core, module) {
    module.doc() = "DevOS native engine (C++20): ray-mesh tracing and RF channel synthesis";
    module.def("trace_segments", &trace_segments, py::arg("origins"), py::arg("ends"), py::arg("vertices"),
               py::arg("faces"), py::arg("face_body"), py::arg("body_count"), py::arg("skip_face"),
               "Per ray and body: (inside length in metres (R,B) float64, surface crossings (R,B) int32).");
    module.def("synthesize_response", &synthesize_response, py::arg("path_lengths"), py::arg("inside_length"),
               py::arg("crossings"), py::arg("reflect_body"), py::arg("path_node"), py::arg("path_weight"),
               py::arg("refractive_index"),
               py::arg("reflection"), py::arg("frequencies_hz"), py::arg("node_count"),
               "Complex channel response H[node, frequency] from traced paths.");
    module.def("ema", &ema, py::arg("values"), py::arg("alpha"),
               py::arg("initial") = std::numeric_limits<double>::quiet_NaN(),
               "Exponential moving average y[i] = y[i-1] + alpha * (x[i] - y[i-1]); NaNs skipped.");
    module.def("version", [] { return std::string("devos_core 0.2 (C++") + std::to_string(__cplusplus) + ")"; });
}
