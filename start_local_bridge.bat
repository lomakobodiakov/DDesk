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
py -3 local_bridge.py
if errorlevel 1 (
  echo.
  echo Bridge stopped with an error.
  pause
)
