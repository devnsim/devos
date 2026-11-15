# ==============================================================================
# FILE: app/modules/rf_imaging/sensor.py
# OVERVIEW: The RF sensor data model: sweep settings, antenna array, frames, captures, and sensors
#           (a physics-based mock now; real hardware later through the same RfSensor interface).
# PURPOSE:
#   1. SweepConfig: the stepped-frequency sweep (default 2.5-6 GHz) and what it implies
#      (range resolution, longest unambiguous path).
#   2. AntennaArray: where the transmit and receive antennas are; every TX-RX pair is one channel.
#   3. RfFrame / RfCapture: one sweep of complex responses, and a whole test session that can be
#      saved to / loaded from .npz (the format real recordings will use too).
#   4. RfSensor: the interface any data source implements (read_frame, calibration_measurements).
#   5. MockRfSensor: simulates a room (walls, objects, a walking person) with the radar forward model,
#      including an unknown per-channel hardware response, leakage and noise, so calibration and
#      imaging are exercised exactly as they will be with real hardware.
#   6. CaptureReplaySensor: plays a saved capture back as if it were live.
# CONTRACT:
#   - Requires: numpy, app.data.em_materials (Material, MATERIAL_BY_NAME, SPEED_OF_LIGHT_M_S)
#   - Exposes: SweepConfig, AntennaArray, RfFrame, RfCapture, RfSensor, Scatterer, MockRfSensor,
#              CaptureReplaySensor, simulate_response(), default_test_scene()
# NOTES:
#   - [2026-11-15] Created. Data is shaped like a stepped-frequency MIMO radar / VNA: per frame, a
#                  complex response H[pair, frequency]. Positions in metres; x = across, y = away
#                  from the array (depth), z = up.
#   - [2026-11-15] Mock time is a simulated test clock (frame_interval_s per frame), not wall time,
#                  so a replay or a slow machine still sees a consistent test.
#   - [2026-11-15] Moved from app/data/rf/sensor.py (module apps live in app/modules/<name>/).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - channel / pair: One TX-RX antenna combination; frames hold one row of responses per pair.
# - frequency bin: One step of the sweep; frames hold one column per bin.
# - leakage: Signal going straight from TX to RX without touching the scene.
# - path length (m): TX -> point -> RX distance; what the phase of a response encodes.
# - response H (complex): Measured amplitude + phase of the received wave relative to the transmitted
#   one, for one pair at one frequency.
# - scatterer: A point that reflects with a material's reflection coefficient (the mock scene is
#   made of these).
# - system response: The unknown gain/phase/delay of each channel's cables and electronics;
#   calibration measures and removes it.
# - unambiguous path (m): c / frequency step; longer paths wrap around (alias) in the image.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. SWEEP_AND_ARRAY
#    2.1 sweep_config
#    2.2 antenna_array
# 3. FRAMES_AND_CAPTURES
#    3.1 rf_frame
#    3.2 rf_capture
# 4. FORWARD_MODEL
#    4.1 scatterer
#    4.2 simulate_response
# 5. SENSORS
#    5.1 rf_sensor_interface
#    5.2 default_test_scene
#    5.3 mock_rf_sensor
#    5.4 capture_replay_sensor
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from app.data.em_materials import MATERIAL_BY_NAME, SPEED_OF_LIGHT_M_S, Material
# --- END [1.1] ---


# ==============================================================================
# [2.1] sweep_config
# ==============================================================================
@dataclass(frozen=True)
class SweepConfig:
    """Stepped-frequency sweep: bin_count evenly spaced frequencies from start_hz to stop_hz."""

    start_hz: float = 2.5e9
    stop_hz: float = 6.0e9
    bin_count: int = 64

    def frequencies_hz(self) -> np.ndarray:
        return np.linspace(self.start_hz, self.stop_hz, self.bin_count)

    @property
    def bandwidth_hz(self) -> float:
        return self.stop_hz - self.start_hz

    @property
    def step_hz(self) -> float:
        return self.bandwidth_hz / (self.bin_count - 1)

    @property
    def range_resolution_m(self) -> float:
        """Smallest separation in depth that can be told apart: c / (2 B)."""
        return SPEED_OF_LIGHT_M_S / (2 * self.bandwidth_hz)

    @property
    def unambiguous_path_m(self) -> float:
        """Longest TX->point->RX path before the image wraps around: c / step."""
        return SPEED_OF_LIGHT_M_S / self.step_hz

    def to_dict(self) -> dict:
        return {"start_hz": self.start_hz, "stop_hz": self.stop_hz, "bin_count": self.bin_count}
