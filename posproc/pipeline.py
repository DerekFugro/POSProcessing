"""End-to-end processing: PCS files -> RINEX -> CORS PPK -> fused trajectory -> CSV logs + report."""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import convert, cors, ppk, rt27
from .config import Settings
from .csvlog import CsvMatch, CsvMatcher
from .fuse import FusionResult, fuse
from .geo import gps_to_datetime, gps_to_decimal_year
from .pcs import PcsData, group_sessions, read_pcs

FRAME_NOTE = ("ITRF2020 at the epoch of the data (NOAA CORS coordinates moved to the survey date "
              "with their published velocities). WGS84 (G2296) agrees with ITRF2020 at the "
              "centimetre level, so this is reported as WGS84.")


class Cancelled(Exception):
    pass


@dataclass
class SessionResult:
    name: str
    pcs: PcsData
    start: dt.datetime | None = None
    end: dt.datetime | None = None
    packets: str = ""
    station: str = ""
    baseline_km: float = float("nan")
    base_latlon: tuple[float, float] | None = None
    base_interval_s: float = float("nan")
    quality: dict = field(default_factory=dict)
    fusion: FusionResult | None = None
    trajectory_csv: Path | None = None
    rms_before: dict = field(default_factory=dict)
    rms_after: dict = field(default_factory=dict)
    correction_stats: dict = field(default_factory=dict)
    error: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    out_dir: Path
    sessions: list[SessionResult] = field(default_factory=list)
    csv: list[CsvMatch] = field(default_factory=list)
    report_html: Path | None = None
    frame: str = FRAME_NOTE


