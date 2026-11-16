# ==============================================================================
# FILE: app/modules/map/view.py
# OVERVIEW: Map module - a slippy map (Leaflet in Qt WebEngine) with base layers, the user's places and
#           GeoJSON layers from the library. A first GIS step; heavy GIS (QGIS) can plug in later.
# PURPOSE:
#   1. MapBlock: the map page. Python -> JS through runJavaScript (queued until the page is ready);
#      JS -> Python through console messages prefixed "devos:" (JSON), no web channel needed.
#      Right-click the map to add a place; drag a marker to move it; the view (centre, zoom) is remembered.
#   2. PlacesBlock: list, filter, go to, rename, note, delete. LayersBlock: base map, GeoJSON layers on/off,
#      import GeoJSON, export places as GeoJSON.
#   3. Toolbar: "go to" field (lat, lon), coordinates under the mouse, status line.
# CONTRACT:
#   - Requires: app.modules.base.ModuleView, app.modules.map.store, app.ui.web_support (profile, availability),
#               app.data.store.content, app.ui (ui_tools, tool_bag, canvas), network access for tiles and
#               Leaflet (unpkg.com), QSS selector #map_panel
#   - Exposes: MapModule(parent=None, context=None) with store, add_place(), go_to(), places_changed, layers_changed,
#              view_requested; MapBlock, PlacesBlock, LayersBlock, BASE_LAYERS, LEAFLET_VERSION
# NOTES:
#   - [2026-11-16] Created (Map / GIS module).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - base layer: The background map tiles (street map, topographic, satellite; "Dark" = street tiles inverted).
# - bridge message: console.log("devos:" + JSON) from the page; {"type": "ready" | "moved" | "click" | ...}.
# - LEAFLET_VERSION (str): Pinned Leaflet release loaded from unpkg.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SHARED
#    1.1 imports
#    1.2 map_page_html
# 2. BLOCKS
#    2.1 map_block
#    2.2 places_block
#    2.3 layers_block
# 3. MODULE
#    3.1 map_module
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QVBoxLayout, QWidget,
)

from app.data.activity_log import get_activity_log
from app.data.store.content import get_content_store
from app.data.store.database import get_database
from app.modules.base import ModuleView
from app.modules.map.store import MapStore, places_geojson
from app.ui.canvas.block_canvas import BlockSpec
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.theme import TAG_COLORS
from app.ui.tool_bag import ToolSpec, register_tools
from app.ui.ui_tools import add_button, block_layout, control_label, set_status, status_label
from app.ui.web_support import browser_profile, web_engine_unavailable_reason

LEAFLET_VERSION = "1.9.4"
BASE_LAYERS = {          # name: (tile URL, attribution, max zoom, dark: invert the tiles to suit the dark theme)
    "Dark": ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", "&copy; OpenStreetMap contributors", 19, True),
    "Streets": ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", "&copy; OpenStreetMap contributors", 19, False),
    "Topographic": ("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
                    "&copy; OpenStreetMap contributors, SRTM | &copy; OpenTopoMap (CC-BY-SA)", 17, False),
    "Satellite": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                  "Tiles &copy; Esri", 19, False),
}
DEFAULT_VIEW = {"lat": 51.505, "lon": -0.09, "zoom": 3}
DEFAULT_BLOCKS = {"split": "h", "sizes": [4, 1], "children": [
    {"kind": "map"},
    {"split": "v", "sizes": [3, 2], "children": [{"kind": "places"}, {"kind": "layers"}]},
]}
_LAT_LON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$")


def parse_lat_lon(text: str) -> tuple[float, float] | None:
    match = _LAT_LON.match(text)
    if not match:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None
# --- END [1.1] ---

