"""Command-line run (no GUI).

  python -m posproc.cli --pcs <folder or files...> [--csv <folder or files...>] --out <folder> [--rate 10]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .config import Settings
from .pipeline import Pipeline


def _expand(items: list[str], pattern: str, suffix: str = "") -> list[str]:
    out = []
    for it in items:
        p = Path(it)
        if p.is_dir():
            out += [str(f) for f in sorted(p.rglob(pattern))
                    if f.is_file() and not (suffix and f.stem.lower().endswith(suffix.lower()))]
        else:
            out.append(str(p))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="POS PPK Processor (command line)")
    ap.add_argument("--pcs", nargs="+", required=True, help="POS files or folders (LV_1_*.0nn)")
    ap.add_argument("--csv", nargs="*", default=[], help="CSV GPS logs or folders")
    ap.add_argument("--out", required=True, help="Output folder")
    ap.add_argument("--rate", type=float, default=10.0, help="Trajectory rate Hz (0 = all epochs)")
    a = ap.parse_args(argv)
    s = Settings.load()
    pcs = [f for f in _expand(a.pcs, "LV_*.*") if Path(f).suffix[1:].isdigit()]
    csvs = _expand(a.csv, "*.csv", s.csv_suffix)
    t0 = time.time()
    last = [0.0]

    def progress(frac, msg):
        if time.time() - last[0] > 5:
            last[0] = time.time()
            print(f"[{frac * 100:5.1f}%] {msg}", flush=True)

    res = Pipeline(s, log=lambda m: print(m, flush=True), progress=progress).run(pcs, csvs, Path(a.out), a.rate)
    print(f"Finished in {time.time() - t0:.0f} s. Report: {res.report_html}")
    return 0 if all(not sr.error for sr in res.sessions) else 1


if __name__ == "__main__":
    sys.exit(main())