class Pipeline:
    def __init__(self, settings: Settings, log=print, progress=None, cancelled=lambda: False):
        self.s, self.log, self.progress, self.cancelled = settings, log, progress or (lambda *_: None), cancelled

    def _check(self):
        if self.cancelled():
            raise Cancelled()

    def run(self, pcs_files: list[str], csv_files: list[str], out_dir: Path,
            trajectory_rate_hz: float = 10.0) -> RunResult:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        result = RunResult(out_dir)
        sessions = group_sessions(pcs_files)
        for i, (name, files) in enumerate(sessions.items()):
            self.log(f"=== Session {name} ({len(files)} files) ===")
            sr = self._run_session(name, files, out_dir, trajectory_rate_hz, i, len(sessions))
            result.sessions.append(sr)
        good = [s for s in result.sessions if s.fusion is not None]
        if csv_files:
            if good:
                result.csv = self._run_csv(good, csv_files, out_dir)
            else:
                self.log("CSV logs skipped: no session was post-processed.")
        from .report import write_report
        result.report_html = write_report(result, out_dir / "PPK_Report.html")
        self.log(f"Report: {result.report_html}")
        return result

    # ------------------------------------------------------------------
    def _run_session(self, name, files, out_dir, rate_hz, i, n) -> SessionResult:
        step = lambda k, msg: self.progress((i + k / 7) / n, msg)
        work = out_dir / name
        work.mkdir(parents=True, exist_ok=True)
        step(0, f"{name}: reading POS files")
        pcs = read_pcs(files, progress=lambda a, b, f: self.progress((i + a / b / 7) / n, f"Reading {f}"))
        sr = SessionResult(name, pcs)
        try:
            self._session_core(sr, work, rate_hz, step)
        except Cancelled:
            raise
        except Exception as exc:                     # keep going with other sessions
            sr.error = str(exc)
            self.log(f"ERROR in {name}: {exc}")
        return sr

    def _session_core(self, sr: SessionResult, work: Path, rate_hz: float, step):
        pcs, s = sr.pcs, self.s
        if len(pcs.g1) < 2:
            raise ValueError("No Group 1 navigation data in these files.")
        week = pcs.gps_week
        if not week:
            raise ValueError("No GPS week found (Group 3).")
        if not pcs.lever.found:
            sr.warnings.append("Message 20 (lever arms) not found; reference point used as antenna.")
        t0, t1 = float(pcs.g1["t"][0]), float(pcs.g1["t"][-1])
        sr.start, sr.end = gps_to_datetime(week, t0), gps_to_datetime(week, t1)
        sr.rms_before = _rms_g2(pcs.g2, pcs.g1)
        self.log(f"POS: {pcs.version.split('|')[0].strip()}")
        self.log(pcs.time_note)
        self.log(f"Data {sr.start:%Y-%m-%d %H:%M:%S} to {sr.end:%H:%M:%S} GPS, {len(pcs.g1)} nav epochs")
        self.log(f"Lever arm ref->GNSS (X fwd, Y right, Z down): {pcs.lever.ref_to_gnss1}")
        self._check()

        step(1, f"{sr.name}: extracting raw GNSS")
        counts = rt27.scan_packets(bytes(pcs.gnss_stream))
        sr.packets = rt27.describe(counts)
        self.log(f"Receiver packets: {sr.packets}")
        if rt27.rt27_epochs(counts) == 0:
            raise ValueError("No RT27 raw observations in Group 10001 - raw GNSS was not logged.")
        stream = rt27.write_stream(pcs.gnss_stream, work / f"{sr.name}{s.rt27_extension}")
        self._check()

        step(2, f"{sr.name}: converting to RINEX")
        rover_obs, rover_nav = convert.convert_rt27(s.convert_to_rinex_exe, stream, work / "rinex",
                                                    s.rinex_version, self.log)
        self.log(f"Rover RINEX: {rover_obs.name}")
        self._check()

        step(3, f"{sr.name}: getting base station data")
        days = cors.days_covered(sr.start - dt.timedelta(minutes=1), sr.end + dt.timedelta(minutes=1))
        lat0, lon0 = float(np.median(pcs.g1["lat"])), float(np.median(pcs.g1["lon"]))
        bases = self._bases(lat0, lon0, days, gps_to_decimal_year(week, (t0 + t1) / 2))
        self._check()

        step(4, f"{sr.name}: PPK processing")
        pos = None
        for base in bases:
            self.log(f"Trying base {base.station.site} ({base.station.dist_km:.1f} km, "
                     f"{base.interval_s:g} s data)")
            conf = ppk.write_config(work / "rtklib.conf", base.xyz, s.elevation_mask_deg, s.navsys, s.ar_mode)
            out_pos = work / f"{sr.name}_{base.station.site}.pos"
            try:
                ppk.run_rnx2rtkp(s.rnx2rtkp_exe, conf, rover_obs, base.obs_files,
                                 rover_nav + base.nav_files, out_pos, week, t0, t1, self.log)
                cand = ppk.read_pos(out_pos)
            except ppk.PpkError as exc:
                self.log(str(exc))
                continue
            q = ppk.quality_summary(cand)
            self.log("PPK quality: " + ", ".join(f"{k} {v:.0f}%" for k, v in q.items()))
            usable = q.get("fix", 0) + (q.get("float", 0) if s.use_float else 0)
            if usable >= 20 or base is bases[-1]:
                pos, sr.quality = cand, q
                sr.station, sr.baseline_km, sr.base_interval_s = base.station.site, base.station.dist_km, base.interval_s
                if base.station.lat or base.station.lon:
                    sr.base_latlon = (base.station.lat, base.station.lon)
                break
        if pos is None:
            raise ValueError("PPK failed with every base station tried.")
        if sr.baseline_km > 30 and sr.quality.get("fix", 0) < 50:
            sr.warnings.append(f"Base station is {sr.baseline_km:.0f} km away; only "
                               f"{sr.quality.get('fix', 0):.0f}% fixed; expect decimetre (float) accuracy.")
        if sr.baseline_km > 30 and sr.quality.get("fix", 0) >= 50:
            sr.warnings.append(f"Base station is {sr.baseline_km:.0f} km away. Fixes at this distance can be "
                               "wrong; check the result against a known point (e.g. the site GCPs).")
        if sr.base_interval_s > 5:
            sr.warnings.append(f"Base data is {sr.base_interval_s:g}-second (NOAA thins data older than "
                               "30 days); fine when parked, weaker while driving.")
        self._check()

        step(5, f"{sr.name}: blending with real-time trajectory")
        f = fuse(pcs.g1, pcs.lever.ref_to_gnss1, pos, week, q_m_per_sqrt_s=s.process_noise_m_per_sqrt_s,
                 min_sigma=s.ppk_min_sigma_m, use_float=s.use_float, outlier_sigma=s.outlier_sigma)
        sr.fusion = f
        self.log(f"PPK fixes used {f.ppk_used}, rejected as outliers {f.ppk_rejected}")
        sr.rms_after = _rms_after(f)
        sr.correction_stats = _corr_stats(f)
        if (~f.covered).mean() > 0.01:
            sr.warnings.append(f"{(~f.covered).mean() * 100:.0f}% of the trajectory is outside PPK coverage "
                               "(correction held from the nearest fix).")

        step(6, f"{sr.name}: writing trajectory")
        sr.trajectory_csv = _write_trajectory(work / f"{sr.name}_PPK_trajectory_WGS84.csv", pcs, f, week, rate_hz)
        self.log(f"Trajectory: {sr.trajectory_csv}")

    def _bases(self, lat, lon, days, year_frac) -> list[cors.BaseData]:
        s = self.s
        if s.manual_base_rinex:
            if len(s.manual_base_xyz) != 3:
                raise ValueError("Manual base needs manual_base_xyz (ITRF2020 ECEF X, Y, Z of the ARP).")
            st = cors.Station("USER", tuple(s.manual_base_xyz), (0, 0, 0), "manual")
            from .geo import geodetic_to_ecef
            st.dist_km = math.dist(geodetic_to_ecef(lat, lon, 0.0), st.xyz2020) / 1000
            files = [Path(p) for p in s.manual_base_rinex]
            return [cors.BaseData(st, files, [], st.xyz2020, cors.rinex_interval(files[0]))]
        client = cors.CorsClient(s.cors_bucket, s.cache_dir, self.log)
        near = client.nearest(lat, lon, days, s.max_baseline_km)
        if not near:
            raise ValueError(f"No NOAA CORS station with data within {s.max_baseline_km:g} km.")
        out = []
        for st in near[: s.stations_to_try]:
            try:
                out.append(client.download(st, days, year_frac))
                self.log(f"Downloaded {st.site} ({st.dist_km:.1f} km)")
            except Exception as exc:
                self.log(f"Skip {st.site}: {exc}")
        if not out:
            raise ValueError("Could not download any base station data.")
        return out

    def _run_csv(self, sessions: list[SessionResult], csv_files, out_dir) -> list[CsvMatch]:
        g1 = np.concatenate([s.pcs.g1 for s in sessions])
        order = np.argsort(g1["t"], kind="stable")
        fus = _concat_fusion([s.fusion for s in sessions])
        matcher = CsvMatcher(g1[order], fus)
        out = []
        for k, c in enumerate(csv_files):
            self._check()
            self.progress(0.95 + 0.05 * k / len(csv_files), f"CSV {Path(c).name}")
            try:
                r = matcher.process(Path(c), out_dir / "csv", self.s.csv_suffix, self.s.csv_elev_units,
                                    self.s.csv_match_tolerance_m, self.s.csv_ambiguity_gap_s)
            except Exception as exc:
                r = CsvMatch(Path(c), warnings=[f"Failed: {exc}"])
            self.log(f"CSV {Path(c).name}: {r.matched}/{r.rows} rows matched"
                     + (f" -> {r.out_path.name}" if r.out_path else "")
                     + ("" if not r.warnings else " | " + "; ".join(r.warnings)))
            out.append(r)
        return out


