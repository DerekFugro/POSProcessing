"""Listen to the PCS Display Port (UDP 5600) for live RMS and health.

The display port is a ~1 Hz UDP broadcast that POSView also reads. Any number of
PCs can listen, so this runs alongside POSView, the logger and USB logging.
Nothing is sent to the PCS.
"""
from __future__ import annotations

import socket
import threading
import time

from .logger import ALIGN_STATUS, NO_DATA_WARN_S, LiveStatus, _StreamWatcher

DISPLAY_PORT = 5600
RECV_SIZE = 65536
STALE_S = 3.0

# Traffic-light limits for the health display (metres / degrees, 1-sigma)
POS_RMS_GOOD_M = 0.5
POS_RMS_WARN_M = 1.0
HEADING_RMS_GOOD_DEG = 0.1
HEADING_RMS_WARN_DEG = 0.5

# Record tab status light (one light for field users; agreed with Derek 2026-11-15):
# green = Full navigation, GNSS OK, position RMS under 1 m; orange = aligning or RMS high;
# red = no data from the POS. Looser than the Diagnostics rows on purpose: OmniSTAR G2 sits
# near 0.5 m, so the 0.5 m row limit would make the big light flicker.
RECORD_POS_RMS_OK_M = 1.0
LOOKING_FOR_POS_S = 10.0     # grey "looking" before red "no data" when nothing has arrived yet


class Health:
    GOOD, WARN, BAD, NONE = "good", "warn", "bad", "none"


def grade(value: float, good: float, warn: float) -> str:
    if value != value:                       # NaN
        return Health.NONE
    return Health.GOOD if value <= good else Health.WARN if value <= warn else Health.BAD


def nav_age(s: LiveStatus, now: float | None = None) -> float:
    """Seconds since navigation data (Group 1) last arrived; inf if never."""
    return ((time.time() if now is None else now) - s.nav_time) if s.nav_time else float("inf")


NO_NAV_TEXT = "No navigation data (Group 1)"


def health_summary(s: LiveStatus, age_s: float, now: float | None = None) -> dict[str, tuple[str, str]]:
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
    if age_s <= STALE_S and s.source_ip and nav_age(s, now) > STALE_S:
        # POS is talking but not navigating: don't show the last solution as if it were current
        out["align"] = (NO_NAV_TEXT, Health.BAD)
        for key in ("pos_rms", "att_rms", "position"):
            out[key] = ("-", Health.NONE)
    return out


def record_status(s: LiveStatus, age_s: float, waited_s: float, recording: bool = False,
                  log: LiveStatus | None = None, now: float | None = None) -> tuple[str, str, str]:
    """One plain-language status for the Record tab: (Health grade, title, detail).

    s, age_s: latest display-port status and its age. waited_s: time since listening began.
    recording, log: whether the logger is running and its status (TCP 5603)."""
    if recording and log is not None and not log.connected and log.error:
        return Health.BAD, "Not recording", "Cannot reach the POS logging port. Check the network cable. Retrying..."
    if age_s > STALE_S:
        if not s.source_ip and waited_s < LOOKING_FOR_POS_S:
            return Health.NONE, "Looking for the POS...", "Waiting for data from the POS."
        return Health.BAD, "No data from the POS", "Check the POS is switched on and the network cable is plugged in."
    if nav_age(s, now) > STALE_S:
        if not s.nav_time and waited_s < LOOKING_FOR_POS_S:
            return Health.NONE, "Looking for the POS...", "Waiting for navigation data from the POS."
        return Health.BAD, "No navigation from the POS", ("The POS is on but not sending a position. "
                                                           "It may be in standby - start navigation in POSView.")
    if recording and log is not None and log.lever_gnss is not None and all(v == 0.0 for v in log.lever_gnss):
        return Health.WARN, "POS lever arms not set", "The POS reports zero lever arms - check POSView."
    if recording and log is not None and log.warnings:
        if log.since_data_s > NO_DATA_WARN_S:
            return Health.WARN, "Recording, but no data", f"Nothing received for {log.since_data_s:.0f} s."
        return Health.WARN, "Recording, but data is missing", "Some data needed for processing is missing - see Diagnostics."
    if s.align != 0:
        nav = ALIGN_STATUS.get(s.align, f"status {s.align}")
        return Health.WARN, "Wait - POS is aligning", f"Navigation: {nav}. Wait for Full navigation."
    if not s.gnss_status:
        return Health.WARN, "Waiting for GNSS", "No GNSS status from the POS yet."
    if s.nsv < 5:
        return Health.WARN, "Few satellites", f"{s.gnss_status}, {s.nsv} satellites."
    n, e, d = s.rms_neu
    worst = max(abs(x) for x in ((n * n + e * e) ** 0.5, d)) if n == n and e == e and d == d else float("nan")
    if not worst <= RECORD_POS_RMS_OK_M:
        return Health.WARN, "Position accuracy low", (f"Position error about {worst:.1f} m." if worst == worst
                                                      else "No position accuracy from the POS yet.")
    return Health.GOOD, "POS ready", f"Full navigation · {s.gnss_status}, {s.nsv} satellites"


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
