# ==============================================================================
# FILE: app/modules/rf_imaging/imaging.py
# OVERVIEW: The maths that turns raw RF sweeps into 3D images, motion, tracks and material estimates.
# PURPOSE:
#   1. Calibration: removes each channel's unknown hardware response and the TX->RX leakage, using an
#      empty-scene sweep and a metal reference at a known spot.
#   2. Backprojector: delay-and-sum 3D imaging. Inverts the forward model in sensor.py, scaled so
#      a point with reflection coefficient rho images as |rho| (calibrated reflectivity).
#      Images each sub-band separately too, which gives a reflectivity spectrum per voxel.
#   3. ClutterFilter: running average of the calibrated sweeps (= the static room); subtracting it
#      leaves only what changed (motion).
#   4. SparseReflectorJob: orthogonal matching pursuit -> sparse reflectors with sidelobe-free spectra;
#      classify_materials() matches those spectra against the material library.
#   5. TargetTracker: follows the strongest moving reflector with an alpha-beta filter.
#   6. range_profiles(): inverse FFT over frequency -> echo strength vs path length (range-time view).
#   7. RfImagingPipeline: runs all of the above per frame and keeps rolling histories (Tensor4D).
# CONTRACT:
#   - Requires: numpy, app.data.tensor4d.Tensor4D, app.modules.rf_imaging.sensor (SweepConfig, AntennaArray,
#               RfFrame, simulate_response), app.data.em_materials (MATERIALS, NO_MATERIAL_ID, c)
#   - Exposes: VoxelGrid, Calibration, Backprojector, SparseReflectorJob, ClutterFilter, TargetTracker, FrameResult,
#              RfImagingPipeline, classify_materials(), range_profiles()
# NOTES:
#   - [2026-11-15] Created. Image formation is matched-filter backprojection:
#                      I(v) = 1/(P F) * sum_{p,f} H_cal[p,f] * d_tx * d_rx * exp(+j 2 pi f (d_tx + d_rx) / c)
#                  It is a precomputed matrix product (steering matrix x sweep), so a frame costs a few ms.
#   - [2026-11-15] Material estimates assume calibrated data and roughly normal incidence; they are
#                  "best match + confidence", not ground truth.
#   - [2026-11-15] Plain CLEAN (one reflector at a time) was replaced by orthogonal matching pursuit
#                  with joint least-squares refits: on noiseless test data CLEAN got 11/94 scene points
#                  right because neighbouring reflectors' sub-band echoes overlap.
#   - [2026-11-15] Health pass: removed unused SparseReflectorJob.is_done.
#   - [2026-11-15] Static materials come from a SparseReflectorJob on the clutter background spread over frames
#                  (bounded cost per frame); the moving target gets its own small CLEAN near the track.
#   - [2026-11-15] Materials come from sparse reflectors, not raw voxels: with only 25 channels the
#                  sidelobes of strong reflectors (metal) swamp weak ones (wood, plasterboard), so raw
#                  per-voxel classification labelled almost the whole volume.
#   - [2026-11-15] TargetTracker measures the motion centroid near the peak only (blob_radius_m).
#                  Gains tuned on the mock walk (alpha 0.25, beta 0.03): ~15 cm median error, ~5 cm
#                  frame-to-frame jitter; the remaining error is the sparse array's measurement noise.
#   - [2026-11-15] Steering matrices cost voxel_count x pair_count x bin_count complex64
#                  (~77 MB for the default 21x17x17 grid, 25 pairs, 64 bins). Build once per setup.
#   - [2026-11-15] Moved from app/data/rf/imaging.py (module apps live in app/modules/<name>/).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - alpha-beta filter: Simple tracker: predict position from velocity, then correct both by fixed
#   fractions (alpha, beta) of the measurement error.
# - backprojection: For each voxel, undo the phase delay each channel would see from that voxel and
#   add everything up; real reflectors add up in phase, everything else cancels.
# - band_count (int): Number of equal sub-bands the sweep is split into for spectra (default: 4).
# - clutter: Echoes from things that don't move (walls, furniture); averaged out to reveal motion.
# - reflectivity (|rho|): Calibrated image value; ~|Gamma| of the material at that voxel (0-1).
# - steering matrix: Precomputed exp(+j k path) * spreading for every voxel x (pair, frequency).
# - voxel: One cell of the 3D image grid (default 10 cm).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. GRID_AND_CALIBRATION
#    2.1 voxel_grid
#    2.2 calibration
# 3. IMAGING
#    3.1 backprojector (+ SparseReflectorJob)
#    3.2 clutter_filter
#    3.3 classify_materials
#    3.4 target_tracker
#    3.5 range_profiles
# 4. PIPELINE
#    4.1 frame_result
#    4.2 rf_imaging_pipeline
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.data.em_materials import MATERIALS, NO_MATERIAL_ID, SPEED_OF_LIGHT_M_S
from app.modules.rf_imaging.sensor import AntennaArray, RfFrame, SweepConfig, simulate_response
from app.data.tensor4d import Tensor4D
# --- END [1.1] ---


