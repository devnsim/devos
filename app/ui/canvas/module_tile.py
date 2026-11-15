# ==============================================================================
# FILE: app/ui/canvas/module_tile.py
# OVERVIEW: One block on a ModuleCanvas: a draggable header bar over a module's content.
# PURPOSE:
#   1. Frames any module content widget (or an "empty module" hint) under a title header.
#   2. Starts a drag when the header is dragged, carrying only this tile's tile_id.
#   3. Accepts dragged tiles and reports where they landed (centre = swap, edge = tile beside);
#      previews the landing spot while a tile hovers.
#   4. Offers "split" (new empty module to the right) and "close module" buttons.
#   5. An empty tile shows a module picker (library dropdown + Open) and reports the choice.
# CONTRACT:
#   - Requires: app.ui.widgets.sidebar (ICON_SIZE, load_icon), app.ui.widgets.tiling_layout
#               (edge_zone_at, zone_preview_rect), assets/icons/split_pane.svg, tab_close.svg,
#               QSS selectors #module_tile_panel, #module_header_panel, #module_title_label,
#               #module_tile_btn, #module_body_panel, #empty_module_label, #drop_overlay_panel
#   - Exposes: ModuleTile (QFrame) with signals split_requested(), close_requested(), drag_started(),
#              drag_finished(), module_dropped(int source_tile_id, str drop_zone), module_chosen(str);
#              tile_id, content_widget, module_type_id, instance_id, set_content(), set_can_close(),
#              set_is_active(); MODULE_MIME_TYPE
# NOTES:
#   - [2026-11-15] Empty tiles take a module_choices list [(module_type_id, title, description)] and
#                  show a picker; set_content() swaps the picker for the chosen module's view.
#   - [2026-11-15] Created. Mirrors TabGroup's drag/drop contract: a tile never moves itself; it
#                  reports drops and ModuleCanvas rearranges the tree (tiling_layout.py).
#   - [2026-11-15] Moved from app/ui/modules/module_tile.py (module apps live in app/modules/<name>/).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - content_widget (QWidget | None): The module shown in the tile body; None = empty module.
# - MODULE_MIME_TYPE (str): Drag-and-drop format for a tile; its payload is the tile_id as text.
# - tile_id (int): Number ModuleCanvas gives each tile; unique within that canvas.
# - DRAG_PIXMAP_MAX_WIDTH_PX (int | px): Width of the ghost image that follows the mouse (default: 240).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS_AND_CONSTANTS
#    1.1 imports
#    1.2 constants
# 2. MODULE_HEADER
#    2.1 header_drag
# 3. MODULE_TILE
#    3.1 class_signals_and_constants
#    3.2 init_tile
#    3.3 build_header
#    3.4 build_body
#    3.5 tile_state
#    3.6 drop_events
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from PySide6.QtCore import QByteArray, QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QDrag, QMouseEvent
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget,
)

from app.ui.widgets.sidebar import ICON_SIZE, load_icon
from app.ui.widgets.tiling_layout import edge_zone_at, zone_preview_rect
# --- END [1.1] ---

# ==============================================================================
# [1.2] constants
# ==============================================================================
MODULE_MIME_TYPE = "application/x-devos-module"
# --- END [1.2] ---


# ==============================================================================
# [2.1] header_drag
# ==============================================================================
class ModuleHeader(QWidget):
    """Title bar of a tile; dragging it past the start distance starts a tile drag."""

    drag_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="module_header_panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._press_point: QPoint | None = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_point = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_point is not None and event.buttons() & Qt.MouseButton.LeftButton:
            moved_px = (event.position().toPoint() - self._press_point).manhattanLength()
            if moved_px >= QApplication.startDragDistance():
                self._press_point = None
                self.drag_requested.emit()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._press_point = None
        super().mouseReleaseEvent(event)
# --- END [2.1] ---


# ==============================================================================
# [3.1] class_signals_and_constants
# ==============================================================================
class ModuleTile(QFrame):
    split_requested = Signal()
    close_requested = Signal()
    drag_started = Signal()
    drag_finished = Signal()
    module_dropped = Signal(int, str)       # source tile_id, drop zone
    module_chosen = Signal(str)             # module_type_id picked in an empty tile

    MIN_WIDTH_PX = 160
    MIN_HEIGHT_PX = 100
    DRAG_PIXMAP_MAX_WIDTH_PX = 240
    EMPTY_MODULE_TEXT = "Empty module — pick one from the library\n(drag the header to move · drop on an edge to tile)"
