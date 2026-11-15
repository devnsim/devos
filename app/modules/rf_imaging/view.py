# ==============================================================================
# FILE: app/modules/rf_imaging/view.py
# OVERVIEW: RF imaging inspector: live 2.5-6 GHz sensor data -> 3D reflectivity, motion, materials, track.
# PURPOSE:
#   1. Reads frames from an RfSensor (mock now, real hardware or a saved capture later), records them
#      into an RfCapture, and runs them through RfImagingPipeline.
#   2. 3D view: reflectivity / motion / material voxels, the antennas, the scanned box, the target's
#      track and the selected voxel.
#   3. Tabs: Range-time and Spectrum waterfalls (per antenna pair), Slice (2D cut, click to select a
#      voxel), Materials (selected voxel's spectrum vs the material library + scene summary).
#   4. Test controls: live/pause, calibrate, save / load captures, back to mock; timeline scrubbing.
# CONTRACT:
#   - Requires: numpy, pyqtgraph (+ opengl), app.modules.rf_imaging (sensor, imaging), app.data.em_materials,
#               app.ui.gl_support.opengl_unavailable_reason, app.ui.theme colors,
#               QSS selectors #rf_inspector_panel, #rf_controls_panel, #rf_source_label, #rf_stats_label,
#               #rf_material_summary_label, #inspector_control_label, #live_toggle_btn,
#               #scatter_unavailable_label
#   - Exposes: RfInspectorModule(sensor=None, context=None) (QWidget); set_sensor(sensor); calibrate(); refresh_views();
#              attributes sensor, pipeline, capture, selected_voxel
# NOTES:
#   - [2026-11-15] Created. Heavy setup (steering matrices, ~0.5 s / ~80 MB) waits until the module is
#                  first shown, so app start-up stays fast.
#   - [2026-11-15] Raw frames are recorded (not images) so saved tests can be re-processed later with
#                  better maths. Replays are not re-recorded.
#   - [2026-11-15] Only the visible tab is redrawn each frame.
#   - [2026-11-15] With a ModuleContext, saved captures are indexed in SQLite (store.py) and the
#                  Load button offers a menu of recent captures plus Browse.
#   - [2026-11-15] "Remove static" subtracts the latest clutter background from every history row
#                  (an approximation for older rows; the background drifts slowly).
#   - [2026-11-15] Moved from app/ui/modules/rf_inspector_module.py (module apps live in app/modules/<name>/).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - history slot / time step: Index into the pipeline's rolling history (newest = history_length - 1).
# - path length (m): TX -> reflector -> RX distance; the range-time view's horizontal axis.
# - selected voxel: (ix, iy, iz) picked in the Slice tab; inspected in the Materials tab and marked in 3D.
# - view mode: What the 3D view and slice show: reflectivity (|rho|), motion, or materials.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS_AND_CONSTANTS
#    1.1 imports
#    1.2 constants
# 2. RF_INSPECTOR_MODULE
#    2.1 init_module
#    2.2 build_controls
#    2.3 build_tabs
#    2.4 build_scene_view
#    2.5 startup_and_sources
#    2.6 capture_files
#    2.7 live_loop
#    2.8 refresh_views
#    2.9 refresh_tabs
#    2.10 refresh_scene
#    2.11 control_handlers
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QSlider, QSplitter,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from app.modules.rf_imaging.imaging import RfImagingPipeline, Calibration, range_profiles
from app.data.em_materials import MATERIALS, NO_MATERIAL_ID
from app.modules.rf_imaging.sensor import CaptureReplaySensor, MockRfSensor, RfCapture, RfSensor
from app.modules.rf_imaging.store import RfImagingStore
from app.ui.gl_support import gl, opengl_unavailable_reason
from app.ui.theme import BACKGROUND_COLOR, BORDER_COLOR, MUTED_TEXT_COLOR
# --- END [1.1] ---

# ==============================================================================
# [1.2] constants
# ==============================================================================
VIEW_REFLECTIVITY = "Reflectivity"
VIEW_MOTION = "Motion"
VIEW_MATERIALS = "Materials"
VIEW_MODES = (VIEW_REFLECTIVITY, VIEW_MOTION, VIEW_MATERIALS)

PLANE_TOP = "Top (x–y)"
PLANE_FRONT = "Front (x–z)"
PLANE_SIDE = "Side (y–z)"
SLICE_PLANES = {PLANE_TOP: 2, PLANE_FRONT: 1, PLANE_SIDE: 0}       # plane -> axis held fixed

ALL_PAIRS_LABEL = "All pairs (mean)"
TX_COLOR = (1.0, 0.55, 0.2, 1.0)
RX_COLOR = (0.3, 0.8, 1.0, 1.0)
TRACK_COLOR = (1.0, 0.6, 0.1, 0.9)
EMPTY_MATERIAL_RGB = (40, 40, 40)
# --- END [1.2] ---


