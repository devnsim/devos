# ==============================================================================
# FILE: app/modules/wave_sim/blocks.py
# OVERVIEW: The wave simulator's views as blocks (split / rearrange / resize / close / replace).
# PURPOSE:
#   1. TwinBlock: 3D spatial twin - CAD bodies coloured by material, TX / RX spheres, rays coloured by
#      excess loss for one receiver or all (the block's own choice).
#   2. WaterfallBlock: one receiver's |H| (dB) or phase across the sweep over time, newest on top.
#   3. SpectroscopyBlock: eps' / eps'' of a body's material across the sweep; assign materials, add
#      custom materials (to SQLite through the module).
# CONTRACT:
#   - Requires: numpy, pyqtgraph (+ opengl), app.data.em_materials (c), app.ui (gl_support, plot_tools,
#               ui_tools, theme). Blocks read the module: scene, engine, materials, last_result and call
#               module.assign_material() / module.add_custom_material().
#   - Exposes: TwinBlock, WaterfallBlock, SpectroscopyBlock, block_specs(module), DEFAULT_BLOCKS
#              Hooks the module calls: refresh() every frame, on_scene_changed() after imports / resets /
#              material edits.
# NOTES:
#   - [2026-11-15] Created from the old fixed-splitter WaveSimModule (health pass + modules-as-canvases).
#                  Receiver / display / body choices are per block and saved via block_state().
#   - [2026-11-15] Block top rows end with reserve_controls_space() (room for the corner controls).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - excess loss (dB): Ray gain relative to free space over the same length (engine.py).
# - LOSS_COLOR_RANGE_DB (float): Excess loss mapped to full red (default: 30 dB).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS_AND_CONSTANTS
#    1.1 imports
# 2. BLOCKS
#    2.1 twin_block
#    2.2 waterfall_block
#    2.3 spectroscopy_block
# 3. REGISTRY
#    3.1 block_specs
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from app.data.em_materials import SPEED_OF_LIGHT_M_S
from app.ui.canvas.block_canvas import BlockSpec, reserve_controls_space
from app.ui.gl_support import gl, opengl_unavailable_reason
from app.ui.plot_tools import styled_plot
from app.ui.theme import BACKGROUND_COLOR
from app.ui.ui_tools import control_label

TX_COLOR, RX_COLOR = (1.0, 0.3, 0.25, 1.0), (0.3, 0.85, 1.0, 1.0)
WALL_ALPHA, SOLID_ALPHA = 0.16, 0.55
LOSS_COLOR_RANGE_DB = 30.0
WATERFALL_DB_RANGE = 60.0
MAX_RAYS_PER_NODE = 14
ALL_RECEIVERS = "All receivers"
DISPLAY_AMPLITUDE, DISPLAY_PHASE = "|H| (dB)", "Phase (rad)"
AMPLITUDE_LUT = pg.colormap.get("viridis").getLookupTable(nPts=256)
try:
    PHASE_LUT = pg.colormap.get("CET-C1").getLookupTable(nPts=256)          # cyclic, for phase
except Exception:
    PHASE_LUT = pg.colormap.get("plasma").getLookupTable(nPts=256)
DEFAULT_BLOCKS = {"split": "h", "sizes": [3, 2], "children": [
    {"kind": "twin"},
    {"split": "v", "sizes": [1, 1], "children": [{"kind": "waterfall"}, {"kind": "spectroscopy"}]},
]}


def _spin(low: float, high: float, value: float, step: float, suffix: str, decimals: int = 2) -> QDoubleSpinBox:
    spin = QDoubleSpinBox(minimum=low, maximum=high, value=value, singleStep=step, suffix=suffix, decimals=decimals)
    spin.setKeyboardTracking(False)
    return spin
# --- END [1.1] ---


