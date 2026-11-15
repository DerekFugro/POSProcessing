"""Marks: when another app (Surveyor) stops to take images, record the POS navigation for a
few minutes into a CSV, and keep one summary line per mark in marks.csv.

Fed from the logger thread with every Group 1 record (about 50 Hz on the ARAN PCS). Values are
the POS real-time solution (POS reference point), so they are for tying images to time; the
accurate positions come later from post-processing the .000 files.
"""
from __future__ import annotations

import csv
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MARK_SECONDS = 300.0
MAX_MARK_SECONDS = 3600.0
TIME_TYPES = {0: "POS", 1: "GPS", 2: "UTC"}       # POS time-types byte, low nibble = Time 1
NAV_COLUMNS = ["time1_s", "time_type", "lat_deg", "lon_deg", "height_m", "roll_deg", "pitch_deg",
               "heading_deg", "speed_mps", "align_status", "rms_n_m", "rms_e_m", "rms_d_m"]
SUMMARY_COLUMNS = ["mark", "label", "file", "start_time1_s", "time_type", "lat_deg", "lon_deg",
                   "height_m", "roll_deg", "pitch_deg", "heading_deg", "speed_mps", "rms_n_m",
                   "rms_e_m", "rms_d_m", "light", "message"]
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_label(label: str) -> str:
    """Label usable in a file name: letters, digits, _ . - (others become _)."""
    return _UNSAFE.sub("_", label.strip())[:60].strip("_") or "mark"


@dataclass
class _Mark:
    number: int
    label: str
    path: Path
    seconds: float
    writer: object = None
    fh: object = None
    start_t: float | None = None
    rows: int = 0
    done: bool = False
    extra: dict = field(default_factory=dict)


class MarkRecorder:
    """Thread-safe: start() from the GUI thread, add_nav() from the logger thread."""

    def __init__(self, on_done=None):
        self._lock = threading.Lock()
        self._active: list[_Mark] = []
        self._count = 0
        self.on_done = on_done or (lambda info: None)

    def start(self, folder: Path, label: str, seconds: float, snapshot: dict) -> dict:
        """Begin a mark CSV in <folder>\\marks\\. Returns what was started (for the reply)."""
        seconds = max(1.0, min(float(seconds), MAX_MARK_SECONDS))
        folder = Path(folder)
        with self._lock:
            self._count += 1
            number = self._count
            marks_dir = folder / "marks"
            marks_dir.mkdir(parents=True, exist_ok=True)
            path = marks_dir / f"{number:02d}_{safe_label(label)}.csv"
            fh = open(path, "w", newline="", encoding="utf-8")
            writer = csv.writer(fh)
            writer.writerow(NAV_COLUMNS)
            self._active.append(_Mark(number, label, path, seconds, writer, fh))
            self._write_summary(folder / "marks.csv", number, label, path, snapshot)
        return {"mark": number, "label": label, "file": str(path), "seconds": seconds}

    def add_nav(self, row: dict):
        """One Group 1 epoch (dict with NAV_COLUMNS keys). Closes marks whose time is up."""
        finished = []
        with self._lock:
            for m in self._active:
                if m.start_t is None:
                    m.start_t = row["time1_s"]
                elapsed = row["time1_s"] - m.start_t
                if elapsed < -1.0:                 # time went backwards (week rollover): restart
                    m.start_t, elapsed = row["time1_s"], 0.0
                if elapsed > m.seconds:
                    m.done = True
                    continue
                m.writer.writerow([_fmt(row[c]) for c in NAV_COLUMNS])
                m.rows += 1
            for m in [m for m in self._active if m.done]:
                m.fh.close()
                self._active.remove(m)
                finished.append({"mark": m.number, "label": m.label, "file": str(m.path), "rows": m.rows})
        for info in finished:
            self.on_done(info)

    def close_all(self) -> list[dict]:
        """Recording stopped: close open marks early. Returns what was closed."""
        closed = []
        with self._lock:
            for m in self._active:
                m.fh.close()
                closed.append({"mark": m.number, "label": m.label, "file": str(m.path), "rows": m.rows,
                               "complete": False})
            self._active.clear()
        return closed

    @property
    def active(self) -> list[dict]:
        with self._lock:
            return [{"mark": m.number, "label": m.label, "rows": m.rows} for m in self._active]

    @staticmethod
    def _write_summary(path: Path, number: int, label: str, mark_file: Path, snap: dict):
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(SUMMARY_COLUMNS)
            values = {"mark": number, "label": label, "file": mark_file.name, "start_time1_s": snap.get("time1_s"),
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