# ==============================================================================
# [2.1] voxel_grid
# ==============================================================================
@dataclass(frozen=True)
class VoxelGrid:
    """Regular 3D grid of voxel centres (metres). Default: the space in front of the array."""

    x_range_m: tuple[float, float] = (-1.0, 1.0)
    y_range_m: tuple[float, float] = (0.5, 2.1)
    z_range_m: tuple[float, float] = (-0.8, 0.8)
    spacing_m: float = 0.1

    def axis_m(self, axis_index: int) -> np.ndarray:
        start, stop = (self.x_range_m, self.y_range_m, self.z_range_m)[axis_index]
        return np.round(np.arange(start, stop + self.spacing_m / 2, self.spacing_m), 6)

    @property
    def shape(self) -> tuple[int, int, int]:
        return tuple(len(self.axis_m(axis_index)) for axis_index in range(3))

    @property
    def voxel_count(self) -> int:
        return int(np.prod(self.shape))

    def centers_m(self) -> np.ndarray:
        """(voxel_count, 3) voxel centres in C order (x slowest, z fastest) - matches reshape(shape)."""
        grid_x, grid_y, grid_z = np.meshgrid(self.axis_m(0), self.axis_m(1), self.axis_m(2), indexing="ij")
        return np.stack([grid_x.ravel(), grid_y.ravel(), grid_z.ravel()], axis=1)

    def index_to_m(self, voxel_index: tuple[int, int, int]) -> np.ndarray:
        return np.array([self.axis_m(axis)[voxel_index[axis]] for axis in range(3)])

    def m_to_index(self, position_m: np.ndarray) -> tuple[int, int, int]:
        indices = []
        for axis in range(3):
            axis_values = self.axis_m(axis)
            indices.append(int(np.clip(np.argmin(np.abs(axis_values - position_m[axis])), 0, len(axis_values) - 1)))
        return tuple(indices)
# --- END [2.1] ---

# ==============================================================================
# [2.2] calibration
# ==============================================================================
@dataclass
class Calibration:
    """Per-channel correction: H_cal = (H - empty) / system_response."""

    empty_response: np.ndarray                  # (P, F) leakage + anything present with no target
    system_response: np.ndarray                 # (P, F) gain/phase/delay per channel and frequency

    @classmethod
    def identity(cls, pair_count: int, bin_count: int) -> Calibration:
        return cls(np.zeros((pair_count, bin_count), complex), np.ones((pair_count, bin_count), complex))

    @classmethod
    def from_reference(
        cls, empty_response: np.ndarray, reference_response: np.ndarray, reference_position_m: np.ndarray,
        config: SweepConfig, array: AntennaArray,
    ) -> Calibration:
        """A metal reflector (Gamma = -1) at a known spot: what we measured / what the model predicts
        = this channel's system response."""
        model_response = simulate_response(np.asarray(reference_position_m)[None, :], np.array([-1.0 + 0j]), config, array)
        system_response = (reference_response - empty_response) / model_response
        return cls(np.asarray(empty_response, complex), system_response)

    def apply(self, response: np.ndarray) -> np.ndarray:
        return ((response - self.empty_response) / self.system_response).astype(np.complex64)
# --- END [2.2] ---


