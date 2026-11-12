"""Read live_chain.py's triggers (+ the scanner watchlists) and answer, for a Rs 25,000 account:
does the skills' default ORB make money, does the time machine's gate help, does the scanner's watchlist help?

  python3 live_report.py --triggers live-chain.json --watchlists watchlists.json --out live-chain-report.json
"""
import argparse
import collections
import importlib.util
import json
import math
import os
import random

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
BUDGET, CAP_PCT, BUFFER_PCT, MAX_LOSS, SLOTS, SLIP = 25000.0, 33.33, 10.0, 500.0, 3, 20 / 1e4


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def summary(rows):
    rows = [r for r in rows if r["qty"] >= 1]
    if not rows:
        return {"n": 0}
    by_day = collections.defaultdict(list)
    for r in rows:
        by_day[r["day"]].append(r["r"])
    dm = [mean(v) for v in by_day.values()]
    sd = (sum((x - mean(dm)) ** 2 for x in dm) / (len(dm) - 1)) ** 0.5 if len(dm) > 1 else None
    return {"n": len(rows), "days": len(dm), "avg_r_net": round(mean(r["r"] for r in rows), 3),
            "avg_r_before_costs": round(mean(r["r_before_costs"] for r in rows), 3),
            "per_day_avg_r": round(mean(dm), 3),
            "per_day_t": round(mean(dm) / (sd / math.sqrt(len(dm))), 2) if sd else None,
            "win_rate": round(mean(1.0 if r["r"] > 0 else 0.0 for r in rows), 3),
            "avg_risk_pct": round(mean(r["risk_pct"] for r in rows), 3),
            "exits": dict(collections.Counter(r["why"] for r in rows))}


def corr(xs, ys):
    mx, my = mean(xs), mean(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) if sx and sy else None