def _concat_fusion(fs: list[FusionResult]) -> FusionResult:
    if len(fs) == 1:
        return fs[0]
    cat = lambda name: np.concatenate([getattr(f, name) for f in fs])
    t = cat("t")
    o = np.argsort(t, kind="stable")
    return FusionResult(t[o], cat("lat")[o], cat("lon")[o], cat("h")[o], cat("sigma")[o], cat("correction")[o],
                        cat("covered")[o], cat("ppk_t"), cat("ppk_resid"),
                        sum(f.ppk_used for f in fs), sum(f.ppk_rejected for f in fs))


def _rms(x) -> float:
    x = np.asarray(x, float)
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else float("nan")


def _med(x) -> float:
    x = np.asarray(x, float)
    return float(np.median(x)) if len(x) else float("nan")


def _rms_g2(g2, g1) -> dict:
    """Typical (median) real-time RMS while the POS was fully aligned (Group 1 status 0)."""
    if not len(g2):
        return {}
    full = g1["t"][g1["align"] == 0]
    if len(full):
        g2 = g2[(g2["t"] >= full[0]) & (g2["t"] <= full[-1])]
    return {"north": _med(g2["n"]), "east": _med(g2["e"]), "down": _med(g2["d"]),
            "roll": _med(g2["roll"]), "pitch": _med(g2["pitch"]), "heading": _med(g2["heading"])}


