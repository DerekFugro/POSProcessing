"""Marks: when another app (Surveyor) stops to take images, record the POS navigation for a
few minutes into a CSV, keep one line per mark in the session's marks.csv, and sum up how
good the position was (light, RMS, attitude spread) when the mark ends.

Fed from the logger thread with every Group 1 record (about 50 Hz on the ARAN PCS). Values are
the POS real-time solution at the POS reference point (ellipsoidal height), so they are for
tying images to time and checking the van stood still; the accurate positions come later from
post-processing the .000 files.
"""
from __future__ import annotations

import csv
import math
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MARK_SECONDS = 300.0
MAX_MARK_SECONDS = 3600.0
WALL_GRACE_S = 5.0            # end a mark by the PC clock this long after its time if POS time stalls
TIME_TYPES = {0: "POS", 1: "GPS", 2: "UTC"}       # POS time-types byte, low nibble = Time 1
NAV_COLUMNS = ["time1_s", "time_type", "lat_deg", "lon_deg", "height_m", "roll_deg", "pitch_deg",
               "heading_deg", "speed_mps", "align_status", "rms_n_m", "rms_e_m", "rms_d_m"]
MARK_COLUMNS = NAV_COLUMNS + ["light", "message"]
SUMMARY_COLUMNS = ["mark", "label", "file", "start_time1_s", "time_type", "lat_deg", "lon_deg",
                   "height_m", "roll_deg", "pitch_deg", "heading_deg", "speed_mps", "rms_n_m",
                   "rms_e_m", "rms_d_m", "light", "message"]
LIGHT_RANK = {"green": 0, "orange": 1, "grey": 2, "red": 3}   # worst wins; grey = no status yet
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class MarkError(ValueError):
    """A mark could not be started (plain-language reason for the other app)."""


def safe_label(label: str) -> str:
    """Label usable in a file name: letters, digits, _ . - (others become _)."""
    return _UNSAFE.sub("_", label.strip())[:60].strip("_") or "mark"


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v)


@dataclass
class _Stats:
    """Running summary of one mark."""
    first_t: float | None = None
    last_t: float | None = None
    time_type: str = ""
    green_whole_time: bool = True
    worst_light: str = ""
    worst_message: str = ""
    max_rms: dict = field(default_factory=lambda: {"rms_n_m": None, "rms_e_m": None, "rms_d_m": None})
    angle_ref: dict = field(default_factory=dict)       # first value of each angle
    angle_lo: dict = field(default_factory=dict)
    angle_hi: dict = field(default_factory=dict)

    def add(self, row: dict, light: str, message: str):
        t = row["time1_s"]
        if self.first_t is None:
            self.first_t = t
        self.last_t = t
        self.time_type = row.get("time_type") or self.time_type
        if light != "green":
            self.green_whole_time = False
        if not self.worst_light or LIGHT_RANK.get(light, 2) > LIGHT_RANK.get(self.worst_light, 2):
            self.worst_light, self.worst_message = light, message
        for k in self.max_rms:
            v = row.get(k)
            if _finite(v) and (self.max_rms[k] is None or v > self.max_rms[k]):
                self.max_rms[k] = float(v)
        for k in ("roll_deg", "pitch_deg", "heading_deg"):
            v = row.get(k)
            if not _finite(v):
                continue
            ref = self.angle_ref.setdefault(k, v)
            d = (v - ref + 180.0) % 360.0 - 180.0          # wrap-safe (heading across north)
            self.angle_lo[k] = min(self.angle_lo.get(k, d), d)
            self.angle_hi[k] = max(self.angle_hi.get(k, d), d)

    def summary(self, rows: int) -> dict:
        spread = {f"{k.replace('_deg', '')}_spread_deg": (self.angle_hi[k] - self.angle_lo[k]) if k in self.angle_hi
                  else None for k in ("roll_deg", "pitch_deg", "heading_deg")}
        return {"green_whole_time": self.green_whole_time and rows > 0,
                "worst_light": self.worst_light or "grey", "worst_message": self.worst_message,
                "max_rms_n_m": self.max_rms["rms_n_m"], "max_rms_e_m": self.max_rms["rms_e_m"],
                "max_rms_d_m": self.max_rms["rms_d_m"], **spread, "time_type": self.time_type,
                "first_time1_s": self.first_t, "last_time1_s": self.last_t}


@dataclass
class _Mark:
    number: int
    label: str
    path: Path
    seconds: float
    writer: object = None
    fh: object = None
    start_t: float | None = None
    wall_start: float = 0.0
    rows: int = 0
    done: bool = False
    stats: _Stats = field(default_factory=_Stats)

    def info(self, complete: bool) -> dict:
        return {"mark": self.number, "label": self.label, "file": str(self.path), "seconds": self.seconds,
                "rows": self.rows, "complete": complete, **self.stats.summary(self.rows)}


