"""Diagnostics around nse-backtest for a Rs 25,000 account (3 Nov settings: Rs 8,333 a trade, 20 bp slippage a
fill, Shoonya charges, Rs 500 max loss a trade, 3 positions, 10% cash buffer, no leverage).

It imports nse-backtest/backtest.py, simulates every grid variant once, re-does the tool's walk-forward with the
tool's own functions (and checks it reproduces the saved run), then measures what the tool does not report:

  costs     where the money goes: before any cost, after slippage, after charges; cost per trade in R
  leak      training trades that exit inside the test window, and the walk-forward with them purged
  censor    signals still open at the end of the data (the tool drops them)
  stable    every variant's result over all test windows (is the choice of parameters doing anything?)
  account   what a Rs 25,000 account could take: 3 slots, whole shares, the Rs 500 rule, the cash buffer

  python3 harness.py momentum --from 2024-01-15 --to 2026-11-11 --run nse/backtest/<run>.json --out DIR
  python3 harness.py orb      --from 2026-08-18 --to 2026-11-11 --run nse/backtest/<run>.json --out DIR
"""
import argparse
import collections
import datetime
import importlib.util
import json
import os
import random
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
BUDGET, CAP_PCT, BUFFER_PCT, MAX_LOSS, SLOTS = 25000.0, 33.33, 10.0, 500.0, 3