def _rms_after(f: FusionResult) -> dict:
    c = f.covered if f.covered.any() else np.ones(len(f.t), bool)
    return {"north": _med(f.sigma[c, 0]), "east": _med(f.sigma[c, 1]), "down": _med(f.sigma[c, 2]),
            "ppk_resid_north": _rms(f.ppk_resid[:, 0]), "ppk_resid_east": _rms(f.ppk_resid[:, 1]),
            "ppk_resid_down": _rms(f.ppk_resid[:, 2])}


def _corr_stats(f: FusionResult) -> dict:
    c = f.correction
    hz = np.hypot(c[:, 0], c[:, 1])
    return {"mean_north": float(c[:, 0].mean()), "mean_east": float(c[:, 1].mean()),
            "mean_up": float(-c[:, 2].mean()), "rms_horizontal": _rms(hz), "max_horizontal": float(hz.max()),
            "rms_vertical": _rms(c[:, 2]), "max_vertical": float(np.abs(c[:, 2]).max())}


def _write_trajectory(path: Path, pcs: PcsData, f: FusionResult, week: int, rate_hz: float) -> Path:
    g1 = pcs.g1
    if rate_hz and rate_hz > 0:
        keep = np.ones(len(f.t), bool)
        bins = np.floor(f.t * rate_hz + 1e-6)
        keep[1:] = np.diff(bins) > 0
    else:
        keep = np.ones(len(f.t), bool)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["GPSWeek", "GPSSeconds", "Latitude_deg", "Longitude_deg", "EllipsoidHeight_m",
                    "Roll_deg", "Pitch_deg", "Heading_deg", "SigmaNorth_m", "SigmaEast_m", "SigmaDown_m",
                    "CorrNorth_m", "CorrEast_m", "CorrUp_m", "PPKCovered"])
        for k in np.flatnonzero(keep):
            w.writerow([week, f"{f.t[k]:.4f}", f"{f.lat[k]:.10f}", f"{f.lon[k]:.10f}", f"{f.h[k]:.4f}",
                        f"{g1['roll'][k]:.5f}", f"{g1['pitch'][k]:.5f}", f"{g1['heading'][k]:.5f}",
                        f"{f.sigma[k, 0]:.4f}", f"{f.sigma[k, 1]:.4f}", f"{f.sigma[k, 2]:.4f}",
                        f"{f.correction[k, 0]:.4f}", f"{f.correction[k, 1]:.4f}", f"{-f.correction[k, 2]:.4f}",
                        int(f.covered[k])])
    path.with_suffix(".json").write_text(json.dumps({"frame": FRAME_NOTE, "reference_point":
        "POS reference point (Group 1), same as the real-time data", "time": "GPS week + seconds of week"},
        indent=2), encoding="utf-8")
    return path
