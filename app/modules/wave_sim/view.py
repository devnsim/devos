# ==============================================================================
# FILE: app/modules/wave_sim/view.py
# OVERVIEW: RF Wave Simulator module UI: the front end of the wave_sim app (engine.py + C++ core +
#           store.py). Left: 3D spatial twin. Right: time x frequency waterfall and dielectric
#           spectroscopy of the selected material.
# PURPOSE:
#   1. 3D twin: CAD meshes coloured by material, TX (red) and RX (cyan) spheres, live rays coloured by
#      excess loss (green = clear, yellow = ~10 dB, red = >= 30 dB absorbed).
#   2. Waterfall: one receiver's |H| (dB) or phase across the sweep, newest frame on top.
#   3. Spectroscopy: eps' (wave slowing) and eps'' (absorption) of the selected body's material across
#      the sweep, with n, phase slowing per cm, absorption per cm and |Gamma| at the centre frequency.
#   4. Controls: live/pause, oscillator (FMCW/OFDM, start/stop, LC tank readout), TX position,
#      animation, CAD import, material assignment, custom materials, tensor snapshots to SQLite.
#   5. Remembers its scene (sweep, TX, materials, imported files) in its module settings.
# CONTRACT:
#   - Requires: pyqtgraph (+ opengl), app.modules.wave_sim (engine, physics, scene, store),
#               app.native.ENGINE_NAME, app.ui.gl_support, app.ui.theme colours, QSS selectors
#               #wave_sim_panel, #inspector_control_label, #live_toggle_btn, #rf_source_label,
#               #rf_stats_label, #rf_material_summary_label, #scatter_unavailable_label
#   - Exposes: WaveSimModule(context=None) (QWidget); attributes engine, scene, store, materials
# NOTES:
#   - [2026-11-15] Created from the "first-principles physics engine" spec: LC-tank oscillator 1-10 GHz
#                  (FMCW / OFDM), spherical 1/r waves with e^{-jkr}, CAD import (trimesh), dielectric
#                  ray tracing (C++ devos_core), 4D tensor (nodes x time x freq x [amp, phase]).
#   - [2026-11-15] Without a context (no database) it runs with the built-in materials and saves nothing.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - excess loss (dB): Ray's gain relative to free space over the same length (engine.py).
# - LOSS_COLOR_RANGE_DB (float): Excess loss mapped to full red (default: 30 dB).
# - selected body: Body chosen in the spectroscopy panel; its material is shown and can be changed.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS_AND_CONSTANTS
#    1.1 imports
# 2. WAVE_SIM_MODULE
#    2.1 init_module
#    2.2 restore_settings
#    2.3 build_controls
#    2.4 build_views
#    2.5 scene_graphics
#    2.6 live_loop
#    2.7 refresh_views
#    2.8 handlers
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
import time
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
    QSplitter, QToolButton, QVBoxLayout, QWidget,
)

from app import native
from app.data.em_materials import MATERIAL_BY_NAME, SPEED_OF_LIGHT_M_S
from app.modules.wave_sim.engine import WaveSimEngine
from app.modules.wave_sim.physics import WAVEFORM_FMCW, WAVEFORM_OFDM, SweepSettings, lc_tank_capacitance_f
from app.modules.wave_sim.scene import default_room, load_mesh_file
from app.modules.wave_sim.store import WaveSimStore
from app.ui.gl_support import gl, opengl_unavailable_reason
from app.ui.theme import BACKGROUND_COLOR, BORDER_COLOR, MUTED_TEXT_COLOR

TX_COLOR = (1.0, 0.3, 0.25, 1.0)
RX_COLOR = (0.3, 0.85, 1.0, 1.0)
WALL_ALPHA, SOLID_ALPHA = 0.16, 0.55
CAD_FILE_FILTER = "CAD meshes (*.stl *.obj *.ply *.off *.glb *.gltf)"
DISPLAY_AMPLITUDE, DISPLAY_PHASE = "|H| (dB)", "Phase (rad)"
# --- END [1.1] ---


