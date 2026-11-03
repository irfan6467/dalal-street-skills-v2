# Blind test, Monday 2 November 2026: pre-registration

Written and pushed **before** any NSE data for the test day, or the sessions before it that have not been fetched
yet, was downloaded into this workspace. The git commit time of this file is the proof. Nothing below may change
after the screening runs; `blind/blind_test.py` reads every number from `rules.json` next to this file, and the
SHA-256 of `rules.json` is printed in every output.

## Question

On Monday 2 November 2026, would two rule sets, screening only on data up to the session before, have made or lost
money intraday with ₹25,000 each, after Shoonya charges and slippage?

## Data and the wall between screening and outcome

- NSE `sec_bhavdata_full` end-of-day files only (open, high, low, close, volume, turnover, delivery %).
- **Screen phase**: downloads sessions from 2025-09-01 up to and including 2026-11-01 only. The test day's file is
  never requested in this phase. The signal day is the last session in that set.
- Picks, sizes, entry rules and stops are written to `picks.json`, hashed, committed and pushed.
- **Reveal phase**: only then is the 2026-11-02 file downloaded and the picks scored.
- If 2026-11-02 was not a trading session, the test is void and says so; no other day is substituted.

## Accounts (identical for both rule sets, each its own ₹25,000)

| setting | value |
| --- | --- |
| capital | ₹25,000 per rule set |
| max loss per trade | ₹500, charges included |
| max positions | 3; if more qualify, the 3 with the highest 20-session median turnover |
| position value cap | ₹8,333.33 (no leverage) |
| product / broker | MIS intraday, Shoonya charges (0.03% capped at ₹5 an order, statutory charges) |
| slippage | 20 bp on every fill |

## Universe (both rule sets)

All stocks in the signal-day bhavcopy, series EQ or BE, raw close ≥ ₹20, 20-session median turnover ≥ ₹50 lakh, and
not locked at one price on the signal day.

## Rule set A: momentum (frozen, params `981319a2c38f`)

Exactly `nse-backtest` momentum `signal_ok` with the parameters frozen on 29 Oct 2026 (252-session closing breakout,
volume ≥ 1.5x its 20-day average, close in the top quarter of the range, 63-day return in the top 20%, delivery ≥ 30%,
≤ 40% in 5 days, ≤ 2 locked days in 10). Stop: the lower of the signal-day low and entry − 3.0 x ATR(14).
That rule set was found to have **NO EDGE** in its own walk-forward; this test does not change that.

## Rule set B: breakout with volume (new, written now)

Signal-day close above the highest high of the prior 20 sessions; close ≥ 2% above the previous close; volume ≥ 2x
its 20-session average; close in the top quarter of the day's range; delivery ≥ 30% (if reported).
Stop: the higher (tighter) of the signal-day low and entry − 1.5 x ATR(14).

## Trade rules on 2 November (both)

1. Entry at the open plus 20 bp. Skipped if the open is more than 5% above the signal close, at or below the stop,
   or the day is locked at one price.
2. Quantity: the largest whole number with qty x (entry − stop) + round-trip charges at the stop ≤ ₹500 and
   qty x entry ≤ ₹8,333.33. Fewer than 1 share: skipped.
3. Target 2R (entry + 2 x (entry − stop)).
4. With only the day's open, high, low and close: if the low reaches the stop, the exit is the stop (the worse of
   stop and open) minus 20 bp, **even if the high also reached the target** (stop assumed first). Else if the high
   reaches the target, exit at the target minus 20 bp. Else exit at the close minus 20 bp (the close stands in for
   the 15:10 flat-by).

## Scoring

Per rule set: net rupees after charges, return on ₹25,000, R per trade, exits by type.
Benchmark: the average open-to-close return (minus 2 x 20 bp) of every stock in the universe that day, so a rule
set is compared with simply buying the liquid market at the open.
One day is one sample: the result says what happened, not whether either rule set has an edge.
