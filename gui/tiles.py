"""Local OpenStreetMap tile cache + server for the maps.

The map pages ask http://127.0.0.1:<port>/tiles/{z}/{x}/{y}.png. A tile already on disk
is served from the cache (works offline); otherwise it is downloaded once from
OpenStreetMap, saved, then served. Only tiles the user actually looks at are fetched
- no bulk pre-downloading, as the OSM tile usage policy forbids that.
https://operations.osmfoundation.org/policies/tiles/
"""
from __future__ import annotations

import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
USER_AGENT = "POS-PPK-Processor/1.0 (internal survey tool; tile cache, no bulk download)"
REFRESH_AFTER_S = 30 * 86400        # re-download a cached tile after 30 days when online
MAX_PARALLEL = 2                    # OSM policy: keep parallel downloads low
TIMEOUT_S = 15
MAX_ZOOM = 19
_PATH_RE = re.compile(r"^/tiles/(\d{1,2})/(\d+)/(\d+)\.png$")
WEB_DIR = Path(__file__).resolve().parent / "web"


class TileCache:
    def __init__(self, cache_dir: Path):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._gate = threading.Semaphore(MAX_PARALLEL)
        self.offline_until = 0.0

    def get(self, z: int, x: int, y: int) -> bytes | None:
        if z > MAX_ZOOM or x >= 2 ** z or y >= 2 ** z:
            return None
        f = self.dir / str(z) / str(x) / f"{y}.png"
        fresh = f.exists() and time.time() - f.stat().st_mtime < REFRESH_AFTER_S
        if fresh or (f.exists() and time.time() < self.offline_until):
            return f.read_bytes()
        data = self._download(z, x, y)
        if data:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(f)
            return data
        return f.read_bytes() if f.exists() else None      # offline: use the old copy

    def _download(self, z, x, y) -> bytes | None:
        if time.time() < self.offline_until:
            return None
        with self._gate:
            try:
                r = self.session.get(OSM_URL.format(z=z, x=x, y=y), timeout=TIMEOUT_S)
                if r.status_code == 200 and r.content[:4] == b"\x89PNG":
                    return r.content
            except requests.RequestException:
                self.offline_until = time.time() + 60        # don't hammer while offline
        return None

    def cached_count(self) -> int:
        return sum(1 for _ in self.dir.rglob("*.png"))


class _Handler(BaseHTTPRequestHandler):
    cache: TileCache = None
    pages: dict = {}

    def do_GET(self):  # noqa: N802
        m = _PATH_RE.match(self.path)
        if m:
            data = self.cache.get(*map(int, m.groups()))
            if data is None:
                self.send_error(404)
                return
            self._send(data, "image/png")
            return
        rel = self.path.lstrip("/").split("?")[0]
        if rel.startswith("pages/") and rel[6:] in self.pages:
            self._send(self.pages[rel[6:]].encode("utf-8"), "text/html; charset=utf-8")
            return
        # bundled Leaflet files
        f = (WEB_DIR / rel).resolve()
        if WEB_DIR in f.parents and f.is_file():
            ctype = {"js": "application/javascript", "css": "text/css", "png": "image/png",
                     "html": "text/html; charset=utf-8"}.get(f.suffix[1:], "application/octet-stream")
            self._send(f.read_bytes(), ctype)
            return
        self.send_error(404)

    def _send(self, data: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):     # keep the console quiet
        pass


class TileServer:
    """Starts once per app on a free localhost port."""
    _instance: "TileServer | None" = None

    def __init__(self, cache_dir: Path):
        handler = type("Handler", (_Handler,), {"cache": TileCache(cache_dir), "pages": {}})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.cache = handler.cache
        self.pages = handler.pages
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @classmethod
    def get(cls, cache_dir: Path) -> "TileServer":
        if cls._instance is None:
            cls._instance = TileServer(cache_dir)
        return cls._instance

    def put_page(self, name: str, html: str) -> str:
        """Serve a map page from memory (no files written) and return its URL."""
        self.pages[name] = html
        return f"{self.base_url}/pages/{name}?v={time.time():.0f}"