# --- END [2.1] ---

# ==============================================================================
# [2.2] antenna_array
# ==============================================================================
@dataclass(frozen=True)
class AntennaArray:
    """Antenna positions (metres). Every TX is paired with every RX (pair index = tx * rx_count + rx)."""

    tx_positions_m: np.ndarray                  # (tx_count, 3)
    rx_positions_m: np.ndarray                  # (rx_count, 3)

    @classmethod
    def l_shaped(cls, count: int = 5, span_m: float = 0.9) -> AntennaArray:
        """TX along the top edge (x), RX down the left edge (z): every pair's midpoint lands on a
        count x count grid, giving a 2D aperture for 3D imaging from few antennas."""
        offsets = np.linspace(-span_m / 2, span_m / 2, count)
        edge_m = span_m / 2 + 0.05
        tx_positions = np.stack([offsets, np.zeros(count), np.full(count, edge_m)], axis=1)
        rx_positions = np.stack([np.full(count, -edge_m), np.zeros(count), offsets], axis=1)
        return cls(tx_positions, rx_positions)

    @property
    def pair_count(self) -> int:
        return len(self.tx_positions_m) * len(self.rx_positions_m)

    def pair_positions_m(self) -> tuple[np.ndarray, np.ndarray]:
        """(tx position per pair, rx position per pair), each (pair_count, 3)."""
        tx_index, rx_index = np.meshgrid(
            np.arange(len(self.tx_positions_m)), np.arange(len(self.rx_positions_m)), indexing="ij"
        )
        return self.tx_positions_m[tx_index.ravel()], self.rx_positions_m[rx_index.ravel()]

    def pair_label(self, pair_index: int) -> str:
        tx_index, rx_index = divmod(pair_index, len(self.rx_positions_m))
        return f"TX{tx_index + 1}-RX{rx_index + 1}"

    def to_dict(self) -> dict:
        return {"tx_positions_m": self.tx_positions_m.tolist(), "rx_positions_m": self.rx_positions_m.tolist()}
# --- END [2.2] ---


# ==============================================================================
# [3.1] rf_frame
# ==============================================================================
@dataclass
class RfFrame:
    """One sweep: complex response for every pair at every frequency."""

    timestamp_s: float
    response: np.ndarray                        # complex64 (pair_count, bin_count)
# --- END [3.1] ---