# ==============================================================================
# [2.1] init_module
# ==============================================================================
class RfInspectorModule(QWidget):
    """Live RF imaging inspector (see file header)."""

    LIVE_INTERVAL_MS = 50
    SLIDER_STEPS = 100
    TARGET_VOTE_FRAMES = 40                         # ~2 s of target material estimates
    DB_RANGE = 45.0                                 # waterfall colour range below the peak
    DEFAULT_THRESHOLDS = {VIEW_REFLECTIVITY: 0.3, VIEW_MOTION: 0.45, VIEW_MATERIALS: 0.0}

    def __init__(self, sensor: RfSensor | None = None, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, objectName="rf_inspector_panel")
        self.context = context
        self.store = RfImagingStore(context.database, context.instance_id) if context is not None else None
        self.sensor: RfSensor | None = sensor
        self.pipeline: RfImagingPipeline | None = None
        self.capture: RfCapture | None = None
        self.is_calibrated = False
        self.selected_voxel: tuple[int, int, int] | None = None
        self._is_live = True
        self._is_following_latest = True
        self._thresholds = dict(self.DEFAULT_THRESHOLDS)
        self._frame_times: list[float] = []
        self._process_ms = 0.0
        self._last_stats_time = 0.0
        self.reflectivity_colormap = pg.colormap.get("viridis")
        self.motion_colormap = pg.colormap.get("inferno")

        self.inspector_layout = QVBoxLayout(self)
        self.inspector_layout.setContentsMargins(6, 6, 6, 6)
        self.inspector_layout.setSpacing(6)
        self._build_controls()

        self.view_splitter = QSplitter(Qt.Orientation.Horizontal, objectName="pane_splitter")
        self.view_splitter.setChildrenCollapsible(False)
        self.view_splitter.setHandleWidth(4)
        self._build_tabs()
        self._build_scene_view()
        self.inspector_layout.addWidget(self.view_splitter, 1)

        self.live_timer = QTimer(self, interval=self.LIVE_INTERVAL_MS, timeout=self._on_live_tick)
        self.set_live(True)
        self.rf_source_label.setText("Starting when first shown…")
# --- END [2.1] ---

# ==============================================================================
# [2.2] build_controls
# ==============================================================================
    def _build_controls(self) -> None:
        self.rf_controls_panel = QWidget(objectName="rf_controls_panel")
        controls_layout = QGridLayout(self.rf_controls_panel)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setHorizontalSpacing(6)
        controls_layout.setVerticalSpacing(4)

        # Row 0: test controls, source, stats
        self.live_toggle_btn = QToolButton(objectName="live_toggle_btn", checkable=True)
        self.live_toggle_btn.toggled.connect(self.set_live)
        self.calibrate_btn = QPushButton("Calibrate", toolTip="Re-run calibration (empty + metal reference sweeps)")
        self.calibrate_btn.clicked.connect(self.calibrate)
        self.save_capture_btn = QPushButton("Save…", toolTip="Save the recorded test (raw frames + calibration)")
        self.save_capture_btn.clicked.connect(self._on_save_capture)
        self.load_capture_btn = QPushButton("Load…", toolTip="Replay a saved test")
        self.load_capture_menu = QMenu(self.load_capture_btn)
        self.load_capture_menu.aboutToShow.connect(self._fill_load_menu)
        self.load_capture_btn.setMenu(self.load_capture_menu)
        self.mock_source_btn = QPushButton("Mock", toolTip="Switch back to the simulated sensor")
        self.mock_source_btn.clicked.connect(lambda: self.set_sensor(MockRfSensor()))
        self.rf_source_label = QLabel(objectName="rf_source_label")
        # Clip instead of pushing into the stats text when the tile is narrow.
        self.rf_source_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.rf_stats_label = QLabel(objectName="rf_stats_label")

        top_row = QHBoxLayout()
        top_row.setSpacing(4)
        for widget in (self.live_toggle_btn, self.calibrate_btn, self.save_capture_btn,
                       self.load_capture_btn, self.mock_source_btn):
            top_row.addWidget(widget)
        top_row.addSpacing(8)
        top_row.addWidget(self.rf_source_label, 1)
        top_row.addWidget(self.rf_stats_label)
        controls_layout.addLayout(top_row, 0, 0, 1, 10)

        # Row 1: view mode, antenna pair, timeline, threshold, overlays
        self.view_mode_combo = QComboBox(objectName="view_mode_combo")
        self.view_mode_combo.addItems(VIEW_MODES)
        self.view_mode_combo.currentTextChanged.connect(self._on_view_mode_changed)
        self.pair_combo = QComboBox(objectName="pair_combo", toolTip="Antenna pair for the waterfalls")
        self.pair_combo.currentIndexChanged.connect(self.refresh_views)
        self.time_step_label = self._control_label("t")
        self.time_step_slider = QSlider(Qt.Orientation.Horizontal, objectName="time_step_slider")
        self.time_step_slider.valueChanged.connect(self._on_time_step_changed)
        self.threshold_label = self._control_label("")
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal, objectName="threshold_slider")
        self.threshold_slider.setRange(0, self.SLIDER_STEPS)
        self.threshold_slider.valueChanged.connect(self._on_threshold_changed)
        self.show_track_check = QCheckBox("Track", checked=True)
        self.show_antennas_check = QCheckBox("Antennas", checked=True)
        self.remove_static_check = QCheckBox("Remove static", checked=True)
        self.remove_static_check.setToolTip("Waterfalls: subtract the static-room estimate so moving echoes stand out")
        for check in (self.show_track_check, self.show_antennas_check, self.remove_static_check):
            check.toggled.connect(self.refresh_views)

        row_widgets = [
            (self._control_label("view"), 0), (self.view_mode_combo, 0), (self._control_label("pair"), 0),
            (self.pair_combo, 0), (self.time_step_label, 0), (self.time_step_slider, 2),
            (self.threshold_label, 0), (self.threshold_slider, 1), (self.show_track_check, 0),
            (self.show_antennas_check, 0), (self.remove_static_check, 0),
        ]
        for column, (widget, stretch) in enumerate(row_widgets):
            controls_layout.addWidget(widget, 1, column)
            controls_layout.setColumnStretch(column, stretch)
        self.inspector_layout.addWidget(self.rf_controls_panel)
        self._set_threshold_slider(self.view_mode_combo.currentText())

    def _control_label(self, text: str) -> QLabel:
        return QLabel(text, objectName="inspector_control_label")
