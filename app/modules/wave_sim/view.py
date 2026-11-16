# ==============================================================================
# FILE: app/modules/wave_sim/view.py
# OVERVIEW: RF Wave Simulator module: the front end of the wave_sim app (engine.py + C++ core + store.py).
#           A toolbar for the source and scene, and a canvas of blocks (3D twin, waterfalls, spectroscopy).
# PURPOSE:
#   1. Toolbar: live / pause, CAD import, scene reset, tensor snapshot to SQLite, engine + stats; oscillator
#      (FMCW / OFDM, start / stop, LC tank readout), TX position, animation.
#   2. Blocks (blocks.py) the user can split, rearrange, resize, close or replace.
#   3. Scene operations blocks call: assign_material(), add_custom_material(); after any scene change every
#      block's on_scene_changed() runs.
#   4. Remembers its scene (sweep, TX, materials, imported files) and block layout in its module settings.
# CONTRACT:
#   - Requires: app.modules.base.ModuleView, app.modules.wave_sim (engine, physics, scene, store, blocks),
#               app.native, app.data.em_materials, app.ui.ui_tools, QSS selectors #wave_sim_panel,
#               #module_controls_panel, #module_source_label, #module_stats_label, #control_label
#   - Exposes: WaveSimModule(parent=None, context=None) (ModuleView); import_mesh(); assign_material();
#              add_custom_material(); step_once(); attributes engine, scene, store, materials, last_result,
#              block_canvas
# NOTES:
#   - [2026-11-15] Created from the "first-principles physics engine" spec: LC-tank oscillator 1-10 GHz
#                  (FMCW / OFDM), spherical 1/r waves with e^{-jkr}, CAD import (trimesh), dielectric
#                  ray tracing (C++ devos_core), 4D tensor (nodes x time x freq x [amp, phase]).
#   - [2026-11-15] Without a context (no database) it runs with the built-in materials and saves nothing.
#   - [2026-11-15] Rebuilt on ModuleView with blocks (health pass + modules-as-canvases): the fixed
#                  splitter, copied live timer / settings helpers / plot styling are gone.
#   - [2026-11-15] Module toolbar's top row ends with reserve_controls_space(): module tiles now carry the
#                  fading corner controls (no title bar), which must not cover the toolbar.
#   - [2026-11-15] CAD imports are copied into the user's content library (scene keeps the item id; old
#                  saved paths still load). Engine, imports, materials, snapshots and resets are logged.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - DEFAULT_IMPORT_MATERIAL (str): Material given to newly imported meshes (default: "PTFE / plastic").
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. WAVE_SIM_MODULE
#    2.1 init_module
#    2.2 restore_settings
#    2.3 build_controls
#    2.4 live_loop
#    2.5 scene_operations
#    2.6 handlers
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
import time
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
    QWidget,
)

from app import native
from app.data.activity_log import get_activity_log
from app.data.em_materials import MATERIAL_BY_NAME
from app.data.store.content import get_content_store
from app.modules.base import ModuleView
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.modules.wave_sim.blocks import DEFAULT_BLOCKS, block_specs
from app.modules.wave_sim.engine import WaveSimEngine
from app.modules.wave_sim.physics import WAVEFORM_FMCW, WAVEFORM_OFDM, SweepSettings, lc_tank_capacitance_f
from app.modules.wave_sim.scene import default_room, load_mesh_file
from app.modules.wave_sim.store import WaveSimStore
from app.ui.ui_tools import control_label

CAD_FILE_FILTER = "CAD meshes (*.stl *.obj *.ply *.off *.glb *.gltf)"
# --- END [1.1] ---


