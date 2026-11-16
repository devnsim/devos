# ==============================================================================
# FILE: app/modules/tensor_inspector/view.py
# OVERVIEW: Live inspector for a Tensor4D, as a canvas of blocks: waterfalls (time x one axis) and 3D
#           scatters of one time step. Blocks can be split, rearranged, resized, closed or replaced.
# PURPOSE:
#   1. Module toolbar: live / pause, time step (shared by all blocks), threshold, stats.
#   2. WaterfallBlock: rows = steps of the first axis (newest at the top), columns = a chosen axis; the
#      other two axes are held at the block's own sliders (each waterfall block can look elsewhere).
#   3. ScatterBlock: the volume at the chosen time step above the threshold, coloured by value, with a
#      white line for every waterfall block's sampling line.
#   4. Live mode: a timer pushes new frames from the demo wave source into the tensor.
#   5. launch_inspector() opens the inspector in its own window (used by Tensor4D.inspect()).
# CONTRACT:
#   - Requires: numpy, pyqtgraph (+ opengl), app.data.tensor4d (Tensor4D, wave_frame), app.modules.base,
#               app.ui (block_canvas, gl_support, plot_tools, ui_tools, theme), QSS selectors
#               #tensor_inspector_panel, #module_controls_panel, #control_label, #module_stats_label,
#               #scatter_unavailable_label
#   - Exposes: TensorInspectorModule(tensor=None, is_live=True, parent=None, context=None); set_tensor();
#              refresh_views(); time_step; launch_inspector(tensor, title) -> QWidget
# NOTES:
#   - [2026-11-15] Created. Built on PySide6 + pyqtgraph (pyqtgraph auto-selects PySide6). PyQt6 was
#                  not used: it can't share a process with PySide6 (both bind the same Qt libraries).
#   - [2026-11-15] If the OpenGL 3D view can't be created, a label replaces it (gl_support probe;
#                  GLViewWidget only fails inside initializeGL() on first show, which crashed the app).
#   - [2026-11-15] Colour levels are fixed per tensor (symmetric around 0 from the first range seen)
#                  so colours don't "breathe" as frames stream in.
#   - [2026-11-15] Moved from app/ui/modules/tensor_inspector_module.py (module apps live in app/modules/<name>/).
#   - [2026-11-15] Rebuilt as blocks on ModuleView (health pass): the fixed splitter, the copied live
#                  timer and plot styling are gone; waterfall axis + held sliders moved into each
#                  waterfall block (block_state saves them).
#   - [2026-11-15] Block top rows end with reserve_controls_space() (room for the corner controls).
#   - [2026-11-15] Module toolbar's top row ends with reserve_controls_space(): module tiles now carry the
#                  fading corner controls (no title bar), which must not cover the toolbar.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - follow latest (bool): True while the time slider sits at the newest step; it then stays there.
# - LIVE_INTERVAL_MS (int | ms): Time between pushed frames in live mode (default: 50, i.e. 20 fps).
# - THRESHOLD_SLIDER_STEPS (int): Resolution of the threshold slider over the colour range (default: 100).
# - time step: Index along the tensor's first axis.
# - waterfall axis: The axis that runs across a waterfall block's columns (x, y or z).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. BLOCKS
#    2.1 waterfall_block
#    2.2 scatter_block
# 3. TENSOR_INSPECTOR_MODULE
#    3.1 init_module
#    3.2 controls
#    3.3 tensor_and_refresh
#    3.4 live_mode
# 4. LAUNCHER
#    4.1 launch_inspector
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from app.data.tensor4d import Tensor4D, wave_frame
from app.modules.base import ModuleView
from app.ui.canvas.block_canvas import BlockSpec, reserve_controls_space
from app.ui.gl_support import gl, opengl_unavailable_reason
from app.ui.plot_tools import styled_plot
from app.ui.theme import BACKGROUND_COLOR
from app.ui.ui_tools import control_label

COLORMAP = pg.colormap.get("viridis")
# --- END [1.1] ---


