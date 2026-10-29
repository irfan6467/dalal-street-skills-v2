---
name: nse-validate
description: >-
  Forward test (out-of-sample, after the fact) for a strategy that
  nse-backtest rated CANDIDATE. Freezes the run's parameters, costs and fill
  rules, then replays them only on market data that did not exist when the
  backtest was made, day after day, and gives COLLECTING, FAILED (drawdown
  breaker or a losing finished sample) or PASSED (net profit, profit factor
  and a t-statistic over the threshold). With "require_validated" on,
  nse-risk blocks live entries from any strategy that has not PASSED. Use when
  the user says "forward test", "paper test this strategy", "validate the
  strategy", "is it still working", "has it passed", "can I go live with it",
  or after a CANDIDATE from /nse-backtest.
argument-hint: "start --run RUN_ID | update | status | stop --hash HASH"
---

# nse-validate

On Windows, use `py` in place of `python3`. Needs `/nse-backtest` installed
next to it: the forward test runs the backtest's own strategy code, so the
rules cannot drift between test and trading.

A backtest, even walk-forward, has been looked at. The forward test has not:
it counts only signals dated after the run was made. It is the last check
before real money, and it is still not proof.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/validate.py" start --run momentum-20261029-101500
python3 "${CLAUDE_SKILL_DIR}/validate.py" update        # after the day's data: backtest.py fetch-bhav / fetch-bars
python3 "${CLAUDE_SKILL_DIR}/validate.py" status
python3 "${CLAUDE_SKILL_DIR}/validate.py" stop --hash 1a2b3c4d5e6f --reason "changed my mind"
```

## The gate

| status | when |
| --- | --- |
| COLLECTING | fewer than 30 forward trades, or fewer than 60 trading days (momentum) / 20 sessions (ORB) |
| FAILED | early: drawdown beyond 1.5x the backtest's out-of-sample max drawdown (at least 6R). At the end: average R per entry day not above 0, profit factor under 1.1, or t under 1.0 |
| PASSED | finished sample: average R per entry day above 0 after charges, profit factor at least 1.1, t at least 1.0 |

Override any of these in `nse/backtest.json` under `"validate"`:
`min_trades`, `min_days` (`{"momentum": 60, "orb": 20}`), `dd_breaker_x`,
`dd_floor_r`, `pass_profit_factor`, `pass_min_t`. These are the user's choices:
Claude does not loosen them, least of all after a FAILED.

## How strong the gate is (measured, not assumed)

Simulated on 3,000 forward samples drawn from real out-of-sample trade
results of this pack's momentum strategy (R standard deviation 1.55):

| true edge per trade | pass rate, 30 trades | pass rate, 60 trades |
| --- | --- | --- |
| none (0R) | 13% | 12% |
| +0.15R | 29% | 36% |
| +0.30R | 52% | 70% |
| +0.50R | 83% | 96% |

So about one in eight strategies with no edge at all would still PASS on its
own. That is why a strategy only reaches this gate after being a backtest
CANDIDATE (noise rarely gets there), and why Claude must say this plainly to
the user when a test passes. A rule of "profitable with profit factor 1.1",
without the t threshold, passed 35 to 40% of no-edge strategies.

## Rules for Claude

- Report the status with its one-line reason, the forward numbers next to the
  backtest's, and the trade count.
- **PASSED is not "proven" and not a recommendation.** Say: consistent with the
  backtest over N trades; roughly one in eight no-edge strategies would pass;
  start with small size; keep the forward test running.
- After a FAILED, do not suggest re-tuning the same strategy until it passes:
  that turns the forward test back into a backtest.
- Never edit `nse/validate/*.json` by hand or on the user's behalf.

## Turning the gate on for live trading

The user adds `"require_validated": true` to `nse/config.json`. Then, in live
mode, nse-risk blocks any new entry whose order is not tagged with a strategy
that PASSED. Orders are tagged by sizing them with
`/nse-budget --strategy-run <RUN_ID>`; an untagged (discretionary) order is
blocked too while the setting is on. Exits are never blocked.

## Files

| file | what |
| --- | --- |
| `nse/validate/<params_hash>.json` | one forward test: frozen params, costs, start date, backtest reference, forward stats, every forward trade, history |
| `nse/backtest/<run>.json` | read: the run being validated |
| `nse/cache/bhav`, `nse/cache/bars` | read: the data, refreshed with nse-backtest's fetch commands |