# --- END [2.2] ---

# ==============================================================================
# [2.3] build_tabs
# ==============================================================================
    def _build_tabs(self) -> None:
        self.rf_view_tabs = QTabWidget(objectName="rf_view_tabs")
        self.rf_view_tabs.setMinimumWidth(260)
        self.rf_view_tabs.currentChanged.connect(self.refresh_views)

        # --- Range-time waterfall ---
        self.range_plot, self.range_image, self.range_time_line = self._waterfall_plot("path length (m)")
        self.rf_view_tabs.addTab(self.range_plot, "Range–time")

        # --- Spectrum waterfall ---
        self.spectrum_plot, self.spectrum_image, self.spectrum_time_line = self._waterfall_plot("frequency (GHz)")
        self.rf_view_tabs.addTab(self.spectrum_plot, "Spectrum")

        # --- Slice ---
        slice_panel = QWidget()
        slice_layout = QVBoxLayout(slice_panel)
        slice_layout.setContentsMargins(0, 4, 0, 0)
        slice_row = QHBoxLayout()
        self.slice_plane_combo = QComboBox(objectName="slice_plane_combo")
        self.slice_plane_combo.addItems(SLICE_PLANES)
        self.slice_plane_combo.currentTextChanged.connect(self._on_slice_plane_changed)
        self.slice_index_label = self._control_label("")
        self.slice_index_slider = QSlider(Qt.Orientation.Horizontal, objectName="slice_index_slider")
        self.slice_index_slider.valueChanged.connect(self.refresh_views)
        slice_row.addWidget(self.slice_plane_combo)
        slice_row.addWidget(self.slice_index_label)
        slice_row.addWidget(self.slice_index_slider, 1)
        slice_layout.addLayout(slice_row)
        self.slice_plot = self._styled_plot()
        self.slice_plot.getPlotItem().setAspectLocked(True)
        self.slice_image = pg.ImageItem(axisOrder="row-major")
        self.slice_plot.addItem(self.slice_image)
        self.slice_marker = pg.ScatterPlotItem(size=14, symbol="s", pen=pg.mkPen("w", width=1.5), brush=None)
        self.slice_plot.addItem(self.slice_marker)
        self.slice_track_marker = pg.ScatterPlotItem(size=10, symbol="o", pen=None, brush=pg.mkBrush(255, 160, 30))
        self.slice_plot.addItem(self.slice_track_marker)
        self.slice_plot.scene().sigMouseClicked.connect(self._on_slice_clicked)
        slice_layout.addWidget(self.slice_plot, 1)
        slice_layout.addWidget(self._control_label("Click a voxel to inspect its material spectrum."))
        self.rf_view_tabs.addTab(slice_panel, "Slice")

        # --- Materials ---
        materials_panel = QWidget()
        materials_layout = QVBoxLayout(materials_panel)
        materials_layout.setContentsMargins(0, 4, 0, 0)
        self.material_plot = self._styled_plot()
        material_plot_item = self.material_plot.getPlotItem()
        material_plot_item.setLabel("bottom", "frequency (GHz)")
        material_plot_item.setLabel("left", "|Γ| reflectivity")
        material_plot_item.setYRange(0, 1.05)
        material_plot_item.addLegend(offset=(-4, 4), labelTextSize="8pt", verSpacing=-6)
        self.material_spectrum_points = pg.ScatterPlotItem(size=11, symbol="o", pen=pg.mkPen("w"), brush=pg.mkBrush(255, 255, 255, 180))
        material_plot_item.addItem(self.material_spectrum_points)
        materials_layout.addWidget(self.material_plot, 1)
        self.rf_material_summary_label = QLabel(objectName="rf_material_summary_label")
        self.rf_material_summary_label.setWordWrap(True)
        materials_layout.addWidget(self.rf_material_summary_label)
        self.rf_view_tabs.addTab(materials_panel, "Materials")

        self.view_splitter.addWidget(self.rf_view_tabs)

    def _styled_plot(self) -> pg.PlotWidget:
        plot = pg.PlotWidget(background=BACKGROUND_COLOR)
        plot_item = plot.getPlotItem()
        plot_item.setMenuEnabled(False)
        plot_item.hideButtons()
        for axis_name in ("left", "bottom"):
            axis = plot_item.getAxis(axis_name)
            axis.enableAutoSIPrefix(False)               # metres stay metres (no "x0.001" rescaling)
            axis.setPen(pg.mkPen(BORDER_COLOR))
            axis.setTextPen(pg.mkPen(MUTED_TEXT_COLOR))
        return plot

    def _waterfall_plot(self, x_label: str) -> tuple[pg.PlotWidget, pg.ImageItem, pg.InfiniteLine]:
        plot = self._styled_plot()
        plot.getPlotItem().setLabel("bottom", x_label)
        plot.getPlotItem().setLabel("left", "time step (newest top)")
        image = pg.ImageItem(axisOrder="row-major")
        image.setLookupTable(self.reflectivity_colormap.getLookupTable(nPts=256))
        plot.addItem(image)
        time_line = pg.InfiniteLine(angle=0, movable=True, pen=pg.mkPen("w", width=1), hoverPen=pg.mkPen("w", width=2))
        time_line.sigPositionChanged.connect(lambda line=time_line: self._on_time_line_moved(line))
        plot.addItem(time_line)
        return plot, image, time_line
