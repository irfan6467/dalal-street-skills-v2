#!/usr/bin/env python3
"""Blind one-day test: screen on history only, lock the picks, then reveal the test day.

  python3 blind/blind_test.py screen blind/2026-11-02     # history only -> picks.json (commit and push it)
  python3 blind/blind_test.py reveal blind/2026-11-02     # downloads the test day -> result.json, RESULT.md

Every setting comes from <folder>/rules.json (see PREREGISTRATION.md). The screen phase refuses to run if any
bhavcopy dated on or after the test day is in its data folder; the reveal phase refuses to run unless picks.json
exists and still matches its recorded hash. Standard library only; reuses nse-backtest.
"""

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_bt():
    spec = importlib.util.spec_from_file_location("nse_backtest_blind", os.path.join(ROOT, "nse-backtest",
                                                                                     "backtest.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def jdump(path, doc):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False, default=str)
        fh.write("\n")


def d(s):
    return datetime.date.fromisoformat(s)


# ---------------------------------------------------------------- screen

def universe(daily, day, u, bt):
    out = {}
    for sym, bars in daily.items():
        if not bars or bars[-1]["d"] != day or len(bars) < 21:
            continue
        b = bars[-1]
        if b["series"] not in u["series"] or b["rc"] < u["min_price"] or bt.locked(b):
            continue
        med = statistics.median(x["to"] for x in bars[-20:])
        if med < u["min_turnover_lacs_med20"]:
            continue
        out[sym] = med
    return out


def raw(b, x):
    """A back-adjusted price on bar b, in that bar's traded rupees."""
    return x * b["rc"] / b["c"] if b["c"] else x


def set_b_ok(bars, atr14, r):
    b, prev = bars[-1], bars[-2]
    n = r["lookback"]
    if len(bars) < n + 1 or atr14 is None:
        return False
    if b["c"] <= max(x["h"] for x in bars[-n - 1:-1]):
        return False
    if b["c"] < prev["c"] * (1 + r["min_up_pct"] / 100.0):
        return False
    vavg = sum(x["v"] for x in bars[-n - 1:-1]) / n
    if vavg <= 0 or b["v"] < r["vol_mult"] * vavg:
        return False
    rng = b["h"] - b["l"]
    if rng <= 0 or (b["c"] - b["l"]) / rng < r["min_close_in_range"]:
        return False
    if b["dp"] is not None and b["dp"] < r["min_deliv_pct"]:
        return False
    return True


def cmd_screen(folder, hist_dir, fetch):
    bt = load_bt()
    R = json.load(open(os.path.join(folder, "rules.json"), encoding="utf-8"))
    rules_sha = sha(os.path.join(folder, "rules.json"))
    test_day = d(R["test_day"])
    if os.path.exists(os.path.join(folder, "picks.json")):
        raise SystemExit("picks.json already exists: the screen ran once and is locked")
    if fetch:
        got, miss = bt.fetch_bhav(d(R["history_from"]), d(R["history_to"]), hist_dir, pause=0.4)
        print("history: %d files downloaded, %d weekdays with none" % (got, miss), file=sys.stderr)
    late = [p for dd, p in bt.bhav_files(hist_dir) if dd >= test_day]
    if late:
        raise SystemExit("the wall is broken: %d file(s) dated on/after %s are in %s" % (len(late), test_day, hist_dir))
    daily, days = bt.load_daily(hist_dir)
    signal_day = days[-1]
    uni = universe(daily, signal_day, R["universe"], bt)

    mom = bt.Momentum(daily, days)
    pa = R["set_a_momentum"]["params"]
    p_full = dict(bt.Momentum.FIXED, **pa)
    cand = {"A": [], "B": []}
    for sym, med in uni.items():
        bars = daily[sym]
        b = bars[-1]
        atrs = bt.atr(bars, 14)
        a14 = atrs[-1]
        if a14 is None:
            continue
        rec = {"symbol": sym, "signal_close": round(b["rc"], 2), "signal_low": round(raw(b, b["l"]), 2),
               "atr14": round(raw(b, a14), 4), "turnover_lacs_med20": round(med, 2), "deliv_pct": b["dp"]}
        ind = mom.ind.get(sym)
        if ind is not None and ind["idx"].get(signal_day) == len(bars) - 1 and mom.signal_ok(sym, len(bars) - 1, p_full):
            cand["A"].append(rec)
        if set_b_ok(bars, a14, R["set_b_breakout"]):
            cand["B"].append(rec)
    picks = {}
    for k in ("A", "B"):
        ranked = sorted(cand[k], key=lambda r: -r["turnover_lacs_med20"])
        picks[k] = {"qualified": len(ranked), "taken": ranked[:R["max_positions"]]}
    doc = {"test_day": R["test_day"], "signal_day": signal_day.isoformat(), "rules_sha256": rules_sha,
           "history_files": len(days), "universe_size": len(uni), "universe": sorted(uni),
           "picks": picks, "screened_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    path = os.path.join(folder, "picks.json")
    jdump(path, doc)
    print("signal day %s, universe %d, set A %d qualified, set B %d qualified -> %s sha256 %s" % (
        signal_day, len(uni), picks["A"]["qualified"], picks["B"]["qualified"], path, sha(path)))
    return 0


# ---------------------------------------------------------------- reveal

def size_and_play(bt, R, rec, bar, stop_rule):
    s = R["slippage_bps"] / 1e4
    o, h, lo, c = bar["o"], bar["h"], bar["l"], bar["c"]
    out = {"symbol": rec["symbol"], "open": o, "high": h, "low": lo, "close": c}
    if bar["h"] > 0 and (bar["h"] - bar["l"]) / bar["h"] <= 0.001:
        return dict(out, skipped="locked at one price")
    if o > rec["signal_close"] * (1 + R["max_gap_pct"] / 100.0):
        return dict(out, skipped="opened more than %.0f%% above the signal close" % R["max_gap_pct"])
    entry = o * (1 + s)
    stop = stop_rule(rec, entry)
    if o <= stop or entry <= stop:
        return dict(out, skipped="opened at or below the stop %.2f" % stop)
    stop_fill = min(stop, o) * (1 - s)
    qty = 0
    q = int(R["position_cap_inr"] // entry)
    while q >= 1:
        ch = bt.trade_charges(q * entry, q * stop_fill, "MIS", "NSE", R["config"])["total"]
        if q * (entry - stop_fill) + ch <= R["max_loss_per_trade_inr"]:
            qty = q
            break
        q -= 1
    if qty < 1:
        return dict(out, skipped="not even one share fits the ₹%d max loss" % R["max_loss_per_trade_inr"])
    target = entry + R["target_r"] * (entry - stop)
    if lo <= stop:
        px, why = stop_fill, "stop"
    elif h >= target:
        px, why = target * (1 - s), "target"
    else:
        px, why = c * (1 - s), "close"
    ch = bt.trade_charges(qty * entry, qty * px, "MIS", "NSE", R["config"])
    gross = qty * (px - entry)
    risk = qty * (entry - stop)
    return dict(out, qty=qty, entry=round(entry, 2), stop=round(stop, 2), target=round(target, 2),
                exit=round(px, 2), exit_reason=why, gross_inr=round(gross, 2), charges_inr=ch["total"],
                net_inr=round(gross - ch["total"], 2), risk_inr=round(risk, 2),
                r=round((gross - ch["total"]) / risk, 3) if risk > 0 else None)


def cmd_reveal(folder, day_dir, fetch, recorded_sha=None):
    bt = load_bt()
    R = json.load(open(os.path.join(folder, "rules.json"), encoding="utf-8"))
    ppath = os.path.join(folder, "picks.json")
    if not os.path.exists(ppath):
        raise SystemExit("no picks.json: run screen first, then commit it")
    P = json.load(open(ppath, encoding="utf-8"))
    if P["rules_sha256"] != sha(os.path.join(folder, "rules.json")):
        raise SystemExit("rules.json changed after the screen: the test is void")
    if recorded_sha and recorded_sha != sha(ppath):
        raise SystemExit("picks.json changed after it was committed: the test is void")
    test_day = d(R["test_day"])
    if fetch:
        bt.fetch_bhav(test_day, test_day, day_dir, pause=0)
    files = [p for dd, p in bt.bhav_files(day_dir) if dd == test_day]
    if not files:
        doc = {"test_day": R["test_day"], "void": "no NSE bhavcopy for the test day (holiday or not published)"}
        jdump(os.path.join(folder, "result.json"), doc)
        print(doc["void"])
        return 0
    file_day, rows = bt.parse_bhav_text(bt._read_text(files[0]))
    if file_day != test_day:
        doc = {"test_day": R["test_day"], "void": "the file named for the test day holds %s (NSE serves the last "
               "session's file on a holiday): no session on the test day" % file_day.isoformat()}
        jdump(os.path.join(folder, "result.json"), doc)
        print(doc["void"])
        return 0
    bars = {}
    for r in rows:
        if r["series"] in R["universe"]["series"] and (r["sym"] not in bars or r["series"] == "EQ"):
            bars[r["sym"]] = r
    A = R["set_a_momentum"]
    B = R["set_b_breakout"]
    rules = {"A": lambda rec, e: min(rec["signal_low"], e - A["params"]["atr_stop"] * rec["atr14"]),
             "B": lambda rec, e: max(rec["signal_low"], e - B["atr_mult"] * rec["atr14"])}
    res = {"test_day": R["test_day"], "signal_day": P["signal_day"], "picks_sha256": sha(ppath),
           "rules_sha256": P["rules_sha256"], "sets": {}}
    for k in ("A", "B"):
        trades = []
        for rec in P["picks"][k]["taken"]:
            b = bars.get(rec["symbol"])
            if b is None:
                trades.append({"symbol": rec["symbol"], "skipped": "no trade on the test day"})
                continue
            trades.append(size_and_play(bt, R, rec, b, rules[k]))
        done = [t for t in trades if "net_inr" in t]
        net = round(sum(t["net_inr"] for t in done), 2)
        res["sets"][k] = {"qualified": P["picks"][k]["qualified"], "trades": trades, "taken": len(done),
                          "net_inr": net, "return_pct": round(net / R["capital_inr"] * 100, 3),
                          "charges_inr": round(sum(t["charges_inr"] for t in done), 2)}
    s = R["slippage_bps"] / 1e4
    rets = []
    for sym in P["universe"]:
        b = bars.get(sym)
        if b and b["o"] > 0 and not (b["h"] > 0 and (b["h"] - b["l"]) / b["h"] <= 0.001):
            rets.append(b["c"] * (1 - s) / (b["o"] * (1 + s)) - 1)
    res["benchmark"] = {"stocks": len(rets), "avg_open_to_close_pct": round(statistics.mean(rets) * 100, 3) if rets
                        else None, "share_up_pct": round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1)
                        if rets else None}
    jdump(os.path.join(folder, "result.json"), res)
    with open(os.path.join(folder, "RESULT.md"), "w", encoding="utf-8") as fh:
        fh.write(render(res, R))
    print(render(res, R))
    return 0


def render(res, R):
    L = ["# Blind test result, %s" % res["test_day"], "",
         "Signal day %s. Rules sha256 `%s`, picks sha256 `%s` (both fixed before the test day was downloaded)."
         % (res["signal_day"], res["rules_sha256"][:16], res["picks_sha256"][:16]), ""]
    names = {"A": "Set A, momentum (frozen, NO EDGE in its walk-forward)", "B": "Set B, breakout with volume"}
    for k in ("A", "B"):
        S = res["sets"][k]
        L += ["## %s" % names[k], "",
              "%d qualified, %d traded. Net **%s₹%.2f** on ₹%s (%+.3f%%), charges ₹%.2f." % (
                  S["qualified"], S["taken"], "+" if S["net_inr"] >= 0 else "−", abs(S["net_inr"]),
                  "{:,}".format(R["capital_inr"]), S["return_pct"], S["charges_inr"]), ""]
        if S["trades"]:
            L.append("| stock | qty | entry | stop | target | exit | reason | net ₹ | R |")
            L.append("| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |")
            for t in S["trades"]:
                if "skipped" in t:
                    L.append("| %s | | | | | | skipped: %s | | |" % (t["symbol"], t["skipped"]))
                else:
                    L.append("| %s | %d | %.2f | %.2f | %.2f | %.2f | %s | %+.2f | %+.2f |" % (
                        t["symbol"], t["qty"], t["entry"], t["stop"], t["target"], t["exit"], t["exit_reason"],
                        t["net_inr"], t["r"]))
        else:
            L.append("No stock qualified: no trades.")
        L.append("")
    bm = res["benchmark"]
    L += ["## Benchmark", "",
          "Buying every stock in the screened universe at the open and selling at the close (20 bp each side): "
          "average %s%% over %d stocks; %s%% of them rose." % (bm["avg_open_to_close_pct"], bm["stocks"],
                                                                bm["share_up_pct"]), "",
          "One day is one sample. This shows what happened on %s, not whether either rule set has an edge." %
          res["test_day"], ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("phase", choices=("screen", "reveal"))
    ap.add_argument("folder")
    ap.add_argument("--data", help="data folder (default <folder>/.data/history or .data/day)")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--picks-sha", help="reveal: the picks.json sha256 recorded at commit time")
    a = ap.parse_args(argv)
    if a.phase == "screen":
        return cmd_screen(a.folder, a.data or os.path.join(a.folder, ".data", "history"), not a.no_fetch)
    return cmd_reveal(a.folder, a.data or os.path.join(a.folder, ".data", "day"), not a.no_fetch, a.picks_sha)


if __name__ == "__main__":
    sys.exit(main())
