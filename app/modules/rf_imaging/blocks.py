# ==============================================================================
# FILE: app/modules/rf_imaging/blocks.py
# OVERVIEW: The RF imaging module's views as blocks (split / rearrange / resize / close / replace).
# PURPOSE:
#   1. SceneBlock: 3D voxels (reflectivity / motion / materials), antennas, scanned box, target track,
#      selected voxel.
#   2. WaterfallBlock: range-time (path length) or spectrum (frequency) waterfall for its own antenna pair.
#   3. SliceBlock: a 2D cut (its own plane + position); clicking a voxel selects it module-wide.
#   4. MaterialsBlock: the selected voxel's sub-band spectrum vs the material library + scene summary.
# CONTRACT:
#   - Requires: numpy, pyqtgraph (+ opengl), app.data.em_materials, app.modules.rf_imaging.imaging
#               (range_profiles), app.ui (gl_support, plot_tools, ui_tools, theme). Each block reads the
#               module's shared state: pipeline, time_step, view mode, thresholds, selected_voxel, overlays.
#   - Exposes: SceneBlock, WaterfallBlock, SliceBlock, MaterialsBlock, block_specs(module), DEFAULT_BLOCKS,
#              VIEW_REFLECTIVITY / VIEW_MOTION / VIEW_MATERIALS, VIEW_MODES
# NOTES:
#   - [2026-11-15] Created from the old tabbed RfInspectorModule (health pass + modules-as-canvases):
#                  every view is now a block; pair choice moved into each waterfall block and the slice
#                  plane into each slice block (saved via block_state()).
#   - [2026-11-15] Blocks may exist before the imaging pipeline (it is built on first show): refresh()
#                  does nothing until then and on_pipeline_ready() does the pipeline-dependent setup.
#   - [2026-11-15] Block top rows end with reserve_controls_space() (room for the corner controls).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - DB_RANGE (float | dB): Waterfall colour range below the peak (default: 45).
# - view mode (str): What the 3D view and slices show: Reflectivity, Motion or Materials.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS_AND_CONSTANTS
#    1.1 imports
# 2. BLOCKS
#    2.1 scene_block
#    2.2 waterfall_block
#    2.3 slice_block
#    2.4 materials_block
# 3. REGISTRY
#    3.1 block_specs
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from collections import Counter

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from app.data.em_materials import MATERIALS, NO_MATERIAL_ID
from app.modules.rf_imaging.imaging import range_profiles
from app.ui.canvas.block_canvas import BlockSpec, reserve_controls_space
from app.ui.gl_support import gl, opengl_unavailable_reason
from app.ui.plot_tools import styled_plot
from app.ui.theme import BACKGROUND_COLOR
from app.ui.ui_tools import control_label

VIEW_REFLECTIVITY, VIEW_MOTION, VIEW_MATERIALS = "Reflectivity", "Motion", "Materials"
VIEW_MODES = (VIEW_REFLECTIVITY, VIEW_MOTION, VIEW_MATERIALS)
SLICE_PLANES = {"Top (x–y)": 2, "Front (x–z)": 1, "Side (y–z)": 0}       # plane -> axis held fixed
ALL_PAIRS_LABEL = "All pairs (mean)"
TX_COLOR, RX_COLOR = (1.0, 0.55, 0.2, 1.0), (0.3, 0.8, 1.0, 1.0)
TRACK_COLOR = (1.0, 0.6, 0.1, 0.9)
EMPTY_MATERIAL_RGB = (40, 40, 40)
REFLECTIVITY_COLORMAP, MOTION_COLORMAP = pg.colormap.get("viridis"), pg.colormap.get("inferno")
DB_RANGE = 45.0
TARGET_VOTE_FRAMES = 40                                                    # ~2 s of target estimates
DEFAULT_BLOCKS = {"split": "h", "sizes": [1, 1], "children": [
    {"split": "v", "sizes": [1, 1], "children": [{"kind": "range_time"}, {"kind": "slice"}]},
    {"kind": "scene"},
]}


def _volume_colors(module, volume: np.ndarray, values: np.ndarray) -> np.ndarray:
    """RGBA per value for the module's view mode."""
    mode = module.view_mode()
    if mode == VIEW_MATERIALS:
        return np.array([(*MATERIALS[int(i)].color, 0.95) for i in values])
    high = 1.0 if mode == VIEW_REFLECTIVITY else max(float(volume.max()), 0.02)
    normalized = np.clip(values / high, 0, 1)
    colors = (MOTION_COLORMAP if mode == VIEW_MOTION else REFLECTIVITY_COLORMAP).map(normalized, mode="float")
    colors[:, 3] = 0.3 + 0.7 * normalized
    return colors