# ==============================================================================
# [2.1] init_module
# ==============================================================================
class WaveSimModule(QWidget):
    LIVE_INTERVAL_MS = 50
    LOSS_COLOR_RANGE_DB = 30.0
    WATERFALL_DB_RANGE = 60.0
    MAX_RAYS_PER_NODE = 14
    DEFAULT_IMPORT_MATERIAL = "PTFE / plastic"

    def __init__(self, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, objectName="wave_sim_panel")
        self.context = context
        self.store = WaveSimStore(context.database, context.instance_id) if context is not None else None
        self.materials = self.store.materials() if self.store else dict(MATERIAL_BY_NAME)
        self.scene = default_room()
        self.engine = WaveSimEngine(self.scene, SweepSettings(), self.materials)
        self._is_live = True
        self._frame_times: list[float] = []
        self._last_stats_time = 0.0
        self._last_result = None
        self.body_items: list = []
        self._restore_settings()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        self._build_controls(layout)
        self.view_splitter = QSplitter(Qt.Orientation.Horizontal, objectName="pane_splitter")
        self.view_splitter.setChildrenCollapsible(False)
        self.view_splitter.setHandleWidth(4)
        self._build_scene_view()
        self._build_right_views()
        self.view_splitter.setSizes([3, 2])
        layout.addWidget(self.view_splitter, 1)

        self.live_timer = QTimer(self, interval=self.LIVE_INTERVAL_MS, timeout=self._on_live_tick)
        self.set_live(True)
        self._rebuild_scene_graphics()
        self._refresh_spectroscopy()
# --- END [2.1] ---

# ==============================================================================
# [2.2] restore_settings
# ==============================================================================
    def _setting(self, key: str, default):
        return self.context.settings.get(key, default) if self.context is not None else default

    def _save_setting(self, key: str, value) -> None:
        if self.context is not None:
            self.context.settings.set(key, value)

    def _restore_settings(self) -> None:
        sweep = self._setting("sweep", None)
        if sweep:
            self.engine.set_sweep(SweepSettings(**sweep))
        tx = self._setting("tx_position_m", None)
        if tx:
            self.scene.tx_position_m = np.array(tx, float)
        for imported in self._setting("imported_meshes", []):
            if Path(imported["path"]).exists() and imported.get("material") in self.materials:
                try:
                    place_at = None if imported.get("place_at_m") is None else tuple(imported["place_at_m"])
                    self.scene.bodies.append(load_mesh_file(imported["path"], imported["material"], place_at))
                except (ValueError, OSError) as error:
                    print(f"[DevOS] wave_sim: skipped {imported['path']}: {error}")
        for body in self.scene.bodies:
            material_name = self._setting("body_materials", {}).get(body.name)
            if material_name in self.materials:
                body.material_name = material_name
        self.engine.is_animating = self._setting("animate", True)
# --- END [2.2] ---