# --- END [2.3] ---

# ==============================================================================
# [2.4] build_scene_view
# ==============================================================================
    def _build_scene_view(self) -> None:
        self.scene_view = None
        unavailable_reason = opengl_unavailable_reason()
        if not unavailable_reason:
            self.scene_view = gl.GLViewWidget()
            self.scene_view.setObjectName("scene_view")
            self.scene_view.setBackgroundColor(BACKGROUND_COLOR)
            self.scene_view.setMinimumWidth(240)
            self.floor_grid = gl.GLGridItem(color=(255, 255, 255, 25))
            self.region_box = gl.GLBoxItem(color=(255, 255, 255, 60))
            self.voxel_item = gl.GLScatterPlotItem(pxMode=True, size=7)
            self.voxel_item.setGLOptions("additive")
            self.antenna_item = gl.GLScatterPlotItem(pxMode=True, size=10)
            self.track_item = gl.GLLinePlotItem(color=TRACK_COLOR, width=2, antialias=True, mode="line_strip")
            self.target_item = gl.GLScatterPlotItem(pxMode=True, size=16, color=(1, 1, 1, 0.9))
            self.selected_item = gl.GLScatterPlotItem(pxMode=True, size=18, color=(1, 1, 1, 0.5))
            for item in (self.floor_grid, self.region_box, self.voxel_item, self.antenna_item,
                         self.track_item, self.target_item, self.selected_item):
                self.scene_view.addItem(item)
            self.view_splitter.addWidget(self.scene_view)
        else:
            self.scatter_unavailable_label = QLabel(
                f"3D view unavailable: {unavailable_reason}", objectName="scatter_unavailable_label",
                alignment=Qt.AlignmentFlag.AlignCenter,
            )
            self.view_splitter.addWidget(self.scatter_unavailable_label)
        self.view_splitter.setSizes([1, 1])
# --- END [2.4] ---

# ==============================================================================
# [2.5] startup_and_sources
# ==============================================================================
    def _ensure_started(self) -> None:
        if self.pipeline is None:
            self.set_sensor(self.sensor or MockRfSensor())

    def set_sensor(self, sensor: RfSensor) -> None:
        """Switch data source. Rebuilds the imaging setup if the sweep or antennas differ, then calibrates."""
        self.sensor = sensor
        needs_new_pipeline = (
            self.pipeline is None or self.pipeline.config != sensor.config
            or not np.array_equal(self.pipeline.array.tx_positions_m, sensor.array.tx_positions_m)
            or not np.array_equal(self.pipeline.array.rx_positions_m, sensor.array.rx_positions_m)
        )
        if needs_new_pipeline:
            self.pipeline = RfImagingPipeline(sensor.config, sensor.array)
            self._configure_for_pipeline()
        self.capture = RfCapture(sensor.config, sensor.array, metadata={"source": sensor.description})
        self.calibrate()

    def _configure_for_pipeline(self) -> None:
        pipeline = self.pipeline
        self.pair_combo.blockSignals(True)
        self.pair_combo.clear()
        self.pair_combo.addItem(ALL_PAIRS_LABEL)
        self.pair_combo.addItems([pipeline.array.pair_label(index) for index in range(pipeline.array.pair_count)])
        self.pair_combo.blockSignals(False)
        self._on_slice_plane_changed(self.slice_plane_combo.currentText())
        self._draw_material_library()
        if self.scene_view is not None:
            self._place_scene_frame()

    def calibrate(self) -> None:
        """Empty + metal-reference sweeps -> per-channel correction. Clears history and the recording."""
        if self.sensor is None or self.pipeline is None:
            return
        measurements = self.sensor.calibration_measurements()
        if measurements is None:
            self.pipeline.set_calibration(Calibration.identity(self.sensor.array.pair_count, self.sensor.config.bin_count))
            self.is_calibrated = False
        else:
            empty, reference, position = measurements
            self.pipeline.set_calibration(Calibration.from_reference(empty, reference, position, self.sensor.config, self.sensor.array))
            self.is_calibrated = True
            if self.capture is not None:
                self.capture.calibration_empty, self.capture.calibration_reference = empty, reference
                self.capture.calibration_position_m = position
        if self.capture is not None:
            self.capture.clear()
        self._is_following_latest = True
        self._update_source_label()
        self.refresh_views()

    @property
    def is_recording(self) -> bool:
        return not isinstance(self.sensor, CaptureReplaySensor)

    def _update_source_label(self) -> None:
        if self.sensor is None:
            return
        calibration_text = "calibrated" if self.is_calibrated else "UNCALIBRATED"
        self.rf_source_label.setText(f"{self.sensor.description}  ·  {calibration_text}")
        self.rf_source_label.setToolTip(self.rf_source_label.text())
