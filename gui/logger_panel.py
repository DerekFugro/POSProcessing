"""Diagnostics tab and the live recording engine behind the Record tab.

Shows POS health (display port UDP 5600), the recording connection, the logging-port status
(TCP 5603: data rate, groups, warnings) and a message log. Read-only: recording is started and
stopped on the Record tab, and the connection is changed on the Settings tab. Listen only -
nothing is sent to the POS.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                               QVBoxLayout, QWidget)

from posproc.logger import DEFAULT_PORT, DEFAULT_SESSION_NAME, REQUIRED_GROUPS, LiveStatus, PcsLogger

from . import styles
from .health_panel import HealthPanel

MARGIN = 8
SPACING = 6
BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')     # not allowed in a Windows folder name
WINDOW_TITLE = "Recording"


class _Bridge(QObject):
    """Carries logger-thread callbacks into the GUI thread."""
    status = Signal(object)
    message = Signal(str)
    finished = Signal()


class LoggerPanel(QWidget):
    files_ready = Signal(list)          # logged files to add to the processing list
    running_changed = Signal(bool)      # logger started (True) or stopped (False)
    logging_status = Signal(object)     # LiveStatus from the logger, about once a second
    out_folder_changed = Signal(str)    # folder recordings are saved under

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._create_widgets()
        self._create_layouts()
        self._connect_signals()
        self._apply_styles()
        self._load_initial_state()

    def _create_widgets(self):
        self.health_panel = HealthPanel(self.settings)
        self.connection_group = QGroupBox("Recording connection - PCS Ethernet logging port (change on the Settings tab)")
        self.host_value = QLabel()
        self.port_value = QLabel()
        self.name_value = QLabel()
        self.out_value = QLabel()
        self.out_value.setWordWrap(True)
        self.folder_value = QLabel()
        self.folder_value.setWordWrap(True)
        self.status_group = QGroupBox(f"Logging status (TCP {DEFAULT_PORT})")
        self.state_value = QLabel()
        self.data_value = QLabel()
        self.file_value = QLabel()
        self.file_value.setWordWrap(True)
        self.groups_value = QLabel()
        self.groups_value.setWordWrap(True)
        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)

        self._bridge = _Bridge()
        self._logger: PcsLogger | None = None
        self._thread: threading.Thread | None = None
        self._last_files: list[str] = []
        self._host = ""
        self._out = ""

    def _create_layouts(self):
        self.connection_layout = QFormLayout(self.connection_group)
        self.connection_layout.addRow("PCS IP address", self.host_value)
        self.connection_layout.addRow("Logging port", self.port_value)
        self.connection_layout.addRow("Session name", self.name_value)
        self.connection_layout.addRow("Save to", self.out_value)
        self.connection_layout.addRow("Folder", self.folder_value)

        self.status_layout = QFormLayout(self.status_group)
        self.status_layout.setSpacing(SPACING)
        self.status_layout.addRow("State", self.state_value)
        self.status_layout.addRow("Data", self.data_value)
        self.status_layout.addRow("Current file", self.file_value)
        self.status_layout.addRow("Groups (Hz)", self.groups_value)
        self.status_layout.addRow("", self.warning_label)

        self.left_layout = QVBoxLayout()
        self.left_layout.setSpacing(SPACING)
        self.left_layout.addWidget(self.health_panel)
        self.left_layout.addWidget(self.connection_group)
        self.left_layout.addStretch()
        self.right_layout = QVBoxLayout()
        self.right_layout.setSpacing(SPACING)
        self.right_layout.addWidget(self.status_group)
        self.right_layout.addWidget(self.log_view, 1)
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        self.main_layout.setSpacing(MARGIN)
        self.main_layout.addLayout(self.left_layout, 1)
        self.main_layout.addLayout(self.right_layout, 1)

    def _connect_signals(self):
        self._bridge.status.connect(self._on_bridge_status)
        self._bridge.message.connect(self.log_view.appendPlainText)
        self._bridge.finished.connect(self._on_bridge_finished)
        self.health_panel.message.connect(self.log_view.appendPlainText)
        self.health_panel.pcs_found.connect(self._on_health_panel_pcs_found)

    def _apply_styles(self):
        self.warning_label.setStyleSheet(f"color: {styles.ACCENT_ORANGE};")
        self.log_view.setStyleSheet("font-family: Consolas, monospace; font-size: 9pt;")
        for w in (self.host_value, self.port_value, self.name_value, self.out_value):
            w.setStyleSheet("font-weight: bold;")
        self.folder_value.setStyleSheet(f"color: {styles.TEXT_SECONDARY};")

    def _load_initial_state(self):
        self._host = self.settings.logger_host
        self.set_out_folder(self.settings.logger_out)
        self.reload_settings()
        self._set_running(False)
        self._show(LiveStatus())

    # --- Business logic ---
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_files(self) -> list[str]:
        """Files written by the last finished logger run."""
        return list(self._last_files)

    @property
    def out_folder(self) -> str:
        return self._out

    def set_out_folder(self, folder: str):
        folder = str(Path(folder)) if folder.strip() else ""
        if folder != self._out:
            self._out = folder
            self.out_folder_changed.emit(folder)
        self._update_connection()

    def _port(self) -> int:
        return self.settings.logger_port or DEFAULT_PORT

    def _session_name(self) -> str:
        return (getattr(self.settings, "logger_session_name", "") or "").strip() or DEFAULT_SESSION_NAME

    def reload_settings(self):
        """Show the connection settings again (after they change on the Settings tab)."""
        if not self.is_running:
            self._host = self.settings.logger_host
        self._update_connection()

    def _update_connection(self):
        self.host_value.setText(self._host or "-")
        self.port_value.setText(str(self._port()))
        self.name_value.setText(self._session_name())
        self.out_value.setText(self._out or "(not set - pick it on the Record tab)")
        if self.is_running and self._logger.watch.status.session_folder:
            self.folder_value.setText(self._logger.watch.status.session_folder)
        elif self._out:
            self.folder_value.setText(str(Path(self._out) / f"{self._session_name()}_YYYYMMDD_HHMMSS")
                                      + "   (date and time set when recording starts)")
        else:
            self.folder_value.setText("-")

    def _set_running(self, running: bool):
        self.running_changed.emit(running)

    def _on_health_panel_pcs_found(self, ip: str):
        """Use the PCS address found from its display broadcast (not while logging)."""
        if self.is_running or self._host == ip:
            return
        self._host = ip
        self._update_connection()
        self.log_view.appendPlainText(f"PCS address set to {ip} (found automatically).")

    def start_logging(self) -> bool:
        """Start recording to the current folder. Returns False if it could not start."""
        if self.is_running:
            return True
        host, name, out = self._host.strip(), self._session_name(), self._out
        if not host:
            QMessageBox.warning(self, WINDOW_TITLE, "No PCS address yet. Set it on the Settings tab.")
            return False
        if BAD_NAME_CHARS.search(name):
            QMessageBox.warning(self, WINDOW_TITLE, 'The session name cannot contain \\ / : * ? " < > |')
            return False
        if not out:
            QMessageBox.warning(self, WINDOW_TITLE, "Pick a folder to save recordings in.")
            return False
        try:
            Path(out).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, WINDOW_TITLE, f"Cannot use {out}:\n{exc}")
            return False
        self.settings.logger_host, self.settings.logger_out = host, out
        self.settings.save()
        self.log_view.clear()
        self._logger = PcsLogger(host, Path(out), self._port(), session_name=name,
                                 on_status=self._bridge.status.emit, log=self._bridge.message.emit)
        self._thread = threading.Thread(target=self._run_logger, daemon=True)
        self._thread.start()
        self._set_running(True)
        return True

    def _run_logger(self):
        try:
            self._logger.run()
        finally:
            self._bridge.finished.emit()

    def stop_logging(self):
        if self._logger and self.is_running:
            self._logger.stop()
            self.state_value.setText("Stopping...")

    def stop_and_wait(self, timeout: float = 10.0):
        self.health_panel.stop()
        if self.is_running:
            self._logger.stop()
            self._thread.join(timeout)

    def _on_bridge_finished(self):
        self._last_files = list(self._logger.watch.status.files) if self._logger else []
        self._set_running(False)
        self.log_view.appendPlainText(f"Stopped. {len(self._last_files)} file(s) written.")

    def send_files_to_processing(self):
        if self._last_files:
            self.files_ready.emit(list(self._last_files))

    def _on_bridge_status(self, s: LiveStatus):
        self._show(s)
        self.logging_status.emit(s)

    def _show(self, s: LiveStatus):
        self.state_value.setText(s.state + (f"  -  {s.error}" if s.error else ""))
        self.data_value.setText(f"{s.bytes_total / 1e6:.1f} MB  ({s.rate_kbps:.0f} kB/s), {len(s.files)} file(s)")
        self.file_value.setText(s.current_file or "-")
        if s.session_folder:
            self.folder_value.setText(s.session_folder)
        parts = []
        for g, rate in s.group_rates.items():
            mark = " *" if g in REQUIRED_GROUPS else ""
            parts.append(f"{g}: {rate:.0f}{mark}")
        self.groups_value.setText(", ".join(parts) + ("   (* needed for PPK)" if parts else "") or "-")
        self.warning_label.setText("\n".join("⚠ " + w for w in s.warnings))
