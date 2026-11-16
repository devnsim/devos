# ==============================================================================
# FILE: app/modules/graph/view.py
# OVERVIEW: Graph tool: a plain 2D graph with a gear icon that opens its settings window - browse the
#           user's datasets, import CSVs, create mock data, choose columns and the graph's look.
# PURPOSE:
#   1. Plot one dataset: an x column against one or more y columns.
#   2. Settings window (SettingsDialog): Data tab (dataset list, import, mock generator, delete, columns)
#      and Appearance tab (title, axis labels, grid, line width, markers, legend).
#   3. Remembers its dataset, columns and appearance in its module settings.
# CONTRACT:
#   - Requires: pyqtgraph, app.modules.base.ModuleView, app.modules.graph.datasets, app.data.store.content,
#               app.ui (plot_tools, settings_dialog, ui_tools, canvas.tile_canvas), app.data.activity_log
#   - Exposes: GraphModule(parent=None, context=None); apply_config(config); config (dict); dataset
# NOTES:
#   - [2026-11-15] Created (reusable tools). Data never leaves the content library: imports are copied in,
#                  mock data is saved there, the user never picks a save location.
#   - [2026-11-15] apply_config reloads only when the chosen dataset differs from the one on screen.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - config (dict): {"dataset_id", "x_column", "y_columns", "title", "x_label", "y_label", "grid",
#   "line_width", "markers", "legend"} - saved as the module setting "graph".
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS
#    1.1 imports
# 2. GRAPH_MODULE
#    2.1 init
#    2.2 plotting
#    2.3 settings_window
#    2.4 data_actions
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from app.data.activity_log import get_activity_log
from app.data.store.content import get_content_store
from app.modules.base import ModuleView
from app.modules.graph.datasets import DatasetError, MOCK_GENERATORS, import_csv, load_dataset, make_mock, save_dataset
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.plot_tools import styled_plot
from app.ui.settings_dialog import SettingsDialog
from app.ui.ui_tools import control_label, tool_button

LINE_COLORS = [(100, 181, 246), (255, 183, 77), (129, 199, 132), (240, 98, 146), (179, 157, 219), (77, 182, 172)]
DEFAULT_CONFIG = {"dataset_id": None, "x_column": None, "y_columns": [], "title": "", "x_label": "", "y_label": "",
                  "grid": True, "line_width": 1.5, "markers": False, "legend": True}
# --- END [1.1] ---


# ==============================================================================
# [2.1] init
# ==============================================================================
class GraphModule(ModuleView):
    def __init__(self, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, context, "graph_panel")
        self.log = get_activity_log()
        self.config = {**DEFAULT_CONFIG, **(self.setting("graph", {}) or {})}
        self.dataset = None
        self.settings_window: SettingsDialog | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        self.dataset_label = control_label()
        self.settings_btn = tool_button("module_settings_btn", "settings.svg", "Graph settings: data and appearance")
        self.settings_btn.clicked.connect(self.open_settings)
        row.addWidget(self.dataset_label, 1)
        row.addWidget(self.settings_btn)
        reserve_controls_space(row)
        layout.addLayout(row)
        self.plot = styled_plot()
        self.legend = self.plot.getPlotItem().addLegend(offset=(-8, 8), labelTextSize="8pt")
        layout.addWidget(self.plot, 1)
        self.placeholder = pg.TextItem("No data yet — open ⚙ settings to choose, import or create a dataset",
                                       color=(140, 140, 140), anchor=(0.5, 0.5))
        self.plot.addItem(self.placeholder)
        get_content_store().add_listener(self._on_content_changed)
        self._load_and_plot()
# --- END [2.1] ---

