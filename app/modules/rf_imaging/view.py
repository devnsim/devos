# ==============================================================================
# FILE: app/modules/rf_imaging/view.py
# OVERVIEW: RF imaging module: live 2.5-6 GHz sensor data -> 3D reflectivity, motion, materials, track,
#           shown as a canvas of blocks (3D scene, waterfalls, slices, materials).
# PURPOSE:
#   1. Reads frames from an RfSensor (mock now, real hardware or a saved capture later), records them
#      into an RfCapture, and runs them through RfImagingPipeline.
#   2. Module toolbar: live/pause, calibrate, save / load captures, back to mock; view mode, timeline,
#      threshold and overlay switches shared by every block.
#   3. Blocks (blocks.py): the views, which the user can split, rearrange, resize, close or replace.
#   4. Shared state for blocks: time_step(), view_mode(), view_volume(), threshold_mask(), selected voxel.
# CONTRACT:
#   - Requires: numpy, app.modules.base.ModuleView, app.modules.rf_imaging (sensor, imaging, store, blocks),
#               app.ui.ui_tools, QSS selectors #rf_inspector_panel, #module_controls_panel,
#               #module_source_label, #module_stats_label, #control_label, #live_toggle_btn
#   - Exposes: RfInspectorModule(sensor=None, parent=None, context=None) (ModuleView); set_sensor(); calibrate();
#              load_capture(item_id); refresh_views(); set_time_step(); set_selected_voxel(); attributes
#              sensor, pipeline, capture, selected_voxel, block_canvas
# NOTES:
#   - [2026-11-17] Library access goes through the Knowledge engine (app.clients.library), not the content store (Phase 4).
#   - [2026-11-15] Created. Heavy setup (steering matrices, ~0.5 s / ~80 MB) waits until the module is
#                  first shown, so app start-up stays fast.
#   - [2026-11-15] Raw frames are recorded (not images) so saved tests can be re-processed later with
#                  better maths. Replays are not re-recorded.
#   - [2026-11-15] With a ModuleContext, saved captures are indexed in SQLite (store.py) and the
#                  Load button offers a menu of recent captures plus Browse.
#   - [2026-11-15] "Remove static" subtracts the latest clutter background from every history row
#                  (an approximation for older rows; the background drifts slowly).
#   - [2026-11-15] Moved from app/ui/modules/rf_inspector_module.py (module apps live in app/modules/<name>/).
#   - [2026-11-15] Captures live in the user's content library: Save stores "RF test <time>" without a file
#                  dialog; Load lists library captures (+ import a file). rf_imaging/store.py (capture index)
#                  was removed - the content library replaces it. Pipeline build, calibration, source changes,
#                  saves and loads are announced in the activity log.
#   - [2026-11-15] Rebuilt on ModuleView with blocks (health pass + modules-as-canvases): tabs and the
#                  fixed splitter replaced by blocks.py; pair choice moved into each waterfall block.
#   - [2026-11-15] Module toolbar's top row ends with reserve_controls_space(): module tiles now carry the
#                  fading corner controls (no title bar), which must not cover the toolbar.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - history slot / time step: Index into the pipeline's rolling history (newest = history_length - 1).
# - selected voxel: (ix, iy, iz) picked in a Slice block; inspected by Materials blocks, marked in 3D.
# - view mode: What 3D scenes and slices show: reflectivity (|rho|), motion, or materials.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. RF_INSPECTOR_MODULE
#    2.1 init_module
#    2.2 build_controls
#    2.3 sources_and_calibration
#    2.4 capture_files
#    2.5 live_loop
#    2.6 shared_state_for_blocks
#    2.7 refresh
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QSlider, QVBoxLayout,
    QWidget,
)

from app.data.em_materials import NO_MATERIAL_ID
from app.data.activity_log import get_activity_log
from app.clients.library import library
from app.modules.base import ModuleView
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.modules.rf_imaging.blocks import (
    DEFAULT_BLOCKS, VIEW_MATERIALS, VIEW_MODES, VIEW_MOTION, VIEW_REFLECTIVITY, block_specs,
)
from app.modules.rf_imaging.imaging import Calibration, RfImagingPipeline
from app.modules.rf_imaging.sensor import CaptureReplaySensor, MockRfSensor, RfCapture, RfSensor
from app.ui.ui_tools import control_label
# --- END [1.1] ---