# ==============================================================================
# [2.1] twin_block
# ==============================================================================
class TwinBlock(QWidget):
    def __init__(self, module) -> None:
        super().__init__()
        self.module = module
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(control_label("rays"))
        self.rays_combo = QComboBox(toolTip="Receiver whose rays are drawn")
        self.rays_combo.addItems(module.scene.rx_names + [ALL_RECEIVERS])
        self.rays_combo.currentIndexChanged.connect(self.refresh)
        row.addWidget(self.rays_combo)
        row.addStretch()
        reserve_controls_space(row)
        layout.addLayout(row)
        self.view = None
        self.body_items: list = []
        reason = opengl_unavailable_reason()
        if reason:
            layout.addWidget(QLabel(f"3D view unavailable: {reason}", objectName="scatter_unavailable_label",
                                    alignment=Qt.AlignmentFlag.AlignCenter), 1)
            return
        self.view = gl.GLViewWidget()
        self.view.setBackgroundColor(BACKGROUND_COLOR)
        grid = gl.GLGridItem(color=(255, 255, 255, 18))
        grid.setSize(10, 10)
        grid.setSpacing(0.5, 0.5)
        self.view.addItem(grid)
        self.ray_item = gl.GLLinePlotItem(mode="lines", width=1.6, antialias=True)
        self.view.addItem(self.ray_item)
        sphere = gl.MeshData.sphere(rows=10, cols=18, radius=0.09)
        self.tx_item = gl.GLMeshItem(meshdata=sphere, color=TX_COLOR, smooth=True, shader="shaded")
        self.view.addItem(self.tx_item)
        for name, position in zip(module.scene.rx_names, module.scene.rx_positions_m):
            rx_item = gl.GLMeshItem(meshdata=sphere, color=RX_COLOR, smooth=True, shader="shaded")
            rx_item.translate(*position)
            self.view.addItem(rx_item)
            self.view.addItem(gl.GLTextItem(pos=position + np.array([0, 0, 0.18]), text=name.split()[0],
                                            color=(180, 230, 255, 255)))
        self.view.opts["center"] = pg.Vector(0, 0, 1.0)
        self.view.setCameraPosition(distance=10.5, elevation=38, azimuth=-62)
        layout.addWidget(self.view, 1)
        self.on_scene_changed()

    def on_scene_changed(self) -> None:
        """(Re)create one mesh item per body - after import, reset, or material changes."""
        if self.view is None:
            return
        for item in self.body_items:
            self.view.removeItem(item)
        self.body_items = []
        for body in self.module.scene.bodies:
            material = self.module.materials[body.material_name]
            alpha = WALL_ALPHA if body.name.startswith(("Wall", "Floor")) else SOLID_ALPHA
            item = gl.GLMeshItem(
                meshdata=gl.MeshData(vertexes=body.vertices, faces=body.faces), color=(*material.color, alpha),
                smooth=False, shader="shaded", drawEdges=True, edgeColor=(*material.color, min(1.0, alpha + 0.3)),
                glOptions="translucent",
            )
            self.view.addItem(item)
            self.body_items.append(item)
        self._place_movers()

    def _place_movers(self) -> None:
        self.tx_item.resetTransform()
        self.tx_item.translate(*self.module.scene.tx_position_m)
        for body, item in zip(self.module.scene.bodies, self.body_items):
            if body.is_animated:
                item.resetTransform()
                item.translate(*body.offset_at(self.module.engine.time_s))

    def refresh(self) -> None:
        result = self.module.last_result
        if self.view is None or result is None:
            return
        self._place_movers()
        paths = result.paths
        nodes = range(self.module.scene.node_count)
        if self.rays_combo.currentText() != ALL_RECEIVERS:
            nodes = [self.rays_combo.currentIndex()]
        keep = []
        for node in nodes:
            node_paths = np.flatnonzero(paths.node == node)
            keep.extend(node_paths[np.argsort(paths.gain_db[node_paths])[::-1][:MAX_RAYS_PER_NODE]].tolist())
        self.ray_item.setVisible(bool(keep))
        if not keep:
            return
        keep = np.array(keep)
        points = paths.points[keep]                              # (K, 3, 3): TX, bounce, RX
        segments = np.concatenate([points[:, [0, 1]], points[:, [1, 2]]], axis=0).reshape(-1, 3)
        fraction = np.clip(-paths.excess_loss_db[keep] / LOSS_COLOR_RANGE_DB, 0.0, 1.0)
        colors = np.column_stack([np.clip(fraction * 3, 0, 1), np.clip(1.5 - 1.5 * fraction, 0, 1),
                                  np.full_like(fraction, 0.25), np.full_like(fraction, 0.9)])   # green -> red
        colors = np.repeat(colors, 2, axis=0)
        self.ray_item.setData(pos=segments, color=np.vstack([colors, colors]))

    def block_state(self) -> dict:
        return {"rays": self.rays_combo.currentText()}

    def restore_block_state(self, state: dict) -> None:
        if state.get("rays"):
            self.rays_combo.setCurrentText(state["rays"])
# --- END [2.1] ---

