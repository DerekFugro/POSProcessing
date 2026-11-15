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
