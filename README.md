# POS PPK Processor

Post-processes Applanix POS LV real-time data (PCS logs `LV_1_*.000, .001 ...`) against
NOAA CORS base stations and writes a WGS84 trajectory, an RMS report, a map, and
post-processed copies of ARAN real-time CSV GPS logs. Built for extrinsic camera
calibrations where the truck collected with OmniSTAR VBS real-time corrections.

## What it does

1. **Reads the POS files** (Group 1 navigation, Group 2 RMS, Group 3 GNSS status,
   Group 99 versions, Message 20 lever arms, Group 10001 raw GNSS).
2. **Pulls out the raw GNSS stream** (Trimble BD960 RT27) and converts it to RINEX with
   **Trimble Convert to RINEX**.
3. **Downloads the nearest NOAA CORS station** (daily RINEX + broadcast orbits) from the
   NOAA open-data bucket, with the station's ITRF2020 position moved to the survey date.
4. **Runs carrier-phase PPK** with **RTKLIB rnx2rtkp** (forward + backward).
5. **Blends** the PPK antenna fixes with the real-time trajectory: the real-time position
   error is modelled as a slow drift and removed with a Kalman filter + backward smoother.
   The IMU motion already inside the real-time solution carries the trajectory between
   fixes. **Attitude is not re-processed** (raw IMU data is export-controlled and can't be
   decoded without POSPac).
6. **Writes** `<session>_PPK_trajectory_WGS84.csv`, `PPK_Report.html`, and for each CSV log
   `<name>_PPK.csv` (same columns; only Latitude, Longitude, Elevation replaced).
7. **Live POS tab:** POS health and a live map from the PCS display broadcast, and logging of the
   PCS Ethernet logging port to `.000` files on this PC.

## Setup (once per PC)