# ==============================================================================
# [2.1] init_module
# ==============================================================================
class WaveSimModule(ModuleView):
    LIVE_INTERVAL_MS = 50
    DEFAULT_IMPORT_MATERIAL = "PTFE / plastic"

    def __init__(self, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, context, "wave_sim_panel")
        self.store = WaveSimStore(context.database, context.instance_id) if context is not None else None
        self.materials = self.store.materials() if self.store else dict(MATERIAL_BY_NAME)
        self.log = get_activity_log()
        self.scene = default_room()
        self.engine = WaveSimEngine(self.scene, SweepSettings(), self.materials)
        self.log.info("wave_sim", f"Ray tracer: {native.ENGINE_NAME if native.HAS_NATIVE else 'NumPy fallback (C++ engine not built)'}")
        self.last_result = None
        self._frame_times: list[float] = []
        self._last_stats_time = 0.0
        self._restore_settings()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self._build_controls())
        self.block_canvas = self.build_block_canvas(block_specs(self), DEFAULT_BLOCKS)
        layout.addWidget(self.block_canvas, 1)
# --- END [2.1] ---

# ==============================================================================
# [2.2] restore_settings
# ==============================================================================
    def _restore_settings(self) -> None:
        sweep = self.setting("sweep")
        if sweep:
            self.engine.set_sweep(SweepSettings(**sweep))
        tx = self.setting("tx_position_m")
        if tx:
            self.scene.tx_position_m = np.array(tx, float)
        library = get_content_store()
        for imported in self.setting("imported_meshes", []):
            item = library.get(imported["item_id"]) if imported.get("item_id") else None
            path = library.path(item) if item else Path(imported.get("path", ""))   # "path": scenes saved earlier
            if not path.exists() or imported.get("material") not in self.materials:
                self.log.warning("wave_sim", f"Scene mesh missing, skipped: {item.name if item else path.name}")
                continue
            try:
                place_at = None if imported.get("place_at_m") is None else tuple(imported["place_at_m"])
                body = load_mesh_file(path, imported["material"], place_at)
                body.name, body.library_item_id = (item.name if item else body.name), (item.item_id if item else None)
                self.scene.bodies.append(body)
            except (ValueError, OSError) as error:
                self.log.error("wave_sim", f"Could not load scene mesh {path.name}", error)
        assignments = self.setting("body_materials", {})
        for body in self.scene.bodies:
            if assignments.get(body.name) in self.materials:
                body.material_name = assignments[body.name]
        self.engine.is_animating = self.setting("animate", True)
# --- END [2.2] ---

