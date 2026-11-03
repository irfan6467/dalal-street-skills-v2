@echo off
REM Read-only live scan started by Claude: Shoonya market data only. No orders.
cd /d "%~dp0"
del nse\scan-run.done 2>nul
echo [%time%] Shoonya selftest...
py -u nse-shoonya\shoonya.py selftest > nse\shoonya-test.log 2>&1
echo [%time%] Switching every skill to Shoonya data...
py -u nse-shoonya\shoonya.py use shoonya > nse\use-shoonya.log 2>&1
echo [%time%] Opening-range breakout scan, Nifty 50 (1-2 minutes)...
py -u nse-scanner\scan.py orb --universe nifty50 > nse\scan-orb.log 2>&1
echo [%time%] DONE. You can close this window.
echo DONE > nse\scan-run.done
timeout /t 30
