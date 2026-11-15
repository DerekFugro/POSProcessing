"""App settings, stored as settings.json. Edit there or in the GUI.

Where things live:
  run from the source folder:  settings.json, cache\\ and tools\\rtklib\\ beside the code (as before)
  installed (PyInstaller exe):  program in C:\\Program Files\\Fugro Roadware\\ACSApps\\POSProcessing\\
                                settings.json and cache\\ in C:\\ProgramData\\Fugro Roadware\\POSProcessing\\
                                (shared by all users; Program Files is read-only for them)
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
SOURCE_DIR = Path(__file__).resolve().parent.parent
INSTALL_DIR = Path(sys.executable).resolve().parent if FROZEN else SOURCE_DIR     # program files
DATA_DIR = (Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Fugro Roadware" / "POSProcessing"
            if FROZEN else SOURCE_DIR)                                             # writable data
APP_DIR = INSTALL_DIR          # kept for older code
SETTINGS_FILE = DATA_DIR / "settings.json"


def app_version() -> str:
    """'2026.11.15-abc1234' from the VERSION file the installer build writes; 'dev' when run from source."""
    base = Path(getattr(sys, "_MEIPASS", SOURCE_DIR))
    try:
        return (base / "VERSION").read_text(encoding="ascii").strip() or "dev"
    except OSError:
        return "dev"


@dataclass
class Settings:
    # External tools (Windows)
    convert_to_rinex_exe: str = r"C:\Program Files (x86)\Trimble\ConvertToRinex\convertToRinex.exe"
    rnx2rtkp_exe: str = str(INSTALL_DIR / "tools" / "rtklib" / "rnx2rtkp.exe")
    rinex_version: str = "3.04"
    rt27_extension: str = ".rt27"           # extension ConvertToRINEX sees on the extracted stream
    # CORS
    cors_bucket: str = "https://noaa-cors-pds.s3.amazonaws.com"
    cache_dir: str = str(DATA_DIR / "cache")
    tile_cache_dir: str = str(DATA_DIR / "cache" / "tiles_osm")
    max_baseline_km: float = 100.0
    stations_to_try: int = 3
    manual_base_rinex: list[str] = field(default_factory=list)   # optional: your own base files
    manual_base_xyz: list[float] = field(default_factory=list)   # ITRF2020 ECEF of manual base ARP
    # PPK (RTKLIB)
    elevation_mask_deg: float = 15.0
    navsys: int = 1 + 4                      # GPS + GLONASS (RTKLIB bit mask)
    ar_mode: str = "fix-and-hold"
    # Antenna calibrations (ANTEX). POSView lever arm goes to the antenna bottom (ARP), so RTKLIB
    # must solve for the ARP: rover = Zephyr 2 (all Applanix POS LV antennas), base = from its RINEX.
    use_antenna_models: bool = True
    rover_antenna: str = "TRM57970.00     NONE"
    antex_url: str = "https://www.ngs.noaa.gov/ANTCAL/LoadFile?file=ngs20.atx"
    # Fusion
    process_noise_m_per_sqrt_s: float = 0.02
    ppk_min_sigma_m: float = 0.01
    use_float: bool = True
    outlier_sigma: float = 5.0
    # CSV logs
    csv_suffix: str = "_PPK"
    csv_elev_units: str = "auto"             # auto | ft | m
    csv_match_tolerance_m: float = 0.05
    csv_ambiguity_gap_s: float = 60.0
    # Live logger (PCS Ethernet logging port)
    logger_host: str = "192.168.0.40"
    logger_port: int = 5603
    logger_out: str = ""                     # user picks it; each run makes <name>_YYYYMMDD_HHMMSS inside
    logger_session_name: str = "SurveyorPOS"
    # Live health from the PCS display port
    display_port: int = 5600
    display_listen_on_start: bool = True
    display_only_from_pcs: bool = False      # True: ignore display data not from logger_host

    @classmethod
    def load(cls, path: Path = SETTINGS_FILE) -> "Settings":
        s = cls()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            for k, v in data.items():
                if hasattr(s, k):
                    setattr(s, k, v)
        return s

    def save(self, path: Path = SETTINGS_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
