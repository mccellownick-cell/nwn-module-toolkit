@echo off
rem Double-click: analyse Vordan's Hero Creator with this NWN2 fork and write the HTML report.
rem Kept pure ASCII on purpose - the Cyrillic part of the path comes from %~dp0, not from this file.
chcp 65001 >nul
cd /d "%~dp0"
python analyse_vhc.py
echo.
pause
