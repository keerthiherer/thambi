@echo off
setlocal
cd /d "%~dp0"
title THAMBI - private local assistant

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

"%PY%" -c "import tkinter, requests" >nul 2>nul
if errorlevel 1 (
  echo THAMBI dependencies are missing or incomplete.
  echo Run: powershell -ExecutionPolicy Bypass -File scripts\install.ps1
  pause
  exit /b 1
)

"%PY%" -m aether
if errorlevel 1 (
  echo.
  echo THAMBI exited with an error. See data\logs\ for details.
  pause
)
endlocal
