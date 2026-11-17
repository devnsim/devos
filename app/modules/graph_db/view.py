# ==============================================================================
# FILE: app/modules/graph_db/view.py
# OVERVIEW: Graph Database module - the user's Kùzu graph databases (in the library): schema tree, a
#           Cypher console, an interactive graph view and an inspector for the clicked node / relationship.
# PURPOSE:
#   1. Toolbar: database picker, New, Import…, Sample data, status line.
#   2. Blocks: Schema (node / rel tables with counts and properties; + tables, drop, show in graph),
#      Cypher (run with Ctrl+Enter, results grid, history, "show result as graph"), Graph (force layout,
#      click to inspect, labels), Inspector (properties of the selected element).
# CONTRACT:
#   - Requires: app.modules.base.ModuleView, app.modules.graph_db.engine, kuzu (optional), networkx,
#               pyqtgraph, app.clients.library, app.ui (ui_tools, plot_tools, tool_bag, canvas, theme),
#               QSS selectors #graph_db_panel, #cypher_editor
#   - Exposes: GraphDbModule(parent=None, context=None) with graph_db, item_id, open_item(), new_database(),
#              run_query(text), show_graph(sample), select_element(element); SchemaBlock, CypherBlock,
#              GraphViewBlock, InspectorBlock
# NOTES:
#   - [2026-11-17] Library access goes through the Knowledge engine (app.clients.library), not the content store (Phase 4).
#   - [2026-11-16] Created (KuzuDB graph database view).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - graph shown: The GraphSample the Graph block draws (the whole database's sample, or a query result).
# - selected element: The node or relationship last clicked (Inspector shows it).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SHARED
#    1.1 imports
#    1.2 table_dialog
# 2. BLOCKS
#    2.1 schema_block
#    2.2 cypher_block
#    2.3 graph_view_block
#    2.4 inspector_block
# 3. MODULE
#    3.1 init
#    3.2 databases
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from app.data.activity_log import get_activity_log
from app.clients.library import library
from app.modules.base import ModuleView
from app.modules.graph_db.engine import (
    KUZU_TYPES, GraphDb, GraphDbError, GraphSample, describe, element_key, is_node, is_rel, kuzu_unavailable_reason,
    properties_of,
)
from app.ui.canvas.block_canvas import BlockSpec
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.theme import BACKGROUND_COLOR, EDGE_COLOR, MUTED_TEXT_COLOR, TAG_COLORS, TEXT_COLOR
from app.ui.tool_bag import ToolSpec, register_tools
from app.ui.ui_tools import add_button, block_layout, control_label, fit_columns, set_status, status_label
from app.ui.engine_bridge import watch_library

HISTORY_SIZE = 30
DEFAULT_BLOCKS = {"split": "h", "sizes": [1, 4], "children": [
    {"kind": "schema"},
    {"split": "v", "sizes": [3, 2], "children": [
        {"split": "h", "sizes": [3, 1], "children": [{"kind": "graph"}, {"kind": "inspector"}]},
        {"kind": "cypher"},
    ]},
]}


def label_color(label: str, order: list[str] | None = None) -> str:
    """Colour of a table / label: by its position in the schema (distinct for the first 8), else a hash."""
    if order and label in order:
        return TAG_COLORS[order.index(label) % len(TAG_COLORS)]
    return TAG_COLORS[sum(map(ord, label)) % len(TAG_COLORS)]
# --- END [1.1] ---