# --- END [2.5] ---

# ==============================================================================
# [2.6] capture_files
# ==============================================================================
    def _on_save_capture(self) -> None:
        if self.capture is None or self.capture.frame_count == 0:
            self.rf_source_label.setText("Nothing recorded yet.")
            return
        file_path, _ = QFileDialog.getSaveFileName(self, "Save RF capture", "rf_capture.npz", "RF capture (*.npz)")
        if not file_path:
            return
        self.capture.metadata["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        saved_path = self.capture.save(file_path)
        if self.store is not None:
            self.store.record_capture(saved_path.resolve(), self.capture.frame_count,
                                      self.capture.config.to_dict(), self.sensor.description)
        self.rf_source_label.setText(f"Saved {self.capture.frame_count:,} frames to {saved_path.name}")

    def _fill_load_menu(self) -> None:
        """Recent captures from the database, then Browse."""
        self.load_capture_menu.clear()
        recent = self.store.recent_captures() if self.store is not None else []
        for capture in recent:
            saved_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(capture["saved_at"]))
            action = self.load_capture_menu.addAction(
                f"{Path(capture['file_path']).name}  ·  {capture['frame_count']:,} frames  ·  {saved_text}"
            )
            action.triggered.connect(lambda _checked=False, path=capture["file_path"]: self.load_capture(path))
        if recent:
            self.load_capture_menu.addSeparator()
        self.load_capture_menu.addAction("Browse…").triggered.connect(self._on_browse_capture)

    def _on_browse_capture(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "Load RF capture", "", "RF capture (*.npz)")
        if file_path:
            self.load_capture(file_path)

    def load_capture(self, file_path: str) -> None:
        """Replay a saved capture file."""
        try:
            capture = RfCapture.load(file_path)
            self.set_sensor(CaptureReplaySensor(capture, Path(file_path).name))
        except (OSError, ValueError, KeyError) as error:
            self.rf_source_label.setText(f"Could not load capture: {error}")
# --- END [2.6] ---

# ==============================================================================
# [2.7] live_loop
# ==============================================================================
    def set_live(self, is_live: bool) -> None:
        self._is_live = is_live
        self.live_toggle_btn.blockSignals(True)
        self.live_toggle_btn.setChecked(is_live)
        self.live_toggle_btn.blockSignals(False)
        self.live_toggle_btn.setText("❚❚ Pause" if is_live else "▶ Live")
        self._update_live_timer()

    def _on_live_tick(self) -> None:
        frame = self.sensor.read_frame() if self.sensor else None
        if frame is None:
            return
        started = time.perf_counter()
        if self.is_recording:
            self.capture.append(frame)
        self.pipeline.process(frame)
        self._process_ms = (time.perf_counter() - started) * 1000
        if not self._is_following_latest:
            # Keep showing the same moment as the history scrolls by one step.
            self.time_step_slider.blockSignals(True)
            self.time_step_slider.setValue(max(self.time_step_slider.minimum(), self.time_step_slider.value() - 1))
            self.time_step_slider.blockSignals(False)
        self._frame_times = [stamp for stamp in self._frame_times if started - stamp < 1.0] + [started]
        self.refresh_views()

    def _update_live_timer(self) -> None:
        if self._is_live and self.isVisible() and self.pipeline is not None:
            self.live_timer.start()
        else:
            self.live_timer.stop()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._ensure_started()
        self._update_live_timer()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._update_live_timer()
# --- END [2.7] ---

