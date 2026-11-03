@echo off
REM Paper exit approval: records the users typed yes and books the exit in the PAPER ledger. Nothing is sent.
cd /d "%~dp0"
del nse\flat-approve.done 2>nul
py -u nse-alerts\alerts.py quote BEL --write > nse\f5-quote.log 2>&1
py -u nse-broker\ticket.py approve --index 1 --reply "yes" > nse\f6-approve.log 2>&1
py -u nse-broker\ticket.py log > nse\f7-log.log 2>&1
echo DONE > nse\flat-approve.done
timeout /t 10
