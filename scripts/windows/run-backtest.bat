@echo off
REM Backtest run started by Claude: downloads NSE public bhavcopy files and runs a local backtest.
REM Read-only: no login, no broker, no orders.
cd /d "%~dp0"
echo [%time%] Downloading 2 years of NSE bhavcopies (about 10 minutes)...
py -u nse-backtest\backtest.py fetch-bhav --from 2024-10-29 --to 2026-10-28 > nse\fetch-bhav.log 2>&1
echo [%time%] Download finished. Running the momentum walk-forward backtest...
py -u nse-backtest\backtest.py run --strategy momentum --from 2024-10-29 --to 2026-10-28 > nse\backtest-momentum.log 2>&1
echo [%time%] DONE. Results are in nse\backtest-momentum.log
echo DONE > nse\backtest-run.done
pause
