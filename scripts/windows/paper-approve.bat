@echo off
REM Paper approval: records the users typed yes and books the fill in the PAPER ledger. Nothing is sent to Shoonya.
cd /d "%~dp0"
del nse\paper-approve.done 2>nul
py -u nse-alerts\alerts.py quote BEL --write > nse\p7-quote.log 2>&1
py -u nse-broker\ticket.py approve --index 1 --reply "yes" > nse\p8-approve.log 2>&1
py -u nse-broker\ticket.py log > nse\p9-log.log 2>&1
echo DONE > nse\paper-approve.done
timeout /t 10