# ==============================================================================
# [2.8] refresh_views
# ==============================================================================
    def refresh_views(self) -> None:
        if self.pipeline is None:
            return
        self._sync_time_slider()
        time_step = self.time_step_slider.value()
        self.time_step_label.setText(f"t {time_step - self.pipeline.history_length + 1:+d}")
        current_tab = self.rf_view_tabs.currentIndex()
        if current_tab in (0, 1):
            self._refresh_waterfall(is_range=current_tab == 0, time_step=time_step)
        elif current_tab == 2:
            self._refresh_slice(time_step)
        else:
            self._refresh_materials_tab()
        if self.scene_view is not None:
            self._refresh_scene(time_step)
        self._refresh_stats()

    def _sync_time_slider(self) -> None:
        """Slider covers only filled history slots; follows the newest frame unless moved away."""
        history_length = self.pipeline.history_length
        oldest_filled = history_length - max(self.pipeline.filled_count, 1)
        self.time_step_slider.blockSignals(True)
        self.time_step_slider.setRange(oldest_filled, history_length - 1)
        if self._is_following_latest:
            self.time_step_slider.setValue(history_length - 1)
        self.time_step_slider.blockSignals(False)

    def _view_volume(self, time_step: int) -> np.ndarray:
        mode = self.view_mode_combo.currentText()
        source = {VIEW_REFLECTIVITY: self.pipeline.reflectivity, VIEW_MOTION: self.pipeline.motion,
                  VIEW_MATERIALS: self.pipeline.materials}[mode]
        return source.volume(t=time_step)

    def _threshold_mask(self, volume: np.ndarray) -> np.ndarray:
        mode = self.view_mode_combo.currentText()
        threshold = self._thresholds[mode]
        if mode == VIEW_MATERIALS:
            return (volume != NO_MATERIAL_ID) & (self.pipeline.material_confidence >= threshold)
        if mode == VIEW_MOTION:
            return volume >= threshold * max(float(volume.max()), 0.02)
        return volume >= threshold

    def _refresh_stats(self) -> None:
        now = time.monotonic()
        if now - self._last_stats_time < 0.4:
            return
        self._last_stats_time = now
        track = self.pipeline.track_positions_m[-1]
        track_text = "no target" if np.isnan(track).any() else f"target ({track[0]:+.2f}, {track[1]:.2f}, {track[2]:+.2f}) m"
        recording_text = f"● rec {self.capture.frame_count:,}  ·  " if self.is_recording and self.capture else ""
        self.rf_stats_label.setText(
            f"{recording_text}{len(self._frame_times)} fps  ·  {self._process_ms:.0f} ms  ·  {track_text}"
        )
        self._update_source_label()
# --- END [2.8] ---

