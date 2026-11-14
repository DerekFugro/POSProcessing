"""Pretend to be a PCS: serve recorded .000 files on TCP 5603 (logging port) and send
~1 Hz display data on UDP 5600, so the logger and health display can be tested without a truck.

  python tools\\pcs_simulator.py --files "F:\\...\\Posdata\\250519\\LV_1_202505191146.00*" --speed 10
then point the logger at host 127.0.0.1.
"""
from __future__ import annotations

import argparse
import glob
import socket
import threading
import time
from pathlib import Path

REAL_RATE_BYTES_S = 42_000      # about what a POS LV 220 logging port sends (8 MB per ~200 s)
CHUNK = 16384


def serve(files: list[Path], port: int = 5603, speed: float = 1.0, once: bool = True,
          ready: threading.Event | None = None, host: str = "127.0.0.1"):
    data = b"".join(Path(f).read_bytes() for f in files)
    srv = socket.create_server((host, port), reuse_port=False)
    if ready:
        ready.set()
    try:
        while True:
            conn, addr = srv.accept()
            print(f"Client {addr} connected; sending {len(data) / 1e6:.1f} MB at {speed:g}x")
            try:
                delay = CHUNK / (REAL_RATE_BYTES_S * speed) if speed > 0 else 0
                for i in range(0, len(data), CHUNK):
                    conn.sendall(data[i:i + CHUNK])
                    if delay:
                        time.sleep(delay)
                print("All data sent.")
            except OSError as exc:
                print(f"Client dropped: {exc}")
            finally:
                conn.close()
            if once:
                break
    finally:
        srv.close()


def serve_display(files: list[Path], host: str = "127.0.0.1", port: int = 5600, speed: float = 1.0,
                  stop: threading.Event | None = None):
    """Send groups 1, 2, 3, 10, 11, 99 at ~1 Hz over UDP, like the PCS display port."""
    import struct
    data = b"".join(Path(f).read_bytes() for f in files)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    last: dict[int, float] = {}
    t_prev = None
    i = 0
    while not (stop and stop.is_set()):
        j = data.find(b"$GRP", i)
        if j < 0 or j + 8 > len(data):
            break
        gid, bc = struct.unpack_from("<HH", data, j + 4)
        end = j + 8 + bc
        if data[end - 2:end] != b"$#":
            i = j + 1
            continue
        i = end
        if gid not in (1, 2, 3, 10, 11, 99):
            continue
        t = struct.unpack_from("<d", data, j + 8)[0]
        if t - last.get(gid, -1e9) < 1.0:
            continue
        last[gid] = t
        if t_prev is not None and speed > 0 and t > t_prev:
            time.sleep(min((t - t_prev) / speed, 2.0))
        t_prev = t
        sock.sendto(data[j:end], (host, port))
    sock.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", required=True, help="glob of .000 files to replay")
    ap.add_argument("--port", type=int, default=5603)
    ap.add_argument("--speed", type=float, default=1.0, help="1 = real time, 0 = as fast as possible")
    ap.add_argument("--display-to", default="127.0.0.1", help="where to send UDP 5600 display data")
    a = ap.parse_args()
    files = [Path(f) for f in sorted(glob.glob(a.files))]
    if not files:
        raise SystemExit("No files matched.")
    threading.Thread(target=serve_display, args=(files, a.display_to, 5600, a.speed), daemon=True).start()
    serve(files, a.port, a.speed, once=False)


if __name__ == "__main__":
    main()