# ==============================================================================
# [2.1] waterfall_block
# ==============================================================================
class WaterfallBlock(QWidget):
    """Time x one axis, the other two held by this block's sliders."""

    def __init__(self, module: "TensorInspectorModule") -> None:
        super().__init__()
        self.module = module
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.axis_combo = QComboBox(toolTip="Axis across the waterfall's columns")
        self.axis_combo.addItems(module.tensor.axis_names[1:])
        self.held_labels = [control_label(), control_label()]
        self.held_sliders = [QSlider(Qt.Orientation.Horizontal), QSlider(Qt.Orientation.Horizontal)]
        row.addWidget(control_label("along"))
        row.addWidget(self.axis_combo)
        for label, slider in zip(self.held_labels, self.held_sliders):
            row.addWidget(label)
            row.addWidget(slider, 1)
            slider.valueChanged.connect(module.refresh_views)
        reserve_controls_space(row)
        layout.addLayout(row)
        self.plot = styled_plot()
        self.image = pg.ImageItem(axisOrder="row-major")      # image[time step, position along the axis]
        self.image.setLookupTable(COLORMAP.getLookupTable(nPts=256))
        self.plot.addItem(self.image)
        self.time_line = pg.InfiniteLine(angle=0, movable=True, pen=pg.mkPen("w", width=1), hoverPen=pg.mkPen("w", width=2))
        self.time_line.sigPositionChanged.connect(lambda: module.set_time_step(int(self.time_line.value())))
        self.plot.addItem(self.time_line)
        layout.addWidget(self.plot, 1)
        self.axis_combo.currentIndexChanged.connect(self._on_axis_changed)
        self._on_axis_changed()

    def axes(self) -> tuple[str, dict[str, int]]:
        """(axis along the columns, {held axis: index})."""
        spatial = self.module.tensor.axis_names[1:]
        along = spatial[self.axis_combo.currentIndex()]
        held_names = [name for name in spatial if name != along]
        return along, {name: slider.value() for name, slider in zip(held_names, self.held_sliders)}

    def _on_axis_changed(self) -> None:
        tensor = self.module.tensor
        along = tensor.axis_names[1:][self.axis_combo.currentIndex()]
        for slider, name in zip(self.held_sliders, [n for n in tensor.axis_names[1:] if n != along]):
            slider.blockSignals(True)
            slider.setRange(0, tensor.size(name) - 1)
            slider.setValue(tensor.size(name) // 2)
            slider.blockSignals(False)
        self.plot.getPlotItem().setLabel("bottom", along)
        self.plot.getPlotItem().setLabel("left", tensor.axis_names[0])
        self.refresh()
        self.plot.getPlotItem().autoRange(padding=0)

    def refresh(self) -> None:
        module = self.module
        along, held = self.axes()
        for label, (name, value) in zip(self.held_labels, held.items()):
            label.setText(f"{name} {value}")
        self.image.setImage(module.tensor.waterfall(along, **held), levels=module.color_levels, autoLevels=False)
        self.time_line.blockSignals(True)
        self.time_line.setValue(module.time_step + 0.5)
        self.time_line.blockSignals(False)

    def block_state(self) -> dict:
        return {"along": self.axis_combo.currentIndex(), "held": [slider.value() for slider in self.held_sliders]}

    def restore_block_state(self, state: dict) -> None:
        self.axis_combo.setCurrentIndex(state.get("along", 0))
        for slider, value in zip(self.held_sliders, state.get("held", [])):
            slider.setValue(value)
# --- END [2.1] ---

# ==============================================================================
# [2.2] scatter_block
# ==============================================================================
class ScatterBlock(QWidget):
    """3D scatter of the chosen time step, plus each waterfall block's sampling line."""

    POINT_SIZE_PX = 5
    CAMERA_DISTANCE_FACTOR = 1.5

    def __init__(self, module: "TensorInspectorModule") -> None:
        super().__init__()
        self.module = module
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = None
        self.point_count = 0
        reason = opengl_unavailable_reason()
        if reason:
            layout.addWidget(QLabel(f"3D view unavailable: {reason}", objectName="scatter_unavailable_label",
                                    alignment=Qt.AlignmentFlag.AlignCenter))
            return
        self.view = gl.GLViewWidget()
        self.view.setBackgroundColor(BACKGROUND_COLOR)
        self.grid = gl.GLGridItem(color=(255, 255, 255, 30))
        self.points = gl.GLScatterPlotItem(pxMode=True, size=self.POINT_SIZE_PX)
        self.points.setGLOptions("additive")
        self.lines = gl.GLLinePlotItem(color=(1, 1, 1, 0.8), width=2, antialias=True, mode="lines")
        for item in (self.grid, self.points, self.lines):
            self.view.addItem(item)
        layout.addWidget(self.view)
        self.reset_camera()

    def reset_camera(self) -> None:
        if self.view is None:
            return
        spatial = np.array(self.module.tensor.shape[1:], dtype=float)
        self.grid.setSize(spatial[0], spatial[1])
        self.grid.setSpacing(max(1.0, spatial[0] / 8), max(1.0, spatial[1] / 8))
        self.view.setCameraPosition(distance=float(spatial.max()) * self.CAMERA_DISTANCE_FACTOR, elevation=25, azimuth=40)

    def refresh(self) -> None:
        if self.view is None:
            return
        module = self.module
        tensor = module.tensor
        positions, values = tensor.to_points(module.time_step, module.threshold())
        center = (np.array(tensor.shape[1:], dtype=np.float32) - 1) / 2
        low, high = module.color_levels
        normalized = np.clip((values - low) / (high - low), 0.0, 1.0)
        colors = COLORMAP.map(normalized, mode="float")
        colors[:, 3] = 0.25 + 0.75 * normalized              # weaker values fade out
        self.points.setData(pos=positions - center, color=colors)
        self.point_count = len(values)
        segments = []
        for waterfall in module.block_canvas.blocks("waterfall"):
            along, held = waterfall.axes()
            start, end = np.zeros(3, np.float32), np.zeros(3, np.float32)
            for position, name in enumerate(tensor.axis_names[1:]):
                start[position], end[position] = (0, tensor.size(name) - 1) if name == along else (held[name], held[name])
            segments += [start - center, end - center]
        self.lines.setVisible(bool(segments))
        if segments:
            self.lines.setData(pos=np.array(segments))
# --- END [2.2] ---


# ==============================================================================
# [3.1] init_module
# ==============================================================================
class TensorInspectorModule(ModuleView):
    """Blocks of waterfalls and 3D scatters over one Tensor4D (see file header)."""

    LIVE_INTERVAL_MS = 50
    THRESHOLD_SLIDER_STEPS = 100
    DEFAULT_THRESHOLD_FRACTION = 0.25            # of the positive colour range
    STATS_UPDATE_INTERVAL_S = 0.5
    DEFAULT_BLOCKS = {"split": "h", "sizes": [1, 1], "children": [{"kind": "waterfall"}, {"kind": "scatter"}]}

    def __init__(self, tensor: Tensor4D | None = None, is_live: bool = True, parent: QWidget | None = None,
                 context=None) -> None:
        super().__init__(parent, context, "tensor_inspector_panel")
        self.tensor = tensor if tensor is not None else Tensor4D.demo()
        self._live_step = self.tensor.size(0)          # next demo step to generate
        self._is_following_latest = True
        self.color_levels = (-1.0, 1.0)
        self.time_step = self.tensor.size(0) - 1
        self._frame_times: list[float] = []
        self._last_stats_time = 0.0
        self._set_color_levels()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self._build_controls(is_live))
        self.block_canvas = self.build_block_canvas([
            BlockSpec("waterfall", "Waterfall", "Time × one axis; the other two held by sliders",
                      lambda: WaterfallBlock(self)),
            BlockSpec("scatter", "3D scatter", "Volume at the selected time step, above the threshold",
                      lambda: ScatterBlock(self)),
        ], self.DEFAULT_BLOCKS)
        layout.addWidget(self.block_canvas, 1)
        self.refresh_views()
