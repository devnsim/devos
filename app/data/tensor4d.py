# ==============================================================================
# FILE: app/data/tensor4d.py
# OVERVIEW: Tensor4D, a 4D NumPy array with named axes and clean slicing methods.
# PURPOSE:
#   1. Wraps a 4D NumPy array (default axes t, x, y, z) and validates its shape.
#   2. Slices by axis name: take(), volume(), plane(), line(), value(), window(), reduce().
#   3. Builds the views the inspector draws: waterfall() (time x one axis) and to_points() (3D scatter).
#   4. Streams: push() appends a new frame along the first axis, dropping the oldest (fixed memory).
#   5. Provides a demo source (wave_frame / Tensor4D.demo) and inspect() to open the live inspector.
# CONTRACT:
#   - Requires: numpy. No Qt import at module level (inspect() imports the UI lazily).
#   - Exposes: Tensor4D (class), wave_frame(spatial_shape, phase) -> ndarray, DEFAULT_AXIS_NAMES
# NOTES:
#   - [2026-11-15] Created for the Project Editor's Tensor Inspector module. Methods return plain
#                  NumPy arrays (views where NumPy allows) so callers can use any NumPy tool on them.
#   - [2026-11-15] push() shifts the buffer in place (memmove) instead of np.roll, which would
#                  allocate a full copy on every frame.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - axis (str | int): An axis given by name ("t", "x", ...) or position (0-3); negative positions allowed.
# - fixed axes (kwargs): Axis name -> index to hold still, e.g. take(t=5, z=2) keeps x and y free.
# - frame (ndarray): One step along the first axis; shape = shape[1:] (a 3D volume for t, x, y, z).
# - free axes: Axes not fixed by a slicing call; they remain in the result in their original order.
# - waterfall (ndarray): 2D plane (first axis x one other axis), all other axes fixed; rows = time.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS_AND_CONSTANTS
#    1.1 imports
#    1.2 constants
# 2. TENSOR4D
#    2.1 init_and_properties
#    2.2 axis_lookup
#    2.3 slicing
#    2.4 reductions_and_windows
#    2.5 inspector_views
#    2.6 streaming
#    2.7 demo_and_inspect
# 3. DEMO_SOURCE
#    3.1 wave_frame
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
# --- END [1.1] ---

# ==============================================================================
# [1.2] constants
# ==============================================================================
DEFAULT_AXIS_NAMES = ("t", "x", "y", "z")
REDUCE_OPERATIONS = {"mean": np.mean, "max": np.max, "min": np.min, "sum": np.sum, "std": np.std}
# --- END [1.2] ---


# ==============================================================================
# [2.1] init_and_properties
# ==============================================================================
class Tensor4D:
    """A 4D array with named axes.

    >>> tensor = Tensor4D.demo(shape=(64, 24, 24, 24))
    >>> tensor.volume(t=-1).shape           # latest 3D frame
    (24, 24, 24)
    >>> tensor.waterfall("x", y=12, z=12).shape
    (64, 24)
    >>> tensor.window(x=(4, 12)).shape      # sub-tensor, still a Tensor4D
    (64, 8, 24, 24)
    """

    NDIM = 4

    def __init__(self, data: np.ndarray, axis_names: Sequence[str] = DEFAULT_AXIS_NAMES) -> None:
        data = np.asarray(data)
        if data.ndim != self.NDIM:
            raise ValueError(f"Tensor4D needs a 4D array, got shape {data.shape}")
        axis_names = tuple(axis_names)
        if len(axis_names) != self.NDIM or len(set(axis_names)) != self.NDIM:
            raise ValueError(f"Tensor4D needs 4 unique axis names, got {axis_names}")
        self._data = data
        self.axis_names = axis_names

    @property
    def data(self) -> np.ndarray:
        """The underlying array (read-only view; use push() or the array you passed in to change it)."""
        view = self._data.view()
        view.flags.writeable = False
        return view

    @property
    def shape(self) -> tuple[int, int, int, int]:
        return self._data.shape

    @property
    def dtype(self) -> np.dtype:
        return self._data.dtype

    def __getitem__(self, key):
        """Plain NumPy indexing, e.g. tensor[-1, :, :, 4]."""
        return self.data[key]

    def __repr__(self) -> str:
        axes_text = ", ".join(f"{name}={size}" for name, size in zip(self.axis_names, self.shape))
        return f"Tensor4D({axes_text}, dtype={self.dtype})"

    def value_range(self) -> tuple[float, float]:
        return float(self._data.min()), float(self._data.max())