# ==============================================================================
# [2.1] init_module
# ==============================================================================
class RfInspectorModule(ModuleView):
    """Live RF imaging module (see file header)."""

    LIVE_INTERVAL_MS = 50
    SLIDER_STEPS = 100
    DEFAULT_THRESHOLDS = {VIEW_REFLECTIVITY: 0.3, VIEW_MOTION: 0.45, VIEW_MATERIALS: 0.0}

    def __init__(self, sensor: RfSensor | None = None, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, context, "rf_inspector_panel")
        self.log = get_activity_log()
        self.sensor: RfSensor | None = sensor
        self.pipeline: RfImagingPipeline | None = None
        self.capture: RfCapture | None = None
        self.is_calibrated = False
        self.selected_voxel: tuple[int, int, int] | None = None
        self._is_following_latest = True
        self._thresholds = dict(self.DEFAULT_THRESHOLDS)
        self._frame_times: list[float] = []
        self._process_ms = 0.0
        self._last_stats_time = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self._build_controls())
        self.block_canvas = self.build_block_canvas(block_specs(self), DEFAULT_BLOCKS)
        layout.addWidget(self.block_canvas, 1)
        self.module_source_label.setText("Starting when first shown…")
# --- END [2.1] ---

# ==============================================================================
# [2.2] build_controls
# ==============================================================================
    def _build_controls(self) -> QWidget:
        panel = QWidget(objectName="module_controls_panel")
        rows = QVBoxLayout(panel)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(4)

        top = QHBoxLayout()
        top.addWidget(self.setup_live(self.LIVE_INTERVAL_MS, self._on_live_tick))
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
        self.module_source_label = QLabel(objectName="module_source_label")
        self.module_source_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.module_stats_label = QLabel(objectName="module_stats_label")
        for widget in (self.calibrate_btn, self.save_capture_btn, self.load_capture_btn, self.mock_source_btn):
            top.addWidget(widget)
        top.addSpacing(8)
        top.addWidget(self.module_source_label, 1)
        top.addWidget(self.module_stats_label)
        reserve_controls_space(top)
        rows.addLayout(top)

        bottom = QHBoxLayout()
        self.view_mode_combo = QComboBox(objectName="view_mode_combo")
        self.view_mode_combo.addItems(VIEW_MODES)
        self.view_mode_combo.currentTextChanged.connect(self._on_view_mode_changed)
        self.time_step_label = control_label("t")
        self.time_step_slider = QSlider(Qt.Orientation.Horizontal, objectName="time_step_slider")
        self.time_step_slider.valueChanged.connect(self._on_time_step_slider)
        self.threshold_label = control_label()
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal, objectName="threshold_slider")
        self.threshold_slider.setRange(0, self.SLIDER_STEPS)
        self.threshold_slider.valueChanged.connect(self._on_threshold_changed)
        self.show_track_check = QCheckBox("Track", checked=True)
        self.show_antennas_check = QCheckBox("Antennas", checked=True)
        self.remove_static_check = QCheckBox("Remove static", checked=True,
                                             toolTip="Waterfalls: subtract the static-room estimate so moving echoes stand out")
        for check in (self.show_track_check, self.show_antennas_check, self.remove_static_check):
            check.toggled.connect(self.refresh_views)
        for widget, stretch in ((control_label("view"), 0), (self.view_mode_combo, 0), (self.time_step_label, 0),
                                (self.time_step_slider, 2), (self.threshold_label, 0), (self.threshold_slider, 1),
                                (self.show_track_check, 0), (self.show_antennas_check, 0), (self.remove_static_check, 0)):
            bottom.addWidget(widget, stretch)
        rows.addLayout(bottom)
        self._set_threshold_slider(self.view_mode_combo.currentText())
        return panel
# --- END [2.2] ---

