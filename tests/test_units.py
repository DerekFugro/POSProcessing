import numpy as np

from posproc import geo
from posproc.cors import parse_coord_file
from posproc.fuse import _smooth
from posproc.ppk import read_pos

POS_SAMPLE = """% program   : RTKLIB
%  GPST                  latitude(deg) longitude(deg)  height(m)   Q  ns   sdn(m)   sde(m)   sdu(m)  sdne(m)  sdeu(m)  sdun(m) age(s)  ratio
2367 149380.000   37.605434170  -77.491701220    28.6300   1  12   0.0040   0.0030   0.0100   0.0010   0.0000   0.0010   1.00    9.9
2367 149381.000   37.605434171  -77.491701221    28.6310   2  11   0.0500   0.0400   0.0900   0.0010   0.0000   0.0010   2.00    1.5
"""

COORD_SAMPLE = """ SITE   EPOCH        x             y             z          Vx      Vy      Vz    Ctry.  State      Site
LS02  2020.00   1107719.290  -4960162.031   3840686.239   -14.8     0.6     3.0    US      VA      Operational
"""


def test_read_pos(tmp_path):
    p = tmp_path / "a.pos"
    p.write_text(POS_SAMPLE)
    pos = read_pos(p)
    assert len(pos) == 2 and pos["q"].tolist() == [1, 2] and pos["week"][0] == 2367
    assert abs(pos["ratio"][0] - 9.9) < 1e-9


def test_geo_roundtrip():
    x, y, z = geo.geodetic_to_ecef(37.6, -77.49, 28.6)
    lat, lon, h = geo.ecef_to_geodetic(x, y, z)
    assert abs(lat - 37.6) < 1e-9 and abs(lon + 77.49) < 1e-9 and abs(h - 28.6) < 1e-4


def test_ned_apply_inverse():
    lat, lon, h = geo.apply_ned(37.6, -77.49, 28.0, 1.5, -2.0, 0.3)
    dn, de, dd = geo.ned_offset(lat, lon, h, 37.6, -77.49, 28.0)
    assert np.allclose([dn, de, dd], [1.5, -2.0, 0.3], atol=1e-6)


def test_body_to_ned_heading():
    # forward lever arm with heading 90 deg points east
    v = geo.body_to_ned([0.0], [0.0], [90.0], (1.0, 0.0, 0.0))
    assert np.allclose(v[0], [0, 1, 0], atol=1e-12)


def test_coord_file_and_velocity():
    st = parse_coord_file(COORD_SAMPLE)["LS02"]
    x, y, z = st.xyz_at(2025.0)
    assert abs(x - (1107719.290 - 0.0148 * 5)) < 1e-6


def test_smoother_averages_noise():
    rng = np.random.default_rng(1)
    t = np.arange(200.0)
    z = np.column_stack([np.full(200, 0.5), np.zeros(200), np.zeros(200)]) + rng.normal(0, 0.05, (200, 3))
    xs, ps = _smooth(t, z, np.full((200, 3), 0.05 ** 2), 1e-6)
    assert abs(xs[:, 0].mean() - 0.5) < 0.01 and np.all(ps < 0.05 ** 2)


def test_session_folder_name_and_no_overwrite(tmp_path):
    import datetime as dt

    from posproc.logger import session_folder

    when = dt.datetime(2026, 11, 14, 13, 45, 7)
    first = session_folder(tmp_path, "SurveyorPOS", when)
    assert first == tmp_path / "SurveyorPOS_20261114_134507"
    first.mkdir()
    assert session_folder(tmp_path, "SurveyorPOS", when).name == "SurveyorPOS_20261114_134507_2"