# ==============================================================================
# [2.2] plotting
# ==============================================================================
    def _load_and_plot(self) -> None:
        self.dataset = None
        if self.config["dataset_id"] is not None:
            try:
                self.dataset = load_dataset(self.config["dataset_id"])
            except DatasetError as error:
                self.log.warning("graph", f"Graph can't show its dataset: {error}")
        self.redraw()

    def redraw(self) -> None:
        plot_item = self.plot.getPlotItem()
        for item in list(plot_item.listDataItems()):
            plot_item.removeItem(item)
        self.legend.clear()
        config, dataset = self.config, self.dataset
        plot_item.setTitle(config["title"] or None, color="#c8c8c8", size="10pt")
        plot_item.showGrid(x=config["grid"], y=config["grid"], alpha=0.15)
        self.legend.setVisible(config["legend"])
        if dataset is None:
            self.dataset_label.setText("no dataset")
            self.placeholder.setVisible(True)
            return
        self.placeholder.setVisible(False)
        x_column = config["x_column"] if config["x_column"] in dataset.columns else dataset.columns[0]
        y_columns = [name for name in config["y_columns"] if name in dataset.columns] or \
            [name for name in dataset.columns if name != x_column][:1]
        x = dataset.column(x_column)
        for index, name in enumerate(y_columns):
            color = LINE_COLORS[index % len(LINE_COLORS)]
            plot_item.plot(x, dataset.column(name), name=name, pen=pg.mkPen(color, width=config["line_width"]),
                           symbol="o" if config["markers"] else None, symbolSize=5, symbolBrush=color, connect="finite")
        plot_item.setLabel("bottom", config["x_label"] or x_column)
        plot_item.setLabel("left", config["y_label"] or (y_columns[0] if len(y_columns) == 1 else ""))
        plot_item.autoRange()
        self.dataset_label.setText(f"{dataset.name} · {len(x):,} points")

    def apply_config(self, config: dict) -> None:
        """New settings: save them, reload the data if the dataset changed, redraw."""
        # Compare with what is on screen (not self.config: the settings window may have pre-set it).
        dataset_changed = config.get("dataset_id", self.config["dataset_id"]) != (self.dataset.item_id if self.dataset else None)
        self.config = {**self.config, **config}
        self.save_setting("graph", self.config)
        if dataset_changed:
            self._load_and_plot()
        else:
            self.redraw()
        self.log.info("graph", "Graph settings applied")
# --- END [2.2] ---