class MarkRecorder:
    """Thread-safe: start()/set_light() from the GUI thread, add_nav()/tick() from the logger thread."""

    def __init__(self, on_done=None):
        self._lock = threading.Lock()
        self._active: list[_Mark] = []
        self._count = 0
        self._light, self._message = "grey", ""
        self.on_done = on_done or (lambda info: None)

    def set_light(self, light: str, message: str):
        """Current status light (green/orange/red/grey) and its title; stored on every CSV row."""
        with self._lock:
            self._light, self._message = light, message

    def start(self, session: Path, label: str, seconds: float, snapshot: dict, folder: str | None = None) -> dict:
        """Begin a mark CSV, in `folder` if given (must exist and be writable), else in
        <session>\\marks\\. Every mark is listed in <session>\\marks.csv. Raises MarkError."""
        seconds = max(1.0, min(float(seconds), MAX_MARK_SECONDS))
        session = Path(session)
        if folder is not None:
            target = Path(folder)
            if not target.is_absolute():
                raise MarkError(f"Folder must be a full path: {folder}")
            if not target.is_dir():
                raise MarkError(f"Folder does not exist: {folder}")
        else:
            target = session / "marks"
        with self._lock:
            number = self._count + 1
            path = target / f"{number:02d}_{safe_label(label)}.csv"
            try:
                target.mkdir(parents=True, exist_ok=True)
                fh = open(path, "w", newline="", encoding="utf-8")
            except OSError as exc:
                raise MarkError(f"Cannot write in {target}: {exc.strerror or exc}") from None
            self._count = number
            writer = csv.writer(fh)
            writer.writerow(MARK_COLUMNS)
            self._active.append(_Mark(number, label, path, seconds, writer, fh, wall_start=time.monotonic()))
            self._write_summary(session / "marks.csv", number, label, path, snapshot)
        return {"mark": number, "label": label, "file": str(path), "seconds": seconds}

    def add_nav(self, row: dict):
        """One Group 1 epoch (dict with NAV_COLUMNS keys). Closes marks whose time is up."""
        with self._lock:
            light, message = self._light, self._message
            for m in self._active:
                if m.start_t is None:
                    m.start_t = row["time1_s"]
                elapsed = row["time1_s"] - m.start_t
                if elapsed < -1.0:                 # time went backwards (week rollover): restart
                    m.start_t, elapsed = row["time1_s"], 0.0
                if elapsed > m.seconds:
                    m.done = True
                    continue
                m.writer.writerow([_fmt(row[c]) for c in NAV_COLUMNS] + [light, message])
                m.rows += 1
                m.stats.add(row, light, message)
            finished = self._collect_done()
        self._report(finished)

    def tick(self, now: float | None = None):
        """Called about once a second: end marks by the PC clock if POS time has stalled
        (e.g. the POS stopped navigating), so a mark never runs much past its seconds."""
        now = time.monotonic() if now is None else now
        with self._lock:
            for m in self._active:
                if now - m.wall_start > m.seconds + WALL_GRACE_S:
                    m.done = True
            finished = self._collect_done()
        self._report(finished)

    def _collect_done(self) -> list[dict]:
        done = [m for m in self._active if m.done]
        for m in done:
            m.fh.close()
            self._active.remove(m)
        return [m.info(complete=True) for m in done]

    def _report(self, finished: list[dict]):
        for info in finished:
            self.on_done(info)

    def close_all(self) -> list[dict]:
        """Recording stopped: close open marks early. Returns their summaries (complete=False)."""
        with self._lock:
            closed = []
            for m in self._active:
                m.fh.close()
                closed.append(m.info(complete=False))
            self._active.clear()
        return closed

    @property
    def active(self) -> list[dict]:
        with self._lock:
            return [{"mark": m.number, "label": m.label, "rows": m.rows, "file": str(m.path)} for m in self._active]

    @staticmethod
    def _write_summary(path: Path, number: int, label: str, mark_file: Path, snap: dict):
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(SUMMARY_COLUMNS)
            values = {"mark": number, "label": label, "file": str(mark_file), "start_time1_s": snap.get("time1_s"),
                      **{k: snap.get(k) for k in SUMMARY_COLUMNS if k not in ("mark", "label", "file", "start_time1_s")}}
            w.writerow([_fmt(values[c]) for c in SUMMARY_COLUMNS])


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if v != v:                              # NaN
            return ""
        return f"{v:.9f}" if abs(v) < 1000 and v != int(v) else f"{v:.4f}"
    return str(v)
