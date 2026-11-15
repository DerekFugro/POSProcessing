# PyInstaller spec - builds packaging\dist\POSProcessing\POSProcessing.exe (no Python needed).
# Run through packaging\build.ps1, which writes VERSION first.
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "gui" / "web"), "gui/web"),     # bundled Leaflet for the maps
    (str(ROOT / "VERSION"), "."),               # posproc.config.app_version()
]

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=["matplotlib.backends.backend_agg"],   # report plots (imported inside a function)
    excludes=["pytest", "tkinter", "IPython"],
    noarchive=False,
)

# OpenSSL must be Python's own copy. On a PC with Git for Windows on PATH, PyInstaller can pick
# Git's libssl-3-x64.dll and the app then fails on HTTPS (same fix as SurveyorCalibration).
PY_DLLS = Path(sys.base_prefix) / "DLLs"
SSL_DLLS = {"libssl-3-x64.dll", "libcrypto-3-x64.dll"}


def python_ssl(binaries):
    fixed = []
    for name, src, kind in binaries:
        own = PY_DLLS / Path(name).name
        if Path(name).name.lower() in SSL_DLLS and own.exists():
            src = str(own)
        if "\\git\\" in src.lower():
            raise SystemExit(f"Build stopped: {name} would come from Git ({src}).")
        fixed.append((name, src, kind))
    return fixed


a.binaries = python_ssl(a.binaries)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="POSProcessing", console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="POSProcessing")
