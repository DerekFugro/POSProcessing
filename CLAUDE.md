# CLAUDE.md - POS PPK Processor

Guide for Claude (or any coding agent) working in this repo. Read this first, then
`docs/CONTEXT.md` for references and verified data facts, and `README.md` for user-facing
behaviour.

## What this app is

A Windows PySide6 app (plus CLI) that post-processes Applanix **POS LV** real-time logs
(`LV_1_YYYYMMDDHHMM.000, .001 ...`) against **NOAA CORS** base data, for ARAN truck camera
extrinsic calibrations. It also listens live to the PCS (health on UDP 5600, logging on
TCP 5603). Owner: Derek Pescod (hardware / field). Repo: github.com/DerekFugro/POSProcessing.

## Working rules (from Derek)

- Before writing or changing code: answer every question he asked first. If a question would
  change how the code is written, ask and **wait** - no code in the same reply.
- Short, plain answers (grade 9). Lead with the answer. Say what is verified, inferred or guessed.
- Never claim something is done or tested unless it was actually run.
- Never invent ICD layouts, file formats or data. Check against real files before trusting a field.
- Git: commit straight to `main` and push when Derek asks - no branches or pull requests (one
  PC works on this app; decided 2026-11-15). Git identity is set in this repo's config. The
  GitHub repo is public: never commit installers, Applanix manuals or other third-party files.

## Layout

| Path | Contents |
|---|---|
| `posproc/` | Processing library, no Qt. Import-safe for CLI and tests. |
| `posproc/pcs.py` | POS file reader: Groups 1, 2, 3, 99, 10001; Message 20; time conversion |
| `posproc/rt27.py` | Pull Group 10001 (Trimble RT27) out to a `.rt27` file, packet stats |
| `posproc/convert.py` | Runs Trimble `convertToRinex.exe` (RT27 -> RINEX 3.04) |
| `posproc/cors.py` | NOAA CORS station list, nearest-station pick, download (S3 bucket) |
| `posproc/antenna.py` | NGS ANTEX download/cache, antenna names from RINEX, offsets |
| `posproc/ppk.py` | Writes RTKLIB config, runs `rnx2rtkp`, reads `.pos` |
| `posproc/multibase.py` | Compares PPK from the 3 nearest bases, throws out wrong fixes, merges, realistic accuracy |
| `posproc/fuse.py` | Kalman + RTS smoother of (PPK antenna - real-time antenna) offset |
| `posproc/csvlog.py` | Ties ARAN CSV rows to POS time, writes `<name>_PPK.csv` |
| `posproc/pipeline.py` | Orchestration, per-session results, trajectory CSV |
| `posproc/report.py` | HTML report |
| `posproc/logger.py` | TCP 5603 logger + live stream parser (`_StreamWatcher`) |
| `posproc/display.py` | UDP 5600 display listener + health grading |
| `packaging/` | Installer: `build.ps1` (PyInstaller + Inno Setup -> `packaging\Output\POSProcessing_Setup_<ver>.exe`), `POSProcessing.spec`, `POSProcessing.iss`. Installed: `ACSApps\POSProcessing`; data: `ProgramData\Fugro Roadware\POSProcessing` (`posproc/config.py`) |
| `posproc/stdio.py` | stdin/stdout for the windowless exe (`--surveyor`, `--cli`) |
| `posproc/marklog.py` | After processing: mark CSVs get post-processed positions (original -> `.csv.old`) + per-mark report |
| `posproc/control.py`, `posproc/marks.py` | Line protocol and mark CSVs for apps that control recording (Surveyor) - see `docs/Comunication4OtherApps.md` |
| `posproc/cli.py` | `python -m posproc.cli --pcs ... --csv ... --out ...` |
| `posproc/config.py` | `Settings` dataclass <-> `settings.json` (per PC, not in git) |
| `gui/` | PySide6 GUI, four tabs: `main_window.py` (tabs + locking while busy), `record_panel.py` (Record: status light, folder, Start/Stop, live map), `process_panel.py` (Process: files, Run, Map/Report/Log), `logger_panel.py` (Diagnostics, read-only; also owns the recording engine), `settings_panel.py` (Settings: Recording + Processing), `surveyor_link.py` (stdin/stdout link for `--surveyor`), `health_panel.py`, `map_view.py`, `tiles.py` (OSM tile cache server), `worker.py`, `styles.py` |
| `gui/web/leaflet/` | Bundled Leaflet 1.9.4 (from npm, checksum checked) |
| `tools/pcs_simulator.py` | Fake PCS: replays `.000` files on TCP 5603 and UDP 5600 |
| `tests/` | pytest; sample-data tests need `POSPROC_SAMPLES` |

Not in git (see `.gitignore`): `.venv/`, `cache/`, `settings.json`, `test_run/`,
`test_rt27/`, `installers/`, `tools/rtklib/*.exe`, `docs/reference/` (Applanix PDFs are
copyrighted - they live in `D:\Manuals`).

## Code conventions

- GUI classes use the 5-method `__init__`: `_create_widgets`, `_create_layouts`,
  `_connect_signals`, `_apply_styles`, `_load_initial_state`. Slots named
  `_on_<widget>_<signal>`. Constants at module top, no magic numbers.
- Dark theme colours only from `gui/styles.py`.
- Long work never on the GUI thread: `QThread` worker (processing) or a Python thread with a
  Qt signal bridge (logger, display listener). Keep it that way.
- `posproc` must not import Qt.

## Verified facts - do not "fix" these

