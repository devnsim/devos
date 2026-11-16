// ==============================================================================
// FILE: core/src/series.hpp
// OVERVIEW: Time-series primitives (recursive filters) for market data and other signals.
// PURPOSE:
//   1. Declares ema() (implemented in series.cpp, bound in bindings.cpp).
// CONTRACT:
//   - Requires: common.hpp
//   - Exposes: ema(values, alpha, initial)
// NOTES:
//   - [2026-11-15] Created when devos_core was split into several files.
// ==============================================================================
#pragma once

#include "common.hpp"

// y[i] = (1 - alpha) * y[i-1] + alpha * x[i]; y[0] = x[0] unless initial is finite.
// NaN inputs are skipped (the output repeats the previous value; NaN until the first valid input).
devos::Array<double> ema(devos::Array<double> values, double alpha, double initial);
