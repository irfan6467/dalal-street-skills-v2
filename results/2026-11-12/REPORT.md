# Backtest of 12 Nov 2026 on a ₹25,000 account: results and shortcomings

Settings, unchanged from 3 Nov: capital ₹25,000, ₹8,333 a trade (3 positions, no leverage), ₹500 max loss a
trade, 10% cash buffer, Shoonya charges, 20 bp slippage on every fill. Data: NSE daily files 2 Jan 2023 to
11 Nov 2026 (956 sessions) and Yahoo 5-minute bars for the Nifty 200, 17 Aug to 11 Nov 2026 (60 sessions).

## 1. Verdicts

| test | out-of-sample trades | per entry day | 95% interval | windows positive | verdict |
| --- | ---: | ---: | --- | ---: | --- |
| Momentum (delivery), walk-forward 12+3 months, Feb 2024 to Nov 2026 | 672 on 272 days | −0.160R | −0.32 to +0.01 | 2 of 8 | **NO EDGE** |
| ORB (intraday), the backtester's grid, walk-forward 4+2 weeks | 2,641 on 39 days | −0.402R | −0.49 to −0.32 | 0 of 5 | **NO EDGE** |
| ORB as the skills trade it by default (nothing fitted), 39 days | 4,140 on 39 days | −0.858R | t = −13 | | loses |

Not one of the 24 parameter sets of either strategy was positive over the test windows.

## 2. What ₹25,000 would have done

| what was traded | period | trades taken | result | worst drawdown |
| --- | --- | ---: | ---: | ---: |
| Momentum, 3 slots (which signal gets a free slot is luck: 300 orderings) | 21 months | 57 of 672 signals | median −₹4,569 (−₹12,510 to +₹3,164); 18% of orderings made money | median ₹7,292 |
| ORB, backtester's rules, first 3 triggers a day | 39 days | 117 of 2,641 | −₹4,779 (−19%) | ₹5,198 |
| A. Skills' ORB, all Nifty 200, first 3 a day | 39 days | 117 | −₹3,148 (−12.6%) | ₹3,148 |
| B. A + time machine gate | 39 days | 113 | −₹2,070 (−8.3%) | ₹2,810 |
| C. Scanner's top 10 only, first 3 | 37 days | 103 | −₹3,006 (−12.0%) | ₹3,006 |
| D. Scanner's top 10 + time machine (the 15 Oct chain) | 37 days | 20 | −₹606 (−2.4%) | ₹821 |

D loses least only because it trades least: its 20 trades averaged −0.72R.

## 3. Shortcomings, with the evidence and a fix

### A. The strategies

| # | shortcoming | evidence | fix |
| --- | --- | --- | --- |
| A1 | Neither strategy has an edge even before costs. | Before slippage and charges: momentum −0.02R a trade, ORB (grid) −0.001R, skills' ORB +0.06R. | Stop tuning these rules. Test a different idea, written down first and judged on new data. |
| A2 | The skills' ORB stop is too close for its costs. | Midpoint stop is 0.69% from the entry (median); a round trip costs 0.5% of the position. Costs: 0.81R a trade on average, 1.25R when the stop is 0.3-0.5% away, 2.0R under 0.3%. | A floor on the stop distance (for example, skip under 1%), or the range low as the stop. Not enough alone: stops over 1% still made −0.28R. |
| A3 | Momentum's ₹500 "max loss" is not a maximum overnight. | 6.1% of exits were gaps through the stop, at −2.14R on average; stops sit about 10% below the entry. | Size for the gap (smaller positions on wide stops), or accept that the figure is a typical loss, not a cap. |
| A4 | Later triggers are worse. | Before costs: triggers by 09:55 +0.17R; after 10:00, −0.08R to −0.02R. | A hypothesis to test forward ("triggers before 10:00 only"), not a result: it was found by slicing the same 39 days. |

### B. Costs and the size of the account

| # | shortcoming | evidence | fix |
| --- | --- | --- | --- |
| B1 | The 20 bp slippage setting decides everything intraday, and it has never been measured. | Skills' ORB by slippage: 0 bp −0.11R, 5 bp −0.27R, 10 bp −0.43R, 20 bp −0.75R. | Measure it: on the laptop, log Shoonya's best bid and ask at every paper decision and compare with the next trade price. Then set the figure from data. |
| B2 | The position cap always sets the size; the ₹500 rule never does intraday. | In 4,140 ORB trades the cap bound every time; average money at risk ₹59 (0.24% of capital). | A decision for Irfan: fewer, larger positions cut the fixed-cost share but concentrate risk. |
| B3 | Some stocks cannot be bought at all. | 280 of 4,420 triggers (6.3%) were in stocks priced above the ₹8,332 cap. | Drop them from the universe up front, so the time machine and the watchlist do not spend slots on them. |
| B4 | Three slots take a small, arbitrary sample of the signals. | Momentum: 57 of 672 signals; outcome range −₹12,510 to +₹3,164 from the same signals. ORB: 72 triggers a day (median) for 3 slots. | A ranking rule decided in advance (and backtested), instead of "first come". |

### C. The live skill chain

