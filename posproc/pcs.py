"""Reader for Applanix POS LV V5 logged data files (LV_1_YYYYMMDDHHMM.000, .001, ...).

Record layouts follow the Applanix User ICD (PUBS-ICD-003259 Rev 9.0, POS AV V6),
which was checked byte-for-byte against real POS LV 220 V5 (SW 09.03) logs:
  Group 1  = 132-byte navigation solution
  Group 2  = 80-byte performance metrics (RMS)
  Group 3  = 76 + 20*N primary GNSS status
  Group 99 = version string
  Group 10001 = raw primary GNSS receiver stream (Trimble RT27 for a BD960)
  Message 20 = installation parameters (lever arms)

Every record starts with b"$GRP" or b"$MSG", then ushort id, ushort byte count,
and ends with b"$#". Total record length is byte count + 8.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

GRP = b"$GRP"
MSG = b"$MSG"
END = b"$#"
HDR = 8          # $GRP + id + byte count
TIME_FIELDS = 26  # time1, time2, distance (3 doubles) + time types + distance type

G1_DTYPE = np.dtype([
    ("t", "f8"), ("lat", "f8"), ("lon", "f8"), ("h", "f8"),
    ("vn", "f4"), ("ve", "f4"), ("vd", "f4"),
    ("roll", "f8"), ("pitch", "f8"), ("heading", "f8"),
    ("speed", "f4"), ("align", "u1"),
])
G2_DTYPE = np.dtype([
    ("t", "f8"), ("n", "f4"), ("e", "f4"), ("d", "f4"),
    ("roll", "f4"), ("pitch", "f4"), ("heading", "f4"),
])
G3_DTYPE = np.dtype([("t", "f8"), ("status", "i1"), ("nsv", "u1"), ("week", "u4"), ("geoid", "f4")])

_G1 = struct.Struct("<d 16x 2x ddd fff dddd ff 12x 12x B")   # starts at record offset 8
_G2 = struct.Struct("<d 16x 2x ffffff ffffff")
_SESSION_RE = re.compile(r"^(?P<base>.+)\.(?P<seq>\d{3})$")


@dataclass
class LeverArms:
    """Lever arms from Message 20 (vehicle frame: X forward, Y right, Z down, metres)."""
    ref_to_imu: tuple[float, float, float] = (0.0, 0.0, 0.0)
    ref_to_gnss1: tuple[float, float, float] = (0.0, 0.0, 0.0)
    found: bool = False

    @property
    def gnss_is_zero(self) -> bool:
        """Message 20 present but ref->GNSS is 0, 0, 0: lever arms are not set in the POS.
        (Seen on S/N 10481 on 2026-11-15 after its settings changed; on 2026-11-14 it sent
        -6.20, -0.81, -3.09 m.) Processing would then put the antenna at the reference point."""
        return self.found and all(v == 0.0 for v in self.ref_to_gnss1)


ZERO_LEVER_WARNING = ("The POS reports a ref->GNSS lever arm of 0, 0, 0 m (Message 20): its lever arms "
                      "are probably not set. Positions would be off by several metres. Check the "
                      "installation settings in POSView.")


def lever_arms_from_msg20(buf: bytes, j: int) -> LeverArms:
    """Lever arms from a Message 20 record that starts at buf[j] ('$MSG')."""
    imu = struct.unpack_from("<3f", buf, j + 13)
    gnss = struct.unpack_from("<3f", buf, j + 25)
    return LeverArms(tuple(map(float, imu)), tuple(map(float, gnss)), True)


@dataclass
class PcsData:
    files: list[Path] = field(default_factory=list)
    g1: np.ndarray = field(default_factory=lambda: np.zeros(0, G1_DTYPE))
    g2: np.ndarray = field(default_factory=lambda: np.zeros(0, G2_DTYPE))
    g3: np.ndarray = field(default_factory=lambda: np.zeros(0, G3_DTYPE))
    lever: LeverArms = field(default_factory=LeverArms)
    version: str = ""
    receiver_type: int = -1
    gnss_stream: bytearray = field(default_factory=bytearray)
    bad_records: int = 0
    time1_type: int = -1          # 0 POS, 1 GPS, 2 UTC (from the time-types byte)
    gps_utc_offset: float = float("nan")   # leap seconds from Group 3
    time_note: str = ""

    @property
    def gps_week(self) -> int:
        weeks = self.g3["week"][self.g3["week"] > 0]
        return int(np.median(weeks)) if len(weeks) else 0

    @property
    def geoid_sep(self) -> float:
        return float(np.median(self.g3["geoid"])) if len(self.g3) else float("nan")


def group_sessions(paths: list[str | Path]) -> dict[str, list[Path]]:
    """Group LV_1_*.000/.001/... files into logging sessions, in sequence order."""
    sessions: dict[str, list[Path]] = {}
    for p in map(Path, paths):
        m = _SESSION_RE.match(p.name)
        if not m:
            raise ValueError(f"Not a POS log file (expected .000, .001 ...): {p.name}")
        sessions.setdefault(m.group("base"), []).append(p)
    for base in sessions:
        sessions[base].sort(key=lambda q: int(q.suffix[1:]))
    return dict(sorted(sessions.items()))


def read_pcs(paths: list[str | Path], progress=None) -> PcsData:
    """Read and concatenate POS log files of one or more sessions.

    Files of one session are joined byte-for-byte, because a record can be split
    across a .000/.001 boundary.
    """
    out = PcsData()
    g1, g2, g3 = [], [], []
    sessions = group_sessions(paths)
    total = sum(len(v) for v in sessions.values())
    done = 0
    for files in sessions.values():
        tail = b""
        for f in files:
            out.files.append(f)
            buf = tail + f.read_bytes()
            tail = _parse_buffer(buf, out, g1, g2, g3)
            done += 1
            if progress:
                progress(done, total, f.name)
    out.g1 = _sorted_unique(np.array(g1, dtype=G1_DTYPE))
    out.g2 = _sorted_unique(np.array(g2, dtype=G2_DTYPE))
    out.g3 = _sorted_unique(np.array(g3, dtype=G3_DTYPE))
    _to_gps_time(out)
    return out


def _to_gps_time(out: PcsData) -> None:
    """Make every group time GPS seconds of week. POS can be set to log Time 1 as UTC."""
    if out.time1_type == 1:
        out.time_note = "POS Time 1 is GPS time"
    elif out.time1_type == 2:
        off = out.gps_utc_offset
        if not np.isfinite(off) or off <= 0:
            raise ValueError("POS logged UTC time but no GPS-UTC leap-second offset was found (Group 3).")
        for a in (out.g1, out.g2, out.g3):
            if len(a):
                a["t"] += off
        out.time_note = f"POS Time 1 is UTC; converted to GPS time (+{off:g} s leap seconds)"
    elif out.time1_type == 0:
        raise ValueError("POS logged Time 1 as POS time (time since power-on); GPS time is needed.")


def _sorted_unique(a: np.ndarray) -> np.ndarray:
    if len(a) == 0:
        return a
    a = a[np.argsort(a["t"], kind="stable")]
    keep = np.ones(len(a), bool)
    keep[1:] = np.diff(a["t"]) > 0
    return a[keep]


def _parse_buffer(buf: bytes, out: PcsData, g1, g2, g3) -> bytes:
    """Parse all complete records in buf. Returns the unparsed tail."""
    i, n = 0, len(buf)
    while True:
        j = buf.find(b"$", i)
        if j < 0 or j + HDR > n:
            return buf[j:] if 0 <= j else b""
        tag = buf[j:j + 4]
        if tag not in (GRP, MSG):
            i = j + 1
            continue
        rid, bc = struct.unpack_from("<HH", buf, j + 4)
        end = j + HDR + bc
        if end > n:
            return buf[j:]            # record continues in next file
        if buf[end - 2:end] != END:
            out.bad_records += 1
            i = j + 1
            continue
        if tag == GRP:
            _handle_group(rid, buf, j, bc, out, g1, g2, g3)
        else:
            _handle_message(rid, buf, j, out)
        i = end


def _handle_group(gid, buf, j, bc, out: PcsData, g1, g2, g3):
    if out.time1_type < 0 and gid in (1, 2, 3):
        out.time1_type = buf[j + HDR + 24] & 0x0F
    if gid == 1 and bc == 132:
        v = _G1.unpack_from(buf, j + 8)
        # t, lat, lon, h, vn, ve, vd, roll, pitch, heading, wander, track, speed, align
        g1.append((v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8], v[9], v[12], v[13]))
    elif gid == 2 and bc == 80:
        v = _G2.unpack_from(buf, j + 8)
        g2.append((v[0], v[1], v[2], v[3], v[7], v[8], v[9]))
    elif gid == 3:
        t = struct.unpack_from("<d", buf, j + 8)[0]
        p = j + HDR + TIME_FIELDS
        status, nsv, cb = struct.unpack_from("<bBH", buf, p)
        q = p + 4 + cb
        if q + 36 <= j + HDR + bc:
            _h, _v, _lat, _ref, week, off, _ml, geoid, rtype = struct.unpack_from("<fffHLdffH", buf, q)
            g3.append((t, status, nsv, week, geoid))
            if off > 0:
                out.gps_utc_offset = off
            out.receiver_type = rtype
    elif gid == 99 and not out.version:
        raw = buf[j + HDR + TIME_FIELDS:j + HDR + bc]
        texts = re.findall(rb"[ -~]{6,}", raw)
        out.version = " | ".join(t.decode("ascii", "replace").strip() for t in texts)
    elif gid == 10001:
        p = j + HDR + TIME_FIELDS
        rtype, _res, nbytes = struct.unpack_from("<HlH", buf, p)
        out.receiver_type = rtype
        start = p + 8
        out.gnss_stream += buf[start:start + nbytes]


def _handle_message(mid, buf, j, out: PcsData):
    if mid == 20 and not out.lever.found:
        out.lever = lever_arms_from_msg20(buf, j)
