"""Record tab: the simple screen for field users - one status light, a folder, one big button.

It drives the same logger and display listener as the Diagnostics tab (LoggerPanel), so there
is only ever one recording and one listener. Listen only: nothing is sent to the POS.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from posproc.display import Health, record_status
from posproc.logger import DEFAULT_SESSION_NAME, LiveStatus

from . import styles
from .logger_panel import LoggerPanel
from .map_view import LiveMapView

MARGIN = 16
SPACING = 12
LIGHT_SIZE = 64
LEFT_WIDTH = 480
REFRESH_MS = 1000
LOW_SPACE_HOURS = 2.0
# Logging-port data rate measured on the ARAN PCS, 2026-11-14: about 37 KB/s (~130 MB per hour).
LOG_BYTES_PER_HOUR = 37 * 1024 * 3600
LIGHT_COLOURS = {Health.GOOD: styles.ACCENT_GREEN, Health.WARN: styles.ACCENT_ORANGE,
                 Health.BAD: styles.ACCENT_RED, Health.NONE: styles.TEXT_SECONDARY}
START_TEXT = "START RECORDING"
STOP_TEXT = "STOP RECORDING"
WINDOW_TITLE = "Record"


def drive_problem(folder: str) -> str:
    """Plain-language reason the folder can't be used, or '' if it looks fine."""
    if not folder.strip():
        return "Pick a folder to save recordings in."
    anchor = Path(folder).anchor
    if anchor and not Path(anchor).exists():
        return f"Drive {anchor.rstrip(chr(92))} not found - plug in the drive or pick another folder."
    return ""


def free_space_text(folder: str) -> tuple[str, bool]:
    """('F:\\ 120 GB free (about 900 hours of recording)', low_space)."""
    p = Path(folder)
    probe = p if p.exists() else Path(p.anchor)
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return "", False
    hours = free / LOG_BYTES_PER_HOUR
    return f"{p.anchor} {free / 1e9:,.0f} GB free (about {hours:,.0f} hours of recording)", hours < LOW_SPACE_HOURS


