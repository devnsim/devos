# ==============================================================================
# FILE: app/ui/widgets/tiling_layout.py
# OVERVIEW: Shared tiling engine: a tree of QSplitters whose leaves are tiles (panes or modules).
# PURPOSE:
#   1. Decides which drop zone (centre or one of four edges) a point falls in, and the
#      preview rectangle for that zone.
#   2. Splits a leaf: puts a new leaf on one side of it, wrapping it in a new splitter if needed.
#   3. Swaps two leaves anywhere in the tree.
#   4. Tidies the tree after leaves are removed (no empty, single-child or same-direction nesting).
# CONTRACT:
#   - Requires: QSS selector #pane_splitter in app/ui/theme.py (seams)
#   - Exposes: DROP_ZONE_CENTER/LEFT/RIGHT/TOP/BOTTOM, EDGE_DROP_ZONES, EDGE_SPLITS,
#              EDGE_ZONE_FRACTION, edge_zone_at(), zone_preview_rect(), new_splitter(),
#              insert_beside(), swap_leaves(), normalize_tree(), first_leaf()
# NOTES:
#   - [2026-11-15] Extracted from tab_area.py so TabArea (tab panes) and ModuleCanvas (module tiles)
#                  tile with the exact same rules. Functions only touch QSplitters and leaf widgets;
#                  the callers keep their own bookkeeping (lists of panes/tiles, active pane...).
#   - [2026-11-15] No QSplitter.replaceWidget() anywhere: PySide hands the replaced widget to Python,
#                  which then garbage-collects it (and every page inside). insertWidget() moves instead.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - drop zone (str): Where something was dropped on a tile: DROP_ZONE_CENTER or one of EDGE_DROP_ZONES.
# - EDGE_ZONE_FRACTION (float): Share of a tile, from each edge, that counts as that edge (default: 0.25).
# - EDGE_SPLITS (dict): Edge -> (splitter orientation needed, does the new leaf go after the old one?).
# - layout tree: A root QSplitter (never removed) holding splitters (branches) and tiles (leaves).
# - leaf / tile: Any non-QSplitter widget in the tree (a TabGroup or a ModuleTile).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS_AND_CONSTANTS
#    1.1 imports
#    1.2 drop_zone_constants
# 2. DROP_ZONES
#    2.1 edge_zone_at
#    2.2 zone_preview_rect
# 3. TREE_OPERATIONS
#    3.1 new_splitter
#    3.2 insert_beside
#    3.3 swap_leaves
#    3.4 normalize_tree
#    3.5 first_leaf
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QSplitter, QWidget
# --- END [1.1] ---

# ==============================================================================
# [1.2] drop_zone_constants
# ==============================================================================
DROP_ZONE_CENTER = "center"
DROP_ZONE_LEFT = "left"
DROP_ZONE_RIGHT = "right"
DROP_ZONE_TOP = "top"
DROP_ZONE_BOTTOM = "bottom"
EDGE_DROP_ZONES = (DROP_ZONE_LEFT, DROP_ZONE_RIGHT, DROP_ZONE_TOP, DROP_ZONE_BOTTOM)
EDGE_ZONE_FRACTION = 0.25

EDGE_SPLITS = {
    DROP_ZONE_LEFT: (Qt.Orientation.Horizontal, False),
    DROP_ZONE_RIGHT: (Qt.Orientation.Horizontal, True),
    DROP_ZONE_TOP: (Qt.Orientation.Vertical, False),
    DROP_ZONE_BOTTOM: (Qt.Orientation.Vertical, True),
}
# --- END [1.2] ---


