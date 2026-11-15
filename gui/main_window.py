"""Main window: four tabs - Record (live POS logging), Process (PPK of PCS files and CSV logs,
with map, report and log), Diagnostics and Settings. Opens on Record."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow, QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget

from posproc.config import Settings

from . import styles
from .logger_panel import LoggerPanel
from .process_panel import ProcessPanel
from .record_panel import RecordPanel
from .settings_dialog import SettingsDialog

WINDOW_TITLE = "POS PPK Processor"
WINDOW_SIZE = (1400, 860)
MARGIN = 8
SETTINGS_PLACEHOLDER = "Settings will move onto this tab. For now use the button below."


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
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
        self.settings_tab = QWidget()
        self.settings_label = QLabel(SETTINGS_PLACEHOLDER)
        self.settings_button = QPushButton("Settings...")

    # --- Layout ---
    def _create_layouts(self):
        self.settings_layout = QVBoxLayout(self.settings_tab)
        self.settings_layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        self.settings_layout.addWidget(self.settings_label)
        self.settings_layout.addWidget(self.settings_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.settings_layout.addStretch()

        self.main_tabs.addTab(self.record_panel, "Record")
        self.main_tabs.addTab(self.process_panel, "Process")
        self.main_tabs.addTab(self.logger_panel, "Diagnostics")
        self.main_tabs.addTab(self.settings_tab, "Settings")
        self.setCentralWidget(self.main_tabs)

    # --- Signals & Slots ---
    def _connect_signals(self):
        self.settings_button.clicked.connect(self._on_settings_button_clicked)
        self.logger_panel.files_ready.connect(self._on_logger_panel_files_ready)
        self.process_panel.running_changed.connect(self._on_process_panel_running_changed)

    # --- Styling ---
    def _apply_styles(self):
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(*WINDOW_SIZE)
        self.setStyleSheet(styles.APP)
        self.settings_label.setStyleSheet(f"color: {styles.TEXT_SECONDARY};")

    # --- Initial State ---
    def _load_initial_state(self):
        self.main_tabs.setCurrentWidget(self.record_panel)

    # --- Business logic ---
    def _on_settings_button_clicked(self):
        SettingsDialog(self.settings, self).exec()

    def _on_process_panel_running_changed(self, running: bool):
        self.settings_button.setEnabled(not running)

    def _on_logger_panel_files_ready(self, files: list):
        self.process_panel.add_pcs_files(files)
        self.main_tabs.setCurrentWidget(self.process_panel)

    def closeEvent(self, event):
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
