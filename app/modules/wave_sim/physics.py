# ==============================================================================
# FILE: app/modules/wave_sim/physics.py
# OVERVIEW: The wave simulator's source and data model: a tunable LC-tank oscillator sweep and the
#           4D wave tensor (nodes x time x frequency x [amplitude, phase]) it fills.
# PURPOSE:
#   1. SweepSettings: 1-10 GHz sweep as an FMCW chirp (evenly spaced samples, band edges included)
#      or OFDM (subcarrier centres with guard bands).
#   2. LC tank maths: f = 1 / (2 pi sqrt(L C)) and the capacitance a varactor must reach per frequency.
#   3. WaveTensor: contiguous float32 ring buffer, shape (nodes, time, frequency, 2), ready to hand to
#      C++ (buffer protocol, zero copy) or PyTorch (torch.from_numpy).
# CONTRACT:
#   - Requires: numpy, app.data.tensor4d.Tensor4D
#   - Exposes: SweepSettings, WAVEFORM_FMCW, WAVEFORM_OFDM, lc_tank_frequency_hz(),
#              lc_tank_capacitance_f(), WaveTensor
# NOTES:
#   - [2026-11-15] Created. The channel is computed in the frequency domain; FMCW vs OFDM only
#                  changes which frequencies are sampled (both measure H(f) at those points).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - FMCW: Frequency-modulated continuous wave; a linear chirp from start to stop, sampled bin_count times.
# - guard band: Unused edge of an OFDM band (GUARD_FRACTION of the bandwidth on each side).
# - head (int): Next time slot WaveTensor.push() writes; the buffer wraps around.
# - OFDM: Orthogonal frequency-division multiplexing; many evenly spaced subcarriers sent at once.
# - tank inductance (H): Fixed L of the LC tank; tuning sweeps C (varactor).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SWEEP
#    1.1 sweep_settings
#    1.2 lc_tank
# 2. TENSOR
#    2.1 wave_tensor
# ==============================================================================

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from app.data.tensor4d import Tensor4D

WAVEFORM_FMCW = "FMCW chirp"
WAVEFORM_OFDM = "OFDM"


# ==============================================================================
# [1.1] sweep_settings
# ==============================================================================
@dataclass(frozen=True)
class SweepSettings:
    start_hz: float = 1.0e9
    stop_hz: float = 10.0e9
    bin_count: int = 128
    waveform: str = WAVEFORM_FMCW
    tank_inductance_h: float = 1.0e-9

    GUARD_FRACTION = 0.05

    def frequencies_hz(self) -> np.ndarray:
        if self.waveform == WAVEFORM_OFDM:
            guard_hz = self.GUARD_FRACTION * (self.stop_hz - self.start_hz)
            usable_start, usable_stop = self.start_hz + guard_hz, self.stop_hz - guard_hz
            spacing_hz = (usable_stop - usable_start) / self.bin_count
            return usable_start + spacing_hz * (np.arange(self.bin_count) + 0.5)   # subcarrier centres
        return np.linspace(self.start_hz, self.stop_hz, self.bin_count)

    @property
    def center_hz(self) -> float:
        return 0.5 * (self.start_hz + self.stop_hz)

    def to_dict(self) -> dict:
        return asdict(self)
# --- END [1.1] ---

# ==============================================================================
# [1.2] lc_tank
# ==============================================================================
def lc_tank_frequency_hz(inductance_h: float, capacitance_f: np.ndarray | float) -> np.ndarray:
    """Resonant frequency of an LC tank: f = 1 / (2 pi sqrt(L C))."""
    return 1.0 / (2 * np.pi * np.sqrt(inductance_h * np.asarray(capacitance_f)))


def lc_tank_capacitance_f(inductance_h: float, frequency_hz: np.ndarray | float) -> np.ndarray:
    """Capacitance that tunes the tank to frequency_hz: C = 1 / ((2 pi f)^2 L)."""
    return 1.0 / ((2 * np.pi * np.asarray(frequency_hz)) ** 2 * inductance_h)
# --- END [1.2] ---


# ==============================================================================
# [2.1] wave_tensor
# ==============================================================================
class WaveTensor:
    """Ring buffer of channel snapshots: float32 (nodes, time, frequency, 2) = [amplitude, phase rad]."""

    AXIS_NAMES = ("node", "t", "f", "part")

    def __init__(self, node_count: int, time_steps: int, frequency_count: int) -> None:
        self.data = np.zeros((node_count, time_steps, frequency_count, 2), dtype=np.float32)   # C-contiguous
        self.timestamps_s = np.full(time_steps, np.nan)
        self.head = 0
        self.frame_count = 0

    @property
    def shape(self) -> tuple[int, int, int, int]:
        return self.data.shape

    @property
    def time_steps(self) -> int:
        return self.data.shape[1]

    def push(self, response: np.ndarray, timestamp_s: float) -> None:
        """Store one complex response (nodes, frequency) at the head slot."""
        self.data[:, self.head, :, 0] = np.abs(response)
        self.data[:, self.head, :, 1] = np.angle(response)
        self.timestamps_s[self.head] = timestamp_s
        self.head = (self.head + 1) % self.time_steps
        self.frame_count += 1

    def time_order(self) -> np.ndarray:
        """Slot indices oldest -> newest (only filled slots)."""
        filled = min(self.frame_count, self.time_steps)
        return (self.head - filled + np.arange(filled)) % self.time_steps

    def node_history(self, node: int) -> np.ndarray:
        """(filled_time, frequency, 2) for one node, oldest first."""
        return self.data[node, self.time_order()]

    def complex_history(self, node: int) -> np.ndarray:
        history = self.node_history(node)
        return history[..., 0] * np.exp(1j * history[..., 1])

    def as_tensor4d(self) -> Tensor4D:
        """Time-ordered copy with named axes (node, t, f, part)."""
        return Tensor4D(self.data[:, self.time_order()], self.AXIS_NAMES)
# --- END [2.1] ---
