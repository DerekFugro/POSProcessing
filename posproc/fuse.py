"""Blend PPK antenna fixes with the real-time POS trajectory.

The real-time Group 1 solution already holds the IMU's motion (position, attitude,
50 Hz). Its position error (OmniSTAR/real-time GNSS drift) changes slowly. We model
that error as a 3-D random walk, estimate it from the PPK fixes with a Kalman filter
plus Rauch-Tung-Striebel backward smoother, and remove it from every Group 1 epoch.
Attitude is left as logged.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .geo import apply_ned, body_to_ned, ned_offset

SECONDS_PER_WEEK = 604800.0


@dataclass
class FusionResult:
    t: np.ndarray                 # Group 1 times (GPS seconds of week)
    lat: np.ndarray
    lon: np.ndarray
    h: np.ndarray                 # ellipsoidal height, metres
    sigma: np.ndarray             # (N,3) 1-sigma N,E,D metres of the correction
    correction: np.ndarray        # (N,3) N,E,D metres applied to the real-time position
    covered: np.ndarray           # bool: epoch is inside the span of usable PPK fixes
    ppk_t: np.ndarray             # times of usable PPK fixes
    ppk_resid: np.ndarray         # (M,3) PPK minus smoothed correction
    ppk_used: int
    ppk_rejected: int


def antenna_positions(g1: np.ndarray, lever_ref_to_gnss) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    off = body_to_ned(g1["roll"], g1["pitch"], g1["heading"], lever_ref_to_gnss)
    return apply_ned(g1["lat"], g1["lon"], g1["h"], off[:, 0], off[:, 1], off[:, 2])


def ppk_times_on_session_week(pos: np.ndarray, week0: int) -> np.ndarray:
    return pos["t"] + (pos["week"] - week0) * SECONDS_PER_WEEK


def fuse(g1: np.ndarray, lever_ref_to_gnss, pos: np.ndarray, week0: int, *,
         q_m_per_sqrt_s: float = 0.02, min_sigma: float = 0.01, use_float: bool = True,
         outlier_sigma: float = 5.0) -> FusionResult:
    t_rt = g1["t"]
    alat, alon, ah = antenna_positions(g1, lever_ref_to_gnss)

    tp = ppk_times_on_session_week(pos, week0)
    good_q = (pos["q"] == 1) | ((pos["q"] == 2) & use_float)
    inside = (tp >= t_rt[0]) & (tp <= t_rt[-1])
    sel = good_q & inside
    if sel.sum() < 2:
        raise ValueError("Fewer than 2 usable PPK fixes overlap the POS data.")
    tp, p = tp[sel], pos[sel]
    order = np.argsort(tp)
    tp, p = tp[order], p[order]

    rlat = np.interp(tp, t_rt, alat)
    rlon = np.interp(tp, t_rt, alon)
    rh = np.interp(tp, t_rt, ah)
    dn, de, dd = ned_offset(p["lat"], p["lon"], p["h"], rlat, rlon, rh)
    z = np.column_stack([dn, de, dd])
    r = np.column_stack([np.maximum(p["sdn"], min_sigma), np.maximum(p["sde"], min_sigma),
                         np.maximum(p["sdu"], min_sigma)]) ** 2
    q = q_m_per_sqrt_s ** 2

    keep = np.ones(len(tp), bool)
    for _ in range(4):
        xs, ps = _smooth(tp[keep], z[keep], r[keep], q)
        xi = np.column_stack([np.interp(tp, tp[keep], xs[:, k]) for k in range(3)])
        pi = np.column_stack([np.interp(tp, tp[keep], ps[:, k]) for k in range(3)])
        new_keep = ~np.any(np.abs(z - xi) > outlier_sigma * np.sqrt(r + pi), axis=1)
        if np.array_equal(new_keep, keep):
            break
        keep = new_keep
        if keep.sum() < 2:
            raise ValueError("Too many PPK fixes rejected as outliers.")
    else:
        xs, ps = _smooth(tp[keep], z[keep], r[keep], q)

    tk = tp[keep]
    corr, var, covered = _evaluate(t_rt, tk, xs, ps, q)
    lat, lon, h = apply_ned(g1["lat"], g1["lon"], g1["h"], corr[:, 0], corr[:, 1], corr[:, 2])
    return FusionResult(t_rt, lat, lon, h, np.sqrt(var), corr, covered,
                        tk, z[keep] - xs, int(keep.sum()), int((~keep).sum()))


def _smooth(t, z, r, q):
    """Per-axis random-walk Kalman filter + RTS smoother. Returns states and variances."""
    n = len(t)
    xf = np.zeros((n, 3)); pf = np.zeros((n, 3)); pp = np.zeros((n, 3))
    x, P = z[0].copy(), r[0].copy()
    for k in range(n):
        if k:
            P = P + q * (t[k] - t[k - 1])
        pp[k] = P
        K = P / (P + r[k])
        x = x + K * (z[k] - x)
        P = (1 - K) * P
        xf[k], pf[k] = x, P
    xs, ps = xf.copy(), pf.copy()
    for k in range(n - 2, -1, -1):
        c = pf[k] / pp[k + 1]
        xs[k] = xf[k] + c * (xs[k + 1] - xf[k])   # random walk: predicted state = xf[k]
        ps[k] = pf[k] + c * c * (ps[k + 1] - pp[k + 1])
    return xs, ps


def _evaluate(t, tk, xs, ps, q):
    """Correction and variance at every real-time epoch."""
    corr = np.column_stack([np.interp(t, tk, xs[:, k]) for k in range(3)])
    idx = np.clip(np.searchsorted(tk, t) - 1, 0, len(tk) - 2)
    t0, t1 = tk[idx], tk[idx + 1]
    a = np.clip((t - t0) / np.where(t1 > t0, t1 - t0, 1), 0, 1)[:, None]
    var = (1 - a) * ps[idx] + a * ps[idx + 1] + q * ((t1 - t0)[:, None]) * a * (1 - a)
    before, after = t < tk[0], t > tk[-1]
    var[before] = ps[0] + q * (tk[0] - t[before])[:, None]
    var[after] = ps[-1] + q * (t[after] - tk[-1])[:, None]
    return corr, var, ~(before | after)