# ==============================================================================
# [2.3] build_controls
# ==============================================================================
    def _label(self, text: str) -> QLabel:
        return QLabel(text, objectName="inspector_control_label")

    def _spin(self, low: float, high: float, value: float, step: float, suffix: str, decimals: int = 2) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(minimum=low, maximum=high, value=value, singleStep=step, suffix=suffix, decimals=decimals)
        spin.setKeyboardTracking(False)
        return spin

    def _build_controls(self, layout: QVBoxLayout) -> None:
        top_row = QHBoxLayout()
        self.live_toggle_btn = QToolButton(objectName="live_toggle_btn", checkable=True)
        self.live_toggle_btn.toggled.connect(self.set_live)
        self.import_cad_btn = QPushButton("Import CAD…", toolTip="Add an .stl / .obj mesh as a body (mm auto-detected)")
        self.import_cad_btn.clicked.connect(self._on_import_cad)
        self.reset_scene_btn = QPushButton("Reset scene", toolTip="Back to the default room (removes imported meshes)")
        self.reset_scene_btn.clicked.connect(self._on_reset_scene)
        self.snapshot_btn = QPushButton("Snapshot → DB", toolTip="Save the current 4D tensor into SQLite")
        self.snapshot_btn.clicked.connect(self._on_snapshot)
        self.snapshot_btn.setEnabled(self.store is not None)
        self.engine_label = QLabel(objectName="rf_source_label")
        self.engine_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.stats_label = QLabel(objectName="rf_stats_label")
        for widget in (self.live_toggle_btn, self.import_cad_btn, self.reset_scene_btn, self.snapshot_btn):
            top_row.addWidget(widget)
        top_row.addWidget(self.engine_label, 1)
        top_row.addWidget(self.stats_label)
        layout.addLayout(top_row)

        sweep = self.engine.sweep
        oscillator_row = QHBoxLayout()
        self.waveform_combo = QComboBox(objectName="waveform_combo")
        self.waveform_combo.addItems([WAVEFORM_FMCW, WAVEFORM_OFDM])
        self.waveform_combo.setCurrentText(sweep.waveform)
        self.start_ghz_spin = self._spin(1.0, 9.9, sweep.start_hz / 1e9, 0.1, " GHz")
        self.stop_ghz_spin = self._spin(1.1, 10.0, sweep.stop_hz / 1e9, 0.1, " GHz")
        self.lc_tank_label = self._label("")
        tx = self.scene.tx_position_m
        self.tx_spins = [self._spin(-5.0, 5.0, float(tx[axis]), 0.1, " m") for axis in range(3)]
        self.animate_check = QCheckBox("Animate", checked=self.engine.is_animating, toolTip="Walk the person through the room")
        self.all_rays_check = QCheckBox("All RX rays", toolTip="Draw rays to every receiver, not just the selected one")
        oscillator_row.addWidget(self._label("oscillator"))
        oscillator_row.addWidget(self.waveform_combo)
        oscillator_row.addWidget(self.start_ghz_spin)
        oscillator_row.addWidget(self._label("→"))
        oscillator_row.addWidget(self.stop_ghz_spin)
        oscillator_row.addStretch()
        oscillator_row.addWidget(self._label("TX"))
        for axis, spin in zip("xyz", self.tx_spins):
            oscillator_row.addWidget(self._label(axis))
            oscillator_row.addWidget(spin)
        oscillator_row.addWidget(self.animate_check)
        oscillator_row.addWidget(self.all_rays_check)
        layout.addLayout(oscillator_row)

        self.waveform_combo.currentTextChanged.connect(self._on_sweep_changed)
        self.start_ghz_spin.valueChanged.connect(self._on_sweep_changed)
        self.stop_ghz_spin.valueChanged.connect(self._on_sweep_changed)
        for spin in self.tx_spins:
            spin.valueChanged.connect(self._on_tx_changed)
        self.animate_check.toggled.connect(self._on_animate_toggled)
        self.all_rays_check.toggled.connect(lambda: self._refresh_rays())
        self._update_lc_label()
# --- END [2.3] ---

