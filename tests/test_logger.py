"""Logger test: replay a sample .000 through a fake PCS and check the files are identical."""
import glob
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from pcs_simulator import serve  # noqa: E402

from posproc.logger import PcsLogger  # noqa: E402
from posproc.pcs import read_pcs  # noqa: E402

SAMPLES = os.environ.get("POSPROC_SAMPLES", "")
pytestmark = pytest.mark.skipif(not SAMPLES or not Path(SAMPLES).exists(), reason="sample data not set")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_logger_records_identical_stream(tmp_path):
    src = sorted(glob.glob(os.path.join(SAMPLES, "Extrinsic", "Loop", "Posdata", "250519", "LV_1_202505191146.03[01]")))
    port = _free_port()
    ready = threading.Event()
    threading.Thread(target=serve, args=([Path(f) for f in src], port, 0, True, ready), daemon=True).start()
    ready.wait(5)

    statuses = []
    lg = PcsLogger("127.0.0.1", tmp_path, port=port, file_size=4 * 1024 * 1024, on_status=statuses.append,
                   log=lambda m: None)
    t = threading.Thread(target=lg.run, daemon=True)
    t.start()
    want = sum(Path(f).stat().st_size for f in src)
    deadline = time.time() + 30
    while lg.watch.status.bytes_total < want and time.time() < deadline:
        time.sleep(0.2)
    lg.stop()
    t.join(10)

    files = [Path(f) for f in lg.watch.status.files]
    assert len(files) >= 4                                   # split at 4 MB
    got = b"".join(f.read_bytes() for f in files)
    assert got == b"".join(Path(f).read_bytes() for f in src)
    assert lg.watch.counts[1] > 1000 and lg.watch.counts[10001] > 0

    data = read_pcs(files)                                   # processing side reads them unchanged
    assert data.bad_records == 0 and len(data.g1) > 1000


def test_display_listener_reads_health():
    from pcs_simulator import serve_display
    from posproc.display import DisplayListener, health_summary

    src = sorted(glob.glob(os.path.join(SAMPLES, "Extrinsic", "Loop", "Posdata", "250519", "LV_1_202505191146.030")))
    port = _free_port()
    got = []
    dl = DisplayListener(port=port, on_status=lambda s, age: got.append((s, age)), log=lambda m: None)
    t = threading.Thread(target=dl.run, daemon=True)
    t.start()
    time.sleep(0.5)
    stop = threading.Event()
    threading.Thread(target=serve_display, args=([Path(f) for f in src], "127.0.0.1", port, 20, stop),
                     daemon=True).start()
    deadline = time.time() + 15
    while time.time() < deadline and (not got or got[-1][0].rms_neu[0] != got[-1][0].rms_neu[0]):
        time.sleep(0.3)
    stop.set()
    dl.stop()
    t.join(5)
    s, age = got[-1]
    assert 0 < s.rms_neu[0] < 5 and s.nsv > 0 and s.align == 0
    h = health_summary(s, age)
    assert h["align"][0] == "Full navigation" and h["link"][1] == "good"
    assert s.source_ip == "127.0.0.1" and h["pcs"][0] == "127.0.0.1"
