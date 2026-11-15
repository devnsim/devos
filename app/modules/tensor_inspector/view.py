# ==============================================================================
# FILE: app/modules/tensor_inspector/view.py
# OVERVIEW: Live inspector for a Tensor4D: a waterfall (time x one axis) beside a 3D scatter.
# PURPOSE:
#   1. Waterfall: rows = steps of the first axis (newest at the top), columns = a chosen axis,
#      with the other two axes held at slider positions.
#   2. 3D scatter: the volume at one time step, showing only cells at or above a threshold,
#      coloured by value; the waterfall's line through the volume is drawn in white.
#   3. Live mode: a timer pushes new frames from the demo wave source into the tensor.
#   4. launch_inspector() opens the same inspector in its own window (used by Tensor4D.inspect()).
# CONTRACT:
#   - Requires: numpy, pyqtgraph (+ pyqtgraph.opengl / PyOpenGL for the 3D view), app.data.tensor4d
#               (Tensor4D, wave_frame), app.ui.theme colors, QSS selectors #tensor_inspector_panel,
#               #inspector_controls_panel, #inspector_control_label, #inspector_stats_label,
#               #scatter_unavailable_label
#   - Exposes: TensorInspectorModule(tensor=None, is_live=True, context=None) (QWidget); set_tensor(tensor);
#              set_live(is_live); refresh_views(); launch_inspector(tensor, title) -> QWidget
# NOTES:
#   - [2026-11-15] Created. Built on PySide6 + pyqtgraph (pyqtgraph auto-selects PySide6). PyQt6 was
#                  not used: it can't share a process with PySide6 (both bind the same Qt libraries).
#   - [2026-11-15] The live timer only runs while the inspector is visible (showEvent / hideEvent),
#                  so a parked page or hidden tab costs no CPU.
#   - [2026-11-15] The OpenGL probe moved to app/ui/gl_support.py (shared by every 3D module).
#   - [2026-11-15] If the OpenGL 3D view can't be created (no GL driver), a label replaces it and
#                  the waterfall keeps working. GL is probed up front (opengl_unavailable_reason):
#                  GLViewWidget only fails inside initializeGL() on first show, and that crashed the app.
#   - [2026-11-15] Colour levels are fixed per tensor (symmetric around 0 from the first range seen)
#                  so colours don't "breathe" as frames stream in.
#   - [2026-11-15] Moved from app/ui/modules/tensor_inspector_module.py (module apps live in app/modules/<name>/).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - follow latest (bool): True while the time slider sits at the newest step; it then stays there as
#   frames stream in.
# - LIVE_INTERVAL_MS (int | ms): Time between pushed frames in live mode (default: 50, i.e. 20 fps).
# - THRESHOLD_SLIDER_STEPS (int): Resolution of the threshold slider over the colour range (default: 100).
# - time step: Index along the tensor's first axis.
# - waterfall axis: The axis that runs across the waterfall's columns (x, y or z).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS_AND_CONSTANTS
#    1.1 imports
#    1.2 optional_opengl (opengl_unavailable_reason)
# 2. TENSOR_INSPECTOR_MODULE
#    2.1 class_constants
#    2.2 init_module
#    2.3 build_controls
#    2.4 build_waterfall
#    2.5 build_scatter
#    2.6 public_api
#    2.7 refresh_views
#    2.8 control_handlers
#    2.9 live_mode
# 3. LAUNCHER
#    3.1 launch_inspector
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QLabel, QSlider, QSplitter, QToolButton, QVBoxLayout, QWidget,
)

from app.data.tensor4d import Tensor4D, wave_frame
from app.ui.theme import BACKGROUND_COLOR, BORDER_COLOR, MUTED_TEXT_COLOR
# --- END [1.1] ---

# ==============================================================================
# [1.2] optional_opengl
# ==============================================================================
from app.ui.gl_support import gl, opengl_unavailable_reason   # noqa: E402  (shared GL probe)
# --- END [1.2] ---


