"""Listen to the PCS Display Port (UDP 5600) for live RMS and health.

The display port is a ~1 Hz UDP broadcast that POSView also reads. Any number of
PCs can listen, so this runs alongside POSView, the logger and USB logging.
Nothing is sent to the PCS.
"""
from __future__ import annotations

import socket
import threading
import time

from .logger import ALIGN_STATUS, LiveStatus, _StreamWatcher

DISPLAY_PORT = 5600
RECV_SIZE = 65536
STALE_S = 3.0

# Traffic-light limits for the health display (metres / degrees, 1-sigma)
POS_RMS_GOOD_M = 0.5
POS_RMS_WARN_M = 1.0
HEADING_RMS_GOOD_DEG = 0.1
HEADING_RMS_WARN_DEG = 0.5


class Health:
    GOOD, WARN, BAD, NONE = "good", "warn", "bad", "none"


def grade(value: float, good: float, warn: float) -> str:
    if value != value:                       # NaN
        return Health.NONE
    return Health.GOOD if value <= good else Health.WARN if value <= warn else Health.BAD


def health_summary(s: LiveStatus, age_s: float) -> dict[str, tuple[str, str]]:
    """Text and grade for each health line."""
    out = {}
    if age_s > STALE_S:
        out["link"] = (f"No display data for {age_s:.0f} s", Health.BAD)
    else:
        out["link"] = ("Receiving", Health.GOOD)
    a = s.align
    out["align"] = (ALIGN_STATUS.get(a, "-" if a < 0 else f"status {a}"),
                    Health.NONE if a < 0 else Health.GOOD if a == 0 else Health.WARN)
    gn = s.gnss_status or "-"
    out["gnss"] = (f"{gn}, {s.nsv} satellites",
                   Health.NONE if not s.gnss_status else Health.BAD if s.nsv < 5 else Health.GOOD)
    n, e, d = s.rms_neu
    horiz = (n * n + e * e) ** 0.5 if n == n and e == e else float("nan")
    out["pos_rms"] = (f"N {n:.3f}  E {e:.3f}  D {d:.3f} m" if n == n else "-",
                      max((grade(horiz, POS_RMS_GOOD_M, POS_RMS_WARN_M), grade(d, POS_RMS_GOOD_M, POS_RMS_WARN_M)),
                          key=["none", "good", "warn", "bad"].index))
    r, p, h = s.rms_rph
    out["att_rms"] = (f"roll {r:.3f}  pitch {p:.3f}  heading {h:.3f} deg" if r == r else "-",
                      grade(h, HEADING_RMS_GOOD_DEG, HEADING_RMS_WARN_DEG))
    out["pcs"] = (s.source_ip or "searching...", Health.GOOD if s.source_ip else Health.NONE)
    out["position"] = (f"{s.lat:.7f}, {s.lon:.7f}, h {s.h:.2f} m, {s.speed:.1f} m/s" if s.lat == s.lat else "-",
                       Health.NONE)
    return out


class DisplayListener:
    """Background UDP listener. Calls on_status(LiveStatus, age_s) about once a second."""

    def __init__(self, pcs_host: str = "", port: int = DISPLAY_PORT, on_status=None, log=print):
        self.pcs_host, self.port = pcs_host.strip(), port
        self.on_status, self.log = on_status or (lambda s, a: None), log
        self.watch = _StreamWatcher()
        self._stop = threading.Event()
        self.last_rx = 0.0

    def stop(self):
        self._stop.set()

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # share the port with POSView on this PC
        try:
            sock.bind(("", self.port))
        except OSError as exc:
            self.log(f"Cannot listen on UDP {self.port}: {exc}")
            self.watch.status.error = f"Cannot listen on UDP {self.port}: {exc}"
            self.on_status(self.watch.status, float("inf"))
            sock.close()
            return
        sock.settimeout(1.0)
        self.log(f"Listening for PCS display data on UDP {self.port}"
                 + (f" from {self.pcs_host}" if self.pcs_host else ""))
        last_emit = 0.0
        try:
            while not self._stop.is_set():
                try:
                    data, addr = sock.recvfrom(RECV_SIZE)
                except socket.timeout:
                    data, addr = None, None
                now = time.time()
                if data and (not self.pcs_host or addr[0] == self.pcs_host):
                    self.watch.feed(data)
                    self.last_rx = now
                    if self.watch.status.source_ip != addr[0]:
                        self.watch.status.source_ip = addr[0]
                        self.log(f"PCS found at {addr[0]} (display data on UDP {self.port})")
                if now - last_emit >= 1.0:
                    last_emit = now
                    self.on_status(self.watch.status, now - self.last_rx if self.last_rx else float("inf"))
        finally:
            sock.close()