# ==============================================================================
# [2.4] build_views
# ==============================================================================
    def _styled_plot(self) -> pg.PlotWidget:
        plot = pg.PlotWidget(background=BACKGROUND_COLOR)
        item = plot.getPlotItem()
        item.setMenuEnabled(False)
        item.hideButtons()
        for axis_name in ("left", "bottom"):
            axis = item.getAxis(axis_name)
            axis.enableAutoSIPrefix(False)
            axis.setPen(pg.mkPen(BORDER_COLOR))
            axis.setTextPen(pg.mkPen(MUTED_TEXT_COLOR))
        return plot

    def _build_scene_view(self) -> None:
        self.scene_view = None
        reason = opengl_unavailable_reason()
        if reason:
            self.view_splitter.addWidget(QLabel(f"3D view unavailable: {reason}", objectName="scatter_unavailable_label",
                                                alignment=Qt.AlignmentFlag.AlignCenter))
            return
        self.scene_view = gl.GLViewWidget()
        self.scene_view.setBackgroundColor(BACKGROUND_COLOR)
        self.scene_view.setMinimumWidth(260)
        grid = gl.GLGridItem(color=(255, 255, 255, 18))
        grid.setSize(10, 10)
        grid.setSpacing(0.5, 0.5)
        self.scene_view.addItem(grid)
        self.ray_item = gl.GLLinePlotItem(mode="lines", width=1.6, antialias=True)
        self.scene_view.addItem(self.ray_item)
        sphere = gl.MeshData.sphere(rows=10, cols=18, radius=0.09)
        self.tx_item = gl.GLMeshItem(meshdata=sphere, color=TX_COLOR, smooth=True, shader="shaded")
        self.scene_view.addItem(self.tx_item)
        self.rx_items, self.rx_labels = [], []
        for name, position in zip(self.scene.rx_names, self.scene.rx_positions_m):
            rx_item = gl.GLMeshItem(meshdata=sphere, color=RX_COLOR, smooth=True, shader="shaded")
            rx_item.translate(*position)
            label = gl.GLTextItem(pos=position + np.array([0, 0, 0.18]), text=name.split()[0], color=(180, 230, 255, 255))
            self.scene_view.addItem(rx_item)
            self.scene_view.addItem(label)
            self.rx_items.append(rx_item)
            self.rx_labels.append(label)
        self.scene_view.opts["center"] = pg.Vector(0, 0, 1.0)
        self.scene_view.setCameraPosition(distance=10.5, elevation=38, azimuth=-62)
        self.view_splitter.addWidget(self.scene_view)

    def _build_right_views(self) -> None:
        right_splitter = QSplitter(Qt.Orientation.Vertical, objectName="pane_splitter")
        right_splitter.setChildrenCollapsible(False)
        right_splitter.setHandleWidth(4)

        # --- waterfall ---
        waterfall_panel = QWidget()
        waterfall_layout = QVBoxLayout(waterfall_panel)
        waterfall_layout.setContentsMargins(0, 0, 0, 0)
        waterfall_row = QHBoxLayout()
        self.node_combo = QComboBox(objectName="node_combo")
        self.node_combo.addItems(self.scene.rx_names)
        self.node_combo.currentIndexChanged.connect(self._on_node_changed)
        self.display_combo = QComboBox(objectName="display_combo")
        self.display_combo.addItems([DISPLAY_AMPLITUDE, DISPLAY_PHASE])
        self.display_combo.currentTextChanged.connect(lambda: self._refresh_waterfall())
        waterfall_row.addWidget(self._label("receiver"))
        waterfall_row.addWidget(self.node_combo, 1)
        waterfall_row.addWidget(self.display_combo)
        waterfall_row.addWidget(self.lc_tank_label)
        waterfall_layout.addLayout(waterfall_row)
        self.waterfall_plot = self._styled_plot()
        self.waterfall_plot.getPlotItem().setLabel("bottom", "frequency (GHz)")
        self.waterfall_plot.getPlotItem().setLabel("left", "time step (newest top)")
        self.waterfall_image = pg.ImageItem(axisOrder="row-major")
        self.waterfall_plot.addItem(self.waterfall_image)
        self.amplitude_lut = pg.colormap.get("viridis").getLookupTable(nPts=256)
        try:
            self.phase_lut = pg.colormap.get("CET-C1").getLookupTable(nPts=256)     # cyclic, for phase
        except Exception:
            self.phase_lut = pg.colormap.get("plasma").getLookupTable(nPts=256)
        waterfall_layout.addWidget(self.waterfall_plot, 1)
        right_splitter.addWidget(waterfall_panel)

        # --- spectroscopy ---
        spectroscopy_panel = QWidget()
        spectroscopy_layout = QVBoxLayout(spectroscopy_panel)
        spectroscopy_layout.setContentsMargins(0, 0, 0, 0)
        assign_row = QHBoxLayout()
        self.body_combo = QComboBox(objectName="body_combo", toolTip="Body to inspect / re-assign")
        self.material_combo = QComboBox(objectName="material_combo", toolTip="Material of the selected body")
        self.body_combo.currentIndexChanged.connect(self._on_body_selected)
        self.material_combo.currentTextChanged.connect(self._on_material_assigned)
        assign_row.addWidget(self._label("body"))
        assign_row.addWidget(self.body_combo, 1)
        assign_row.addWidget(self._label("material"))
        assign_row.addWidget(self.material_combo, 1)
        spectroscopy_layout.addLayout(assign_row)
        custom_row = QHBoxLayout()
        self.custom_name_edit = QLineEdit(placeholderText="custom material name")
        self.custom_permittivity_spin = self._spin(1.0, 100.0, 4.0, 0.1, "", 2)
        self.custom_conductivity_spin = self._spin(0.0, 100.0, 0.01, 0.01, " S/m", 4)
        self.add_material_btn = QPushButton("Add material", toolTip="Save a constant-eps'/sigma material to the database")
        self.add_material_btn.clicked.connect(self._on_add_material)
        self.add_material_btn.setEnabled(self.store is not None)
        custom_row.addWidget(self.custom_name_edit, 1)
        custom_row.addWidget(self._label("ε′"))
        custom_row.addWidget(self.custom_permittivity_spin)
        custom_row.addWidget(self._label("σ"))
        custom_row.addWidget(self.custom_conductivity_spin)
        custom_row.addWidget(self.add_material_btn)
        spectroscopy_layout.addLayout(custom_row)
        self.spectroscopy_plot = self._styled_plot()
        spectroscopy_item = self.spectroscopy_plot.getPlotItem()
        spectroscopy_item.setLogMode(y=True)
        spectroscopy_item.setLabel("bottom", "frequency (GHz)")
        spectroscopy_item.setLabel("left", "ε′ (slowing), ε″ (absorption)")
        spectroscopy_item.addLegend(offset=(-4, 4), labelTextSize="8pt")
        self.permittivity_curve = spectroscopy_item.plot(name="ε′  real part")
        self.loss_curve = spectroscopy_item.plot(name="ε″  loss part")
        spectroscopy_layout.addWidget(self.spectroscopy_plot, 1)
        self.spectroscopy_label = QLabel(objectName="rf_material_summary_label")
        self.spectroscopy_label.setWordWrap(True)
        spectroscopy_layout.addWidget(self.spectroscopy_label)
        right_splitter.addWidget(spectroscopy_panel)
        right_splitter.setSizes([1, 1])
        self.view_splitter.addWidget(right_splitter)
        self._fill_body_and_material_combos()