# ==============================================================================
# [3.2] rf_capture
# ==============================================================================
@dataclass
class RfCapture:
    """A recorded test: settings, antenna layout, calibration data and every raw frame.
    Raw frames are kept (not images) so a capture can be re-processed with better maths later."""

    config: SweepConfig
    array: AntennaArray
    metadata: dict = field(default_factory=dict)
    max_frame_count: int = 2400                 # ~2 minutes at 20 fps; oldest frames drop after
    timestamps_s: list[float] = field(default_factory=list)
    responses: list[np.ndarray] = field(default_factory=list)
    calibration_empty: np.ndarray | None = None
    calibration_reference: np.ndarray | None = None
    calibration_position_m: np.ndarray | None = None

    FORMAT_VERSION = 1

    def append(self, frame: RfFrame) -> None:
        self.timestamps_s.append(frame.timestamp_s)
        self.responses.append(frame.response)
        if len(self.responses) > self.max_frame_count:
            del self.timestamps_s[0], self.responses[0]

    def clear(self) -> None:
        self.timestamps_s.clear()
        self.responses.clear()

    @property
    def frame_count(self) -> int:
        return len(self.responses)

    def frame(self, frame_index: int) -> RfFrame:
        return RfFrame(self.timestamps_s[frame_index], self.responses[frame_index])

    def save(self, path: str | Path) -> Path:
        """Write the capture to a .npz file (raw frames, calibration data, settings as JSON)."""
        path = Path(path).with_suffix(".npz")
        header = {
            "format_version": self.FORMAT_VERSION,
            "config": self.config.to_dict(),
            "array": self.array.to_dict(),
            "metadata": self.metadata,
        }
        arrays = {
            "header_json": np.array(json.dumps(header)),
            "timestamps_s": np.asarray(self.timestamps_s, dtype=np.float64),
            "responses": np.asarray(self.responses, dtype=np.complex64).reshape(
                (self.frame_count, self.array.pair_count, self.config.bin_count)
            ),
        }
        if self.calibration_reference is not None:
            arrays["calibration_empty"] = self.calibration_empty
            arrays["calibration_reference"] = self.calibration_reference
            arrays["calibration_position_m"] = self.calibration_position_m
        np.savez_compressed(path, **arrays)
        return path

    @classmethod
    def load(cls, path: str | Path) -> RfCapture:
        with np.load(Path(path), allow_pickle=False) as archive:
            header = json.loads(str(archive["header_json"]))
            if header.get("format_version") != cls.FORMAT_VERSION:
                raise ValueError(f"Unsupported capture format {header.get('format_version')}")
            capture = cls(
                SweepConfig(**header["config"]),
                AntennaArray(
                    np.asarray(header["array"]["tx_positions_m"]), np.asarray(header["array"]["rx_positions_m"])
                ),
                metadata=header.get("metadata", {}),
            )
            capture.timestamps_s = archive["timestamps_s"].tolist()
            capture.responses = list(archive["responses"])
            capture.max_frame_count = max(capture.max_frame_count, capture.frame_count)
            if "calibration_reference" in archive:
                capture.calibration_empty = archive["calibration_empty"]
                capture.calibration_reference = archive["calibration_reference"]
                capture.calibration_position_m = archive["calibration_position_m"]
        return capture
# --- END [3.2] ---


# ==============================================================================
# [4.1] scatterer
# ==============================================================================
@dataclass
class Scatterer:
    position_m: tuple[float, float, float]
    material: Material
# --- END [4.1] ---

# ==============================================================================
# [4.2] simulate_response
# ==============================================================================
def simulate_response(
    positions_m: np.ndarray, reflectivity: np.ndarray, config: SweepConfig, array: AntennaArray
) -> np.ndarray:
    """Radar forward model (point scatterers, single bounce, far-field amplitude):
        H[p, f] = sum_s  rho[s, f] * exp(-j 2 pi f (d_tx + d_rx) / c) / (d_tx * d_rx)
    positions_m: (S, 3); reflectivity: (S, F) complex (or (S,) for frequency-flat).
    Returns (pair_count, bin_count) complex. The imaging maths inverts exactly this model."""
    positions_m = np.atleast_2d(np.asarray(positions_m, dtype=float))
    reflectivity = np.asarray(reflectivity)
    frequencies_hz = config.frequencies_hz()
    if reflectivity.ndim == 1:
        reflectivity = np.repeat(reflectivity[:, None], len(frequencies_hz), axis=1)
    tx_positions, rx_positions = array.pair_positions_m()
    tx_distance = np.linalg.norm(positions_m[None, :, :] - tx_positions[:, None, :], axis=2)   # (P, S)
    rx_distance = np.linalg.norm(positions_m[None, :, :] - rx_positions[:, None, :], axis=2)
    path_m = tx_distance + rx_distance
    spreading = 1.0 / (tx_distance * rx_distance)
    phase = np.exp(-2j * np.pi * path_m[:, :, None] * frequencies_hz[None, None, :] / SPEED_OF_LIGHT_M_S)
    return np.einsum("ps,psf,sf->pf", spreading, phase, reflectivity)
