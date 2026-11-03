@echo off
REM Paper exit preparation started by Claude: builds the closing SELL ticket in the PAPER ledger. Nothing is sent.
cd /d "%~dp0"
del nse\flat-prep.done 2>nul
py -u nse-broker\ticket.py flatten --symbol BEL > nse\f1-flatten.log 2>&1
py -u nse-alerts\alerts.py quote BEL --write > nse\f2-quote.log 2>&1
py -u nse-risk\check.py > nse\f3-risk.log 2>&1
py -u nse-broker\ticket.py show > nse\f4-ticket.log 2>&1
echo DONE > nse\flat-prep.done
timeout /t 10