# ==============================================================================
# [3.1] backprojector
# ==============================================================================
class Backprojector:
    """Delay-and-sum imaging onto a VoxelGrid, per sub-band. image(sweep) -> (band_count, voxel_count)."""

    def __init__(self, config: SweepConfig, array: AntennaArray, grid: VoxelGrid, band_count: int = 4) -> None:
        if config.bin_count % band_count:
            raise ValueError(f"bin_count {config.bin_count} must split evenly into {band_count} bands")
        self.config, self.array, self.grid, self.band_count = config, array, grid, band_count
        frequencies_hz = config.frequencies_hz()
        self.band_bin_count = config.bin_count // band_count
        self.band_frequencies_hz = frequencies_hz.reshape(band_count, self.band_bin_count)
        self.band_centers_hz = self.band_frequencies_hz.mean(axis=1)

        centers = grid.centers_m()
        tx_positions, rx_positions = array.pair_positions_m()
        tx_distance = np.linalg.norm(centers[:, None, :] - tx_positions[None, :, :], axis=2)   # (V, P)
        rx_distance = np.linalg.norm(centers[:, None, :] - rx_positions[None, :, :], axis=2)
        path_m = tx_distance + rx_distance
        compensation = tx_distance * rx_distance                       # undo 1/(d_tx d_rx) spreading
        self.path_m = path_m.astype(np.float32)                        # kept for CLEAN's point model
        self.spreading = (1.0 / compensation).astype(np.float32)
        normalisation = 1.0 / (array.pair_count * self.band_bin_count)

        # One steering matrix per band: (V, P * band_bins), matching sweep[:, band].ravel()
        self.steering_matrices = []
        for band_frequencies in self.band_frequencies_hz:
            phase = np.exp(2j * np.pi * path_m[:, :, None] * band_frequencies[None, None, :] / SPEED_OF_LIGHT_M_S)
            steering = (compensation[:, :, None] * phase * normalisation).astype(np.complex64)
            self.steering_matrices.append(steering.reshape(grid.voxel_count, -1))

    def image_bands(self, calibrated_sweep: np.ndarray) -> np.ndarray:
        """Complex sub-band images (band_count, voxel_count)."""
        bands = calibrated_sweep.reshape(self.array.pair_count, self.band_count, self.band_bin_count)
        return np.stack([
            steering @ bands[:, band_index, :].ravel()
            for band_index, steering in enumerate(self.steering_matrices)
        ])

    @staticmethod
    def combine(band_images: np.ndarray) -> np.ndarray:
        """Full-band image = mean of the sub-band images (they have equal bin counts)."""
        return band_images.mean(axis=0)

    def point_model(self, voxel_index: int) -> np.ndarray:
        """Forward-model sweep of a unit (rho = 1) point at a voxel, split into bands: (P, band_count, band_bins)."""
        frequencies_hz = self.band_frequencies_hz.ravel()
        phase = np.exp(-2j * np.pi * self.path_m[voxel_index][:, None] * frequencies_hz[None, :] / SPEED_OF_LIGHT_M_S)
        model = self.spreading[voxel_index][:, None] * phase
        return model.reshape(self.array.pair_count, self.band_count, self.band_bin_count)

    def select_candidates(self, full_image: np.ndarray, min_reflectivity: float, max_candidates: int,
                          near_position_m: np.ndarray | None = None, radius_m: float = 0.0) -> np.ndarray:
        """Voxels CLEAN may pick: strongest voxels above half min_reflectivity (optionally only near a point)."""
        magnitude = np.abs(full_image)
        is_candidate = magnitude >= 0.5 * min_reflectivity
        if near_position_m is not None:
            is_candidate &= np.linalg.norm(self.grid.centers_m() - near_position_m, axis=1) <= radius_m
        candidates = np.flatnonzero(is_candidate)
        return candidates[np.argsort(magnitude[candidates])[::-1][:max_candidates]]


