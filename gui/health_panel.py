"""POS health box: live RMS and status from the PCS display port (UDP 5600), like POSView."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QWidget

from posproc.display import DISPLAY_PORT, DisplayListener, Health, health_summary
from posproc.logger import LiveStatus

from . import styles

COLOURS = {Health.GOOD: styles.ACCENT_GREEN, Health.WARN: styles.ACCENT_ORANGE,
           Health.BAD: styles.ACCENT_RED, Health.NONE: styles.TEXT_SECONDARY}
ROWS = [("pcs", "PCS address"), ("link", "Display data"), ("align", "Navigation"), ("gnss", "GNSS"),
        ("pos_rms", "Position RMS"), ("att_rms", "Attitude RMS"), ("position", "Position")]
DOT = "●"


class _Bridge(QObject):
    status = Signal(object, float)
    message = Signal(str)


class HealthPanel(QGroupBox):
    message = Signal(str)
    pcs_found = Signal(str)          # IP address the display data comes from
    position = Signal(float, float, float, str)   # lat, lon, heading, health colour

    def __init__(self, settings, parent=None):
        super().__init__(f"POS health - display port UDP {DISPLAY_PORT} (same data POSView shows, listen only)", parent)
        self.settings = settings
        self._create_widgets()
        self._create_layouts()
        self._connect_signals()
        self._apply_styles()
        self._load_initial_state()

    def _create_widgets(self):
        self.listen_button = QPushButton("Stop listening")
        self.source_label = QLabel()
        self.values: dict[str, QLabel] = {}
        self.dots: dict[str, QLabel] = {}
        self.rows: dict[str, QWidget] = {}
        for key, _ in ROWS:
            self.dots[key] = QLabel(DOT)
            self.values[key] = QLabel("-")
            self.rows[key] = QWidget()
        self._bridge = _Bridge()
        self._listener: DisplayListener | None = None
        self._thread: threading.Thread | None = None

    def _create_layouts(self):
        self.form_layout = QFormLayout(self)
        for key, title in ROWS:
            row_layout = QHBoxLayout(self.rows[key])
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(self.dots[key])
            row_layout.addWidget(self.values[key], 1)
            self.form_layout.addRow(title, self.rows[key])
        self.button_layout = QHBoxLayout()
        self.button_layout.addWidget(self.source_label, 1)
        self.button_layout.addWidget(self.listen_button)
        self.form_layout.addRow(self.button_layout)

    def _connect_signals(self):
        self.listen_button.clicked.connect(self._on_listen_button_clicked)
        self._bridge.status.connect(self._on_bridge_status)
        self._bridge.message.connect(self.message.emit)

    def _apply_styles(self):
        self.source_label.setStyleSheet(f"color: {styles.TEXT_SECONDARY};")
        for key in self.values:
            self.values[key].setStyleSheet("font-weight: bold;")

    def _load_initial_state(self):
        self._show(LiveStatus(), float("inf"))
        if self.settings.display_listen_on_start:
            self.start()
        else:
            self.listen_button.setText("Start listening")

    # --- Business logic ---
    @property
    def is_listening(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_listening:
            return
        host = self.settings.logger_host if self.settings.display_only_from_pcs else ""
        self._listener = DisplayListener(host, self.settings.display_port,
                                         on_status=self._bridge.status.emit, log=self._bridge.message.emit)
        self._thread = threading.Thread(target=self._listener.run, daemon=True)
        self._thread.start()
        self.listen_button.setText("Stop listening")
        self.source_label.setText(f"Listening on UDP {self.settings.display_port}"
                                  + (f" from {host}" if host else " (any PCS on this network)"))

    def stop(self):
        if self._listener:
            self._listener.stop()
        if self._thread:
            self._thread.join(3)
        self.listen_button.setText("Start listening")
        self.source_label.setText("Not listening")

    def _on_listen_button_clicked(self):
        if self.is_listening:
            self.stop()
        else:
            self.start()

    def _on_bridge_status(self, s: LiveStatus, age: float):
        self._show(s, age)

    def _show(self, s: LiveStatus, age: float):
        if s.source_ip and s.source_ip != getattr(self, "_found_ip", ""):
            self._found_ip = s.source_ip
            self.pcs_found.emit(s.source_ip)
        summary = health_summary(s, age)
        if s.error:
            summary["link"] = (s.error, Health.BAD)
        if age < 3 and s.lat == s.lat:
            self.position.emit(s.lat, s.lon, s.heading, COLOURS[summary["pos_rms"][1]])
        for key, (text, grade) in summary.items():
            self.values[key].setText(text)
            self.dots[key].setStyleSheet(f"color: {COLOURS[grade]}; font-size: 14pt;")
