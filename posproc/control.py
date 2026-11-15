"""Line protocol for other apps (Surveyor) that start this app and control recording.

One JSON object per line, UTF-8. Commands come in on stdin, replies and events go out on
stdout. Full description for other developers: docs/Comunication4OtherApps.md.
No Qt here, so the protocol can be tested on its own.
"""
from __future__ import annotations

import json
import math
import time

PROTOCOL_VERSION = 2          # 2: mark "folder" + summary in mark_done/closed_marks (v1 commands unchanged)
HEARTBEAT_S = 60.0
COMMANDS = ("status", "start", "mark", "stop", "quit")


class CommandError(ValueError):
    pass


def parse_command(line: str) -> dict:
    """Parse one input line into a command dict with at least 'cmd'. Raises CommandError."""
    line = line.strip()
    if not line:
        raise CommandError("empty line")
    try:
        msg = json.loads(line)
    except json.JSONDecodeError as exc:
        raise CommandError(f"not JSON: {exc.msg}") from None
    if not isinstance(msg, dict):
        raise CommandError("a command must be a JSON object")
    cmd = msg.get("cmd")
    if cmd not in COMMANDS:
        raise CommandError(f"unknown cmd {cmd!r}; use one of {', '.join(COMMANDS)}")
    if cmd == "mark":
        label = msg.get("label", "")
        if not isinstance(label, str) or not label.strip():
            raise CommandError("mark needs a non-empty 'label'")
        seconds = msg.get("seconds", None)
        if seconds is not None and (not isinstance(seconds, (int, float)) or seconds <= 0):
            raise CommandError("'seconds' must be a positive number")
        folder = msg.get("folder", None)
        if folder is not None and (not isinstance(folder, str) or not folder.strip()):
            raise CommandError("'folder' must be a full folder path")
    return msg


def _clean(v):
    """JSON-safe value: NaN/inf become null, tuples become lists."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


def message(msg_type: str, request: dict | None = None, **fields) -> str:
    """One output line. 'id' from the request is echoed so the caller can match replies."""
    out = {"type": msg_type, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), **fields}
    if request and "id" in request:
        out["id"] = request["id"]
    return json.dumps(_clean(out), separators=(",", ":"))
