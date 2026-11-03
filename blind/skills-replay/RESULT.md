# Full-skill blind replay: Thursday 15 Oct 2026

Test day drawn from the hash of `rules.json` (`draw.json`); picks locked in `22d75d1` and the replay driver in
`6016905`, both pushed before any 15 Oct bar was read. Settings: ₹25,000, ₹500 max loss a trade, 3 trades,
₹3,000 daily loss limit, 33.33% position cap, no leverage, MIS, Shoonya charges, 20 bp slippage a fill.

## Result

| run | trades | net after charges | after 20 bp slippage |
| --- | ---: | ---: | ---: |
| **Official** (rules.json as written) | 0 | ₹0 | ₹0 |
| Shadow (news gate removed, decided before any 15 Oct data) | 1 | −₹95.07 | **−₹127.73** |
| Nifty 200, open to close, 20 bp a side | | average −0.45% | 84 of 200 up |

One day is one sample. Neither run says anything about an edge.

## What each skill did

| skill | as of | what happened |
| --- | --- | --- |
| nse-backtest | 14 Oct | momentum walk-forward 29 Oct 2024 to 14 Oct 2026 on de-duplicated NSE history: **NO EDGE** (−0.143R per entry day, 95% interval −0.31 to +0.03; 567 out-of-sample trades, profit factor 1.008) |
| nse-validate | 14 Oct | refused the run: only a CANDIDATE is forward-tested |
| nse-scanner | 14 Oct | Nifty 200: 40 daily-close setups, 6 delivery spikes; top 10 by turnover: TCS, INFY, MEESHO, BHEL, HCLTECH, HINDUNILVR, CANBK, NESTLEIND, ADANIENSOL, MARUTI |
| nse-news | 15 Oct 09:00 | 168 headlines; every one of the 10 had a results or SEBI/regulatory item (Q2 season), so the official run stands aside |
| nse-analyst | 14 Oct | June-quarter numbers only (context, no gate), `phase1/analyst-point-in-time.json` |
| nse-bigmoney | n/a | not run: NSE serves only the latest day's bulk/block file |
| nse-shoonya | n/a | not run: laptop only (status here: not logged in) |
| nse-alerts | 15 Oct | clock, plan alerts for the trade, a check every 5 minutes |
| nse-entry | 15 Oct | 6 opening-range breakouts: INFY 09:45; TCS, HCLTECH, MARUTI 09:50; MEESHO 10:10; ADANIENSOL 10:20 |
| nse-exit | 15 Oct | midpoint stop, 2R target, flat by 14:55 (F&O) or 15:10 |
| nse-timemachine | 15 Oct | passed 1 of 6: MEESHO, average +0.262R; the other five averaged −0.19R to −0.38R |
| nse-budget | 15 Oct | MEESHO 36 shares at ₹228.00 (the position cap bound: ₹8,208; the ₹500 risk limit allowed 196) |
| nse-risk | 15 Oct | PASS: max loss ₹95.07 incl. charges, reward:risk 1.72 after charges |
| nse-broker | 15 Oct | paper: "yes" (typed under the rule, not Irfan's consent), filled at ₹228.00 at 10:10; stop ₹225.60 hit at 12:10 |

## Hindsight (not a rule, shown so the gate can be judged)

The five triggers the time machine rejected, played with their own exit plans: INFY −0.48R, TCS −0.60R,
HCLTECH +0.66R, MARUTI **+2.00R** (target), ADANIENSOL **+2.00R** (target), before charges. The one it accepted
lost 1R. On this day the gate picked the loser; one day cannot say whether it helps or hurts. The news gate's worst
fears did come true for stocks that never broke out: NESTLEIND −7.92% (results that day), CANBK −5.70% and BHEL
−3.99% (results the day before).

## Problems found on the way

- **nse-alerts missed the stop.** At 12:10 the last price was 225.60000610351562 (Yahoo's float) against a stop of
  225.6, so `price <= stop` was false and the stop alert never rang; it also checks only the last price, so a bar
  whose low went through the stop (225.31) between checks cannot ring. The broker's tracker booked the stop
  correctly from the bar's low.
- The news rule as written cannot run in results season: any headline mentioning Q2 drops the stock.
- The position cap (33.33%, no leverage) binds long before the ₹500 risk limit: the trade risked ₹95. With
  opening-range stops near 1%, the risk limit never matters at this size.
- The scanner's header said "Yahoo spark" for closes built from NSE files (the label fix knows only Shoonya and
  Yahoo).
- The installed claude.ai copies of the skills are older than this repo (no holiday-file fix); this replay ran the
  repo's scripts.

Files: `rules.json`, `draw.json`, `PREREGISTRATION.md`, `picks.json`, `phase1/` (screen logs), `phase2/` (every
skill call with its command, exit code and output), `result.json`, `tools/` (the scripts that built the inputs and
drove the day).