# --- END [3.1] ---

# ==============================================================================
# [3.2] controls
# ==============================================================================
    def _build_controls(self, is_live: bool) -> QWidget:
        panel = QWidget(objectName="module_controls_panel")
        row = QHBoxLayout(panel)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.setup_live(self.LIVE_INTERVAL_MS, self._push_live_frame, is_live))
        self.time_step_label = control_label()
        self.time_step_slider = QSlider(Qt.Orientation.Horizontal, objectName="time_step_slider")
        self.time_step_slider.valueChanged.connect(self.set_time_step)
        self.threshold_label = control_label()
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal, objectName="threshold_slider")
        self.threshold_slider.setRange(0, self.THRESHOLD_SLIDER_STEPS)
        self.threshold_slider.setValue(round(self.THRESHOLD_SLIDER_STEPS * (0.5 + self.DEFAULT_THRESHOLD_FRACTION / 2)))
        self.threshold_slider.valueChanged.connect(self.refresh_views)
        self.module_stats_label = QLabel(objectName="module_stats_label")
        for widget, stretch in ((self.time_step_label, 0), (self.time_step_slider, 2), (self.threshold_label, 0),
                                (self.threshold_slider, 1), (self.module_stats_label, 0)):
            row.addWidget(widget, stretch)
        reserve_controls_space(row)
        self.time_step_slider.setRange(0, self.tensor.size(0) - 1)
        self.time_step_slider.setValue(self.time_step)
        return panel

    def threshold(self) -> float:
        low, high = self.color_levels
        return low + (high - low) * self.threshold_slider.value() / self.THRESHOLD_SLIDER_STEPS

    def set_time_step(self, time_step: int) -> None:
        time_step = int(np.clip(time_step, 0, self.tensor.size(0) - 1))
        self.time_step = time_step
        self._is_following_latest = time_step == self.tensor.size(0) - 1
        if self.time_step_slider.value() != time_step:
            self.time_step_slider.setValue(time_step)        # re-enters once, then refreshes
            return
        self.refresh_views()
