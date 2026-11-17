# ==============================================================================
# FILE: app/modules/cad/workbench.py
# OVERVIEW: Part Design workbench - a separate window that walks through designing a part:
#           1 Sketch → 2 Features → 3 Check → 4 Save & export, with a live 3D preview and the feature
#           history (click a feature to see the part as it was at that step).
# PURPOSE:
#   1. SketchCanvas: draw rectangles, circles and polygons on a millimetre grid (snap), select / delete,
#      zoom with the wheel, pan with the right or middle button; other sketches show faintly.
#   2. PartDesignWindow: the four steps (each unlocks when the previous one is done), feature dialogs
#      (pad / pocket / hole), numeric shape editing, measurements and mass, saving the part (JSON) and STL
#      into the library.
# CONTRACT:
#   - Requires: app.modules.cad (engine, mesh_view), app.clients.library, app.data.activity_log, app.ui.theme,
#               app.ui.widgets.step_bar.StepProgressBar, QSS selectors #part_design_window, #create_new_label
#   - Exposes: PartDesignWindow(part, item_id=None, parent=None) with signal saved(int item_id); SketchCanvas;
#              STEPS
# NOTES:
#   - [2026-11-17] Library access goes through the Knowledge engine (app.clients.library), not the content store (Phase 4).
#   - [2026-11-17] The 1-4 step list on the left became a progress bar across the top (StepProgressBar): name,
#                  steps and Back / Next in one row, the hint under it, the work area full width.
#   - [2026-11-16] Created (CAD part design workflow in its own window).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - step: One stage of the workflow (Sketch, Features, Check, Save & export); done = the next can start.
# - rollback: Previewing the part after only the first N features (click a feature in the history).
# - GRID_MM (float): Snap step of the sketch canvas (default: 1 mm; 5 mm when zoomed far out).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SKETCH_CANVAS
#    1.1 imports
#    1.2 sketch_canvas
# 2. DIALOGS
#    2.1 parameter_dialog
# 3. WINDOW
#    3.1 init
#    3.2 steps
#    3.3 sketch_step
#    3.4 features_step
#    3.5 check_and_export
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMessageBox, QPushButton, QSplitter, QStackedWidget, QToolButton, QVBoxLayout,
    QWidget,
)

from app.data.activity_log import get_activity_log
from app.clients.library import library
from app.modules.cad.engine import MATERIAL_DENSITIES, PART_FORMAT, CadError, Feature, Part, Shape, Sketch, measure
from app.modules.cad.mesh_view import MeshViewer
from app.ui.theme import (
    ACCENT_HIGHLIGHT_COLOR, BACKGROUND_COLOR, BORDER_COLOR, EDGE_COLOR, ERROR_COLOR, MUTED_TEXT_COLOR, SUCCESS_COLOR,
    TEXT_COLOR,
)
from app.ui.widgets.step_bar import StepProgressBar

STEPS = ("Sketch", "Features", "Check", "Save & export")
GRID_MM = 1.0
# --- END [1.1] ---