class SparseReflectorJob:
    """Finds the few points that explain the data (orthogonal matching pursuit), resumable across frames.

    Each step: image the residual at the candidate voxels and add the strongest to the support.
    Every refit_every picks, all support strengths are re-solved together by least squares and the
    residual is recomputed (this is what plain CLEAN lacks: neighbouring reflectors' echoes overlap,
    so estimating them one at a time compounds errors). When done, result() fits each sub-band
    separately on the final support, giving every reflector a clean reflectivity spectrum.
    """

    def __init__(self, backprojector: Backprojector, calibrated_sweep: np.ndarray, candidates: np.ndarray,
                 min_reflectivity: float, max_reflectors: int = 140, refit_every: int = 8) -> None:
        self.backprojector, self.candidates = backprojector, candidates
        self.min_reflectivity, self.max_reflectors, self.refit_every = min_reflectivity, max_reflectors, refit_every
        self.data = calibrated_sweep.astype(np.complex64).ravel()                    # (P * F,) band-major per pair
        self.residual = self.data.copy()
        self.full_steering = np.concatenate(
            [steering[candidates].reshape(len(candidates), backprojector.array.pair_count, -1)
             for steering in backprojector.steering_matrices], axis=2,
        ).reshape(len(candidates), -1) / backprojector.band_count                   # full-band image at candidates
        self.support: list[int] = []                                                # positions in candidates
        self._atoms: list[np.ndarray] = []
        self._is_done = len(candidates) == 0

    def step(self, pick_count: int) -> bool:
        """Add up to pick_count reflectors; returns True when finished."""
        for _ in range(pick_count):
            if self._is_done:
                break
            magnitude = np.abs(self.full_steering @ self.residual)
            magnitude[self.support] = 0.0
            best = int(magnitude.argmax())
            if magnitude[best] < self.min_reflectivity or len(self.support) >= self.max_reflectors:
                self._refit()
                self._is_done = True
                break
            self.support.append(best)
            self._atoms.append(self.backprojector.point_model(int(self.candidates[best])).ravel().astype(np.complex64))
            if len(self.support) % self.refit_every == 0:
                self._refit()
            else:   # cheap interim update for the newest atom only
                atom = self._atoms[-1]
                self.residual -= atom * (np.vdot(atom, self.residual) / np.vdot(atom, atom))
        return self._is_done

    def _refit(self) -> None:
        if not self.support:
            return
        atom_matrix = np.stack(self._atoms, axis=1)                                 # (P * F, R)
        strengths, *_ = np.linalg.lstsq(atom_matrix, self.data, rcond=None)
        self.residual = self.data - atom_matrix @ strengths

    def result(self) -> tuple[np.ndarray, np.ndarray]:
        """(voxel indices (R,), per-band complex reflectivity (R, band_count)) from a per-band joint fit."""
        if not self.support:
            return np.empty(0, int), np.empty((0, self.backprojector.band_count), complex)
        backprojector = self.backprojector
        shape = (backprojector.array.pair_count, backprojector.band_count, backprojector.band_bin_count)
        data_bands = self.data.reshape(shape)
        atom_bands = np.stack(self._atoms, axis=1).reshape(*shape, len(self.support))
        band_rho = np.stack([
            np.linalg.lstsq(atom_bands[:, band_index].reshape(-1, len(self.support)),
                            data_bands[:, band_index].ravel(), rcond=None)[0]
            for band_index in range(backprojector.band_count)
        ], axis=1)                                                                  # (R, band_count)
        return self.candidates[self.support], band_rho
# --- END [3.1] ---

# ==============================================================================
# [3.2] clutter_filter
# ==============================================================================
class ClutterFilter:
    """Exponential running average of calibrated sweeps = static background; returns what changed."""

    def __init__(self, smoothing: float = 0.03) -> None:
        self.smoothing = smoothing
        self.background: np.ndarray | None = None

    def update(self, calibrated_sweep: np.ndarray) -> np.ndarray:
        if self.background is None:
            self.background = calibrated_sweep.astype(np.complex64).copy()
        moving_part = calibrated_sweep - self.background
        self.background += self.smoothing * moving_part
        return moving_part

    def reset(self) -> None:
        self.background = None
# --- END [3.2] ---

# ==============================================================================
# [3.3] classify_materials
# ==============================================================================
def material_band_signatures(band_centers_hz: np.ndarray) -> np.ndarray:
    """|Gamma| of every library material at each band centre: (material_count, band_count)."""
    return np.abs(np.array([material.reflection_coefficient(band_centers_hz) for material in MATERIALS]))


