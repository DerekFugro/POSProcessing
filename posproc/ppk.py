"""Run RTKLIB (rnx2rtkp) carrier-phase post-processing of the rover against a base."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .geo import gps_to_datetime

POS_DTYPE = np.dtype([
    ("week", "i4"), ("t", "f8"), ("lat", "f8"), ("lon", "f8"), ("h", "f8"),
    ("q", "i4"), ("ns", "i4"), ("sdn", "f8"), ("sde", "f8"), ("sdu", "f8"), ("ratio", "f8"),
])
Q_NAMES = {1: "fix", 2: "float", 3: "sbas", 4: "dgps", 5: "single", 6: "ppp"}


class PpkError(RuntimeError):
    pass


def write_config(path: Path, base_xyz, elmask: float, navsys: int, armode: str,
                 rover_ant: str = "", base_ant: str = "", antex: Path | None = None) -> Path:
    """rtklib.conf. With antex + antenna names, RTKLIB applies the antenna calibrations: the
    rover solution is at its ARP (bottom of the antenna) and the base phase centre is placed
    at its ARP (NOAA coordinates) + offset."""
    lines = {
        "pos1-posmode": "kinematic",
        "pos1-frequency": "l1+l2",
        "pos1-soltype": "combined",        # forward + backward, best for post-processing
        "pos1-elmask": f"{elmask:g}",
        "pos1-navsys": str(navsys),
        "pos1-ionoopt": "brdc",
        "pos1-tropopt": "saas",
        "pos1-sateph": "brdc",
        "pos2-armode": armode,
        "pos2-gloarmode": "off",
        "pos2-arthres": "3",
        "misc-timeinterp": "on",           # interpolate 30 s base data
        "out-solformat": "llh",
        "out-timesys": "gpst",
        "out-timeform": "tow",
        "out-height": "ellipsoidal",
        "out-outhead": "on",
        "ant1-antdele": "0", "ant1-antdeln": "0", "ant1-antdelu": "0",
        "ant2-postype": "xyz",
        "ant2-pos1": f"{base_xyz[0]:.4f}",
        "ant2-pos2": f"{base_xyz[1]:.4f}",
        "ant2-pos3": f"{base_xyz[2]:.4f}",
        "ant2-antdele": "0", "ant2-antdeln": "0", "ant2-antdelu": "0",
    }
    if antex is not None:
        lines["ant1-anttype"] = rover_ant
        lines["ant2-anttype"] = base_ant
        lines["file-rcvantfile"] = str(antex)   # RTKLIB-EX 2.5.1 name (file-rcvantp is silently ignored)
    path.write_text("\n".join(f"{k:<18}={v}" for k, v in lines.items()) + "\n", encoding="ascii")
    return path


def run_rnx2rtkp(exe: str, conf: Path, rover_obs: Path, base_obs: list[Path], nav: list[Path],
                 out_pos: Path, week: int, t0: float, t1: float, log=print) -> Path:
    if not Path(exe).exists():
        raise PpkError(f"RTKLIB rnx2rtkp not found at:\n  {exe}\nSet its path in Settings.")
    ts, te = gps_to_datetime(week, t0 - 30), gps_to_datetime(week, t1 + 30)
    cmd = [exe, "-k", str(conf), "-o", str(out_pos),
           "-ts", ts.strftime("%Y/%m/%d"), ts.strftime("%H:%M:%S"),
           "-te", te.strftime("%Y/%m/%d"), te.strftime("%H:%M:%S"),
           str(rover_obs), *map(str, base_obs), *map(str, nav)]
    stamp = out_pos.with_suffix(".run.txt")
    key = conf.read_text() + "\n" + " ".join(cmd)
    if (out_pos.exists() and stamp.exists() and stamp.read_text() == key
            and out_pos.stat().st_mtime > max(Path(f).stat().st_mtime for f in [rover_obs, *base_obs])):
        log(f"Reusing PPK result from an earlier run: {out_pos.name}")
        return out_pos
    log("Running RTKLIB: " + " ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=7200,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if res.returncode != 0 or not out_pos.exists():
        raise PpkError(f"rnx2rtkp failed (code {res.returncode}):\n{res.stderr[-2000:]}")
    stamp.write_text(key)
    return out_pos


def read_pos(path: Path) -> np.ndarray:
    rows = []
    for line in path.read_text(errors="replace").splitlines():
        if not line or line.startswith("%"):
            continue
        p = line.split()
        if len(p) < 10:
            continue
        try:
            ratio = float(p[14]) if len(p) > 14 else 0.0
            rows.append((int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4]),
                         int(p[5]), int(p[6]), float(p[7]), float(p[8]), float(p[9]), ratio))
        except ValueError:
            continue
    if not rows:
        raise PpkError(f"No solutions in {path.name}. Check base distance and data overlap.")
    return np.array(rows, dtype=POS_DTYPE)


def quality_summary(pos: np.ndarray) -> dict[str, float]:
    n = len(pos)
    return {Q_NAMES.get(q, str(q)): float(np.sum(pos["q"] == q)) / n * 100 for q in np.unique(pos["q"])}
