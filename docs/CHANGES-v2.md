# v2: what was wrong for real use, and what changed

v1 (dalal-street-skills 1.1.0) is a careful pack. Four gaps stopped it being something to trade on.

## 1. Delayed, unofficial data → Shoonya exchange data behind every skill

**Was:** every price came from Yahoo's free chart API: unofficial, can lag, can rate-limit, and an opening-range
breakout timed on late bars mostly measures the delay.

**Now:** every skill already fetched prices through one function (`yahoo_get`). That function now asks
`shoonya_feed.py` first. When the provider is Shoonya, chart and spark requests are answered from Shoonya's
TPSeries (1, 3, 5, 10, 15, 30, 60, 120, 240-minute bars), EODChartData (daily) and GetQuotes, returned in the exact
JSON shape each skill already parses, so no skill's logic changed. Fundamentals and FX stay on Yahoo.

- OAuth login runs in the operator's terminal; secrets come from environment variables; the day's session token is
  stored owner-only and never printed or logged.
- Shoonya documents its own response schemas as unverified and shows two auth styles. `/nse-shoonya selftest`
  checks both on a live account, saves the one that works, and validates OHLC consistency, ascending timestamps,
  per-bar (not cumulative) volume and intraday-vs-daily agreement.
- Failure is loud: with Shoonya selected, an error stops the skill instead of quietly using delayed data. A Yahoo
  fallback exists but is off by default.
- Rate limits are respected: at most about one market-data request per second per instrument.

## 2. No real evidence of an edge → a walk-forward backtester

**Was:** `/nse-timemachine` replays the rest of a day 10,000 times from the stock's own last 60 days. That shows the
spread of outcomes for one setup, not whether a rule makes money across many trades and market regimes.

**Now:** `/nse-backtest` runs walk-forward tests: choose parameters on a training window, judge them on the next unseen
window, roll forward, and report only the unseen results.

- Point-in-time daily data from NSE's own bhavcopies (delisted stocks included, so no survivorship bias); splits and
  bonuses back-adjusted from NSE's adjusted PREV_CLOSE; raw prices kept for price filters.
- Every trade pays STT, exchange, SEBI and stamp charges, GST, brokerage, DP charge and the user's slippage. Gaps
  through a stop fill at the open; a bar touching stop and target counts the stop; circuit-locked days cannot be traded.
- Significance is computed on day-level returns (same-day trades are correlated), with a bootstrap interval.
- Verdicts: NO EDGE, INSUFFICIENT DATA, INCONCLUSIVE, CANDIDATE. Never "proven".
- Every run is logged with how many parameter variants it tried, and reruns are called out (fitting noise by retrying).
- `signals` emits today's setups from a run's frozen parameters, so trading uses exactly the tested rules.

**Checked:** the engine finds an edge planted in synthetic data (CANDIDATE on every planted seed tested) and calls pure
noise NO EDGE or INCONCLUSIVE, never CANDIDATE (10 noise seeds, momentum and ORB).

## 3. Strategies → micro-cap momentum and intraday ORB, with Indian hygiene

- **momentum** (delivery): N-day-high breakout on a volume spike, closing strong, top relative strength, with filters
  for the ways small caps fool momentum in India: minimum price and median turnover, minimum delivery % on the
  signal day, no parabolic 5-day runs, no repeated circuit locks.
- **orb** (intraday MIS): 15/30-minute opening range, closed-bar breakout with volume, stop at range low or mid,
  1.5R/2R/no target, flat by 15:10.

## 4. No path from backtest to money → forward-test gate, consent-gated Shoonya orders

- `/nse-validate` freezes a CANDIDATE and replays it only on data newer than the run. COLLECTING → FAILED (drawdown
  breaker or a losing finished sample) or PASSED. The gate's error rates were measured by simulation and are printed
  in its skill doc: about 12% of no-edge strategies still pass; a +0.3R edge passes 52 to 70%.
- `"require_validated": true` makes `/nse-risk` block live entries from any strategy that has not PASSED (and untagged
  discretionary entries). Orders are tagged with `/nse-budget --strategy-run`.
- Shoonya joins the broker table. Its brokerage is deliberately not built in: charges refuse to compute until the user
  sets them.
- `ticket.py send` places Shoonya orders only after: no kill switch, live mode, orders explicitly enabled, an exchange
  Algo ID (SEBI), an nse-risk PASS for that exact order, the user's typed yes no older than 5 minutes, no duplicate, a
  daily cap, one order per second, and a consent code the **operator types in their own terminal** (default) or in
  their own chat message (opt-in). Shoonya's API takes no MKT or SL-M orders, so stops are SL-LMT with a buffer the
  user chooses, and the ticket warns that such a stop can be skipped in a fast fall.
- Every approved order (any broker, paper or live) is also written as a broker-neutral order intent
  (`docs/order-intent.schema.json`) for an external execution system to consume.

## What is still not solved

- **No result on real NSE data ships with this pack.** The development sandbox could not reach NSE or Yahoo. Run
  `fetch-bhav` and `run` on your own machine; expect most ideas to come back NO EDGE.
- **Shoonya's live responses are unverified here.** Field names follow Shoonya's docs and official SDK; the selftest
  is how a real account confirms them. Run it before switching the provider.
- Surveillance lists (ASM/GSM) are not available historically, so backtests cannot exclude them day by day.
- Intraday history is weeks, not years; ORB evidence accumulates slowly.
- A 30-trade forward test is a weak filter (see the measured rates). It narrows the odds; it does not remove them.
- Slippage is the user's assumption. In micro-caps, the day you most need to exit is when it is worst.