# ==============================================================================
# [1.2] map_page_html
# ==============================================================================
MAP_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://unpkg.com/leaflet@VERSION/dist/leaflet.css">
<style>html,body,#map{margin:0;height:100%;background:#1e1e1e;color:#c8c8c8;font:12px sans-serif}
.leaflet-container{background:#1e1e1e}#offline{padding:24px}
.dark-tiles .leaflet-tile-pane{filter:invert(1) hue-rotate(180deg) brightness(.85) contrast(.9)}</style></head>
<body><div id="map"></div>
<script>function send(o){console.log("devos:"+JSON.stringify(o));}</script>
<script src="https://unpkg.com/leaflet@VERSION/dist/leaflet.js"
        onerror="document.body.innerHTML='<div id=offline>The map library could not load (offline?). '+
        'DevOS needs internet access for map tiles.</div>';send({type:'offline'})"></script>
<script>
if (window.L) {
  const map = L.map('map', {zoomControl: true, worldCopyJump: true});
  let base = null, places = L.layerGroup().addTo(map), layers = {}, markers = {};
  window.devos = {
    setBase(url, attribution, maxZoom, dark) {
      if (base) map.removeLayer(base);
      base = L.tileLayer(url, {attribution, maxZoom}).addTo(map);
      document.getElementById('map').classList.toggle('dark-tiles', dark);
    },
    setView(lat, lon, zoom) { map.setView([lat, lon], zoom); },
    flyTo(lat, lon, zoom) { map.flyTo([lat, lon], zoom || Math.max(map.getZoom(), 12), {duration: 0.8}); },
    setPlaces(list, selected) {
      places.clearLayers(); markers = {};
      for (const p of list) {
        const m = L.marker([p.lat, p.lon], {draggable: true, title: p.name}).addTo(places);
        m.bindTooltip(p.name, {direction: 'top', offset: [-15, -12]});
        if (p.id === selected) m.openTooltip();
        m.on('click', () => send({type: 'place', id: p.id}));
        m.on('dragend', () => { const ll = m.getLatLng(); send({type: 'moved_place', id: p.id, lat: ll.lat, lon: ll.lng}); });
        markers[p.id] = m;
      }
    },
    setLayer(id, data, color, visible) {
      if (layers[id]) { map.removeLayer(layers[id]); delete layers[id]; }
      if (!visible) return;
      layers[id] = L.geoJSON(data, {style: {color, weight: 2, fillOpacity: 0.2},
        pointToLayer: (f, ll) => L.circleMarker(ll, {radius: 5, color}),
        onEachFeature: (f, l) => { if (f.properties) l.bindPopup(Object.entries(f.properties).slice(0, 12)
          .map(([k, v]) => '<b>' + k + '</b>: ' + v).join('<br>')); }}).addTo(map);
    },
    fitLayer(id) { if (layers[id]) map.fitBounds(layers[id].getBounds(), {padding: [20, 20]}); },
    fitPlaces() { const b = places.getLayers().map(m => m.getLatLng()); if (b.length) map.fitBounds(L.latLngBounds(b).pad(0.2)); },
  };
  map.on('moveend', () => { const c = map.getCenter(); send({type: 'view', lat: c.lat, lon: c.lng, zoom: map.getZoom()}); });
  let last = 0;
  map.on('mousemove', e => { const now = Date.now(); if (now - last > 80) { last = now;
    send({type: 'mouse', lat: e.latlng.lat, lon: e.latlng.lng}); } });
  map.on('contextmenu', e => send({type: 'context', lat: e.latlng.lat, lon: e.latlng.lng}));
  send({type: 'ready'});
}
</script></body></html>""".replace("VERSION", LEAFLET_VERSION)
# --- END [1.2] ---


# ==============================================================================
# [2.1] map_block
# ==============================================================================
def _bridge_page_class():
    from PySide6.QtWebEngineCore import QWebEnginePage

    class BridgePage(QWebEnginePage):
        """Turns console.log("devos:{...}") from the map page into Python calls."""

        def __init__(self, block: "MapBlock") -> None:
            super().__init__(browser_profile(), block)
            self.block = block

        def javaScriptConsoleMessage(self, level, message: str, line: int, source: str) -> None:  # noqa: N802
            if message.startswith("devos:"):
                try:
                    self.block.on_message(json.loads(message[6:]))
                except (ValueError, KeyError) as error:
                    get_activity_log().warning("map", f"Bad message from the map page: {error}")
    return BridgePage


class MapBlock(QWidget):
    def __init__(self, module: "MapModule") -> None:
        super().__init__()
        self.module = module
        self.is_ready = False
        self._pending: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        reason = web_engine_unavailable_reason()
        self.web_view = None
        if reason:
            layout.addWidget(QLabel(f"The map can't start here.\n{reason}", objectName="module_error_label", wordWrap=True))
            return
        from PySide6.QtWebEngineWidgets import QWebEngineView
        self.web_view = QWebEngineView(self)
        self.web_view.setPage(_bridge_page_class()(self))
        self.web_view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.web_view.setHtml(MAP_PAGE, QUrl("https://devos.map/"))
        layout.addWidget(self.web_view)
        module.places_changed.connect(self.push_places)
        module.layers_changed.connect(self.push_layers)
        module.base_changed.connect(self.push_base)
        module.view_requested.connect(lambda lat, lon, zoom: self.call(f"devos.flyTo({lat}, {lon}, {zoom})"))
        module.fit_requested.connect(lambda what: self.call("devos.fitPlaces()" if what == "places" else f"devos.fitLayer({json.dumps(what)})"))

    def call(self, script: str) -> None:
        if self.web_view is None:
            return
        if self.is_ready:
            self.web_view.page().runJavaScript(script)
        else:
            self._pending.append(script)

    def on_message(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "ready":
            self.is_ready = True
            view = self.module.setting("view", DEFAULT_VIEW)
            self.push_base()
            self.web_view.page().runJavaScript(f"devos.setView({view['lat']}, {view['lon']}, {view['zoom']})")
            self.push_places()
            self.push_layers()
            for script in self._pending:
                self.web_view.page().runJavaScript(script)
            self._pending = []
        elif kind == "offline":
            self.module.report("The map library couldn't load — no internet connection?", is_error=True)
        else:
            self.module.on_map_message(message)

    def push_base(self) -> None:
        url, attribution, max_zoom, is_dark = BASE_LAYERS.get(self.module.setting("base", "Dark"), BASE_LAYERS["Dark"])
        self.call(f"devos.setBase({json.dumps(url)}, {json.dumps(attribution)}, {max_zoom}, {json.dumps(is_dark)})")

    def push_places(self) -> None:
        places = [place.as_dict() for place in self.module.store.places()]
        self.call(f"devos.setPlaces({json.dumps(places)}, {json.dumps(self.module.selected_place_id)})")

    def push_layers(self) -> None:
        visible = set(self.module.setting("visible_layers", []))
        store = get_content_store()
        for index, item in enumerate(store.items("geo")):
            is_visible = item.item_id in visible
            data = "null"
            if is_visible:
                try:
                    data = store.path(item).read_text(encoding="utf-8")
                    json.loads(data)
                except (OSError, ValueError) as error:
                    self.module.report(f"Layer “{item.name}” can't be shown: {error}", is_error=True)
                    continue
            color = TAG_COLORS[index % len(TAG_COLORS)]
            self.call(f"devos.setLayer({json.dumps(str(item.item_id))}, {data}, {json.dumps(color)}, {json.dumps(is_visible)})")
# --- END [2.1] ---

# ==============================================================================
# [2.2] places_block
# ==============================================================================
class PlacesBlock(QWidget):
    def __init__(self, module: "MapModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        self.filter_edit = QLineEdit(placeholderText="Filter places", clearButtonEnabled=True)
        self.filter_edit.textChanged.connect(self.refresh)
        row.addWidget(self.filter_edit, 1)
        add_button(row, "Fit", lambda: module.fit_requested.emit("places"), "Zoom to all places")
        reserve_controls_space(row)
        self.places_list = QListWidget(objectName="places_list")
        self.places_list.itemClicked.connect(lambda item: module.select_place(item.data(Qt.ItemDataRole.UserRole), fly=True))
        self.places_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.places_list.customContextMenuRequested.connect(self._menu)
        layout.addWidget(self.places_list, 1)
        self.note_edit = QLineEdit(placeholderText="Note for the selected place")
        self.note_edit.editingFinished.connect(self._save_note)
        layout.addWidget(self.note_edit)
        layout.addWidget(control_label("Right-click the map to add a place · drag a marker to move it"))
        module.places_changed.connect(self.refresh)
        module.place_selected.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        needle = self.filter_edit.text().strip().lower()
        self.places_list.clear()
        for place in self.module.store.places():
            if needle and needle not in f"{place.name} {place.note}".lower():
                continue
            item = QListWidgetItem(f"{place.name}\n{place.latitude:.5f}, {place.longitude:.5f}")
            item.setData(Qt.ItemDataRole.UserRole, place.place_id)
            item.setToolTip(place.note)
            self.places_list.addItem(item)
            if place.place_id == self.module.selected_place_id:
                item.setSelected(True)
                self.places_list.setCurrentItem(item)
        place = self.module.store.get(self.module.selected_place_id) if self.module.selected_place_id else None
        self.note_edit.setEnabled(place is not None)
        if not self.note_edit.hasFocus():
            self.note_edit.setText(place.note if place else "")

    def _save_note(self) -> None:
        if self.module.selected_place_id:
            self.module.store.update(self.module.selected_place_id, note=self.note_edit.text())
            self.module.places_changed.emit()

    def _menu(self, position) -> None:
        item = self.places_list.itemAt(position)
        if item is None:
            return
        place = self.module.store.get(item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        menu.addAction("Go to").triggered.connect(lambda: self.module.select_place(place.place_id, fly=True))
        menu.addAction("Rename…").triggered.connect(lambda: self.module.rename_place(place.place_id))
        menu.addAction("Copy coordinates").triggered.connect(
            lambda: QApplication.clipboard().setText(f"{place.latitude}, {place.longitude}"))
        menu.addSeparator()
        menu.addAction("Delete").triggered.connect(lambda: self.module.delete_place(place.place_id))
        menu.exec(self.places_list.viewport().mapToGlobal(position))
# --- END [2.2] ---

# ==============================================================================
# [2.3] layers_block
# ==============================================================================
class LayersBlock(QWidget):
    def __init__(self, module: "MapModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        row.addWidget(control_label("Base map"))
        self.base_combo = QComboBox()
        self.base_combo.addItems(list(BASE_LAYERS))
        self.base_combo.setCurrentText(module.setting("base", "Dark"))
        self.base_combo.currentTextChanged.connect(module.set_base)
        row.addWidget(self.base_combo, 1)
        reserve_controls_space(row)
        self.layer_list = QListWidget(objectName="layer_list")
        self.layer_list.itemChanged.connect(self._on_item_changed)
        self.layer_list.itemDoubleClicked.connect(lambda item: module.fit_requested.emit(str(item.data(Qt.ItemDataRole.UserRole))))
        layout.addWidget(self.layer_list, 1)
        buttons = QHBoxLayout()
        add_button(buttons, "Import GeoJSON…", module.import_geojson)
        add_button(buttons, "Places → GeoJSON", module.export_places, "Save your places as a GeoJSON layer in the Library")
        layout.addLayout(buttons)
        module.layers_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        visible = set(self.module.setting("visible_layers", []))
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        for item in get_content_store().items("geo"):
            entry = QListWidgetItem(item.name)
            entry.setData(Qt.ItemDataRole.UserRole, item.item_id)
            entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            entry.setCheckState(Qt.CheckState.Checked if item.item_id in visible else Qt.CheckState.Unchecked)
            entry.setToolTip("Double-click to zoom to this layer")
            self.layer_list.addItem(entry)
        if not self.layer_list.count():
            hint = QListWidgetItem("No GeoJSON layers yet — import one")
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
            self.layer_list.addItem(hint)
        self.layer_list.blockSignals(False)

    def _on_item_changed(self, entry: QListWidgetItem) -> None:
        item_id = entry.data(Qt.ItemDataRole.UserRole)
        if item_id is not None:
            self.module.set_layer_visible(item_id, entry.checkState() == Qt.CheckState.Checked)
# --- END [2.3] ---


# ==============================================================================
# [3.1] map_module
# ==============================================================================
class MapModule(ModuleView):
    places_changed = Signal()
    place_selected = Signal()
    layers_changed = Signal()
    base_changed = Signal()
    view_requested = Signal(float, float, int)
    fit_requested = Signal(str)                      # "places" or a layer's item id

    def __init__(self, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, context, "map_panel")
        self.log = get_activity_log()
        self.store = MapStore(context.database if context is not None else get_database())
        self.selected_place_id: int | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        row = QHBoxLayout()
        self.goto_edit = QLineEdit(placeholderText="Go to:  lat, lon   (e.g. 48.8584, 2.2945)")
        self.goto_edit.setMaximumWidth(260)
        self.goto_edit.returnPressed.connect(self._go_to_typed)
        row.addWidget(self.goto_edit)
        self.mouse_label = QLabel(objectName="module_stats_label")
        self.mouse_label.setMinimumWidth(150)
        row.addWidget(self.mouse_label)
        self.module_status_label = status_label()
        row.addWidget(self.module_status_label, 1)
        reserve_controls_space(row)
        layout.addLayout(row)
        self.block_canvas = self.build_block_canvas([
            BlockSpec("map", "Map", "The map: base layer, places, GeoJSON layers", lambda: MapBlock(self)),
            BlockSpec("places", "Places", "Your places: go to, rename, note, delete", lambda: PlacesBlock(self)),
            BlockSpec("layers", "Layers", "Base map and GeoJSON layers from your library", lambda: LayersBlock(self)),
        ], DEFAULT_BLOCKS)
        layout.addWidget(self.block_canvas, 1)
        get_content_store().add_listener(self._on_content_changed)
        self.destroyed.connect(lambda *_: get_content_store().remove_listener(self._on_content_changed))

    def report(self, text: str, is_error: bool = False) -> None:
        if is_error:
            self.log.warning("map", text)
        set_status(self.module_status_label, text, is_error)

    def _on_content_changed(self, kind: str) -> None:
        if kind == "geo":
            self.layers_changed.emit()

    def on_map_message(self, message: dict) -> None:
        kind = message["type"]
        if kind == "view":
            self.save_setting("view", {"lat": round(message["lat"], 6), "lon": round(message["lon"], 6), "zoom": int(message["zoom"])})
        elif kind == "mouse":
            self.mouse_label.setText(f"{message['lat']:.5f}, {message['lon']:.5f}")
        elif kind == "context":
            self.add_place_at(message["lat"], message["lon"])
        elif kind == "place":
            self.select_place(int(message["id"]))
        elif kind == "moved_place":
            self.store.update(int(message["id"]), latitude=message["lat"], longitude=message["lon"])
            self.places_changed.emit()

    def add_place_at(self, lat: float, lon: float, name: str | None = None) -> None:
        if name is None:
            name, is_ok = QInputDialog.getText(self, "Add place", f"Name for {lat:.5f}, {lon:.5f}")
            if not is_ok:
                return
        place = self.store.add(name, lat, lon)
        self.log.info("map", f"Added place “{place.name}”")
        self.selected_place_id = place.place_id
        self.places_changed.emit()
        self.place_selected.emit()

    def select_place(self, place_id: int, fly: bool = False) -> None:
        self.selected_place_id = place_id
        self.place_selected.emit()
        place = self.store.get(place_id)
        if place and fly:
            self.view_requested.emit(place.latitude, place.longitude, 0)

    def rename_place(self, place_id: int) -> None:
        place = self.store.get(place_id)
        name, is_ok = QInputDialog.getText(self, "Rename place", "Name", text=place.name)
        if is_ok and name.strip():
            self.store.update(place_id, name=name.strip())
            self.places_changed.emit()

    def delete_place(self, place_id: int) -> None:
        place = self.store.get(place_id)
        if place and QMessageBox.question(self, "Delete place", f"Delete “{place.name}”?") == QMessageBox.StandardButton.Yes:
            self.store.delete(place_id)
            self.log.info("map", f"Deleted place “{place.name}”")
            self.places_changed.emit()

    def _go_to_typed(self) -> None:
        position = parse_lat_lon(self.goto_edit.text())
        if position is None:
            self.report("Type a position as: latitude, longitude", is_error=True)
            return
        self.view_requested.emit(position[0], position[1], 14)

    def set_base(self, name: str) -> None:
        self.save_setting("base", name)
        self.base_changed.emit()

    def set_layer_visible(self, item_id: int, is_visible: bool) -> None:
        visible = set(self.setting("visible_layers", []))
        visible = visible | {item_id} if is_visible else visible - {item_id}
        self.save_setting("visible_layers", sorted(visible))
        self.layers_changed.emit()
        if is_visible:
            self.fit_requested.emit(str(item_id))

    def import_geojson(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Import GeoJSON", "", "GeoJSON (*.geojson *.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if data.get("type") not in ("FeatureCollection", "Feature", "Point", "LineString", "Polygon", "MultiPolygon",
                                        "MultiLineString", "MultiPoint", "GeometryCollection"):
                raise ValueError("not GeoJSON (no FeatureCollection / geometry type)")
        except (OSError, ValueError, AttributeError) as error:
            self.report(f"Import failed: {error}", is_error=True)
            return
        with self.log.step("map", f"Import layer {Path(path).name}"):
            item = get_content_store().add_bytes("geo", Path(path).stem, Path(path).read_bytes(), ".geojson")
        self.set_layer_visible(item.item_id, True)

    def export_places(self) -> None:
        with self.log.step("map", "Export places as GeoJSON"):
            item = get_content_store().create_text("geo", "My places", places_geojson(self.store.places()), ".geojson")
        self.report(f"Saved “{item.name}” in your Library (Map layers)")


register_tools([
    ToolSpec("map.fit_places", "Zoom to places", "⌖", "Fit the map to all your places",
             lambda module: module.fit_requested.emit("places"), "map", "zoom fit"),
    ToolSpec("map.export_places", "Places → GeoJSON", "⇪", "Save your places as a GeoJSON layer",
             lambda module: module.export_places(), "map", "export"),
])
# --- END [3.1] ---