| # | shortcoming | evidence | fix |
| --- | --- | --- | --- |
| C1 | The rules the skills trade were never backtested by the tool. | The tool's ORB enters at the next bar's open, uses a 15/30-minute range, a volume filter, last entry 14:00, flat 15:10. The skills enter at the trigger close, 15-minute range, no volume filter, last entry 14:30/14:45, flat 14:55/15:10. | Add the skills' defaults to the backtester as a strategy (`results/2026-11-12/tools/live_chain.py` is a working draft). |
| C2 | The time machine gate barely separates good from bad. | Correlation of its forecast with the result: 0.11. Trades it passed made −0.59R, rejected −0.77R; before costs +0.07R against +0.06R. Its edge is that it passes wider stops (0.93% against 0.76%). | Keep it as information, not as a gate, until it shows skill on new days. |
| C3 | Only the backtester knows about slippage. | nse-exit, nse-budget, nse-risk and nse-timemachine contain no slippage at all. The time machine forecast −0.17R on average; the trades made −0.11R with no slippage and −0.75R with 20 bp. | Read `slippage_bps` in those four skills, so reward:risk, the gate and the minimum reward:risk are net of the same costs. |
| C4 | The scanner's watchlist adds nothing to next-day ORB. | Triggers in the top 10: −0.74R; all others: −0.75R. | Drop it from the intraday chain, or test another ranking forward. |
| C5 | Carried over from 3 Nov, still open. | nse-alerts misses a stop touched exactly (float comparison); the news gate drops every stock in results season; exits after flat-by not blocked; tracking can use a 15-minute-old quote. | As listed in CLAUDE.md. |

### D. The backtest tool

| # | shortcoming | evidence | fix |
| --- | --- | --- | --- |
| D1 | The fetcher skips weekends, so special sessions are missing. | Six real sessions were absent (Sat 1 Feb 2025 Budget day, Sun 12 Nov 2023 and Sun 8 Nov 2026 Muhurat, three special Saturdays). The next day's PREV_CLOSE then looked like a split: **4,613 false price adjustments** (985 on 3 Feb 2025). Added by hand for this run. | Ask for every calendar day; the date check added on 3 Nov already rejects copies. Affects the daily cloud job too. |
| D2 | Training uses results from inside the test window. | 245 of 3,026 training trades (8%) exited after the training window ended. Purging them changed the chosen parameters in 4 of 8 windows (result −0.14R, still NO EDGE). | Count a training trade only if it closed by the end of the window. |
| D3 | No warm-up check. | Started on the first day of data (as on 29 Oct and 3 Nov), the 252-day breakout had 22 training trades in window 1; with a year of earlier data, 214. | Refuse or warn when fewer than 260 sessions precede `--from`. |
| D4 | Open trades at the end are dropped. | 12 signals of the last window were still open (−0.13R marked to the last close); the window counted 1 trade. | Mark them to the last close and report them separately. |
| D5 | It sizes by a fixed rupee amount, not the way nse-budget does, and has no account. | 87.5% of momentum trades risked more than ₹500 at ₹8,333 (average ₹778); no limit on positions or cash. | A portfolio mode: capital, slots, the ₹500 rule, whole shares; report rupees and drawdown next to R (`tools/harness.py` does this). |
| D6 | ORB trades are saved without clock times. | "First 3 of the day" could not be read from a run; the times had to be recomputed. | Keep entry and exit times in the trade record. |
| D7 | The trial counter forgets. | It reports 4 momentum runs; counting 29 Oct, 3 Nov and today's four there are 6. It counts per working folder. | Keep the run log with the results in the repo. |

### E. Data

| # | shortcoming | evidence | fix |
| --- | --- | --- | --- |
| E1 | Intraday evidence is 39 test days from one stretch of market. | Yahoo keeps 60 days of 5-minute bars. | Save the bars every day (the cloud job can), or pull longer history from Shoonya on the laptop. |
| E2 | Today's Nifty 200 list was applied to August. | One list, downloaded 3 Nov. | Keep dated copies of the list. Small effect over 3 months. |

## 4. What I would do, in this order

1. Do not put real money on either strategy. Both lose before costs are even counted.
2. Fix D1 (missing sessions): it silently corrupts every later backtest and the daily job.
3. Measure slippage (B1) and put it into the live skills (C3). Until then every reward:risk the skills print is too high.
4. Make the backtester test what the skills trade, with an account (C1, D5, D6).
5. Only then pick one new idea, write its rules down, and judge it on days that have not happened yet.

## 5. Limits of this report

- The intraday findings rest on 39 days. The slices in A4, C2 and C4 were found on those same days.
- Tool fixes D2 to D4 do not change today's verdicts; they matter for a strategy that is close to the line.
- The account simulations book closed trades only and ignore brokerage minimums beyond the config.
- Momentum has now been run several times on overlapping data (the counter in D7). Verdicts were NO EDGE each time.

Files: `backtest/` (the tool's logs and runs, diagnostics, every out-of-sample trade, the 4,420 live-rule triggers
with the time machine's forecast, the daily watchlists), `tools/` (the scripts), `config-used.json`.
Not financial advice.