# --- END [2.4] ---

# ==============================================================================
# [2.5] scene_graphics
# ==============================================================================
    def _rebuild_scene_graphics(self) -> None:
        """(Re)create one mesh item per body - after import, reset, or material changes."""
        if self.scene_view is None:
            return
        for item in self.body_items:
            self.scene_view.removeItem(item)
        self.body_items = []
        for body in self.scene.bodies:
            material = self.materials[body.material_name]
            alpha = WALL_ALPHA if body.name.startswith(("Wall", "Floor")) else SOLID_ALPHA
            item = gl.GLMeshItem(
                meshdata=gl.MeshData(vertexes=body.vertices, faces=body.faces), color=(*material.color, alpha),
                smooth=False, shader="shaded", drawEdges=True, edgeColor=(*material.color, min(1.0, alpha + 0.3)),
                glOptions="translucent",
            )
            self.scene_view.addItem(item)
            self.body_items.append(item)
        self._place_movers()

    def _place_movers(self) -> None:
        if self.scene_view is None:
            return
        self.tx_item.resetTransform()
        self.tx_item.translate(*self.scene.tx_position_m)
        for body, item in zip(self.scene.bodies, self.body_items):
            if body.is_animated:
                item.resetTransform()
                item.translate(*body.offset_at(self.engine.time_s))

    def _loss_colors(self, excess_loss_db: np.ndarray) -> np.ndarray:
        """0 dB -> green, LOSS_COLOR_RANGE_DB / 3 -> yellow, >= LOSS_COLOR_RANGE_DB -> red."""
        fraction = np.clip(-excess_loss_db / self.LOSS_COLOR_RANGE_DB, 0.0, 1.0)
        red = np.clip(fraction * 3, 0, 1)
        green = np.clip(1.5 - 1.5 * fraction, 0, 1)
        return np.column_stack([red, green, np.full_like(red, 0.25), np.full_like(red, 0.9)])

    def _refresh_rays(self) -> None:
        if self.scene_view is None or self._last_result is None:
            return
        paths = self._last_result.paths
        selected_node = self.node_combo.currentIndex()
        keep = []
        for node in range(self.scene.node_count):
            if not self.all_rays_check.isChecked() and node != selected_node:
                continue
            node_paths = np.flatnonzero(paths.node == node)
            strongest = node_paths[np.argsort(paths.gain_db[node_paths])[::-1][: self.MAX_RAYS_PER_NODE]]
            keep.extend(strongest.tolist())
        if not keep:
            self.ray_item.setVisible(False)
            return
        keep = np.array(keep)
        points = paths.points[keep]                                  # (K, 3, 3): TX, bounce, RX
        segments = np.concatenate([points[:, [0, 1]], points[:, [1, 2]]], axis=0).reshape(-1, 3)
        colors = np.repeat(self._loss_colors(paths.excess_loss_db[keep]), 2, axis=0)
        self.ray_item.setData(pos=segments, color=np.vstack([colors, colors]))
        self.ray_item.setVisible(True)
