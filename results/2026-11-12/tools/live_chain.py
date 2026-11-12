"""The rules the skills actually trade, on every Nifty 200 opening-range trigger, with the time machine's verdict.

nse-backtest's ORB grid is not what nse-entry / nse-exit do by default. This replays the defaults:
  entry   first completed 5-minute bar that CLOSES above the 15-minute range high, at that close,
          by 14:45 (14:30 for F&O stocks: continuous trading ends 15:15, minus the 45-minute buffer)
  stop    the range midpoint, rounded down to the 0.05 tick;  target 2R
  flat    15:10, or 14:55 for F&O stocks (Shoonya square-off assumed 15:00)
  costs   20 bp slippage on every fill, Shoonya MIS charges, sized like nse-budget (cap 33.33% of
          Rs 25,000, Rs 500 max loss with charges)
and, for each trigger, calls nse-timemachine in-process on that stock's bars cut at the trigger bar's close
(nothing later), exactly as the 15 Oct replay did. No parameter is fitted, so every day is out of sample.

  python3 live_chain.py --bars nse/cache/bars --fo fo_mktlots.csv --from 2026-09-14 --to 2026-11-11 --out FILE
"""
import argparse
import copy
import datetime
import importlib.util
import json
import math
import multiprocessing
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
EPOCH = datetime.datetime(1970, 1, 1)
IST = datetime.timedelta(hours=5, minutes=30)
STEP = 300
BUDGET, CAP_PCT, MAX_LOSS, SLIP = 25000.0, 33.33, 500.0, 20 / 1e4