def account(bt, cfg, rows, allow, rank=None, seed=None):
    """Each day: triggers in clock order (ties: rank, else random with seed, else alphabetical); `allow` is the
    gate. Up to 3 trades a day, 3 open at once, cash after the 10% buffer, sized like nse-budget."""
    rnd = random.Random(seed)
    by_day = collections.defaultdict(list)
    for r in rows:
        by_day[r["day"]].append(r)
    net, taken, curve, wins, rs = 0.0, 0, [], 0, []
    for d in sorted(by_day):
        todays = by_day[d]
        if seed is not None:
            rnd.shuffle(todays)
        todays = sorted(todays, key=lambda r: (r["t_in"], (rank or {}).get((d, r["sym"]), 99),
                                               0 if seed is not None else r["sym"]))
        open_pos, n = [], 0
        for r in todays:
            if n >= SLOTS:
                break
            if not allow(r):
                continue
            open_pos = [p for p in open_pos if p["t_out"] > r["t_in"]]
            if len(open_pos) >= SLOTS:
                continue
            cash = BUDGET * (1 - BUFFER_PCT / 100.0) - sum(p["cost"] for p in open_pos)
            q = int(min(BUDGET * CAP_PCT / 100.0, cash) // r["entry"])
            while q >= 1:
                ch = bt.trade_charges(r["entry"] * q, r["stop"] * q, product="MIS", exchange="NSE", cfg=cfg)["total"]
                if q * (r["entry"] - r["stop"]) + ch <= MAX_LOSS:
                    break
                q -= 1
            if q < 1:
                continue
            e, x = r["entry"] * (1 + SLIP), r["exit"] * (1 - SLIP)
            ch = bt.trade_charges(e * q, x * q, product="MIS", exchange="NSE", cfg=cfg)["total"]
            pnl = (x - e) * q - ch
            net += pnl
            taken += 1
            n += 1
            wins += pnl > 0
            rs.append(pnl / ((r["entry"] - r["stop"]) * q))
            open_pos.append({"t_out": r["t_out"], "cost": r["entry"] * q})
        curve.append(net)
    peak, dd = 0.0, 0.0
    for v in curve:
        peak = max(peak, v)
        dd = max(dd, peak - v)
    return {"trades": taken, "days": len(by_day), "net_inr": round(net, 0), "avg_r": round(mean(rs), 3) if rs else None,
            "win_rate": round(wins / float(taken), 3) if taken else None, "max_drawdown_inr": round(dd, 0),
            "pct_of_capital": round(100 * net / BUDGET, 1)}


def account_mc(bt, cfg, rows, allow, n=200):
    runs = [account(bt, cfg, rows, allow, seed=s) for s in range(n)]
    nets = sorted(r["net_inr"] for r in runs)
    q = lambda p: nets[min(n - 1, int(p * n))]
    return {"orderings_of_same-minute_triggers": n, "net_inr": {"p5": q(0.05), "median": q(0.5), "p95": q(0.95)},
            "profitable_share": round(sum(1 for x in nets if x > 0) / float(n), 3),
            "trades_median": sorted(r["trades"] for r in runs)[n // 2]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--triggers", required=True)
    ap.add_argument("--watchlists", required=True)
    ap.add_argument("--config", default=os.path.join("nse", "config.json"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    spec = importlib.util.spec_from_file_location("bt_lr", os.path.join(ROOT, "nse-backtest", "backtest.py"))
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)
    cfg = json.load(open(a.config))
    rows = json.load(open(a.triggers))
    wl = json.load(open(a.watchlists))
    rank = {(d, s): i for d, v in wl.items() for i, s in enumerate(v["top10"])}
    for r in rows:
        r["in_wl"] = (r["day"], r["sym"]) in rank
    ok = [r for r in rows if r["qty"] >= 1 and "tm_avg_R" in r]
    res = {"triggers": len(rows), "days": len({r["day"] for r in rows}),
           "too_dear_for_one_share": sum(1 for r in rows if r["qty"] < 1),
           "all": summary(rows),
           "time_machine": {"passed (avg R > 0)": summary([r for r in ok if r["tm_avg_R"] > 0]),
                            "rejected": summary([r for r in ok if r["tm_avg_R"] <= 0]),
                            "pass_rate": round(mean(1.0 if r["tm_avg_R"] > 0 else 0.0 for r in ok), 3),
                            "correlation_forecast_vs_result": round(corr([r["tm_avg_R"] for r in ok], [r["r"] for r in ok]), 3),
                            "avg_forecast_r": round(mean(r["tm_avg_R"] for r in ok), 3),
                            "avg_result_r": round(mean(r["r"] for r in ok), 3)},
           "scanner_watchlist": {"in top 10": summary([r for r in rows if r["in_wl"]]),
                                 "not in it": summary([r for r in rows if not r["in_wl"]])}}
    # the forecast in five equal groups, lowest to highest
    srt = sorted(ok, key=lambda r: r["tm_avg_R"])
    k = len(srt) // 5
    res["time_machine"]["by_forecast_fifth"] = [
        {"forecast_avg_r": round(mean(r["tm_avg_R"] for r in g), 3), "result_avg_r": round(mean(r["r"] for r in g), 3),
         "result_before_costs": round(mean(r["r_before_costs"] for r in g), 3), "n": len(g)}
        for g in (srt[i * k:(i + 1) * k if i < 4 else len(srt)] for i in range(5))]
    by_time = collections.defaultdict(list)
    for r in ok:
        hh = int(r["t_in"][:2])
        by_time["09:35-09:55" if r["t_in"] < "10:00" else "10:00-10:55" if hh < 11 else "11:00-12:55" if hh < 13
                else "13:00 on"].append(r)
    res["by_trigger_time"] = {k2: summary(v) for k2, v in sorted(by_time.items())}
    rb = collections.defaultdict(list)
    for r in ok:
        p = r["risk_pct"]
        rb["under 0.3%" if p < 0.3 else "0.3-0.5%" if p < 0.5 else "0.5-1%" if p < 1 else "over 1%"].append(r)
    res["by_stop_distance"] = {k2: dict(summary(v), cost_in_r=round(mean(x["r_before_costs"] - x["r"] for x in v), 3))
                               for k2, v in rb.items()}
    res["account_25000"] = {
        "A all Nifty 200, no gate": dict(account(bt, cfg, rows, lambda r: True), ties=account_mc(bt, cfg, rows, lambda r: True)),
        "B all Nifty 200, time machine gate": dict(account(bt, cfg, rows, lambda r: r.get("tm_avg_R", -1) > 0),
                                                   ties=account_mc(bt, cfg, rows, lambda r: r.get("tm_avg_R", -1) > 0)),
        "C scanner top 10, no gate": account(bt, cfg, [r for r in rows if r["in_wl"]], lambda r: True, rank),
        "D scanner top 10 + time machine (the 15 Oct chain)": account(
            bt, cfg, [r for r in rows if r["in_wl"]], lambda r: r.get("tm_avg_R", -1) > 0, rank)}
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
