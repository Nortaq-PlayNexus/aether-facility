@echo off
REM AETHER FACILITY - One-click launcher
REM Opens the command center. Full sweep (2+3+5+6) is the recommended daily flow.
title AETHER FACILITY - Command Center
cd /d "%~dp0"
python "%~dp0_SCRIPTS\facility.py" menu
pause
