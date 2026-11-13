@echo off
chcp 65001 >nul
cd /d "%~dp0"
title BrainByte - Keyomars AI Agent v0.3.2
python run_web.py
if errorlevel 1 (
  echo.
  echo Python or the local server could not start. See README_FA.md
  pause
)