# ==============================================================================
# [2.2] waterfall_block
# ==============================================================================
class WaterfallBlock(QWidget):
    def __init__(self, module) -> None:
        super().__init__()
        self.module = module
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.node_combo = QComboBox()
        self.node_combo.addItems(module.scene.rx_names)
        self.display_combo = QComboBox()
        self.display_combo.addItems([DISPLAY_AMPLITUDE, DISPLAY_PHASE])
        for combo in (self.node_combo, self.display_combo):
            combo.currentIndexChanged.connect(self.refresh)
        row.addWidget(control_label("receiver"))
        row.addWidget(self.node_combo, 1)
        row.addWidget(self.display_combo)
        reserve_controls_space(row)
        layout.addLayout(row)
        self.plot = styled_plot("frequency (GHz)", "time step (newest top)")
        self.image = pg.ImageItem(axisOrder="row-major")
        self.plot.addItem(self.image)
        layout.addWidget(self.plot, 1)

    def refresh(self) -> None:
        tensor = self.module.engine.tensor
        if tensor.frame_count == 0:
            return
        history = tensor.node_history(self.node_combo.currentIndex())          # (time, F, 2)
        frequencies = self.module.engine.frequencies_hz
        sweep = self.module.engine.sweep
        if self.display_combo.currentText() == DISPLAY_PHASE:
            self.image.setLookupTable(PHASE_LUT)
            self.image.setImage(history[..., 1], levels=(-np.pi, np.pi), autoLevels=False)
        else:
            decibels = 20 * np.log10(history[..., 0] + 1e-12)
            peak = float(decibels.max())
            self.image.setLookupTable(AMPLITUDE_LUT)
            self.image.setImage(decibels, levels=(peak - WATERFALL_DB_RANGE, peak), autoLevels=False)
        step_ghz = (frequencies[-1] - frequencies[0]) / max(len(frequencies) - 1, 1) / 1e9
        # Newest row at the top of the axis, even while the history is still filling.
        self.image.setRect(QRectF(frequencies[0] / 1e9 - step_ghz / 2, tensor.time_steps - len(history),
                                  (frequencies[-1] - frequencies[0]) / 1e9 + step_ghz, len(history)))
        self.plot.getPlotItem().setXRange(sweep.start_hz / 1e9, sweep.stop_hz / 1e9, padding=0)
        self.plot.getPlotItem().setYRange(0, tensor.time_steps, padding=0)

    def block_state(self) -> dict:
        return {"node": self.node_combo.currentIndex(), "display": self.display_combo.currentText()}

    def restore_block_state(self, state: dict) -> None:
        self.node_combo.setCurrentIndex(state.get("node", 0))
        self.display_combo.setCurrentText(state.get("display", DISPLAY_AMPLITUDE))
# --- END [2.2] ---

