---
name: nse-backtest
description: >-
  Walk-forward backtester for NSE strategies, judged only on out-of-sample
  trades after full Indian charges (STT, exchange, SEBI, stamp, GST,
  brokerage, DP) and the user's own slippage. Two strategies: micro/small-cap
  momentum swing (delivery) on NSE bhavcopies with pump-and-dump, circuit-lock
  and low-delivery filters, and intraday opening-range breakout (MIS) on
  5-minute bars. Point-in-time daily data (delisted stocks included), split
  and bonus adjustment from NSE's own prices, gaps through stops filled at the
  open, locked days untradeable, day-level significance. Verdicts: NO EDGE,
  INSUFFICIENT DATA, INCONCLUSIVE or CANDIDATE, never "proven". Also emits
  today's signals from a run's frozen parameters so paper and live trading use
  the exact backtested rules. Use when the user says "backtest", "does this
  strategy work", "walk-forward", "test momentum", "test ORB", "is there an
  edge", "out of sample", "today's momentum signals", or before /nse-validate.
argument-hint: "run --strategy momentum|orb --from YYYY-MM-DD --to YYYY-MM-DD | signals --run ID | runs | fetch-bhav | fetch-bars"
---

# nse-backtest

On Windows, use `py` in place of `python3`.

A backtest that picks its parameters on the same data it reports is a story,
not evidence. This one picks parameters on a training window and reports only
what happened in the next window it had never seen, window after window. Its
best possible verdict is **CANDIDATE**: worth paper trading. Nothing here
proves an edge, and Claude must never describe a result as proven, guaranteed
or "will make money".

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
# data
python3 "${CLAUDE_SKILL_DIR}/backtest.py" fetch-bhav --from 2023-01-01 --to 2026-10-28      # ~3 years of NSE bhavcopies
python3 "${CLAUDE_SKILL_DIR}/backtest.py" fetch-bars RELIANCE TCS INFY HDFCBANK --interval 5m --range 60d
# tests
python3 "${CLAUDE_SKILL_DIR}/backtest.py" run --strategy momentum --from 2023-01-01 --to 2026-10-28
python3 "${CLAUDE_SKILL_DIR}/backtest.py" run --strategy orb --from 2026-08-03 --to 2026-10-28
python3 "${CLAUDE_SKILL_DIR}/backtest.py" runs                         # every run and how many variants were tried
# today's signals with a CANDIDATE run's frozen parameters
python3 "${CLAUDE_SKILL_DIR}/backtest.py" signals --run momentum-20261029-101500
```

Flags for `run`: `--train N --test M` (months for momentum, default 12 and 3;
weeks for ORB, default 4 and 2), `--grid small|full`, `--min-trades 30`,
`--bhav-dir` / `--bars-dir` (defaults `nse/cache/bhav`, `nse/cache/bars`),
`--slippage-bps`, `--notional-per-trade`, `--json`.

## Before the first run: the user's cost assumptions

The tool refuses to run until the user has set, in `nse/backtest.json`:

```json
{"costs": {"slippage_bps": <number>, "notional_per_trade": <rupees>}}
```

Claude never fills these in on its own. Ask the user as a multiple-choice
question (typical values: slippage 5, 10 or 20 bp per side, more for
micro-caps; notional the rupee size they actually trade), then write what they
choose. The broker (for brokerage and DP charge) comes from `nse/config.json`.

## Reading a result

- Lead with the **verdict and its one-line reason**, then the out-of-sample
  numbers: average R after charges, the 95% interval per entry day, profit
  factor, max drawdown in R, worst losing streak.
- Point out the **gap between in-sample and out-of-sample**: that gap is the
  overfit.
- If `runs` shows several runs of the same strategy, say so. Rerunning with
  new settings after seeing results is how noise gets fitted. The first
  honest run counts most.
- CANDIDATE → next step is `/nse-validate start` with the run id. Not live
  money.
- NO EDGE or INCONCLUSIVE → say it plainly. Do not tune until it passes.

## The strategies

**momentum** (delivery, CNC, daily bhavcopy): close at an N-day high (63/126/252)
on volume ≥ 1.5–2.5× its 20-day average, closing in the top quarter of its
range, 63-day return in the top 20% of the market that day. Hygiene filters:
price ≥ ₹20, 20-day median turnover ≥ ₹50 lakh, delivery ≥ 30% on the signal
day, no more than +40% in 5 days, at most 2 circuit-locked days in the last 10,
EQ or BE series. Entry next open (skipped if it gaps over 5% or is locked).
Stop: the lower of the signal day's low and entry − 2–3 × ATR(14). Exit:
close below the 10/20-day low, the stop, or 40 days.

**orb** (intraday, MIS, 5-minute bars): opening range of 15 or 30 minutes; a
bar closing above it by 14:00 with volume ≥ 1–1.5× the range's bars; entry at
the next bar's open; stop at the range low or midpoint; target 1.5R, 2R or
none; flat by 15:10. Range must be 0.3%–3% of price. Long only.

## Known limits, said up front

- ASM/GSM surveillance lists are not published historically, so the backtest
  cannot exclude them day by day; the live scanner and `/nse-risk` do.
- Intraday history from Shoonya or Yahoo is weeks, not years. ORB results on
  60 days of bars are thin: expect INSUFFICIENT DATA or wide intervals, and
  keep fetching bars daily to build history.
- Fills use daily or 5-minute bars, not ticks. Slippage is the user's
  assumption; micro-caps can be far worse on the day it matters.
- Corporate actions are detected from NSE's adjusted PREV_CLOSE (moves over
  2%). Large special dividends can look like a split.
- Trades are counted one position per stock at a time with no portfolio cap,
  so results are per-trade edge, not a portfolio return.

## Files

| file | what |
| --- | --- |
| `nse/backtest.json` | `costs` (required), optional overrides |
| `nse/config.json` | broker (charges), read as in every skill |
| `nse/cache/bhav/` | `sec_bhavdata_full_DDMMYYYY.csv` from NSE |
| `nse/cache/bars/` | chart JSON per symbol (via the data provider, Shoonya or Yahoo) |
| `nse/backtest/<run>.json` | full result: folds, frozen params and hash, every out-of-sample trade |
| `nse/backtest/runs.jsonl` | one line per run, for the trial count |
| `nse/backtest/signals-<strategy>.json` | today's signals from `signals` |
