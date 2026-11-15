# Communication for other apps (Surveyor) - POS recording control

How another program (the Surveyor extrinsic-calibration app) starts the POS PPK Processor,
makes it record the POS LV, checks it is OK before taking images, marks each image location,
and stops it. Written for the developer / agent building the Surveyor side.

Protocol version: **2** (the `hello` line says which). Version 2 adds an optional mark
`"folder"` and a summary in `mark_done` / `closed_marks`; every version 1 command and message
still works the same. Everything below was run against the real POS (ARAN, PCS at
192.168.0.40) on 2026-11-15 with a test client; the example transcripts are from those runs.

## The idea in one paragraph

Surveyor starts this app as a **child process** with **stdin/stdout pipes**. The app opens
its normal window (Record tab, locked, "Controlled by Surveyor") and **starts recording at
once** into a new folder `<drive>\SurveyorPOS_YYYYMMDD_HHMMSS\`. The two programs exchange
**one JSON object per line**. Surveyor sends commands (`status`, `mark`, `stop`, ...); the app
replies, and also sends a **heartbeat every 60 s**. Before collecting images at a location,
Surveyor asks for `status` and **only collects when `ok_to_collect` is true** (recording and
the status light is green). At each location Surveyor sends `mark`, and the app records the
POS navigation for 5 minutes into a CSV. The POS data is post-processed later (PPK) from the
recorded `.000` files; nothing is processed during the drive.

## Starting the app

Run the app's own Python from its folder - **not** `launch.bat` (it uses `start`, which
drops the pipes):

| Item | Value |
|---|---|
| Program | `E:\DP\POSProcessor\.venv\Scripts\pythonw.exe` |
| Arguments | `app.py --surveyor --out F:\` |
| Working folder | `E:\DP\POSProcessor` |
| stdin / stdout | pipes, text, UTF-8, one line per message (output is plain ASCII JSON) |

- `--out` is the **root** (drive or folder). The app makes `SurveyorPOS_YYYYMMDD_HHMMSS`
  inside it, exactly as when a person presses START RECORDING. The session name
  (`SurveyorPOS`) comes from the app's Settings tab.
- `.venv` must exist: run `launch.bat` once by hand on a new PC (it builds it).
- Use `pythonw.exe` so no console window opens. stderr is not used for messages.
- The app's saved manual "Save to" folder is **not** changed by `--out`.
- Only one app may control recording at a time; the PCS logging port takes one connection.
  Do not run a second copy, and do not record from POSView's Ethernet logging at the same time.

Minimal Python (Surveyor is Python 3.12 / PySide6; `QProcess` works the same way):

```python
import json, subprocess