# ==============================================================================
# [2.3] sources_and_calibration
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
        self.log.info("rf_imaging", f"Data source: {sensor.description}")
        if needs_new_pipeline:
            with self.log.step("rf_imaging", f"Build imaging pipeline ({sensor.array.pair_count} pairs × "
                                             f"{sensor.config.bin_count} bins, 3D grid + steering matrices)"):
                self.pipeline = RfImagingPipeline(sensor.config, sensor.array)
            for block in self.block_canvas.blocks():
                block.on_pipeline_ready()
        self.capture = RfCapture(sensor.config, sensor.array, metadata={"source": sensor.description})
        self.calibrate()
        self._update_live_timer()

    def calibrate(self) -> None:
        """Empty + metal-reference sweeps -> per-channel correction. Clears history and the recording."""
        if self.sensor is None or self.pipeline is None:
            return
        measurements = self.sensor.calibration_measurements()
        if measurements is None:
            self.pipeline.set_calibration(Calibration.identity(self.sensor.array.pair_count, self.sensor.config.bin_count))
            self.is_calibrated = False
            self.log.warning("rf_imaging", "No calibration data from this source: images are uncalibrated")
        else:
            empty, reference, position = measurements
            with self.log.step("rf_imaging", "Calibrate (empty sweep + metal reference)"):
                self.pipeline.set_calibration(Calibration.from_reference(empty, reference, position, self.sensor.config,
                                                                         self.sensor.array))
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
        if self.sensor is not None:
            self.module_source_label.setText(f"{self.sensor.description}  ·  "
                                             f"{'calibrated' if self.is_calibrated else 'UNCALIBRATED'}")
            self.module_source_label.setToolTip(self.module_source_label.text())
# --- END [2.3] ---

# ==============================================================================
# [2.4] capture_files
# ==============================================================================
    def _on_save_capture(self) -> None:
        """Save the recorded test into the user's library (no file dialog: DevOS keeps it)."""
        if self.capture is None or self.capture.frame_count == 0:
            self.log.warning("rf_imaging", "Nothing recorded yet to save")
            return
        store = library()
        name = store.unique_name("capture", time.strftime("RF test %Y-%m-%d %H:%M"))
        self.capture.metadata["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with self.log.step("rf_imaging", f"Save {self.capture.frame_count:,} frames as “{name}”"):
            path = self.capture.save(store.new_path("capture", ".npz"))
            store.register_path("capture", name, path, {"frames": self.capture.frame_count,
                                                          "source": self.sensor.description,
                                                          "sweep": self.capture.config.to_dict()})

    def _fill_load_menu(self) -> None:
        """Captures in the user's library, newest first, then import from a file."""
        self.load_capture_menu.clear()
        store = library()
        captures = store.items("capture")[:12]
        for item in captures:
            saved_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(item.created_at))
            frames = item.metadata.get("frames")
            action = self.load_capture_menu.addAction(f"{item.name}  ·  {f'{frames:,} frames  ·  ' if frames else ''}{saved_text}")
            action.triggered.connect(lambda _checked=False, item_id=item.item_id: self.load_capture(item_id))
        if not captures:
            self.load_capture_menu.addAction("No saved tests yet").setEnabled(False)
        self.load_capture_menu.addSeparator()
        self.load_capture_menu.addAction("Import a capture file…").triggered.connect(self._on_import_capture)

    def _on_import_capture(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "Import RF capture", "", "RF capture (*.npz)")
        if file_path:
            item = library().add_file("capture", file_path)
            self.load_capture(item.item_id)

    def load_capture(self, item_id: int) -> None:
        """Replay a capture from the user's library."""
        store = library()
        item = store.get(item_id)
        if item is None:
            self.log.warning("rf_imaging", f"Capture #{item_id} is no longer in the library")
            return
        try:
            with self.log.step("rf_imaging", f"Load capture “{item.name}”"):
                capture = RfCapture.load(store.path(item))
        except (OSError, ValueError, KeyError):
            return                                           # step() logged the reason
        self.set_sensor(CaptureReplaySensor(capture, item.name))
# --- END [2.4] ---

# ==============================================================================
# [2.5] live_loop
# ==============================================================================
    def can_run_live(self) -> bool:
        return self.pipeline is not None

    def showEvent(self, event) -> None:
        self._ensure_started()                     # build the imaging setup on first show
        super().showEvent(event)

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
# --- END [2.5] ---