# ==============================================================================
# [2.3] spectroscopy_block
# ==============================================================================
class SpectroscopyBlock(QWidget):
    def __init__(self, module) -> None:
        super().__init__()
        self.module = module
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        assign_row = QHBoxLayout()
        self.body_combo = QComboBox(toolTip="Body to inspect / re-assign")
        self.material_combo = QComboBox(toolTip="Material of the selected body")
        self.body_combo.currentIndexChanged.connect(self._on_body_selected)
        self.material_combo.currentTextChanged.connect(self._on_material_chosen)
        assign_row.addWidget(control_label("body"))
        assign_row.addWidget(self.body_combo, 1)
        assign_row.addWidget(control_label("material"))
        assign_row.addWidget(self.material_combo, 1)
        reserve_controls_space(assign_row)
        layout.addLayout(assign_row)
        custom_row = QHBoxLayout()
        self.custom_name_edit = QLineEdit(placeholderText="custom material name")
        self.custom_permittivity_spin = _spin(1.0, 100.0, 4.0, 0.1, "", 2)
        self.custom_conductivity_spin = _spin(0.0, 100.0, 0.01, 0.01, " S/m", 4)
        self.add_material_btn = QPushButton("Add material", toolTip="Save a constant-eps'/sigma material to the database")
        self.add_material_btn.clicked.connect(self._on_add_material)
        self.add_material_btn.setEnabled(module.store is not None)
        for widget, stretch in ((self.custom_name_edit, 1), (control_label("ε′"), 0), (self.custom_permittivity_spin, 0),
                                (control_label("σ"), 0), (self.custom_conductivity_spin, 0), (self.add_material_btn, 0)):
            custom_row.addWidget(widget, stretch)
        layout.addLayout(custom_row)
        self.plot = styled_plot("frequency (GHz)", "ε′ (slowing), ε″ (absorption)")
        plot_item = self.plot.getPlotItem()
        plot_item.setLogMode(y=True)
        plot_item.addLegend(offset=(-4, 4), labelTextSize="8pt")
        self.permittivity_curve = plot_item.plot(name="ε′  real part")
        self.loss_curve = plot_item.plot(name="ε″  loss part")
        layout.addWidget(self.plot, 1)
        self.summary_label = QLabel(objectName="module_summary_label", wordWrap=True)
        layout.addWidget(self.summary_label)
        self.on_scene_changed()

    def on_scene_changed(self) -> None:
        """Bodies or materials changed: refill the combos, keep the selection."""
        current = self.body_combo.currentText()
        for combo, items in ((self.body_combo, [body.name for body in self.module.scene.bodies]),
                             (self.material_combo, list(self.module.materials))):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(items)
            combo.blockSignals(False)
        if current:
            self.body_combo.setCurrentText(current)
        self._on_body_selected()

    def _selected_body(self):
        index = self.body_combo.currentIndex()
        bodies = self.module.scene.bodies
        return bodies[index] if 0 <= index < len(bodies) else None

    def _on_body_selected(self) -> None:
        body = self._selected_body()
        if body is None:
            return
        self.material_combo.blockSignals(True)
        self.material_combo.setCurrentText(body.material_name)
        self.material_combo.blockSignals(False)
        self.refresh_curves()

    def _on_material_chosen(self, material_name: str) -> None:
        body = self._selected_body()
        if body is not None and material_name in self.module.materials:
            self.module.assign_material(body.name, material_name)

    def _on_add_material(self) -> None:
        name = self.custom_name_edit.text().strip()
        if not name:
            return
        error = self.module.add_custom_material(name, self.custom_permittivity_spin.value(),
                                                self.custom_conductivity_spin.value())
        if error:
            self.summary_label.setText(error)
            return
        self.material_combo.setCurrentText(name)                 # assigns it to the selected body

    def refresh(self) -> None:
        """Nothing changes per frame; curves update on scene / sweep changes."""

    def refresh_curves(self) -> None:
        body = self._selected_body()
        if body is None:
            return
        material = self.module.materials[body.material_name]
        sweep = self.module.engine.sweep
        frequencies = np.linspace(sweep.start_hz, sweep.stop_hz, 120)
        color = tuple(int(255 * c) for c in material.color)
        self.permittivity_curve.setData(frequencies / 1e9, np.maximum(np.real(material.complex_permittivity(frequencies)), 1e-6),
                                        pen=pg.mkPen(color, width=2))
        self.loss_curve.setData(frequencies / 1e9, np.maximum(material.loss_permittivity(frequencies), 1e-6),
                                pen=pg.mkPen(color, width=2, style=Qt.PenStyle.DashLine))
        center = sweep.center_hz
        index = complex(np.asarray(material.refractive_index(center)))
        k0 = 2 * np.pi * center / SPEED_OF_LIGHT_M_S
        gamma = abs(complex(np.asarray(material.reflection_coefficient(center))))
        if material.is_conductor:
            detail = "perfect conductor: no transmission, |Γ| = 1"
        else:
            detail = (f"n = {index.real:.2f} − {-index.imag:.3f}j · slowing {np.degrees(k0 * (index.real - 1) * 0.01):.0f}°/cm · "
                      f"absorption {8.686 * k0 * (-index.imag) * 0.01:.3f} dB/cm · |Γ| {gamma:.2f}")
        self.summary_label.setText(f"<b>{body.name}</b> — {material.name} ({material.source})<br>"
                                   f"at {center / 1e9:.2f} GHz: {detail}")

    def block_state(self) -> dict:
        return {"body": self.body_combo.currentText()}

    def restore_block_state(self, state: dict) -> None:
        if state.get("body"):
            self.body_combo.setCurrentText(state["body"])
# --- END [2.3] ---


# ==============================================================================
# [3.1] block_specs
# ==============================================================================
def block_specs(module) -> list[BlockSpec]:
    return [
        BlockSpec("twin", "3D twin", "CAD scene, TX / RX and attenuation-coloured rays", lambda: TwinBlock(module)),
        BlockSpec("waterfall", "Waterfall", "One receiver's |H| or phase across the sweep over time",
                  lambda: WaterfallBlock(module)),
        BlockSpec("spectroscopy", "Spectroscopy", "A body's ε′ / ε″ across the sweep; assign materials",
                  lambda: SpectroscopyBlock(module)),
    ]
# --- END [3.1] ---
