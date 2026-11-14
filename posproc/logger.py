"""Log POS LV data from the PCS Ethernet Logging Data Port (TCP 5603) to .000/.001 files.

The PCS sends the same byte stream it writes to its own USB logging, so the files
this writes are read by the processing side unchanged. The logger only listens:
it never sends anything to the PCS, so it cannot change POS settings.

Command line:
  python -m posproc.logger --host 192.168.0.40 --out D:\\POSLogs
"""
from __future__ import annotations

import argparse
import datetime as dt
import socket
import struct
import sys
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HOST = "192.168.0.40"        # your trucks' PCS (Applanix factory default is 192.168.53.100)
DEFAULT_PORT = 5603                  # Ethernet Logging Data Port (POS ICD)
FILE_SIZE = 8 * 1024 * 1024          # same ~8 MB split as PCS USB logging
RECV_SIZE = 65536
CONNECT_TIMEOUT_S = 5.0
RECV_TIMEOUT_S = 2.0
NO_DATA_WARN_S = 5.0
RECONNECT_WAIT_S = 3.0
REQUIRED_GROUPS = {1: "navigation", 2: "RMS", 3: "GNSS status", 10001: "raw GNSS"}
ALIGN_STATUS = {0: "Full navigation", 1: "Fine alignment", 2: "GC CHI 2 (aligning with GNSS)",
                3: "PC CHI 2 (aligning, no GNSS)", 4: "GC CHI 1 (aligning with GNSS)",
                5: "PC CHI 1 (aligning, no GNSS)", 6: "Coarse levelling", 7: "Initial solution", 8: "No solution"}
GNSS_STATUS = {-1: "unknown", 0: "no data", 1: "C/A 2D", 2: "C/A 3D", 3: "DGPS 2D", 4: "DGPS 3D",
               5: "RTK float", 6: "RTK wide-lane", 7: "RTK fixed", 8: "P-code", 9: "OmniSTAR HP",
               10: "OmniSTAR XP", 11: "OmniSTAR VBS", 13: "OmniSTAR G2", 14: "Trimble RTX"}


@dataclass
class LiveStatus:
    connected: bool = False
    state: str = "Stopped"
    bytes_total: int = 0
    rate_kbps: float = 0.0
    files: list[str] = field(default_factory=list)
    current_file: str = ""
    group_rates: dict[int, float] = field(default_factory=dict)
    lat: float = float("nan")
    lon: float = float("nan")
    h: float = float("nan")
    speed: float = float("nan")
    heading: float = float("nan")
    rms_neu: tuple[float, float, float] = (float("nan"),) * 3
    rms_rph: tuple[float, float, float] = (float("nan"),) * 3     # roll, pitch, heading deg
    align: int = -1
    source_ip: str = ""           # address the data came from (found automatically)
    gnss_status: str = ""
    nsv: int = 0
    since_data_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    error: str = ""


class _StreamWatcher:
    """Parses complete records from the live stream for the status display only."""

    def __init__(self):
        self.buf = b""
        self.counts: Counter = Counter()
        self.history: deque = deque()          # (time, Counter snapshot)
        self.status = LiveStatus()

    def feed(self, data: bytes):
        self.buf += data
        b, i, n = self.buf, 0, len(self.buf)
        while True:
            j = b.find(b"$", i)
            if j < 0:
                self.buf = b""
                return
            if j + 8 > n:
                self.buf = b[j:]
                return
            tag = b[j:j + 4]
            if tag not in (b"$GRP", b"$MSG"):
                i = j + 1
                continue
            rid, bc = struct.unpack_from("<HH", b, j + 4)
            end = j + 8 + bc
            if end > n:
                self.buf = b[j:]
                return
            if b[end - 2:end] != b"$#":
                i = j + 1
                continue
            if tag == b"$GRP":
                self.counts[rid] += 1
                self._group(rid, b, j, bc)
            i = end

    def _group(self, gid, b, j, bc):
        s = self.status
        if gid == 1 and bc == 132:
            s.lat, s.lon, s.h = struct.unpack_from("<ddd", b, j + 34)
            s.heading = struct.unpack_from("<d", b, j + 34 + 24 + 12 + 16)[0]
            s.speed = struct.unpack_from("<f", b, j + 34 + 24 + 12 + 32 + 4)[0]
            s.align = b[j + 134]
        elif gid == 2 and bc == 80:
            n, e, d = struct.unpack_from("<fff", b, j + 34)
            s.rms_neu = (n, e, d)
            s.rms_rph = struct.unpack_from("<fff", b, j + 58)
        elif gid == 3:
            st, nsv = struct.unpack_from("<bB", b, j + 34)
            s.gnss_status, s.nsv = GNSS_STATUS.get(st, f"mode {st}"), nsv

    def rates(self, now: float) -> dict[int, float]:
        self.history.append((now, Counter(self.counts)))
        while self.history and now - self.history[0][0] > 5:
            self.history.popleft()
        t0, c0 = self.history[0]
        span = now - t0
        if span < 0.5:
            return {}
        return {g: (self.counts[g] - c0.get(g, 0)) / span for g in sorted(self.counts)}