def count_text(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def duration_text(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60:02d} min"


class RecordPanel(QWidget):
    def __init__(self, settings, logger_panel: LoggerPanel, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.logger_panel = logger_panel
        self._create_widgets()
        self._create_layouts()
        self._connect_signals()
        self._apply_styles()
        self._load_initial_state()

    def _create_widgets(self):
        self.status_light = QLabel()
        self.status_title = QLabel()
        self.status_title.setWordWrap(True)
        self.status_detail = QLabel()
        self.status_detail.setWordWrap(True)
        self.folder_label = QLabel("Save to")
        self.folder_input = QLineEdit()
        self.browse_button = QPushButton("Browse...")
        self.folder_preview = QLabel()
        self.folder_preview.setWordWrap(True)
        self.space_label = QLabel()
        self.record_button = QPushButton(START_TEXT)
        self.progress_label = QLabel()
        self.progress_label.setWordWrap(True)
        self.send_button = QPushButton("Send this recording to the Process tab")
        self.controlled_label = QLabel()
        self.controlled_label.setWordWrap(True)
        self.live_map = LiveMapView(Path(self.settings.tile_cache_dir))
        self.left_widget = QWidget()
        self.status_row = QWidget()
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.timer = QTimer(self)

        self._health: tuple[LiveStatus, float] = (LiveStatus(), float("inf"))
        self._log: LiveStatus | None = None
        self._listen_start = time.monotonic()
        self._record_start = 0.0
        self._running = False         # follows logger_panel.running_changed
        self._controller = ""         # name of the app controlling recording, '' = manual
        self._light = (Health.NONE, "", "")

    def _create_layouts(self):
        self.status_text_layout = QVBoxLayout()
        self.status_text_layout.setSpacing(2)
        self.status_text_layout.addWidget(self.status_title)
        self.status_text_layout.addWidget(self.status_detail)
        self.status_row_layout = QHBoxLayout(self.status_row)
        self.status_row_layout.setContentsMargins(0, 0, 0, 0)
        self.status_row_layout.setSpacing(SPACING)
        self.status_row_layout.addWidget(self.status_light, 0, Qt.AlignmentFlag.AlignTop)
        self.status_row_layout.addLayout(self.status_text_layout, 1)

        self.folder_row_layout = QHBoxLayout()
        self.folder_row_layout.addWidget(self.folder_label)
        self.folder_row_layout.addWidget(self.folder_input, 1)
        self.folder_row_layout.addWidget(self.browse_button)

        self.left_layout = QVBoxLayout(self.left_widget)
        self.left_layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        self.left_layout.setSpacing(SPACING)
        self.left_layout.addWidget(self.status_row)
        self.left_layout.addWidget(self.controlled_label)
        self.left_layout.addSpacing(SPACING)
        self.left_layout.addLayout(self.folder_row_layout)
        self.left_layout.addWidget(self.folder_preview)
        self.left_layout.addWidget(self.space_label)
        self.left_layout.addSpacing(SPACING)
        self.left_layout.addWidget(self.record_button)
        self.left_layout.addWidget(self.progress_label)
        self.left_layout.addWidget(self.send_button)
        self.left_layout.addStretch()

        self.splitter.addWidget(self.left_widget)
        self.splitter.addWidget(self.live_map)
        self.splitter.setStretchFactor(1, 1)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.addWidget(self.splitter)

    def _connect_signals(self):
        self.browse_button.clicked.connect(self._on_browse_button_clicked)
        self.folder_input.editingFinished.connect(self._on_folder_input_editing_finished)
        self.record_button.clicked.connect(self._on_record_button_clicked)
        self.send_button.clicked.connect(self.logger_panel.send_files_to_processing)
        self.timer.timeout.connect(self._on_timer_timeout)
        self.logger_panel.health_panel.status.connect(self._on_health_panel_status)
        self.logger_panel.health_panel.position.connect(self.live_map.add_point)
        self.logger_panel.logging_status.connect(self._on_logger_panel_logging_status)
        self.logger_panel.running_changed.connect(self._on_logger_panel_running_changed)
        self.logger_panel.out_folder_changed.connect(self._on_logger_panel_out_folder_changed)

    def _apply_styles(self):
        self.left_widget.setFixedWidth(LEFT_WIDTH)
        self.status_light.setFixedSize(LIGHT_SIZE, LIGHT_SIZE)
        self.status_title.setStyleSheet("font-size: 22pt; font-weight: bold;")
        self.status_detail.setStyleSheet(f"font-size: 12pt; color: {styles.TEXT_SECONDARY};")
        self.folder_label.setStyleSheet("font-size: 11pt;")
        self.folder_input.setStyleSheet(f"font-size: 11pt; background-color: {styles.DARK_BG_ALT};")
        self.folder_preview.setStyleSheet(f"color: {styles.TEXT_SECONDARY};")
        self.progress_label.setStyleSheet("font-size: 12pt;")
        self.send_button.setStyleSheet(styles.RUN_BUTTON)
        self.controlled_label.setStyleSheet(f"font-size: 12pt; font-weight: bold; color: {styles.ACCENT_BLUE};")
        self.record_button.setMinimumHeight(LIGHT_SIZE + SPACING)
        self.record_button.setStyleSheet(styles.RECORD_BUTTON)

    def _load_initial_state(self):
        self.folder_input.setText(self.logger_panel.out_folder)
        self.send_button.hide()
        self.controlled_label.hide()
        self.progress_label.setText("Not recording.")
        self._show_running(self._running)
        self.refresh()
        self.timer.start(REFRESH_MS)

    # --- Business logic ---
    @property
    def light(self) -> tuple[str, str, str]:
        """Current status light: (Health grade, title, detail)."""
        return self._light

    @property
    def display_status(self) -> LiveStatus:
        """Latest display-port (UDP 5600) status, about 1 Hz."""
        return self._health[0]

    @property
    def recording_seconds(self) -> float:
        return time.monotonic() - self._record_start if self._running else 0.0

    def set_controller(self, name: str, folder: str):
        """Another app runs recording: lock the folder and button, show who is in control."""
        self._controller = name
        self.controlled_label.setText(f"Controlled by {name} - recording is started and stopped there.")
        self.controlled_label.setVisible(bool(name))
        if folder:
            self._set_folder(folder)
        self._show_running(self._running)

    def _folder(self) -> str:
        return self.folder_input.text().strip()

    def _set_folder(self, folder: str):
        folder = str(Path(folder)) if folder.strip() else ""
        self.folder_input.setText(folder)
        if folder != self.logger_panel.out_folder:
            self.logger_panel.set_out_folder(folder)
        self.refresh()

    def _on_browse_button_clicked(self):
        folder = QFileDialog.getExistingDirectory(self, "Where to save recordings", self._folder())
        if folder:
            self._set_folder(folder)

    def _on_folder_input_editing_finished(self):
        self._set_folder(self._folder())

    def _on_logger_panel_out_folder_changed(self, text: str):
        if text.strip() != self._folder():
            self.folder_input.setText(text.strip())
            self.refresh()

    def _on_record_button_clicked(self):
        if self._running:
            answer = QMessageBox.question(self, WINDOW_TITLE, "Stop recording?")
            if answer == QMessageBox.StandardButton.Yes:
                self.logger_panel.stop_logging()
                self.progress_label.setText("Stopping...")
            return
        if not self._folder():
            self._on_browse_button_clicked()
            if not self._folder():
                return
        problem = drive_problem(self._folder())
        if problem:
            QMessageBox.warning(self, WINDOW_TITLE, problem)
            return
        self._set_folder(self._folder())
        self._log = None
        self.logger_panel.start_logging()

    def _on_logger_panel_running_changed(self, running: bool):
        self._running = running
        if running:
            self._record_start = time.monotonic()
        self._show_running(running)
        self.refresh()

    def _show_running(self, running: bool):
        self.record_button.setText(STOP_TEXT if running else START_TEXT)
        self.record_button.setStyleSheet(styles.STOP_RECORD_BUTTON if running else styles.RECORD_BUTTON)
        for w in (self.folder_input, self.browse_button):
            w.setEnabled(not running and not self._controller)
        self.record_button.setEnabled(not self._controller)
        self.send_button.setVisible(not running and bool(self.logger_panel.last_files) and not self._controller)

    def _on_health_panel_status(self, s: LiveStatus, age: float):
        self._health = (s, age)
        self._refresh_light()

    def _on_logger_panel_logging_status(self, s: LiveStatus):
        self._log = s
        self.refresh()

    def _on_timer_timeout(self):
        self.refresh()

    def refresh(self):
        """Update everything shown (also after settings change)."""
        self._refresh_light()
        self._refresh_folder()
        self._refresh_progress()

    def _refresh_light(self):
        s, age = self._health
        level, title, detail = record_status(s, age, time.monotonic() - self._listen_start,
                                             self._running, self._log)
        self._light = (level, title, detail)
        colour = LIGHT_COLOURS[level]
        self.status_light.setStyleSheet(f"background-color: {colour}; border-radius: {LIGHT_SIZE // 2}px;")
        self.status_title.setText(title)
        self.status_title.setStyleSheet(f"font-size: 22pt; font-weight: bold; color: {colour};")
        self.status_detail.setText(detail)

    def _refresh_folder(self):
        folder = self._folder()
        problem = drive_problem(folder)
        if self._running and self._log and self._log.session_folder:
            self.folder_preview.setText(f"Recording into {self._log.session_folder}")
        elif problem:
            self.folder_preview.setText(problem)
        else:
            name = getattr(self.settings, "logger_session_name", "") or DEFAULT_SESSION_NAME
            self.folder_preview.setText(f"New recording goes to {Path(folder) / (name + '_YYYYMMDD_HHMMSS')}")
        colour = styles.ACCENT_ORANGE if problem else styles.TEXT_SECONDARY
        self.folder_preview.setStyleSheet(f"color: {colour};")
        text, low = ("", False) if problem else free_space_text(folder)
        self.space_label.setText(text + ("  -  space is low" if low else ""))
        self.space_label.setStyleSheet(f"color: {styles.ACCENT_ORANGE if low else styles.TEXT_SECONDARY};")

    def _refresh_progress(self):
        if self._running:
            log = self._log or LiveStatus()
            elapsed = duration_text(time.monotonic() - self._record_start)
            self.progress_label.setText(f"Recording  ·  {elapsed}  ·  {count_text(len(log.files), 'file')}  ·  "
                                        f"{log.bytes_total / 1e6:,.0f} MB")
        elif self.logger_panel.last_files:
            files = self.logger_panel.last_files
            self.progress_label.setText(f"Stopped. {count_text(len(files), 'file')} saved in {Path(files[0]).parent}")
            self.send_button.setVisible(not self._controller)
