# CONTEXT - POSProcessing

Orientation file for anyone (person or coding agent) working on this project.

## Folders

| Path | What it is |
|---|---|
| `posproc/` | Processing library, no GUI. `pcs.py` reader, `rt27.py` raw GNSS, `convert.py` Trimble Convert to RINEX, `cors.py` NOAA CORS, `antenna.py` antenna calibrations (NGS ANTEX), `ppk.py` RTKLIB, `fuse.py` Kalman/RTS blend, `csvlog.py` CSV matching, `report.py`, `pipeline.py` orchestration, `config.py` settings, `logger.py` / `display.py` live POS, `control.py` / `marks.py` / `stdio.py` Surveyor link |
| `gui/` | PySide6 GUI. Follows the 5-method `__init__` pattern, dark theme in `styles.py` |
| `tests/` | pytest. `test_sample_data.py` needs `POSPROC_SAMPLES` |
| `tools/rtklib/` | Put `rnx2rtkp.exe` here (not committed) |
| `tools/pcs_simulator.py` | Fake PCS for testing the Record and Diagnostics tabs |
| `cache/` | Downloaded CORS data, coordinate list, antenna file, OSM map tiles (safe to delete). Installed app: `C:\ProgramData\Fugro Roadware\POSProcessing\cache` |
| `installers/` | Trimble installers + RTKLIB zip (on the PC, not committed; only `README.txt` is in git) |
| `packaging/` | Installer build (`build.ps1`, PyInstaller spec, Inno Setup script) |
| `docs/` | This file; `Comunication4OtherApps.md` (Surveyor protocol); `docs/reference/` holds local copies of Applanix PDFs (not committed) |

## Authoritative references

| Reference | Use for | Status |
|---|---|---|
| Applanix **PUBS-ICD-003259 Rev 9.0** (POS AV V6 User ICD, 2014) | Group/Message byte layouts | Used as stand-in. Groups 1, 2, 3, 99, 10001 and Message 20 were checked byte-for-byte against POS LV 220 V5 SW 09.03 logs |
| Applanix PUBS-MAN-003758 (POS LV V5 Install & Operation Guide, Rev 17/22/26) | Hardware, logging setup, Ethernet logging | In `D:\Manuals\pdf`; no byte layouts |
| NOAA CORS data page https://geodesy.noaa.gov/CORS/data.shtml | Bucket paths, coordinate files | Used |
| Trimble Convert to RINEX help https://help.fieldsystems.trimble.com/tbc/17261.htm | Accepts RT27 input | Used |

All Applanix PDFs used are in `D:\Manuals\pdf` (see `D:\Manuals\MANUALS_INDEX.md`).
Ignore: POS MV V5 guide (marine), POSPac GNSS-Inertial Tools manual (POSPac only).

## Known reference gaps

- **PUBS-ICD-003759** (POS LV V5 User ICD) not found. Stand-in above matches the data, but
  any new group must be checked against real files before use.
- **Group 4 raw IMU format** is export-controlled and unpublished. Not decoded.
- **Convert to RINEX** 4.0.1.x converts the extracted `.rt27` stream correctly (tested), but
  needs the Trimble Office Configuration Utility installed first.
- **Ports** (from the V6 ICD): display UDP 5600, control 5601, real-time data 5602/5606,
  logging 5603. Confirmed on the ARAN59 V5 truck 2026-11-15: 5600 and 5603 (listen only; this
  app sends nothing to the PCS).
- **Antennas**: Applanix POS LV antennas are Trimble Zephyr 2 (`TRM57970.00` in NGS ngs20.atx);
  the POSView lever arm is to the antenna bottom (ARP). RTKLIB-EX 2.5.1 reads the antenna file
  from `file-rcvantfile` (not `file-rcvantp`).

## Sample inputs (not deliverables)

- POS logs: `F:\Camera Calibrations\WithLoops\2026\20260425_ARAN69_VA26_ Surveyor\Extrinsic\Loop\Posdata\2505xx\`
- CSV logs: `...\Old School\FL|FR|RL|ROW\<time>\*_Processed.csv`
- Verified facts: CSV Elevation = POS Group 1 altitude in **feet** (Group 1 altitude looks
  ellipsoidal: geoid separation here is -33.6 m - inference); CSV Heading = POS heading
  exactly; CSV Roll/Pitch differ from POS (other frame). Group 3 GNSS status during
  collection is 12/13, which the V6 ICD table does not define (VBS is 11 there).
- ARAN69 POS logs Time 1 and Time 2 as **UTC** (time-types byte 0x22); GPS-UTC = 18 s.
- Groups logged on the trucks: 1 (~50 Hz), 2, 3, 9, 10, 11, 99 (1 Hz), 4 and 10006 (200 Hz),
  10001 and 10009 (raw GNSS streams), plus 25 setup messages including Message 20.
- ARAN59 (2026-11-15) Ethernet logging: 1, 2, 3, 4, 9, 10, 11, 99, 10001, 10006 (no 10009).
  Real data on that PC: `F:\SurveyorPOS_20261114_183242` (parked 40 min) and
  `F:\ExtrinsicAran59_20261115_*` (Surveyor test runs).