# --- END [2.5] ---

# ==============================================================================
# [2.6] live_loop
# ==============================================================================
    def set_live(self, is_live: bool) -> None:
        self._is_live = is_live
        self.live_toggle_btn.blockSignals(True)
        self.live_toggle_btn.setChecked(is_live)
        self.live_toggle_btn.blockSignals(False)
        self.live_toggle_btn.setText("❚❚ Pause" if is_live else "▶ Live")
        self._update_live_timer()

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

    def step_once(self) -> None:
        """Advance the simulation by one frame and redraw (also used by the timer)."""
        self._last_result = self.engine.step(self.LIVE_INTERVAL_MS / 1000)
        now = time.perf_counter()
        self._frame_times = [stamp for stamp in self._frame_times if now - stamp < 1.0] + [now]
        self._place_movers()
        self._refresh_rays()
        self._refresh_waterfall()
        self._refresh_stats()

    def _on_live_tick(self) -> None:
        self.step_once()
# --- END [2.6] ---

# ==============================================================================
# [2.7] refresh_views
# ==============================================================================
    def _refresh_waterfall(self) -> None:
        tensor = self.engine.tensor
        if tensor.frame_count == 0:
            return
        history = tensor.node_history(self.node_combo.currentIndex())          # (time, F, 2)
        sweep = self.engine.sweep
        frequencies = self.engine.frequencies_hz
        if self.display_combo.currentText() == DISPLAY_PHASE:
            self.waterfall_image.setLookupTable(self.phase_lut)
            self.waterfall_image.setImage(history[..., 1], levels=(-np.pi, np.pi), autoLevels=False)
        else:
            decibels = 20 * np.log10(history[..., 0] + 1e-12)
            peak = float(decibels.max())
            self.waterfall_image.setLookupTable(self.amplitude_lut)
            self.waterfall_image.setImage(decibels, levels=(peak - self.WATERFALL_DB_RANGE, peak), autoLevels=False)
        step_ghz = (frequencies[-1] - frequencies[0]) / max(len(frequencies) - 1, 1) / 1e9
        # Newest row at the top of the axis, even while the history is still filling.
        self.waterfall_image.setRect(QRectF(frequencies[0] / 1e9 - step_ghz / 2, tensor.time_steps - len(history),
                                            (frequencies[-1] - frequencies[0]) / 1e9 + step_ghz, len(history)))
        self.waterfall_plot.getPlotItem().setXRange(sweep.start_hz / 1e9, sweep.stop_hz / 1e9, padding=0)
        self.waterfall_plot.getPlotItem().setYRange(0, tensor.time_steps, padding=0)

    def _refresh_spectroscopy(self) -> None:
        body = self._selected_body()
        if body is None:
            return
        material = self.materials[body.material_name]
        sweep = self.engine.sweep
        frequencies = np.linspace(sweep.start_hz, sweep.stop_hz, 120)
        permittivity = np.real(material.complex_permittivity(frequencies))
        loss = material.loss_permittivity(frequencies)
        color = tuple(int(255 * c) for c in material.color)
        self.permittivity_curve.setData(frequencies / 1e9, np.maximum(permittivity, 1e-6), pen=pg.mkPen(color, width=2))
        self.loss_curve.setData(frequencies / 1e9, np.maximum(loss, 1e-6),
                                pen=pg.mkPen(color, width=2, style=Qt.PenStyle.DashLine))
        center = sweep.center_hz
        index = complex(np.asarray(material.refractive_index(center)))
        k0 = 2 * np.pi * center / SPEED_OF_LIGHT_M_S
        gamma = abs(complex(np.asarray(material.reflection_coefficient(center))))
        if material.is_conductor:
            detail = "perfect conductor: no transmission, |Γ| = 1"
        else:
            slowing_deg_cm = np.degrees(k0 * (index.real - 1) * 0.01)
            absorption_db_cm = 8.686 * k0 * (-index.imag) * 0.01
            detail = (f"n = {index.real:.2f} − {-index.imag:.3f}j · slowing {slowing_deg_cm:.0f}°/cm · "
                      f"absorption {absorption_db_cm:.3f} dB/cm · |Γ| {gamma:.2f}")
        self.spectroscopy_label.setText(
            f"<b>{body.name}</b> — {material.name} ({material.source})<br>at {center / 1e9:.2f} GHz: {detail}"
        )

    def _refresh_stats(self) -> None:
        now = time.monotonic()
        if now - self._last_stats_time < 0.4 or self._last_result is None:
            return
        self._last_stats_time = now
        result = self._last_result
        engine_text = "C++ " + native.ENGINE_NAME.split(" (")[0] if native.HAS_NATIVE else "NumPy fallback"
        self.engine_label.setText(
            f"engine: {engine_text}  ·  {len(self.scene.bodies)} bodies, "
            f"{sum(len(b.faces) for b in self.scene.bodies)} triangles  ·  tensor {'×'.join(map(str, self.engine.tensor.shape))} float32"
        )
        self.engine_label.setToolTip(native.ENGINE_NAME)
        reflections = int((result.paths.reflect_body >= 0).sum())
        self.stats_label.setText(
            f"{len(self._frame_times)} fps  ·  {reflections} bounces  ·  trace {result.trace_ms:.1f} ms  ·  "
            f"synth {result.synth_ms:.1f} ms  ·  t {result.timestamp_s:.1f} s"
        )