# ==============================================================================
# [1.2] table_dialog
# ==============================================================================
class TableDialog(QDialog):
    """New node table (name, properties, primary key) or relationship table (name, from, to, properties)."""

    def __init__(self, parent: QWidget | None, is_rel: bool, node_tables: list[str]) -> None:
        super().__init__(parent)
        self.is_rel = is_rel
        self.setWindowTitle("New relationship table" if is_rel else "New node table")
        form = QFormLayout(self)
        self.name_edit = QLineEdit(placeholderText="e.g. Follows" if is_rel else "e.g. Person")
        form.addRow("Name", self.name_edit)
        if is_rel:
            self.from_combo, self.to_combo = QComboBox(), QComboBox()
            self.from_combo.addItems(node_tables)
            self.to_combo.addItems(node_tables)
            form.addRow("From", self.from_combo)
            form.addRow("To", self.to_combo)
        self.columns_edit = QPlainTextEdit(placeholderText="One property per line:  name TYPE\n"
                                                           "e.g.  name STRING\n      age INT64")
        self.columns_edit.setPlainText("" if is_rel else "name STRING")
        self.columns_edit.setMaximumHeight(120)
        form.addRow("Properties", self.columns_edit)
        form.addRow("", QLabel("Types: " + ", ".join(KUZU_TYPES[:10]) + " …", objectName="control_label", wordWrap=True))
        if not is_rel:
            self.key_edit = QLineEdit("name", placeholderText="primary key property")
            form.addRow("Primary key", self.key_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def columns(self) -> list[tuple[str, str]]:
        columns = []
        for line in self.columns_edit.toPlainText().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                columns.append((parts[0], " ".join(parts[1:]).upper()))
            elif parts:
                columns.append((parts[0], "STRING"))
        return columns
# --- END [1.2] ---


# ==============================================================================
# [2.1] schema_block
# ==============================================================================
class SchemaBlock(QWidget):
    def __init__(self, module: "GraphDbModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        add_button(row, "+ Node", lambda: module.new_table(False), "New node table")
        add_button(row, "+ Rel", lambda: module.new_table(True), "New relationship table")
        reserve_controls_space(row)
        self.schema_tree = QTreeWidget(objectName="schema_tree", headerHidden=True)
        self.schema_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.schema_tree.customContextMenuRequested.connect(self._menu)
        self.schema_tree.itemDoubleClicked.connect(self._show_table)
        layout.addWidget(self.schema_tree, 1)
        module.database_changed.connect(self.refresh)
        module.schema_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self.schema_tree.clear()
        graph_db = self.module.graph_db
        if graph_db is None:
            return
        try:
            tables = graph_db.tables()
            for caption, kind in (("Node tables", "NODE"), ("Relationship tables", "REL")):
                group = QTreeWidgetItem([caption])
                self.schema_tree.addTopLevelItem(group)
                for name in [name for name, table_kind in tables if table_kind == kind]:
                    count = graph_db.count(name, kind)
                    title = name
                    if kind == "REL":
                        title += "  " + ", ".join(f"{a}→{b}" for a, b in graph_db.connection_of(name))
                    item = QTreeWidgetItem([f"{title}  ·  {count}"])
                    item.setData(0, Qt.ItemDataRole.UserRole, (name, kind))
                    item.setForeground(0, pg.mkColor(label_color(name, self.module.label_order)))
                    for column, column_type, is_key in graph_db.columns(name):
                        child = QTreeWidgetItem([f"{column}  {column_type}{'  🔑' if is_key else ''}"])
                        child.setForeground(0, pg.mkColor(MUTED_TEXT_COLOR))
                        item.addChild(child)
                    group.addChild(item)
                group.setExpanded(True)
        except GraphDbError as error:
            self.module.report(f"Schema: {error}", is_error=True)

    def _table_of(self, item: QTreeWidgetItem | None):
        return item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None

    def _show_table(self, item: QTreeWidgetItem) -> None:
        table = self._table_of(item)
        if table:
            name, kind = table
            pattern = f"MATCH (n:{name}) RETURN n LIMIT 300" if kind == "NODE" else f"MATCH (a)-[r:{name}]->(b) RETURN a, r, b LIMIT 300"
            self.module.run_query(pattern, show_graph=True)

    def _menu(self, position) -> None:
        item = self.schema_tree.itemAt(position)
        table = self._table_of(item)
        if not table:
            return
        name, kind = table
        menu = QMenu(self)
        menu.addAction("Show in graph").triggered.connect(lambda: self._show_table(item))
        menu.addAction("Insert MATCH into console").triggered.connect(
            lambda: self.module.set_console_text(f"MATCH (n:{name}) RETURN n LIMIT 25" if kind == "NODE"
                                                 else f"MATCH (a)-[r:{name}]->(b) RETURN a, r, b LIMIT 25"))
        menu.addSeparator()
        menu.addAction(f"Drop table {name}…").triggered.connect(lambda: self.module.drop_table(name))
        menu.exec(self.schema_tree.viewport().mapToGlobal(position))
# --- END [2.1] ---

# ==============================================================================
# [2.2] cypher_block
# ==============================================================================
class CypherBlock(QWidget):
    def __init__(self, module: "GraphDbModule") -> None:
        super().__init__()
        self.module = module
        self.last_outcome = None
        layout, row = block_layout(self)
        add_button(row, "▶ Run", self.run, "Run (Ctrl+Enter); a selection runs only the selected text")
        self.history_combo = QComboBox(toolTip="Recent queries")
        self.history_combo.setMinimumWidth(160)
        self.history_combo.activated.connect(lambda: self.cypher_editor.setPlainText(self.history_combo.currentData() or ""))
        row.addWidget(self.history_combo, 1)
        add_button(row, "Show as graph", self._show_as_graph, "Draw the nodes and relationships of the last result")
        reserve_controls_space(row)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.cypher_editor = QPlainTextEdit(objectName="cypher_editor",
                                            placeholderText="MATCH (a)-[r]->(b) RETURN a, r, b LIMIT 25   (Ctrl+Enter runs)")
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(keys), self.cypher_editor, context=Qt.ShortcutContext.WidgetShortcut).activated.connect(self.run)
        self.results_table = QTableWidget(objectName="cypher_results_table")
        self.results_table.setAlternatingRowColors(True)
        splitter.addWidget(self.cypher_editor)
        splitter.addWidget(self.results_table)
        splitter.setSizes([90, 200])
        layout.addWidget(splitter, 1)
        self.result_label = control_label()
        layout.addWidget(self.result_label)
        module.console_text_requested.connect(self.cypher_editor.setPlainText)
        module.query_ran.connect(self.show_outcomes)
        self._fill_history()

    def run(self) -> None:
        cursor = self.cypher_editor.textCursor()
        text = cursor.selectedText().replace(" ", "\n") if cursor.hasSelection() else self.cypher_editor.toPlainText()
        if text.strip():
            self.module.run_query(text)
            self._fill_history()

    def _fill_history(self) -> None:
        self.history_combo.clear()
        for query in self.module.setting("history", []):
            self.history_combo.addItem(query.replace("\n", " ")[:80], query)

    def show_outcomes(self, outcomes: list) -> None:
        if not outcomes:
            return
        outcome = outcomes[-1]
        self.last_outcome = outcome
        self.results_table.clear()
        self.results_table.setColumnCount(len(outcome.columns))
        self.results_table.setHorizontalHeaderLabels(outcome.columns)
        self.results_table.setRowCount(min(len(outcome.rows), 2000))
        for row_index, values in enumerate(outcome.rows[:2000]):
            for column, value in enumerate(values):
                item = QTableWidgetItem(describe(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.results_table.setItem(row_index, column, item)
        fit_columns(self.results_table, 320)
        total = sum(entry.elapsed_ms for entry in outcomes)
        if outcome.error:
            self.result_label.setText(f"Error in statement {len(outcomes)}: {outcome.error}")
        else:
            self.result_label.setText(f"{len(outcomes)} statement(s) · {len(outcome.rows)} row(s) · {total:.1f} ms")

    def _show_as_graph(self) -> None:
        if self.last_outcome is None:
            return
        sample = GraphSample()
        for values in self.last_outcome.rows:
            for value in values:
                for element in (value.get("_nodes", []) + value.get("_rels", []) if isinstance(value, dict) and "_nodes" in value else [value]):
                    if is_node(element):
                        sample.nodes[element_key(element["_id"])] = element
                    elif is_rel(element):
                        sample.rels.append(element)
        self.module.show_graph(sample)

    def block_state(self) -> dict:
        return {"text": self.cypher_editor.toPlainText()}

    def restore_block_state(self, state: dict) -> None:
        self.cypher_editor.setPlainText(state.get("text", ""))
# --- END [2.2] ---

# ==============================================================================
# [2.3] graph_view_block
# ==============================================================================
class GraphViewBlock(QWidget):
    """Nodes coloured by label, arrows by relationship; spring layout; click selects."""

    def __init__(self, module: "GraphDbModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        add_button(row, "◎", module.show_whole_graph, "Whole graph: sample the database (up to 300 relationships)")
        add_button(row, "⟳", self.refresh, "Lay the graph out again")
        self.labels_check = QPushButton("Aa", checkable=True, checked=True, toolTip="Show node labels")
        self.labels_check.toggled.connect(self.refresh)
        row.addWidget(self.labels_check)
        self.info_label = control_label()
        row.addWidget(self.info_label, 1)
        reserve_controls_space(row)
        self.graph_widget = pg.GraphicsLayoutWidget()
        self.graph_widget.setBackground(BACKGROUND_COLOR)
        self.view_box = self.graph_widget.addViewBox()
        self.view_box.setAspectLocked(True)
        self.edges_item = pg.PlotCurveItem(pen=pg.mkPen(EDGE_COLOR, width=1.2), connect="pairs")
        self.arrows_item = pg.ScatterPlotItem(size=7, pen=None)
        self.nodes_item = pg.ScatterPlotItem(size=16, pen=pg.mkPen(BACKGROUND_COLOR, width=1.5), hoverable=True,
                                             hoverSize=20, tip=None)
        self.nodes_item.sigClicked.connect(self._on_node_clicked)
        for item in (self.edges_item, self.arrows_item, self.nodes_item):
            self.view_box.addItem(item)
        self.text_items: list[pg.TextItem] = []
        layout.addWidget(self.graph_widget, 1)
        self.keys: list[str] = []
        module.graph_shown.connect(self.refresh)

    def refresh(self) -> None:
        import networkx as nx
        sample: GraphSample = self.module.graph_sample
        for text in self.text_items:
            self.view_box.removeItem(text)
        self.text_items = []
        self.keys = list(sample.nodes)
        self.info_label.setText(f"{len(sample.nodes)} nodes · {len(sample.rels)} relationships")
        if not self.keys:
            self.nodes_item.setData([])
            self.edges_item.setData([], [])
            self.arrows_item.setData([])
            return
        graph = nx.DiGraph()
        graph.add_nodes_from(self.keys)
        edges = [(element_key(rel["_src"]), element_key(rel["_dst"])) for rel in sample.rels]
        graph.add_edges_from((a, b) for a, b in edges if a in sample.nodes and b in sample.nodes)
        positions = nx.spring_layout(graph, seed=7, k=1.6 / max(1, len(self.keys)) ** 0.5)
        points = np.array([positions[key] for key in self.keys])
        brushes = [pg.mkBrush(label_color(sample.nodes[key]["_label"], self.module.label_order)) for key in self.keys]
        self.nodes_item.setData(pos=points, brush=brushes, data=self.keys)
        segments, arrow_points = [], []
        for a, b in edges:
            if a in positions and b in positions:
                start, end = np.array(positions[a]), np.array(positions[b])
                segments += [start, end]
                arrow_points.append(start + (end - start) * 0.82)
        if segments:
            segments_array = np.array(segments)
            self.edges_item.setData(segments_array[:, 0], segments_array[:, 1])
            self.arrows_item.setData(pos=np.array(arrow_points), brush=pg.mkBrush(MUTED_TEXT_COLOR), symbol="o")
        else:
            self.edges_item.setData([], [])
            self.arrows_item.setData([])
        if self.labels_check.isChecked() and len(self.keys) <= 400:
            for key, point in zip(self.keys, points):
                node = sample.nodes[key]
                props = properties_of(node)
                caption = str(next(iter(props.values()), node["_label"]))[:24]
                text = pg.TextItem(caption, color=TEXT_COLOR, anchor=(0.5, -0.4))
                text.setPos(*point)
                self.view_box.addItem(text)
                self.text_items.append(text)
        self.view_box.autoRange(padding=0.12)

    def _on_node_clicked(self, _item, points, *_args) -> None:
        if len(points):
            key = points[0].data()
            self.module.select_element(self.module.graph_sample.nodes.get(key))
# --- END [2.3] ---

# ==============================================================================
# [2.4] inspector_block
# ==============================================================================
class InspectorBlock(QWidget):
    def __init__(self, module: "GraphDbModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        self.title_label = QLabel("Click a node in the graph", objectName="structure_title_label")
        row.addWidget(self.title_label, 1)
        reserve_controls_space(row)
        self.properties_table = QTableWidget(0, 2, objectName="inspector_table")
        self.properties_table.setHorizontalHeaderLabels(["Property", "Value"])
        self.properties_table.horizontalHeader().setStretchLastSection(True)
        self.properties_table.verticalHeader().hide()
        layout.addWidget(self.properties_table, 1)
        self.neighbours_btn = QPushButton("Expand neighbours")
        self.neighbours_btn.clicked.connect(module.expand_selected)
        layout.addWidget(self.neighbours_btn)
        module.element_selected.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        element = self.module.selected_element
        self.neighbours_btn.setEnabled(is_node(element))
        if element is None:
            self.title_label.setText("Click a node in the graph")
            self.properties_table.setRowCount(0)
            return
        self.title_label.setText((":" if is_node(element) else "[:") + element["_label"] + ("" if is_node(element) else "]"))
        props = properties_of(element)
        self.properties_table.setRowCount(len(props))
        for row_index, (key, value) in enumerate(props.items()):
            self.properties_table.setItem(row_index, 0, QTableWidgetItem(key))
            self.properties_table.setItem(row_index, 1, QTableWidgetItem(describe(value)))
# --- END [2.4] ---


# ==============================================================================
# [3.1] init
# ==============================================================================
class GraphDbModule(ModuleView):
    database_changed = Signal()
    schema_changed = Signal()
    graph_shown = Signal()
    element_selected = Signal()
    query_ran = Signal(list)
    console_text_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, context, "graph_db_panel")
        self.log = get_activity_log()
        self.store = library()
        self.graph_db: GraphDb | None = None
        self.item_id: int | None = None
        self.graph_sample = GraphSample()
        self.selected_element: dict | None = None
        self.label_order: list[str] = []                # table names in schema order (stable colours)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        reason = kuzu_unavailable_reason()
        if reason:
            layout.addWidget(QLabel(f"The graph database engine can't start.\n{reason}", objectName="module_error_label",
                                    wordWrap=True, alignment=Qt.AlignmentFlag.AlignCenter))
            return
        row = QHBoxLayout()
        self.db_combo = QComboBox(toolTip="Open a graph database from your library")
        self.db_combo.setMinimumWidth(170)
        self.db_combo.activated.connect(lambda: self.open_item(self.db_combo.currentData()))
        row.addWidget(self.db_combo)
        for text, handler, tip in (("New", self.new_database, "Create an empty graph database in your library"),
                                   ("Import…", self.import_database, "Copy a .kuzu database file into your library"),
                                   ("Sample data", self.load_sample, "Add a small people / cities / projects graph")):
            button = QPushButton(text, toolTip=tip)
            button.clicked.connect(handler)
            row.addWidget(button)
        self.module_status_label = status_label()
        row.addWidget(self.module_status_label, 1)
        reserve_controls_space(row)
        layout.addLayout(row)
        self.database_changed.connect(self._update_label_order)      # before the blocks: they draw with it
        self.schema_changed.connect(self._update_label_order)
        self.block_canvas = self.build_block_canvas([
            BlockSpec("schema", "Schema", "Node and relationship tables with their properties", lambda: SchemaBlock(self)),
            BlockSpec("cypher", "Cypher console", "Run Cypher queries; results, history, show as graph", lambda: CypherBlock(self)),
            BlockSpec("graph", "Graph", "Nodes and relationships drawn with a force layout", lambda: GraphViewBlock(self)),
            BlockSpec("inspector", "Inspector", "Properties of the clicked node", lambda: InspectorBlock(self)),
        ], DEFAULT_BLOCKS)
        layout.addWidget(self.block_canvas, 1)
        watch_library(self, self._on_content_changed)
        self._fill_combo()
        saved_id = self.setting("item_id")
        if saved_id is not None and self.store.get(saved_id) is not None:
            self.open_item(saved_id)
        else:
            self.report("Create a graph database with New (try Sample data next)")

    def _update_label_order(self) -> None:
        try:
            self.label_order = [name for name, _kind in self.graph_db.tables()] if self.graph_db else []
        except GraphDbError:
            self.label_order = []

    def report(self, text: str, is_error: bool = False) -> None:
        if is_error:
            self.log.warning("graph_db", text)
        set_status(self.module_status_label, text, is_error)
# --- END [3.1] ---

# ==============================================================================
# [3.2] databases
# ==============================================================================
    def _fill_combo(self) -> None:
        self.db_combo.clear()
        for item in self.store.items("graph"):
            self.db_combo.addItem(item.name, item.item_id)
        self.db_combo.setCurrentIndex(max(0, self.db_combo.findData(self.item_id)))

    def _on_content_changed(self, kind: str) -> None:
        if kind == "graph":
            self._fill_combo()
            if self.item_id is not None and self.store.get(self.item_id) is None:
                self._close()

    def _close(self) -> None:
        if self.graph_db is not None:
            self.graph_db.close()
        self.graph_db, self.item_id = None, None
        self.graph_sample, self.selected_element = GraphSample(), None
        self.database_changed.emit()
        self.graph_shown.emit()
        self.element_selected.emit()

    def open_item(self, item_id: int | None) -> None:
        if item_id is None or item_id == self.item_id:
            return
        item = self.store.get(item_id)
        if item is None:
            return
        self._close()
        try:
            with self.log.step("graph_db", f"Open graph database “{item.name}”"):
                self.graph_db = GraphDb(self.store.path(item))
        except GraphDbError as error:
            self.report(str(error), is_error=True)
            return
        self.item_id = item_id
        self.save_setting("item_id", item_id)
        self._fill_combo()
        self.database_changed.emit()
        self.show_whole_graph()
        self.report(f"{item.name} · {len(self.graph_db.tables())} tables")

    def new_database(self) -> None:
        name, is_ok = QInputDialog.getText(self, "New graph database", "Name", text="Graph")
        if not is_ok or not name.strip():
            return
        path = self.store.new_path("graph", ".kuzu")
        with self.log.step("graph_db", f"Create graph database “{name.strip()}”"):
            GraphDb(path).close()
            item = self.store.register_path("graph", self.store.unique_name("graph", name.strip()), path)
        self.open_item(item.item_id)

    def import_database(self) -> None:
        source, _filter = QFileDialog.getOpenFileName(self, "Import Kùzu database", "", "Kùzu database (*.kuzu *);;All files (*)")
        if not source:
            return
        path = self.store.new_path("graph", ".kuzu")
        with self.log.step("graph_db", f"Import {Path(source).name}"):
            shutil.copy2(source, path)
            if Path(source + ".wal").exists():
                shutil.copy2(source + ".wal", str(path) + ".wal")
            item = self.store.register_path("graph", self.store.unique_name("graph", Path(source).stem), path)
        self.open_item(item.item_id)

    def load_sample(self) -> None:
        if self.graph_db is None:
            self.report("Open or create a graph database first", is_error=True)
            return
        try:
            with self.log.step("graph_db", "Load sample graph"):
                self.graph_db.load_sample_data()
        except GraphDbError as error:
            self.report(f"Sample data: {error}", is_error=True)
            return
        self.schema_changed.emit()
        self.show_whole_graph()
        self.report("Loaded the sample graph — try: MATCH (p:Person)-[:LivesIn]->(c:City) RETURN p, c")

    def run_query(self, text: str, show_graph: bool = False) -> list:
        if self.graph_db is None:
            self.report("Open or create a graph database first", is_error=True)
            return []
        outcomes = self.graph_db.run(text)
        history = [text] + [query for query in self.setting("history", []) if query != text]
        self.save_setting("history", history[:HISTORY_SIZE])
        failed = next((outcome for outcome in outcomes if outcome.error), None)
        if failed:
            self.report(f"Query failed: {failed.error}", is_error=True)
        else:
            self.report(f"Ran {len(outcomes)} statement(s)")
            if any(statement.statement.split()[0].upper() in ("CREATE", "DROP", "ALTER", "MERGE", "DELETE", "SET", "COPY")
                   for statement in outcomes):
                self.schema_changed.emit()
        self.query_ran.emit(outcomes)
        if show_graph and outcomes and not failed:
            sample = GraphSample()
            for values in outcomes[-1].rows:
                for value in values:
                    if is_node(value):
                        sample.nodes[element_key(value["_id"])] = value
                    elif is_rel(value):
                        sample.rels.append(value)
            self.show_graph(sample)
        return outcomes

    def show_whole_graph(self) -> None:
        if self.graph_db is not None:
            try:
                self.show_graph(self.graph_db.graph_sample())
            except GraphDbError as error:
                self.report(f"Graph: {error}", is_error=True)

    def show_graph(self, sample: GraphSample) -> None:
        self.graph_sample = sample
        self.graph_shown.emit()

    def select_element(self, element: dict | None) -> None:
        self.selected_element = element
        self.element_selected.emit()

    def expand_selected(self) -> None:
        """Add the selected node's neighbours to the graph shown."""
        element = self.selected_element
        if not is_node(element) or self.graph_db is None:
            return
        identifier = element["_id"]
        rows = self.graph_db.query("MATCH (a)-[r]-(b) WHERE offset(id(a)) = $offset AND label(a) = $label RETURN a, r, b LIMIT 200",
                                   {"offset": identifier["offset"], "label": element["_label"]})
        sample = GraphSample(dict(self.graph_sample.nodes), list(self.graph_sample.rels))
        known_rels = {element_key(rel["_id"]) for rel in sample.rels}
        for source, rel, target in rows:
            sample.nodes[element_key(source["_id"])] = source
            sample.nodes[element_key(target["_id"])] = target
            if element_key(rel["_id"]) not in known_rels:
                sample.rels.append(rel)
        self.show_graph(sample)

    def new_table(self, is_rel: bool) -> None:
        if self.graph_db is None:
            self.report("Open or create a graph database first", is_error=True)
            return
        node_tables = self.graph_db.node_tables()
        if is_rel and not node_tables:
            self.report("Create a node table first (relationships connect node tables)", is_error=True)
            return
        dialog = TableDialog(self, is_rel, node_tables)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.name_edit.text().strip()
        try:
            with self.log.step("graph_db", f"Create {'relationship' if is_rel else 'node'} table {name}"):
                if is_rel:
                    self.graph_db.create_rel_table(name, dialog.from_combo.currentText(), dialog.to_combo.currentText(), dialog.columns())
                else:
                    self.graph_db.create_node_table(name, dialog.columns(), dialog.key_edit.text().strip())
        except GraphDbError as error:
            self.report(f"Create table: {error}", is_error=True)
            return
        self.schema_changed.emit()
        self.report(f"Created table {name}")

    def drop_table(self, name: str) -> None:
        if QMessageBox.question(self, "Drop table", f"Drop table {name} and everything in it? This can't be undone.") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            with self.log.step("graph_db", f"Drop table {name}"):
                self.graph_db.drop_table(name)
        except GraphDbError as error:
            self.report(f"Drop table: {error} (drop its relationship tables first)", is_error=True)
            return
        self.schema_changed.emit()
        self.show_whole_graph()

    def set_console_text(self, text: str) -> None:
        self.console_text_requested.emit(text)



register_tools([
    ToolSpec("graph_db.whole_graph", "Whole graph", "◎", "Draw a sample of the whole graph database",
             lambda module: module.show_whole_graph(), "graph_db", "draw"),
    ToolSpec("graph_db.expand", "Expand neighbours", "✳", "Add the selected node's neighbours to the drawing",
             lambda module: module.expand_selected(), "graph_db", "neighbors"),
])
# --- END [3.2] ---
