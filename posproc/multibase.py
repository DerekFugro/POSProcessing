"""Cross-check PPK answers from several base stations and merge them into one solution.

On long baselines RTKLIB sometimes "fixes" to a wrong answer (a whole number of wavelengths off),
and nothing in its own output shows it. A second and third base catch this: epoch by epoch, each
base's fix is compared with the other bases' answers. A fix that disagrees with them and has no
other base backing it is thrown out; where no fix can be trusted the float answers are used.
The spread between bases also gives a realistic accuracy (RTKLIB's own sigmas are ~10x too small).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Agreement limits. Measured 2026-11-15 run 172605: float answers from three bases agree to
# 0.38 m horizontal / 0.33 m vertical (95%); the wrong LS02 fixes were 0.8-1.3 m away.
FIX_TOL_H_M = 0.15            # fix vs fix
FIX_TOL_V_M = 0.25
FLOAT_TOL_H_M = 0.40          # fix vs float, float vs float
FLOAT_TOL_V_M = 0.50
SEGMENT_REJECT_FRACTION = 0.2  # throw out a whole run of fixes if more than this part of it is wrong
MIN_SEGMENT_CONFIRMED = 10    # confirmed epochs that make a whole run of fixes trusted
MIN_PAIR_EPOCHS = 30        # epochs needed before a base-to-base comparison is trusted
ACCURACY_PERCENTILE = 95
TIME_KEY_SCALE = 100          # epochs match to 0.01 s
EARTH_R_M = 6378137.0
Q_NONE, Q_FIX, Q_FLOAT = 0, 1, 2

# Status of each base's fix at each epoch
CONFIRMED = "confirmed"       # another base's fix agrees
FLOAT_CHECKED = "float_checked"   # no other fix there, the other bases' floats agree
UNCHECKED = "unchecked"       # no other base has an answer at that epoch
REJECTED = "rejected"         # disagrees with the other bases: a wrong fix


@dataclass
class BaseSolution:
    site: str
    dist_km: float
    pos: np.ndarray               # ppk.POS_DTYPE rows


@dataclass
class CrossCheck:
    pos: np.ndarray               # merged solution for the fusion
    primary: str                  # base used for most of the merged solution
    per_base: dict = field(default_factory=dict)   # site -> {"epochs", "fix", CONFIRMED, ..., REJECTED}
    pairs: list = field(default_factory=list)      # base-to-base agreement, see _pair_stats
    accuracy: dict = field(default_factory=dict)   # {"horizontal", "vertical", "basis"} (m, 95%) or {}
    warnings: list = field(default_factory=list)


def _keys(pos: np.ndarray) -> np.ndarray:
    return np.round(pos["t"] * TIME_KEY_SCALE).astype(np.int64)


def _neu(lat1, lon1, h1, lat2, lon2, h2):
    """Local north / east / up difference (m) of point 1 minus point 2 (small differences)."""
    n = np.radians(lat1 - lat2) * EARTH_R_M
    e = np.radians(lon1 - lon2) * EARTH_R_M * np.cos(np.radians(lat1))
    return n, e, h1 - h2


def _grid(sols: list[BaseSolution]):
    """All bases on one epoch axis: arrays (bases x epochs); q = 0 where a base has no answer."""
    keys = np.unique(np.concatenate([_keys(s.pos) for s in sols]))
    nb, ne = len(sols), len(keys)
    lat, lon, h = (np.full((nb, ne), np.nan) for _ in range(3))
    q = np.zeros((nb, ne), np.int32)
    row = np.full((nb, ne), -1, np.int64)
    for b, s in enumerate(sols):
        k = _keys(s.pos)
        _, first = np.unique(k, return_index=True)          # drop duplicate epochs, keep the first
        idx = np.searchsorted(keys, k[first])
        p = s.pos[first]
        lat[b, idx], lon[b, idx], h[b, idx] = p["lat"], p["lon"], p["h"]
        q[b, idx] = np.where(np.isin(p["q"], (Q_FIX, Q_FLOAT)), p["q"], Q_NONE)
        row[b, idx] = first
    return keys, lat, lon, h, q, row


def _agreement(lat, lon, h, q):
    """agree[a, b, t] / disagree[a, b, t]: do base a and base b agree at epoch t?"""
    nb, ne = q.shape
    agree = np.zeros((nb, nb, ne), bool)
    disagree = np.zeros((nb, nb, ne), bool)
    for a in range(nb):
        for b in range(nb):
            if a == b:
                continue
            both = (q[a] > 0) & (q[b] > 0)
            n, e, u = _neu(lat[a], lon[a], h[a], lat[b], lon[b], h[b])
            both_fixed = (q[a] == Q_FIX) & (q[b] == Q_FIX)
            tol_h = np.where(both_fixed, FIX_TOL_H_M, FLOAT_TOL_H_M)
            tol_v = np.where(both_fixed, FIX_TOL_V_M, FLOAT_TOL_V_M)
            with np.errstate(invalid="ignore"):
                ok = (np.hypot(n, e) <= tol_h) & (np.abs(u) <= tol_v)
            agree[a, b] = both & ok
            disagree[a, b] = both & ~ok
    return agree, disagree


def _fix_status(q, agree, disagree) -> np.ndarray:
    """Status string for every (base, epoch) where that base is fixed; '' elsewhere.
    A fix is thrown out when another base disagrees and it has less backing than the solution
    it disagrees with (so 2 agreeing bases out-vote 1; 2 bases that disagree both lose)."""
    nb, ne = q.shape
    support = agree.sum(axis=1)                              # (bases, epochs)
    status = np.full((nb, ne), "", dtype=object)
    for a in range(nb):
        fixed = q[a] == Q_FIX
        # A float that disagrees only counts when another base backs it: a lone float can be
        # metres off while it converges, a fix cannot be judged by it.
        counts = disagree[a] & ((q == Q_FIX) | (support >= 1))
        any_dis = counts.any(axis=0)
        dis_support = np.where(counts, support, -1).max(axis=0)   # strongest backing against a
        fix_agree = (agree[a] & (q == Q_FIX)).any(axis=0)
        rejected = any_dis & ((support[a] == 0) | (support[a] < dis_support))
        status[a, fixed] = UNCHECKED
        status[a, fixed & ~rejected & (support[a] > 0)] = FLOAT_CHECKED
        status[a, fixed & ~rejected & fix_agree] = CONFIRMED
        status[a, fixed & rejected] = REJECTED
        _reject_bad_segments(fixed, status[a])
    return status


def _reject_bad_segments(fixed: np.ndarray, status: np.ndarray) -> None:
    """A run of fixed epochs shares one set of ambiguities, so it is right or wrong as a whole.
    Mostly confirmed by another base's fixes -> keep all of it (the clashes were with another
    base's wrong fix). Otherwise, a real part wrong -> throw all of it out."""
    edges = np.flatnonzero(np.diff(np.concatenate(([0], fixed.astype(np.int8), [0]))))
    for start, stop in zip(edges[::2], edges[1::2]):
        seg = status[start:stop]
        confirmed, rejected = (seg == CONFIRMED).sum(), (seg == REJECTED).sum()
        agreeing = confirmed + (seg == FLOAT_CHECKED).sum()
        if confirmed >= MIN_SEGMENT_CONFIRMED and agreeing > rejected:
            seg[seg == REJECTED] = CONFIRMED
        elif rejected > SEGMENT_REJECT_FRACTION * len(seg):
            seg[:] = REJECTED


