"""Convert the extracted RT27 stream to RINEX with Trimble Convert to RINEX.

Command line (Convert to RINEX 3.x): convertToRinex.exe <input> -p <outdir> -v <ver> -d -s
On PCs with a comma decimal separator the version must be written "3,04", so we
retry with a comma if the first run makes no files.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

OBS_PATTERNS = ("*.??o", "*.??O", "*_MO.rnx", "*.obs")
NAV_PATTERNS = ("*.??n", "*.??N", "*.??g", "*.??G", "*.??l", "*.??p", "*_MN.rnx", "*_GN.rnx", "*_RN.rnx", "*.nav")


class ConvertError(RuntimeError):
    pass


def convert_rt27(exe: str, stream_file: Path, out_dir: Path, version: str, log=print) -> tuple[Path, list[Path]]:
    exe_path = Path(exe)
    if not exe_path.exists():
        raise ConvertError(
            f"Trimble Convert to RINEX not found at:\n  {exe}\n"
            "Install it from Trimble (free) and set its path in Settings.")
    out_dir.mkdir(parents=True, exist_ok=True)
    prev = _newest(out_dir, OBS_PATTERNS, stream_file.stat().st_mtime)
    if prev:
        log(f"Reusing RINEX from an earlier run: {prev.name}")
        nav = [p for pat in NAV_PATTERNS for p in out_dir.glob(pat)]
        return prev, sorted(set(nav))
    start = time.time() - 2
    for ver in (version, version.replace(".", ",")):
        cmd = [str(exe_path), str(stream_file), "-p", str(out_dir), "-v", ver, "-d", "-s"]
        log("Running: " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if res.stdout.strip():
            log(res.stdout.strip())
        if res.stderr.strip():
            log(res.stderr.strip())
        if "type initializer" in (res.stdout + res.stderr):
            raise ConvertError(
                "Convert to RINEX could not start: its Trimble data files are missing "
                "(C:\\ProgramData\\Trimble). Install the Trimble Office Configuration Utility "
                "from Trimble's support site, then try again.")
        obs = _newest(out_dir, OBS_PATTERNS, start)
        if obs:
            nav = [p for pat in NAV_PATTERNS for p in out_dir.glob(pat) if p.stat().st_mtime >= start]
            return obs, sorted(set(nav))
    raise ConvertError(
        "Convert to RINEX ran but made no observation file.\n"
        f"Input: {stream_file}\nCheck that it accepts RT27 files with extension "
        f"'{stream_file.suffix}' (Settings > rt27_extension).")


def _newest(folder: Path, patterns, since: float) -> Path | None:
    files = [p for pat in patterns for p in folder.glob(pat) if p.stat().st_mtime >= since]
    return max(files, key=lambda p: p.stat().st_size) if files else None