# ==============================================================================
# [2.1] class_constants
# ==============================================================================
class TensorInspectorModule(QWidget):
    """Waterfall + 3D scatter inspector for a Tensor4D, optionally streaming live demo frames."""

    LIVE_INTERVAL_MS = 50
    THRESHOLD_SLIDER_STEPS = 100
    DEFAULT_THRESHOLD_FRACTION = 0.25            # of the positive colour range
    CAMERA_DISTANCE_FACTOR = 1.5                 # camera distance = largest spatial size x this
    COLORMAP_NAME = "viridis"
    SCATTER_POINT_SIZE_PX = 5
    WATERFALL_AXES = ("x", "y", "z")
    STATS_UPDATE_INTERVAL_S = 0.5
# --- END [2.1] ---

# ==============================================================================
# [2.2] init_module
# ==============================================================================
    def __init__(self, tensor: Tensor4D | None = None, is_live: bool = True, parent: QWidget | None = None,
                 context=None) -> None:
        super().__init__(parent, objectName="tensor_inspector_panel")
        self.tensor = tensor if tensor is not None else Tensor4D.demo()
        self._live_step = self.tensor.size(0)          # next demo step to generate
        self._is_live = is_live
        self._is_following_latest = True
        self._color_levels = (-1.0, 1.0)
        self._frame_times: list[float] = []
        self._last_stats_time = 0.0
        self.colormap = pg.colormap.get(self.COLORMAP_NAME)

        self.inspector_layout = QVBoxLayout(self)
        self.inspector_layout.setContentsMargins(6, 6, 6, 6)
        self.inspector_layout.setSpacing(6)
        self._build_controls()

        self.view_splitter = QSplitter(Qt.Orientation.Horizontal, objectName="pane_splitter")
        self.view_splitter.setChildrenCollapsible(False)
        self.view_splitter.setHandleWidth(4)
        self._build_waterfall()
        self._build_scatter()
        self.inspector_layout.addWidget(self.view_splitter, 1)

        self.live_timer = QTimer(self, interval=self.LIVE_INTERVAL_MS, timeout=self._push_live_frame)
        self.set_tensor(self.tensor)
        self.set_live(is_live)
# --- END [2.2] ---

# ==============================================================================
# [2.3] build_controls
# ==============================================================================
    def _build_controls(self) -> None:
        self.inspector_controls_panel = QWidget(objectName="inspector_controls_panel")
        controls_layout = QGridLayout(self.inspector_controls_panel)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setHorizontalSpacing(6)
        controls_layout.setVerticalSpacing(4)

        # Row 0: play/pause, waterfall axis, the two held axes
        self.live_toggle_btn = QToolButton(objectName="live_toggle_btn", checkable=True)
        self.live_toggle_btn.setToolTip("Stream new frames (live demo source)")
        self.live_toggle_btn.toggled.connect(self.set_live)
        self.waterfall_axis_combo = QComboBox(objectName="waterfall_axis_combo")
        self.waterfall_axis_combo.addItems(self.WATERFALL_AXES)
        self.waterfall_axis_combo.setToolTip("Axis across the waterfall's columns")
        self.waterfall_axis_combo.currentIndexChanged.connect(self._on_waterfall_axis_changed)
        self.first_held_label, self.first_held_slider = self._labeled_slider("first_held")
        self.second_held_label, self.second_held_slider = self._labeled_slider("second_held")

        controls_layout.addWidget(self.live_toggle_btn, 0, 0)
        controls_layout.addWidget(self._control_label("waterfall"), 0, 1)
        controls_layout.addWidget(self.waterfall_axis_combo, 0, 2)
        controls_layout.addWidget(self.first_held_label, 0, 3)
        controls_layout.addWidget(self.first_held_slider, 0, 4)
        controls_layout.addWidget(self.second_held_label, 0, 5)
        controls_layout.addWidget(self.second_held_slider, 0, 6)

        # Row 1: time step for the 3D view, threshold, stats
        self.time_step_label, self.time_step_slider = self._labeled_slider("time_step")
        self.threshold_label, self.threshold_slider = self._labeled_slider("threshold")
        self.threshold_slider.setRange(0, self.THRESHOLD_SLIDER_STEPS)
        self.inspector_stats_label = QLabel(objectName="inspector_stats_label")

        controls_layout.addWidget(self.time_step_label, 1, 1)
        controls_layout.addWidget(self.time_step_slider, 1, 2, 1, 3)
        controls_layout.addWidget(self.threshold_label, 1, 5)
        controls_layout.addWidget(self.threshold_slider, 1, 6)
        controls_layout.addWidget(self.inspector_stats_label, 0, 7, 2, 1, Qt.AlignmentFlag.AlignRight)
        controls_layout.setColumnStretch(4, 1)
        controls_layout.setColumnStretch(6, 1)

        self.first_held_slider.valueChanged.connect(self.refresh_views)
        self.second_held_slider.valueChanged.connect(self.refresh_views)
        self.time_step_slider.valueChanged.connect(self._on_time_step_changed)
        self.threshold_slider.valueChanged.connect(self.refresh_views)
        self.inspector_layout.addWidget(self.inspector_controls_panel)

    def _labeled_slider(self, name: str) -> tuple[QLabel, QSlider]:
        slider = QSlider(Qt.Orientation.Horizontal, objectName=f"{name}_slider")
        slider.setMinimumWidth(60)
        return self._control_label(""), slider

    def _control_label(self, text: str) -> QLabel:
        return QLabel(text, objectName="inspector_control_label")