# --- END [4.2] ---


# ==============================================================================
# [5.1] rf_sensor_interface
# ==============================================================================
class RfSensor(Protocol):
    """Anything that produces RfFrames: the mock, a capture replay, later real hardware drivers."""

    config: SweepConfig
    array: AntennaArray
    description: str

    def read_frame(self) -> RfFrame | None:
        """Next sweep, or None if no new data is ready."""

    def calibration_measurements(self) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """(empty-scene response, metal-reference response, reference position) or None if unavailable.
        Real procedure: sweep with nothing in front of the array, then with a metal sphere/plate at a
        known spot."""
# --- END [5.1] ---

# ==============================================================================
# [5.2] default_test_scene
# ==============================================================================
def default_test_scene() -> list[Scatterer]:
    """Static room contents in front of the array (points on a 10 cm lattice)."""
    scatterers: list[Scatterer] = []

    def add_grid(material_name: str, xs, ys, zs) -> None:
        for x in np.atleast_1d(xs):
            for y in np.atleast_1d(ys):
                for z in np.atleast_1d(zs):
                    scatterers.append(Scatterer((float(x), float(y), float(z)), MATERIAL_BY_NAME[material_name]))

    lattice = lambda start, stop: np.round(np.arange(start, stop + 1e-9, 0.2), 2)   # noqa: E731
    add_grid("Plasterboard", lattice(-0.8, 0.8), 1.9, lattice(-0.6, 0.6))   # back wall
    add_grid("Metal", 0.6, 1.3, np.round(np.arange(-0.6, 0.61, 0.1), 2))    # vertical pipe
    add_grid("Concrete", (-0.7, -0.6, -0.5), 1.2, (-0.5, -0.4, -0.3))         # block, front face
    add_grid("Wood", np.round(np.arange(-0.4, 0.01, 0.1), 2), 1.5, -0.6)    # shelf
    add_grid("Glass", 0.3, 1.6, (0.3, 0.4, 0.5))                              # small pane
    add_grid("Water", -0.2, 1.0, 0.5)                                         # bottle
    return scatterers
# --- END [5.2] ---

