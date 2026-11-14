"""Tests against real ARAN69 sample data. Set POSPROC_SAMPLES to the Surveyor folder.

Example (Windows):
  set POSPROC_SAMPLES=F:\\Camera Calibrations\\WithLoops\\2026\\20260425_ARAN69_VA26_ Surveyor
  pytest -q
"""
import glob
import os
from pathlib import Path

import numpy as np
import pytest

from posproc.csvlog import CsvMatcher
from posproc.fuse import antenna_positions, fuse
from posproc.geo import apply_ned
from posproc.pcs import read_pcs
from posproc.ppk import POS_DTYPE
from posproc.rt27 import rt27_epochs, scan_packets

SAMPLES = os.environ.get("POSPROC_SAMPLES", "")
pytestmark = pytest.mark.skipif(not SAMPLES or not Path(SAMPLES).exists(), reason="sample data not set")


@pytest.fixture(scope="module")
def pcs():
    files = sorted(glob.glob(os.path.join(SAMPLES, "Extrinsic", "Loop", "Posdata", "250519", "LV_1_202505191146.0[23]*")))
    return read_pcs(files)


def _fake_ppk(pcs, off_neu=(0.10, -0.20, 0.30)):
    g1 = pcs.g1
    alat, alon, ah = antenna_positions(g1, pcs.lever.ref_to_gnss1)
    s = np.arange(0, len(g1), 50)
    la, lo, hh = apply_ned(alat[s], alon[s], ah[s], off_neu[0], off_neu[1], -off_neu[2])
    pos = np.zeros(len(s), POS_DTYPE)
    pos["week"], pos["t"], pos["lat"], pos["lon"], pos["h"] = pcs.gps_week, g1["t"][s], la, lo, hh
    pos["q"], pos["sdn"], pos["sde"], pos["sdu"] = 1, 0.01, 0.01, 0.02
    return pos


def test_reader(pcs):
    assert len(pcs.g1) > 1000 and len(pcs.g2) > 10
    assert pcs.bad_records == 0
    assert pcs.lever.found and pcs.lever.ref_to_gnss1[2] < 0       # antenna above reference point
    assert pcs.gps_week == 2367 and pcs.receiver_type == 16
    assert np.all(np.diff(pcs.g1["t"]) > 0)
    assert pcs.time1_type == 2 and pcs.gps_utc_offset == 18.0   # ARAN69 logs UTC


def test_gnss_stream_has_rt27(pcs):
    assert rt27_epochs(scan_packets(bytes(pcs.gnss_stream))) > 100


def test_fusion_recovers_constant_offset(pcs):
    f = fuse(pcs.g1, pcs.lever.ref_to_gnss1, _fake_ppk(pcs), pcs.gps_week)
    mean = f.correction[f.covered].mean(axis=0)
    assert np.allclose(mean, [0.10, -0.20, -0.30], atol=0.005)
    assert f.ppk_rejected == 0


def test_csv_matching_and_output(pcs, tmp_path):
    f = fuse(pcs.g1, pcs.lever.ref_to_gnss1, _fake_ppk(pcs), pcs.gps_week)
    m = CsvMatcher(pcs.g1, f)
    csv_path = Path(SAMPLES, "Old School", "FL", "20250519.132937", "FL 1_Processed.csv")
    r = m.process(csv_path, tmp_path, "_PPK", "auto", 0.05, 60)
    assert r.units == "ft"
    assert r.matched == r.rows
    assert r.median_resid_m < 0.01
    assert abs(r.mean_corr_neu_m[2] - 0.30) < 0.01
    out = r.out_path.read_text().splitlines()
    assert out[0] == csv_path.read_text(encoding="utf-8-sig").splitlines()[0]


def test_pipeline_end_to_end_with_stubbed_tools(pcs, tmp_path, monkeypatch):
    """Runs the whole pipeline with the external programs replaced by stubs."""
    from posproc import convert, cors, pipeline, ppk
    from posproc.config import Settings

    fake = _fake_ppk(pcs)
    obs = tmp_path / "rover.25o"
    obs.write_text("x")
    monkeypatch.setattr(convert, "convert_rt27", lambda *a, **k: (obs, []))
    st = cors.Station("TEST", (1107719.29, -4960162.031, 3840686.239), (0, 0, 0), "Operational", dist_km=38.6)
    monkeypatch.setattr(pipeline.Pipeline, "_bases",
                        lambda self, *a: [cors.BaseData(st, [obs], [], st.xyz2020, 30.0)])
    monkeypatch.setattr(ppk, "run_rnx2rtkp", lambda *a, **k: obs)
    monkeypatch.setattr(ppk, "read_pos", lambda p: fake)
    files = [str(f) for f in pcs.files]
    csvs = [str(Path(SAMPLES, "Old School", "FL", "20250519.132937", "FL 1_Processed.csv"))]
    res = pipeline.Pipeline(Settings(cache_dir=str(tmp_path / "c"))).run(files, csvs, tmp_path / "out")
    sr = res.sessions[0]
    assert not sr.error, sr.error
    assert sr.trajectory_csv.exists() and res.report_html.exists()
    assert res.csv[0].matched == res.csv[0].rows
    assert "Before: real-time POS" in res.report_html.read_text()