# --- END [2.3] ---

# ==============================================================================
# [2.4] build_waterfall
# ==============================================================================
    def _build_waterfall(self) -> None:
        self.waterfall_plot = pg.PlotWidget(background=BACKGROUND_COLOR)
        self.waterfall_plot.setObjectName("waterfall_plot")
        self.waterfall_plot.setMinimumWidth(160)
        plot_item = self.waterfall_plot.getPlotItem()
        plot_item.setMenuEnabled(False)
        plot_item.hideButtons()
        for axis_name in ("left", "bottom"):
            axis = plot_item.getAxis(axis_name)
            axis.setPen(pg.mkPen(BORDER_COLOR))
            axis.setTextPen(pg.mkPen(MUTED_TEXT_COLOR))

        # Row-major: image[row, column] = image[time step, position along the waterfall axis]
        self.waterfall_image = pg.ImageItem(axisOrder="row-major")
        self.waterfall_image.setLookupTable(self.colormap.getLookupTable(nPts=256))
        plot_item.addItem(self.waterfall_image)

        # Draggable line marking the time step shown in 3D
        self.time_step_line = pg.InfiniteLine(
            angle=0, movable=True, pen=pg.mkPen("w", width=1), hoverPen=pg.mkPen("w", width=2)
        )
        self.time_step_line.sigPositionChanged.connect(self._on_time_step_line_moved)
        plot_item.addItem(self.time_step_line)
        self.view_splitter.addWidget(self.waterfall_plot)
# --- END [2.4] ---

# ==============================================================================
# [2.5] build_scatter
# ==============================================================================
    def _build_scatter(self) -> None:
        self.scatter_view = None
        unavailable_reason = opengl_unavailable_reason()
        if not unavailable_reason:
            try:
                self.scatter_view = gl.GLViewWidget()
                self.scatter_view.setObjectName("scatter_view")
                self.scatter_view.setBackgroundColor(BACKGROUND_COLOR)
                self.scatter_view.setMinimumWidth(160)
                self.scatter_grid = gl.GLGridItem(color=(255, 255, 255, 30))
                self.scatter_view.addItem(self.scatter_grid)
                self.scatter_item = gl.GLScatterPlotItem(pxMode=True, size=self.SCATTER_POINT_SIZE_PX)
                self.scatter_item.setGLOptions("additive")
                self.scatter_view.addItem(self.scatter_item)
                self.waterfall_line_item = gl.GLLinePlotItem(color=(1, 1, 1, 0.8), width=2, antialias=True)
                self.scatter_view.addItem(self.waterfall_line_item)
            except Exception as error:                   # GL context couldn't be created
                self.scatter_view = None
                fallback_text = f"3D view unavailable ({error.__class__.__name__})"
        else:
            fallback_text = f"3D view unavailable: {unavailable_reason}"

        if self.scatter_view is None:
            self.scatter_unavailable_label = QLabel(fallback_text, objectName="scatter_unavailable_label")
            self.scatter_unavailable_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.view_splitter.addWidget(self.scatter_unavailable_label)
        else:
            self.view_splitter.addWidget(self.scatter_view)
