@echo off
rem POS PPK Processor - first run creates .venv with Python 3.12 and installs packages.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating Python 3.12 environment...
    py -3.12 -m venv .venv || (echo Python 3.12 not found. Install it from python.org & pause & exit /b 1)
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
)
start "" ".venv\Scripts\pythonw.exe" app.py