- POS record: `$GRP`/`$MSG`, ushort id, ushort byte count (= length - 8), ends `$#`.
  26-byte time/distance header. Group 1 = 132, Group 2 = 80, Group 3 = 76 + 20*N bytes.
- **ARAN69 logs Time 1 as UTC, not GPS** (time-types byte 0x22). `pcs.py` reads the
  time-types byte and adds the Group 3 GPS-UTC offset (18 s). Getting this wrong gives
  metre-level errors while driving.
- Group 10001 holds Trimble RT27 (RAWDATA 0x57 record type 6). RTKLIB cannot read RT27;
  Trimble Convert to RINEX (4.0.1.x) can, but crashes without the **Trimble Office
  Configuration Utility** (`C:\Program Files (x86)\Common Files\Trimble\Config\`).
- Message 20 lever arm ref -> primary GNSS, vehicle frame X fwd / Y right / Z down.
- ARAN CSV: Elevation = POS Group 1 altitude in **feet**; Heading = POS heading exactly;
  CSV Roll/Pitch are a different frame - do not use them for matching. No time column.
- Raw IMU (Group 4) format is export-controlled; attitude is not re-processed.
- Antennas (Derek, 2026-11-15): all Applanix POS LV antennas are Trimble Zephyr 2 ->
  `TRM57970.00 NONE` (ngs20.atx: TRM55970.00 is a copy, TRM77970.00 within 6 mm). The POSView
  ref->GNSS lever arm is measured to the antenna **bottom (ARP)**, so RTKLIB solves for the ARP
  with ANTEX (`posproc/antenna.py`). RTKLIB-EX 2.5.1 option is `file-rcvantfile` -
  `file-rcvantp` is silently ignored. Convert to RINEX writes the rover antenna as `UNKNOWN_EXT`.
- Group 3 GNSS status 12/13 on this firmware is undefined in the V6 ICD (name unknown).
- ARAN59 POS (LV-220 S/N 10481, SW10.21): Time 1 = UTC (0x22), Group 1 at 50 Hz, Message 20 +
  setup messages 21-62 are sent each time a logger connects. The logging port 5603 served two
  loggers at once with full data (2026-11-15). Parked heading noise 0.1-0.2 deg over 5 min.

## Accuracy (ARAN69, 2025-05-19, base LS02 39 km, 30 s base data)

- RTKLIB: 83% fixed, 17% float. Real-time (Group 2) median RMS 0.23 / 0.24 / 0.29 m N/E/D.
- Two-base check (LS02 vs LOYO 51 km): horizontal median 4.4 cm, 95% < 10 cm; vertical
  ~10 cm offset between bases. Realistic: ~3-5 cm horizontal, ~5-10 cm vertical.
- RTKLIB formal sigmas (~7 mm) are ~10x optimistic. The report's "After" row still uses
  them - known issue, see To do.

## Running and testing

```
.venv\Scripts\python.exe -m pytest -q                       # unit tests (sample tests skip)
set POSPROC_SAMPLES=F:\Camera Calibrations\WithLoops\2026\20260425_ARAN69_VA26_ Surveyor
run_tests.bat                                                # all tests incl. sample data
.venv\Scripts\python.exe -m posproc.cli --pcs <Posdata\250519> --csv <Old School> --out test_run
.venv\Scripts\python.exe tools\pcs_simulator.py --files "<Posdata\250519\LV_1_*.03*>" --speed 1
```

A full PPK run of the 175-minute sample takes about 4 minutes (RTKLIB is most of it).
RINEX and `.pos` results are reused on re-runs when inputs are unchanged.

On the ARAN59 bench/van PC (2026-11-15): Python is `.uv\python` inside the project (AppData
written from Claude is private to Claude); the sample folder above is NOT there, so 7 sample
tests skip. Real data there: `F:\SurveyorPOS_20261114_183242` (parked 40 min, processes at 98%
fixed) and the Surveyor runs `F:\ExtrinsicAran59_*`. Installer: `packaging\build.ps1` ->
installed `C:\Program Files\Fugro Roadware\ACSApps\POSProcessing` (version 3fa0c3a);
`POSProcessing.exe --cli ...` processes without the window.

## To do (priority order)

1. Process a **driving** recording (so far only parked data) and the 2026-11-15 Surveyor runs
   on F: once NOAA posts that day. Record/Diagnostics and Surveyor control were tested on the
   ARAN59 truck 2026-11-15.
2. Check accuracy against the Richmond GCPs (`RichmondTennisClick.gcp`; need zone/units).
   Two-base check (LS02 vs CORB, 2026-11-14 parked): 0.7 / 0.3 / 3.1 cm with antenna calibrations.
3. Done 2026-11-16: every session runs against 3 bases (`posproc/multibase.py`); fixes that
   disagree are thrown out (floats used), Report shows realistic 95% accuracy from base-to-base
   agreement. 2026-11-15 short runs: LS02 fixes 1 m off -> now float, ~30-40 cm.
4. Antenna calibrations: done 2026-11-15 (Zephyr 2 + base from RINEX, NGS ngs20.atx).
5. Test POS units of other years / firmware.
6. Name GNSS mode 12; GPS week rollover inside a session; download base stations lazily.
7. Installer: built and installed on ARAN59 2026-11-15 (update over the old version tested).
   Not tested: a PC without the Trimble tools, uninstall. Setup is unsigned ("Unknown publisher").
8. Get an ARAN CSV log (`*_Processed.csv`) with its POS files onto the PC to test CSV matching
   (`tests/test_sample_data.py` needs POSPROC_SAMPLES).
9. SurveyorCalibration moves into `ACSApps` later (that project's change).