def test_logger_writes_into_session_folder(tmp_path):
    import socket
    import threading
    import time

    from posproc.logger import PcsLogger

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        conn, _ = srv.accept()
        conn.sendall(b"x" * 1000)
        time.sleep(1)
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    lg = PcsLogger("127.0.0.1", tmp_path, port=port, session_name="SurveyorPOS", log=lambda m: None)
    t = threading.Thread(target=lg.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while lg.watch.status.bytes_total < 1000 and time.time() < deadline:
        time.sleep(0.05)
    lg.stop()
    t.join(10)
    srv.close()

    folders = list(tmp_path.iterdir())
    assert len(folders) == 1 and folders[0].name.startswith("SurveyorPOS_")
    files = list(folders[0].glob("LV_1_*.000"))
    assert len(files) == 1 and files[0].stat().st_size == 1000
    assert lg.watch.status.session_folder == str(folders[0])


def _live(align=0, gnss="OmniSTAR G2", nsv=29, rms=(0.35, 0.35, 0.45), ip="192.168.0.40", nav_age_s=0.5):
    import time

    from posproc.logger import LiveStatus

    return LiveStatus(align=align, gnss_status=gnss, nsv=nsv, rms_neu=rms, source_ip=ip,
                      nav_time=time.time() - nav_age_s)


def test_record_status_light():
    from posproc.display import Health, record_status
    from posproc.logger import LiveStatus

    assert record_status(_live(), 0.5, 60)[:2] == (Health.GOOD, "POS ready")
    assert record_status(_live(rms=(0.6, 0.6, 0.9)), 0.5, 60)[0] == Health.GOOD     # 0.85 m: still under 1 m
    assert record_status(_live(rms=(0.9, 0.9, 0.5)), 0.5, 60)[:2] == (Health.WARN, "Position accuracy low")
    assert record_status(_live(align=1), 0.5, 60)[:2] == (Health.WARN, "Wait - POS is aligning")
    assert record_status(_live(nsv=3), 0.5, 60)[:2] == (Health.WARN, "Few satellites")
    assert record_status(_live(gnss=""), 0.5, 60)[:2] == (Health.WARN, "Waiting for GNSS")
    # nothing heard yet: grey for the first seconds, then red
    assert record_status(LiveStatus(), float("inf"), 2)[0] == Health.NONE
    assert record_status(LiveStatus(), float("inf"), 30)[:2] == (Health.BAD, "No data from the POS")
    assert record_status(_live(), 8.0, 60)[0] == Health.BAD                          # data stopped


def test_record_status_while_recording():
    from posproc.display import Health, record_status
    from posproc.logger import LiveStatus

    ok_log = LiveStatus(connected=True)
    assert record_status(_live(), 0.5, 60, True, ok_log)[0] == Health.GOOD
    lost = LiveStatus(connected=False, error="Connection lost")
    assert record_status(_live(), 0.5, 60, True, lost)[:2] == (Health.BAD, "Not recording")
    missing = LiveStatus(connected=True, warnings=["Not receiving group 10001 (raw GNSS)"])
    assert record_status(_live(), 0.5, 60, True, missing)[:2] == (Health.WARN, "Recording, but data is missing")
    quiet = LiveStatus(connected=True, since_data_s=9, warnings=["No data for 9 s"])
    assert record_status(_live(), 0.5, 60, True, quiet)[:2] == (Health.WARN, "Recording, but no data")


def test_record_panel_helpers(tmp_path):
    from gui.record_panel import drive_problem, duration_text, free_space_text

    assert duration_text(59) == "0 min" and duration_text(12 * 60 + 5) == "12 min"
    assert duration_text(65 * 60) == "1 h 05 min"
    assert drive_problem("") == "Pick a folder to save recordings in."
    assert drive_problem(str(tmp_path)) == ""
    text, low = free_space_text(str(tmp_path / "not_made_yet"))
    assert "GB free" in text and "hours of recording" in text and low is False


def test_tabs_locked_while_busy():
    from gui.main_window import DIAGNOSTICS, PROCESS, RECORD, SETTINGS, locked_tabs

    assert locked_tabs(False, False) == {}
    assert set(locked_tabs(True, False)) == {PROCESS, SETTINGS}
    assert set(locked_tabs(False, True)) == {RECORD, SETTINGS}
    assert DIAGNOSTICS not in locked_tabs(True, True)


def _msg20(gnss, imu=(0.0, 0.0, 0.0)) -> bytes:
    """A Message 20 record with lever arms at the offsets the POS uses (13 IMU, 25 GNSS)."""
    import struct

    rec = bytearray(48)
    rec[0:4] = b"$MSG"
    struct.pack_into("<HH", rec, 4, 20, len(rec) - 8)
    struct.pack_into("<3f", rec, 13, *imu)
    struct.pack_into("<3f", rec, 25, *gnss)
    rec[-2:] = b"$#"
    return bytes(rec)


def test_zero_lever_arms_flagged():
    from posproc.display import Health, record_status
    from posproc.logger import LiveStatus, _StreamWatcher
    from posproc.pcs import LeverArms, lever_arms_from_msg20

    good = (-6.19597, -0.814121, -3.09098)
    assert not LeverArms((0, 0, 0), good, True).gnss_is_zero
    assert LeverArms((0, 0, 0), (0.0, 0.0, 0.0), True).gnss_is_zero
    assert not LeverArms().gnss_is_zero                          # not found is a different warning
    assert abs(lever_arms_from_msg20(_msg20(good), 0).ref_to_gnss1[0] - good[0]) < 1e-5

    w = _StreamWatcher()
    w.feed(b"junk" + _msg20((0.0, 0.0, 0.0)))
    assert w.status.lever_gnss == (0.0, 0.0, 0.0)
    log = LiveStatus(connected=True, lever_gnss=(0.0, 0.0, 0.0))
    assert record_status(_live(), 0.5, 60, True, log)[:2] == (Health.WARN, "POS lever arms not set")
    log.lever_gnss = good
    assert record_status(_live(), 0.5, 60, True, log)[0] == Health.GOOD


def test_no_navigation_detected():
    """POS sends display data (GNSS etc.) but no Group 1: seen on 2026-11-15 with the POS in standby."""
    from posproc.display import NO_NAV_TEXT, Health, health_summary, record_status
    from posproc.logger import LiveStatus

    stale = _live(nav_age_s=30)                     # last Full navigation 30 s ago, still in memory
    assert record_status(stale, 0.5, 60)[:2] == (Health.BAD, "No navigation from the POS")
    rows = health_summary(stale, 0.5)
    assert rows["align"] == (NO_NAV_TEXT, Health.BAD) and rows["pos_rms"][0] == "-"
    never = LiveStatus(gnss_status="OmniSTAR G2", nsv=32, source_ip="192.168.0.40")
    assert record_status(never, 0.5, 2)[0] == Health.NONE          # just started: still looking
    assert record_status(never, 0.5, 30)[:2] == (Health.BAD, "No navigation from the POS")
    assert health_summary(_live(), 0.5)["align"][1] == Health.GOOD  # fresh navigation unchanged


def test_logger_warns_when_needed_group_stops():
    from posproc.logger import LiveStatus, PcsLogger

    lg = PcsLogger("x", ".", log=lambda m: None)
    for g in (1, 2, 3, 10001):
        lg.watch.counts[g] = 100                     # all seen earlier in the recording
    s = LiveStatus(group_rates={1: 50.0, 2: 1.0, 3: 1.0, 10001: 4.0})
    assert lg._warnings(60, s) == []
    s.group_rates = {1: 0.0, 2: 0.0, 3: 1.0, 10001: 4.0}       # navigation stopped mid-recording
    w = lg._warnings(60, s)
    assert len(w) == 1 and "1 (navigation)" in w[0] and "2 (RMS)" in w[0]


def test_control_protocol_lines():
    import json

    import pytest

    from posproc.control import CommandError, message, parse_command

    assert parse_command('{"cmd":"status","id":7}') == {"cmd": "status", "id": 7}
    assert parse_command('{"cmd":"mark","label":"Location 3","seconds":300}')["label"] == "Location 3"
    for bad in ("", "hello", "[1]", '{"cmd":"jump"}', '{"cmd":"mark"}', '{"cmd":"mark","label":"x","seconds":-1}'):
        with pytest.raises(CommandError):
            parse_command(bad)
    out = json.loads(message("reply", {"cmd": "status", "id": "a1"}, ok=True, nav={"lat_deg": float("nan")}))
    assert out["type"] == "reply" and out["id"] == "a1" and out["nav"]["lat_deg"] is None and "time" in out


def test_mark_recorder_writes_csv_for_its_time(tmp_path):
    import csv

    from posproc.marks import NAV_COLUMNS, MarkRecorder, safe_label

    done = []
    rec = MarkRecorder(on_done=done.append)
    snap = {"time1_s": 1000.0, "lat_deg": 37.6, "light": "green", "message": "POS ready"}
    info = rec.start(tmp_path, "Location 3 / east", 2.0, snap)
    assert info["mark"] == 1 and safe_label("Location 3 / east") == "Location_3_east"
    row = dict.fromkeys(NAV_COLUMNS, 0.0)
    for k in range(0, 160):                     # 50 Hz for 3.2 s; mark is 2 s
        rec.add_nav({**row, "time1_s": 1000.0 + k * 0.02})
    assert len(done) == 1 and done[0]["rows"] == 101          # 0.00 .. 2.00 s inclusive
    with open(done[0]["file"], newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == NAV_COLUMNS and len(rows) == 102
    with open(tmp_path / "marks.csv", newline="") as fh:
        summary = list(csv.DictReader(fh))
    assert summary[0]["label"] == "Location 3 / east" and summary[0]["light"] == "green"
    rec.start(tmp_path, "Location 4", 300, snap)
    rec.add_nav({**row, "time1_s": 2000.0})
    closed = rec.close_all()                    # recording stopped early
    assert closed[0]["mark"] == 2 and closed[0]["complete"] is False


def test_watcher_group1_fields_match_file_reader():
    """Live parser's time/roll/pitch/heading agree with the file reader on a real recording."""
    from pathlib import Path

    import pytest

    from posproc.logger import _StreamWatcher, nav_row
    from posproc.pcs import read_pcs

    f = Path("F:/SurveyorPOS_20261114_183242/LV_1_202611141832.000")
    if not f.exists():
        pytest.skip("ARAN recording on F: not available")
    rows = []
    w = _StreamWatcher()
    w.on_nav = rows.append
    w.feed(f.read_bytes())
    g1 = read_pcs([f]).g1
    first = rows[0]
    assert first["time_type"] == "UTC"                    # file reader adds 18 s to make GPS time
    match = g1[abs(g1["t"] - 18.0 - first["time1_s"]) < 1e-6][0]
    for key, col in (("lat_deg", "lat"), ("lon_deg", "lon"), ("roll_deg", "roll"), ("pitch_deg", "pitch"),
                     ("heading_deg", "heading")):
        assert abs(first[key] - match[col]) < 1e-6, key