# --- END [2.1] ---

# ==============================================================================
# [2.2] axis_lookup
# ==============================================================================
    def axis(self, axis: str | int) -> int:
        """Position (0-3) of an axis given by name or position."""
        if isinstance(axis, str):
            if axis not in self.axis_names:
                raise KeyError(f"No axis {axis!r}; axes are {self.axis_names}")
            return self.axis_names.index(axis)
        if not -self.NDIM <= axis < self.NDIM:
            raise IndexError(f"Axis {axis} out of range for 4 axes")
        return axis % self.NDIM

    def size(self, axis: str | int) -> int:
        return self.shape[self.axis(axis)]

    def free_axes(self, **fixed: int) -> tuple[str, ...]:
        """Names of the axes a slicing call with these fixed axes would keep."""
        fixed_positions = {self.axis(name) for name in fixed}
        return tuple(name for position, name in enumerate(self.axis_names) if position not in fixed_positions)
# --- END [2.2] ---

# ==============================================================================
# [2.3] slicing
# ==============================================================================
    def take(self, **fixed: int) -> np.ndarray:
        """Hold the named axes at one index each; the free axes stay, in order.
        take(t=3) -> (x, y, z);  take(y=1, z=2) -> (t, x);  indices may be negative."""
        index = [slice(None)] * self.NDIM
        for name, position_index in fixed.items():
            axis_position = self.axis(name)
            size = self.shape[axis_position]
            if not -size <= position_index < size:
                raise IndexError(f"{name}={position_index} out of range (size {size})")
            index[axis_position] = position_index
        return self.data[tuple(index)]

    def volume(self, **fixed: int) -> np.ndarray:
        """3D block: exactly one axis fixed, e.g. volume(t=-1)."""
        return self._take_with_free_count(3, fixed)

    def plane(self, **fixed: int) -> np.ndarray:
        """2D slice: exactly two axes fixed, e.g. plane(t=0, z=5) -> (x, y)."""
        return self._take_with_free_count(2, fixed)

    def line(self, **fixed: int) -> np.ndarray:
        """1D profile: exactly three axes fixed, e.g. line(x=2, y=3, z=4) -> values over t."""
        return self._take_with_free_count(1, fixed)

    def value(self, **fixed: int) -> float:
        """Single value: all four axes fixed."""
        return float(self._take_with_free_count(0, fixed))

    def _take_with_free_count(self, free_count: int, fixed: dict[str, int]) -> np.ndarray:
        if len(fixed) != self.NDIM - free_count:
            raise ValueError(
                f"Fix exactly {self.NDIM - free_count} axes for a {free_count}D result; got {sorted(fixed)}"
            )
        return self.take(**fixed)
# --- END [2.3] ---

# ==============================================================================
# [2.4] reductions_and_windows
# ==============================================================================
    def window(self, **ranges: tuple[int, int] | slice) -> Tensor4D:
        """Sub-tensor over index ranges, e.g. window(x=(4, 12), t=slice(-32, None)).
        Keeps all four axes (and their names); shares memory with this tensor."""
        index = [slice(None)] * self.NDIM
        for name, index_range in ranges.items():
            index[self.axis(name)] = index_range if isinstance(index_range, slice) else slice(*index_range)
        return Tensor4D(self._data[tuple(index)], self.axis_names)

    def reduce(self, axis: str | int, operation: str = "mean") -> np.ndarray:
        """Collapse one axis with mean / max / min / sum / std -> 3D array of the other axes."""
        if operation not in REDUCE_OPERATIONS:
            raise ValueError(f"operation must be one of {sorted(REDUCE_OPERATIONS)}")
        return REDUCE_OPERATIONS[operation](self._data, axis=self.axis(axis))
