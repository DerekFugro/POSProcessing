"""Settings dialog: tool paths and processing options (saved to settings.json)."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from posproc.config import Settings

from . import styles

SPACING = 6
MIN_WIDTH = 640


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._create_widgets()
        self._create_layouts()
        self._connect_signals()
        self._apply_styles()
        self._load_initial_state()

    def _create_widgets(self):
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
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                           QDialogButtonBox.StandardButton.Cancel)
        self.convert_row = QWidget()
        self.rtklib_row = QWidget()

    def _create_layouts(self):
        self.convert_layout = QHBoxLayout(self.convert_row)
        self.convert_layout.setContentsMargins(0, 0, 0, 0)
        self.convert_layout.addWidget(self.convert_exe_input)
        self.convert_layout.addWidget(self.convert_exe_button)
        self.rtklib_layout = QHBoxLayout(self.rtklib_row)
        self.rtklib_layout.setContentsMargins(0, 0, 0, 0)
        self.rtklib_layout.addWidget(self.rtklib_exe_input)
        self.rtklib_layout.addWidget(self.rtklib_exe_button)
        self.form_layout = QFormLayout()
        self.form_layout.setSpacing(SPACING)
        self.form_layout.addRow("Trimble convertToRinex.exe", self.convert_row)
        self.form_layout.addRow("RTKLIB rnx2rtkp.exe", self.rtklib_row)
        self.form_layout.addRow("RINEX version", self.rinex_version_combo)
        self.form_layout.addRow("RT27 file extension", self.rt27_ext_input)
        self.form_layout.addRow("Max base distance", self.max_baseline_spin)
        self.form_layout.addRow("Base stations to try", self.stations_spin)
        self.form_layout.addRow("Elevation mask", self.elmask_spin)
        self.form_layout.addRow("Ambiguity mode", self.armode_combo)
        self.form_layout.addRow("", self.use_float_check)
        self.form_layout.addRow("Real-time drift (process noise)", self.noise_spin)
        self.form_layout.addRow("CSV output suffix", self.csv_suffix_input)
        self.form_layout.addRow("CSV elevation units", self.csv_units_combo)
        self.form_layout.addRow("CSV match tolerance", self.csv_tol_spin)
        self.form_layout.addRow("Manual base RINEX", self.manual_base_input)
        self.form_layout.addRow("Manual base XYZ", self.manual_xyz_input)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.addLayout(self.form_layout)
        self.main_layout.addWidget(self.button_box)

    def _connect_signals(self):
        self.convert_exe_button.clicked.connect(self._on_convert_exe_button_clicked)
        self.rtklib_exe_button.clicked.connect(self._on_rtklib_exe_button_clicked)
        self.button_box.accepted.connect(self._on_button_box_accepted)
        self.button_box.rejected.connect(self.reject)

    def _apply_styles(self):
        self.setWindowTitle("Settings")
        self.setMinimumWidth(MIN_WIDTH)
        self.setStyleSheet(styles.APP)

    def _load_initial_state(self):
        s = self.settings
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

    # --- Business logic ---
    def _browse_exe(self, line: QLineEdit, title: str):
        path, _ = QFileDialog.getOpenFileName(self, title, line.text(), "Programs (*.exe);;All files (*)")
        if path:
            line.setText(path)

    def _on_convert_exe_button_clicked(self):
        self._browse_exe(self.convert_exe_input, "Find convertToRinex.exe")

    def _on_rtklib_exe_button_clicked(self):
        self._browse_exe(self.rtklib_exe_input, "Find rnx2rtkp.exe")

    def _on_button_box_accepted(self):
        s = self.settings
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
        xyz = [v.strip() for v in self.manual_xyz_input.text().split(",") if v.strip()]
        try:
            s.manual_base_xyz = [float(v) for v in xyz]
        except ValueError:
            s.manual_base_xyz = []
        s.save()
        self.accept()
