# ==============================================================================
# FILE: app/modules/cad/view.py
# OVERVIEW: CAD module - the user's CAD models (library): a 3D viewer, the model list and measurements;
#           opens the Part Design workbench (separate window) to design or edit parts step by step.
# PURPOSE:
#   1. Toolbar: New part, Edit part (parts only), Import model…, status line.
#   2. Blocks: Viewer (3D), Models (library CAD items: parts and meshes), Info (size, volume, mass by material).
# CONTRACT:
#   - Requires: app.modules.base.ModuleView, app.modules.cad (engine, mesh_view, workbench),
#               app.clients.library, app.ui (ui_tools, tool_bag, canvas), QSS selector #cad_panel
#   - Exposes: CadModule(parent=None, context=None) with item_id, mesh, open_item(), new_part(), edit_part(),
#              import_model(), workbench; ViewerBlock, ModelsBlock, InfoBlock
# NOTES:
#   - [2026-11-17] Library access goes through the Knowledge engine (app.clients.library), not the content store (Phase 4).
#   - [2026-11-16] Created (CAD module + Part Design workbench).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - part item: A library CAD item whose metadata format is "devos-part" (JSON sketches + features).
# - mesh item: Any other CAD item (.stl, .obj, .ply, .off, .glb).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. BLOCKS
#    1.1 imports
#    1.2 viewer_block
#    1.3 models_block
#    1.4 info_block
# 2. MODULE
#    2.1 cad_module
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from app.data.activity_log import get_activity_log
from app.clients.library import library
from app.data.content_kinds import kind_for_path
from app.modules.base import ModuleView
from app.modules.cad.engine import MATERIAL_DENSITIES, PART_FORMAT, CadError, Part, cad_unavailable_reason, load_mesh, measure
from app.modules.cad.mesh_view import MeshViewer
from app.ui.canvas.block_canvas import BlockSpec
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.tool_bag import ToolSpec, register_tools
from app.ui.ui_tools import block_layout, control_label, set_status, status_label
from app.ui.engine_bridge import watch_library

MESH_FILTER = "Meshes (*.stl *.obj *.ply *.off *.glb *.gltf)"
DEFAULT_BLOCKS = {"split": "h", "sizes": [1, 3], "children": [
    {"split": "v", "sizes": [3, 2], "children": [{"kind": "models"}, {"kind": "info"}]},
    {"kind": "viewer"},
]}
# --- END [1.1] ---

# ==============================================================================
# [1.2] viewer_block
# ==============================================================================
class ViewerBlock(QWidget):
    def __init__(self, module: "CadModule") -> None:
        super().__init__()
        self.module = module
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = MeshViewer()
        layout.addWidget(self.viewer)
        module.model_changed.connect(lambda: self.viewer.set_mesh(module.mesh))
        self.viewer.set_mesh(module.mesh)
# --- END [1.2] ---