# --- END [2.4] ---

# ==============================================================================
# [2.5] inspector_views
# ==============================================================================
    def waterfall(self, along: str | int, **fixed: int) -> np.ndarray:
        """Time x one axis: rows are steps of the first axis, columns run along `along`.
        The two remaining axes must be fixed, e.g. waterfall("x", y=12, z=12) -> (t, x)."""
        along_position = self.axis(along)
        if along_position == 0:
            raise ValueError("waterfall runs along a non-time axis (the first axis is the rows)")
        plane = self.plane(**fixed)
        if self.free_axes(**fixed) != (self.axis_names[0], self.axis_names[along_position]):
            raise ValueError(f"Fix the two axes other than {self.axis_names[0]!r} and {along!r}")
        return plane

    def to_points(self, step: int = -1, threshold: float | None = None) -> tuple[np.ndarray, np.ndarray]:
        """3D scatter data for one step of the first axis.
        Returns (positions (N, 3) as float32 indices, values (N,)); with threshold, only cells whose
        value is >= threshold are kept."""
        volume = self.take(**{self.axis_names[0]: step})
        if threshold is None:
            mask = np.ones(volume.shape, dtype=bool)
        else:
            mask = volume >= threshold
        positions = np.argwhere(mask).astype(np.float32)
        return positions, volume[mask]
# --- END [2.5] ---

# ==============================================================================
# [2.6] streaming
# ==============================================================================
    def push(self, frame: np.ndarray) -> None:
        """Append a frame at the end of the first axis and drop the oldest (buffer size stays fixed)."""
        frame = np.asarray(frame)
        if frame.shape != self.shape[1:]:
            raise ValueError(f"frame shape {frame.shape} != {self.shape[1:]}")
        if not self._data.flags.writeable:
            self._data = self._data.copy()
        self._data[:-1] = self._data[1:]          # in-place shift; NumPy handles the overlap
        self._data[-1] = frame
# --- END [2.6] ---

# ==============================================================================
# [2.7] demo_and_inspect
# ==============================================================================
    @classmethod
    def demo(cls, shape: tuple[int, int, int, int] = (96, 24, 24, 24)) -> Tensor4D:
        """A tensor filled with the moving wave field from wave_frame()."""
        step_count, *spatial_shape = shape
        frames = [wave_frame(tuple(spatial_shape), step) for step in range(step_count)]
        return cls(np.stack(frames).astype(np.float32))

    def inspect(self, title: str = "Tensor Inspector"):
        """Open the live inspector in its own window (needs a running QApplication)."""
        from app.modules.tensor_inspector.view import launch_inspector   # lazy: keeps numpy-only import
        return launch_inspector(self, title)
# --- END [2.7] ---


# ==============================================================================
# [3.1] wave_frame
# ==============================================================================
def wave_frame(spatial_shape: tuple[int, int, int], step: float, seed: int | None = None) -> np.ndarray:
    """One 3D frame of a demo field: two spherical waves orbiting the centre, interfering,
    under a soft Gaussian envelope, plus a little noise. Values roughly in [-2, 2]."""
    x_size, y_size, z_size = spatial_shape
    x, y, z = np.meshgrid(
        np.linspace(-1, 1, x_size), np.linspace(-1, 1, y_size), np.linspace(-1, 1, z_size), indexing="ij"
    )
    angle = step * 0.06
    first_source = (0.45 * np.cos(angle), 0.45 * np.sin(angle), 0.2 * np.sin(angle * 0.5))
    second_source = (-0.45 * np.cos(angle * 0.7), 0.3 * np.sin(angle * 1.3), -0.3)
    field = np.zeros(spatial_shape)
    for source_x, source_y, source_z in (first_source, second_source):
        radius = np.sqrt((x - source_x) ** 2 + (y - source_y) ** 2 + (z - source_z) ** 2)
        field += np.cos(9.0 * radius - step * 0.35) * np.exp(-2.5 * radius**2)
    rng = np.random.default_rng(seed)
    return (field + rng.normal(0.0, 0.05, spatial_shape)).astype(np.float32)
# --- END [3.1] ---
