@echo off
chcp 65001 >nul
title DocuDesk Local Bridge
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3 is not installed or the py launcher was not found.
  echo Install Python from https://www.python.org/downloads/windows/
  echo During setup, enable "Add python.exe to PATH".
  pause
  exit /b 1
)
py -3 -c "import win32com.client; from PIL import Image; import zeroconf" >nul 2>nul
if errorlevel 1 (
  echo Installing required scanner components (WIA + network discovery)...
  py -3 -m pip install --user --upgrade pywin32 Pillow zeroconf
  if errorlevel 1 (
    echo.
    echo Could not install scanner components. Check internet access and Python installation.
    pause
    exit /b 1
  )
)
py -3 local_bridge.py
if errorlevel 1 (
  echo.
  echo Bridge stopped with an error.
  pause
)