# ==============================================================================
# [1.2] sketch_canvas
# ==============================================================================
class SketchCanvas(QWidget):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(320, 260)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.sketch: Sketch | None = None
        self.ghosts: list[Sketch] = []
        self.tool = "rect"
        self.scale = 6.0                                # px per mm
        self.origin = QPointF(0, 0)                     # world point at the widget centre
        self.selected: int | None = None
        self._start: QPointF | None = None              # world point where a drag began
        self._points: list[QPointF] = []                # polygon in progress
        self._cursor = QPointF(0, 0)
        self._pan_from: QPointF | None = None

    # --- coordinates ---
    def to_screen(self, point: QPointF) -> QPointF:
        return QPointF(self.width() / 2 + (point.x() - self.origin.x()) * self.scale,
                       self.height() / 2 - (point.y() - self.origin.y()) * self.scale)

    def to_world(self, point: QPointF, snap: bool = True) -> QPointF:
        x = (point.x() - self.width() / 2) / self.scale + self.origin.x()
        y = -(point.y() - self.height() / 2) / self.scale + self.origin.y()
        step = self.grid_step()
        return QPointF(round(x / step) * step, round(y / step) * step) if snap else QPointF(x, y)

    def grid_step(self) -> float:
        return GRID_MM if self.scale >= 3 else 5.0 if self.scale >= 0.8 else 10.0

    def set_sketch(self, sketch: Sketch | None, ghosts: list[Sketch]) -> None:
        self.sketch, self.ghosts, self.selected = sketch, ghosts, None
        self._points, self._start = [], None
        self.update()

    def fit(self) -> None:
        bounds = [s.bounds() for s in ([self.sketch] if self.sketch else []) + self.ghosts]
        bounds = [b for b in bounds if b]
        if not bounds:
            self.origin, self.scale = QPointF(0, 0), 6.0
        else:
            x0, y0 = min(b[0] for b in bounds), min(b[1] for b in bounds)
            x1, y1 = max(b[2] for b in bounds), max(b[3] for b in bounds)
            self.origin = QPointF((x0 + x1) / 2, (y0 + y1) / 2)
            self.scale = max(0.2, min(40.0, 0.75 * min(self.width() / max(x1 - x0, 1), self.height() / max(y1 - y0, 1))))
        self.update()

    # --- drawing ---
    def paintEvent(self, _event) -> None:                               # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(BACKGROUND_COLOR))
        step = self.grid_step()
        left, top = self.to_world(QPointF(0, 0), snap=False), self.to_world(QPointF(self.width(), self.height()), snap=False)
        for index in range(int(math.floor(left.x() / step)), int(math.ceil(top.x() / step)) + 1):
            x = self.to_screen(QPointF(index * step, 0)).x()
            painter.setPen(QPen(QColor(EDGE_COLOR if index % 10 == 0 else BORDER_COLOR), 1))
            painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        for index in range(int(math.floor(top.y() / step)), int(math.ceil(left.y() / step)) + 1):
            y = self.to_screen(QPointF(0, index * step)).y()
            painter.setPen(QPen(QColor(EDGE_COLOR if index % 10 == 0 else BORDER_COLOR), 1))
            painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
        origin = self.to_screen(QPointF(0, 0))
        painter.setPen(QPen(QColor(ERROR_COLOR), 1.5))
        painter.drawLine(origin, origin + QPointF(30, 0))
        painter.setPen(QPen(QColor(SUCCESS_COLOR), 1.5))
        painter.drawLine(origin, origin + QPointF(0, -30))
        for ghost in self.ghosts:
            for shape in ghost.shapes:
                self._draw_shape(painter, shape, QColor(MUTED_TEXT_COLOR), fill_alpha=18)
        if self.sketch is not None:
            for index, shape in enumerate(self.sketch.shapes):
                color = QColor("#ffffff") if index == self.selected else QColor(ACCENT_HIGHLIGHT_COLOR)
                self._draw_shape(painter, shape, color, fill_alpha=70 if index == self.selected else 45)
        preview = self._preview_shape()
        if preview is not None:
            self._draw_shape(painter, preview, QColor(SUCCESS_COLOR), fill_alpha=40)
        if self._points:
            pen = QPen(QColor(SUCCESS_COLOR), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            points = [self.to_screen(point) for point in self._points + [self._cursor]]
            for a, b in zip(points, points[1:]):
                painter.drawLine(a, b)
        painter.setPen(QPen(QColor(TEXT_COLOR)))
        painter.drawText(8, self.height() - 8, f"x {self._cursor.x():g}  y {self._cursor.y():g} mm   ·   grid {step:g} mm"
                         + ("   ·   " + self._hint() if self._hint() else ""))

    def _draw_shape(self, painter: QPainter, shape: Shape, color: QColor, fill_alpha: int) -> None:
        fill = QColor(color)
        fill.setAlpha(fill_alpha)
        painter.setPen(QPen(color, 1.6))
        painter.setBrush(QBrush(fill))
        p = shape.params
        if shape.kind == "rect":
            a, b = self.to_screen(QPointF(p["x"], p["y"])), self.to_screen(QPointF(p["x"] + p["w"], p["y"] + p["h"]))
            painter.drawRect(QRectF(a, b).normalized())
        elif shape.kind == "circle":
            centre = self.to_screen(QPointF(p["cx"], p["cy"]))
            radius = abs(p["r"]) * self.scale
            painter.drawEllipse(centre, radius, radius)
        elif shape.kind == "polygon":
            painter.drawPolygon(QPolygonF([self.to_screen(QPointF(x, y)) for x, y in p["points"]]))

    def _preview_shape(self) -> Shape | None:
        if self._start is None:
            return None
        a, b = self._start, self._cursor
        if self.tool == "rect":
            return Shape("rect", {"x": a.x(), "y": a.y(), "w": b.x() - a.x(), "h": b.y() - a.y()})
        if self.tool == "circle":
            return Shape("circle", {"cx": a.x(), "cy": a.y(), "r": math.hypot(b.x() - a.x(), b.y() - a.y())})
        return None

    def _hint(self) -> str:
        preview = self._preview_shape()
        if preview is not None:
            return preview.describe()
        if self.tool == "polygon":
            return "click points · double-click or click the first point to close · Esc cancels" if self._points else \
                   "click to start a polygon"
        return {"select": "click a shape to select · Delete removes it", "rect": "drag to draw a rectangle",
                "circle": "drag from the centre to draw a circle"}.get(self.tool, "")

    # --- mouse / keys ---
    def mousePressEvent(self, event) -> None:                           # noqa: N802
        if event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._pan_from = event.position()
            return
        if self.sketch is None:
            return
        point = self.to_world(event.position())
        if self.tool == "select":
            self.selected = self._shape_at(self.to_world(event.position(), snap=False))
            self.changed.emit()
        elif self.tool in ("rect", "circle"):
            self._start = point
        elif self.tool == "polygon":
            if len(self._points) >= 3 and (self.to_screen(self._points[0]) - event.position()).manhattanLength() < 10:
                self._close_polygon()
            else:
                self._points.append(point)
        self.update()

    def mouseMoveEvent(self, event) -> None:                            # noqa: N802
        if self._pan_from is not None:
            delta = event.position() - self._pan_from
            self.origin -= QPointF(delta.x() / self.scale, -delta.y() / self.scale)
            self._pan_from = event.position()
        self._cursor = self.to_world(event.position())
        self.update()

    def mouseReleaseEvent(self, event) -> None:                         # noqa: N802
        self._pan_from = None
        shape = self._preview_shape()
        self._start = None
        if shape is not None and self.sketch is not None:
            is_big_enough = (shape.kind == "rect" and abs(shape.params["w"]) > 0 and abs(shape.params["h"]) > 0) or \
                            (shape.kind == "circle" and shape.params["r"] > 0)
            if is_big_enough:
                if shape.kind == "rect":                     # store normalised (positive width / height)
                    p = shape.params
                    shape.params = {"x": min(p["x"], p["x"] + p["w"]), "y": min(p["y"], p["y"] + p["h"]),
                                    "w": abs(p["w"]), "h": abs(p["h"])}
                self.sketch.shapes.append(shape)
                self.selected = len(self.sketch.shapes) - 1
                self.changed.emit()
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:                     # noqa: N802
        if self.tool == "polygon" and len(self._points) >= 3:
            self._close_polygon()

    def _close_polygon(self) -> None:
        points = [[point.x(), point.y()] for point in self._points]
        deduplicated = [point for index, point in enumerate(points) if index == 0 or point != points[index - 1]]
        self._points = []
        if len(deduplicated) >= 3 and self.sketch is not None:
            self.sketch.shapes.append(Shape("polygon", {"points": deduplicated}))
            self.selected = len(self.sketch.shapes) - 1
            self.changed.emit()
        self.update()

    def wheelEvent(self, event) -> None:                                # noqa: N802
        before = self.to_world(event.position(), snap=False)
        self.scale = max(0.2, min(60.0, self.scale * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15)))
        after = self.to_world(event.position(), snap=False)
        self.origin += before - after                                    # zoom around the cursor
        self.update()

    def keyPressEvent(self, event) -> None:                             # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._points, self._start = [], None
            self.update()
        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        else:
            super().keyPressEvent(event)

    def delete_selected(self) -> None:
        if self.sketch is not None and self.selected is not None and self.selected < len(self.sketch.shapes):
            self.sketch.shapes.pop(self.selected)
            self.selected = None
            self.changed.emit()
            self.update()

    def _shape_at(self, point: QPointF) -> int | None:
        from shapely.geometry import Point
        if self.sketch is None:
            return None
        here = Point(point.x(), point.y()).buffer(4 / self.scale)
        for index in reversed(range(len(self.sketch.shapes))):
            if self.sketch.shapes[index].geometry().intersects(here):
                return index
        return None