# ==============================================================================
# [5.3] mock_rf_sensor
# ==============================================================================
class MockRfSensor:
    """Simulated MIMO sensor in a room with a person walking a figure-eight and breathing."""

    PERSON_PERIOD_S = 14.0
    BREATH_PERIOD_S = 4.0
    BREATH_DEPTH_M = 0.006
    CALIBRATION_POSITION_M = (0.0, 0.9, 0.0)

    def __init__(
        self, config: SweepConfig | None = None, array: AntennaArray | None = None,
        scene: list[Scatterer] | None = None, noise_level: float = 0.004, frame_interval_s: float = 0.05,
        seed: int = 7,
    ) -> None:
        self.config = config or SweepConfig()
        self.array = array or AntennaArray.l_shaped()
        self.scene = scene if scene is not None else default_test_scene()
        self.noise_level = noise_level
        self.frame_interval_s = frame_interval_s
        self.description = (
            f"Mock sensor · {self.config.start_hz / 1e9:.1f}-{self.config.stop_hz / 1e9:.1f} GHz · "
            f"{self.array.pair_count} pairs × {self.config.bin_count} bins"
        )
        self._rng = np.random.default_rng(seed)
        self._clock_s = 0.0
        frequencies_hz = self.config.frequencies_hz()

        # Unknown hardware: per-channel gain, phase offset and cable delay (calibration removes it).
        pair_count = self.array.pair_count
        gain = self._rng.uniform(0.6, 1.4, (pair_count, 1))
        phase_offset = self._rng.uniform(-np.pi, np.pi, (pair_count, 1))
        cable_delay_s = self._rng.uniform(0.5e-9, 2.0e-9, (pair_count, 1))
        self.system_response = gain * np.exp(1j * (phase_offset - 2 * np.pi * frequencies_hz[None, :] * cable_delay_s))

        # Direct TX->RX leakage (strong, constant).
        tx_positions, rx_positions = self.array.pair_positions_m()
        baseline_m = np.linalg.norm(tx_positions - rx_positions, axis=1)
        self.leakage = (0.3 / baseline_m)[:, None] * np.exp(
            -2j * np.pi * baseline_m[:, None] * frequencies_hz[None, :] / SPEED_OF_LIGHT_M_S
        )

        # Static scene response is the same every frame: compute once.
        self._static_positions = np.array([s.position_m for s in self.scene])
        self._static_reflectivity = np.array([s.material.reflection_coefficient(frequencies_hz) for s in self.scene])
        self._static_response = simulate_response(
            self._static_positions, self._static_reflectivity, self.config, self.array
        )
        self._tissue_reflectivity = MATERIAL_BY_NAME["Human tissue"].reflection_coefficient(frequencies_hz)

    def person_positions_m(self, time_s: float) -> np.ndarray:
        """Front surface of the torso (3 x 4 points) walking a figure-eight, chest moving with breath."""
        phase = 2 * np.pi * time_s / self.PERSON_PERIOD_S
        center_x = 0.55 * np.sin(phase)
        center_y = 0.95 + 0.25 * np.sin(2 * phase)
        center_y -= self.BREATH_DEPTH_M * np.sin(2 * np.pi * time_s / self.BREATH_PERIOD_S)
        xs = center_x + np.array([-0.12, 0.0, 0.12])
        zs = np.array([-0.15, 0.0, 0.15, 0.3])
        grid_x, grid_z = np.meshgrid(xs, zs, indexing="ij")
        return np.stack([grid_x.ravel(), np.full(grid_x.size, center_y), grid_z.ravel()], axis=1)

    def person_center_m(self, time_s: float) -> np.ndarray:
        return self.person_positions_m(time_s).mean(axis=0)

    def read_frame(self) -> RfFrame:
        person_positions = self.person_positions_m(self._clock_s)
        person_reflectivity = np.repeat(self._tissue_reflectivity[None, :], len(person_positions), axis=0)
        scene_response = self._static_response + simulate_response(
            person_positions, person_reflectivity, self.config, self.array
        )
        frame = RfFrame(self._clock_s, self._measure(scene_response))
        self._clock_s += self.frame_interval_s
        return frame

    def calibration_measurements(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Empty-room and metal-reference sweeps, as taken before a test."""
        reference_position = np.array(self.CALIBRATION_POSITION_M)
        reference_response = simulate_response(reference_position[None, :], np.array([-1.0 + 0j]), self.config, self.array)
        empty_scene = np.zeros_like(reference_response)
        return self._measure(empty_scene), self._measure(reference_response), reference_position

    def _measure(self, scene_response: np.ndarray) -> np.ndarray:
        noise = self._rng.normal(0, self.noise_level, scene_response.shape) + 1j * self._rng.normal(
            0, self.noise_level, scene_response.shape
        )
        return ((scene_response + self.leakage) * self.system_response + noise).astype(np.complex64)
# --- END [5.3] ---

# ==============================================================================
# [5.4] capture_replay_sensor
# ==============================================================================
class CaptureReplaySensor:
    """Plays a saved RfCapture back frame by frame (loops at the end)."""

    def __init__(self, capture: RfCapture, source_name: str = "capture") -> None:
        if capture.frame_count == 0:
            raise ValueError("Capture has no frames")
        self.capture = capture
        self.config = capture.config
        self.array = capture.array
        self.description = f"Replay · {source_name} · {capture.frame_count} frames"
        self._frame_index = 0

    def read_frame(self) -> RfFrame:
        frame = self.capture.frame(self._frame_index)
        self._frame_index = (self._frame_index + 1) % self.capture.frame_count
        return frame

    def calibration_measurements(self) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        if self.capture.calibration_reference is None:
            return None
        return self.capture.calibration_empty, self.capture.calibration_reference, self.capture.calibration_position_m
# --- END [5.4] ---
