"""Updating Surveyor mark CSVs with post-processed positions (posproc/marklog.py)."""
import csv
import shutil
from types import SimpleNamespace

import numpy as np

from posproc import marklog
from posproc.marks import MARK_COLUMNS, SUMMARY_COLUMNS
from posproc.ppk import POS_DTYPE

LEAP = 18.0
WEEK = 2393
T0_UTC = 80798.5                  # first mark row, UTC seconds of week (as logged on ARAN59)
RATE = 50
N_ROWS = 500
SHIFT_LAT = 1e-5                  # post-processed minus real-time, degrees (~1.1 m north)


def _session(fixed=True, accuracy=True):
    t = (T0_UTC + LEAP - 10) + np.arange((N_ROWS + 1000)) / RATE      # GPS time, covers the mark
    n = len(t)
    pos = np.zeros(int(n / RATE), POS_DTYPE)
    pos["week"], pos["t"] = WEEK, t[0] + np.arange(len(pos))
    pos["q"] = 1 if fixed else 2
    fusion = SimpleNamespace(t=t, lat=np.full(n, 37.6 + SHIFT_LAT), lon=np.full(n, -77.49), h=np.full(n, 30.0),
                             sigma=np.full((n, 3), 0.007), covered=np.ones(n, bool))
    cc = SimpleNamespace(pos=pos, per_base={"LS02": {}, "CORB": {}},
                         accuracy={"horizontal": 0.04, "vertical": 0.09, "basis": "x"} if accuracy else {})
    pcs = SimpleNamespace(gps_utc_offset=LEAP, gps_week=WEEK)
    return SimpleNamespace(name="LV_1_X", fusion=fusion, cross_check=cc, pcs=pcs, station="LS02")


def _make_run(root, t0=T0_UTC):
    """<root>\\Run\\SurveyorPOS_x\\marks.csv + <root>\\Run\\ROW\\Position1\\01_ROW.csv"""
    session = root / "Run" / "SurveyorPOS_x"
    mark_dir = root / "Run" / "ROW" / "Position1"
    session.mkdir(parents=True)
    mark_dir.mkdir(parents=True)
    mark = mark_dir / "01_ROW_Position1.csv"
    with open(mark, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(MARK_COLUMNS)
        for k in range(N_ROWS):
            w.writerow([f"{t0 + k / RATE:.4f}", "UTC", "37.600000000", "-77.490000000", "30.100000000",
                        "0.3", "0.2", "68.1", "0.0", "0", "0.35", "0.35", "0.51", "green", "POS ready"])
    with open(session / "marks.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(SUMMARY_COLUMNS)
        w.writerow(["1", "ROW_Position1", str(mark)] + [""] * (len(SUMMARY_COLUMNS) - 3))
    return session, mark


def _rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_mark_csv_updated_original_kept(tmp_path):
    session, mark = _make_run(tmp_path)
    [mu] = marklog.update_marks([session], [_session()], log=lambda *_: None)
    assert mu.updated == N_ROWS and mu.quality == "fixed" and not mu.warnings
    old = mark.with_name(mark.name + ".old")
    assert _rows(old)[0]["lat_deg"] == "37.600000000"          # original untouched
    rows = _rows(mark)
    assert list(rows[0]) == MARK_COLUMNS                         # same format as before
    assert abs(float(rows[0]["lat_deg"]) - (37.6 + SHIFT_LAT)) < 1e-9
    assert float(rows[0]["height_m"]) == 30.0
    assert rows[0]["heading_deg"] == "68.1" and rows[0]["light"] == "green"   # attitude stays real-time
    assert abs(float(rows[0]["rms_n_m"]) - 0.04 / marklog.H95_TO_SIGMA) < 1e-6   # realistic, not 7 mm
    assert abs(mu.shift_neu[0] - (37.6 + SHIFT_LAT - 37.6) * np.pi / 180 * 6378137) < 0.01
    assert mu.report.exists()


def test_rerun_reads_the_original_again(tmp_path):
    session, mark = _make_run(tmp_path)
    marklog.update_marks([session], [_session()], log=lambda *_: None)
    [mu] = marklog.update_marks([session], [_session()], log=lambda *_: None)
    assert mu.updated == N_ROWS
    assert _rows(mark.with_name(mark.name + ".old"))[0]["lat_deg"] == "37.600000000"


def test_found_after_run_folder_is_moved(tmp_path):
    session, mark = _make_run(tmp_path / "F")
    shutil.move(str(tmp_path / "F" / "Run"), str(tmp_path / "Copy"))
    moved = tmp_path / "Copy" / "SurveyorPOS_x"
    [(label, path)] = marklog.find_marks(moved)
    assert path == tmp_path / "Copy" / "ROW" / "Position1" / mark.name


def test_copied_run_updates_the_copy_not_the_original(tmp_path):
    session, mark = _make_run(tmp_path / "F")
    shutil.copytree(tmp_path / "F" / "Run", tmp_path / "Copy")
    [mu] = marklog.update_marks([tmp_path / "Copy" / "SurveyorPOS_x"], [_session()], log=lambda *_: None)
    assert mu.path == tmp_path / "Copy" / "ROW" / "Position1" / mark.name
    assert not mark.with_name(mark.name + ".old").exists()      # original run untouched


def test_float_stop_written_with_warning(tmp_path):
    session, mark = _make_run(tmp_path)
    [mu] = marklog.update_marks([session], [_session(fixed=False)], log=lambda *_: None)
    assert mu.updated == N_ROWS and mu.quality == "float"
    assert any("float accuracy" in w for w in mu.warnings)


def test_mark_outside_processed_time_left_alone(tmp_path):
    session, mark = _make_run(tmp_path, t0=T0_UTC + 5000)
    before = mark.read_text(encoding="utf-8")
    [mu] = marklog.update_marks([session], [_session()], log=lambda *_: None)
    assert mu.updated == 0 and mu.quality == "not processed"
    assert mark.read_text(encoding="utf-8") == before
    assert not mark.with_name(mark.name + ".old").exists()
