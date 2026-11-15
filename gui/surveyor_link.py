"""stdin/stdout link to the app that started us (Surveyor). See docs/Comunication4OtherApps.md.

A reader thread turns each stdin line into a Qt signal (handled on the GUI thread). Output
lines are written with a lock and flushed at once. When stdin closes (the other app exited),
closed is emitted so recording can be stopped cleanly.

The installed app is a windowless exe. There Python may leave sys.stdin / sys.stdout as None
even when the parent passed pipes, so posproc.stdio opens the Windows standard handles.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

from posproc.control import CommandError, message, parse_command
from posproc.stdio import std_streams


class SurveyorLink(QObject):
    command = Signal(object)        # parsed command dict
    closed = Signal()               # stdin reached end of file

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._in, self._out = std_streams()

    @property
    def available(self) -> bool:
        """False when started without pipes (e.g. from Explorer): nothing to talk to."""
        return self._in is not None and self._out is not None

    def start(self):
        if not self.available or self._thread:
            return
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def send(self, msg_type: str, request: dict | None = None, **fields):
        if self._out is None:
            return
        line = message(msg_type, request, **fields)
        with self._lock:
            try:
                self._out.write(line + "\n")
                self._out.flush()
            except (OSError, ValueError, AttributeError):
                pass                   # the other app has gone; closed will follow

    def _read_loop(self):
        try:
            for line in self._in:
                if not line.strip():
                    continue
                try:
                    self.command.emit(parse_command(line))
                except CommandError as exc:
                    self.send("error", None, error=str(exc), line=line.strip()[:200])
        except (OSError, ValueError):
            pass
        self.closed.emit()