# ==============================================================================
# [1.3] models_block
# ==============================================================================
class ModelsBlock(QWidget):
    def __init__(self, module: "CadModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        row.addWidget(control_label("CAD models in your library"))
        row.addStretch()
        reserve_controls_space(row)
        self.models_list = QListWidget(objectName="models_list")
        self.models_list.itemClicked.connect(lambda item: module.open_item(item.data(Qt.ItemDataRole.UserRole)))
        self.models_list.itemDoubleClicked.connect(lambda item: module.edit_part() if module.is_part(item.data(Qt.ItemDataRole.UserRole)) else None)
        layout.addWidget(self.models_list, 1)
        layout.addWidget(control_label("Double-click a part to edit it in Part Design"))
        module.models_changed.connect(self.refresh)
        module.model_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self.models_list.clear()
        for item in library().items("cad"):
            is_part = item.metadata.get("format") == PART_FORMAT
            entry = QListWidgetItem(("⚙ " if is_part else "▲ ") + item.name)
            entry.setData(Qt.ItemDataRole.UserRole, item.item_id)
            entry.setToolTip("Part (editable)" if is_part else item.extension.upper().lstrip(".") + " mesh")
            self.models_list.addItem(entry)
            if item.item_id == self.module.item_id:
                self.models_list.setCurrentItem(entry)
# --- END [1.3] ---

# ==============================================================================
# [1.4] info_block
# ==============================================================================
class InfoBlock(QWidget):
    def __init__(self, module: "CadModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        row.addWidget(control_label("Material"))
        self.material_combo = QComboBox()
        self.material_combo.addItems(list(MATERIAL_DENSITIES))
        self.material_combo.currentIndexChanged.connect(self.refresh)
        row.addWidget(self.material_combo, 1)
        reserve_controls_space(row)
        self.info_label = QLabel(objectName="module_summary_label", wordWrap=True)
        self.info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.info_label)
        layout.addStretch()
        module.model_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        mesh = self.module.mesh
        if mesh is None:
            self.info_label.setText("Pick a model, or design one with New part.")
            return
        info = measure(mesh, MATERIAL_DENSITIES[self.material_combo.currentText()])
        size = info["size_mm"]
        lines = [f"{size[0]:.2f} × {size[1]:.2f} × {size[2]:.2f} mm", f"{info['triangles']:,} triangles",
                 f"Area {info['area_mm2']:,.0f} mm²"]
        if not math.isnan(info["volume_mm3"]):
            lines += [f"Volume {info['volume_mm3'] / 1000:,.2f} cm³", f"Mass {info['mass_g']:,.1f} g ({self.material_combo.currentText()})"]
        else:
            lines.append("Not watertight (no volume)")
        self.info_label.setText("\n".join(lines))

    def block_state(self) -> dict:
        return {"material": self.material_combo.currentText()}

    def restore_block_state(self, state: dict) -> None:
        self.material_combo.setCurrentText(state.get("material", "PLA"))
# --- END [1.4] ---


# ==============================================================================
# [2.1] cad_module
# ==============================================================================
class CadModule(ModuleView):
    models_changed = Signal()
    model_changed = Signal()

    def __init__(self, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, context, "cad_panel")
        self.log = get_activity_log()
        self.store = library()
        self.item_id: int | None = None
        self.mesh = None
        self.workbench = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        reason = cad_unavailable_reason()
        if reason:
            layout.addWidget(QLabel(f"CAD can't start.\n{reason}", objectName="module_error_label", wordWrap=True,
                                    alignment=Qt.AlignmentFlag.AlignCenter))
            return
        row = QHBoxLayout()
        new_btn = QPushButton("New part", toolTip="Design a part in the Part Design workbench")
        new_btn.clicked.connect(self.new_part)
        self.edit_btn = QPushButton("Edit part", toolTip="Open the selected part in Part Design")
        self.edit_btn.clicked.connect(self.edit_part)
        import_btn = QPushButton("Import model…", toolTip="Copy a mesh (.stl, .obj …) into your library")
        import_btn.clicked.connect(self.import_model)
        for button in (new_btn, self.edit_btn, import_btn):
            row.addWidget(button)
        self.module_status_label = status_label()
        row.addWidget(self.module_status_label, 1)
        reserve_controls_space(row)
        layout.addLayout(row)
        self.block_canvas = self.build_block_canvas([
            BlockSpec("viewer", "3D viewer", "The selected model in 3D (drag to orbit, wheel to zoom)", lambda: ViewerBlock(self)),
            BlockSpec("models", "Models", "CAD models in your library: parts and meshes", lambda: ModelsBlock(self)),
            BlockSpec("info", "Info", "Size, area, volume and mass of the selected model", lambda: InfoBlock(self)),
        ], DEFAULT_BLOCKS)
        layout.addWidget(self.block_canvas, 1)
        watch_library(self, self._on_content_changed)
        saved = self.setting("item_id")
        if saved is not None and self.store.get(saved) is not None:
            self.open_item(saved)
        self._update_buttons()

    def _on_content_changed(self, kind: str) -> None:
        if kind == "cad":
            self.models_changed.emit()

    def is_part(self, item_id: int | None) -> bool:
        item = self.store.get(item_id) if item_id is not None else None
        return item is not None and item.metadata.get("format") == PART_FORMAT

    def _update_buttons(self) -> None:
        self.edit_btn.setEnabled(self.is_part(self.item_id))

    def open_item(self, item_id: int, force: bool = False) -> None:
        if item_id == self.item_id and not force:
            return
        item = self.store.get(item_id)
        if item is None:
            return
        try:
            with self.log.step("cad", f"Open model “{item.name}”"):
                self.mesh = load_mesh(self.store.path(item))
            set_status(self.module_status_label, f"{item.name}")
        except CadError as error:
            self.mesh = None
            set_status(self.module_status_label, f"Couldn't open “{item.name}”: {error}", is_error=True)
        self.item_id = item_id
        self.save_setting("item_id", item_id)
        self._update_buttons()
        self.model_changed.emit()

    def new_part(self) -> None:
        self._open_workbench(Part(self.store.unique_name("cad", "Part")), None)

    def edit_part(self) -> None:
        if not self.is_part(self.item_id):
            return
        try:
            part = Part.from_dict(json.loads(self.store.path(self.item_id).read_text(encoding="utf-8")))
        except (OSError, ValueError, CadError) as error:
            set_status(self.module_status_label, f"Couldn't read the part: {error}", is_error=True)
            return
        self._open_workbench(part, self.item_id)

    def _open_workbench(self, part: Part, item_id: int | None) -> None:
        from app.modules.cad.workbench import PartDesignWindow
        if self.workbench is not None:
            try:
                self.workbench.close()
            except RuntimeError:
                pass
        self.workbench = PartDesignWindow(part, item_id)
        self.workbench.saved.connect(lambda saved_id: self.open_item(saved_id, force=True))
        self.workbench.destroyed.connect(lambda *_: setattr(self, "workbench", None))
        self.workbench.setWindowTitle(f"Part Design — {part.name}")
        self.workbench.show()
        self.log.info("cad", f"Part Design opened for “{part.name}”")

    def import_model(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Import model", "", MESH_FILTER)
        if not path:
            return
        if kind_for_path(path) != "cad":
            set_status(self.module_status_label, "That isn't a mesh file DevOS knows", is_error=True)
            return
        with self.log.step("cad", f"Import {Path(path).name}"):
            item = self.store.add_file("cad", path)
        self.open_item(item.item_id)


register_tools([
    ToolSpec("cad.new_part", "New part", "⚙", "Design a new part in the Part Design workbench",
             lambda module: module.new_part(), "cad", "design sketch"),
    ToolSpec("cad.edit_part", "Edit part", "✎", "Open the selected part in Part Design", lambda module: module.edit_part(),
             "cad", "modify"),
])
# --- END [2.1] ---
