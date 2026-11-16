// ==============================================================================
// FILE: core/src/common.hpp
// OVERVIEW: Shared includes and aliases for every devos_core source file.
// PURPOSE:
//   1. One place for the pybind11 includes and the NumPy array alias (Array<T>).
// CONTRACT:
//   - Requires: pybind11
//   - Exposes: namespace devos { Complex, Array<T> }, alias py
// NOTES:
//   - [2026-11-15] Created when the core grew past one file (wave engine + series maths).
// ==============================================================================
#pragma once

#include <pybind11/complex.h>
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <complex>

namespace py = pybind11;

namespace devos {

using Complex = std::complex<double>;

// NumPy array argument: C-contiguous, converted (copied) if the caller passes another layout/dtype.
template <typename T>
using Array = py::array_t<T, py::array::c_style | py::array::forcecast>;

}  // namespace devos
