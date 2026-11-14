"""Write post-processed versions of ARAN real-time CSV GPS logs.

The CSV rows hold values copied from the real-time POS solution (lat, lon,
elevation, roll, pitch, heading) but no time column. We find each row's time by
matching lat/lon/height/heading against Group 1, then replace
lat/lon/elevation with the post-processed values at that time. Every other
column is written back unchanged.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from .fuse import FusionResult
from .geo import FT_PER_M, ned_offset

ATT_WEIGHT_M_PER_DEG = 1.0   # 0.001 deg of heading counts like 1 mm of position
# Only heading is used: in the ARAN CSV logs heading equals the POS heading exactly,
# but roll and pitch do not (they are in another frame), so they are ignored.
CANDIDATES = 64
LEFT_SPOT_M = 1.0          # truck counts as having left a spot after moving this far


@dataclass
class CsvMatch:
    path: Path
    out_path: Path | None = None
    rows: int = 0
    matched: int = 0
    units: str = ""
    median_resid_m: float = float("nan")
    max_resid_m: float = float("nan")
    t_start: float = float("nan")
    t_end: float = float("nan")
    mean_corr_neu_m: tuple[float, float, float] = (float("nan"),) * 3
    points: list[tuple[float, float]] = field(default_factory=list)   # corrected lat/lon for the map
    warnings: list[str] = field(default_factory=list)
    ok: bool = False


def _col(header: list[str], name: str) -> int:
    for i, h in enumerate(header):
        if h.strip().lower() == name.lower():
            return i
    raise ValueError(f"Column '{name}' not found")


def _num(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return float("nan")


class CsvMatcher:
    def __init__(self, g1: np.ndarray, fusion: FusionResult):
        self.g1, self.f = g1, fusion
        self.lat0, self.lon0, self.h0 = float(g1["lat"][0]), float(g1["lon"][0]), float(g1["h"][0])
        n, e, d = ned_offset(g1["lat"], g1["lon"], g1["h"], self.lat0, self.lon0, self.h0)
        self.feat_h = np.column_stack([n, e, g1["heading"] * ATT_WEIGHT_M_PER_DEG])
        self.tree_h = cKDTree(self.feat_h)
        self.u = -d

    def process(self, path: Path, out_dir: Path | None, suffix: str, units_setting: str,
                tol_m: float, gap_s: float) -> CsvMatch:
        res = CsvMatch(Path(path))
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        if len(rows) < 2:
            res.warnings.append("File has no data rows.")
            return res
        header, data = rows[0], rows[1:]
        ci = {k: _col(header, k) for k in ("Latitude", "Longitude", "Elevation", "Heading")}
        vals = {k: np.array([_num(r[i]) if i < len(r) else np.nan for r in data]) for k, i in ci.items()}
        valid = np.isfinite(vals["Latitude"]) & np.isfinite(vals["Longitude"]) & np.isfinite(vals["Elevation"])
        res.rows = int(valid.sum())
        if not res.rows:
            res.warnings.append("No rows with position.")
            return res

        n, e, _ = ned_offset(vals["Latitude"], vals["Longitude"], 0.0, self.lat0, self.lon0, 0.0)
        q = np.column_stack([n, e, vals["Heading"] * ATT_WEIGHT_M_PER_DEG])
        q = np.nan_to_num(q)
        k = min(CANDIDATES, len(self.feat_h))
        dist, idx = self.tree_h.query(q[valid], k=k)
        dist, idx = np.atleast_2d(dist), np.atleast_2d(idx)

        # Units: compare row elevation with the real-time height of the best horizontal match.
        elev = vals["Elevation"][valid]
        h_best = self.g1["h"][idx[:, 0]]
        units = units_setting
        if units == "auto":
            units = "ft" if np.median(np.abs(elev / FT_PER_M - h_best)) < np.median(np.abs(elev - h_best)) else "m"
        res.units = units
        elev_m = elev / FT_PER_M if units == "ft" else elev

        # Pick, among the candidates, the epoch that also matches the height.
        dh = self.g1["h"][idx] - elev_m[:, None]
        total = np.sqrt(dist ** 2 + dh ** 2)
        best = np.argmin(total, axis=1)
        bi = idx[np.arange(len(idx)), best]
        resid = total[np.arange(len(idx)), best]
        t_match = self.g1["t"][bi]

        res.median_resid_m = float(np.median(resid))
        res.max_resid_m = float(np.max(resid))
        good = resid <= tol_m
        res.matched = int(good.sum())
        if res.matched == 0:
            res.warnings.append(f"No POS data matches this log (best match {res.median_resid_m:.2f} m). "
                                "It may be from another day or session; no output written.")
            return res
        if res.matched < res.rows:
            res.warnings.append(f"{res.rows - res.matched} rows match the POS data worse than {tol_m * 100:.0f} cm.")
        ts = np.sort(t_match[good]) if good.any() else np.sort(t_match)
        res.t_start, res.t_end = float(ts[0]), float(ts[-1])
        if len(ts) > 1 and np.max(np.diff(ts)) > gap_s:
            res.warnings.append(f"Rows match POS times more than {gap_s:.0f} s apart - the truck may have "
                                "been at this spot more than once. Check this file.")
        alt = self._other_visits(q[valid], elev_m, t_match, tol_m, gap_s)
        if alt:
            res.warnings.append(f"Similar position also seen at another time ({alt}). Check this file.")
        if not self.f.covered[bi].all():
            res.warnings.append("Some rows fall outside the PPK coverage; corrections there are held, not measured.")

        lat = np.interp(t_match, self.f.t, self.f.lat)
        lon = np.interp(t_match, self.f.t, self.f.lon)
        hh = np.interp(t_match, self.f.t, self.f.h)
        cn = np.interp(t_match, self.f.t, self.f.correction[:, 0])
        ce = np.interp(t_match, self.f.t, self.f.correction[:, 1])
        cd = np.interp(t_match, self.f.t, self.f.correction[:, 2])
        res.mean_corr_neu_m = (float(np.mean(cn)), float(np.mean(ce)), float(-np.mean(cd)))
        out_elev = hh * FT_PER_M if units == "ft" else hh
        res.points = list(zip(lat.tolist(), lon.tolist()))

        rows_idx = np.flatnonzero(valid)
        for j, r in enumerate(rows_idx):
            row = data[r]
            row[ci["Latitude"]] = f"{lat[j]:.9f}"
            row[ci["Longitude"]] = f"{lon[j]:.9f}"
            row[ci["Elevation"]] = f"{out_elev[j]:.6f}"

        folder = Path(out_dir) if out_dir else res.path.parent
        folder.mkdir(parents=True, exist_ok=True)
        res.out_path = folder / f"{res.path.stem}{suffix}{res.path.suffix}"
        if res.out_path.resolve() == res.path.resolve():
            raise ValueError("Output file would overwrite the input; change the suffix.")
        with open(res.out_path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows([header] + data)
        res.ok = res.matched == res.rows and not res.warnings
        return res

    def _other_visits(self, q, elev_m, t_match, tol_m, gap_s) -> str:
        """Look for a separate visit: the same spot again after the truck drove away (>1 m)."""
        mid = len(q) // 2
        hits = np.array(self.tree_h.query_ball_point(q[mid], r=max(tol_m, 0.02)), dtype=int)
        if not len(hits):
            return ""
        hits = np.sort(hits[np.abs(self.g1["h"][hits] - elev_m[mid]) <= max(tol_m, 0.02)])
        t0 = np.median(t_match)
        hz = np.hypot(self.feat_h[:, 0] - q[mid, 0], self.feat_h[:, 1] - q[mid, 1])
        own = hits[np.argmin(np.abs(self.g1["t"][hits] - t0))]
        away = np.concatenate([[0], np.cumsum(hz > LEFT_SPOT_M)])
        lo, hi = np.minimum(hits, own), np.maximum(hits, own)
        left = (away[hi + 1] - away[lo]) > 0
        if left.any():
            return f"GPS time {self.g1['t'][hits[left][0]]:.0f} s"
        return ""