1. Python 3.12 (`py -3.12` must work).
2. From `installers\` (kept on the PC/share, not in git), install in this order:
   1. `TrimbleCFGUpdate.exe` - Trimble Office Configuration Utility. **Required**: without it
      Convert to RINEX crashes at start-up ("type initializer ... threw an exception").
   2. `convertToRinex_v4_0_1_10_sign.msi` - Trimble Convert to RINEX. Installs to
      `C:\Program Files (x86)\Trimble\ConvertToRinex\convertToRinex.exe` (the default in Settings).
3. RTKLIB-EX 2.5.1 (github.com/rtklibexplorer/RTKLIB): unzip and put `rnx2rtkp.exe` in
   `tools\rtklib\` (or set its path in Settings).
4. Double-click `launch.bat` (first run builds `.venv` and installs packages).

## Use

1. **Add files / Add folder** for the POS files (a folder is searched with subfolders).
2. Optional: add CSV GPS logs (`FL 1.csv`, `ReviewData_*.csv` ...). Files already ending in the
   output suffix are skipped by *Add folder*.
3. Pick an output folder and trajectory rate, press **Run**.
4. Check **Report**, **Map** and **Log** tabs. Orange items in the report need a look.

Command line (same steps, no GUI):
`.venv\Scripts\python.exe -m posproc.cli --pcs "<POS folder>" --csv "<CSV folder>" --out "<output>" --rate 10`

## Outputs

| File | Contents |
|---|---|
| `PPK_Report.html` | Before/after accuracy, PPK fix rate, base used, correction plots, CSV match table |
| `<session>\<session>_PPK_trajectory_WGS84.csv` | GPS week/seconds, lat, lon, ellipsoid height, roll/pitch/heading (real-time), sigma N/E/D, correction N/E/Up, PPK-covered flag |
| `csv\<name>_PPK.csv` | Copy of each CSV log with only Latitude, Longitude, Elevation (feet) replaced |
| `<session>\rinex\`, `<session>_<BASE>.pos`, `rtklib.conf`, `.rt27` | Working files (rover RINEX, RTKLIB result, settings, raw GNSS) |

Downloaded CORS data and map tiles are cached once in `cache\`.

## Live POS tab

**POS health (UDP 5600 display port).** Starts listening when the app opens. Shows the same
~1 Hz data POSView shows: navigation/alignment status, GNSS mode and satellites, position RMS
(N/E/D), attitude RMS (roll/pitch/heading) and position, with green/orange/red dots
(position RMS green <= 0.5 m, orange <= 1 m; heading RMS green <= 0.1 deg, orange <= 0.5 deg).
The display port is a broadcast, so it runs alongside POSView and any logging.

**Live map.** The truck's position and trail (last hour) from the display data, coloured by
position-RMS health, with a heading tick. *Follow truck* keeps it centred; *Clear trail* resets it.

**Log to this PC (TCP 5603 Ethernet logging port).** Writes `LV_1_YYYYMMDDHHMM.000, .001 ...`
(8 MB parts) into `<folder>\YYMMDD\` - the same format as PCS USB logging. It only listens;
nothing is sent to the POS.

- The PCS address is found automatically from its display broadcast and filled in (default
  192.168.0.40 if nothing is heard yet). You can still type it.
- Logging status shows data rate, current file and each group's rate; it warns if group
  1, 2, 3 or 10001 is missing.
- Groups on the logging port are chosen in POSView (Logging > Ethernet Logging). Keep the same set
  the crews log to USB (1, 2, 3, 4, 9, 10, 11, 99, 10001, 10006, 10009) so POSPac can also use it.
- POSView can stay open as normal. Only the logging port itself takes one connection, so POSView's
  own Ethernet logging can't run while this app is logging.
- If the link drops, the logger reconnects by itself and keeps writing the same log. Data sent while
  disconnected may be lost (check the log), so keep PCS USB logging on as a backup.
- **Send logged files to processing** puts them in the POS file list; press Run.

Command line: `.venv\Scripts\python.exe -m posproc.logger --host 192.168.0.40 --out C:\POSLogs`
(Ctrl+C to stop).

Test without a truck: `.venv\Scripts\python.exe tools\pcs_simulator.py --files "<folder>\LV_1_*.0*" --speed 1`
then log from host `127.0.0.1` (the simulator also sends display data to UDP 5600).

## Maps and offline use

Both maps (Live POS and the post-processing Map tab) use OpenStreetMap tiles through a small
tile cache built into the app. Leaflet is bundled, so nothing else is loaded from the internet.
Each tile is downloaded once when first viewed and saved in `cache\tiles_osm\`; after that it
works offline (e.g. in a truck with no internet). Cached tiles are refreshed after 30 days when
online. The app does not bulk-download areas - the OSM tile policy forbids that - so to have a
site available offline, look at it on the map once while online.

## How CSV rows are tied to time

The CSV has no time column. Each row's latitude, longitude, height and heading are the
real-time POS values, so the app finds the POS epoch with the same values (heading matches
to 1e-8 deg). PC time is not used. Roll/pitch in the CSV are in a different frame and are
ignored. Elevation units are detected (the ARAN logs are in **feet**). A file is flagged if
its rows match more than one visit to the same spot or match worse than the tolerance.

## Results so far and limits

Test data: ARAN69, 2025-05-19, Richmond VA, 175 minutes.

- Nearest working NOAA CORS is **LS02, ~39 km**; NOAA thins data older than 30 days to
  30-second spacing. Even so RTKLIB got **83% fixed / 17% float**.
- Real-time OmniSTAR accuracy (POS Group 2, median): 0.23 / 0.24 / 0.29 m N/E/D.
- Checking LS02 against a second base (LOYO, 51 km): horizontal agrees to 4.4 cm median
  (95% within 10 cm); heights differ by ~10 cm. **Realistic accuracy: ~3-5 cm horizontal,
  ~5-10 cm vertical.** Not yet checked against a surveyed point.
- The report's "After" numbers are RTKLIB's own sigmas (~7 mm) and are too optimistic.
- Antenna phase-centre models are not applied yet (likely cause of the ~10 cm height gap).
- Attitude is not re-processed (raw IMU format is export-controlled).
- Output frame is ITRF2020 at the epoch of the data, reported as WGS84 (G2296) - same at
  the centimetre level.
- The POS on ARAN69 logs **UTC** time; the app converts to GPS time using the leap seconds
  in Group 3.
- A different base (e.g. your own receiver) can be used via *Settings > Manual base RINEX + XYZ*.

## Tests

`run_tests.bat` (uses the ARAN69 2025-05-19 sample data). Developer: `pytest -q` with
`POSPROC_SAMPLES` set to the Surveyor sample folder; without it the sample-data tests skip.

## For developers

See `CLAUDE.md` (layout, conventions, verified facts, to-do list) and `docs/CONTEXT.md`
(references and data notes).
