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


def _live(align=0, gnss="OmniSTAR G2", nsv=29, rms=(0.35, 0.35, 0.45), ip="192.168.0.40"):
    from posproc.logger import LiveStatus

    return LiveStatus(align=align, gnss_status=gnss, nsv=nsv, rms_neu=rms, source_ip=ip)


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
