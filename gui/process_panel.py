"""Process tab: pick PCS files and CSV logs, run PPK, then view map, report and log."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QSplitter, QTabWidget, QVBoxLayout, QWidget)

from posproc.pcs import group_sessions

from . import styles
from .map_view import HAS_WEBENGINE, MapView
from .worker import PipelineWorker

if HAS_WEBENGINE:
    from PySide6.QtWebEngineWidgets import QWebEngineView

WINDOW_TITLE = "Process"
LEFT_PANEL_WIDTH = 430
MARGIN = 8
SPACING = 6
PROGRESS_STEPS = 1000
CLOSE_WAIT_MS = 5000
PCS_FILTER = "POS log files (*.0[0-9][0-9]);;All files (*)"
RATE_CHOICES = [("10 Hz", 10.0), ("1 Hz", 1.0), ("All epochs (~50 Hz)", 0.0)]


class ProcessPanel(QWidget):
    running_changed = Signal(bool)      # processing started (True) or finished/stopped (False)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._create_widgets()
        self._create_layouts()
        self._connect_signals()
        self._apply_styles()
        self._load_initial_state()

    # --- Widget Construction ---
    def _create_widgets(self):
        self.left_panel = QWidget()
        self.splitter = QSplitter()

        self.pcs_group = QGroupBox("1. POS (PCS) files  -  LV_1_*.000, .001 ...")
        self.pcs_list = QListWidget()
        self.pcs_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.pcs_list.setTextElideMode(Qt.TextElideMode.ElideLeft)
        self.pcs_add_files_button = QPushButton("Add files...")
        self.pcs_add_folder_button = QPushButton("Add folder...")
        self.pcs_remove_button = QPushButton("Remove")
        self.pcs_clear_button = QPushButton("Clear")
        self.pcs_summary_label = QLabel()

        self.csv_group = QGroupBox("2. Real-time CSV GPS logs (optional)")
        self.csv_list = QListWidget()
        self.csv_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.csv_list.setTextElideMode(Qt.TextElideMode.ElideLeft)
        self.csv_add_files_button = QPushButton("Add files...")
        self.csv_add_folder_button = QPushButton("Add folder...")
        self.csv_remove_button = QPushButton("Remove")
        self.csv_clear_button = QPushButton("Clear")

        self.output_group = QGroupBox("3. Output")
        self.output_input = QLineEdit()
        self.output_browse_button = QPushButton("Browse...")
        self.rate_label = QLabel("Trajectory rate:")
        self.rate_combo = QComboBox()
        for text, _ in RATE_CHOICES:
            self.rate_combo.addItem(text)

        self.run_button = QPushButton("Run")
        self.cancel_button = QPushButton("Cancel")
        self.open_folder_button = QPushButton("Open output folder")
        self.progress_bar = QProgressBar()
        self.status_label = QLabel("Ready")

        self.results_tabs = QTabWidget()
        self.map_view = MapView(Path(self.settings.tile_cache_dir))
        self.report_view = QWebEngineView() if HAS_WEBENGINE else QLabel("Report opens in your browser.")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)

        self._thread: QThread | None = None
        self._worker: PipelineWorker | None = None
        self._last_result = None

    # --- Layout ---
    def _create_layouts(self):
        self.pcs_button_layout = QHBoxLayout()
        for b in (self.pcs_add_files_button, self.pcs_add_folder_button, self.pcs_remove_button, self.pcs_clear_button):
            self.pcs_button_layout.addWidget(b)
        self.pcs_layout = QVBoxLayout(self.pcs_group)
        self.pcs_layout.addWidget(self.pcs_list)
        self.pcs_layout.addLayout(self.pcs_button_layout)
        self.pcs_layout.addWidget(self.pcs_summary_label)

        self.csv_button_layout = QHBoxLayout()
        for b in (self.csv_add_files_button, self.csv_add_folder_button, self.csv_remove_button, self.csv_clear_button):
            self.csv_button_layout.addWidget(b)
        self.csv_layout = QVBoxLayout(self.csv_group)
        self.csv_layout.addWidget(self.csv_list)
        self.csv_layout.addLayout(self.csv_button_layout)

        self.output_row_layout = QHBoxLayout()
        self.output_row_layout.addWidget(self.output_input)
        self.output_row_layout.addWidget(self.output_browse_button)
        self.rate_row_layout = QHBoxLayout()
        self.rate_row_layout.addWidget(self.rate_label)
        self.rate_row_layout.addWidget(self.rate_combo)
        self.rate_row_layout.addStretch()
        self.output_layout = QVBoxLayout(self.output_group)
        self.output_layout.addLayout(self.output_row_layout)
        self.output_layout.addLayout(self.rate_row_layout)

        self.run_row_layout = QHBoxLayout()
        self.run_row_layout.addWidget(self.run_button, 2)
        self.run_row_layout.addWidget(self.cancel_button, 1)

        self.left_layout = QVBoxLayout(self.left_panel)
        self.left_layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        self.left_layout.setSpacing(SPACING)
        self.left_layout.addWidget(self.pcs_group, 3)
        self.left_layout.addWidget(self.csv_group, 2)
        self.left_layout.addWidget(self.output_group)
        self.left_layout.addLayout(self.run_row_layout)
        self.left_layout.addWidget(self.progress_bar)
        self.left_layout.addWidget(self.status_label)
        self.left_layout.addWidget(self.open_folder_button)

        self.results_tabs.addTab(self.map_view, "Map")
        self.results_tabs.addTab(self.report_view, "Report")
        self.results_tabs.addTab(self.log_view, "Log")
        self.splitter.addWidget(self.left_panel)
        self.splitter.addWidget(self.results_tabs)
        self.splitter.setStretchFactor(1, 1)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.addWidget(self.splitter)

    # --- Signals & Slots ---
    def _connect_signals(self):
        self.pcs_add_files_button.clicked.connect(self._on_pcs_add_files_button_clicked)
        self.pcs_add_folder_button.clicked.connect(self._on_pcs_add_folder_button_clicked)
        self.pcs_remove_button.clicked.connect(self._on_pcs_remove_button_clicked)
        self.pcs_clear_button.clicked.connect(self._on_pcs_clear_button_clicked)
        self.csv_add_files_button.clicked.connect(self._on_csv_add_files_button_clicked)
        self.csv_add_folder_button.clicked.connect(self._on_csv_add_folder_button_clicked)
        self.csv_remove_button.clicked.connect(self._on_csv_remove_button_clicked)
        self.csv_clear_button.clicked.connect(self.csv_list.clear)
        self.output_browse_button.clicked.connect(self._on_output_browse_button_clicked)
        self.run_button.clicked.connect(self._on_run_button_clicked)
        self.cancel_button.clicked.connect(self._on_cancel_button_clicked)
        self.open_folder_button.clicked.connect(self._on_open_folder_button_clicked)

    # --- Styling ---
    def _apply_styles(self):
        self.run_button.setStyleSheet(styles.RUN_BUTTON)
        self.cancel_button.setStyleSheet(styles.CANCEL_BUTTON)
        self.left_panel.setMinimumWidth(LEFT_PANEL_WIDTH)
        self.log_view.setStyleSheet("font-family: Consolas, monospace; font-size: 9pt;")
        self.status_label.setStyleSheet(f"color: {styles.TEXT_SECONDARY};")
        self.pcs_summary_label.setStyleSheet(f"color: {styles.TEXT_SECONDARY};")

    # --- Initial State ---
    def _load_initial_state(self):
        self.progress_bar.setRange(0, PROGRESS_STEPS)
        self.progress_bar.setValue(0)
        self.cancel_button.setEnabled(False)
        self.open_folder_button.setEnabled(False)
        self._update_pcs_summary()

    # --- Business logic ---
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def add_pcs_files(self, files: list[str]):
        """Add logged files (e.g. from the Record tab) to the POS file list."""
        self._add_unique(self.pcs_list, files)
        self._update_pcs_summary()
        self.status_label.setText(f"Added {len(files)} logged file(s). Press Run to process.")

    def _list_items(self, lw: QListWidget) -> list[str]:
        return [lw.item(i).text() for i in range(lw.count())]

    def _add_unique(self, lw: QListWidget, paths):
        have = set(self._list_items(lw))
        for p in sorted(str(Path(x)) for x in paths):
            if p not in have:
                lw.addItem(p)
                have.add(p)

    def _update_pcs_summary(self):
        files = self._list_items(self.pcs_list)
        try:
            sessions = group_sessions(files)
            self.pcs_summary_label.setText(f"{len(files)} files in {len(sessions)} session(s)")
        except ValueError as exc:
            self.pcs_summary_label.setText(str(exc))
        if files and not self.output_input.text():
            self.output_input.setText(str(Path(files[0]).parent / "PPK_Output"))

    def _on_pcs_add_files_button_clicked(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select POS files", "", PCS_FILTER)
        self._add_unique(self.pcs_list, paths)
        self._update_pcs_summary()

    def _on_pcs_add_folder_button_clicked(self):
        folder = QFileDialog.getExistingDirectory(self, "Folder with POS files")
        if folder:
            found = [p for p in Path(folder).rglob("*") if p.is_file() and p.suffix[1:].isdigit() and len(p.suffix) == 4]
            self._add_unique(self.pcs_list, found)
            self._update_pcs_summary()

    def _on_pcs_remove_button_clicked(self):
        for item in self.pcs_list.selectedItems():
            self.pcs_list.takeItem(self.pcs_list.row(item))
        self._update_pcs_summary()

    def _on_pcs_clear_button_clicked(self):
        self.pcs_list.clear()
        self._update_pcs_summary()

    def _on_csv_add_files_button_clicked(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select CSV GPS logs", "", "CSV (*.csv)")
        self._add_unique(self.csv_list, paths)

    def _on_csv_add_folder_button_clicked(self):
        folder = QFileDialog.getExistingDirectory(self, "Folder with CSV logs (searched with subfolders)")
        if folder:
            suffix = self.settings.csv_suffix.lower()
            found = [p for p in Path(folder).rglob("*.csv") if not p.stem.lower().endswith(suffix)]
            self._add_unique(self.csv_list, found)

    def _on_csv_remove_button_clicked(self):
        for item in self.csv_list.selectedItems():
            self.csv_list.takeItem(self.csv_list.row(item))

    def _on_output_browse_button_clicked(self):
        folder = QFileDialog.getExistingDirectory(self, "Output folder", self.output_input.text())
        if folder:
            self.output_input.setText(folder)

    def _on_open_folder_button_clicked(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.output_input.text()))

    def _on_run_button_clicked(self):
        pcs = self._list_items(self.pcs_list)
        if not pcs:
            QMessageBox.warning(self, WINDOW_TITLE, "Add the POS (PCS) files first.")
            return
        try:
            group_sessions(pcs)
        except ValueError as exc:
            QMessageBox.warning(self, WINDOW_TITLE, str(exc))
            return
        out = self.output_input.text().strip()
        if not out:
            QMessageBox.warning(self, WINDOW_TITLE, "Choose an output folder.")
            return
        missing = [p for p in (self.settings.convert_to_rinex_exe, self.settings.rnx2rtkp_exe)
                   if not Path(p).exists()]
        if missing and not self.settings.manual_base_rinex:
            QMessageBox.warning(self, WINDOW_TITLE, "These programs were not found:\n\n" + "\n".join(missing)
                                + "\n\nSet their paths in Settings.")
            return
        self.log_view.clear()
        self.progress_bar.setValue(0)
        rate = RATE_CHOICES[self.rate_combo.currentIndex()][1]
        self._thread = QThread(self)
        self._worker = PipelineWorker(self.settings, pcs, self._list_items(self.csv_list), Path(out), rate)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self._on_worker_log)
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._set_running(True)
        self._thread.start()
        self.results_tabs.setCurrentWidget(self.log_view)

    def _on_cancel_button_clicked(self):
        if self._worker:
            self._worker.cancel()
            self.status_label.setText("Cancelling after the current step...")

    def _set_running(self, running: bool):
        self.run_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        for b in (self.pcs_add_files_button, self.pcs_add_folder_button, self.pcs_remove_button,
                  self.pcs_clear_button, self.csv_add_files_button, self.csv_add_folder_button,
                  self.csv_remove_button, self.csv_clear_button):
            b.setEnabled(not running)
        self.running_changed.emit(running)

    def _on_worker_log(self, text: str):
        self.log_view.appendPlainText(text)

    def _on_worker_progress(self, frac: float, msg: str):
        self.progress_bar.setValue(int(max(0.0, min(1.0, frac)) * PROGRESS_STEPS))
        self.status_label.setText(msg)

    def _on_worker_finished(self, result):
        self._set_running(False)
        self._last_result = result
        self.progress_bar.setValue(PROGRESS_STEPS)
        ok = [s for s in result.sessions if s.fusion is not None]
        self.status_label.setText(f"Done: {len(ok)}/{len(result.sessions)} session(s) processed, "
                                  f"{len(result.csv)} CSV log(s).")
        self.open_folder_button.setEnabled(True)
        self.map_view.show_result(result)
        if result.report_html:
            if HAS_WEBENGINE:
                self.report_view.load(QUrl.fromLocalFile(str(result.report_html)))
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.report_html)))
        self.results_tabs.setCurrentWidget(self.report_view if ok else self.log_view)

    def _on_worker_failed(self, message: str):
        self._set_running(False)
        self.status_label.setText("Stopped: " + message)
        QMessageBox.warning(self, WINDOW_TITLE, message)

    def stop_and_wait(self):
        """Cancel a running job and wait for its thread (app closing)."""
        if self.is_running:
            if self._worker:
                self._worker.cancel()
            self._thread.quit()
            self._thread.wait(CLOSE_WAIT_MS)