# ==============================================================================
# [2.9] refresh_tabs
# ==============================================================================
    def _selected_pair_sweeps(self) -> np.ndarray:
        """(history, bins) complex for the chosen pair, or the per-pair magnitude mean for 'All pairs'."""
        pair_index = self.pair_combo.currentIndex() - 1
        sweeps = self.pipeline.sweeps
        background = self.pipeline.clutter_filter.background
        if self.remove_static_check.isChecked() and background is not None:
            sweeps = sweeps - background[None, :, :]     # latest static estimate; older rows approximate
        return sweeps[:, pair_index, :] if pair_index >= 0 else sweeps

    def _refresh_waterfall(self, is_range: bool, time_step: int) -> None:
        history_length = self.pipeline.history_length
        sweeps = self._selected_pair_sweeps()
        if is_range:
            profiles, path_axis_m = range_profiles(sweeps, self.pipeline.config)
            magnitude = profiles.mean(axis=1) if profiles.ndim == 3 else profiles
            x_start, x_width = 0.0, float(path_axis_m[-1])
            image, time_line = self.range_image, self.range_time_line
        else:
            magnitude = np.abs(sweeps).mean(axis=1) if sweeps.ndim == 3 else np.abs(sweeps)
            config = self.pipeline.config
            x_start, x_width = config.start_hz / 1e9, config.bandwidth_hz / 1e9
            image, time_line = self.spectrum_image, self.spectrum_time_line
        decibels = 20 * np.log10(magnitude + 1e-9)
        peak = float(decibels[-max(self.pipeline.filled_count, 1):].max())
        image.setImage(decibels, levels=(peak - self.DB_RANGE, peak), autoLevels=False)
        image.setRect(QRectF(x_start, 0, x_width, history_length))
        time_line.blockSignals(True)
        time_line.setValue(time_step + 0.5)
        time_line.blockSignals(False)

    def _refresh_slice(self, time_step: int) -> None:
        grid = self.pipeline.grid
        fixed_axis = SLICE_PLANES[self.slice_plane_combo.currentText()]
        slice_index = self.slice_index_slider.value()
        self.slice_index_label.setText(f"{'xyz'[fixed_axis]} = {grid.axis_m(fixed_axis)[slice_index]:+.2f} m")
        volume = self._view_volume(time_step)
        plane = np.take(volume, slice_index, axis=fixed_axis).T          # rows = vertical axis
        horizontal_axis, vertical_axis = [axis for axis in range(3) if axis != fixed_axis]
        mode = self.view_mode_combo.currentText()
        if mode == VIEW_MATERIALS:
            self.slice_image.setLookupTable(None)
            self.slice_image.setImage(self._material_rgb(plane), autoLevels=False, levels=(0, 255))
        else:
            colormap = self.motion_colormap if mode == VIEW_MOTION else self.reflectivity_colormap
            self.slice_image.setLookupTable(colormap.getLookupTable(nPts=256))
            high = 1.0 if mode == VIEW_REFLECTIVITY else max(float(volume.max()), 0.02)
            self.slice_image.setImage(plane, levels=(0.0, high), autoLevels=False)
        spacing = grid.spacing_m
        h_axis, v_axis = grid.axis_m(horizontal_axis), grid.axis_m(vertical_axis)
        self.slice_image.setRect(QRectF(h_axis[0] - spacing / 2, v_axis[0] - spacing / 2, len(h_axis) * spacing, len(v_axis) * spacing))
        self.slice_plot.getPlotItem().setLabel("bottom", f"{'xyz'[horizontal_axis]} (m)")
        self.slice_plot.getPlotItem().setLabel("left", f"{'xyz'[vertical_axis]} (m)")

        if self.selected_voxel is not None and self.selected_voxel[fixed_axis] == slice_index:
            selected_m = grid.index_to_m(self.selected_voxel)
            self.slice_marker.setData([selected_m[horizontal_axis]], [selected_m[vertical_axis]])
        else:
            self.slice_marker.setData([], [])
        track = self.pipeline.track_positions_m[time_step]
        if self.show_track_check.isChecked() and not np.isnan(track).any():
            self.slice_track_marker.setData([track[horizontal_axis]], [track[vertical_axis]])
        else:
            self.slice_track_marker.setData([], [])

    def _material_rgb(self, material_plane: np.ndarray) -> np.ndarray:
        palette = np.array([EMPTY_MATERIAL_RGB] + [tuple(int(255 * c) for c in m.color) for m in MATERIALS], np.uint8)
        return palette[material_plane.astype(int) + 1]                  # NO_MATERIAL_ID (-1) -> index 0

    def _draw_material_library(self) -> None:
        plot_item = self.material_plot.getPlotItem()
        frequencies_hz = np.linspace(self.pipeline.config.start_hz, self.pipeline.config.stop_hz, 60)
        for material in MATERIALS:
            color = tuple(int(255 * c) for c in material.color)
            plot_item.plot(frequencies_hz / 1e9, np.abs(material.reflection_coefficient(frequencies_hz)),
                           pen=pg.mkPen(color, width=1.5), name=material.name)

    def _refresh_materials_tab(self) -> None:
        pipeline = self.pipeline
        lines = []
        if self.selected_voxel is None:
            self.material_spectrum_points.setData([], [])
            lines.append("Select a voxel in the Slice tab to compare its spectrum with the library.")
        else:
            band_magnitudes = pipeline.band_reflectivity[(slice(None), *self.selected_voxel)]
            position_m = pipeline.grid.index_to_m(self.selected_voxel)
            position_text = f"({position_m[0]:+.1f}, {position_m[1]:.1f}, {position_m[2]:+.1f}) m"
            material_id = int(pipeline.static_material_ids[self.selected_voxel])
            if material_id == NO_MATERIAL_ID:
                self.material_spectrum_points.setData([], [])
                lines.append(f"Voxel {position_text}: no reflector found here.")
            else:
                self.material_spectrum_points.setData(pipeline.backprojector.band_centers_hz / 1e9, band_magnitudes)
                confidence = pipeline.material_confidence[self.selected_voxel]
                lines.append(f"Voxel {position_text}: <b>{MATERIALS[material_id].name}</b> · confidence {confidence:.2f} "
                             f"· sub-band |Γ| {np.array2string(band_magnitudes, precision=2)}")

        counts = Counter(int(i) for i in pipeline.static_material_ids.ravel() if i != NO_MATERIAL_ID)
        summary = ", ".join(f"{MATERIALS[i].name} {n}" for i, n in counts.most_common()) or "none yet"
        lines.append(f"Static reflectors (pass {pipeline.static_map_count}): {summary}")
        recent = pipeline.target_material_ids[-self.TARGET_VOTE_FRAMES:]
        recent = recent[recent != NO_MATERIAL_ID]
        if len(recent):
            votes = Counter(int(i) for i in recent).most_common(2)
            vote_text = ", ".join(f"{MATERIALS[i].name} {100 * n / len(recent):.0f}%" for i, n in votes)
            lines.append(f"Moving target (last ~2 s): {vote_text} — extended bodies read high; treat as water-rich vs not.")
        self.rf_material_summary_label.setText("<br>".join(lines))
# --- END [2.9] ---

