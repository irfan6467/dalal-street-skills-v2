# Daily cloud job

Runs in Claude's cloud as a scheduled task on trading days after the close (about 18:30 IST, after NSE publishes
the bhavcopy). Public NSE end-of-day files only: it never touches Shoonya, never places orders, holds no secrets.

```bash
scripts/cloud/run_daily.sh        # fetch ~400 days of bhavcopies, write reports/<date>.md, update journal/, push
```

What each run writes:

| path | what |
| --- | --- |
| `reports/YYYY-MM-DD.md` | delivery-% spikes, bulk and block deals, momentum setups (study only), shadow-journal score |
| `journal/shadow-momentum.json` | every momentum signal since the journal began, with frozen params `981319a2c38f`, re-scored daily on data newer than the backtest |
| `journal/deals.json` | bulk/block deal history (nse-bigmoney state) |

The momentum parameters are frozen from `results/2026-10-29/backtest/`, which found **NO EDGE** after charges. The
shadow journal exists to test that honestly on new data; it is not a trading signal. Costs and charges come from
`settings.json` (₹3,500 a trade, 20 bp slippage, Shoonya charges).

Network: the cloud environment must allow `nsearchives.nseindia.com` and `archives.nseindia.com`
(claude.ai Settings > Capabilities). Without that the job stops with that message and writes nothing.
Live Shoonya work (data, scans, paper trades) stays on the laptop: Shoonya only accepts the whitelisted IP.
