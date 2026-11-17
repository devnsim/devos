# ==============================================================================
# FILE: engines/toolkit/series.py
# OVERVIEW: Series shape toolkit - everyday operations on values over time, shared by engines: cut a time
#           window, resample to a fixed step, decimate for display without losing peaks.
# PURPOSE:
#   1. window(): the samples between two times.
#   2. resample(): one value per step (mean / last / min / max / sum), empty steps left out.
#   3. decimate(): at most N points for a display, keeping each bucket's minimum and maximum, so spikes in a
#      10 kHz signal still show on a 1000-pixel plot.
# CONTRACT:
#   - Requires: numpy.
#   - Exposes: window(times, values, start, end), resample(times, values, step, how), decimate(times, values, max_points)
# NOTES:
#   - [2026-11-17] Created in Phase 2 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - times / values: Equal-length 1-D arrays; times ascending (seconds).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SERIES_TOOLKIT
#    1.1 operations
# ==============================================================================

# ==============================================================================
# [1.1] operations
# ==============================================================================
from __future__ import annotations

import numpy as np

HOW = {"mean": np.mean, "last": lambda block: block[-1], "min": np.min, "max": np.max, "sum": np.sum}


def window(times, values, start: float | None = None, end: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    times, values = np.asarray(times, dtype=float), np.asarray(values)
    low = 0 if start is None else int(np.searchsorted(times, start, side="left"))
    high = len(times) if end is None else int(np.searchsorted(times, end, side="right"))
    return times[low:high], values[low:high]


def resample(times, values, step: float, how: str = "mean") -> tuple[np.ndarray, np.ndarray]:
    """One value per `step` seconds (bucket start times); buckets without samples are left out."""
    if how not in HOW:
        raise ValueError(f"how must be one of {sorted(HOW)}")
    times, values = np.asarray(times, dtype=float), np.asarray(values, dtype=float)
    if len(times) == 0:
        return times, values
    buckets = np.floor((times - times[0]) / step).astype(np.int64)
    edges = np.flatnonzero(np.diff(buckets)) + 1
    starts = np.concatenate([[0], edges])
    out_values = np.array([HOW[how](block) for block in np.split(values, edges)])
    return times[0] + buckets[starts] * step, out_values


def decimate(times, values, max_points: int) -> tuple[np.ndarray, np.ndarray]:
    """At most max_points samples, keeping each bucket's min and max (in time order) so peaks survive."""
    times, values = np.asarray(times, dtype=float), np.asarray(values, dtype=float)
    if len(times) <= max_points or max_points < 4:
        return times, values
    bucket_count = max_points // 2
    keep = []
    for indices in np.array_split(np.arange(len(times)), bucket_count):
        block = values[indices]
        low, high = indices[int(np.argmin(block))], indices[int(np.argmax(block))]
        keep += sorted({low, high})
    keep = np.array(keep)
    return times[keep], values[keep]
# --- END [1.1] ---