def classify_materials(
    band_magnitudes: np.ndarray, signatures: np.ndarray, min_reflectivity: float
) -> tuple[np.ndarray, np.ndarray]:
    """Nearest material by least squares on sub-band reflectivity.
    band_magnitudes: (band_count, N). Returns (material id per item, confidence 0-1 per item);
    items whose mean reflectivity < min_reflectivity get NO_MATERIAL_ID and confidence 0.
    Confidence = how much better the best match is than the runner-up (1 - best_error / second_error)."""
    errors = ((band_magnitudes.T[:, None, :] - signatures[None, :, :]) ** 2).sum(axis=2)    # (N, M)
    order = np.argsort(errors, axis=1)
    best_error = np.take_along_axis(errors, order[:, :1], axis=1)[:, 0]
    second_error = np.take_along_axis(errors, order[:, 1:2], axis=1)[:, 0]
    material_ids = order[:, 0].astype(np.int8)
    confidence = 1.0 - best_error / np.maximum(second_error, 1e-12)
    too_weak = band_magnitudes.mean(axis=0) < min_reflectivity
    material_ids[too_weak] = NO_MATERIAL_ID
    confidence[too_weak] = 0.0
    return material_ids, confidence.astype(np.float32)
# --- END [3.3] ---

# ==============================================================================
# [3.4] target_tracker
# ==============================================================================
class TargetTracker:
    """Alpha-beta tracker on the motion image's strongest blob."""

    def __init__(self, grid: VoxelGrid, alpha: float = 0.25, beta: float = 0.03, min_motion: float = 0.02,
                 lost_after_frames: int = 10, blob_radius_m: float = 0.3) -> None:
        self.grid, self.alpha, self.beta = grid, alpha, beta
        self.min_motion, self.lost_after_frames = min_motion, lost_after_frames
        self.blob_radius_m = blob_radius_m
        self.centers = grid.centers_m()
        self.position_m: np.ndarray | None = None
        self.velocity_m_s = np.zeros(3)
        self._missed_frames = 0
        self._last_time_s: float | None = None

    def update(self, motion_magnitude: np.ndarray, time_s: float) -> np.ndarray | None:
        """motion_magnitude: (voxel_count,). Returns the filtered position or None if no target."""
        dt = 0.05 if self._last_time_s is None else max(time_s - self._last_time_s, 1e-3)
        self._last_time_s = time_s
        peak = float(motion_magnitude.max())
        measurement = None
        if peak >= self.min_motion:
            # Centroid of strong motion near the peak only: the background average leaves a faint
            # "ghost" where the target used to be, which a global centroid would drift towards.
            peak_position = self.centers[int(motion_magnitude.argmax())]
            is_near_peak = np.linalg.norm(self.centers - peak_position, axis=1) <= self.blob_radius_m
            weights = np.where(is_near_peak & (motion_magnitude >= 0.5 * peak), motion_magnitude, 0.0)
            measurement = (weights[:, None] * self.centers).sum(axis=0) / weights.sum()

        if self.position_m is None:
            if measurement is not None:
                self.position_m, self.velocity_m_s, self._missed_frames = measurement, np.zeros(3), 0
            return self.position_m

        predicted = self.position_m + self.velocity_m_s * dt
        if measurement is None:
            self._missed_frames += 1
            if self._missed_frames > self.lost_after_frames:
                self.position_m = None
                return None
            self.position_m = predicted
            return predicted
        self._missed_frames = 0
        residual = measurement - predicted
        self.position_m = predicted + self.alpha * residual
        self.velocity_m_s = self.velocity_m_s + (self.beta / dt) * residual
        return self.position_m

    def reset(self) -> None:
        self.position_m, self.velocity_m_s, self._last_time_s, self._missed_frames = None, np.zeros(3), None, 0
# --- END [3.4] ---

# ==============================================================================
# [3.5] range_profiles
# ==============================================================================
def range_profiles(sweeps: np.ndarray, config: SweepConfig, padded_length: int = 256) -> tuple[np.ndarray, np.ndarray]:
    """Echo strength vs path length: Hann-windowed inverse FFT over frequency.
    sweeps: (..., bin_count) complex. Returns (magnitudes (..., padded_length), path axis in metres),
    covering path lengths 0 .. unambiguous_path (longer paths wrap around)."""
    window = np.hanning(config.bin_count).astype(np.float32)
    profiles = np.abs(np.fft.ifft(sweeps * window, n=padded_length, axis=-1))
    path_axis_m = np.arange(padded_length) * SPEED_OF_LIGHT_M_S / (padded_length * config.step_hz)
    return profiles, path_axis_m
# --- END [3.5] ---


