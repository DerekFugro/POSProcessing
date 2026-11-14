"""Extract the raw Trimble receiver stream (Group 10001) and check what it holds.

Group 10001 carries the primary receiver's own output, cut into chunks. Joined
back together it is the same byte stream the BD960 sent: Trimble packets
(STX 0x02, status, type, length, data, checksum, ETX 0x03). Raw observations are
packet type 0x57 (RAWDATA); record type 6 is RT27 measurements.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

STX, ETX = 0x02, 0x03
RAWDATA = 0x57
PACKET_NAMES = {0x57: "RAWDATA", 0x40: "GENOUT/GSOF", 0x55: "RETSVDATA", 0x07: "STATUS"}


def scan_packets(stream: bytes) -> Counter:
    """Count Trimble packets as (type, record type) pairs."""
    counts: Counter = Counter()
    j, n = 0, len(stream)
    while j < n - 4:
        if stream[j] == STX:
            ln = stream[j + 3]
            end = j + 4 + ln + 2
            if end <= n and stream[end - 1] == ETX:
                typ = stream[j + 2]
                sub = stream[j + 4] if typ == RAWDATA and ln > 0 else None
                counts[(typ, sub)] += 1
                j = end
                continue
        j += 1
    return counts


def describe(counts: Counter) -> str:
    parts = []
    for (typ, sub), n in counts.most_common():
        name = PACKET_NAMES.get(typ, hex(typ))
        parts.append(f"{name}{'' if sub is None else f'/rec{sub}'}: {n}")
    return ", ".join(parts)


def rt27_epochs(counts: Counter) -> int:
    return counts.get((RAWDATA, 6), 0)


def write_stream(stream: bytes, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = bytes(stream)
    if out_path.exists() and out_path.stat().st_size == len(data) and out_path.read_bytes() == data:
        return out_path            # unchanged: keep the old file so later steps can be reused
    out_path.write_bytes(data)
    return out_path
