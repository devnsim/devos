# ==============================================================================
# FILE: app/ui/widgets/sidebar.py
# OVERVIEW: The collapsible navigation sidebar: the user's pages and folders, favourites, settings.
# PURPOSE:
#   1. Header: "DevOS" title and a button that asks the main window to close the sidebar.
#   2. Favorites: a reorderable list of pinned pages.
#   3. All Pages: a tree of folders and pages - create (+ / folder+ buttons, context menus), rename
#      (F2, double-click, context menu), delete, drag to reorder or move into folders.
#   4. Footer: a settings button in the bottom-right corner.
#   5. Loads SVG icons from assets/icons/ for itself and other widgets (load_icon).
# CONTRACT:
#   - Requires: app.data.store.pages.PageStore, assets/icons (page, globe, folder, folder_open, plus,
#               folder_plus, settings, sidebar_close .svg), app.ui.theme.TITLE_BAR_HEIGHT_PX,
#               QSS selectors #nav_sidebar_panel, #sidebar_header_panel, #app_title_label,
#               #close_sidebar_btn, #sidebar_section_row, #sidebar_section_label, #sidebar_tool_btn,
#               #sidebar_tree, #sidebar_footer_panel, #open_settings_btn
#   - Exposes: Sidebar(page_store) (QWidget) with signals page_selected(int), page_created(int),
#              page_renamed(int, str), page_deleted(int), settings_requested(), close_requested();
#              refresh(); select_page(page_id); clear_selection(); create_page(folder_id=None);
#              start_rename(page_id); MIN/MAX/DEFAULT_WIDTH_PX; load_icon(); ICON_SIZE; ICONS_DIRECTORY
# NOTES:
#   - [2026-11-16] Page icons come from the page kind (Page.icon: page.svg for canvases, globe.svg for browsers).
#   - [2026-11-14] load_icon() returns an empty QIcon and prints a warning when a file is missing.
#   - [2026-11-14] WA_StyledBackground is required: a QWidget *subclass* ignores its QSS background
#                  without it.
#   - [2026-11-15] Rebuilt for user pages: fixed app list (sections "Home"/"Workbenches", button rows,
#                  hand-made folders) replaced by Favorites + All Pages trees backed by PageStore.
#                  Settings moved to a gear in the footer. Folder deletion never deletes pages.
#   - [2026-11-15] Drag and drop uses QTreeWidget InternalMove (moves the item objects); after each drop
#                  the whole arrangement is written back with PageStore.apply_tree().
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - item role data: (kind, id) on each tree item; kind is "page" or "folder".
# - favourite: A page pinned to the Favorites list (it also stays in All Pages).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. ICONS
#    1.1 icon_constants
#    1.2 load_icon
# 2. PAGE_TREE
#    2.1 page_tree
# 3. SIDEBAR
#    3.1 class_signals_and_constants
#    3.2 init_and_layout
#    3.3 refresh
#    3.4 selection
#    3.5 create_rename_delete
#    3.6 tree_events
#    3.7 context_menus
# ==============================================================================

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QMenu, QMessageBox, QToolButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from app.ui.theme import TITLE_BAR_HEIGHT_PX

# ==============================================================================
# [1.1] icon_constants
# ==============================================================================
# This file is devos/app/ui/widgets/sidebar.py -> parents[3] is devos/
ICONS_DIRECTORY = Path(__file__).resolve().parents[3] / "assets" / "icons"
ICON_SIZE = QSize(16, 16)
ITEM_ROLE = Qt.ItemDataRole.UserRole
KIND_PAGE, KIND_FOLDER = "page", "folder"
# --- END [1.1] ---


# ==============================================================================
# [1.2] load_icon
# ==============================================================================
def load_icon(icon_file_name: str) -> QIcon:
    """Load an icon from assets/icons/. Missing files give an empty icon instead of crashing."""
    icon_file_path = ICONS_DIRECTORY / icon_file_name
    if not icon_file_path.exists():
        print(f"[DevOS] icon not found: {icon_file_path}")
        return QIcon()
    return QIcon(str(icon_file_path))
# --- END [1.2] ---