APP = r"E:\DP\POSProcessor"
pos = subprocess.Popen([APP + r"\.venv\Scripts\pythonw.exe", "app.py", "--surveyor", "--out", "F:\\"],
                       cwd=APP, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

def send(cmd: dict):
    pos.stdin.write(json.dumps(cmd) + "\n")
    pos.stdin.flush()

# read on a background thread: for line in pos.stdout: msg = json.loads(line) ...
```

Read stdout continuously on its own thread (or `QProcess.readyReadStandardOutput`); if nobody
reads, the pipe fills up and the app blocks.

## Messages from the app (stdout)

Every message has `"type"` and `"time"` (PC local time, `YYYY-MM-DDTHH:MM:SS`). Replies also
echo `"id"` if the command had one. Unknown fields should be ignored (more may be added).

| type | When | Main fields |
|---|---|---|
| `hello` | first line after start | `protocol` (2), `pid`, `out`, `heartbeat_s` (60) |
| `logging` | once, when the first data is written (session folder exists) | status fields |
| `heartbeat` | every 60 s | status fields |
| `reply` | answer to a command | `cmd`, `ok`, `error` (if `ok` is false), plus fields per command |
| `mark_done` | a mark's CSV reached its time | `mark`, `label`, `file`, `seconds`, `rows`, `complete` (true) and the **mark summary** (below) |
| `stopped` | recording stopped without a `stop`/`quit` command (e.g. stdin closed) | `files`, `closed_marks`, `session_folder` |
| `error` | a line could not be understood, or recording could not start | `error`, sometimes `cmd`, `line` |
| `bye` | last line before the app exits | - |

### Status fields (in `logging`, `heartbeat`, and replies to `status`, `start`, `mark`)

| Field | Meaning |
|---|---|
| `logging` | true = connected to the PCS logging port and data is being written |
| `light` | `green`, `orange`, `red` or `grey` - the big light on the Record tab |
| `message` | the light's title, e.g. `POS ready`, `Wait - POS is aligning`, `No navigation from the POS` |
| `detail` | one plain sentence under the title |
| **`ok_to_collect`** | **`logging` and `light == "green"`. Collect images only when true.** |
| `session_folder` | full path of `...\SurveyorPOS_YYYYMMDD_HHMMSS` (empty until data is written) |
| `files` | number of `.000/.001...` files written so far (8 MB each) |
| `megabytes` | data written so far (about 130 MB per hour) |
| `recording_s` | seconds since recording started |
| `warnings` | list of logging-port warnings (missing groups, no data, zero lever arms) |
| `marks_active` | marks still recording: `[{"mark", "label", "rows", "file"}]` |
| `nav` | latest POS navigation: `time1_s`, `time_type`, `lat_deg`, `lon_deg`, `height_m`, `roll_deg`, `pitch_deg`, `heading_deg`, `speed_mps`, `align_status`, `rms_n_m`, `rms_e_m`, `rms_d_m` |

Numbers that are not available are `null`.

### What the light means

| light | message (examples) | Collect? |
|---|---|---|
| green | `POS ready` - Full navigation, GNSS 5+ satellites, position RMS under 1 m | yes |
| orange | `Wait - POS is aligning`, `Few satellites`, `Position accuracy low`, `POS lever arms not set`, `Recording, but data is missing`, `Recording, but no data` | no |
| red | `No data from the POS`, `No navigation from the POS`, `Not recording` | no |
| grey | `Looking for the POS...` (first 10 s after start) | no |

If the POS goes orange or red during the run, the app **keeps recording** and reports it in
the next heartbeat / reply. Surveyor decides what to do (wait, or skip the location).

## Commands to the app (stdin)

One JSON object per line. `"id"` is optional (any number or string) and is echoed back.

| Command | Example | Reply |
|---|---|---|
| `status` | `{"cmd":"status","id":1}` | `reply` with status fields |
| `start` | `{"cmd":"start","id":2}` | starts recording if it is not running (it starts by itself at launch; use this after `stop` or a start error). `ok` false + `error` if it cannot (e.g. drive missing) |
| `mark` | `{"cmd":"mark","label":"Position3","seconds":300,"folder":"F:\\Ext\\OldSchool\\Cam1\\Position3","id":3}` | see Marks below. `seconds` optional (default 300, max 3600). `folder` optional (protocol 2) |
| `stop` | `{"cmd":"stop","id":4}` | stops recording, closes the files cleanly, replies with `files`, `closed_marks` (each with its mark summary, `complete` false), `session_folder`. The app stays open |
| `quit` | `{"cmd":"quit","id":5}` | like `stop`, then the app closes (`bye`, exit code 0) |

A bad line gets `{"type":"error","error":"..."}` and is otherwise ignored.

**Closing:** if Surveyor exits or closes the app's stdin, the app treats that as `quit`: it
stops recording cleanly (`stopped`), sends `bye` and exits. **Do not kill the process** except
as a last resort - the last part of the current file may be lost. Allow up to ~10 s for `bye`.

## Marks (one per image location)

When Surveyor stops at a location, send `mark` with a label, and (protocol 2) the full path
of the folder where that position's photos go. The reply has the current status (check
`ok_to_collect`), and:

| Field | Meaning |
|---|---|
| `mark` | mark number in this recording (1, 2, 3 ...) |
| `label` | the label you sent |
| `file` | the CSV being written: `<folder>\NN_<label>.csv`, or `<session_folder>\marks\NN_<label>.csv` when no `folder` was given |
| `seconds` | how long it records |

**`folder` (protocol 2):** must be a full path to a folder that **already exists** (Surveyor
makes `<site>\<camera>\PositionN\` first). The app does not create it. If it is missing, not a
full path, or can't be written to, the reply is `ok: false` with a plain `error`, e.g.
`"Folder does not exist: F:\...\Position9"`, and no mark is started (the mark number is not
used up). Without `folder`, the CSV goes to `<session_folder>\marks\` as in protocol 1.

Files written:

- **The mark CSV** (`NN_<label>.csv`, in `folder` or `<session>\marks\`) - every POS
  navigation epoch (Group 1, about **50 Hz**, so 15,000 rows for 5 minutes) from the mark for
  `seconds`. Columns:
  `time1_s, time_type, lat_deg, lon_deg, height_m, roll_deg, pitch_deg, heading_deg,
  speed_mps, align_status, rms_n_m, rms_e_m, rms_d_m, light, message`.
  `light` / `message` are the Record-tab light at that moment (it updates about once a
  second). When the time is up the app sends `mark_done`. If recording stops first, the CSV
  is closed early and listed in `closed_marks` with `"complete": false`.
- **`<session_folder>\marks.csv`** - one line per mark, wherever its CSV went:
  `mark, label, file` (**full path**), `start_time1_s`, and the navigation, `light` and
  `message` at the moment of the mark.

### Mark summary (in `mark_done` and in each `closed_marks` entry)

So Surveyor can rate a position without reading the CSV:

| Field | Meaning |
|---|---|
| `rows` | readings in the CSV |
| `complete` | true = ran its full `seconds`; false = cut short by stop/quit/recording ending |
| `green_whole_time` | true if the light was green for every reading (false if there were no readings) |
| `worst_light` | worst light seen: `red` > `grey` > `orange` > `green` |
| `worst_message` | the light's message when it was worst (first time it reached that level) |
| `max_rms_n_m`, `max_rms_e_m`, `max_rms_d_m` | largest POS position RMS north / east / down, metres |
| `roll_spread_deg`, `pitch_spread_deg`, `heading_spread_deg` | max minus min over the mark, degrees (heading is handled across north: 359.95 and 0.05 is a 0.10 spread). Near 0 = the van did not move |
| `time_type` | `UTC` or `GPS` (see below) |
| `first_time1_s`, `last_time1_s` | POS Time 1 of the first and last reading (seconds of week) |

Values that could not be worked out (no readings) are `null`. For reference, a parked van on
2026-11-15 gave about 0.01-0.02 deg spreads over 15 s and RMS 0.35 / 0.35 / 0.44 m (OmniSTAR).

### Answers to Surveyor's questions (2026-11-15)

- **Is `height_m` ellipsoidal height in metres?** Yes - height above the ellipsoid
  (WGS84 / ITRF), metres, from POS Group 1 (the "altitude" field). Checked: on the
  2026-11-14 recording the real-time height agreed with the post-processed *ellipsoidal*
  height to 0.2 m; an orthometric height would differ by the geoid (about 33 m there).
- **Is the position the POS reference point, or does it include a lever arm?** It is the POS
  **reference point** as set up in POSView. This app adds no lever arm to the mark values.
  Checked: post-processing adds the POS's ref->GNSS lever arm (about 7 m: -6.20, -0.81, -3.09 m) to these values to
  get the antenna, and the result agreed with PPK to 0.28 m - which only works if the values
  are at the reference point. Attitude is the POS vehicle frame as set in POSView.
- **Does `seconds: 300` always give a full 5 minutes while recording keeps running?** Yes,
  timed by the POS clock (Time 1) from the first reading after the mark - about 15,000 rows,
  then `mark_done` with `complete: true`. Two cases end it differently: if the POS stops
  sending navigation (e.g. standby), the mark still ends on time by the PC clock (5 s grace),
  with fewer rows - check `rows` and `green_whole_time`; and if recording stops first, the mark
  is cut short and comes back in `closed_marks` with `complete: false`.

Notes on the values:

- `time1_s` is the POS **Time 1, seconds of the week**. `time_type` says which clock:
  `UTC` on the ARAN trucks (as of 2026-11-15), or `GPS`. GPS = UTC + 18 s (leap seconds).
  The POS sets this in POSView, so always read `time_type`.
- Positions and attitude are the POS **real-time** solution at the POS reference point
  (OmniSTAR, about 0.35 m). They tie images to POS time; the accurate trajectory comes later
  from post-processing the `.000` files on the Process tab / `posproc.cli`.
- `label` is kept exactly in the reply and `marks.csv`; in the file name, characters other
  than letters, digits, `_ . -` become `_`.
- Several marks can run at once (each has its own CSV).

## Example: protocol 2 marks into position folders (real run, paths shortened)

```
<- {"type":"hello","protocol":2,...}
<- {"type":"logging","light":"green","message":"POS ready","ok_to_collect":true,...}
-> {"cmd":"mark","label":"Position1","seconds":15,"folder":"F:\\Ext\\OldSchool\\Cam1\\Position1","id":1}
<- {"type":"reply","cmd":"mark","id":1,"ok":true,"mark":1,"label":"Position1",
    "file":"F:\\Ext\\OldSchool\\Cam1\\Position1\\01_Position1.csv","seconds":15.0,"ok_to_collect":true,...}
-> {"cmd":"mark","label":"Position9","folder":"F:\\Ext\\missing","id":2}
<- {"type":"reply","cmd":"mark","id":2,"ok":false,"error":"Folder does not exist: F:\\Ext\\missing",...}
<- {"type":"mark_done","mark":1,"label":"Position1","file":"...\\Position1\\01_Position1.csv","rows":750,
    "complete":true,"green_whole_time":true,"worst_light":"green","worst_message":"POS ready",
    "max_rms_n_m":0.353,"max_rms_e_m":0.354,"max_rms_d_m":0.441,"roll_spread_deg":0.0095,
    "pitch_spread_deg":0.0092,"heading_spread_deg":0.0164,"time_type":"UTC",
    "first_time1_s":73090.32,"last_time1_s":73105.30,"seconds":15.0}
-> {"cmd":"mark","label":"Position2","folder":"F:\\Ext\\OldSchool\\Cam1\\Position2","id":3}
-> {"cmd":"quit","id":4}
<- {"type":"reply","cmd":"quit","id":4,"ok":true,"closed_marks":[{"mark":2,"label":"Position2",
    "rows":300,"complete":false,"green_whole_time":true,"worst_light":"green",...}],...}
<- {"type":"bye"}
```

## Example session, protocol 1 (real run, paths shortened)

```
<- {"type":"hello","protocol":1,"pid":1234,"out":"F:\\","heartbeat_s":60,...}
<- {"type":"logging","logging":true,"light":"green","message":"POS ready","ok_to_collect":true,
    "session_folder":"F:\\SurveyorPOS_20261115_144536","files":1,"megabytes":0.0,"warnings":[],
    "nav":{"time1_s":67602.998,"time_type":"UTC","lat_deg":37.6055085,"lon_deg":-77.4918355,...},...}
-> {"cmd":"status","id":1}
<- {"type":"reply","cmd":"status","id":1,"ok":true,"logging":true,"light":"green","ok_to_collect":true,...}
-> {"cmd":"mark","label":"Location 1","seconds":15,"id":3}
<- {"type":"reply","cmd":"mark","id":3,"ok":true,"mark":1,"label":"Location 1",
    "file":"F:\\SurveyorPOS_20261115_144536\\marks\\01_Location_1.csv","seconds":15.0,"ok_to_collect":true,...}
<- {"type":"mark_done","mark":1,"label":"Location 1","file":"...\\01_Location_1.csv","rows":750}
<- {"type":"heartbeat","logging":true,"light":"green","ok_to_collect":true,"megabytes":2.2,...}
-> {"cmd":"stop","id":5}
<- {"type":"reply","cmd":"stop","id":5,"ok":true,"session_folder":"F:\\SurveyorPOS_20261115_144536",
    "files":["F:\\SurveyorPOS_20261115_144536\\LV_1_202611151445.000"],
    "closed_marks":[{"mark":2,"label":"Location 2","rows":...,"complete":false}]}
-> {"cmd":"quit","id":6}
<- {"type":"reply","cmd":"quit","id":6,"ok":true,...}
<- {"type":"bye"}
```

## Suggested Surveyor flow

1. Start the app with `--out <drive>`; wait for `hello`, then `logging` (normally a few
   seconds). If `logging` has not come within ~30 s, or an `error` with `"cmd":"start"` arrives,
   show the error and stop.
2. Watch heartbeats. If none for 2-3 minutes, treat the POS app as gone.
3. At each position: make its folder, send `status`; if `ok_to_collect` is false, show
   `message` / `detail` to the crew and wait. When true, send `mark` with the label and
   `folder`, then take images. When `mark_done` arrives, rate the position from the summary
   (e.g. require `green_whole_time` and small spreads).
4. At the end: send `stop` (or `quit`). Save `session_folder` with the calibration job - it is
   the input for post-processing later.

## Where this lives in the code

| File | What |
|---|---|
| `app.py` | `--surveyor --out` arguments |
| `posproc/control.py` | line protocol: command checking, JSON lines (no Qt; tested) |
| `posproc/marks.py` | mark CSVs, `marks.csv` and the mark summary (tested) |
| `gui/surveyor_link.py` | stdin reader thread / stdout writer |
| `gui/main_window.py` | command handling, heartbeat, status fields, clean shutdown |
| `tests/test_units.py` | protocol, marks, Group 1 parsing vs the file reader |
