"""Maps: OpenStreetMap tiles through the local tile cache, Leaflet bundled with the app.

MapView      - post-processing results (real-time vs post-processed track, base, CSV spots)
LiveMapView  - live truck position and trail from the PCS display port
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .tiles import TileServer

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except ImportError:          # map falls back to a message
    HAS_WEBENGINE = False

MAX_POINTS = 4000
MARGIN = 0
LIVE_TRAIL_POINTS = 3600      # ~1 hour at the 1 Hz display rate

_HEAD = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/leaflet/leaflet.css"/>
<script src="/leaflet/leaflet.js"></script>
<style>html,body,#map{height:100%;margin:0;background:#2c3e50}
.legend{background:#fff;padding:6px 8px;border-radius:4px;font:12px Arial;line-height:18px;color:#222}
.legend i{display:inline-block;width:14px;height:4px;margin-right:6px;vertical-align:middle}</style></head>
<body><div id="map"></div><script>
const map = L.map('map', {zoomControl: true});
L.tileLayer('/tiles/{z}/{x}/{y}.png', {maxZoom: 22, maxNativeZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
L.control.scale({metric: true, imperial: false}).addTo(map);
"""

RESULT_JS = """
const D = __DATA__;
const layers = {}; const all = [];
for (const s of D.sessions) {
  if (s.rt.length) { layers['Real-time ' + s.name] = L.polyline(s.rt, {color:'#e74c3c', weight:2}).addTo(map); all.push(...s.rt); }
  if (s.ppk.length) { layers['Post-processed ' + s.name] = L.polyline(s.ppk, {color:'#27ae60', weight:3}).addTo(map); all.push(...s.ppk); }
  if (s.base) { L.circleMarker(s.base, {radius:7, color:'#f39c12'}).bindTooltip('Base ' + s.base_name).addTo(map); }
}
const csv = L.layerGroup().addTo(map);
for (const c of D.csv) {
  L.circleMarker(c.pt, {radius:5, color: c.ok ? '#3498db' : '#f39c12', fillOpacity:0.9}).bindTooltip(c.name).addTo(csv);
}
layers['CSV captures'] = csv;
L.control.layers(null, layers).addTo(map);
const lg = L.control({position:'bottomleft'});
lg.onAdd = () => { const d = L.DomUtil.create('div','legend');
  d.innerHTML = '<i style="background:#e74c3c"></i>Real-time<br><i style="background:#27ae60"></i>Post-processed<br>' +
                '<i style="background:#3498db;height:8px;width:8px;border-radius:4px"></i>CSV capture'; return d; };
lg.addTo(map);
if (all.length) map.fitBounds(L.latLngBounds(all).pad(0.1)); else map.setView([40, -95], 4);
</script></body></html>"""

LIVE_JS = """
map.setView([40, -95], 4);
const trail = L.polyline([], {color:'#e74c3c', weight:3}).addTo(map);
const truck = L.circleMarker([0,0], {radius:8, color:'#fff', weight:2, fillColor:'#3498db', fillOpacity:1});
const nose = L.polyline([], {color:'#fff', weight:3});
let first = true, follow = true;
window.setFollow = (v) => { follow = v; };
window.clearTrail = () => trail.setLatLngs([]);
window.livePoint = (lat, lon, hdg, colour, maxPts) => {
  const p = [lat, lon];
  trail.addLatLng(p);
  const pts = trail.getLatLngs(); if (pts.length > maxPts) { pts.splice(0, pts.length - maxPts); trail.setLatLngs(pts); }
  truck.setLatLng(p).setStyle({fillColor: colour});
  if (!map.hasLayer(truck)) { truck.addTo(map); nose.addTo(map); }
  const r = (Math.PI / 180) * hdg, k = 0.00025;
  nose.setLatLngs([p, [lat + k * Math.cos(r), lon + k * Math.sin(r) / Math.cos(lat * Math.PI / 180)]]);
  if (first) { map.setView(p, 18); first = false; } else if (follow) { map.panTo(p, {animate: false}); }
};
</script></body></html>"""


