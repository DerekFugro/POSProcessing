"""Live logger tab: records the PCS Ethernet logging port (TCP 5603) to .000 files."""
from __future__ import annotations

import re
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QVBoxLayout,
                               QWidget)

from posproc.logger import DEFAULT_PORT, DEFAULT_SESSION_NAME, REQUIRED_GROUPS, LiveStatus, PcsLogger

from . import styles
from .health_panel import HealthPanel
from .map_view import LiveMapView

MARGIN = 8
SPACING = 6
MAX_PORT = 65535
BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')     # not allowed in a Windows folder name


class _Bridge(QObject):
    """Carries logger-thread callbacks into the GUI thread."""
    status = Signal(object)
    message = Signal(str)
    finished = Signal()


class LoggerPanel(QWidget):
    files_ready = Signal(list)          # logged files to add to the processing list
    running_changed = Signal(bool)      # logger started (True) or stopped (False)
    logging_status = Signal(object)     # LiveStatus from the logger, about once a second

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._create_widgets()
        self._create_layouts()
        self._connect_signals()
        self._apply_styles()
        self._load_initial_state()

    def _create_widgets(self):
        self.connection_group = QGroupBox("Log to this PC - PCS Ethernet logging port (listen only, nothing is sent to the POS)")
        self.host_input = QLineEdit()
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, MAX_PORT)
        self.out_input = QLineEdit()
        self.out_browse_button = QPushButton("Browse...")
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText(DEFAULT_SESSION_NAME)
        self.folder_preview = QLabel()
        self.folder_preview.setWordWrap(True)
        self.start_button = QPushButton("Start logging")
        self.stop_button = QPushButton("Stop")
        self.process_button = QPushButton("Send logged files to processing")

        self.health_panel = HealthPanel(self.settings)
        self.live_map = LiveMapView(Path(self.settings.tile_cache_dir))
        self.left_widget = QWidget()
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.status_group = QGroupBox("Logging status (TCP 5603)")
        self.state_value = QLabel()
        self.data_value = QLabel()
        self.file_value = QLabel()
        self.groups_value = QLabel()
        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.file_value.setWordWrap(True)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.out_row = QWidget()

        self._bridge = _Bridge()
        self._logger: PcsLogger | None = None
        self._thread: threading.Thread | None = None
        self._last_files: list[str] = []

    def _create_layouts(self):
        self.out_row_layout = QHBoxLayout(self.out_row)
        self.out_row_layout.setContentsMargins(0, 0, 0, 0)
        self.out_row_layout.addWidget(self.out_input)
        self.out_row_layout.addWidget(self.out_browse_button)
        self.connection_layout = QFormLayout(self.connection_group)
        self.connection_layout.addRow("PCS IP address", self.host_input)
        self.connection_layout.addRow("Logging port", self.port_spin)
        self.connection_layout.addRow("Save logs in", self.out_row)
        self.connection_layout.addRow("Session name", self.name_input)
        self.connection_layout.addRow("New folder", self.folder_preview)

        self.button_layout = QHBoxLayout()
        self.button_layout.addWidget(self.start_button, 2)
        self.button_layout.addWidget(self.stop_button, 1)
        self.button_layout.addWidget(self.process_button, 2)

        self.status_layout = QFormLayout(self.status_group)
        self.status_layout.setSpacing(SPACING)
        self.status_layout.addRow("State", self.state_value)
        self.status_layout.addRow("Data", self.data_value)
        self.status_layout.addRow("Current file", self.file_value)
        self.status_layout.addRow("Groups (Hz)", self.groups_value)
        self.status_layout.addRow("", self.warning_label)

        self.left_layout = QVBoxLayout(self.left_widget)
        self.left_layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        self.left_layout.setSpacing(SPACING)
        self.left_layout.addWidget(self.health_panel)
        self.left_layout.addWidget(self.connection_group)
        self.left_layout.addLayout(self.button_layout)
        self.left_layout.addWidget(self.status_group)
        self.left_layout.addWidget(self.log_view, 1)
        self.splitter.addWidget(self.left_widget)
        self.splitter.addWidget(self.live_map)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.addWidget(self.splitter)

    def _connect_signals(self):
        self.out_browse_button.clicked.connect(self._on_out_browse_button_clicked)
        self.start_button.clicked.connect(self._on_start_button_clicked)
        self.stop_button.clicked.connect(self._on_stop_button_clicked)
        self.process_button.clicked.connect(self._on_process_button_clicked)
        self.out_input.textChanged.connect(self._update_folder_preview)
        self.name_input.textChanged.connect(self._update_folder_preview)
        self._bridge.status.connect(self._on_bridge_status)
        self._bridge.message.connect(self.log_view.appendPlainText)
        self._bridge.finished.connect(self._on_bridge_finished)
        self.health_panel.message.connect(self.log_view.appendPlainText)
        self.health_panel.pcs_found.connect(self._on_health_panel_pcs_found)
        self.health_panel.position.connect(self.live_map.add_point)

    def _apply_styles(self):
        self.start_button.setStyleSheet(styles.RUN_BUTTON)
        self.stop_button.setStyleSheet(styles.CANCEL_BUTTON)
        self.warning_label.setStyleSheet(f"color: {styles.ACCENT_ORANGE};")
        self.log_view.setStyleSheet("font-family: Consolas, monospace; font-size: 9pt;")

    def _load_initial_state(self):
        self.host_input.setText(self.settings.logger_host)
        self.port_spin.setValue(self.settings.logger_port or DEFAULT_PORT)
        self.set_out_folder(self.settings.logger_out)
        self.name_input.setText(getattr(self.settings, "logger_session_name", DEFAULT_SESSION_NAME))
        self._update_folder_preview()
        self._set_running(False)
        self.process_button.setEnabled(False)
        self._show(LiveStatus())

    # --- Business logic ---
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _set_running(self, running: bool):
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        for w in (self.host_input, self.port_spin, self.out_input, self.out_browse_button, self.name_input):
            w.setEnabled(not running)
        self.running_changed.emit(running)

    @property
    def last_files(self) -> list[str]:
        """Files written by the last finished logger run."""
        return list(self._last_files)

    @property
    def out_folder(self) -> str:
        return self.out_input.text().strip()

    def set_out_folder(self, folder: str):
        self.out_input.setText(str(Path(folder)) if folder.strip() else "")

    def _session_name(self) -> str:
        return self.name_input.text().strip() or DEFAULT_SESSION_NAME

    def _update_folder_preview(self):
        """Show the folder the next Start will create: <Save logs in>\\<name>_YYYYMMDD_HHMMSS."""
        out, name = self.out_input.text().strip(), self._session_name()
        if BAD_NAME_CHARS.search(name):
            self.folder_preview.setText('Session name cannot contain \\ / : * ? " < > |')
        elif not out:
            self.folder_preview.setText("Pick a folder to save logs in.")
        else:
            self.folder_preview.setText(str(Path(out) / f"{name}_YYYYMMDD_HHMMSS")
                                        + "   (date and time set when logging starts)")

    def _on_health_panel_pcs_found(self, ip: str):
        """Fill in the PCS address found from its display broadcast (not while logging)."""
        if self.is_running or self.host_input.text().strip() == ip:
            return
        self.host_input.setText(ip)
        self.log_view.appendPlainText(f"PCS address set to {ip} (found automatically).")

    def _on_out_browse_button_clicked(self):
        folder = QFileDialog.getExistingDirectory(self, "Folder for POS logs", self.out_input.text())
        if folder:
            self.set_out_folder(folder)

    def _on_start_button_clicked(self):
        self.start_logging()

    def start_logging(self) -> bool:
        """Start the logger with the current inputs. Returns False if it could not start."""
        if self.is_running:
            return True
        host, name = self.host_input.text().strip(), self._session_name()
        if not host:
            QMessageBox.warning(self, "Logger", "Enter the PCS IP address.")
            return False
        if BAD_NAME_CHARS.search(name):
            QMessageBox.warning(self, "Logger", 'The session name cannot contain \\ / : * ? " < > |')
            return False
        if not self.out_folder:
            self._on_out_browse_button_clicked()          # ask for the folder before logging
        out = self.out_folder
        if not out:
            return False
        try:
            Path(out).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Logger", f"Cannot use {out}:\n{exc}")
            return False
        self.settings.logger_host, self.settings.logger_port, self.settings.logger_out = host, self.port_spin.value(), out
        self.settings.logger_session_name = name
        self.settings.save()
        self.log_view.clear()
        self._logger = PcsLogger(host, Path(out), self.port_spin.value(), session_name=name,
                                 on_status=self._bridge.status.emit, log=self._bridge.message.emit)
        self._thread = threading.Thread(target=self._run_logger, daemon=True)
        self._set_running(True)
        self.process_button.setEnabled(False)
        self._thread.start()
        return True

    def _run_logger(self):
        try:
            self._logger.run()
        finally:
            self._bridge.finished.emit()

    def _on_stop_button_clicked(self):
        self.stop_logging()

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
        self.process_button.setEnabled(bool(self._last_files))
        self.log_view.appendPlainText(f"Stopped. {len(self._last_files)} file(s) written.")

    def _on_process_button_clicked(self):
        self.send_files_to_processing()

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
            self.folder_preview.setText(s.session_folder)
        parts = []
        for g, rate in s.group_rates.items():
            mark = " *" if g in REQUIRED_GROUPS else ""
            parts.append(f"{g}: {rate:.0f}{mark}")
        self.groups_value.setText(", ".join(parts) + ("   (* needed for PPK)" if parts else "") or "-")
        self.warning_label.setText("\n".join("⚠ " + w for w in s.warnings))