# --- END [3.1] ---

# ==============================================================================
# [3.2] init_tile
# ==============================================================================
    def __init__(
        self, tile_id: int, title_text: str, content_widget: QWidget | None = None,
        module_type_id: str | None = None, instance_id: str | None = None,
        module_choices: list[tuple[str, str, str]] | None = None,
    ) -> None:
        super().__init__(objectName="module_tile_panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumSize(self.MIN_WIDTH_PX, self.MIN_HEIGHT_PX)
        self.setAcceptDrops(True)
        self.tile_id = tile_id
        self.content_widget = content_widget
        self.module_type_id = module_type_id
        self.instance_id = instance_id
        self.module_choices = module_choices or []

        self.tile_layout = QVBoxLayout(self)
        self.tile_layout.setContentsMargins(0, 0, 0, 0)
        self.tile_layout.setSpacing(0)
        self._build_header(title_text)
        self._build_body()
# --- END [3.2] ---

# ==============================================================================
# [3.3] build_header
# ==============================================================================
    def _build_header(self, title_text: str) -> None:
        self.module_header_panel = ModuleHeader()
        self.module_header_panel.drag_requested.connect(self._start_drag)
        header_layout = QHBoxLayout(self.module_header_panel)
        header_layout.setContentsMargins(8, 2, 4, 2)
        header_layout.setSpacing(2)

        self.module_title_label = QLabel(title_text, objectName="module_title_label")
        self.split_module_btn = self._build_header_btn("split_pane.svg", "Split right (new module)")
        self.split_module_btn.clicked.connect(self.split_requested)
        self.close_module_btn = self._build_header_btn("tab_close.svg", "Close module")
        self.close_module_btn.clicked.connect(self.close_requested)
        self.close_module_btn.hide()                       # only shown while there are 2+ tiles

        header_layout.addWidget(self.module_title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.split_module_btn)
        header_layout.addWidget(self.close_module_btn)
        self.tile_layout.addWidget(self.module_header_panel)

    def _build_header_btn(self, icon_file_name: str, tool_tip_text: str) -> QToolButton:
        header_btn = QToolButton(objectName="module_tile_btn", toolTip=tool_tip_text)
        header_btn.setIcon(load_icon(icon_file_name))
        header_btn.setIconSize(ICON_SIZE)
        header_btn.setAutoRaise(True)
        header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return header_btn
# --- END [3.3] ---

# ==============================================================================
# [3.4] build_body
# ==============================================================================
    def _build_body(self) -> None:
        self.module_body_panel = QWidget(objectName="module_body_panel")
        self.body_layout = QVBoxLayout(self.module_body_panel)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        if self.content_widget is None:
            self.module_picker_panel = self._build_module_picker()
            self.body_layout.addWidget(self.module_picker_panel)
        else:
            self.body_layout.addWidget(self.content_widget)
        self.tile_layout.addWidget(self.module_body_panel, 1)

        # Drop highlight: drawn over the whole tile while another tile hovers it
        self.drop_overlay_panel = QWidget(self, objectName="drop_overlay_panel")
        self.drop_overlay_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.drop_overlay_panel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.drop_overlay_panel.hide()

    def _build_module_picker(self) -> QWidget:
        """Empty-tile body: hint, library dropdown and an Open button."""
        picker_panel = QWidget(objectName="module_picker_panel")
        picker_layout = QVBoxLayout(picker_panel)
        picker_layout.addStretch()
        self.empty_module_label = QLabel(self.EMPTY_MODULE_TEXT, objectName="empty_module_label")
        self.empty_module_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_module_label.setWordWrap(True)
        picker_layout.addWidget(self.empty_module_label)
        if self.module_choices:
            choice_row = QHBoxLayout()
            choice_row.addStretch()
            self.module_choice_combo = QComboBox(objectName="module_choice_combo")
            for module_type_id, title, description in self.module_choices:
                self.module_choice_combo.addItem(title, module_type_id)
                self.module_choice_combo.setItemData(
                    self.module_choice_combo.count() - 1, description, Qt.ItemDataRole.ToolTipRole
                )
            self.open_module_btn = QPushButton("Open", objectName="open_module_btn")
            self.open_module_btn.clicked.connect(
                lambda: self.module_chosen.emit(self.module_choice_combo.currentData())
            )
            choice_row.addWidget(self.module_choice_combo)
            choice_row.addWidget(self.open_module_btn)
            choice_row.addStretch()
            picker_layout.addLayout(choice_row)
        picker_layout.addStretch()
        return picker_panel

    def set_content(self, content_widget: QWidget, title_text: str, module_type_id: str, instance_id: str) -> None:
        """Replace the picker (or previous module) with a module view."""
        if self.content_widget is not None:
            self.content_widget.setParent(None)
            self.content_widget.deleteLater()
        elif getattr(self, "module_picker_panel", None) is not None:
            self.module_picker_panel.setParent(None)
            self.module_picker_panel.deleteLater()
            self.module_picker_panel = None
        self.content_widget, self.module_type_id, self.instance_id = content_widget, module_type_id, instance_id
        self.module_title_label.setText(title_text)
        self.body_layout.addWidget(content_widget)
# --- END [3.4] ---

# ==============================================================================
# [3.5] tile_state
# ==============================================================================
    def set_can_close(self, can_close: bool) -> None:
        self.close_module_btn.setVisible(can_close)

    def set_is_active(self, is_active: bool) -> None:
        """Active tile: where "Add module" places new modules (QSS property is_active_tile)."""
        self.module_header_panel.setProperty("is_active_tile", "true" if is_active else "false")
        self.module_header_panel.style().unpolish(self.module_header_panel)
        self.module_header_panel.style().polish(self.module_header_panel)

    def _start_drag(self) -> None:
        mime_data = QMimeData()
        mime_data.setData(MODULE_MIME_TYPE, QByteArray(str(self.tile_id).encode()))
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        ghost_pixmap = self.grab()
        if ghost_pixmap.width() > self.DRAG_PIXMAP_MAX_WIDTH_PX:
            ghost_pixmap = ghost_pixmap.scaledToWidth(
                self.DRAG_PIXMAP_MAX_WIDTH_PX, Qt.TransformationMode.SmoothTransformation
            )
        drag.setPixmap(ghost_pixmap)
        drag.setHotSpot(QPoint(20, 10))

        self.drag_started.emit()
        drag.exec(Qt.DropAction.MoveAction)              # blocks until dropped or cancelled
        self.drag_finished.emit()
# --- END [3.5] ---

# ==============================================================================
# [3.6] drop_events
# ==============================================================================
    def _dragged_tile_id(self, event) -> int | None:
        """tile_id carried by the drag, or None if it isn't a tile (or is this tile)."""
        if not event.mimeData().hasFormat(MODULE_MIME_TYPE):
            return None
        source_tile_id = int(bytes(event.mimeData().data(MODULE_MIME_TYPE)).decode())
        return None if source_tile_id == self.tile_id else source_tile_id

    def dragEnterEvent(self, event) -> None:
        if self._dragged_tile_id(event) is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self._show_drop_preview(event.position().toPoint())

    def dragMoveEvent(self, event) -> None:
        if self._dragged_tile_id(event) is not None:
            event.acceptProposedAction()
            self._show_drop_preview(event.position().toPoint())

    def dragLeaveEvent(self, event) -> None:
        self.drop_overlay_panel.hide()

    def dropEvent(self, event) -> None:
        self.drop_overlay_panel.hide()
        source_tile_id = self._dragged_tile_id(event)
        if source_tile_id is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self.module_dropped.emit(source_tile_id, edge_zone_at(self.rect(), event.position().toPoint()))

    def _show_drop_preview(self, point: QPoint) -> None:
        drop_zone = edge_zone_at(self.rect(), point)
        self.drop_overlay_panel.setGeometry(zone_preview_rect(self.rect(), drop_zone))
        self.drop_overlay_panel.raise_()
        self.drop_overlay_panel.show()
# --- END [3.6] ---