def _decimate(lat, lon, n=MAX_POINTS):
    step = max(1, len(lat) // n)
    return [[round(float(a), 8), round(float(b), 8)] for a, b in zip(lat[::step], lon[::step])]


def _web_view():
    if HAS_WEBENGINE:
        return QWebEngineView()
    return QLabel("Map needs PySide6 QtWebEngine (pip install PySide6-Addons).")


class MapView(QWidget):
    def __init__(self, tile_cache_dir: Path, parent=None):
        super().__init__(parent)
        self.tile_cache_dir = Path(tile_cache_dir)
        self._create_widgets()
        self._create_layouts()
        self._connect_signals()
        self._apply_styles()
        self._load_initial_state()

    def _create_widgets(self):
        self.web_view = _web_view()
        self.server = TileServer.get(self.tile_cache_dir)

    def _create_layouts(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        self.main_layout.addWidget(self.web_view)

    def _connect_signals(self):
        pass

    def _apply_styles(self):
        pass

    def _load_initial_state(self):
        self.show_result(None)

    # --- Business logic ---
    def show_result(self, result) -> None:
        data = {"sessions": [], "csv": []}
        if result is not None:
            for sr in result.sessions:
                g1 = sr.pcs.g1
                entry = {"name": sr.name, "rt": _decimate(g1["lat"], g1["lon"]) if len(g1) else [], "ppk": [],
                         "base": list(sr.base_latlon) if sr.base_latlon else None, "base_name": sr.station}
                if sr.fusion is not None:
                    entry["ppk"] = _decimate(sr.fusion.lat, sr.fusion.lon)
                data["sessions"].append(entry)
            for r in result.csv:
                if r.points:
                    mid = r.points[len(r.points) // 2]
                    data["csv"].append({"name": r.path.name, "pt": [mid[0], mid[1]], "ok": bool(r.ok)})
        if HAS_WEBENGINE:
            url = self.server.put_page("result.html", _HEAD + RESULT_JS.replace("__DATA__", json.dumps(data)))
            self.web_view.load(QUrl(url))


class LiveMapView(QWidget):
    """Truck position and trail, updated from the display port about once a second."""

    def __init__(self, tile_cache_dir: Path, parent=None):
        super().__init__(parent)
        self.tile_cache_dir = Path(tile_cache_dir)
        self._create_widgets()
        self._create_layouts()
        self._connect_signals()
        self._apply_styles()
        self._load_initial_state()

    def _create_widgets(self):
        self.web_view = _web_view()
        self.follow_check = QCheckBox("Follow truck")
        self.clear_button = QPushButton("Clear trail")
        self.server = TileServer.get(self.tile_cache_dir)
        self._last = None
        self._ready = False          # page loaded; points sent before that are lost
        self._pending = None         # latest point that arrived before the page was ready

    def _create_layouts(self):
        self.control_layout = QHBoxLayout()
        self.control_layout.addWidget(self.follow_check)
        self.control_layout.addStretch()
        self.control_layout.addWidget(self.clear_button)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        self.main_layout.addLayout(self.control_layout)
        self.main_layout.addWidget(self.web_view, 1)

    def _connect_signals(self):
        self.follow_check.toggled.connect(self._on_follow_check_toggled)
        self.clear_button.clicked.connect(self._on_clear_button_clicked)
        if HAS_WEBENGINE:
            self.web_view.loadFinished.connect(self._on_web_view_load_finished)

    def _apply_styles(self):
        pass

    def _load_initial_state(self):
        self.follow_check.setChecked(True)
        if HAS_WEBENGINE:
            self.web_view.load(QUrl(self.server.put_page("live.html", _HEAD + LIVE_JS)))

    # --- Business logic ---
    def add_point(self, lat: float, lon: float, heading: float, colour: str):
        if not HAS_WEBENGINE or not (math.isfinite(lat) and math.isfinite(lon)) or (lat == 0 and lon == 0):
            return
        if not self._ready:
            self._pending = (lat, lon, heading, colour)    # a parked truck never sends a new point
            return
        point = (round(lat, 8), round(lon, 8))
        if point == self._last:
            return
        self._last = point
        hdg = heading if math.isfinite(heading) else 0.0
        self.web_view.page().runJavaScript(
            f"window.livePoint && livePoint({point[0]}, {point[1]}, {hdg:.2f}, '{colour}', {LIVE_TRAIL_POINTS});")

    def _on_web_view_load_finished(self, ok: bool):
        self._ready = ok
        if ok and self._pending:
            point, self._pending = self._pending, None
            self.add_point(*point)

    def _on_follow_check_toggled(self, on: bool):
        if HAS_WEBENGINE:
            self.web_view.page().runJavaScript(f"window.setFollow && setFollow({str(on).lower()});")

    def _on_clear_button_clicked(self):
        self._last = None
        if HAS_WEBENGINE:
            self.web_view.page().runJavaScript("window.clearTrail && clearTrail();")
