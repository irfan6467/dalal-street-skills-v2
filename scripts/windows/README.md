# Windows helper scripts (laptop)

Double-click runners used for the 29 Oct 2026 live test. Each runs from the repo root (`%~dp0` is wherever the file
sits, so copy them to the repo root first) and writes its output to `nse\*.log`. Shoonya login stays manual:
`py nse-shoonya\shoonya.py login` in your own PowerShell.

| file | what | touches Shoonya? |
| --- | --- | --- |
| `run-backtest.bat` | download 2 years of NSE bhavcopies, run the momentum walk-forward | no (NSE public files) |
| `run-scan.bat` | selftest, switch to Shoonya data, Nifty 50 ORB scan | read-only data |
| `paper-prep.bat` | entry, exit plan, quote, budget, risk, ticket for one stock (edit the symbol) | read-only data |
| `paper-approve.bat` | records the yes and books the PAPER fill | no orders sent |
| `track.bat` + `track_loop.py` | checks the paper position every minute until 14:50 | read-only data |
| `flat-prep.bat` / `flat-approve.bat` | builds and books the paper exit | no orders sent |

None of them can send an order: `"paper": true` in `nse\config.json`, and live sends need the operator's consent
code typed in their own terminal.
