"""Main window: four tabs - Record (live POS logging), Process (PPK of PCS files and CSV logs,
with map, report and log), Diagnostics and Settings. Opens on Record."""
from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from posproc.config import Settings

from . import styles
from .logger_panel import LoggerPanel
from .process_panel import ProcessPanel
from .record_panel import RecordPanel
from .settings_panel import SettingsPanel

WINDOW_TITLE = "POS PPK Processor"
WINDOW_SIZE = (1400, 860)
RECORD, PROCESS, DIAGNOSTICS, SETTINGS = "Record", "Process", "Diagnostics", "Settings"
LOCKED_WHILE_RECORDING = "Locked while recording - stop recording on the Record tab first."
LOCKED_WHILE_PROCESSING = "Locked while processing - wait for it to finish or press Cancel."


def locked_tabs(recording: bool, processing: bool) -> dict[str, str]:
    """Tabs to grey out while busy, with the reason. Diagnostics is never locked (it only shows)."""
    locked = {}
    if recording:
        locked.update({PROCESS: LOCKED_WHILE_RECORDING, SETTINGS: LOCKED_WHILE_RECORDING})
    if processing:
        locked.update({RECORD: LOCKED_WHILE_PROCESSING, SETTINGS: LOCKED_WHILE_PROCESSING})
    return locked


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
        self.settings_panel = SettingsPanel(self.settings)
        self._recording = False
        self._processing = False

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

    # --- Styling ---
    def _apply_styles(self):
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(*WINDOW_SIZE)
        self.setStyleSheet(styles.APP)

    # --- Initial State ---
    def _load_initial_state(self):
        self.main_tabs.setCurrentWidget(self.record_panel)
        self._update_tab_locks()

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