# ==============================================================================
# [4.1] frame_result
# ==============================================================================
@dataclass
class FrameResult:
    timestamp_s: float
    reflectivity: np.ndarray                    # (nx, ny, nz) |rho|
    motion: np.ndarray                          # (nx, ny, nz) |rho| of the changed part
    material_ids: np.ndarray                    # (nx, ny, nz) int8: latest static material map
    track_position_m: np.ndarray | None
    target_material_id: int                     # tracked target's best material, or NO_MATERIAL_ID
    target_confidence: float
# --- END [4.1] ---

# ==============================================================================
# [4.2] rf_imaging_pipeline
# ==============================================================================
class RfImagingPipeline:
    """Calibrate -> image (per band) -> clutter/motion -> track -> materials, with rolling histories.

    Materials come in two parts:
      - static map: a SparseReflectorJob on the clutter background (the still room), spread over frames
        (clean_iterations_per_frame each) and restarted when done -> material_ids / confidence /
        band_reflectivity for every static reflector;
      - target: a small sparse fit each frame on the moving part, only near the tracked position.

    Histories (oldest first, newest at index -1), each history_length long:
        reflectivity, motion   Tensor4D (t, x, y, z) float32
        materials              Tensor4D (t, x, y, z) int8 (static map as it stood at each frame)
        sweeps                 (t, pair, bin) calibrated complex sweeps (range-time / spectrum views)
        track_positions_m      (t, 3) NaN where no target
        target_material_ids    (t,) int8;  timestamps_s (t,)
    Latest only: material_confidence (x, y, z), band_reflectivity (band_count, x, y, z).
    """

    STATIC_MAX_CANDIDATES = 2500
    STATIC_MAX_REFLECTORS = 160
    STATIC_REFRESH_FRAMES = 40                   # idle frames between static passes (the room rarely changes)
    TARGET_RADIUS_M = 0.35
    TARGET_REFLECTORS = 8

    def __init__(
        self, config: SweepConfig, array: AntennaArray, grid: VoxelGrid | None = None,
        history_length: int = 96, band_count: int = 4, min_material_reflectivity: float = 0.1,
        clean_iterations_per_frame: int = 6,
    ) -> None:
        self.config, self.array = config, array
        self.grid = grid or VoxelGrid()
        self.history_length = history_length
        self.min_material_reflectivity = min_material_reflectivity
        self.clean_iterations_per_frame = clean_iterations_per_frame
        self.calibration = Calibration.identity(array.pair_count, config.bin_count)
        self.backprojector = Backprojector(config, array, self.grid, band_count)
        self.clutter_filter = ClutterFilter()
        self.tracker = TargetTracker(self.grid)
        self.material_signatures = material_band_signatures(self.backprojector.band_centers_hz)
        self.reset_history()

    def reset_history(self) -> None:
        volume_shape = (self.history_length, *self.grid.shape)
        self.reflectivity = Tensor4D(np.zeros(volume_shape, np.float32))
        self.motion = Tensor4D(np.zeros(volume_shape, np.float32))
        self.materials = Tensor4D(np.full(volume_shape, NO_MATERIAL_ID, np.int8))
        self.static_material_ids = np.full(self.grid.shape, NO_MATERIAL_ID, np.int8)
        self.material_confidence = np.zeros(self.grid.shape, np.float32)
        self.band_reflectivity = np.zeros((self.backprojector.band_count, *self.grid.shape), np.float32)
        self.sweeps = np.zeros((self.history_length, self.array.pair_count, self.config.bin_count), np.complex64)
        self.track_positions_m = np.full((self.history_length, 3), np.nan)
        self.target_material_ids = np.full(self.history_length, NO_MATERIAL_ID, np.int8)
        self.timestamps_s = np.full(self.history_length, np.nan)
        self.frame_count = 0
        self.static_map_count = 0                    # finished static CLEAN passes
        self._static_job: SparseReflectorJob | None = None
        self._static_idle_frames = 0
        self.clutter_filter.reset()
        self.tracker.reset()

    def set_calibration(self, calibration: Calibration) -> None:
        self.calibration = calibration
        self.reset_history()

    @property
    def filled_count(self) -> int:
        """How many history slots hold real frames (the rest are zeros at the old end)."""
        return min(self.frame_count, self.history_length)

    def process(self, frame: RfFrame) -> FrameResult:
        calibrated = self.calibration.apply(frame.response)
        reflectivity = np.abs(Backprojector.combine(self.backprojector.image_bands(calibrated)))

        moving_part = self.clutter_filter.update(calibrated)
        motion_image = Backprojector.combine(self.backprojector.image_bands(moving_part))
        motion = np.abs(motion_image)
        track_position = self.tracker.update(motion, frame.timestamp_s)

        self._advance_static_materials()
        target_material_id, target_confidence = self._classify_target(moving_part, motion_image, track_position)

        shape = self.grid.shape
        result = FrameResult(
            frame.timestamp_s, reflectivity.reshape(shape).astype(np.float32), motion.reshape(shape).astype(np.float32),
            self.static_material_ids.copy(), track_position, target_material_id, target_confidence,
        )
        self.reflectivity.push(result.reflectivity)
        self.motion.push(result.motion)
        self.materials.push(result.material_ids)
        self._push_row(self.sweeps, calibrated)
        self._push_row(self.track_positions_m, np.full(3, np.nan) if track_position is None else track_position)
        self._push_row(self.target_material_ids, target_material_id)
        self._push_row(self.timestamps_s, frame.timestamp_s)
        self.frame_count += 1
        return result

    def _advance_static_materials(self) -> None:
        """Run a slice of the static CLEAN; publish the map when a pass finishes, then start the next."""
        if self._static_job is None:
            if self._static_idle_frames > 0:
                self._static_idle_frames -= 1
                return
            background = self.clutter_filter.background
            background_image = Backprojector.combine(self.backprojector.image_bands(background))
            candidates = self.backprojector.select_candidates(
                background_image, self.min_material_reflectivity, self.STATIC_MAX_CANDIDATES
            )
            self._static_job = SparseReflectorJob(
                self.backprojector, background, candidates, self.min_material_reflectivity, self.STATIC_MAX_REFLECTORS
            )
        if not self._static_job.step(self.clean_iterations_per_frame):
            return
        voxel_indices, band_rho = self._static_job.result()
        self._static_job = None
        self._static_idle_frames = self.STATIC_REFRESH_FRAMES if self.static_map_count > 0 else 0
        material_ids = np.full(self.grid.voxel_count, NO_MATERIAL_ID, np.int8)
        confidence = np.zeros(self.grid.voxel_count, np.float32)
        band_magnitudes = np.zeros((self.backprojector.band_count, self.grid.voxel_count), np.float32)
        if len(voxel_indices):
            magnitudes = np.abs(band_rho).T                                   # (band_count, R)
            ids, reflector_confidence = classify_materials(magnitudes, self.material_signatures, self.min_material_reflectivity)
            material_ids[voxel_indices], confidence[voxel_indices] = ids, reflector_confidence
            band_magnitudes[:, voxel_indices] = magnitudes
        self.static_material_ids = material_ids.reshape(self.grid.shape)
        self.material_confidence = confidence.reshape(self.grid.shape)
        self.band_reflectivity = band_magnitudes.reshape(self.backprojector.band_count, *self.grid.shape)
        self.static_map_count += 1

    def _classify_target(self, moving_part, motion_image, track_position) -> tuple[int, float]:
        """Small CLEAN on the moving part around the track; the strongest component's spectrum decides."""
        if track_position is None:
            return NO_MATERIAL_ID, 0.0
        candidates = self.backprojector.select_candidates(
            motion_image, self.min_material_reflectivity, 150, track_position, self.TARGET_RADIUS_M
        )
        job = SparseReflectorJob(self.backprojector, moving_part, candidates, self.min_material_reflectivity, self.TARGET_REFLECTORS)
        job.step(self.TARGET_REFLECTORS + 1)
        voxel_indices, band_rho = job.result()
        if len(voxel_indices) == 0:
            return NO_MATERIAL_ID, 0.0
        strongest = int(np.abs(band_rho).mean(axis=1).argmax())
        ids, confidence = classify_materials(np.abs(band_rho[strongest])[:, None], self.material_signatures, 0.0)
        return int(ids[0]), float(confidence[0])

    @staticmethod
    def _push_row(buffer: np.ndarray, row) -> None:
        buffer[:-1] = buffer[1:]
        buffer[-1] = row
# --- END [4.2] ---