# ==============================================================================
# [2.3] build_controls
# ==============================================================================
    def _spin(self, low: float, high: float, value: float, step: float, suffix: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(minimum=low, maximum=high, value=value, singleStep=step, suffix=suffix, decimals=2)
        spin.setKeyboardTracking(False)
        return spin

    def _build_controls(self) -> QWidget:
        panel = QWidget(objectName="module_controls_panel")
        rows = QVBoxLayout(panel)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(4)

        top = QHBoxLayout()
        top.addWidget(self.setup_live(self.LIVE_INTERVAL_MS, self.step_once))
        self.import_cad_btn = QPushButton("Import CAD…", toolTip="Add an .stl / .obj mesh as a body (mm auto-detected)")
        self.import_cad_btn.clicked.connect(self._on_import_cad)
        self.reset_scene_btn = QPushButton("Reset scene", toolTip="Back to the default room (removes imported meshes)")
        self.reset_scene_btn.clicked.connect(self._on_reset_scene)
        self.snapshot_btn = QPushButton("Snapshot → DB", toolTip="Save the current 4D tensor into SQLite")
        self.snapshot_btn.clicked.connect(self._on_snapshot)
        self.snapshot_btn.setEnabled(self.store is not None)
        self.module_source_label = QLabel(objectName="module_source_label")
        self.module_source_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.module_stats_label = QLabel(objectName="module_stats_label")
        for widget in (self.import_cad_btn, self.reset_scene_btn, self.snapshot_btn):
            top.addWidget(widget)
        top.addWidget(self.module_source_label, 1)
        top.addWidget(self.module_stats_label)
        reserve_controls_space(top)
        rows.addLayout(top)

        sweep = self.engine.sweep
        oscillator = QHBoxLayout()
        self.waveform_combo = QComboBox(objectName="waveform_combo")
        self.waveform_combo.addItems([WAVEFORM_FMCW, WAVEFORM_OFDM])
        self.waveform_combo.setCurrentText(sweep.waveform)
        self.start_ghz_spin = self._spin(1.0, 9.9, sweep.start_hz / 1e9, 0.1, " GHz")
        self.stop_ghz_spin = self._spin(1.1, 10.0, sweep.stop_hz / 1e9, 0.1, " GHz")
        self.lc_tank_label = control_label()
        self.lc_tank_label.setToolTip("f = 1 / (2π√(LC)): the varactor capacitance that tunes the tank across the sweep")
        tx = self.scene.tx_position_m
        self.tx_spins = [self._spin(-5.0, 5.0, float(tx[axis]), 0.1, " m") for axis in range(3)]
        self.animate_check = QCheckBox("Animate", checked=self.engine.is_animating, toolTip="Walk the person through the room")
        for widget in (control_label("oscillator"), self.waveform_combo, self.start_ghz_spin, control_label("→"),
                       self.stop_ghz_spin, self.lc_tank_label):
            oscillator.addWidget(widget)
        oscillator.addStretch()
        oscillator.addWidget(control_label("TX"))
        for axis, spin in zip("xyz", self.tx_spins):
            oscillator.addWidget(control_label(axis))
            oscillator.addWidget(spin)
            spin.valueChanged.connect(self._on_tx_changed)
        oscillator.addWidget(self.animate_check)
        rows.addLayout(oscillator)

        self.waveform_combo.currentTextChanged.connect(self._on_sweep_changed)
        self.start_ghz_spin.valueChanged.connect(self._on_sweep_changed)
        self.stop_ghz_spin.valueChanged.connect(self._on_sweep_changed)
        self.animate_check.toggled.connect(self._on_animate_toggled)
        self._update_lc_label()
        return panel
# --- END [2.3] ---

# ==============================================================================
# [2.4] live_loop
# ==============================================================================
    def step_once(self) -> None:
        """Advance the simulation by one frame and redraw every block (the live timer calls this)."""
        self.last_result = self.engine.step(self.LIVE_INTERVAL_MS / 1000)
        now = time.perf_counter()
        self._frame_times = [stamp for stamp in self._frame_times if now - stamp < 1.0] + [now]
        for block in self.block_canvas.blocks():
            block.refresh()
        self._refresh_stats()

    def _refresh_stats(self) -> None:
        now = time.monotonic()
        if now - self._last_stats_time < 0.4 or self.last_result is None:
            return
        self._last_stats_time = now
        result = self.last_result
        engine_text = "C++ " + native.ENGINE_NAME.split(" (")[0] if native.HAS_NATIVE else "NumPy fallback"
        self.module_source_label.setText(
            f"engine: {engine_text}  ·  {len(self.scene.bodies)} bodies, {sum(len(b.faces) for b in self.scene.bodies)} "
            f"triangles  ·  tensor {'×'.join(map(str, self.engine.tensor.shape))} float32")
        self.module_source_label.setToolTip(native.ENGINE_NAME)
        self.module_stats_label.setText(
            f"{len(self._frame_times)} fps  ·  {int((result.paths.reflect_body >= 0).sum())} bounces  ·  "
            f"trace {result.trace_ms:.1f} ms  ·  synth {result.synth_ms:.1f} ms  ·  t {result.timestamp_s:.1f} s")
# --- END [2.4] ---

# ==============================================================================
# [2.5] scene_operations
# ==============================================================================
    def _scene_changed(self) -> None:
        for block in self.block_canvas.blocks():
            hook = getattr(block, "on_scene_changed", None)
            if callable(hook):
                hook()

    def assign_material(self, body_name: str, material_name: str) -> None:
        body = self.scene.bodies[self.scene.body_index(body_name)]
        body.material_name = material_name
        self.log.info("wave_sim", f"{body_name} is now {material_name}")
        assignments = self.setting("body_materials", {})
        assignments[body_name] = material_name
        self.save_setting("body_materials", assignments)
        self._persist_imports()
        self._scene_changed()

    def add_custom_material(self, name: str, permittivity: float, conductivity_s_m: float) -> str | None:
        """Save a custom material; returns an error message or None."""
        if self.store is None:
            return "Custom materials need the database (open this module from a page)."
        try:
            self.store.add_custom_material(name, permittivity, conductivity_s_m)
        except ValueError as error:
            return str(error)
        self.materials = self.store.materials()
        self.engine.set_materials(self.materials)
        self.log.success("wave_sim", f"Custom material “{name}” saved (ε′ {permittivity:g}, σ {conductivity_s_m:g} S/m)")
        self._scene_changed()
        return None

    def import_mesh(self, file_path: str, material_name: str | None = None, place_at_m=(0.0, 1.0)) -> None:
        """Copy a mesh file into the user's library and add it to the scene (also used by tests and scripts)."""
        library = get_content_store()
        try:
            with self.log.step("wave_sim", f"Import CAD model {Path(file_path).name}"):
                body = load_mesh_file(file_path, material_name or self.DEFAULT_IMPORT_MATERIAL, place_at_m)
                item = library.add_file("cad", file_path, metadata={"triangles": int(len(body.faces))})
        except (ValueError, OSError):
            return                                               # step() logged the reason
        body.library_item_id = item.item_id
        self.log.info("wave_sim", f"Added “{item.name}” to the scene: {len(body.faces):,} triangles, {body.material_name}")
        existing = {each.name for each in self.scene.bodies}
        while body.name in existing:
            body.name += "′"
        self.scene.bodies.append(body)
        self._persist_imports()
        self._scene_changed()

    def _persist_imports(self) -> None:
        self.save_setting("imported_meshes", [
            {"item_id": getattr(body, "library_item_id", None), "path": body.source_path, "material": body.material_name,
             "place_at_m": list(body.place_at_m) if body.place_at_m else None}
            for body in self.scene.bodies if body.source_path])
# --- END [2.5] ---

# ==============================================================================
# [2.6] handlers
# ==============================================================================
    def _update_lc_label(self) -> None:
        sweep = self.engine.sweep
        capacitance = lc_tank_capacitance_f(sweep.tank_inductance_h, np.array([sweep.start_hz, sweep.stop_hz])) * 1e12
        self.lc_tank_label.setText(f"LC tank {sweep.tank_inductance_h * 1e9:.1f} nH · C {capacitance[0]:.2f}→{capacitance[1]:.3f} pF")

    def _on_sweep_changed(self) -> None:
        start_ghz, stop_ghz = self.start_ghz_spin.value(), self.stop_ghz_spin.value()
        if stop_ghz <= start_ghz + 0.05:
            return
        sweep = SweepSettings(start_ghz * 1e9, stop_ghz * 1e9, self.engine.sweep.bin_count,
                              self.waveform_combo.currentText(), self.engine.sweep.tank_inductance_h)
        self.engine.set_sweep(sweep)
        self.save_setting("sweep", sweep.to_dict())
        self._update_lc_label()
        self._scene_changed()                        # spectroscopy curves follow the sweep

    def _on_tx_changed(self) -> None:
        self.scene.tx_position_m = np.array([spin.value() for spin in self.tx_spins])
        self.save_setting("tx_position_m", self.scene.tx_position_m.tolist())

    def _on_animate_toggled(self, is_animating: bool) -> None:
        self.engine.is_animating = is_animating
        self.save_setting("animate", is_animating)

    def _on_import_cad(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "Import CAD mesh", "", CAD_FILE_FILTER)
        if file_path:
            self.import_mesh(file_path)

    def _on_reset_scene(self) -> None:
        tx = self.scene.tx_position_m
        self.scene = default_room()
        self.scene.tx_position_m = tx
        self.engine.scene = self.scene
        self.engine.reset_tensor()
        self.save_setting("imported_meshes", [])
        self.save_setting("body_materials", {})
        self.log.info("wave_sim", "Scene reset to the default room (imported models stay in your library)")
        self._scene_changed()

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
        self.log.success("wave_sim", f"Tensor snapshot #{snapshot_id} saved ({tensor.data.nbytes / 1e6:.1f} MB)")
        self.module_stats_label.setText(f"Saved snapshot #{snapshot_id} ({tensor.data.nbytes / 1e6:.1f} MB) · "
                                        f"{len(self.store.list_snapshots())} stored")
# --- END [2.6] ---
