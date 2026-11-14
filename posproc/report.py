"""HTML processing report: RMS before and after, PPK quality, corrections, CSV matches."""
from __future__ import annotations

import base64
import html
import io
from pathlib import Path

import numpy as np

STYLE = """
body{font-family:Segoe UI,Arial,sans-serif;background:#1e2a36;color:#ecf0f1;margin:24px;max-width:1100px}
h1{font-size:22px}h2{font-size:17px;border-bottom:1px solid #34495e;padding-bottom:4px;margin-top:28px}
table{border-collapse:collapse;margin:8px 0}td,th{border:1px solid #34495e;padding:4px 10px;text-align:right}
th{background:#2c3e50}td:first-child,th:first-child{text-align:left}
.warn{color:#f39c12}.err{color:#e74c3c}.ok{color:#27ae60}.note{color:#bdc3c7;font-size:13px}
img{max-width:100%;background:#fff;border-radius:4px;margin:6px 0}
"""


def _fmt(v, nd=3):
    return "-" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:.{nd}f}"


def _png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return '<img src="data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode() + '">'


def _session_plot(sr) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    f = sr.fusion
    t = (f.t - f.t[0]) / 60
    fig, ax = plt.subplots(2, 1, figsize=(10, 5.5), sharex=True)
    ax[0].plot(t, f.correction[:, 0], label="North")
    ax[0].plot(t, f.correction[:, 1], label="East")
    ax[0].plot(t, -f.correction[:, 2], label="Up")
    ax[0].set_ylabel("Correction (m)")
    ax[0].legend(loc="upper right")
    ax[0].grid(alpha=0.3)
    g2 = sr.pcs.g2
    if len(g2):
        tg = (g2["t"] - f.t[0]) / 60
        ax[1].plot(tg, np.hypot(g2["n"], g2["e"]), label="Before: real-time horiz. RMS (Group 2)")
        ax[1].plot(tg, g2["d"], label="Before: real-time vert. RMS")
    ax[1].plot(t, np.hypot(f.sigma[:, 0], f.sigma[:, 1]), label="After: PPK horiz. sigma")
    ax[1].plot(t, f.sigma[:, 2], label="After: PPK vert. sigma")
    ax[1].set_yscale("log")
    ax[1].set_ylabel("Metres (1-sigma)")
    ax[1].set_xlabel("Minutes from start")
    ax[1].legend(loc="upper right", fontsize=8)
    ax[1].grid(alpha=0.3, which="both")
    return _png(fig)


def write_report(result, path: Path) -> Path:
    e = html.escape
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>PPK Report</title>"
           f"<style>{STYLE}</style></head><body><h1>POS PPK Processing Report</h1>",
           f"<p class='note'>Output folder: {e(str(result.out_dir))}<br>Coordinate frame: {e(result.frame)}</p>"]
    for sr in result.sessions:
        out.append(f"<h2>Session {e(sr.name)}</h2>")
        if sr.start:
            out.append(f"<p>{sr.start:%Y-%m-%d %H:%M:%S} to {sr.end:%H:%M:%S} GPS time &middot; "
                       f"{len(sr.pcs.g1)} nav epochs &middot; {e(sr.pcs.version.split('|')[0].strip())}</p>")
        if sr.error:
            out.append(f"<p class='err'>Failed: {e(sr.error)}</p>")
        for w in sr.warnings:
            out.append(f"<p class='warn'>&#9888; {e(w)}</p>")
        if sr.fusion is None:
            continue
        q = ", ".join(f"{k} {v:.0f}%" for k, v in sr.quality.items())
        out.append(f"<p>Base station <b>{e(sr.station)}</b>, {sr.baseline_km:.1f} km, "
                   f"{sr.base_interval_s:g} s data &middot; PPK quality: {e(q)} &middot; "
                   f"fixes used {sr.fusion.ppk_used}, rejected {sr.fusion.ppk_rejected}</p>")
        b, a = sr.rms_before, sr.rms_after
        out.append("<table><tr><th>Position accuracy (m, 1-sigma, median)</th><th>North</th><th>East</th><th>Down</th></tr>")
        out.append(f"<tr><td>Before: real-time POS RMS (Group 2, fully aligned)</td><td>{_fmt(b.get('north'))}</td>"
                   f"<td>{_fmt(b.get('east'))}</td><td>{_fmt(b.get('down'))}</td></tr>")
        out.append(f"<tr><td>After: post-processed estimate</td><td>{_fmt(a.get('north'))}</td>"
                   f"<td>{_fmt(a.get('east'))}</td><td>{_fmt(a.get('down'))}</td></tr>")
        out.append(f"<tr><td>After: PPK fit residual (RMS)</td><td>{_fmt(a.get('ppk_resid_north'))}</td>"
                   f"<td>{_fmt(a.get('ppk_resid_east'))}</td><td>{_fmt(a.get('ppk_resid_down'))}</td></tr></table>")
        out.append("<p class='note'>'After' is the formal 1-sigma from RTKLIB and the smoother. RTKLIB "
                   "formal sigmas are usually optimistic, especially on long baselines; check against a "
                   "known point for true accuracy.</p>")
        out.append(f"<p class='note'>Attitude is not re-processed. Real-time attitude RMS, median (deg): roll "
                   f"{_fmt(b.get('roll'), 4)}, pitch {_fmt(b.get('pitch'), 4)}, heading {_fmt(b.get('heading'), 4)}.</p>")
        c = sr.correction_stats
        out.append("<table><tr><th>Correction applied (m)</th><th>Mean N</th><th>Mean E</th><th>Mean Up</th>"
                   "<th>RMS horiz.</th><th>Max horiz.</th><th>RMS vert.</th><th>Max vert.</th></tr>"
                   f"<tr><td>Real-time &rarr; PPK</td><td>{_fmt(c['mean_north'])}</td><td>{_fmt(c['mean_east'])}</td>"
                   f"<td>{_fmt(c['mean_up'])}</td><td>{_fmt(c['rms_horizontal'])}</td>"
                   f"<td>{_fmt(c['max_horizontal'])}</td><td>{_fmt(c['rms_vertical'])}</td>"
                   f"<td>{_fmt(c['max_vertical'])}</td></tr></table>")
        try:
            out.append(_session_plot(sr))
        except Exception as exc:   # plotting must never stop the report
            out.append(f"<p class='warn'>Plot failed: {e(str(exc))}</p>")
        if sr.trajectory_csv:
            out.append(f"<p class='note'>Trajectory: {e(str(sr.trajectory_csv))}</p>")
    if result.csv:
        out.append("<h2>CSV GPS logs</h2><table><tr><th>File</th><th>Rows</th><th>Matched</th><th>Units</th>"
                   "<th>Median match (mm)</th><th>Mean shift N/E/Up (m)</th><th>Output / notes</th></tr>")
        for r in result.csv:
            cls = "ok" if r.ok else "warn"
            note = e(r.out_path.name) if r.out_path else ""
            if r.warnings:
                note += "<br>" + "<br>".join(e(w) for w in r.warnings)
            mc = r.mean_corr_neu_m
            out.append(f"<tr><td class='{cls}'>{e(r.path.name)}</td><td>{r.rows}</td><td>{r.matched}</td>"
                       f"<td>{e(r.units)}</td><td>{_fmt(r.median_resid_m * 1000, 1)}</td>"
                       f"<td>{_fmt(mc[0])} / {_fmt(mc[1])} / {_fmt(mc[2])}</td>"
                       f"<td style='text-align:left'>{note}</td></tr>")
        out.append("</table>")
    out.append("</body></html>")
    path.write_text("\n".join(out), encoding="utf-8")
    return path
