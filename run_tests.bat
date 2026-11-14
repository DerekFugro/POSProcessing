@echo off
cd /d "%~dp0"
set POSPROC_SAMPLES=F:\Camera Calibrations\WithLoops\2026\20260425_ARAN69_VA26_ Surveyor
".venv\Scripts\python.exe" -m pytest -q
pause