# --- END [1.1] ---


# ==============================================================================
# [2.1] scene_block
# ==============================================================================
class SceneBlock(QWidget):
    def __init__(self, module) -> None:
        super().__init__()
        self.module = module
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = None
        reason = opengl_unavailable_reason()
        if reason:
            layout.addWidget(QLabel(f"3D view unavailable: {reason}", objectName="scatter_unavailable_label",
                                    alignment=Qt.AlignmentFlag.AlignCenter))
            return
        self.view = gl.GLViewWidget()
        self.view.setBackgroundColor(BACKGROUND_COLOR)
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
            self.view.addItem(item)
        layout.addWidget(self.view)
        if module.pipeline is not None:
            self.on_pipeline_ready()

    def on_pipeline_ready(self) -> None:
        """Floor grid, region box and camera around the voxel grid."""
        if self.view is None:
            return
        grid = self.module.pipeline.grid
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
        center = (lows + highs) / 2
        center[1] = highs[1] / 2                                   # include the antennas at y = 0
        self.view.opts["center"] = pg.Vector(*center)
        self.view.setCameraPosition(distance=float(sizes.max()) * 2.4, elevation=22, azimuth=-60)

    def refresh(self) -> None:
        module, pipeline = self.module, self.module.pipeline
        if self.view is None or pipeline is None:
            return
        time_step = module.time_step()
        volume = module.view_volume(time_step)
        mask = module.threshold_mask(volume)
        positions = pipeline.grid.centers_m()[mask.ravel()]
        if len(positions):
            self.voxel_item.setData(pos=positions, color=_volume_colors(module, volume, volume[mask]))
        self.voxel_item.setVisible(len(positions) > 0)

        array = pipeline.array
        self.antenna_item.setVisible(module.show_antennas())
        if module.show_antennas():
            self.antenna_item.setData(pos=np.vstack([array.tx_positions_m, array.rx_positions_m]), color=np.array(
                [TX_COLOR] * len(array.tx_positions_m) + [RX_COLOR] * len(array.rx_positions_m)))

        track = pipeline.track_positions_m[: time_step + 1]
        track = track[~np.isnan(track).any(axis=1)]
        self.track_item.setVisible(module.show_track() and len(track) > 1)
        if module.show_track() and len(track) > 1:
            self.track_item.setData(pos=track)
        current = pipeline.track_positions_m[time_step]
        show_target = module.show_track() and not np.isnan(current).any()
        self.target_item.setVisible(show_target)
        if show_target:
            self.target_item.setData(pos=current[None, :])
        self.selected_item.setVisible(module.selected_voxel is not None)
        if module.selected_voxel is not None:
            self.selected_item.setData(pos=pipeline.grid.index_to_m(module.selected_voxel)[None, :])
# --- END [2.1] ---