# --- END [2.7] ---

# ==============================================================================
# [2.8] handlers
# ==============================================================================
    def _update_lc_label(self) -> None:
        sweep = self.engine.sweep
        capacitance = lc_tank_capacitance_f(sweep.tank_inductance_h, np.array([sweep.start_hz, sweep.stop_hz])) * 1e12
        self.lc_tank_label.setText(f"LC tank {sweep.tank_inductance_h * 1e9:.1f} nH · C {capacitance[0]:.2f}→{capacitance[1]:.3f} pF")
        self.lc_tank_label.setToolTip("f = 1 / (2π√(LC)): the varactor capacitance that tunes the tank across the sweep")

    def _on_sweep_changed(self) -> None:
        start_ghz, stop_ghz = self.start_ghz_spin.value(), self.stop_ghz_spin.value()
        if stop_ghz <= start_ghz + 0.05:
            return
        sweep = SweepSettings(start_ghz * 1e9, stop_ghz * 1e9, self.engine.sweep.bin_count,
                              self.waveform_combo.currentText(), self.engine.sweep.tank_inductance_h)
        self.engine.set_sweep(sweep)
        self._save_setting("sweep", sweep.to_dict())
        self._update_lc_label()
        self._refresh_spectroscopy()

    def _on_tx_changed(self) -> None:
        self.scene.tx_position_m = np.array([spin.value() for spin in self.tx_spins])
        self._save_setting("tx_position_m", self.scene.tx_position_m.tolist())
        self._place_movers()

    def _on_animate_toggled(self, is_animating: bool) -> None:
        self.engine.is_animating = is_animating
        self._save_setting("animate", is_animating)

    def _on_node_changed(self) -> None:
        self._refresh_waterfall()
        self._refresh_rays()

    def _fill_body_and_material_combos(self) -> None:
        self.body_combo.blockSignals(True)
        current = self.body_combo.currentText()
        self.body_combo.clear()
        self.body_combo.addItems([body.name for body in self.scene.bodies])
        if current in [body.name for body in self.scene.bodies]:
            self.body_combo.setCurrentText(current)
        self.body_combo.blockSignals(False)
        self.material_combo.blockSignals(True)
        self.material_combo.clear()
        self.material_combo.addItems(list(self.materials))
        self.material_combo.blockSignals(False)
        self._on_body_selected()

    def _selected_body(self):
        index = self.body_combo.currentIndex()
        return self.scene.bodies[index] if 0 <= index < len(self.scene.bodies) else None

    def _on_body_selected(self) -> None:
        body = self._selected_body()
        if body is None:
            return
        self.material_combo.blockSignals(True)
        self.material_combo.setCurrentText(body.material_name)
        self.material_combo.blockSignals(False)
        self._refresh_spectroscopy()

    def _on_material_assigned(self, material_name: str) -> None:
        body = self._selected_body()
        if body is None or material_name not in self.materials:
            return
        body.material_name = material_name
        assignments = self._setting("body_materials", {})
        assignments[body.name] = material_name
        self._save_setting("body_materials", assignments)
        self._persist_imports()
        self._rebuild_scene_graphics()
        self._refresh_spectroscopy()

    def _on_add_material(self) -> None:
        name = self.custom_name_edit.text().strip()
        if not name or self.store is None:
            return
        try:
            self.store.add_custom_material(name, self.custom_permittivity_spin.value(), self.custom_conductivity_spin.value())
        except ValueError as error:
            self.spectroscopy_label.setText(str(error))
            return
        self.materials = self.store.materials()
        self.engine.set_materials(self.materials)
        self._fill_body_and_material_combos()
        self.material_combo.setCurrentText(name)                 # assigns it to the selected body

    def _on_import_cad(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "Import CAD mesh", "", CAD_FILE_FILTER)
        if file_path:
            self.import_mesh(file_path)

    def import_mesh(self, file_path: str, material_name: str | None = None, place_at_m=(0.0, 1.0)) -> None:
        """Add a mesh file to the scene (also used by tests and scripts)."""
        try:
            body = load_mesh_file(file_path, material_name or self.DEFAULT_IMPORT_MATERIAL, place_at_m)
        except (ValueError, OSError) as error:
            self.engine_label.setText(f"Import failed: {error}")
            return
        existing = {b.name for b in self.scene.bodies}
        while body.name in existing:
            body.name += "′"
        self.scene.bodies.append(body)
        self._persist_imports()
        self._fill_body_and_material_combos()
        self.body_combo.setCurrentText(body.name)
        self._rebuild_scene_graphics()

    def _persist_imports(self) -> None:
        imported = [{"path": body.source_path, "material": body.material_name,
                     "place_at_m": list(body.place_at_m) if body.place_at_m else None}
                    for body in self.scene.bodies if body.source_path]
        self._save_setting("imported_meshes", imported)

    def _on_reset_scene(self) -> None:
        tx = self.scene.tx_position_m
        self.scene = default_room()
        self.scene.tx_position_m = tx
        self.engine.scene = self.scene
        self.engine.reset_tensor()
        self._save_setting("imported_meshes", [])
        self._save_setting("body_materials", {})
        self._fill_body_and_material_combos()
        self._rebuild_scene_graphics()

    def _on_snapshot(self) -> None:
        if self.store is None or self.engine.tensor.frame_count == 0:
            return
        tensor = self.engine.tensor.as_tensor4d()
        scene_description = {
            "tx_position_m": self.scene.tx_position_m.tolist(),
            "rx": [{"name": name, "position_m": position.tolist()}
                   for name, position in zip(self.scene.rx_names, self.scene.rx_positions_m)],
            "bodies": [{"name": body.name, "material": body.material_name, "source": body.source_path}
                       for body in self.scene.bodies],
            "axes": list(tensor.axis_names),
        }
        snapshot_id = self.store.save_snapshot(tensor.data, f"t = {self.engine.time_s:.1f} s",
                                               self.engine.sweep.to_dict(), scene_description)
        count = len(self.store.list_snapshots())
        self.stats_label.setText(f"Saved snapshot #{snapshot_id} ({tensor.data.nbytes / 1e6:.1f} MB) · {count} stored")
# --- END [2.8] ---