# ==============================================================================
# [2.1] edge_zone_at
# ==============================================================================
def edge_zone_at(area_rect: QRect, point: QPoint, edge_fraction: float = EDGE_ZONE_FRACTION) -> str:
    """Nearest edge of area_rect if point is within edge_fraction of it; otherwise the centre."""
    if area_rect.width() <= 0 or area_rect.height() <= 0:
        return DROP_ZONE_CENTER
    x_fraction = (point.x() - area_rect.left()) / area_rect.width()
    y_fraction = (point.y() - area_rect.top()) / area_rect.height()
    edge_distances = {
        DROP_ZONE_LEFT: x_fraction,
        DROP_ZONE_RIGHT: 1 - x_fraction,
        DROP_ZONE_TOP: y_fraction,
        DROP_ZONE_BOTTOM: 1 - y_fraction,
    }
    nearest_edge = min(edge_distances, key=edge_distances.get)
    return nearest_edge if edge_distances[nearest_edge] < edge_fraction else DROP_ZONE_CENTER
# --- END [2.1] ---

# ==============================================================================
# [2.2] zone_preview_rect
# ==============================================================================
def zone_preview_rect(area_rect: QRect, drop_zone: str) -> QRect:
    """The half of area_rect a new tile would take for an edge zone; all of it otherwise."""
    preview_rect = QRect(area_rect)
    half_width_px = preview_rect.width() // 2
    half_height_px = preview_rect.height() // 2
    if drop_zone == DROP_ZONE_LEFT:
        preview_rect.setWidth(half_width_px)
    elif drop_zone == DROP_ZONE_RIGHT:
        preview_rect.setLeft(preview_rect.left() + half_width_px)
    elif drop_zone == DROP_ZONE_TOP:
        preview_rect.setHeight(half_height_px)
    elif drop_zone == DROP_ZONE_BOTTOM:
        preview_rect.setTop(preview_rect.top() + half_height_px)
    return preview_rect
# --- END [2.2] ---


# ==============================================================================
# [3.1] new_splitter
# ==============================================================================
def new_splitter(orientation: Qt.Orientation, seam_width_px: int) -> QSplitter:
    splitter = QSplitter(orientation, objectName="pane_splitter")
    splitter.setChildrenCollapsible(False)
    splitter.setHandleWidth(seam_width_px)
    return splitter
# --- END [3.1] ---