# --- END [3.2] ---

# ==============================================================================
# [3.3] tensor_and_refresh
# ==============================================================================
    def _set_color_levels(self) -> None:
        low, high = self.tensor.value_range()
        level = max(abs(low), abs(high)) or 1.0
        self.color_levels = (-level, level)

    def set_tensor(self, tensor: Tensor4D) -> None:
        """Inspect a different tensor: resets the time step, colour levels and the 3D cameras."""
        self.tensor = tensor
        self._set_color_levels()
        self.time_step_slider.setRange(0, tensor.size(0) - 1)
        self.set_time_step(tensor.size(0) - 1)
        for scatter in self.block_canvas.blocks("scatter"):
            scatter.reset_camera()

    def refresh_views(self) -> None:
        if not hasattr(self, "block_canvas"):
            return
        self.time_step_label.setText(f"{self.tensor.axis_names[0]} {self.time_step}")
        self.threshold_label.setText(f"≥ {self.threshold():.2f}")
        for block in self.block_canvas.blocks():
            block.refresh()
        self._update_stats()

    def _update_stats(self) -> None:
        now = time.monotonic()
        self._frame_times = [stamp for stamp in self._frame_times if now - stamp < 1.0] + [now]
        if now - self._last_stats_time < self.STATS_UPDATE_INTERVAL_S:
            return
        self._last_stats_time = now
        shape_text = " × ".join(f"{name}{size}" for name, size in zip(self.tensor.axis_names, self.tensor.shape))
        points = sum(block.point_count for block in self.block_canvas.blocks("scatter"))
        rate_text = f"  ·  {len(self._frame_times)} fps" if self.is_live else ""
        self.module_stats_label.setText(f"{shape_text}  ·  {points:,} pts{rate_text}")
# --- END [3.3] ---

# ==============================================================================
# [3.4] live_mode
# ==============================================================================
    def _push_live_frame(self) -> None:
        self.tensor.push(wave_frame(self.tensor.shape[1:], self._live_step))
        self._live_step += 1
        if not self._is_following_latest:
            self.time_step = max(0, self.time_step - 1)      # keep showing the same data as it scrolls
            self.time_step_slider.blockSignals(True)
            self.time_step_slider.setValue(self.time_step)
            self.time_step_slider.blockSignals(False)
        self.refresh_views()
# --- END [3.4] ---


# ==============================================================================
# [4.1] launch_inspector
# ==============================================================================
_open_inspector_windows: list[QWidget] = []          # keeps launched windows alive (no Python owner)


def launch_inspector(tensor: Tensor4D, title: str = "Tensor Inspector", is_live: bool = False) -> QWidget:
    """Open an inspector for `tensor` in its own top-level window. Needs a running QApplication.
    is_live=False by default: a script's own tensor shouldn't be overwritten by the demo source."""
    inspector_window = TensorInspectorModule(tensor, is_live=is_live)
    inspector_window.setWindowTitle(title)
    inspector_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    inspector_window.resize(1000, 560)
    _open_inspector_windows.append(inspector_window)
    inspector_window.destroyed.connect(
        lambda: _open_inspector_windows.remove(inspector_window) if inspector_window in _open_inspector_windows else None
    )
    inspector_window.show()
    return inspector_window
# --- END [4.1] ---
