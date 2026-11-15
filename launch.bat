@echo off
rem POS PPK Processor - first run creates .venv with Python 3.12 and installs packages.
rem Python comes from "py -3.12" if installed, otherwise uv installs it into .uv\python
rem in this folder. Not in AppData: on some PCs AppData written by another app (e.g. Claude)
rem is private to that app, and a .venv built on it does not start from Explorer.
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "" >nul 2>&1 || (
        echo The Python environment is broken ^(its base Python is missing^). Rebuilding...
        rmdir /s /q .venv
    )
)
if not exist ".venv\Scripts\python.exe" (
    call :make_venv || (pause & exit /b 1)
)

rem pythonw shows no console, so check the app imports first and show any error here.
".venv\Scripts\python.exe" -c "import PySide6, numpy, scipy, pyproj, matplotlib, requests" || (
    echo Packages are missing or broken. Delete the .venv folder and run launch.bat again.
    pause & exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" app.py
exit /b 0

:make_venv
echo Creating Python 3.12 environment...
py -3.12 -c "" >nul 2>&1 && goto :venv_from_py
where uv >nul 2>&1 || goto :no_python
set "UV_PYTHON_INSTALL_DIR=%~dp0.uv\python"
set "UV_CACHE_DIR=%~dp0.uv\cache"
uv python install 3.12 || exit /b 1
set "BASE_PY="
for /d %%D in (".uv\python\cpython-3.12*") do if exist "%%D\python.exe" set "BASE_PY=%%D\python.exe"
if not defined BASE_PY goto :no_python
"%BASE_PY%" -m venv .venv || exit /b 1
goto :install

:venv_from_py
py -3.12 -m venv .venv || exit /b 1

:install
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt || exit /b 1
exit /b 0

:no_python
echo Python 3.12 not found. Install it from python.org (tick "py launcher"), or install uv.
exit /b 1
