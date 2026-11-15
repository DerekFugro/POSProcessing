"""Settings tab: everything that changes how the app behaves, saved to settings.json.

Two sections: Recording (PCS connection and session name) and Processing (tool paths, CORS,
PPK and CSV options). Changes apply when Save is pressed.
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)

from posproc.config import Settings
from posproc.logger import DEFAULT_PORT, DEFAULT_SESSION_NAME

from . import styles

MARGIN = 8
SPACING = 6
CONTENT_WIDTH = 720
MAX_PORT = 65535
BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')     # not allowed in a Windows folder name
WINDOW_TITLE = "Settings"
SAVED_TEXT = "Saved."
UNSAVED_TEXT = "Changes not saved yet."


class SettingsPanel(QWidget):
    saved = Signal()                    # settings were changed and written to settings.json

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._create_widgets()
        self._create_layouts()
        self._connect_signals()
        self._apply_styles()
        self._load_initial_state()

    def _create_widgets(self):
        self.scroll_area = QScrollArea()
        self.content = QWidget()

        self.recording_group = QGroupBox("Recording")
        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("Found automatically from the POS display data")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, MAX_PORT)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText(DEFAULT_SESSION_NAME)
        self.name_hint = QLabel()
        self.name_hint.setWordWrap(True)

        self.processing_group = QGroupBox("Processing")
        self.convert_exe_input = QLineEdit()
        self.convert_exe_button = QPushButton("Browse...")
        self.rtklib_exe_input = QLineEdit()
        self.rtklib_exe_button = QPushButton("Browse...")
        self.rinex_version_combo = QComboBox()
        self.rinex_version_combo.addItems(["3.04", "3.02", "2.11"])
        self.rt27_ext_input = QLineEdit()
        self.max_baseline_spin = QDoubleSpinBox()
        self.max_baseline_spin.setRange(1, 1000)
        self.max_baseline_spin.setSuffix(" km")
        self.stations_spin = QSpinBox()
        self.stations_spin.setRange(1, 10)
        self.elmask_spin = QDoubleSpinBox()
        self.elmask_spin.setRange(0, 45)
        self.elmask_spin.setSuffix(" deg")
        self.armode_combo = QComboBox()
        self.armode_combo.addItems(["fix-and-hold", "continuous", "instantaneous", "off"])
        self.use_float_check = QCheckBox("Use float PPK solutions as well as fixed")
        self.noise_spin = QDoubleSpinBox()
        self.noise_spin.setDecimals(3)
        self.noise_spin.setRange(0.001, 1.0)
        self.noise_spin.setSuffix(" m/sqrt(s)")
        self.csv_suffix_input = QLineEdit()
        self.csv_units_combo = QComboBox()
        self.csv_units_combo.addItems(["auto", "ft", "m"])
        self.csv_tol_spin = QDoubleSpinBox()
        self.csv_tol_spin.setDecimals(3)
        self.csv_tol_spin.setRange(0.001, 1.0)
        self.csv_tol_spin.setSuffix(" m")
        self.manual_base_input = QLineEdit()
        self.manual_base_input.setPlaceholderText("Optional: your own base RINEX files, separated by ;")
        self.manual_xyz_input = QLineEdit()
        self.manual_xyz_input.setPlaceholderText("ITRF2020 X, Y, Z of manual base ARP (m)")
        self.convert_row = QWidget()
        self.rtklib_row = QWidget()

        self.save_button = QPushButton("Save")
        self.undo_button = QPushButton("Undo changes")
        self.state_label = QLabel()

    def _create_layouts(self):
        self.recording_layout = QFormLayout(self.recording_group)
        self.recording_layout.setSpacing(SPACING)
        self.recording_layout.addRow("PCS IP address", self.host_input)
        self.recording_layout.addRow("Logging port", self.port_spin)
        self.recording_layout.addRow("Session name", self.name_input)
        self.recording_layout.addRow("", self.name_hint)

        self.convert_layout = QHBoxLayout(self.convert_row)
        self.convert_layout.setContentsMargins(0, 0, 0, 0)
        self.convert_layout.addWidget(self.convert_exe_input)
        self.convert_layout.addWidget(self.convert_exe_button)
        self.rtklib_layout = QHBoxLayout(self.rtklib_row)
        self.rtklib_layout.setContentsMargins(0, 0, 0, 0)
        self.rtklib_layout.addWidget(self.rtklib_exe_input)
        self.rtklib_layout.addWidget(self.rtklib_exe_button)
        self.processing_layout = QFormLayout(self.processing_group)
        self.processing_layout.setSpacing(SPACING)
        self.processing_layout.addRow("Trimble convertToRinex.exe", self.convert_row)
        self.processing_layout.addRow("RTKLIB rnx2rtkp.exe", self.rtklib_row)
        self.processing_layout.addRow("RINEX version", self.rinex_version_combo)
        self.processing_layout.addRow("RT27 file extension", self.rt27_ext_input)
        self.processing_layout.addRow("Max base distance", self.max_baseline_spin)
        self.processing_layout.addRow("Base stations to try", self.stations_spin)
        self.processing_layout.addRow("Elevation mask", self.elmask_spin)
        self.processing_layout.addRow("Ambiguity mode", self.armode_combo)
        self.processing_layout.addRow("", self.use_float_check)
        self.processing_layout.addRow("Real-time drift (process noise)", self.noise_spin)
        self.processing_layout.addRow("CSV output suffix", self.csv_suffix_input)
        self.processing_layout.addRow("CSV elevation units", self.csv_units_combo)
        self.processing_layout.addRow("CSV match tolerance", self.csv_tol_spin)
        self.processing_layout.addRow("Manual base RINEX", self.manual_base_input)
        self.processing_layout.addRow("Manual base XYZ", self.manual_xyz_input)

        self.button_layout = QHBoxLayout()
        self.button_layout.addWidget(self.save_button)
        self.button_layout.addWidget(self.undo_button)
        self.button_layout.addWidget(self.state_label, 1)

        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        self.content_layout.setSpacing(SPACING)
        self.content_layout.addWidget(self.recording_group)
        self.content_layout.addWidget(self.processing_group)
        self.content_layout.addLayout(self.button_layout)
        self.content_layout.addStretch()
        self.scroll_area.setWidget(self.content)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.addWidget(self.scroll_area)

    def _connect_signals(self):
        self.convert_exe_button.clicked.connect(self._on_convert_exe_button_clicked)
        self.rtklib_exe_button.clicked.connect(self._on_rtklib_exe_button_clicked)
        self.save_button.clicked.connect(self._on_save_button_clicked)
        self.undo_button.clicked.connect(self._on_undo_button_clicked)
        self.name_input.textChanged.connect(self._update_name_hint)
        for w in (self.host_input, self.name_input, self.rt27_ext_input, self.csv_suffix_input,
                  self.convert_exe_input, self.rtklib_exe_input, self.manual_base_input, self.manual_xyz_input):
            w.textEdited.connect(self._on_any_changed)
        for w in (self.port_spin, self.stations_spin):
            w.valueChanged.connect(self._on_any_changed)
        for w in (self.max_baseline_spin, self.elmask_spin, self.noise_spin, self.csv_tol_spin):
            w.valueChanged.connect(self._on_any_changed)
        for w in (self.rinex_version_combo, self.armode_combo, self.csv_units_combo):
            w.currentIndexChanged.connect(self._on_any_changed)
        self.use_float_check.toggled.connect(self._on_any_changed)

    def _apply_styles(self):
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.content.setMaximumWidth(CONTENT_WIDTH)
        self.save_button.setStyleSheet(styles.RUN_BUTTON)
        self.name_hint.setStyleSheet(f"color: {styles.TEXT_SECONDARY};")
        self.state_label.setStyleSheet(f"color: {styles.TEXT_SECONDARY};")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

    def _load_initial_state(self):
        self.load_values()

    # --- Business logic ---
    def load_values(self):
        """Fill the form from the current settings (drops unsaved edits)."""
        s = self.settings
        widgets = (self.port_spin, self.stations_spin, self.max_baseline_spin, self.elmask_spin, self.noise_spin,
                   self.csv_tol_spin, self.rinex_version_combo, self.armode_combo, self.csv_units_combo,
                   self.use_float_check)
        for w in widgets:
            w.blockSignals(True)
        self.host_input.setText(s.logger_host)
        self.port_spin.setValue(s.logger_port or DEFAULT_PORT)
        self.name_input.setText(getattr(s, "logger_session_name", "") or DEFAULT_SESSION_NAME)
        self.convert_exe_input.setText(s.convert_to_rinex_exe)
        self.rtklib_exe_input.setText(s.rnx2rtkp_exe)
        self.rinex_version_combo.setCurrentText(s.rinex_version)
        self.rt27_ext_input.setText(s.rt27_extension)
        self.max_baseline_spin.setValue(s.max_baseline_km)
        self.stations_spin.setValue(s.stations_to_try)
        self.elmask_spin.setValue(s.elevation_mask_deg)
        self.armode_combo.setCurrentText(s.ar_mode)
        self.use_float_check.setChecked(s.use_float)
        self.noise_spin.setValue(s.process_noise_m_per_sqrt_s)
        self.csv_suffix_input.setText(s.csv_suffix)
        self.csv_units_combo.setCurrentText(s.csv_elev_units)
        self.csv_tol_spin.setValue(s.csv_match_tolerance_m)
        self.manual_base_input.setText(";".join(s.manual_base_rinex))
        self.manual_xyz_input.setText(", ".join(str(v) for v in s.manual_base_xyz))
        for w in widgets:
            w.blockSignals(False)
        self._update_name_hint()
        self.state_label.setText("")

    def _session_name(self) -> str:
        return self.name_input.text().strip() or DEFAULT_SESSION_NAME

    def _update_name_hint(self):
        name = self._session_name()
        if BAD_NAME_CHARS.search(name):
            self.name_hint.setText('Cannot contain \\ / : * ? " < > |')
        else:
            self.name_hint.setText(f"Each recording makes a folder like {name}_20261115_101245")

    def _on_any_changed(self, *_):
        self.state_label.setText(UNSAVED_TEXT)

    def _browse_exe(self, line: QLineEdit, title: str):
        path, _ = QFileDialog.getOpenFileName(self, title, line.text(), "Programs (*.exe);;All files (*)")
        if path:
            line.setText(path)
            self._on_any_changed()

    def _on_convert_exe_button_clicked(self):
        self._browse_exe(self.convert_exe_input, "Find convertToRinex.exe")

    def _on_rtklib_exe_button_clicked(self):
        self._browse_exe(self.rtklib_exe_input, "Find rnx2rtkp.exe")

    def _on_undo_button_clicked(self):
        self.load_values()

    def _on_save_button_clicked(self):
        self.save()

    def save(self) -> bool:
        """Check and save the form. Returns False (nothing saved) if a value is not allowed."""
        host, name = self.host_input.text().strip(), self._session_name()
        if not host:
            QMessageBox.warning(self, WINDOW_TITLE, "Enter the PCS IP address.")
            return False
        if BAD_NAME_CHARS.search(name):
            QMessageBox.warning(self, WINDOW_TITLE, 'The session name cannot contain \\ / : * ? " < > |')
            return False
        xyz_text = [v.strip() for v in self.manual_xyz_input.text().split(",") if v.strip()]
        try:
            xyz = [float(v) for v in xyz_text]
        except ValueError:
            QMessageBox.warning(self, WINDOW_TITLE, "Manual base XYZ must be three numbers: X, Y, Z.")
            return False
        if xyz and len(xyz) != 3:
            QMessageBox.warning(self, WINDOW_TITLE, "Manual base XYZ must be three numbers: X, Y, Z.")
            return False
        s = self.settings
        s.logger_host = host
        s.logger_port = self.port_spin.value()
        s.logger_session_name = name
        s.convert_to_rinex_exe = self.convert_exe_input.text().strip()
        s.rnx2rtkp_exe = self.rtklib_exe_input.text().strip()
        s.rinex_version = self.rinex_version_combo.currentText()
        ext = self.rt27_ext_input.text().strip() or ".rt27"
        s.rt27_extension = ext if ext.startswith(".") else "." + ext
        s.max_baseline_km = self.max_baseline_spin.value()
        s.stations_to_try = self.stations_spin.value()
        s.elevation_mask_deg = self.elmask_spin.value()
        s.ar_mode = self.armode_combo.currentText()
        s.use_float = self.use_float_check.isChecked()
        s.process_noise_m_per_sqrt_s = self.noise_spin.value()
        s.csv_suffix = self.csv_suffix_input.text().strip() or "_PPK"
        s.csv_elev_units = self.csv_units_combo.currentText()
        s.csv_match_tolerance_m = self.csv_tol_spin.value()
        s.manual_base_rinex = [p.strip() for p in self.manual_base_input.text().split(";") if p.strip()]
        s.manual_base_xyz = xyz
        s.save()
        self.state_label.setText(SAVED_TEXT)
        self.saved.emit()
        return True