# ==============================================================================
# [2.2] waterfall_block
# ==============================================================================
class WaterfallBlock(QWidget):
    """Range-time (is_range) or spectrum waterfall for this block's antenna pair."""

    def __init__(self, module, is_range: bool) -> None:
        super().__init__()
        self.module, self.is_range = module, is_range
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.pair_combo = QComboBox(toolTip="Antenna pair")
        self.pair_combo.currentIndexChanged.connect(self.refresh)
        row.addWidget(control_label("Range–time" if is_range else "Spectrum"))
        row.addStretch()
        row.addWidget(control_label("pair"))
        row.addWidget(self.pair_combo)
        reserve_controls_space(row)
        layout.addLayout(row)
        self.plot = styled_plot("path length (m)" if is_range else "frequency (GHz)", "time step (newest top)")
        self.image = pg.ImageItem(axisOrder="row-major")
        self.image.setLookupTable(REFLECTIVITY_COLORMAP.getLookupTable(nPts=256))
        self.plot.addItem(self.image)
        self.time_line = pg.InfiniteLine(angle=0, movable=True, pen=pg.mkPen("w", width=1), hoverPen=pg.mkPen("w", width=2))
        self.time_line.sigPositionChanged.connect(lambda: module.set_time_step(int(self.time_line.value())))
        self.plot.addItem(self.time_line)
        layout.addWidget(self.plot, 1)
        self._pending_pair = 0
        if module.pipeline is not None:
            self.on_pipeline_ready()

    def on_pipeline_ready(self) -> None:
        array = self.module.pipeline.array
        self.pair_combo.blockSignals(True)
        self.pair_combo.clear()
        self.pair_combo.addItem(ALL_PAIRS_LABEL)
        self.pair_combo.addItems([array.pair_label(index) for index in range(array.pair_count)])
        self.pair_combo.setCurrentIndex(min(self._pending_pair, self.pair_combo.count() - 1))
        self.pair_combo.blockSignals(False)

    def refresh(self) -> None:
        module, pipeline = self.module, self.module.pipeline
        if pipeline is None or self.pair_combo.count() == 0:
            return
        pair_index = self.pair_combo.currentIndex() - 1
        sweeps = pipeline.sweeps
        background = pipeline.clutter_filter.background
        if module.remove_static() and background is not None:
            sweeps = sweeps - background[None, :, :]        # latest static estimate; older rows approximate
        if pair_index >= 0:
            sweeps = sweeps[:, pair_index, :]
        if self.is_range:
            profiles, path_axis_m = range_profiles(sweeps, pipeline.config)
            magnitude = profiles.mean(axis=1) if profiles.ndim == 3 else profiles
            x_start, x_width = 0.0, float(path_axis_m[-1])
        else:
            magnitude = np.abs(sweeps).mean(axis=1) if sweeps.ndim == 3 else np.abs(sweeps)
            x_start, x_width = pipeline.config.start_hz / 1e9, pipeline.config.bandwidth_hz / 1e9
        decibels = 20 * np.log10(magnitude + 1e-9)
        peak = float(decibels[-max(pipeline.filled_count, 1):].max())
        self.image.setImage(decibels, levels=(peak - DB_RANGE, peak), autoLevels=False)
        self.image.setRect(QRectF(x_start, 0, x_width, pipeline.history_length))
        self.time_line.blockSignals(True)
        self.time_line.setValue(module.time_step() + 0.5)
        self.time_line.blockSignals(False)

    def block_state(self) -> dict:
        return {"pair": max(self.pair_combo.currentIndex(), 0)}

    def restore_block_state(self, state: dict) -> None:
        self._pending_pair = int(state.get("pair", 0))
        if self.pair_combo.count():
            self.pair_combo.setCurrentIndex(min(self._pending_pair, self.pair_combo.count() - 1))
# --- END [2.2] ---