def _pair_stats(sols, lat, lon, h, q, status) -> list[dict]:
    """Base-to-base agreement over epochs where both have a usable answer."""
    out = []
    usable = (q > 0) & (status != REJECTED)
    for a in range(len(sols)):
        for b in range(a + 1, len(sols)):
            for kind, m in (("fix", usable[a] & usable[b] & (q[a] == Q_FIX) & (q[b] == Q_FIX)),
                            ("float", usable[a] & usable[b] & ((q[a] == Q_FLOAT) | (q[b] == Q_FLOAT)))):
                if not m.any():
                    continue
                n, e, u = _neu(lat[a, m], lon[a, m], h[a, m], lat[b, m], lon[b, m], h[b, m])
                hz = np.hypot(n, e)
                out.append({"a": sols[a].site, "b": sols[b].site, "kind": kind, "epochs": int(m.sum()),
                            "h_median": float(np.median(hz)),
                            "h_95": float(np.percentile(hz, ACCURACY_PERCENTILE)),
                            "v_median": float(np.median(u)),
                            "v_95": float(np.percentile(np.abs(u), ACCURACY_PERCENTILE))})
    return out


def _accuracy(pairs: list[dict], merged_fix_pct: float) -> dict:
    """Realistic 95% accuracy from how well the bases agree on the kind of answer mostly used."""
    kind = "fix" if merged_fix_pct >= 50 else "float"
    use = [p for p in pairs if p["kind"] == kind and p["epochs"] >= MIN_PAIR_EPOCHS]
    if not use:
        return {}
    best = min(use, key=lambda p: p["h_95"])               # the closest pair: the others may hold an odd base
    return {"horizontal": best["h_95"], "vertical": best["v_95"],
            "basis": f"{best['a']} vs {best['b']}, {kind} answers, {best['epochs']} epochs"}


