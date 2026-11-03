@echo off
REM Paper position tracker started by Claude. Paper ledger only, no orders sent.
cd /d "%~dp0"
del nse\track.done 2>nul
echo Tracking BEL paper position every minute until 14:50 IST. Leave this window open.
py -u track_loop.py
echo Tracker finished.
timeout /t 15
