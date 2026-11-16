"""Put post-processed positions into the mark CSVs (one per Surveyor image stop).

While recording, every mark CSV gets the POS real-time navigation (Group 1, about 50 Hz) and is
listed in <session>\\marks.csv. After post-processing, each row's latitude, longitude and height
are replaced by the post-processed values for the same POS time, and the RMS columns by the
post-processed accuracy. The original file is kept as <name>.csv.old (re-runs read from it).
Roll, pitch and heading stay real-time: raw IMU re-processing is not possible.
Each updated CSV gets a small report next to it: <name>_PPK_report.html.
"""
from __future__ import annotations

import csv
import datetime as dt
import html
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .marks import MARK_COLUMNS, _fmt

MARKS_LIST = "marks.csv"
OLD_SUFFIX = ".old"
REPORT_SUFFIX = "_PPK_report.html"
TIME_MATCH_S = 0.002          # mark rows are Group 1 records, so times match to the logged precision
FIXED_GOOD_PCT = 90.0         # a stop counts as "fixed" when this much of it is fixed
H95_TO_SIGMA = 2.45           # 2-D 95% -> 1-sigma per axis
V95_TO_SIGMA = 1.96
POSITION_COLUMNS = ("lat_deg", "lon_deg", "height_m")
RMS_COLUMNS = ("rms_n_m", "rms_e_m", "rms_d_m")


@dataclass
class MarkUpdate:
    label: str
    path: Path                        # the mark CSV (rewritten in place)
    old_path: Path | None = None      # the original real-time file
    report: Path | None = None
    rows: int = 0
    updated: int = 0
    session: str = ""
    start: dt.datetime | None = None
    seconds: float = 0.0
    quality: str = ""                 # "fixed", "float", "not processed"
    fixed_pct: float = 0.0
    shift_neu: tuple = (float("nan"),) * 3      # mean post-processed minus real-time (m)
    accuracy: dict = field(default_factory=dict)   # session realistic 95% accuracy
    stations: str = ""
    warnings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.updated > 0 and not self.warnings


