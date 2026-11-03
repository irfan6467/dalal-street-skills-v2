# Handoff for Claude Code sessions on this repo

Owner: Irfan (GitHub `irfan6467`, Hyderabad, IST). Fork of `Adityaraj0421/dalal-street-skills` (MIT), extended to
v2: Shoonya live data, walk-forward backtester, forward-test gate, consent-gated Shoonya orders, daily cloud job,
blind one-day tests. 14 skills, 627 offline tests (`python3 -m unittest discover -s tests -p "test_*.py"`).

## How Irfan wants to work (follow every session)

- When a decision or a figure is needed from him: **multiple-choice questions** (A, B, C based on standard
  estimates, ending with "Other (please specify)"), use the AskUserQuestion tool, **one decision at a time**, wait.
- **Never change any financial setting on your own** (capital, risk, charges, slippage, leverage, positions).
- Be a direct, critical analytical partner: say when a premise or result is weak; no flattery.
- Never ask for, store, print or commit Shoonya secrets, TOTP or session tokens. `nse/` is gitignored for that reason.

## Where things run

| where | what |
| --- | --- |
| Claude cloud sessions / scheduled tasks | NSE public end-of-day files only: backtests, `scripts/cloud/run_daily.sh`, `blind/` tests |
| Irfan's Windows laptop | Shoonya (only his whitelisted IP works): login, live data, scans, paper trades (`scripts/windows/`) |

Cloud network: the cloud environment must allow `nsearchives.nseindia.com` and `archives.nseindia.com` (environment
settings > network access > Custom, keep the default list). Check with
`curl -s -o /dev/null -w "%{http_code}" https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_30102026.csv`
(200 = OK; 000 with `connect_rejected` = still blocked).

## State as of 3 Nov 2026

- **29 Oct live test** (`results/2026-10-29/REPORT.md`): Shoonya selftest PASS; momentum walk-forward on 2 years =
  **NO EDGE** (charges 73% of gross); one BEL paper trade +₹6.63 (proves the pipeline, not a strategy).
- Shoonya app in use is `FN195954_U2` (the old `FN195954_U` secret was exposed in chat; Irfan should disable it).
- Open bugs: exits after `flat_by` not blocked; `ticket.py track` may use a 15-minute-old quote; Shoonya MIS
  square-off time unverified (15:00 assumed).
- Fixed 3 Nov: output labelled Shoonya data as "Yahoo". Labels now read `meta.dataSource` (stamped by
  `shoonya_feed` on charts and spark entries) via `data_source()` / `mixed_source()` in `india_core.py`, pasted
  byte for byte into scan, entry, alerts, news, timemachine (`tests/test_source_labels.py` guards the copies).
  Still Yahoo-worded when Shoonya answers: parse error messages ("Yahoo has no data for X") and Yahoo-based limits
  (scanner refuses ORB replays over 6 days old; entry's 1-minute range limit; timemachine's 15:15 last bar).
- Fixed 3 Nov (`a2246a4`): on a holiday NSE serves the last session's bhavcopy under the holiday's name. The
  loader kept every copy: 16 phantom sessions per stock on 14 months of history and ~19.5k false split/bonus
  adjustments. Now `load_daily` counts a DATE1 once and `fetch_bhav` saves a file only if its DATE1 is the day
  asked. **The 29 Oct momentum walk-forward (NO EDGE) ran on the duplicated data: re-run it before relying on it.**
- **Daily cloud job** built and tested offline (`scripts/cloud/`), **not yet scheduled**. NSE is reachable from the
  cloud since 3 Nov (nsearchives drops clients without a browser-like User-Agent; the skills send one).

## Done: blind test for Mon 2 Nov 2026 (`blind/2026-11-02/`)

Pre-registered in `827243e`; picks locked and pushed in `3d12fe8` (picks sha256 `33ed12e7...`) before the 2 Nov
file was fetched; result in `RESULT.md`. Both sets took the same 3 stocks (COFORGE, LAURUSLABS, ZYDUSLIFE), no stop
or target hit, all out at the close: **A −₹156.36, B −₹211.07** while the screened universe averaged +1.0% open to
close (65% up). One day, three correlated stock-days: no evidence either way. A first screen made before the
holiday-file fix (sha `58cfd17e...`, same symbols, ATR ~1% lower) was superseded, by Irfan's choice, before reveal.
Never edit `rules.json`, `picks.json` or `result.json` there.

Next: offer to schedule `scripts/cloud/run_daily.sh` as a weekday cloud scheduled task (~18:30 IST); re-run the
momentum walk-forward on de-duplicated history.

Build notes: `_shared/build/build.py` regenerates size.py, check.py, ticket.py, backtest.py and copies
`_shared/shoonya_feed.py` into every skill; edit the templates in `_shared/`, never the generated copies.
