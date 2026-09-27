@echo off
rem Standalone Windows build: dist-windows\KUMOsystems-<version>-win64.zip (see scripts\build_windows.py).
cd /d "%~dp0.."
if not exist backend\.venv\Scripts\python.exe (
  echo Backend .venv not found: create it once, see README.md
  exit /b 1
)
backend\.venv\Scripts\python.exe scripts\build_windows.py %*
