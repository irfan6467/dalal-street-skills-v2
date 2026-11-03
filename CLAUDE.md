# Handoff for Claude Code sessions on this repo

Owner: Irfan (GitHub `irfan6467`, Hyderabad, IST). Fork of `Adityaraj0421/dalal-street-skills` (MIT), extended to
v2: Shoonya live data, walk-forward backtester, forward-test gate, consent-gated Shoonya orders, daily cloud job,
blind one-day tests. 14 skills, 624 offline tests (`python3 -m unittest discover -s tests -p "test_*.py"`).

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
- **Daily cloud job** built and tested offline (`scripts/cloud/`), **not yet scheduled** (waiting on NSE network access).

## In progress: blind test for Mon 2 Nov 2026 (`blind/2026-11-02/`)

Pre-registered and pushed in commit `827243e` (2026-11-03 09:11 IST) **before any 2 Nov data was fetched**.
`rules.json` sha256 `db6d82f449fa921c36f83f63367b9bb204ce7247371a3da3958285386e41478f`. Two rule sets (A frozen
momentum, B breakout with volume), ₹25,000 each, ₹500 max loss a trade, 3 positions, no leverage, MIS, Shoonya
charges, 20 bp slippage. Irfan asked that the outcome stay unknown to Claude until the picks are locked.

Next steps, in order, once NSE is reachable:
1. `python3 blind/blind_test.py screen blind/2026-11-02` (fetches history to 2026-11-01 only; refuses if the test
   day is in its data). Commit and push `picks.json`; note its sha256.
2. Only then: `python3 blind/blind_test.py reveal blind/2026-11-02 --picks-sha <sha>`. Commit and push
   `result.json` and `RESULT.md`. Report to Irfan; one day is one sample, say so.
3. Never edit `rules.json` or `picks.json` after they are committed; never run reveal before screen is pushed.
4. Then offer to schedule `scripts/cloud/run_daily.sh` as a weekday cloud scheduled task (~18:30 IST).

Build notes: `_shared/build/build.py` regenerates size.py, check.py, ticket.py, backtest.py and copies
`_shared/shoonya_feed.py` into every skill; edit the templates in `_shared/`, never the generated copies.
