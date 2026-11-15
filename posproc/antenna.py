"""GNSS antenna calibrations (ANTEX) for PPK.

The POSView lever arm ref->GNSS is measured to the bottom of the antenna (ARP) - Derek,
2026-11-15. RTKLIB therefore needs the rover's and the base's antenna calibrations, so that it
solves for the rover ARP and puts the base's phase centre at NOAA's ARP + offset. Without them
the result is the antenna phase centre (Zephyr 2: about 6 cm above the ARP) and the base error
(9-12 cm on NOAA bases) goes into the heights.

Calibrations: NGS ngs20.atx (absolute, IGS20 frame), downloaded once into the cache.
All Applanix POS LV antennas are Trimble Zephyr 2 (Derek): TRM57970.00. Checked in ngs20.atx:
TRM55970.00 is a copy of TRM57970.00, and TRM77970.00 differs by <= 6 mm.
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

NGS_ANTEX_URL = "https://www.ngs.noaa.gov/ANTCAL/LoadFile?file=ngs20.atx"
ANTEX_MAX_AGE_S = 90 * 86400
DOWNLOAD_TIMEOUT_S = 180
ZEPHYR2 = "TRM57970.00     NONE"


class AntennaError(RuntimeError):
    pass


def ensure_antex(cache_dir: str | Path, url: str = NGS_ANTEX_URL, log=print) -> Path:
    """Path to the ANTEX file in the cache; downloaded when missing or older than 90 days."""
    path = Path(cache_dir) / "antex" / "ngs20.atx"
    if path.exists() and time.time() - path.stat().st_mtime < ANTEX_MAX_AGE_S:
        return path
    log("Downloading antenna calibrations (NGS ngs20.atx)...")
    try:
        r = requests.get(url, timeout=DOWNLOAD_TIMEOUT_S)
        r.raise_for_status()
        if b"ANTEX VERSION / SYST" not in r.content[:400]:
            raise AntennaError("the download is not an ANTEX file")
    except (requests.RequestException, AntennaError) as exc:
        if path.exists():                          # offline: keep using the old copy
            log(f"Could not refresh antenna calibrations ({exc}); using the cached copy.")
            return path
        raise AntennaError(f"Could not download antenna calibrations from {url}: {exc}") from None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(r.content)
    tmp.replace(path)
    return path


def antenna_name(type_: str, radome: str = "NONE") -> str:
    """ANTEX-style 20-character name: type padded to 16, then the radome."""
    return f"{type_.strip():<16}{(radome or 'NONE').strip():<4}"


def rinex_antenna(obs_path: str | Path) -> str:
    """Antenna type + radome from a RINEX observation header ('' if not given)."""
    with open(obs_path, encoding="ascii", errors="replace") as fh:
        for line in fh:
            label = line[60:].strip()
            if label == "ANT # / TYPE":
                field = line[20:40]
                return antenna_name(field[:16], field[16:20]) if field[:16].strip() else ""
            if label == "END OF HEADER":
                break
    return ""


def antex_offsets(antex: str | Path, name: str) -> dict[str, tuple[float, float, float]] | None:
    """{'G01': (north, east, up) mm, ...} for a receiver antenna, or None if not calibrated."""
    want = antenna_name(name[:16], name[16:20])
    found, freq, out = False, None, {}
    with open(antex, encoding="ascii", errors="replace") as fh:
        for line in fh:
            label = line[60:].strip()
            if label == "TYPE / SERIAL NO":
                found = line[:20] == want and not line[20:40].strip()   # receiver antennas: no serial
            elif not found:
                continue
            elif label == "START OF FREQUENCY":
                freq = line[3:6].strip()
            elif label == "NORTH / EAST / UP" and freq:
                out[freq] = tuple(float(v) for v in line[:30].split())
            elif label == "END OF ANTENNA":
                return out or None
    return None
