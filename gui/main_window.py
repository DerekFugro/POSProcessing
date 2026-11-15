"""Main window: four tabs - Record (live POS logging), Process (PPK of PCS files and CSV logs,
with map, report and log), Diagnostics and Settings. Opens on Record.

Started with --surveyor --out <drive>, another app controls recording through stdin/stdout
(docs/Comunication4OtherApps.md): recording starts at once, the Record tab is locked, a
heartbeat goes out every minute and status / start / mark / stop / quit commands are answered.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from posproc.config import Settings, app_version
from posproc.control import HEARTBEAT_S, PROTOCOL_VERSION
from posproc.display import Health
from posproc.logger import LiveStatus, nav_row
from posproc.marks import DEFAULT_MARK_SECONDS, MarkError

from . import styles
from .logger_panel import LoggerPanel
from .process_panel import ProcessPanel
from .record_panel import RecordPanel, drive_problem
from .settings_panel import SettingsPanel
from .surveyor_link import SurveyorLink

WINDOW_TITLE = "POS PPK Processor"
WINDOW_SIZE = (1400, 860)
RECORD, PROCESS, DIAGNOSTICS, SETTINGS = "Record", "Process", "Diagnostics", "Settings"
LOCKED_WHILE_RECORDING = "Locked while recording - stop recording on the Record tab first."
LOCKED_WHILE_PROCESSING = "Locked while processing - wait for it to finish or press Cancel."
CONTROLLER_NAME = "Surveyor"
LIGHT_NAMES = {Health.GOOD: "green", Health.WARN: "orange", Health.BAD: "red", Health.NONE: "grey"}
CLOSE_DELAY_MS = 200


def locked_tabs(recording: bool, processing: bool) -> dict[str, str]:
    """Tabs to grey out while busy, with the reason. Diagnostics is never locked (it only shows)."""
    locked = {}
    if recording:
        locked.update({PROCESS: LOCKED_WHILE_RECORDING, SETTINGS: LOCKED_WHILE_RECORDING})
    if processing:
        locked.update({RECORD: LOCKED_WHILE_PROCESSING, SETTINGS: LOCKED_WHILE_PROCESSING})
    return locked


class MainWindow(QMainWindow):
    def __init__(self, surveyor_out: str | None = None, parent=None):
        super().__init__(parent)
        self.surveyor_out = surveyor_out
        self._create_widgets()
        self._create_layouts()
        self._connect_signals()
        self._apply_styles()
        self._load_initial_state()

    # --- Widget Construction ---
    def _create_widgets(self):
        self.settings = Settings.load()
        self.main_tabs = QTabWidget()
        self.logger_panel = LoggerPanel(self.settings)
        self.record_panel = RecordPanel(self.settings, self.logger_panel)
        self.process_panel = ProcessPanel(self.settings)
        self.settings_panel = SettingsPanel(self.settings)
        self._recording = False
        self._processing = False
        self.link = SurveyorLink(self) if self.surveyor_out is not None else None
        self.heartbeat_timer = QTimer(self)
        self._pending_stop: dict | None = None
        self._quit_after_stop = False
        self._logging_sent = False

    # --- Layout ---
    def _create_layouts(self):
        self.tab_widgets = {RECORD: self.record_panel, PROCESS: self.process_panel,
                            DIAGNOSTICS: self.logger_panel, SETTINGS: self.settings_panel}
        for name, widget in self.tab_widgets.items():
            self.main_tabs.addTab(widget, name)
        self.setCentralWidget(self.main_tabs)

    # --- Signals & Slots ---
    def _connect_signals(self):
        self.logger_panel.files_ready.connect(self._on_logger_panel_files_ready)
        self.process_panel.running_changed.connect(self._on_process_panel_running_changed)
        self.logger_panel.running_changed.connect(self._on_logger_panel_running_changed)
        self.settings_panel.saved.connect(self._on_settings_panel_saved)
        self.record_panel.light_changed.connect(self._on_record_panel_light_changed)
        if self.link:
            self.link.command.connect(self._on_link_command)
            self.link.closed.connect(self._on_link_closed)
            self.heartbeat_timer.timeout.connect(self._on_heartbeat_timer_timeout)
            self.logger_panel.logging_status.connect(self._on_logger_panel_logging_status)
            self.logger_panel.mark_done.connect(self._on_logger_panel_mark_done)

    # --- Styling ---
    def _apply_styles(self):
        self.setWindowTitle(f"{WINDOW_TITLE}  {app_version()}")
        self.resize(*WINDOW_SIZE)
        self.setStyleSheet(styles.APP)

    # --- Initial State ---
    def _load_initial_state(self):
        self.main_tabs.setCurrentWidget(self.record_panel)
        self._update_tab_locks()
        if self.link:
            self.record_panel.set_controller(CONTROLLER_NAME, self.surveyor_out)
            self.link.start()
            self.link.send("hello", None, protocol=PROTOCOL_VERSION, version=app_version(), pid=os.getpid(), out=self.surveyor_out,
                           heartbeat_s=HEARTBEAT_S)
            self.heartbeat_timer.start(int(HEARTBEAT_S * 1000))
            QTimer.singleShot(0, self._start_for_link)

    # --- Business logic ---
    def _update_tab_locks(self):
        # Follow the running_changed signals: when "stopped" arrives the worker thread may
        # still be finishing, so is_running can briefly still say True.
        locked = locked_tabs(self._recording, self._processing)
        for name, widget in self.tab_widgets.items():
            index = self.main_tabs.indexOf(widget)
            self.main_tabs.setTabEnabled(index, name not in locked)
            self.main_tabs.setTabToolTip(index, locked.get(name, ""))

    def _on_logger_panel_running_changed(self, running: bool):
        self._recording = running
        self._update_tab_locks()
        if self.link and not running:
            self._logging_sent = False
            fields = {"files": self.logger_panel.last_files, "closed_marks": self.logger_panel.closed_marks,
                      "session_folder": (self.logger_panel.live or LiveStatus()).session_folder}
            if self._pending_stop is not None:
                self.link.send("reply", self._pending_stop, cmd=self._pending_stop["cmd"], ok=True, **fields)
                self._pending_stop = None
            else:
                self.link.send("stopped", None, **fields)
            if self._quit_after_stop:
                QTimer.singleShot(CLOSE_DELAY_MS, self.close)

    # --- Link to the controlling app (Surveyor) ---
    def _status_fields(self) -> dict:
        level, title, detail = self.record_panel.light
        live = self.logger_panel.live if self._recording else None
        logging = bool(live and live.connected and live.bytes_total > 0)
        nav_src = live if (live and live.nav_time) else self.record_panel.display_status
        return {"logging": logging, "light": LIGHT_NAMES[level], "message": title, "detail": detail,
                "ok_to_collect": logging and level == Health.GOOD,
                "session_folder": live.session_folder if live else "",
                "files": len(live.files) if live else 0,
                "megabytes": round(live.bytes_total / 1e6, 1) if live else 0.0,
                "recording_s": round(self.record_panel.recording_seconds),
                "warnings": list(live.warnings) if live else [],
                "marks_active": self.logger_panel.live_marks(),
                "nav": nav_row(nav_src)}

    def _start_for_link(self, request: dict | None = None):
        problem = (drive_problem(self.logger_panel.out_folder)
                   or self.logger_panel.start_logging(show_errors=False, remember=False))
        if request is not None:
            self.link.send("reply", request, cmd="start", ok=not problem, error=problem or None,
                           **self._status_fields())
        elif problem:
            self.link.send("error", None, cmd="start", error=problem)

    def _on_link_command(self, req: dict):
        cmd = req["cmd"]
        if cmd == "status":
            self.link.send("reply", req, cmd=cmd, ok=True, **self._status_fields())
        elif cmd == "start":
            self._start_for_link(req)
        elif cmd == "mark":
            self._mark_for_link(req)
        elif cmd in ("stop", "quit"):
            self._quit_after_stop = cmd == "quit"
            if self._recording:
                self._pending_stop = req
                self.logger_panel.stop_logging()
            else:
                self.link.send("reply", req, cmd=cmd, ok=True, files=self.logger_panel.last_files,
                               closed_marks=[], session_folder="")
                if self._quit_after_stop:
                    QTimer.singleShot(CLOSE_DELAY_MS, self.close)

    def _mark_for_link(self, req: dict):
        fields = self._status_fields()
        if not fields["logging"]:
            self.link.send("reply", req, cmd="mark", ok=False, error="not recording", **fields)
            return
        snapshot = {**fields["nav"], "light": fields["light"], "message": fields["message"]}
        try:
            info = self.logger_panel.start_mark(req["label"], req.get("seconds") or DEFAULT_MARK_SECONDS, snapshot,
                                                req.get("folder"))
        except (RuntimeError, MarkError, OSError) as exc:
            self.link.send("reply", req, cmd="mark", ok=False, error=str(exc), **fields)
            return
        self.link.send("reply", req, cmd="mark", ok=True, **info, **fields)

    def _on_link_closed(self):
        """The controlling app went away: stop recording cleanly and close."""
        self._quit_after_stop = True
        if self._recording:
            self.logger_panel.stop_logging()
        else:
            QTimer.singleShot(CLOSE_DELAY_MS, self.close)

    def _on_heartbeat_timer_timeout(self):
        self.link.send("heartbeat", None, **self._status_fields())

    def _on_logger_panel_logging_status(self, s: LiveStatus):
        if s.session_folder and not self._logging_sent:
            self._logging_sent = True
            self.link.send("logging", None, **self._status_fields())

    def _on_logger_panel_mark_done(self, info: dict):
        self.link.send("mark_done", None, **info)

    def _on_record_panel_light_changed(self, level: str, title: str, _detail: str):
        self.logger_panel.set_mark_light(LIGHT_NAMES[level], title)

    def _on_settings_panel_saved(self):
        self.logger_panel.reload_settings()
        self.record_panel.refresh()

    def _on_process_panel_running_changed(self, running: bool):
        self._processing = running
        self._update_tab_locks()

    def _on_logger_panel_files_ready(self, files: list):
        self.process_panel.add_pcs_files(files)
        self.main_tabs.setCurrentWidget(self.process_panel)

    def closeEvent(self, event):
        if self.link:                                 # controlled: no questions, stop cleanly
            self.heartbeat_timer.stop()
            self.logger_panel.stop_and_wait()
            self.process_panel.stop_and_wait()
            self.link.send("bye", None)
            super().closeEvent(event)
            return
        if not self.logger_panel.is_running:
            self.logger_panel.health_panel.stop()
        if self.logger_panel.is_running:
            answer = QMessageBox.question(self, WINDOW_TITLE, "The logger is still recording. Stop it and close?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.logger_panel.stop_and_wait()
        self.process_panel.stop_and_wait()
        super().closeEvent(event)