def load(folder, name):
    spec = importlib.util.spec_from_file_location("lc_" + name, os.path.join(ROOT, folder, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def ist(ts):
    return EPOCH + datetime.timedelta(seconds=ts) + IST


def hm(t):
    return t.strftime("%H:%M")


def one_symbol(job):
    sym, path, fo, frm, to, cfg = job
    bt, tm = load("nse-backtest", "backtest"), load("nse-timemachine", "timemachine")
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    r = doc["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    ts = r["timestamp"]
    by_day = {}
    for i, t in enumerate(ts):
        if None in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]) or t % STEP:
            continue
        s = ist(t)
        if datetime.time(9, 15) <= s.time() < datetime.time(15, 30):
            by_day.setdefault(s.date(), []).append((i, s))
    cas = sym in fo
    cutoff = datetime.time(14, 30) if cas else datetime.time(14, 45)
    flat_by = datetime.time(14, 55) if cas else datetime.time(15, 10)
    out = []
    for day in sorted(by_day):
        if not (frm <= day <= to):
            continue
        bars = by_day[day]
        orb = [(i, s) for i, s in bars if s.time() < datetime.time(9, 30)]
        if not orb:
            continue
        hi = max(q["high"][i] for i, _ in orb)
        lo = min(q["low"][i] for i, _ in orb)
        trig = None
        for n, (i, s) in enumerate(bars):
            end = s + datetime.timedelta(seconds=STEP)
            if s.time() < datetime.time(9, 30):
                continue
            if end.time() > cutoff:
                break
            if q["close"][i] > hi:
                trig = (n, i, end)
                break
        if not trig:
            continue
        n, i, end = trig
        entry_plan = round(q["close"][i], 2)
        stop = math.floor(round((hi + lo) / 2 / 0.05, 6)) * 0.05
        stop = round(stop, 2)
        if stop >= entry_plan:
            continue
        target = round(entry_plan + 2 * (entry_plan - stop), 2)
        exit_px, why, t_out = None, None, None
        for j, s in bars[n + 1:]:
            if s.time() >= flat_by:
                exit_px, why, t_out = q["open"][j], "flat", s
                break
            if q["low"][j] <= stop:
                exit_px, why, t_out = min(stop, q["open"][j]), "stop", s
                break
            if q["high"][j] >= target:
                exit_px, why, t_out = max(target, q["open"][j]), "target", s
                break
        if exit_px is None:
            last = bars[-1]
            exit_px, why, t_out = q["close"][last[0]], "close", last[1]
        entry = entry_plan * (1 + SLIP)
        exit_f = exit_px * (1 - SLIP)
        qty = int(BUDGET * CAP_PCT / 100.0 // entry_plan)
        while qty >= 1:
            ch_stop = bt.trade_charges(entry_plan * qty, stop * qty, product="MIS", exchange="NSE", cfg=cfg)["total"]
            if qty * (entry_plan - stop) + ch_stop <= MAX_LOSS:
                break
            qty -= 1
        row = {"sym": sym, "day": day.isoformat(), "t_in": hm(end), "t_out": hm(t_out), "entry": entry_plan,
               "stop": stop, "target": target, "exit": round(exit_px, 2), "why": why, "cas": cas, "qty": qty,
               "risk_pct": round(100 * (entry_plan - stop) / entry_plan, 3)}
        risk_ps = entry_plan - stop
        row["r_before_costs"] = round((exit_px - entry_plan) / risk_ps, 4)
        if qty >= 1:
            ch = bt.trade_charges(entry * qty, exit_f * qty, product="MIS", exchange="NSE", cfg=cfg)["total"]
            row["net_inr"] = round((exit_f - entry) * qty - ch, 2)
            row["r"] = round(row["net_inr"] / (risk_ps * qty), 4)
            row["value"] = round(entry_plan * qty, 2)
        # the time machine, on bars that had closed by the trigger bar's close
        keep = [k for k, t in enumerate(ts) if ist(t).date() <= day and ist(t + STEP) <= end]
        cut = copy.deepcopy(doc)
        cr = cut["chart"]["result"][0]
        cq = cr["indicators"]["quote"][0]
        cr["timestamp"] = [ts[k] for k in keep]
        for key in list(cq):
            cq[key] = [cq[key][k] for k in keep]
        m = cr["meta"]
        m["regularMarketPrice"] = q["close"][i]
        m["regularMarketTime"] = ts[i] + STEP
        m.pop("currentTradingPeriod", None)
        m.pop("tradingPeriods", None)
        raw = {"symbol": sym, "exchange": "NSE", "yahoo": sym + ".NS", "product": "MIS", "side": "buy",
               "date": day.isoformat(), "setup": "orb", "entry": entry_plan, "entry_time": hm(end), "interval": "5m",
               "opening_range": {"high": hi, "low": lo, "minutes": 15}, "stop": stop, "stop_rule": "or_mid",
               "target": target, "flat_by": hm(datetime.datetime.combine(day, flat_by)), "cas_stock": cas}
        if qty >= 1:
            raw["quantity"] = qty
        try:
            now = tm.parse_now("%s %s" % (day.isoformat(), hm(end)))
            res = tm.time_machine(json.dumps(cut), tm.normalize_plan(raw, "flags", cfg), now, cfg=cfg)
            row["tm_avg_R"] = res["mc"]["avg_R"]
            row["tm_p_target"], row["tm_p_stop"] = res["mc"]["p_target"], res["mc"]["p_stop"]
        except Exception as e:                                   # too few sessions, a gap in the bars, ...
            row["tm_error"] = str(e)[:120]
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True)
    ap.add_argument("--fo", required=True)
    ap.add_argument("--from", dest="frm", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--config", default=os.path.join("nse", "config.json"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=4)
    a = ap.parse_args()
    with open(a.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    fo = set()
    with open(a.fo, encoding="utf-8") as fh:
        for ln in fh:
            cells = [c.strip() for c in ln.split(",")]
            if len(cells) > 1 and cells[1] and cells[1] != "SYMBOL":
                fo.add(cells[1].upper())
    jobs = []
    for f in sorted(os.listdir(a.bars)):
        if f.endswith(".json"):
            jobs.append((f.split(".NS_")[0], os.path.join(a.bars, f), fo, datetime.date.fromisoformat(a.frm),
                         datetime.date.fromisoformat(a.to), cfg))
    with multiprocessing.Pool(a.procs) as pool:
        rows = [r for part in pool.imap_unordered(one_symbol, jobs) for r in part]
    rows.sort(key=lambda r: (r["day"], r["t_in"], r["sym"]))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh)
    print("%d triggers on %d days, %d with a time machine verdict, %d too dear for one share" % (
        len(rows), len({r["day"] for r in rows}), sum(1 for r in rows if "tm_avg_R" in r),
        sum(1 for r in rows if r["qty"] < 1)))


if __name__ == "__main__":
    main()