def cross_check(sols: list[BaseSolution]) -> CrossCheck:
    """Compare the bases, drop wrong fixes, and merge into one solution (sorted by time)."""
    if not sols:
        raise ValueError("No PPK solutions to compare.")
    sols = sorted(sols, key=lambda s: s.dist_km)
    keys, lat, lon, h, q, row = _grid(sols)
    agree, disagree = _agreement(lat, lon, h, q)
    status = _fix_status(q, agree, disagree)
    kept_fix = (q == Q_FIX) & (status != REJECTED)

    per_base = {}
    for b, s in enumerate(sols):
        per_base[s.site] = {"dist_km": s.dist_km, "epochs": int((q[b] > 0).sum()), "fix": int((q[b] == Q_FIX).sum()),
                            **{k: int((status[b] == k).sum()) for k in (CONFIRMED, FLOAT_CHECKED, UNCHECKED, REJECTED)}}
    # primary: most confirmed fixes, then most kept fixes, then most answers, then nearest
    order = sorted(range(len(sols)), key=lambda b: (-per_base[sols[b].site][CONFIRMED], -int(kept_fix[b].sum()),
                                                   -per_base[sols[b].site]["epochs"], sols[b].dist_km))
    primary = order[0]
    by_pref = [primary] + [b for b in range(len(sols)) if b != primary]   # rest stay nearest-first

    pick = np.full(len(keys), -1)
    for wanted in (kept_fix, (q == Q_FLOAT)):
        for b in by_pref:
            take = (pick < 0) & wanted[b]
            pick[take] = b
    rows = [sols[b].pos[row[b, t]] for t, b in enumerate(pick) if b >= 0]
    merged = np.array(rows, dtype=sols[0].pos.dtype) if rows else sols[primary].pos[:0]
    merged = merged[np.argsort(merged["t"], kind="stable")]

    pairs = _pair_stats(sols, lat, lon, h, q, status)
    fix_pct = float((merged["q"] == Q_FIX).mean() * 100) if len(merged) else 0.0
    res = CrossCheck(merged, sols[primary].site, per_base, pairs, _accuracy(pairs, fix_pct))
    res.warnings = _warnings(sols, per_base, fix_pct, res.accuracy)
    return res


def _warnings(sols, per_base, merged_fix_pct, accuracy) -> list[str]:
    w = []
    if len(sols) == 1:
        w.append(f"Only one base station ({sols[0].site}) could be used, so its fixes could not be "
                 "cross-checked. Fixes on long baselines can be wrong by a metre or more.")
        return w
    total_fix = sum(p["fix"] for p in per_base.values())
    bad = {site: p for site, p in per_base.items() if p[REJECTED]}
    for site, p in bad.items():
        w.append(f"{p[REJECTED] * 100 // max(p['fix'], 1)}% of the fixes from {site} disagree with the other "
                 f"base stations and were thrown out (wrong fixes).")
    if merged_fix_pct > 0 and not any(p[CONFIRMED] for p in per_base.values()):
        w.append("No second base station fixed at the same time, so the fixes used are only checked "
                 "against float answers. Check against a known point if accuracy matters.")
    if total_fix and merged_fix_pct == 0:
        w.append("The base stations do not agree on a fixed answer, so float answers are used. "
                 "Expect decimetre accuracy; a longer recording (20+ minutes) usually fixes reliably.")
    if not accuracy:
        w.append("Too little overlap between base stations to estimate a realistic accuracy.")
    return w
