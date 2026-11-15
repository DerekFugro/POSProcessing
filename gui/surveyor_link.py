"""stdin/stdout link to the app that started us (Surveyor). See docs/Comunication4OtherApps.md.

A reader thread turns each stdin line into a Qt signal (handled on the GUI thread). Output
lines are written with a lock and flushed at once. When stdin closes (the other app exited),
closed is emitted so recording can be stopped cleanly.
"""
from __future__ import annotations

import sys
import threading

from PySide6.QtCore import QObject, Signal

from posproc.control import CommandError, message, parse_command


class SurveyorLink(QObject):
    command = Signal(object)        # parsed command dict
    closed = Signal()               # stdin reached end of file

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def available(self) -> bool:
        """False when started without pipes (e.g. pythonw from Explorer): nothing to talk to."""
        return sys.stdin is not None and sys.stdout is not None

    def start(self):
        if not self.available or self._thread:
            return
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def send(self, msg_type: str, request: dict | None = None, **fields):
        line = message(msg_type, request, **fields)
        with self._lock:
            try:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
            except (OSError, ValueError, AttributeError):
                pass                   # the other app has gone; closed will follow

    def _read_loop(self):
        try:
            for line in sys.stdin:
                if not line.strip():
                    continue
                try:
                    self.command.emit(parse_command(line))
                except CommandError as exc:
                    self.send("error", None, error=str(exc), line=line.strip()[:200])
        except (OSError, ValueError):
            pass
        self.closed.emit()
