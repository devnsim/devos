// ==============================================================================
// FILE: core/src/series.cpp
// OVERVIEW: Recursive time-series filters - the loops NumPy can't vectorise.
// PURPOSE:
//   1. ema: exponential moving average with any alpha. Building block for EMA, MACD, RSI (Wilder,
//      alpha = 1/n), Heikin-Ashi opens (alpha = 0.5) and Ornstein-Uhlenbeck processes.
// CONTRACT:
//   - Requires: common.hpp; Python mirror: app/native/numpy_engine.py ema()
//   - Exposes: ema(values, alpha, initial)
// NOTES:
//   - [2026-11-15] Created for the trading chart module.
// ==============================================================================
#include "series.hpp"

#include <cmath>
#include <limits>
#include <stdexcept>

devos::Array<double> ema(devos::Array<double> values, double alpha, double initial) {
    if (!(alpha > 0.0 && alpha <= 1.0)) throw std::invalid_argument("ema: alpha must be in (0, 1]");
    const auto x = values.unchecked<1>();
    const auto count = values.shape(0);
    devos::Array<double> result(count);
    auto y = result.mutable_unchecked<1>();
    double state = initial;
    bool has_state = std::isfinite(initial);
    for (py::ssize_t i = 0; i < count; ++i) {
        const double value = x(i);
        if (std::isfinite(value)) {
            state = has_state ? state + alpha * (value - state) : value;
            has_state = true;
        }
        y(i) = has_state ? state : std::numeric_limits<double>::quiet_NaN();
    }
    return result;
}