def find_marks(session_dir: Path) -> list[tuple[str, Path]]:
    """(label, CSV path) for every mark listed in <session>\\marks.csv. If the run folder was
    copied or moved, the files are found by their place relative to the session folder."""
    lst = Path(session_dir) / MARKS_LIST
    if not lst.exists():
        return []
    out = []
    with open(lst, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            p = _locate(Path(row.get("file", "")), Path(session_dir))
            out.append((row.get("label", ""), p))
    return out


def _locate(p: Path, session_dir: Path) -> Path:
    """Look next to the session folder first, so a copied run updates the copy and never the
    original it was copied from; the stored full path is the last resort."""
    found = lambda c: c.exists() or Path(str(c) + OLD_SUFFIX).exists()
    parts = p.parts[1:]                                 # drop the drive / root
    for base in (session_dir.parent, session_dir):
        for k in range(len(parts)):
            cand = base.joinpath(*parts[k:])
            if found(cand):
                return cand
    return p                                            # original place (or reported as missing)


def update_marks(session_dirs, sessions, log=print) -> list[MarkUpdate]:
    """Update every mark CSV listed in the given session folders. `sessions` are pipeline
    SessionResults (with .fusion, .cross_check, .pcs)."""
    out = []
    for d in sorted({Path(x) for x in session_dirs}):
        for label, path in find_marks(d):
            try:
                mu = _update_one(label, path, sessions)
            except Exception as exc:                    # one bad file must not stop the others
                mu = MarkUpdate(label, path, quality="not processed", warnings=[f"Failed: {exc}"])
            log(f"Mark {label}: {mu.updated}/{mu.rows} rows post-processed, {mu.quality}"
                + ("" if not mu.warnings else " | " + "; ".join(mu.warnings)))
            if mu.updated:
                mu.report = write_mark_report(mu)
            out.append(mu)
    return out


def _update_one(label: str, path: Path, sessions) -> MarkUpdate:
    old = Path(str(path) + OLD_SUFFIX)
    src = old if old.exists() else path
    mu = MarkUpdate(label, path)
    if not src.exists():
        mu.quality, mu.warnings = "not processed", [f"File not found: {path}"]
        return mu
    with open(src, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or MARK_COLUMNS
        rows = list(reader)
    mu.rows = len(rows)
    if not rows:
        mu.quality, mu.warnings = "not processed", ["The mark CSV has no rows."]
        return mu

    sr = _session_for(rows, sessions)
    if sr is None:
        mu.quality, mu.warnings = "not processed", ["No post-processed session covers this mark's time."]
        return mu
    f, pcs = sr.fusion, sr.pcs
    t = np.array([_gps_t(r, pcs) for r in rows])
    i = np.clip(np.searchsorted(f.t, t), 1, len(f.t) - 1)
    i = np.where(np.abs(f.t[i - 1] - t) < np.abs(f.t[i] - t), i - 1, i)
    hit = (np.abs(f.t[i] - t) <= TIME_MATCH_S) & f.covered[i]

    acc = sr.cross_check.accuracy if sr.cross_check else {}
    floor = (acc["horizontal"] / H95_TO_SIGMA, acc["horizontal"] / H95_TO_SIGMA,
             acc["vertical"] / V95_TO_SIGMA) if acc else (0.0, 0.0, 0.0)
    dn, de, du = [], [], []
    for r, k, ok in zip(rows, i, hit):
        if not ok:
            continue
        rt = [float(r[c]) for c in POSITION_COLUMNS]
        new = (f.lat[k], f.lon[k], f.h[k])
        dn.append(np.radians(new[0] - rt[0]) * 6378137.0)
        de.append(np.radians(new[1] - rt[1]) * 6378137.0 * np.cos(np.radians(rt[0])))
        du.append(new[2] - rt[2])
        for c, v in zip(POSITION_COLUMNS, new):
            r[c] = _fmt(float(v))
        if all(c in r for c in RMS_COLUMNS):
            for c, s, fl in zip(RMS_COLUMNS, f.sigma[k], floor):
                r[c] = _fmt(float(max(s, fl)))
    mu.updated = int(hit.sum())
    mu.session, mu.accuracy = sr.name, dict(acc)
    mu.stations = ", ".join(sr.cross_check.per_base) if sr.cross_check else sr.station
    mu.seconds = float(np.nanmax(t) - np.nanmin(t))
    from .geo import gps_to_datetime
    mu.start = gps_to_datetime(pcs.gps_week, float(np.nanmin(t)))
    if mu.updated:
        mu.shift_neu = (float(np.mean(dn)), float(np.mean(de)), float(np.mean(du)))
    mu.fixed_pct = _fixed_pct(sr, np.nanmin(t), np.nanmax(t))
    mu.quality = "fixed" if mu.fixed_pct >= FIXED_GOOD_PCT else "float"
    if mu.updated < mu.rows:
        mu.warnings.append(f"{mu.rows - mu.updated} rows outside the post-processed data kept real-time values.")
    if mu.quality == "float":
        mu.warnings.append(f"Only {mu.fixed_pct:.0f}% fixed during this stop: float accuracy "
                           + (f"(about {acc['horizontal'] * 100:.0f} cm)." if acc else "(decimetres)."))
    if not mu.updated:
        return mu

    if not old.exists():
        path.replace(old)                               # keep the original once, never overwrite it
    mu.old_path = old
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)
    return mu


def _gps_t(row: dict, pcs) -> float:
    try:
        t = float(row["time1_s"])
    except (KeyError, TypeError, ValueError):
        return float("nan")
    if row.get("time_type", "").upper() == "UTC":
        t += pcs.gps_utc_offset
    return t


def _session_for(rows, sessions):
    for sr in sessions:
        if sr.fusion is None or not len(sr.fusion.t):
            continue
        t = np.array([_gps_t(r, sr.pcs) for r in rows[:: max(1, len(rows) // 20)]])
        if np.any((t >= sr.fusion.t[0]) & (t <= sr.fusion.t[-1])):
            return sr
    return None


def _fixed_pct(sr, t0: float, t1: float) -> float:
    if sr.cross_check is None or not len(sr.cross_check.pos):
        return 0.0
    from .fuse import ppk_times_on_session_week
    p = sr.cross_check.pos
    tp = ppk_times_on_session_week(p, sr.pcs.gps_week)
    m = (tp >= t0) & (tp <= t1)
    return float((p["q"][m] == 1).mean() * 100) if m.any() else 0.0


def write_mark_report(mu: MarkUpdate) -> Path:
    e = html.escape
    acc = mu.accuracy
    n, ea, u = mu.shift_neu
    status = "ok" if mu.quality == "fixed" else "warn"
    rows = [
        ("Mark", mu.label), ("File", str(mu.path)), ("Original (real-time)", str(mu.old_path or "")),
        ("Start", f"{mu.start:%Y-%m-%d %H:%M:%S} GPS" if mu.start else ""), ("Length", f"{mu.seconds:.0f} s"),
        ("Rows post-processed", f"{mu.updated} of {mu.rows}"), ("Session", mu.session),
        ("Base stations", mu.stations),
        ("Fixed during this stop", f"{mu.fixed_pct:.0f}% ({mu.quality})"),
        ("Realistic accuracy (95%)", f"{acc['horizontal'] * 100:.0f} cm horizontal, {acc['vertical'] * 100:.0f} cm "
                                     f"vertical (whole session)" if acc else "unknown"),
        ("Mean change from real-time", f"N {n:+.3f} m, E {ea:+.3f} m, Up {u:+.3f} m"),
    ]
    body = "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in rows)
    warn = "".join(f"<p class='warn'>&#9888; {e(w)}</p>" for w in mu.warnings)
    page = (f"<!doctype html><html><head><meta charset='utf-8'><title>PPK {e(mu.label)}</title><style>"
            "body{font-family:Segoe UI,Arial;background:#2c3e50;color:#ecf0f1;margin:20px}"
            "th{text-align:left;padding:4px 12px 4px 0;color:#bdc3c7;font-weight:normal}td{padding:4px}"
            ".ok{color:#27ae60}.warn{color:#f39c12}.note{color:#bdc3c7;font-size:13px}</style></head><body>"
            f"<h2>Post-processed mark: {e(mu.label)} <span class='{status}'>({e(mu.quality)})</span></h2>"
            f"{warn}<table>{body}</table><p class='note'>Latitude, longitude and height (ellipsoidal, POS "
            "reference point) are post-processed; RMS columns hold the post-processed accuracy (1-sigma, "
            "never below the realistic base-to-base value). Roll, pitch, heading, speed and the light "
            "columns are the real-time values.</p></body></html>")
    out = mu.path.with_name(mu.path.stem + REPORT_SUFFIX)
    out.write_text(page, encoding="utf-8")
    return out