# ==============================================================================
# [2.3] slice_block
# ==============================================================================
class SliceBlock(QWidget):
    """2D cut through the volume; click a voxel to select it (shared with the other blocks)."""

    def __init__(self, module) -> None:
        super().__init__()
        self.module = module
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.plane_combo = QComboBox()
        self.plane_combo.addItems(SLICE_PLANES)
        self.plane_combo.currentTextChanged.connect(self._on_plane_changed)
        self.index_label = control_label()
        self.index_slider = QSlider(Qt.Orientation.Horizontal)
        self.index_slider.valueChanged.connect(self.refresh)
        row.addWidget(self.plane_combo)
        row.addWidget(self.index_label)
        row.addWidget(self.index_slider, 1)
        reserve_controls_space(row)
        layout.addLayout(row)
        self.plot = styled_plot()
        self.plot.getPlotItem().setAspectLocked(True)
        self.image = pg.ImageItem(axisOrder="row-major")
        self.plot.addItem(self.image)
        self.selected_marker = pg.ScatterPlotItem(size=14, symbol="s", pen=pg.mkPen("w", width=1.5), brush=None)
        self.track_marker = pg.ScatterPlotItem(size=10, symbol="o", pen=None, brush=pg.mkBrush(255, 160, 30))
        self.plot.addItem(self.selected_marker)
        self.plot.addItem(self.track_marker)
        self.plot.scene().sigMouseClicked.connect(self._on_clicked)
        layout.addWidget(self.plot, 1)
        layout.addWidget(control_label("Click a voxel to inspect its material spectrum."))
        self._pending_index: int | None = None
        if module.pipeline is not None:
            self.on_pipeline_ready()

    def _fixed_axis(self) -> int:
        return SLICE_PLANES[self.plane_combo.currentText()]

    def on_pipeline_ready(self) -> None:
        self._on_plane_changed()

    def _on_plane_changed(self) -> None:
        pipeline = self.module.pipeline
        if pipeline is None:
            return
        fixed_axis = self._fixed_axis()
        size = pipeline.grid.shape[fixed_axis]
        selected = self.module.selected_voxel
        index = self._pending_index if self._pending_index is not None else (selected[fixed_axis] if selected else size // 2)
        self._pending_index = None
        self.index_slider.blockSignals(True)
        self.index_slider.setRange(0, size - 1)
        self.index_slider.setValue(min(index, size - 1))
        self.index_slider.blockSignals(False)
        self.refresh()
        self.plot.getPlotItem().autoRange(padding=0.02)

    def refresh(self) -> None:
        module, pipeline = self.module, self.module.pipeline
        if pipeline is None:
            return
        grid = pipeline.grid
        fixed_axis, slice_index = self._fixed_axis(), self.index_slider.value()
        self.index_label.setText(f"{'xyz'[fixed_axis]} = {grid.axis_m(fixed_axis)[slice_index]:+.2f} m")
        time_step = module.time_step()
        volume = module.view_volume(time_step)
        plane = np.take(volume, slice_index, axis=fixed_axis).T          # rows = vertical axis
        horizontal_axis, vertical_axis = [axis for axis in range(3) if axis != fixed_axis]
        mode = module.view_mode()
        if mode == VIEW_MATERIALS:
            palette = np.array([EMPTY_MATERIAL_RGB] + [tuple(int(255 * c) for c in m.color) for m in MATERIALS], np.uint8)
            self.image.setLookupTable(None)
            self.image.setImage(palette[plane.astype(int) + 1], autoLevels=False, levels=(0, 255))
        else:
            self.image.setLookupTable((MOTION_COLORMAP if mode == VIEW_MOTION else REFLECTIVITY_COLORMAP).getLookupTable(nPts=256))
            high = 1.0 if mode == VIEW_REFLECTIVITY else max(float(volume.max()), 0.02)
            self.image.setImage(plane, levels=(0.0, high), autoLevels=False)
        spacing = grid.spacing_m
        h_axis, v_axis = grid.axis_m(horizontal_axis), grid.axis_m(vertical_axis)
        self.image.setRect(QRectF(h_axis[0] - spacing / 2, v_axis[0] - spacing / 2, len(h_axis) * spacing, len(v_axis) * spacing))
        self.plot.getPlotItem().setLabel("bottom", f"{'xyz'[horizontal_axis]} (m)")
        self.plot.getPlotItem().setLabel("left", f"{'xyz'[vertical_axis]} (m)")
        selected = module.selected_voxel
        if selected is not None and selected[fixed_axis] == slice_index:
            selected_m = grid.index_to_m(selected)
            self.selected_marker.setData([selected_m[horizontal_axis]], [selected_m[vertical_axis]])
        else:
            self.selected_marker.setData([], [])
        track = pipeline.track_positions_m[time_step]
        if module.show_track() and not np.isnan(track).any():
            self.track_marker.setData([track[horizontal_axis]], [track[vertical_axis]])
        else:
            self.track_marker.setData([], [])

    def _on_clicked(self, event) -> None:
        pipeline = self.module.pipeline
        if pipeline is None or event.button() != Qt.MouseButton.LeftButton:
            return
        point = self.plot.getPlotItem().vb.mapSceneToView(event.scenePos())
        fixed_axis = self._fixed_axis()
        horizontal_axis, vertical_axis = [axis for axis in range(3) if axis != fixed_axis]
        position_m = np.zeros(3)
        position_m[horizontal_axis], position_m[vertical_axis] = point.x(), point.y()
        voxel = list(pipeline.grid.m_to_index(position_m))
        voxel[fixed_axis] = self.index_slider.value()
        self.module.set_selected_voxel(tuple(voxel))

    def block_state(self) -> dict:
        return {"plane": self.plane_combo.currentText(), "index": self.index_slider.value()}

    def restore_block_state(self, state: dict) -> None:
        self._pending_index = state.get("index")
        if state.get("plane") in SLICE_PLANES:
            self.plane_combo.setCurrentText(state["plane"])
# --- END [2.3] ---

# ==============================================================================
# [2.4] materials_block
# ==============================================================================
class MaterialsBlock(QWidget):
    """Selected voxel's spectrum against the material library, plus a scene summary."""

    def __init__(self, module) -> None:
        super().__init__()
        self.module = module
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.plot = styled_plot("frequency (GHz)", "|Γ| reflectivity")
        plot_item = self.plot.getPlotItem()
        plot_item.setYRange(0, 1.05)
        plot_item.addLegend(offset=(-4, 4), labelTextSize="8pt", verSpacing=-6)
        self.spectrum_points = pg.ScatterPlotItem(size=11, symbol="o", pen=pg.mkPen("w"), brush=pg.mkBrush(255, 255, 255, 180))
        plot_item.addItem(self.spectrum_points)
        layout.addWidget(self.plot, 1)
        self.summary_label = QLabel(objectName="module_summary_label", wordWrap=True)
        layout.addWidget(self.summary_label)
        self._has_library = False
        if module.pipeline is not None:
            self.on_pipeline_ready()

    def on_pipeline_ready(self) -> None:
        if self._has_library:
            return
        self._has_library = True
        config = self.module.pipeline.config
        frequencies_hz = np.linspace(config.start_hz, config.stop_hz, 60)
        for material in MATERIALS:
            self.plot.getPlotItem().plot(frequencies_hz / 1e9, np.abs(material.reflection_coefficient(frequencies_hz)),
                                         pen=pg.mkPen(tuple(int(255 * c) for c in material.color), width=1.5),
                                         name=material.name)

    def refresh(self) -> None:
        module, pipeline = self.module, self.module.pipeline
        if pipeline is None:
            return
        lines = []
        voxel = module.selected_voxel
        if voxel is None:
            self.spectrum_points.setData([], [])
            lines.append("Select a voxel in a Slice block to compare its spectrum with the library.")
        else:
            band_magnitudes = pipeline.band_reflectivity[(slice(None), *voxel)]
            position_m = pipeline.grid.index_to_m(voxel)
            position_text = f"({position_m[0]:+.1f}, {position_m[1]:.1f}, {position_m[2]:+.1f}) m"
            material_id = int(pipeline.static_material_ids[voxel])
            if material_id == NO_MATERIAL_ID:
                self.spectrum_points.setData([], [])
                lines.append(f"Voxel {position_text}: no reflector found here.")
            else:
                self.spectrum_points.setData(pipeline.backprojector.band_centers_hz / 1e9, band_magnitudes)
                lines.append(f"Voxel {position_text}: <b>{MATERIALS[material_id].name}</b> · confidence "
                             f"{pipeline.material_confidence[voxel]:.2f} · sub-band |Γ| "
                             f"{np.array2string(band_magnitudes, precision=2)}")
        counts = Counter(int(i) for i in pipeline.static_material_ids.ravel() if i != NO_MATERIAL_ID)
        summary = ", ".join(f"{MATERIALS[i].name} {n}" for i, n in counts.most_common()) or "none yet"
        lines.append(f"Static reflectors (pass {pipeline.static_map_count}): {summary}")
        recent = pipeline.target_material_ids[-TARGET_VOTE_FRAMES:]
        recent = recent[recent != NO_MATERIAL_ID]
        if len(recent):
            votes = Counter(int(i) for i in recent).most_common(2)
            vote_text = ", ".join(f"{MATERIALS[i].name} {100 * n / len(recent):.0f}%" for i, n in votes)
            lines.append(f"Moving target (last ~2 s): {vote_text} — extended bodies read high; treat as water-rich vs not.")
        self.summary_label.setText("<br>".join(lines))
# --- END [2.4] ---


# ==============================================================================
# [3.1] block_specs
# ==============================================================================
def block_specs(module) -> list[BlockSpec]:
    return [
        BlockSpec("scene", "3D scene", "Voxels, antennas, track and selection in 3D", lambda: SceneBlock(module)),
        BlockSpec("range_time", "Range–time", "Echo strength vs path length over time (per pair)",
                  lambda: WaterfallBlock(module, is_range=True)),
        BlockSpec("spectrum", "Spectrum", "|H(f)| over time (per pair)", lambda: WaterfallBlock(module, is_range=False)),
        BlockSpec("slice", "Slice", "2D cut through the volume; click to select a voxel", lambda: SliceBlock(module)),
        BlockSpec("materials", "Materials", "Selected voxel's spectrum vs the material library", lambda: MaterialsBlock(module)),
    ]
# --- END [3.1] ---