# ==============================================================================
# [2.1] page_tree
# ==============================================================================
class PageTree(QTreeWidget):
    """Tree of pages (and folders) with internal drag-and-drop; emits items_moved after a drop."""

    items_moved = Signal()

    def __init__(self, has_folders: bool) -> None:
        super().__init__(objectName="sidebar_tree")
        self.setHeaderHidden(True)
        self.setRootIsDecorated(has_folders)
        self.setIndentation(14 if has_folders else 0)
        self.setIconSize(ICON_SIZE)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed)     # F2; double-click handled below
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setExpandsOnDoubleClick(False)
        self.setAnimated(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def dropEvent(self, event) -> None:
        super().dropEvent(event)
        self.items_moved.emit()

    @staticmethod
    def item_key(item: QTreeWidgetItem | None) -> tuple[str, int] | None:
        return None if item is None else item.data(0, ITEM_ROLE)

    def find_item(self, kind: str, item_id: int) -> QTreeWidgetItem | None:
        stack = [self.invisibleRootItem()]
        while stack:
            parent = stack.pop()
            for index in range(parent.childCount()):
                child = parent.child(index)
                if child.data(0, ITEM_ROLE) == (kind, item_id):
                    return child
                stack.append(child)
        return None
# --- END [2.1] ---


# ==============================================================================
# [3.1] class_signals_and_constants
# ==============================================================================
class Sidebar(QWidget):
    page_selected = Signal(int)          # page id clicked
    page_created = Signal(int)           # new page (from + / menus); the main window opens it
    page_renamed = Signal(int, str)      # page id, stored title
    page_deleted = Signal(int)
    settings_requested = Signal()
    close_requested = Signal()

    MIN_WIDTH_PX = 160                   # drag limits for the seam
    MAX_WIDTH_PX = 400
    DEFAULT_WIDTH_PX = 220
    FAVORITES_MAX_ROWS = 8
# --- END [3.1] ---

# ==============================================================================
# [3.2] init_and_layout
# ==============================================================================
    def __init__(self, page_store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("nav_sidebar_panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(self.MIN_WIDTH_PX)
        self.setMaximumWidth(self.MAX_WIDTH_PX)
        self.page_store = page_store
        self._is_refreshing = False

        self.sidebar_layout = QVBoxLayout(self)
        self.sidebar_layout.setContentsMargins(0, 0, 0, 0)
        self.sidebar_layout.setSpacing(0)
        self._build_header()

        self.sidebar_layout.addWidget(self._section_row("Favorites"))
        self.favorites_tree = PageTree(has_folders=False)
        self.sidebar_layout.addWidget(self.favorites_tree)

        self.new_page_btn = self._tool_btn("plus.svg", "New page")
        self.new_page_btn.clicked.connect(lambda: self.create_page(self._selected_folder_id()))
        self.new_folder_btn = self._tool_btn("folder_plus.svg", "New folder")
        self.new_folder_btn.clicked.connect(lambda: self.create_folder(self._selected_folder_id()))
        self.sidebar_layout.addWidget(self._section_row("All Pages", [self.new_page_btn, self.new_folder_btn]))
        self.pages_tree = PageTree(has_folders=True)
        self.sidebar_layout.addWidget(self.pages_tree, 1)

        self._build_footer()
        for tree in (self.favorites_tree, self.pages_tree):
            tree.itemClicked.connect(self._on_item_clicked)
            tree.itemDoubleClicked.connect(lambda item, _column, tree=tree: tree.editItem(item, 0))
            tree.itemChanged.connect(self._on_item_changed)
            tree.customContextMenuRequested.connect(lambda position, tree=tree: self._show_context_menu(tree, position))
        self.pages_tree.items_moved.connect(self._save_tree_arrangement)
        self.favorites_tree.items_moved.connect(self._save_favorite_order)
        self.pages_tree.itemExpanded.connect(lambda item: self._on_folder_expanded(item, True))
        self.pages_tree.itemCollapsed.connect(lambda item: self._on_folder_expanded(item, False))
        self.refresh()

    def _build_header(self) -> None:
        # --- Header: app name + close button (part of the integrated title bar) ---
        self.sidebar_header_panel = QWidget(objectName="sidebar_header_panel")
        self.sidebar_header_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.sidebar_header_panel.setFixedHeight(TITLE_BAR_HEIGHT_PX)   # lines up with the tab rows
        header_layout = QHBoxLayout(self.sidebar_header_panel)
        header_layout.setContentsMargins(12, 0, 6, 0)
        self.app_title_label = QLabel("DevOS", objectName="app_title_label")
        header_layout.addWidget(self.app_title_label)
        header_layout.addStretch()
        self.close_sidebar_btn = QToolButton(objectName="close_sidebar_btn", toolTip="Close sidebar", autoRaise=True)
        self.close_sidebar_btn.setIcon(load_icon("sidebar_close.svg"))
        self.close_sidebar_btn.setIconSize(ICON_SIZE)
        self.close_sidebar_btn.clicked.connect(lambda: self.close_requested.emit())
        header_layout.addWidget(self.close_sidebar_btn)
        self.sidebar_layout.addWidget(self.sidebar_header_panel)

    def _section_row(self, title: str, buttons: list[QToolButton] = ()) -> QWidget:
        row = QWidget(objectName="sidebar_section_row")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(0)
        layout.addWidget(QLabel(title, objectName="sidebar_section_label"))
        layout.addStretch()
        for button in buttons:
            layout.addWidget(button)
        return row

    def _tool_btn(self, icon_file: str, tool_tip: str) -> QToolButton:
        button = QToolButton(objectName="sidebar_tool_btn", toolTip=tool_tip, autoRaise=True)
        button.setIcon(load_icon(icon_file))
        button.setIconSize(QSize(14, 14))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _build_footer(self) -> None:
        self.sidebar_footer_panel = QWidget(objectName="sidebar_footer_panel")
        footer_layout = QHBoxLayout(self.sidebar_footer_panel)
        footer_layout.setContentsMargins(6, 4, 6, 6)
        footer_layout.addStretch()
        self.open_settings_btn = QToolButton(objectName="open_settings_btn", toolTip="Settings", autoRaise=True)
        self.open_settings_btn.setIcon(load_icon("settings.svg"))
        self.open_settings_btn.setIconSize(QSize(18, 18))
        self.open_settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_settings_btn.clicked.connect(self.settings_requested)
        footer_layout.addWidget(self.open_settings_btn)
        self.sidebar_layout.addWidget(self.sidebar_footer_panel)
# --- END [3.2] ---

# ==============================================================================
# [3.3] refresh
# ==============================================================================
    def refresh(self) -> None:
        """Rebuild both trees from the store (keeps the highlighted page)."""
        selected = PageTree.item_key(self.pages_tree.currentItem())
        self._is_refreshing = True
        self.favorites_tree.clear()
        self.pages_tree.clear()
        page_flags = (Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable
                      | Qt.ItemFlag.ItemIsDragEnabled)
        folder_flags = page_flags | Qt.ItemFlag.ItemIsDropEnabled

        for page in self.page_store.favorites():
            self._new_item(self.favorites_tree.invisibleRootItem(), page.title, (KIND_PAGE, page.page_id), page.icon, page_flags)

        folders, pages = self.page_store.folders(), self.page_store.pages()
        folder_items: dict[int, QTreeWidgetItem] = {}
        children: dict[int | None, list] = {}
        for folder in folders:
            children.setdefault(folder.parent_id, []).append((folder.sort_order, 0, folder))
        for page in pages:
            children.setdefault(page.folder_id, []).append((page.sort_order, 1, page))

        def add_children(parent_item: QTreeWidgetItem, parent_id: int | None) -> None:
            for _order, kind_rank, entry in sorted(children.get(parent_id, []), key=lambda value: value[:2]):
                if kind_rank == 0:
                    item = self._new_item(parent_item, entry.name, (KIND_FOLDER, entry.folder_id), "folder.svg", folder_flags)
                    folder_items[entry.folder_id] = item
                    add_children(item, entry.folder_id)
                    item.setExpanded(entry.is_expanded)
                else:
                    self._new_item(parent_item, entry.title, (KIND_PAGE, entry.page_id), entry.icon, page_flags)

        add_children(self.pages_tree.invisibleRootItem(), None)
        self._fit_favorites_height()
        self._is_refreshing = False
        if selected is not None and selected[0] == KIND_PAGE:
            self.select_page(selected[1])

    def _new_item(self, parent: QTreeWidgetItem, text: str, key: tuple[str, int], icon_file: str, flags) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [text])
        item.setData(0, ITEM_ROLE, key)
        item.setIcon(0, load_icon(icon_file))
        item.setFlags(flags)
        item.setToolTip(0, text)
        return item

    def _fit_favorites_height(self) -> None:
        rows = max(1, min(self.favorites_tree.topLevelItemCount(), self.FAVORITES_MAX_ROWS))
        row_height = self.favorites_tree.sizeHintForRow(0) if self.favorites_tree.topLevelItemCount() else 26
        self.favorites_tree.setFixedHeight(rows * max(row_height, 22) + 6)
# --- END [3.3] ---

# ==============================================================================
# [3.4] selection
# ==============================================================================
    def select_page(self, page_id: int) -> None:
        """Highlight a page in both trees (opening its folders)."""
        for tree in (self.favorites_tree, self.pages_tree):
            item = tree.find_item(KIND_PAGE, page_id)
            tree.blockSignals(True)
            if item is None:
                tree.clearSelection()
                tree.setCurrentItem(None)
            else:
                parent = item.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                tree.setCurrentItem(item)
                tree.scrollToItem(item)
            tree.blockSignals(False)

    def clear_selection(self) -> None:
        for tree in (self.favorites_tree, self.pages_tree):
            tree.clearSelection()
            tree.setCurrentItem(None)

    def _selected_folder_id(self) -> int | None:
        """Folder for new items: the selected folder, or the folder of the selected page."""
        item = self.pages_tree.currentItem()
        if item is None or not item.isSelected():
            return None
        kind, item_id = item.data(0, ITEM_ROLE)
        if kind == KIND_FOLDER:
            return item_id
        parent = item.parent()
        return parent.data(0, ITEM_ROLE)[1] if parent is not None else None
# --- END [3.4] ---

# ==============================================================================
# [3.5] create_rename_delete
# ==============================================================================
    def create_page(self, folder_id: int | None = None) -> int:
        page_id = self.page_store.create_page(folder_id=folder_id)
        self.refresh()
        self.select_page(page_id)
        self.page_created.emit(page_id)
        return page_id

    def create_folder(self, parent_id: int | None = None) -> int:
        folder_id = self.page_store.create_folder(parent_id=parent_id)
        self.refresh()
        item = self.pages_tree.find_item(KIND_FOLDER, folder_id)
        if item is not None:
            self.pages_tree.setCurrentItem(item)
            self.pages_tree.editItem(item, 0)          # name it straight away
        return folder_id

    def start_rename(self, page_id: int) -> None:
        item = self.pages_tree.find_item(KIND_PAGE, page_id)
        if item is not None:
            self.select_page(page_id)
            self.pages_tree.editItem(item, 0)

    def set_page_title(self, page_id: int, title: str) -> None:
        """Show a new title (renamed elsewhere, e.g. in the page heading)."""
        self._is_refreshing = True
        for tree in (self.favorites_tree, self.pages_tree):
            item = tree.find_item(KIND_PAGE, page_id)
            if item is not None:
                item.setText(0, title)
                item.setToolTip(0, title)
        self._is_refreshing = False

    def delete_page(self, page_id: int, ask: bool = True) -> None:
        page = self.page_store.page(page_id)
        if page is None:
            return
        if ask and QMessageBox.question(
            self, "Delete page", f"Delete the page “{page.title}” and its layout?\nThis can't be undone.",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.page_store.delete_page(page_id)
        self.refresh()
        self.page_deleted.emit(page_id)

    def delete_folder(self, folder_id: int, ask: bool = True) -> None:
        if ask and QMessageBox.question(
            self, "Delete folder", "Delete this folder? Its pages and sub-folders move up a level.",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.page_store.delete_folder(folder_id)
        self.refresh()
# --- END [3.5] ---

# ==============================================================================
# [3.6] tree_events
# ==============================================================================
    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        kind, item_id = item.data(0, ITEM_ROLE)
        if kind == KIND_PAGE:
            self.page_selected.emit(item_id)
        else:
            item.setExpanded(not item.isExpanded())

    def _on_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._is_refreshing:
            return
        kind, item_id = item.data(0, ITEM_ROLE)
        if kind == KIND_FOLDER:
            name = self.page_store.rename_folder(item_id, item.text(0))
            self._set_text_quietly(item, name)
            return
        page = self.page_store.page(item_id)
        if page is None or page.title == item.text(0):
            return
        title = self.page_store.rename_page(item_id, item.text(0))
        self.set_page_title(item_id, title)
        self.page_renamed.emit(item_id, title)

    def _set_text_quietly(self, item: QTreeWidgetItem, text: str) -> None:
        self._is_refreshing = True
        item.setText(0, text)
        self._is_refreshing = False

    def _on_folder_expanded(self, item: QTreeWidgetItem, is_expanded: bool) -> None:
        if self._is_refreshing:
            return
        kind, item_id = item.data(0, ITEM_ROLE)
        if kind == KIND_FOLDER:
            item.setIcon(0, load_icon("folder_open.svg" if is_expanded else "folder.svg"))
            self.page_store.set_folder_expanded(item_id, is_expanded)

    def _save_tree_arrangement(self) -> None:
        entries = []

        def walk(parent_item: QTreeWidgetItem, parent_id: int | None) -> None:
            for order in range(parent_item.childCount()):
                child = parent_item.child(order)
                kind, item_id = child.data(0, ITEM_ROLE)
                entries.append((kind, item_id, parent_id, order))
                if kind == KIND_FOLDER:
                    walk(child, item_id)

        walk(self.pages_tree.invisibleRootItem(), None)
        self.page_store.apply_tree(entries)

    def _save_favorite_order(self) -> None:
        root = self.favorites_tree.invisibleRootItem()
        self.page_store.apply_favorite_order([root.child(index).data(0, ITEM_ROLE)[1] for index in range(root.childCount())])
# --- END [3.6] ---

# ==============================================================================
# [3.7] context_menus
# ==============================================================================
    def _show_context_menu(self, tree: PageTree, position) -> None:
        item = tree.itemAt(position)
        menu = QMenu(self)
        key = PageTree.item_key(item)
        if key is not None and key[0] == KIND_PAGE:
            page_id = key[1]
            page = self.page_store.page(page_id)
            menu.addAction("Open").triggered.connect(lambda: self.page_selected.emit(page_id))
            menu.addAction("Rename").triggered.connect(lambda: tree.editItem(item, 0))
            if page is not None and page.is_favorite:
                menu.addAction("Remove from Favorites").triggered.connect(lambda: self._set_favorite(page_id, False))
            else:
                menu.addAction("Add to Favorites").triggered.connect(lambda: self._set_favorite(page_id, True))
            menu.addSeparator()
            menu.addAction("Delete page…").triggered.connect(lambda: self.delete_page(page_id))
        elif key is not None and key[0] == KIND_FOLDER:
            folder_id = key[1]
            menu.addAction("New page here").triggered.connect(lambda: self.create_page(folder_id))
            menu.addAction("New folder here").triggered.connect(lambda: self.create_folder(folder_id))
            menu.addAction("Rename").triggered.connect(lambda: tree.editItem(item, 0))
            menu.addSeparator()
            menu.addAction("Delete folder…").triggered.connect(lambda: self.delete_folder(folder_id))
        else:
            menu.addAction("New page").triggered.connect(lambda: self.create_page(None))
            menu.addAction("New folder").triggered.connect(lambda: self.create_folder(None))
        menu.exec(tree.viewport().mapToGlobal(position))

    def _set_favorite(self, page_id: int, is_favorite: bool) -> None:
        self.page_store.set_favorite(page_id, is_favorite)
        self.refresh()
# --- END [3.7] ---