def load_bt():
    spec = importlib.util.spec_from_file_location("bt_h", os.path.join(ROOT, "nse-backtest", "backtest.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def D(s):
    return datetime.date.fromisoformat(s)


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def f(x, nd=3):
    return "-" if x is None else ("%." + str(nd) + "f") % x


def day_mean(trades):
    by = collections.defaultdict(list)
    for t in trades:
        by[t["entry_d"]].append(t["r"])
    return mean([mean(v) for v in by.values()])


# ---------------------------------------------------------------- walk-forward, the tool's way and purged

def simulate_all(bt, strat, combos, start, end, costs, cfg):
    full = {}
    for n, p in enumerate(combos, 1):
        full[json.dumps(p, sort_keys=True)] = strat.trades(p, start, end, costs, cfg)
        print("  variant %d/%d: %d trades" % (n, len(combos), len(full[json.dumps(p, sort_keys=True)])), file=sys.stderr)
    return full


def walk(bt, full, combos, fs, min_trades, purge=False):
    """The tool's selection (bt.objective on the training window). purge=True drops training trades that had not
    exited by the end of the training window: on that day their result was not known."""
    oos, rows = [], []
    for n, (a, b, c, d) in enumerate(fs, 1):
        best, best_score, leak = None, -1e18, None
        for p in combos:
            tr = [t for t in full[json.dumps(p, sort_keys=True)] if a.isoformat() <= t["signal_d"] <= b.isoformat()]
            late = [t for t in tr if t["exit_d"] > b.isoformat()]
            if purge:
                tr = [t for t in tr if t["exit_d"] <= b.isoformat()]
            sc = bt.objective(tr, min_trades)
            if sc > best_score:
                best, best_score, leak = p, sc, (len(late), len(tr) + (len(late) if purge else 0))
        te = [dict(t, fold=n) for t in full[json.dumps(best, sort_keys=True)]
              if c.isoformat() <= t["signal_d"] <= d.isoformat()] if best_score > -1e9 else []
        oos += te
        rows.append({"fold": n, "test": [c.isoformat(), d.isoformat()], "params": best, "oos_trades": len(te),
                     "oos_day_avg_r": day_mean(te), "train_trades_exiting_in_test": leak[0], "train_trades": leak[1]})
    return oos, rows


# ---------------------------------------------------------------- where the money goes

def costs_table(bt, trades, cfg, product, slip_bps):
    s = slip_bps / 1e4
    rows = []
    for t in trades:
        q, e, x, risk = t["qty"], t["entry"], t["exit"], t["risk_inr"]
        e0, x0 = e / (1 + s), x / (1 - s)                      # the prices before slippage
        rows.append({"raw": (x0 - e0) * q, "slip": (e - e0) * q + (x0 - x) * q, "ch": t["charges_inr"], "risk": risk,
                     "net": t["net_inr"], "value": e * q})
    tot = lambda k: sum(r[k] for r in rows)
    out = {"trades": len(rows), "before_costs_inr": round(tot("raw"), 0), "slippage_inr": round(tot("slip"), 0),
           "charges_inr": round(tot("ch"), 0), "net_inr": round(tot("net"), 0),
           "avg_r_before_costs": mean([r["raw"] / r["risk"] for r in rows]),
           "avg_r_net": mean([r["net"] / r["risk"] for r in rows]),
           "avg_cost_in_r": mean([(r["slip"] + r["ch"]) / r["risk"] for r in rows]),
           "avg_cost_pct_of_position": mean([100 * (r["slip"] + r["ch"]) / r["value"] for r in rows]),
           "avg_risk_pct_of_position": mean([100 * r["risk"] / r["value"] for r in rows]),
           "avg_risk_inr": mean([r["risk"] for r in rows]),
           "share_risk_over_500": mean([1.0 if r["risk"] > MAX_LOSS else 0.0 for r in rows])}
    # the same trades at other slippage settings (information only; the run itself uses the configured one)
    sens = {}
    for bp in (0, 5, 10, 20):
        z = bp / 1e4
        rs = []
        for t in trades:
            q = t["qty"]
            e0, x0 = t["entry"] / (1 + s), t["exit"] / (1 - s)
            e1, x1 = e0 * (1 + z), x0 * (1 - z)
            ch = bt.trade_charges(e1 * q, x1 * q, product=product, exchange="NSE", cfg=cfg)["total"]
            rs.append(((x1 - e1) * q - ch) / ((e1 - t["stop"]) * q) if e1 > t["stop"] else 0.0)
        sens["%dbp" % bp] = round(mean(rs), 4)
    out["avg_r_net_by_slippage"] = sens
    return out


def breakdowns(trades):
    out = {}
    why = collections.defaultdict(list)
    for t in trades:
        why[t["why"]].append(t["r"])
    out["by_exit"] = {k: {"n": len(v), "share_pct": round(100.0 * len(v) / len(trades), 1), "avg_r": round(mean(v), 3)}
                      for k, v in sorted(why.items(), key=lambda kv: -len(kv[1]))}
    pb = collections.defaultdict(list)
    for t in trades:
        e = t["entry"]
        pb["under 100" if e < 100 else "100-500" if e < 500 else "500-2000" if e < 2000 else "2000+"].append(t["r"])
    out["by_price"] = {k: {"n": len(v), "avg_r": round(mean(v), 3)} for k, v in pb.items()}
    rb = collections.defaultdict(list)
    for t in trades:
        pct = 100.0 * (t["entry"] - t["stop"]) / t["entry"]
        rb["stop under 0.5%" if pct < 0.5 else "0.5-1%" if pct < 1 else "1-2%" if pct < 2 else "2-5%" if pct < 5
           else "5-10%" if pct < 10 else "over 10%"].append(t)
    out["by_stop_distance"] = {k: {"n": len(v), "avg_r_net": round(mean([t["r"] for t in v]), 3),
                                   "avg_r_before_charges": round(mean([t["r_gross"] for t in v]), 3)}
                               for k, v in rb.items()}
    rs = sorted((t["r"] for t in trades), reverse=True)
    wins = [r for r in rs if r > 0]
    out["concentration"] = {"best_10_share_of_all_winning_r": round(sum(rs[:10]) / sum(wins), 3) if wins else None,
                            "avg_r_without_best_10": round(mean(rs[10:]), 4) if len(rs) > 10 else None,
                            "win_rate": round(len(wins) / len(rs), 3), "avg_win_r": round(mean(wins), 3) if wins else None,
                            "avg_loss_r": round(mean([r for r in rs if r <= 0]), 3) if len(wins) < len(rs) else None}
    per_day = collections.Counter(t["entry_d"] for t in trades)
    c = sorted(per_day.values())
    out["signals_per_entry_day"] = {"entry_days": len(c), "median": statistics.median(c), "p90": c[int(0.9 * len(c))],
                                    "max": c[-1], "days_over_3": sum(1 for x in c if x > SLOTS)}
    hold = [(D(t["exit_d"]) - D(t["entry_d"])).days for t in trades]
    out["calendar_days_held"] = {"median": statistics.median(hold), "p90": sorted(hold)[int(0.9 * len(hold))]}
    return out


# ---------------------------------------------------------------- a Rs 25,000 account

def live_qty(bt, t, cash_left, product, cfg):
    """Whole shares as nse-budget would size them: position cap, cash after the buffer, and the Rs 500 loss rule
    with the round trip's charges at the stop inside it."""
    e, risk_ps = t["entry"], t["entry"] - t["stop"]
    q = int(min(BUDGET * CAP_PCT / 100.0, cash_left) // e)
    while q >= 1:
        ch = bt.trade_charges(e * q, t["stop"] * q, product=product, exchange="NSE", cfg=cfg)["total"]
        if q * risk_ps + ch <= MAX_LOSS:
            break
        q -= 1
    return max(q, 0)


def account(bt, trades, product, cfg, order_key=None, seed=0):
    """Signals in entry order; a slot frees when a trade exits (same-day re-use only for MIS, by clock time)."""
    rnd = random.Random(seed)
    by_day = collections.defaultdict(list)
    for t in trades:
        by_day[t["entry_d"]].append(t)
    open_pos, realised, curve, taken, skipped = [], 0.0, [], 0, collections.Counter()
    for d in sorted(by_day):
        todays = by_day[d]
        if order_key:
            todays = sorted(todays, key=order_key)
        else:
            rnd.shuffle(todays)
        if product == "MIS":
            open_pos, n_today = [], 0
            for t in todays:
                open_pos = [p for p in open_pos if p["t_out"] > t["t_in"]]
                if n_today >= SLOTS:
                    skipped["3 trades already today"] += 1
                    continue
                if len(open_pos) >= SLOTS:
                    skipped["3 positions open"] += 1
                    continue
                cash = BUDGET * (1 - BUFFER_PCT / 100.0) - sum(p["cost"] for p in open_pos)
                q = live_qty(bt, t, cash, product, cfg)
                if q < 1:
                    skipped["one share does not fit"] += 1
                    continue
                ch = bt.trade_charges(t["entry"] * q, t["exit"] * q, product=product, exchange="NSE", cfg=cfg)["total"]
                pnl = (t["exit"] - t["entry"]) * q - ch
                realised += pnl
                taken += 1
                n_today += 1
                open_pos.append({"t_out": t["t_out"], "cost": t["entry"] * q})
                curve.append((d, realised))
        else:
            for p in [p for p in open_pos if p["exit_d"] <= d]:
                realised += p["pnl"]
                curve.append((p["exit_d"], realised))
            open_pos = [p for p in open_pos if p["exit_d"] > d]
            for t in todays:
                if len(open_pos) >= SLOTS:
                    skipped["3 positions open"] += 1
                    continue
                cash = BUDGET * (1 - BUFFER_PCT / 100.0) - sum(p["cost"] for p in open_pos)
                q = live_qty(bt, t, cash, product, cfg)
                if q < 1:
                    skipped["one share does not fit"] += 1
                    continue
                ch = bt.trade_charges(t["entry"] * q, t["exit"] * q, product=product, exchange="NSE", cfg=cfg)["total"]
                open_pos.append({"exit_d": t["exit_d"], "pnl": (t["exit"] - t["entry"]) * q - ch, "cost": t["entry"] * q})
                taken += 1
    for p in sorted(open_pos, key=lambda p: p.get("exit_d", "")):
        if "pnl" in p:
            realised += p["pnl"]
            curve.append((p["exit_d"], realised))
    peak, dd = 0.0, 0.0
    for _, v in sorted(curve):
        peak = max(peak, v)
        dd = max(dd, peak - v)
    return {"net_inr": round(realised, 0), "taken": taken, "of": len(trades), "max_drawdown_inr": round(dd, 0),
            "skipped": dict(skipped)}


def account_mc(bt, trades, product, cfg, n=300):
    runs = [account(bt, trades, product, cfg, seed=s) for s in range(n)]
    nets = sorted(r["net_inr"] for r in runs)
    dds = sorted(r["max_drawdown_inr"] for r in runs)
    q = lambda xs, p: xs[min(len(xs) - 1, int(p * len(xs)))]
    return {"orderings": n, "net_inr": {"p5": q(nets, 0.05), "median": q(nets, 0.5), "p95": q(nets, 0.95)},
            "share_of_orderings_profitable": round(sum(1 for x in nets if x > 0) / float(n), 3),
            "max_drawdown_inr": {"median": q(dds, 0.5), "p95": q(dds, 0.95)},
            "trades_taken_median": q(sorted(r["taken"] for r in runs), 0.5), "signals": len(trades),
            "skipped_example": runs[0]["skipped"]}


# ---------------------------------------------------------------- commands

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy", choices=("momentum", "orb"))
    ap.add_argument("--from", dest="frm", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--run", help="the tool's saved run for the same period: checked against")
    ap.add_argument("--bhav-dir", default=os.path.join("nse", "cache", "bhav"))
    ap.add_argument("--bars-dir", default=os.path.join("nse", "cache", "bars"))
    ap.add_argument("--config", default=os.path.join("nse", "backtest.json"),
                    help="the tool's own file: costs here, broker and charges from nse/config.json")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    bt = load_bt()
    cfg = bt.load_config(a.config, {"slippage_bps": None, "notional_per_trade": None})
    costs = bt.costs_from(cfg)
    start, end = D(a.frm), D(a.to)
    if a.strategy == "momentum":
        daily, days = bt.load_daily(a.bhav_dir)
        strat, unit, train, test, product = bt.Momentum(daily, days), "months", 12, 3, "CNC"
        last_day = days[-1]
    else:
        strat, unit, train, test, product = bt.ORB(bt.load_intraday(a.bars_dir)), "weeks", 4, 2, "MIS"
        last_day = None
    combos = bt.grid(type(strat), "full")
    fs = bt.folds(start, end, train, test, unit)
    full = simulate_all(bt, strat, combos, start, end, costs, cfg)
    oos, rows = walk(bt, full, combos, fs, 30)
    st = bt.stats(oos)
    res = {"strategy": a.strategy, "period": [a.frm, a.to], "folds": rows,
           "tool": {"oos_trades": st.get("trades"), "day_avg_r": st.get("day_avg_r"), "avg_r": st.get("avg_r"),
                    "ci95": st.get("day_ci95"), "profit_factor": st.get("profit_factor")}}
    if a.run:
        saved = json.load(open(a.run))
        same = (saved["oos"]["trades"] == st["trades"] and abs(saved["oos"]["day_avg_r"] - st["day_avg_r"]) < 1e-6
                and [x["params"] for x in saved["folds"]] == [x["params"] for x in rows])
        res["reproduces_the_tool_run"] = same
        if not same:
            raise SystemExit("the harness does not reproduce %s" % a.run)
    # leak
    oos_p, rows_p = walk(bt, full, combos, fs, 30, purge=True)
    st_p = bt.stats(oos_p)
    res["leak"] = {"train_trades_exiting_inside_test": sum(r["train_trades_exiting_in_test"] for r in rows),
                   "train_trades": sum(r["train_trades"] for r in rows),
                   "purged": {"oos_trades": st_p.get("trades"), "day_avg_r": st_p.get("day_avg_r"),
                              "ci95": st_p.get("day_ci95"),
                              "folds_with_other_params": sum(1 for x, y in zip(rows, rows_p) if x["params"] != y["params"]),
                              "verdict": bt.verdict(st_p, [r["oos_day_avg_r"] for r in rows_p], 30)[0] if st_p else None}}
    # every variant over all test windows
    lo, hi = fs[0][2].isoformat(), fs[-1][3].isoformat()
    var = []
    for p in combos:
        tr = [t for t in full[json.dumps(p, sort_keys=True)] if lo <= t["signal_d"] <= hi]
        var.append({"params": p, "trades": len(tr), "day_avg_r": day_mean(tr), "avg_r": mean([t["r"] for t in tr])})
    var.sort(key=lambda v: -(v["day_avg_r"] if v["day_avg_r"] is not None else -9))
    res["variants_over_all_test_windows"] = {
        "positive": sum(1 for v in var if (v["day_avg_r"] or -1) > 0), "of": len(var),
        "best": var[0], "median_day_avg_r": statistics.median(v["day_avg_r"] for v in var if v["day_avg_r"] is not None),
        "worst": var[-1]}
    # censoring (momentum): signals whose trade was still open at the end of the data
    if a.strategy == "momentum":
        frozen = dict(strat.FIXED, **rows[-1]["params"])
        got = {(t["sym"], t["signal_d"]) for t in full[json.dumps(rows[-1]["params"], sort_keys=True)]}
        open_now = []
        for sym, bars in daily.items():
            ind = strat.ind.get(sym)
            if not ind:
                continue
            for i in range(max(1, len(bars) - 45), len(bars) - 1):
                b = bars[i]
                if fs[-1][2] <= b["d"] <= end and (sym, b["d"].isoformat()) not in got and strat.signal_ok(sym, i, frozen):
                    nb = bars[i + 1]
                    if bt.locked(nb) or nb["o"] > b["c"] * (1 + frozen["max_gap_pct"] / 100.0):
                        continue
                    entry = bt.fill_buy(nb["o"], costs["slippage_bps"])
                    stop = min(b["l"], entry - frozen["atr_stop"] * ind["atr"][i])
                    if 0 < stop < entry:
                        open_now.append((bars[-1]["c"] - entry) / (entry - stop))
                        break
        res["censored_open_trades"] = {"n": len(open_now), "avg_open_r_marked_to_last_close": mean(open_now),
                                       "last_fold_trades": rows[-1]["oos_trades"], "data_to": last_day.isoformat()}
    res["costs"] = costs_table(bt, oos, cfg, product, costs["slippage_bps"])
    res["breakdowns"] = breakdowns(oos)
    if a.strategy == "orb":
        for t in oos:                                    # clock times, which the tool does not keep
            p = dict(strat.FIXED, **rows[t["fold"] - 1]["params"])
            r = strat.day_trade(t["sym"], D(t["entry_d"]), strat.intraday[t["sym"]][D(t["entry_d"])], p,
                                costs["slippage_bps"])
            t["t_in"], t["t_out"] = r["t_in"], r["t_out"]
        res["account_25000"] = account(bt, oos, product, cfg, order_key=lambda t: (t["t_in"], t["sym"]))
        res["account_25000"]["rule"] = "first 3 triggers by clock time each day"
    else:
        res["account_25000"] = account_mc(bt, oos, product, cfg)
        res["account_25000"]["rule"] = "3 slots; which signal gets a free slot is random (300 orderings)"
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "diagnostics-%s.json" % a.strategy), "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, default=str)
    with open(os.path.join(a.out, "oos-trades-%s.json" % a.strategy), "w", encoding="utf-8") as fh:
        json.dump(oos, fh)
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
