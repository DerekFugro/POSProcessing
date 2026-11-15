"""Dark theme colours and stylesheets."""
DARK_BG = "#2c3e50"
DARK_BG_ALT = "#34495e"
TEXT_PRIMARY = "#ecf0f1"
TEXT_SECONDARY = "#bdc3c7"
ACCENT_BLUE = "#3498db"
ACCENT_GREEN = "#27ae60"
ACCENT_RED = "#e74c3c"
ACCENT_ORANGE = "#f39c12"
BORDER_COLOR = "#1a252f"

APP = f"""
QWidget {{ background-color: {DARK_BG}; color: {TEXT_PRIMARY}; font-size: 10pt; }}
QGroupBox {{ border: 1px solid {BORDER_COLOR}; border-radius: 4px; margin-top: 14px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {TEXT_SECONDARY}; }}
QListWidget, QPlainTextEdit, QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
    background-color: {DARK_BG_ALT}; border: 1px solid {BORDER_COLOR}; border-radius: 3px; padding: 3px; }}
QPushButton {{ background-color: {DARK_BG_ALT}; border: 1px solid {BORDER_COLOR}; border-radius: 4px; padding: 5px 10px; }}
QPushButton:hover {{ background-color: #3d566e; }}
QPushButton:disabled {{ color: #7f8c8d; }}
QTabWidget::pane {{ border: 1px solid {BORDER_COLOR}; }}
QTabBar::tab {{ background: {DARK_BG_ALT}; padding: 6px 14px; border: 1px solid {BORDER_COLOR}; }}
QTabBar::tab:selected {{ background: {ACCENT_BLUE}; }}
QTabBar::tab:disabled {{ background: {DARK_BG}; color: #7f8c8d; }}
QProgressBar {{ border: 1px solid {BORDER_COLOR}; border-radius: 3px; text-align: center; background: {DARK_BG_ALT}; }}
QProgressBar::chunk {{ background-color: {ACCENT_GREEN}; }}
"""

RUN_BUTTON = f"""
QPushButton {{ background-color: {ACCENT_GREEN}; color: white; font-weight: bold; border-radius: 4px; padding: 8px; }}
QPushButton:hover {{ background-color: #2ecc71; }}
QPushButton:disabled {{ background-color: #7f8c8d; }}
"""
RECORD_BUTTON = f"""
QPushButton {{ background-color: {ACCENT_GREEN}; color: white; font-size: 18pt; font-weight: bold;
              border-radius: 8px; padding: 14px; }}
QPushButton:hover {{ background-color: #2ecc71; }}
QPushButton:disabled {{ background-color: #7f8c8d; }}
"""
STOP_RECORD_BUTTON = f"""
QPushButton {{ background-color: {ACCENT_RED}; color: white; font-size: 18pt; font-weight: bold;
              border-radius: 8px; padding: 14px; }}
QPushButton:hover {{ background-color: #ff6b5b; }}
"""
CANCEL_BUTTON = f"""
QPushButton {{ background-color: {ACCENT_RED}; color: white; border-radius: 4px; padding: 8px; }}
QPushButton:disabled {{ background-color: #7f8c8d; }}
"""