# ==============================================================================
# [2.3] settings_window
# ==============================================================================
    def open_settings(self) -> None:
        if self.settings_window is None:
            self._build_settings_window()
        self._fill_dataset_list()
        self._read_config_into_fields()
        self.settings_window.open_window()

    def _build_settings_window(self) -> None:
        window = SettingsDialog(self.window(), "Graph")
        # --- Data tab ---
        data_page = QWidget()
        data_layout = QVBoxLayout(data_page)
        data_layout.addWidget(QLabel("Datasets in your library"))
        self.dataset_list = QListWidget()
        self.dataset_list.currentItemChanged.connect(self._on_dataset_selected)
        data_layout.addWidget(self.dataset_list, 1)
        buttons = QHBoxLayout()
        import_btn = QPushButton("Import CSV…")
        import_btn.clicked.connect(self._on_import)
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._on_delete_dataset)
        buttons.addWidget(import_btn)
        buttons.addStretch()
        buttons.addWidget(delete_btn)
        data_layout.addLayout(buttons)
        columns = QFormLayout()
        self.x_column_combo = QComboBox()
        self.y_column_list = QListWidget()
        self.y_column_list.setMaximumHeight(110)
        columns.addRow("X column", self.x_column_combo)
        columns.addRow("Y columns", self.y_column_list)
        data_layout.addLayout(columns)
        mock_box = QGroupBox("Create mock data")
        mock_form = QFormLayout(mock_box)
        self.mock_kind_combo = QComboBox()
        self.mock_kind_combo.addItems(MOCK_GENERATORS)
        self.mock_points_spin = QSpinBox(minimum=10, maximum=1_000_000, value=500, singleStep=100)
        self.mock_amplitude_spin = QDoubleSpinBox(minimum=-1e6, maximum=1e6, value=1.0, decimals=3)
        self.mock_frequency_spin = QDoubleSpinBox(minimum=0.0, maximum=1e3, value=0.5, decimals=3)
        self.mock_noise_spin = QDoubleSpinBox(minimum=0.0, maximum=1e6, value=0.05, decimals=3)
        self.mock_name_edit = QLineEdit(placeholderText="name (optional)")
        create_btn = QPushButton("Create and use")
        create_btn.clicked.connect(self._on_create_mock)
        for label, field in (("Shape", self.mock_kind_combo), ("Points", self.mock_points_spin),
                             ("Amplitude", self.mock_amplitude_spin), ("Frequency", self.mock_frequency_spin),
                             ("Noise", self.mock_noise_spin), ("Name", self.mock_name_edit), ("", create_btn)):
            mock_form.addRow(label, field)
        data_layout.addWidget(mock_box)
        window.add_tab("Data", data_page)
        # --- Appearance tab ---
        form = window.add_form_tab("Appearance")
        self.title_edit, self.x_label_edit, self.y_label_edit = QLineEdit(), QLineEdit(), QLineEdit()
        self.grid_check, self.markers_check, self.legend_check = QCheckBox(), QCheckBox(), QCheckBox()
        self.line_width_spin = QDoubleSpinBox(minimum=0.5, maximum=8.0, singleStep=0.5, value=1.5)
        for label, field in (("Title", self.title_edit), ("X axis label", self.x_label_edit),
                             ("Y axis label", self.y_label_edit), ("Grid", self.grid_check),
                             ("Line width", self.line_width_spin), ("Point markers", self.markers_check),
                             ("Legend", self.legend_check)):
            form.addRow(label, field)
        window.applied.connect(self._apply_from_fields)
        self.settings_window = window

    def _fill_dataset_list(self) -> None:
        self.dataset_list.blockSignals(True)
        self.dataset_list.clear()
        for item in get_content_store().items("dataset"):
            row = QListWidgetItem(item.name)
            row.setData(Qt.ItemDataRole.UserRole, item.item_id)
            self.dataset_list.addItem(row)
            if item.item_id == self.config["dataset_id"]:
                self.dataset_list.setCurrentItem(row)
        self.dataset_list.blockSignals(False)
        self._on_dataset_selected()

    def _on_dataset_selected(self) -> None:
        current = self.dataset_list.currentItem()
        self.x_column_combo.clear()
        self.y_column_list.clear()
        if current is None:
            return
        try:
            dataset = self.dataset if self.dataset and self.dataset.item_id == current.data(Qt.ItemDataRole.UserRole) \
                else load_dataset(current.data(Qt.ItemDataRole.UserRole))
        except DatasetError as error:
            self.log.warning("graph", f"Can't read that dataset: {error}")
            return
        self.x_column_combo.addItems(dataset.columns)
        if self.config["x_column"] in dataset.columns:
            self.x_column_combo.setCurrentText(self.config["x_column"])
        for name in dataset.columns:
            row = QListWidgetItem(name)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            is_checked = name in self.config["y_columns"] or (not self.config["y_columns"] and name != dataset.columns[0])
            row.setCheckState(Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
            self.y_column_list.addItem(row)

    def _read_config_into_fields(self) -> None:
        config = self.config
        self.title_edit.setText(config["title"])
        self.x_label_edit.setText(config["x_label"])
        self.y_label_edit.setText(config["y_label"])
        self.grid_check.setChecked(config["grid"])
        self.markers_check.setChecked(config["markers"])
        self.legend_check.setChecked(config["legend"])
        self.line_width_spin.setValue(config["line_width"])

    def _apply_from_fields(self) -> None:
        current = self.dataset_list.currentItem()
        y_columns = [self.y_column_list.item(index).text() for index in range(self.y_column_list.count())
                     if self.y_column_list.item(index).checkState() == Qt.CheckState.Checked]
        self.apply_config({
            "dataset_id": current.data(Qt.ItemDataRole.UserRole) if current else None,
            "x_column": self.x_column_combo.currentText() or None, "y_columns": y_columns,
            "title": self.title_edit.text(), "x_label": self.x_label_edit.text(), "y_label": self.y_label_edit.text(),
            "grid": self.grid_check.isChecked(), "line_width": self.line_width_spin.value(),
            "markers": self.markers_check.isChecked(), "legend": self.legend_check.isChecked(),
        })
# --- END [2.3] ---

# ==============================================================================
# [2.4] data_actions
# ==============================================================================
    def _on_import(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self.settings_window, "Import CSV dataset", "", "CSV (*.csv *.txt)")
        if not file_path:
            return
        try:
            item = import_csv(file_path)
        except DatasetError:
            return                                        # step() logged it with details
        self.config["dataset_id"] = item.item_id
        self._fill_dataset_list()

    def _on_create_mock(self) -> None:
        kind = self.mock_kind_combo.currentText()
        columns, data = make_mock(kind, self.mock_points_spin.value(), self.mock_amplitude_spin.value(),
                                  self.mock_frequency_spin.value(), self.mock_noise_spin.value())
        item = save_dataset(self.mock_name_edit.text().strip() or f"Mock {kind.lower()}", columns, data, {"mock": kind})
        self.config["dataset_id"], self.config["x_column"], self.config["y_columns"] = item.item_id, "x", ["y"]
        self._fill_dataset_list()
        self._apply_from_fields()

    def _on_delete_dataset(self) -> None:
        current = self.dataset_list.currentItem()
        if current is not None:
            get_content_store().delete(current.data(Qt.ItemDataRole.UserRole))

    def _on_content_changed(self, kind: str) -> None:
        if kind != "dataset":
            return
        if self.settings_window is not None and self.settings_window.isVisible():
            self._fill_dataset_list()
        if self.config["dataset_id"] is not None and get_content_store().get(self.config["dataset_id"]) is None:
            self.apply_config({"dataset_id": None})
# --- END [2.4] ---
