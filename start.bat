@echo off
setlocal

cd /d "%~dp0"
title A-Share Stock Selector

set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not defined PYTHON_EXE set "PYTHON_EXE=python"

echo Starting http://127.0.0.1:8000
echo Keep this window open while using the system. Press Ctrl+C to stop.

start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8000'"
"%PYTHON_EXE%" run_dev.py

if errorlevel 1 (
    echo.
    echo [ERROR] The service stopped unexpectedly. Check the message above.
    pause
)

endlocal