# ==============================================================================
# [3.2] insert_beside
# ==============================================================================
def insert_beside(leaf: QWidget, new_leaf: QWidget, drop_zone: str, seam_width_px: int) -> None:
    """Put new_leaf on the drop_zone side of leaf; the two share leaf's old space half and half.
    leaf must already be in a splitter; new_leaf must not be in the tree."""
    needed_orientation, is_new_leaf_after = EDGE_SPLITS[drop_zone]
    parent_splitter = leaf.parentWidget()

    if parent_splitter.count() == 1:
        parent_splitter.setOrientation(needed_orientation)    # a lone leaf can just turn its splitter

    position = parent_splitter.indexOf(leaf)
    if parent_splitter.orientation() == needed_orientation:
        # Join the existing row/column next to leaf, sharing leaf's space.
        sizes_px = parent_splitter.sizes()
        half_px = sizes_px[position] // 2
        sizes_px[position] -= half_px
        new_position = position + 1 if is_new_leaf_after else position
        parent_splitter.insertWidget(new_position, new_leaf)
        sizes_px.insert(new_position, half_px)
        parent_splitter.setSizes(sizes_px)
        return

    # Wrap leaf in a new splitter running the other way, in leaf's old slot.
    parent_sizes_px = parent_splitter.sizes()
    slot_length_px = _length_along(leaf, needed_orientation)
    wrapper_splitter = new_splitter(needed_orientation, seam_width_px)
    parent_splitter.insertWidget(position, wrapper_splitter)          # no replaceWidget(), see NOTES
    leaves_in_order = [leaf, new_leaf] if is_new_leaf_after else [new_leaf, leaf]
    for each_leaf in leaves_in_order:
        wrapper_splitter.addWidget(each_leaf)                         # moves leaf out of parent_splitter
    parent_splitter.setSizes(parent_sizes_px)
    wrapper_splitter.setSizes([slot_length_px // 2, slot_length_px - slot_length_px // 2])
# --- END [3.2] ---

# ==============================================================================
# [3.3] swap_leaves
# ==============================================================================
def swap_leaves(first_leaf_widget: QWidget, second_leaf_widget: QWidget) -> None:
    """Exchange the places of two leaves; both splitters keep their sizes."""
    first_splitter = first_leaf_widget.parentWidget()
    second_splitter = second_leaf_widget.parentWidget()
    first_sizes_px, second_sizes_px = first_splitter.sizes(), second_splitter.sizes()

    # A placeholder holds the first leaf's slot while the leaves trade places.
    placeholder = QWidget()
    first_splitter.insertWidget(first_splitter.indexOf(first_leaf_widget), placeholder)
    second_splitter.insertWidget(second_splitter.indexOf(second_leaf_widget), first_leaf_widget)
    first_splitter.insertWidget(first_splitter.indexOf(placeholder), second_leaf_widget)
    placeholder.setParent(None)
    placeholder.deleteLater()

    first_splitter.setSizes(first_sizes_px)
    if second_splitter is not first_splitter:
        second_splitter.setSizes(second_sizes_px)
# --- END [3.3] ---

# ==============================================================================
# [3.4] normalize_tree
# ==============================================================================
def normalize_tree(root_splitter: QSplitter) -> None:
    """Tidy the tree after a leaf left it, keeping every leaf's size where possible:
    - an inner splitter with no children is removed;
    - an inner splitter with one child is replaced by that child;
    - a splitter inside a splitter of the same orientation is merged into it;
    - if root_splitter holds a single splitter, that splitter's children move up into root.
    """
    _normalize_splitter(root_splitter)
    if root_splitter.count() == 1 and isinstance(root_splitter.widget(0), QSplitter):
        only_child_splitter = root_splitter.widget(0)
        root_splitter.setOrientation(only_child_splitter.orientation())
        _merge_into_parent(root_splitter, only_child_splitter)


def _normalize_splitter(splitter: QSplitter) -> None:
    position = 0
    while position < splitter.count():
        child = splitter.widget(position)
        if not isinstance(child, QSplitter):
            position += 1
            continue
        _normalize_splitter(child)                    # tidy the inside first
        if child.count() == 0:
            child.setParent(None)
            child.deleteLater()
        elif child.count() == 1 or child.orientation() == splitter.orientation():
            _merge_into_parent(splitter, child)
        else:
            position += 1


def _merge_into_parent(parent_splitter: QSplitter, child_splitter: QSplitter) -> None:
    """Replace child_splitter with its own children, splitting its space between them."""
    position = parent_splitter.indexOf(child_splitter)
    parent_sizes_px = parent_splitter.sizes()
    child_sizes_px = child_splitter.sizes()
    grandchildren = [child_splitter.widget(index) for index in range(child_splitter.count())]

    # The space the child had along the parent's direction is shared out in the child's ratios.
    if child_splitter.orientation() == parent_splitter.orientation():
        total_px = sum(child_sizes_px) or 1
        shares_px = [parent_sizes_px[position] * size_px // total_px for size_px in child_sizes_px]
    else:
        shares_px = [parent_sizes_px[position]]       # only one grandchild in this case

    for offset, grandchild in enumerate(grandchildren):
        parent_splitter.insertWidget(position + offset, grandchild)
    child_splitter.setParent(None)
    child_splitter.deleteLater()
    parent_splitter.setSizes(parent_sizes_px[:position] + shares_px + parent_sizes_px[position + 1:])
# --- END [3.4] ---

# ==============================================================================
# [3.5] first_leaf
# ==============================================================================
def first_leaf(root_splitter: QSplitter) -> QWidget | None:
    """First leaf in the tree, which is always the top-left tile."""
    widget = root_splitter
    while isinstance(widget, QSplitter):
        if widget.count() == 0:
            return None
        widget = widget.widget(0)
    return widget


def _length_along(widget: QWidget, orientation: Qt.Orientation) -> int:
    return widget.width() if orientation == Qt.Orientation.Horizontal else widget.height()
# --- END [3.5] ---
