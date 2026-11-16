# ==============================================================================
# FILE: app/modules/cad/mesh_view.py
# OVERVIEW: MeshViewer - a 3D view of one mesh (orbit, zoom) for the CAD module and the Part Design
#           workbench; falls back to a message when OpenGL isn't usable (a failing GL widget crashes the app).
# PURPOSE:
#   1. set_mesh(mesh): show a trimesh mesh shaded, centred, with a ground grid sized to it; None clears.
# CONTRACT:
#   - Requires: app.ui.gl_support (gl, opengl_unavailable_reason), numpy, app.ui.theme, QSS selector
#               #scatter_unavailable_label (the shared "3D unavailable" style)
#   - Exposes: MeshViewer(parent=None) with set_mesh(mesh | None), is_3d
# NOTES:
#   - [2026-11-16] Created with the CAD module.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - MESH_COLOR (tuple): RGBA (0-1) of the shaded part.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. VIEWER
#    1.1 mesh_viewer
# ==============================================================================

# ==============================================================================
# [1.1] mesh_viewer
# ==============================================================================
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.ui.gl_support import gl, opengl_unavailable_reason
from app.ui.theme import BACKGROUND_COLOR

MESH_COLOR = (0.62, 0.78, 0.95, 1.0)


class MeshViewer(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        reason = opengl_unavailable_reason()
        self.is_3d = not reason
        self.mesh_item = self.grid_item = None
        if reason:
            self.fallback_label = QLabel(f"3D view unavailable: {reason}", objectName="scatter_unavailable_label",
                                         alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
            layout.addWidget(self.fallback_label)
            return
        self.view = gl.GLViewWidget()
        self.view.setBackgroundColor(BACKGROUND_COLOR)
        self.view.setCameraPosition(distance=120, elevation=28, azimuth=-60)
        self.grid_item = gl.GLGridItem()
        self.grid_item.setColor((90, 90, 90, 120))
        self.view.addItem(self.grid_item)
        self.view.addItem(gl.GLAxisItem(size=pg_vector(20, 20, 20)))
        layout.addWidget(self.view)

    def set_mesh(self, mesh) -> None:
        if not self.is_3d:
            self.fallback_label.setText("3D view unavailable here (no OpenGL)." + (
                "" if mesh is None else f"\nMesh: {len(mesh.faces)} triangles, "
                f"{' × '.join(f'{v:.1f}' for v in (mesh.bounds[1] - mesh.bounds[0]))} mm"))
            return
        if self.mesh_item is not None:
            self.view.removeItem(self.mesh_item)
            self.mesh_item = None
        if mesh is None:
            return
        self.mesh_item = gl.GLMeshItem(meshdata=gl.MeshData(vertexes=np.asarray(mesh.vertices, dtype=float),
                                                            faces=np.asarray(mesh.faces)),
                                       color=MESH_COLOR, smooth=False, shader="shaded", drawEdges=False)
        self.view.addItem(self.mesh_item)
        low, high = mesh.bounds
        size = float(max(high - low)) or 1.0
        spacing = 10 ** int(np.floor(np.log10(size))) / 2 or 1
        self.grid_item.setSize(size * 2, size * 2)
        self.grid_item.setSpacing(spacing, spacing)
        centre = (low + high) / 2
        self.view.opts["center"] = pg_vector(*centre)
        self.view.setCameraPosition(distance=size * 1.7)


def pg_vector(x: float, y: float, z: float):
    from PySide6.QtGui import QVector3D
    return QVector3D(float(x), float(y), float(z))
# --- END [1.1] ---