# --- END [2.5] ---

# ==============================================================================
# [2.6] public_api
# ==============================================================================
    def set_tensor(self, tensor: Tensor4D) -> None:
        """Inspect a different tensor: resets sliders, colour levels and the 3D camera."""
        self.tensor = tensor
        low, high = tensor.value_range()
        level = max(abs(low), abs(high)) or 1.0
        self._color_levels = (-level, level)

        self.time_step_slider.setRange(0, tensor.size(0) - 1)
        self.time_step_slider.setValue(tensor.size(0) - 1)
        self.threshold_slider.setValue(round(self.THRESHOLD_SLIDER_STEPS * (0.5 + self.DEFAULT_THRESHOLD_FRACTION / 2)))
        self.waterfall_axis_combo.setItemText(0, tensor.axis_names[1])
        self.waterfall_axis_combo.setItemText(1, tensor.axis_names[2])
        self.waterfall_axis_combo.setItemText(2, tensor.axis_names[3])
        self._on_waterfall_axis_changed()

        if self.scatter_view is not None:
            spatial_shape = np.array(tensor.shape[1:], dtype=float)
            self.scatter_grid.setSize(spatial_shape[0], spatial_shape[1])
            self.scatter_grid.setSpacing(max(1.0, spatial_shape[0] / 8), max(1.0, spatial_shape[1] / 8))
            self.scatter_view.setCameraPosition(distance=float(spatial_shape.max()) * self.CAMERA_DISTANCE_FACTOR, elevation=25, azimuth=40)

    def set_live(self, is_live: bool) -> None:
        self._is_live = is_live
        self.live_toggle_btn.blockSignals(True)
        self.live_toggle_btn.setChecked(is_live)
        self.live_toggle_btn.blockSignals(False)
        self.live_toggle_btn.setText("❚❚ Pause" if is_live else "▶ Live")
        self._update_live_timer()
# --- END [2.6] ---

# ==============================================================================
# [2.7] refresh_views
# ==============================================================================
    def refresh_views(self) -> None:
        along_name, held_names = self._waterfall_axes()
        held = {held_names[0]: self.first_held_slider.value(), held_names[1]: self.second_held_slider.value()}
        time_step = self.time_step_slider.value()
        threshold = self._threshold_value()

        # --- labels ---
        self.first_held_label.setText(f"{held_names[0]} {held[held_names[0]]}")
        self.second_held_label.setText(f"{held_names[1]} {held[held_names[1]]}")
        self.time_step_label.setText(f"{self.tensor.axis_names[0]} {time_step}")
        self.threshold_label.setText(f"≥ {threshold:.2f}")

        # --- waterfall: newest step at the top ---
        waterfall = self.tensor.waterfall(along_name, **held)
        self.waterfall_image.setImage(waterfall, levels=self._color_levels, autoLevels=False)
        self.time_step_line.blockSignals(True)
        self.time_step_line.setValue(time_step + 0.5)
        self.time_step_line.blockSignals(False)

        # --- 3D scatter of the chosen step ---
        if self.scatter_view is not None:
            positions, values = self.tensor.to_points(time_step, threshold)
            center = (np.array(self.tensor.shape[1:], dtype=np.float32) - 1) / 2
            low, high = self._color_levels
            normalized = np.clip((values - low) / (high - low), 0.0, 1.0)
            colors = self.colormap.map(normalized, mode="float")
            colors[:, 3] = 0.25 + 0.75 * normalized          # weaker values fade out
            self.scatter_item.setData(pos=positions - center, color=colors)
            self._update_waterfall_line(along_name, held, center)

        self._update_stats(len(values) if self.scatter_view is not None else None)

    def _update_waterfall_line(self, along_name: str, held: dict[str, int], center: np.ndarray) -> None:
        """White line through the volume where the waterfall samples it."""
        axis_names = self.tensor.axis_names[1:]
        along_size = self.tensor.size(along_name)
        line_points = np.zeros((2, 3), dtype=np.float32)
        for position, name in enumerate(axis_names):
            if name == along_name:
                line_points[:, position] = (0, along_size - 1)
            else:
                line_points[:, position] = held[name]
        self.waterfall_line_item.setData(pos=line_points - center)

    def _update_stats(self, point_count: int | None) -> None:
        now = time.monotonic()
        self._frame_times = [stamp for stamp in self._frame_times if now - stamp < 1.0] + [now]
        if now - self._last_stats_time < self.STATS_UPDATE_INTERVAL_S:
            return
        self._last_stats_time = now
        shape_text = " × ".join(f"{name}{size}" for name, size in zip(self.tensor.axis_names, self.tensor.shape))
        points_text = "" if point_count is None else f"  ·  {point_count:,} pts"
        rate_text = f"  ·  {len(self._frame_times)} fps" if self._is_live else ""
        self.inspector_stats_label.setText(f"{shape_text}{points_text}{rate_text}")