# ==============================================================================
# [2.10] refresh_scene
# ==============================================================================
    def _place_scene_frame(self) -> None:
        """Floor grid, region box and camera around the voxel grid (done once per pipeline)."""
        grid = self.pipeline.grid
        lows = np.array([grid.x_range_m[0], grid.y_range_m[0], grid.z_range_m[0]])
        highs = np.array([grid.x_range_m[1], grid.y_range_m[1], grid.z_range_m[1]])
        sizes = highs - lows
        self.region_box.resetTransform()
        self.region_box.setSize(*sizes)
        self.region_box.translate(*lows)
        self.floor_grid.resetTransform()
        self.floor_grid.setSize(sizes[0] + 0.4, highs[1] + 0.2)
        self.floor_grid.setSpacing(0.2, 0.2)
        self.floor_grid.translate(0.0, (highs[1] + 0.2) / 2, lows[2])
        center = (lows + np.array([highs[0], highs[1], highs[2]])) / 2
        center[1] = highs[1] / 2                                         # include the antennas at y = 0
        self.scene_view.opts["center"] = pg.Vector(*center)
        self.scene_view.setCameraPosition(distance=float(sizes.max()) * 2.4, elevation=22, azimuth=-60)

    def _refresh_scene(self, time_step: int) -> None:
        pipeline = self.pipeline
        mode = self.view_mode_combo.currentText()
        volume = self._view_volume(time_step)
        mask = self._threshold_mask(volume)
        positions = pipeline.grid.centers_m()[mask.ravel()]
        values = volume[mask]
        if len(positions):
            if mode == VIEW_MATERIALS:
                colors = np.array([(*MATERIALS[int(i)].color, 0.95) for i in values])
            else:
                high = 1.0 if mode == VIEW_REFLECTIVITY else max(float(volume.max()), 0.02)
                normalized = np.clip(values / high, 0, 1)
                colormap = self.motion_colormap if mode == VIEW_MOTION else self.reflectivity_colormap
                colors = colormap.map(normalized, mode="float")
                colors[:, 3] = 0.3 + 0.7 * normalized
            self.voxel_item.setData(pos=positions, color=colors)
        self.voxel_item.setVisible(len(positions) > 0)

        array = pipeline.array
        self.antenna_item.setVisible(self.show_antennas_check.isChecked())
        if self.show_antennas_check.isChecked():
            antenna_positions = np.vstack([array.tx_positions_m, array.rx_positions_m])
            antenna_colors = np.array([TX_COLOR] * len(array.tx_positions_m) + [RX_COLOR] * len(array.rx_positions_m))
            self.antenna_item.setData(pos=antenna_positions, color=antenna_colors)

        track = pipeline.track_positions_m[: time_step + 1]
        track = track[~np.isnan(track).any(axis=1)]
        show_track = self.show_track_check.isChecked() and len(track) > 1
        self.track_item.setVisible(show_track)
        if show_track:
            self.track_item.setData(pos=track)
        current = pipeline.track_positions_m[time_step]
        show_target = self.show_track_check.isChecked() and not np.isnan(current).any()
        self.target_item.setVisible(show_target)
        if show_target:
            self.target_item.setData(pos=current[None, :])

        self.selected_item.setVisible(self.selected_voxel is not None)
        if self.selected_voxel is not None:
            self.selected_item.setData(pos=pipeline.grid.index_to_m(self.selected_voxel)[None, :])
# --- END [2.10] ---

# ==============================================================================
# [2.11] control_handlers
# ==============================================================================
    def _on_view_mode_changed(self, mode: str) -> None:
        self._set_threshold_slider(mode)
        self.refresh_views()

    def _set_threshold_slider(self, mode: str) -> None:
        self.threshold_slider.blockSignals(True)
        self.threshold_slider.setValue(round(self._thresholds[mode] * self.SLIDER_STEPS))
        self.threshold_slider.blockSignals(False)
        self._update_threshold_label()

    def _on_threshold_changed(self, slider_value: int) -> None:
        self._thresholds[self.view_mode_combo.currentText()] = slider_value / self.SLIDER_STEPS
        self._update_threshold_label()
        self.refresh_views()

    def _update_threshold_label(self) -> None:
        mode = self.view_mode_combo.currentText()
        value = self._thresholds[mode]
        prefix = {VIEW_REFLECTIVITY: "|Γ| ≥", VIEW_MOTION: "≥ peak ×", VIEW_MATERIALS: "conf ≥"}[mode]
        self.threshold_label.setText(f"{prefix} {value:.2f}")

    def _on_time_step_changed(self, time_step: int) -> None:
        self._is_following_latest = time_step == self.time_step_slider.maximum()
        self.refresh_views()

    def _on_time_line_moved(self, time_line: pg.InfiniteLine) -> None:
        self.time_step_slider.setValue(int(time_line.value()))

    def _on_slice_plane_changed(self, plane_name: str) -> None:
        if self.pipeline is None:
            return
        fixed_axis = SLICE_PLANES[plane_name]
        axis_size = self.pipeline.grid.shape[fixed_axis]
        self.slice_index_slider.blockSignals(True)
        self.slice_index_slider.setRange(0, axis_size - 1)
        self.slice_index_slider.setValue(
            self.selected_voxel[fixed_axis] if self.selected_voxel else axis_size // 2
        )
        self.slice_index_slider.blockSignals(False)
        self.refresh_views()
        self.slice_plot.getPlotItem().autoRange(padding=0.02)

    def _on_slice_clicked(self, event) -> None:
        if self.pipeline is None or event.button() != Qt.MouseButton.LeftButton:
            return
        view_point = self.slice_plot.getPlotItem().vb.mapSceneToView(event.scenePos())
        fixed_axis = SLICE_PLANES[self.slice_plane_combo.currentText()]
        horizontal_axis, vertical_axis = [axis for axis in range(3) if axis != fixed_axis]
        position_m = np.zeros(3)
        position_m[horizontal_axis], position_m[vertical_axis] = view_point.x(), view_point.y()
        voxel = list(self.pipeline.grid.m_to_index(position_m))
        voxel[fixed_axis] = self.slice_index_slider.value()
        self.selected_voxel = tuple(voxel)
        self.refresh_views()
# --- END [2.11] ---