# ==============================================================================
# [2.6] shared_state_for_blocks
# ==============================================================================
    def time_step(self) -> int:
        return self.time_step_slider.value()

    def set_time_step(self, time_step: int) -> None:
        self.time_step_slider.setValue(time_step)           # clamped by the slider's range

    def view_mode(self) -> str:
        return self.view_mode_combo.currentText()

    def view_volume(self, time_step: int) -> np.ndarray:
        source = {VIEW_REFLECTIVITY: self.pipeline.reflectivity, VIEW_MOTION: self.pipeline.motion,
                  VIEW_MATERIALS: self.pipeline.materials}[self.view_mode()]
        return source.volume(t=time_step)

    def threshold_mask(self, volume: np.ndarray) -> np.ndarray:
        mode = self.view_mode()
        threshold = self._thresholds[mode]
        if mode == VIEW_MATERIALS:
            return (volume != NO_MATERIAL_ID) & (self.pipeline.material_confidence >= threshold)
        if mode == VIEW_MOTION:
            return volume >= threshold * max(float(volume.max()), 0.02)
        return volume >= threshold

    def show_track(self) -> bool:
        return self.show_track_check.isChecked()

    def show_antennas(self) -> bool:
        return self.show_antennas_check.isChecked()

    def remove_static(self) -> bool:
        return self.remove_static_check.isChecked()

    def set_selected_voxel(self, voxel: tuple[int, int, int]) -> None:
        self.selected_voxel = voxel
        self.refresh_views()
# --- END [2.6] ---

# ==============================================================================
# [2.7] refresh
# ==============================================================================
    def refresh_views(self) -> None:
        if self.pipeline is None or not hasattr(self, "block_canvas"):
            return
        history_length = self.pipeline.history_length
        self.time_step_slider.blockSignals(True)          # range covers only filled history slots
        self.time_step_slider.setRange(history_length - max(self.pipeline.filled_count, 1), history_length - 1)
        if self._is_following_latest:
            self.time_step_slider.setValue(history_length - 1)
        self.time_step_slider.blockSignals(False)
        self.time_step_label.setText(f"t {self.time_step() - history_length + 1:+d}")
        for block in self.block_canvas.blocks():
            block.refresh()
        self._refresh_stats()

    def _refresh_stats(self) -> None:
        now = time.monotonic()
        if now - self._last_stats_time < 0.4:
            return
        self._last_stats_time = now
        track = self.pipeline.track_positions_m[-1]
        track_text = "no target" if np.isnan(track).any() else f"target ({track[0]:+.2f}, {track[1]:.2f}, {track[2]:+.2f}) m"
        recording_text = f"● rec {self.capture.frame_count:,}  ·  " if self.is_recording and self.capture else ""
        self.module_stats_label.setText(f"{recording_text}{len(self._frame_times)} fps  ·  {self._process_ms:.0f} ms  ·  {track_text}")
        self._update_source_label()

    def _on_time_step_slider(self, time_step: int) -> None:
        self._is_following_latest = time_step == self.time_step_slider.maximum()
        self.refresh_views()

    def _on_view_mode_changed(self, mode: str) -> None:
        self._set_threshold_slider(mode)
        self.refresh_views()

    def _set_threshold_slider(self, mode: str) -> None:
        self.threshold_slider.blockSignals(True)
        self.threshold_slider.setValue(round(self._thresholds[mode] * self.SLIDER_STEPS))
        self.threshold_slider.blockSignals(False)
        self._update_threshold_label()

    def _on_threshold_changed(self, slider_value: int) -> None:
        self._thresholds[self.view_mode()] = slider_value / self.SLIDER_STEPS
        self._update_threshold_label()
        self.refresh_views()

    def _update_threshold_label(self) -> None:
        mode = self.view_mode()
        prefix = {VIEW_REFLECTIVITY: "|Γ| ≥", VIEW_MOTION: "≥ peak ×", VIEW_MATERIALS: "conf ≥"}[mode]
        self.threshold_label.setText(f"{prefix} {self._thresholds[mode]:.2f}")
# --- END [2.7] ---