# --- END [2.7] ---

# ==============================================================================
# [2.8] control_handlers
# ==============================================================================
    def _waterfall_axes(self) -> tuple[str, tuple[str, str]]:
        """(axis across the waterfall, the two held axes) by name."""
        spatial_names = self.tensor.axis_names[1:]
        along_name = spatial_names[self.waterfall_axis_combo.currentIndex()]
        held_names = tuple(name for name in spatial_names if name != along_name)
        return along_name, held_names

    def _on_waterfall_axis_changed(self) -> None:
        along_name, held_names = self._waterfall_axes()
        for slider, held_name in ((self.first_held_slider, held_names[0]), (self.second_held_slider, held_names[1])):
            slider.blockSignals(True)
            slider.setRange(0, self.tensor.size(held_name) - 1)
            slider.setValue(self.tensor.size(held_name) // 2)
            slider.blockSignals(False)
        plot_item = self.waterfall_plot.getPlotItem()
        plot_item.setLabel("bottom", along_name)
        plot_item.setLabel("left", self.tensor.axis_names[0])
        self.refresh_views()
        plot_item.autoRange(padding=0)

    def _on_time_step_changed(self, time_step: int) -> None:
        self._is_following_latest = time_step == self.time_step_slider.maximum()
        self.refresh_views()

    def _on_time_step_line_moved(self) -> None:
        time_step = int(np.clip(self.time_step_line.value(), 0, self.tensor.size(0) - 1))
        self.time_step_slider.setValue(time_step)

    def _threshold_value(self) -> float:
        low, high = self._color_levels
        return low + (high - low) * self.threshold_slider.value() / self.THRESHOLD_SLIDER_STEPS
# --- END [2.8] ---

# ==============================================================================
# [2.9] live_mode
# ==============================================================================
    def _push_live_frame(self) -> None:
        self.tensor.push(wave_frame(self.tensor.shape[1:], self._live_step))
        self._live_step += 1
        if not self._is_following_latest:
            # Keep pointing at the same data as it scrolls down by one step.
            self.time_step_slider.blockSignals(True)
            self.time_step_slider.setValue(max(0, self.time_step_slider.value() - 1))
            self.time_step_slider.blockSignals(False)
        self.refresh_views()

    def _update_live_timer(self) -> None:
        if self._is_live and self.isVisible():
            self.live_timer.start()
        else:
            self.live_timer.stop()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_live_timer()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._update_live_timer()
# --- END [2.9] ---


# ==============================================================================
# [3.1] launch_inspector
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
# --- END [3.1] ---