class PcsLogger:
    """Connects to the PCS logging port and writes LV_1_YYYYMMDDHHMM.nnn files."""

    def __init__(self, host: str, out_root: Path, port: int = DEFAULT_PORT, prefix: str = "LV_1",
                 file_size: int = FILE_SIZE, on_status=None, log=print):
        self.host, self.port, self.prefix, self.file_size = host, port, prefix, file_size
        self.out_root = Path(out_root)
        self.on_status, self.log = on_status or (lambda s: None), log
        self._stop = threading.Event()
        self._fh = None
        self._seq = 0
        self._base = ""
        self._folder: Path | None = None
        self.watch = _StreamWatcher()

    # ---------- control ----------
    def stop(self):
        self._stop.set()

    def run(self) -> LiveStatus:
        """Blocking loop: connect, log, reconnect on drops, until stop() is called."""
        s = self.watch.status
        start_wall = time.time()
        last_data, last_tick, bytes_at_tick = time.time(), time.time(), 0
        try:
            while not self._stop.is_set():
                s.state = f"Connecting to {self.host}:{self.port}..."
                self._emit()
                try:
                    sock = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT_S)
                except OSError as exc:
                    s.connected, s.error = False, f"Cannot connect to {self.host}:{self.port} ({exc})"
                    self.log(s.error)
                    self._emit()
                    self._stop.wait(RECONNECT_WAIT_S)
                    continue
                sock.settimeout(RECV_TIMEOUT_S)
                s.connected, s.error, s.state = True, "", "Logging"
                self.log(f"Connected to {self.host}:{self.port}")
                last_data = time.time()
                try:
                    while not self._stop.is_set():
                        try:
                            data = sock.recv(RECV_SIZE)
                        except socket.timeout:
                            data = None
                        now = time.time()
                        if data == b"":
                            raise ConnectionError("PCS closed the connection")
                        if data:
                            self._write(data)
                            self.watch.feed(data)
                            s.bytes_total += len(data)
                            last_data = now
                        s.since_data_s = now - last_data
                        if now - last_tick >= 1.0:
                            s.rate_kbps = (s.bytes_total - bytes_at_tick) / (now - last_tick) / 1024
                            bytes_at_tick, last_tick = s.bytes_total, now
                            s.group_rates = self.watch.rates(now)
                            s.warnings = self._warnings(now - start_wall, s)
                            self._emit()
                except (OSError, ConnectionError) as exc:
                    s.connected, s.error = False, f"Connection lost: {exc}. Reconnecting..."
                    self.log(s.error)
                    self._emit()
                    self._stop.wait(RECONNECT_WAIT_S)
                finally:
                    sock.close()
        finally:
            self._close_file()
            s.connected, s.state = False, "Stopped"
            self._emit()
        return s

    # ---------- files ----------
    def _new_session(self):
        now = dt.datetime.now()
        self._folder = self.out_root / now.strftime("%y%m%d")
        self._folder.mkdir(parents=True, exist_ok=True)
        base = f"{self.prefix}_{now:%Y%m%d%H%M}"
        while any(self._folder.glob(base + ".*")):          # never overwrite an earlier log
            now += dt.timedelta(minutes=1)
            base = f"{self.prefix}_{now:%Y%m%d%H%M}"
        self._base, self._seq = base, 0

    def _open_next(self):
        self._close_file()
        if not self._base:
            self._new_session()
        path = self._folder / f"{self._base}.{self._seq:03d}"
        self._seq += 1
        self._fh = open(path, "wb")
        self.watch.status.files.append(str(path))
        self.watch.status.current_file = str(path)
        self.log(f"Writing {path}")

    def _write(self, data: bytes):
        if self._fh is None or self._fh.tell() >= self.file_size:
            self._open_next()
        self._fh.write(data)

    def _close_file(self):
        if self._fh:
            self._fh.flush()
            self._fh.close()
            self._fh = None

    # ---------- status ----------
    def _warnings(self, elapsed: float, s: LiveStatus) -> list[str]:
        w = []
        if s.since_data_s > NO_DATA_WARN_S:
            w.append(f"No data for {s.since_data_s:.0f} s - is the PCS navigating and logging port groups set?")
        if elapsed > 10:
            missing = [f"{g} ({name})" for g, name in REQUIRED_GROUPS.items() if self.watch.counts[g] == 0]
            if missing:
                w.append("Not receiving group " + ", ".join(missing) +
                         " - add it in POSView: Logging > Ethernet Logging group select.")
        return w

    def _emit(self):
        self.on_status(self.watch.status)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Log POS LV Ethernet logging port to .000 files")
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"PCS IP address (default {DEFAULT_HOST})")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--out", required=True, help="Folder for logs (a YYMMDD subfolder is made)")
    a = ap.parse_args(argv)
    lg = PcsLogger(a.host, Path(a.out), a.port)
    last = [0.0]

    def show(s: LiveStatus):
        if time.time() - last[0] < 5:
            return
        last[0] = time.time()
        g = ", ".join(f"G{k}:{v:.0f}Hz" for k, v in s.group_rates.items() if k in REQUIRED_GROUPS)
        print(f"{s.state} | {s.bytes_total / 1e6:.1f} MB {s.rate_kbps:.0f} kB/s | {g} | "
              f"GNSS {s.gnss_status} {s.nsv} sv | " + " | ".join(s.warnings), flush=True)

    lg.on_status = show
    print("Press Ctrl+C to stop.")
    t = threading.Thread(target=lg.run, daemon=True)
    t.start()
    try:
        while t.is_alive():
            t.join(0.5)
    except KeyboardInterrupt:
        lg.stop()
        t.join(10)
    print("Files:\n  " + "\n  ".join(lg.watch.status.files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
