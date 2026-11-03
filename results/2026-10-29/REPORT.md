# First live test on Shoonya, 29 October 2026

Account: Shoonya (Finvasia), API app `FN195954_U2`, OAuth login. Paper mode throughout: nothing was sent to the broker.
Settings used (`config-used.json`, `backtest/backtest.json`): capital ₹3,500, max loss ₹70 per trade and ₹140 per day,
3 trades a day, no MIS leverage, Shoonya charges (MIS 0.03% capped at ₹5 an order, delivery free, DP ₹10.62 a sell),
backtest slippage 20 bp a side and ₹3,500 a trade.

## 1. Shoonya connection

`live-scan/shoonya-test.log`: selftest PASS. Bearer auth with the OAuth access token; quote with circuit band; 5-minute
bars complete (75 a session); today's daily bar built from the live quote. Two bugs found and fixed during the test:
per-stock tick sizes (RELIANCE trades in ₹0.10 steps, the pack assumed ₹0.05) and the missing intraday daily bar.

## 2. Backtest: micro/small-cap momentum, walk-forward

`backtest/backtest-momentum.log`, full trades in `backtest/momentum-20261029-083521.json`.
Data: 511 NSE bhavcopies, 29 Oct 2024 to 28 Oct 2026. 12 months train, 3 months test, 4 test windows.

**Verdict: NO EDGE.**

| out-of-sample | |
| --- | --- |
| trades | 496 on 183 entry days |
| win rate | 31% (winners +2.13R, losers −0.94R) |
| average per trade | +0.078R before charges, +0.018R after |
| average per entry day | −0.120R, 95% interval −0.36 to +0.15 |
| max drawdown / losing streak | 38.8R / 18 trades |
| rupees | ₹11,923 gross, ₹8,755 charges (73%), ₹3,168 net |

Why it fails:
- **Charges.** About ₹17.65 a trade at ₹3,500 a position, mostly fixed (DP charge, 0.1% STT each side). At the
  user's ₹70 risk that is about 0.18R a trade: more than the whole edge before charges.
- **One exit type pays.** Trades held the full 40 days averaged +2.77R (90 trades); trailing exits 0.00R (215),
  stops −1.08R (170), gaps through the stop −2.65R (21). The top 5% of trades made 48% of the profit.
- **Lone signals lose.** Days with a single signal averaged −0.615R; days with 4+ signals were flat (−0.02R).
- **Decaying.** Test windows: +0.07, +0.06, +0.21, −0.24R. In-sample 0.091R vs out-of-sample 0.018R is the overfit.

Do not re-tune it until it passes: that fits noise.

## 3. Paper trade: BEL, intraday opening-range breakout

`live-scan/scan-orb.log` (38 of 50 Nifty stocks broke out: a broad up day), then `paper-trade/p1..p9`, `f1..f7`,
`track.log`, ledger in `broker.json` and `portfolio.json`, order intents `2026-10-29-*.json`.

| | |
| --- | --- |
| entry | BUY 4 BEL at ₹367.30, 14:17 (breakout 14:05) |
| stop / target | ₹365.00 / ₹371.90, neither touched |
| exit | SELL 4 BEL at ₹369.35, 14:57 |
| result | +₹8.20 gross, ₹1.57 charges, **+₹6.63 net (+0.62R on ₹10.74 risk)** |

It proves the pipeline (live data → scan → entry → exit plan → sizing → risk check → typed yes → fills → tracking →
intents), not the strategy: one trade on a broad up day is not evidence. The exit was 2 minutes after the 14:55
flat-by; live, Shoonya would have squared it off near 15:00 with a fee. The 50% position cap, not the ₹70 risk
rule, set the size.

## 4. Open bugs (not yet fixed)

1. An exit after `flat_by` is neither blocked nor flagged.
2. `ticket.py track` can use a quote up to 15 minutes old instead of live bars.
3. Output labels Shoonya data as "Yahoo".
4. Shoonya's MIS square-off time is unverified (15:00 assumed).

Not financial advice.