# --- END [1.2] ---


# ==============================================================================
# [2.1] parameter_dialog
# ==============================================================================
class ParameterDialog(QDialog):
    """Numbers (and an optional sketch choice) for a feature or a shape."""

    def __init__(self, parent: QWidget | None, title: str, fields: list[tuple[str, str, float, float, float]],
                 sketches: list[str] | None = None, sketch: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QFormLayout(self)
        self.sketch_combo = None
        if sketches is not None:
            self.sketch_combo = QComboBox()
            self.sketch_combo.addItems(sketches)
            if sketch:
                self.sketch_combo.setCurrentText(sketch)
            form.addRow("Sketch", self.sketch_combo)
        self.spins: dict[str, QDoubleSpinBox] = {}
        for key, label, value, low, high in fields:
            spin = QDoubleSpinBox(minimum=low, maximum=high, decimals=2, value=value, suffix=" mm")
            form.addRow(label, spin)
            self.spins[key] = spin
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict:
        values = {key: spin.value() for key, spin in self.spins.items()}
        if self.sketch_combo is not None:
            values["sketch"] = self.sketch_combo.currentText()
        return values
# --- END [2.1] ---


# ==============================================================================
# [3.1] init
# ==============================================================================
class PartDesignWindow(QWidget):
    saved = Signal(int)                               # library item id of the saved part

    def __init__(self, part: Part, item_id: int | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="part_design_window")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(1280, 780)
        self.part, self.item_id = part, item_id
        self.log = get_activity_log()
        self.solid = self.full_solid = None
        self.build_error = ""
        self.rollback: int | None = None
        self.is_dirty = False
        self._rebuild_timer = QTimer(self, singleShot=True, interval=200, timeout=self.rebuild)

        layout = QVBoxLayout(self)
        top_row = QHBoxLayout()                       # name · progress through the steps · back / next
        top_row.setSpacing(12)
        name_column = QVBoxLayout()
        name_column.setSpacing(0)
        name_column.addWidget(QLabel("Part Design", objectName="create_new_label"))
        self.name_edit = QLineEdit(part.name, placeholderText="Part name")
        self.name_edit.setFixedWidth(220)
        self.name_edit.textChanged.connect(self._rename)
        name_column.addWidget(self.name_edit)
        top_row.addLayout(name_column)
        self.workflow_step_bar = StepProgressBar(STEPS)
        self.workflow_step_bar.step_clicked.connect(self._on_step_clicked)
        top_row.addWidget(self.workflow_step_bar, 1)
        self.back_btn, self.next_btn = QPushButton("◀ Back"), QPushButton("Next ▶")
        self.back_btn.clicked.connect(lambda: self.go_to_step(self.pages.currentIndex() - 1))
        self.next_btn.clicked.connect(lambda: self.go_to_step(self.pages.currentIndex() + 1))
        top_row.addWidget(self.back_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        top_row.addWidget(self.next_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top_row)
        self.step_hint_label = QLabel(objectName="control_label", wordWrap=True)
        layout.addWidget(self.step_hint_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.pages = QStackedWidget()
        for builder in (self._build_sketch_page, self._build_features_page, self._build_check_page, self._build_export_page):
            self.pages.addWidget(builder())
        splitter.addWidget(self.pages)
        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = MeshViewer()
        preview_layout.addWidget(self.viewer, 3)
        preview_layout.addWidget(QLabel("History — click a feature to roll back to it", objectName="control_label"))
        self.history_list = QListWidget(objectName="history_list")
        self.history_list.currentRowChanged.connect(self._on_history_clicked)
        preview_layout.addWidget(self.history_list, 1)
        self.build_label = QLabel(objectName="module_status_label", wordWrap=True)
        preview_layout.addWidget(self.build_label)
        splitter.addWidget(preview_panel)
        splitter.setSizes([700, 520])
        layout.addWidget(splitter, 1)

        if not part.sketches:
            part.sketches.append(Sketch(part.new_sketch_name()))
        self._refresh_sketches()
        self.rebuild()
        self.go_to_step(0)
        QTimer.singleShot(0, self.sketch_canvas.fit)

    def _rename(self, text: str) -> None:
        self.part.name = text.strip() or "Part"
        self.setWindowTitle(f"Part Design — {self.part.name}")
        self.is_dirty = True

    def changed(self) -> None:
        self.is_dirty = True
        self._refresh_history()
        self._rebuild_timer.start()
# --- END [3.1] ---

# ==============================================================================
# [3.2] steps
# ==============================================================================
    def step_problem(self, step: int) -> str:
        """Why `step` can't start yet ("" = it can)."""
        if step >= 1 and not any(sketch.shapes for sketch in self.part.sketches):
            return "Draw at least one shape in a sketch first."
        if step >= 2 and self.build_error:
            return f"Fix the features first: {self.build_error}"
        if step >= 2 and self.full_solid is None:
            return "Add a Pad feature to make a solid first."
        return ""

    def go_to_step(self, step: int) -> None:
        step = max(0, min(step, len(STEPS) - 1))
        problem = self.step_problem(step)
        if problem:
            step = self.pages.currentIndex()
        self.pages.setCurrentIndex(step)
        self._refresh_steps()
        if problem:                                   # a locked step was asked for: say why it can't start
            self.step_hint_label.setText(f"Not yet: {problem}")
        if step == 2:
            self._refresh_check()

    def _on_step_clicked(self, index: int) -> None:
        if index >= 0 and index != self.pages.currentIndex():
            self.go_to_step(index)                    # a locked step stays put and says why

    def _refresh_steps(self) -> None:
        current = self.pages.currentIndex()
        states, problems = [], []
        for index in range(len(STEPS)):
            problem = self.step_problem(index)
            is_done = index < current or (index == 0 and any(s.shapes for s in self.part.sketches)) or \
                (index == 1 and self.full_solid is not None)
            states.append("current" if index == current else "done" if is_done and not problem else
                          "locked" if problem else "open")
            problems.append(problem)
        self.workflow_step_bar.set_steps(current, states, problems)
        self.back_btn.setEnabled(current > 0)
        next_problem = self.step_problem(current + 1) if current + 1 < len(STEPS) else "Last step"
        self.next_btn.setEnabled(not next_problem)
        self.next_btn.setToolTip(next_problem)
        hints = ("Draw the profile: rectangles, circles, polygons. Each sketch lies on a plane at height z.",
                 "Turn sketches into a solid: Pad adds material, Pocket and Hole remove it. Order matters.",
                 "Check size, volume and mass; a part must be watertight to print or machine.",
                 "Save the part (editable later) and export STL for slicers / other CAD.")
        self.step_hint_label.setText(hints[current] if not next_problem or current == len(STEPS) - 1 else
                                     f"{hints[current]}\nNext: {next_problem}")

    def rebuild(self) -> None:
        try:
            self.full_solid = self.part.build()
            self.solid = self.full_solid if self.rollback is None else self.part.build(self.rollback)
            self.build_error = ""
        except CadError as error:
            self.full_solid = self.solid = None
            self.build_error = str(error)
        self.viewer.set_mesh(self.solid)
        if self.build_error:
            self.build_label.setText(f"⚠ {self.build_error}")
        elif self.solid is None:
            self.build_label.setText("No solid yet — add a Pad in step 2.")
        else:
            size = self.solid.bounds[1] - self.solid.bounds[0]
            prefix = f"Rolled back to feature {self.rollback} · " if self.rollback is not None else ""
            self.build_label.setText(f"{prefix}{size[0]:.1f} × {size[1]:.1f} × {size[2]:.1f} mm · {len(self.solid.faces)} triangles")
        self._refresh_steps()

    def _refresh_history(self) -> None:
        self.history_list.blockSignals(True)
        self.history_list.clear()
        self.history_list.addItem("0  (start)")
        for index, feature in enumerate(self.part.features, start=1):
            self.history_list.addItem(f"{index}  {feature.describe()}")
        self.history_list.setCurrentRow(len(self.part.features) if self.rollback is None else self.rollback)
        self.history_list.blockSignals(False)
        self.features_list.clear()
        for feature in self.part.features:
            self.features_list.addItem(feature.describe())

    def _on_history_clicked(self, row: int) -> None:
        self.rollback = None if row < 0 or row >= len(self.part.features) else row
        self.rebuild()
# --- END [3.2] ---

# ==============================================================================
# [3.3] sketch_step
# ==============================================================================
    def _build_sketch_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.sketch_combo = QComboBox()
        self.sketch_combo.activated.connect(lambda: self._show_sketch(self.sketch_combo.currentText()))
        row.addWidget(self.sketch_combo)
        new_sketch_btn = QPushButton("+ Sketch", toolTip="A new sketch on a plane at the top of the solid")
        new_sketch_btn.clicked.connect(self._new_sketch)
        row.addWidget(new_sketch_btn)
        row.addWidget(QLabel("Plane z"))
        self.plane_spin = QDoubleSpinBox(minimum=-10000, maximum=10000, decimals=2, suffix=" mm")
        self.plane_spin.valueChanged.connect(self._set_plane)
        row.addWidget(self.plane_spin)
        row.addSpacing(12)
        self.tool_group = QButtonGroup(self)
        for tool, text, tip in (("select", "⬚ Select", "Select a shape (Delete removes it)"), ("rect", "▭ Rectangle", "Drag"),
                                ("circle", "◯ Circle", "Drag from the centre"), ("polygon", "⬠ Polygon", "Click points")):
            button = QToolButton(text=text, checkable=True, toolTip=tip)
            button.clicked.connect(lambda _checked=False, tool=tool: self._set_tool(tool))
            self.tool_group.addButton(button)
            row.addWidget(button)
            if tool == "rect":
                button.setChecked(True)
        row.addStretch()
        fit_btn = QPushButton("Fit")
        row.addWidget(fit_btn)
        layout.addLayout(row)
        self.sketch_canvas = SketchCanvas()
        self.sketch_canvas.changed.connect(self._on_sketch_changed)
        fit_btn.clicked.connect(self.sketch_canvas.fit)
        layout.addWidget(self.sketch_canvas, 1)
        shapes_row = QHBoxLayout()
        self.shapes_list = QListWidget()
        self.shapes_list.setMaximumHeight(110)
        self.shapes_list.itemDoubleClicked.connect(lambda _item: self._edit_shape(self.shapes_list.currentRow()))
        self.shapes_list.currentRowChanged.connect(self._select_shape)
        shapes_row.addWidget(self.shapes_list, 1)
        buttons = QVBoxLayout()
        for text, handler in (("Edit…", lambda: self._edit_shape(self.shapes_list.currentRow())),
                              ("Delete", self.sketch_canvas.delete_selected)):
            button = QPushButton(text)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch()
        shapes_row.addLayout(buttons)
        layout.addLayout(shapes_row)
        return page

    def current_sketch(self) -> Sketch | None:
        return self.part.sketch(self.sketch_combo.currentText())

    def _refresh_sketches(self, select: str | None = None) -> None:
        current = select or self.sketch_combo.currentText() or (self.part.sketches[0].name if self.part.sketches else "")
        self.sketch_combo.clear()
        self.sketch_combo.addItems([sketch.name for sketch in self.part.sketches])
        self.sketch_combo.setCurrentText(current)
        self._show_sketch(self.sketch_combo.currentText())
        self._refresh_history()

    def _show_sketch(self, name: str) -> None:
        sketch = self.part.sketch(name)
        self.plane_spin.blockSignals(True)
        self.plane_spin.setValue(sketch.z if sketch else 0)
        self.plane_spin.blockSignals(False)
        self.sketch_canvas.set_sketch(sketch, [other for other in self.part.sketches if other is not sketch])
        self._refresh_shapes()

    def _new_sketch(self) -> None:
        sketch = Sketch(self.part.new_sketch_name(), self.part.top_z())
        self.part.sketches.append(sketch)
        self._refresh_sketches(sketch.name)
        self.changed()

    def _set_plane(self, z: float) -> None:
        sketch = self.current_sketch()
        if sketch is not None:
            sketch.z = z
            self.changed()

    def _set_tool(self, tool: str) -> None:
        self.sketch_canvas.tool = tool
        self.sketch_canvas.update()
        self.sketch_canvas.setFocus()

    def _on_sketch_changed(self) -> None:
        self._refresh_shapes()
        self.changed()

    def _refresh_shapes(self) -> None:
        sketch = self.current_sketch()
        self.shapes_list.blockSignals(True)
        self.shapes_list.clear()
        for shape in sketch.shapes if sketch else []:
            self.shapes_list.addItem(shape.describe())
        if self.sketch_canvas.selected is not None:
            self.shapes_list.setCurrentRow(self.sketch_canvas.selected)
        self.shapes_list.blockSignals(False)

    def _select_shape(self, row: int) -> None:
        self.sketch_canvas.selected = row if row >= 0 else None
        self.sketch_canvas.update()

    def _edit_shape(self, row: int) -> None:
        sketch = self.current_sketch()
        if sketch is None or not 0 <= row < len(sketch.shapes):
            return
        shape = sketch.shapes[row]
        p = shape.params
        if shape.kind == "rect":
            fields = [("x", "X", p["x"], -1e4, 1e4), ("y", "Y", p["y"], -1e4, 1e4), ("w", "Width", p["w"], 0.01, 1e4),
                      ("h", "Height", p["h"], 0.01, 1e4)]
        elif shape.kind == "circle":
            fields = [("cx", "Centre X", p["cx"], -1e4, 1e4), ("cy", "Centre Y", p["cy"], -1e4, 1e4),
                      ("r", "Radius", p["r"], 0.01, 1e4)]
        else:
            QMessageBox.information(self, "Polygon", "Polygons are edited by redrawing them for now.")
            return
        dialog = ParameterDialog(self, shape.describe(), fields)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            shape.params.update(dialog.values())
            self._refresh_shapes()
            self.sketch_canvas.update()
            self.changed()
# --- END [3.3] ---

# ==============================================================================
# [3.4] features_step
# ==============================================================================
    def _build_features_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        for text, kind, tip in (("+ Pad", "pad", "Extrude a sketch up (adds material)"),
                                ("+ Pocket", "pocket", "Cut a sketch's profile down into the solid"),
                                ("+ Hole", "hole", "A round hole from the top")):
            button = QPushButton(text, toolTip=tip)
            button.clicked.connect(lambda _checked=False, kind=kind: self.add_feature(kind))
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.features_list = QListWidget()
        self.features_list.itemDoubleClicked.connect(lambda _item: self.edit_feature(self.features_list.currentRow()))
        layout.addWidget(self.features_list, 1)
        buttons = QHBoxLayout()
        for text, handler in (("Edit…", lambda: self.edit_feature(self.features_list.currentRow())),
                              ("▲", lambda: self._move_feature(-1)), ("▼", lambda: self._move_feature(1)),
                              ("Delete", self._delete_feature)):
            button = QPushButton(text)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        layout.addWidget(QLabel("Features apply top to bottom. Pocket depth is measured down from its sketch's plane; "
                                "put the sketch's plane at the top face (new sketches start there).",
                                objectName="control_label", wordWrap=True))
        return page

    def _feature_dialog(self, kind: str, params: dict) -> dict | None:
        sketches = [sketch.name for sketch in self.part.sketches if sketch.shapes]
        if kind in ("pad", "pocket") and not sketches:
            QMessageBox.information(self, "No sketch", "Draw a shape in a sketch first.")
            return None
        if kind == "pad":
            dialog = ParameterDialog(self, "Pad", [("height", "Height", params.get("height", 10.0), 0.01, 1e4)],
                                     sketches, params.get("sketch"))
        elif kind == "pocket":
            dialog = ParameterDialog(self, "Pocket", [("depth", "Depth", params.get("depth", 2.0), 0.01, 1e4)],
                                     sketches, params.get("sketch"))
        else:
            dialog = ParameterDialog(self, "Hole", [("x", "X", params.get("x", 0.0), -1e4, 1e4), ("y", "Y", params.get("y", 0.0), -1e4, 1e4),
                                                    ("diameter", "Diameter", params.get("diameter", 5.0), 0.01, 1e4),
                                                    ("depth", "Depth (0 = through)", params.get("depth", 0.0), 0, 1e4)])
        return dialog.values() if dialog.exec() == QDialog.DialogCode.Accepted else None

    def add_feature(self, kind: str, params: dict | None = None) -> None:
        if params is None:
            default_sketch = self.sketch_combo.currentText()
            params = self._feature_dialog(kind, {"sketch": default_sketch})
            if params is None:
                return
        self.part.features.append(Feature(kind, params))
        self.rollback = None
        self.log.info("cad", f"Part “{self.part.name}”: {self.part.features[-1].describe()}")
        self.changed()

    def edit_feature(self, row: int) -> None:
        if not 0 <= row < len(self.part.features):
            return
        feature = self.part.features[row]
        params = self._feature_dialog(feature.kind, feature.params)
        if params is not None:
            feature.params = params
            self.changed()

    def _move_feature(self, step: int) -> None:
        row = self.features_list.currentRow()
        target = row + step
        if 0 <= row < len(self.part.features) and 0 <= target < len(self.part.features):
            features = self.part.features
            features[row], features[target] = features[target], features[row]
            self.changed()
            self.features_list.setCurrentRow(target)

    def _delete_feature(self) -> None:
        row = self.features_list.currentRow()
        if 0 <= row < len(self.part.features):
            self.part.features.pop(row)
            self.rollback = None
            self.changed()
# --- END [3.4] ---

# ==============================================================================
# [3.5] check_and_export
# ==============================================================================
    def _build_check_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        row.addWidget(QLabel("Material"))
        self.material_combo = QComboBox()
        self.material_combo.addItems(list(MATERIAL_DENSITIES))
        self.material_combo.currentIndexChanged.connect(self._refresh_check)
        row.addWidget(self.material_combo)
        row.addStretch()
        layout.addLayout(row)
        self.check_label = QLabel(objectName="module_summary_label", wordWrap=True)
        self.check_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.check_label)
        layout.addStretch()
        return page

    def _refresh_check(self) -> None:
        solid = self.full_solid
        if solid is None:
            self.check_label.setText("No solid to check.")
            return
        density = MATERIAL_DENSITIES[self.material_combo.currentText()]
        info = measure(solid, density)
        size = info["size_mm"]
        lines = [f"Size: {size[0]:.2f} × {size[1]:.2f} × {size[2]:.2f} mm",
                 f"Surface area: {info['area_mm2']:,.1f} mm²", f"Triangles: {info['triangles']:,}"]
        if info["is_watertight"]:
            lines += [f"Volume: {info['volume_mm3']:,.1f} mm³ ({info['volume_mm3'] / 1000:,.2f} cm³)",
                      f"Mass in {self.material_combo.currentText()}: {info['mass_g']:,.1f} g", "✓ Watertight — ready to print / machine"]
        else:
            lines.append("⚠ Not watertight: volume and mass can't be trusted. Check overlapping or touching features.")
        self.check_label.setText("\n".join(lines))

    def _build_export_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        for text, handler, tip in (("Save part to Library", self.save_part, "Editable later: sketches and features (JSON)"),
                                   ("Export STL to Library", self.export_stl_to_library, "A mesh for slicers and viewers"),
                                   ("Export STL / OBJ file…", self.export_file, "Save a mesh anywhere")):
            button = QPushButton(text, toolTip=tip)
            button.clicked.connect(handler)
            layout.addWidget(button)
        self.export_label = QLabel(objectName="module_status_label", wordWrap=True)
        layout.addWidget(self.export_label)
        layout.addStretch()
        return page

    def save_part(self) -> int:
        store = library()
        with self.log.step("cad", f"Save part “{self.part.name}”"):
            if self.item_id is not None and store.get(self.item_id) is not None:
                store.path(self.item_id).write_text(self.part.to_json(), encoding="utf-8")
                store.refresh_size(self.item_id)
                if store.get(self.item_id).name != self.part.name:
                    store.rename(self.item_id, self.part.name)
            else:
                path = store.new_path("cad", ".json")
                path.write_text(self.part.to_json(), encoding="utf-8")
                self.item_id = store.register_path("cad", store.unique_name("cad", self.part.name), path,
                                                   {"format": PART_FORMAT}).item_id
        self.is_dirty = False
        self.export_label.setText(f"Saved “{self.part.name}” in your Library (CAD models)")
        self.saved.emit(self.item_id)
        return self.item_id

    def export_stl_to_library(self) -> None:
        solid = self.full_solid
        if solid is None:
            return
        with self.log.step("cad", f"Export STL of “{self.part.name}”"):
            item = library().add_bytes("cad", f"{self.part.name} (STL)", solid.export(file_type="stl"), ".stl")
        self.export_label.setText(f"Saved “{item.name}” in your Library")

    def export_file(self) -> None:
        solid = self.full_solid
        if solid is None:
            return
        path, _filter = QFileDialog.getSaveFileName(self, "Export mesh", f"{self.part.name}.stl", "STL (*.stl);;OBJ (*.obj)")
        if path:
            with self.log.step("cad", f"Export {path}"):
                solid.export(path)
            self.export_label.setText(f"Exported {path}")

    def closeEvent(self, event) -> None:                                # noqa: N802
        if self.is_dirty and self.part.features:
            answer = QMessageBox.question(self, "Save part?", f"Save the changes to “{self.part.name}”?",
                                          QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                          | QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save:
                self.save_part()
        super().closeEvent(event)
# --- END [3.5] ---
