#!/usr/bin/env python3
"""Daily after-close job for Claude's cloud: market report + momentum shadow journal.

Runs on NSE's public end-of-day files only (no Shoonya, no orders, no secrets):

  1. fetch the last ~400 calendar days of NSE bhavcopies (sec_bhavdata_full) into a scratch cache
  2. market report for the latest session: delivery-% spikes, bulk and block deals, momentum setups
     (labelled study only: the 29 Oct 2026 walk-forward found NO EDGE)
  3. shadow journal: every momentum signal is logged with the frozen parameters, and all signals since the
     journal started are re-scored on data that did not exist when the parameters were frozen
  4. write reports/<date>.md and update journal/ in the repo (the wrapper commits and pushes)

  python3 scripts/cloud/daily_eod.py                     # normal run from the repo root
  python3 scripts/cloud/daily_eod.py --bhav-dir DIR --no-fetch --no-deals   # offline (tests)

Standard library only. Needs network access to nsearchives.nseindia.com and archives.nseindia.com.
"""

import argparse
import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLOUD = os.path.join(ROOT, "scripts", "cloud")
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
JOURNAL_DIR = os.path.join(ROOT, "journal")
REPORTS_DIR = os.path.join(ROOT, "reports")
SHADOW = os.path.join(JOURNAL_DIR, "shadow-momentum.json")
DEALS_STATE = os.path.join(JOURNAL_DIR, "deals.json")
LOOKBACK_DAYS = 400            # 252-session breakout + 63-day relative strength + ATR warm-up


def load_backtest():
    path = os.path.join(ROOT, "nse-backtest", "backtest.py")
    spec = importlib.util.spec_from_file_location("nse_backtest_cloud", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False, default=str)
        fh.write("\n")
    os.replace(tmp, path)


def inr(x, dp=0):
    s = ("%." + str(dp) + "f") % abs(x)
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return ("-" if x < 0 else "") + "₹" + whole + ("." + frac if frac else "")


# ---------------------------------------------------------------- delivery spikes

def delivery_spikes(daily, day, n=15, mult=3.0, min_dp=50.0, min_turnover_lacs=50.0, min_price=20.0):
    """Stocks whose delivered quantity on `day` is at least `mult` x its own 20-session average, with a high
    delivery % and real turnover: buying that was taken home, not traded out the same day."""
    out = []
    for sym, bars in daily.items():
        if not bars or bars[-1]["d"] != day:
            continue
        b = bars[-1]
        if b["dp"] is None or b["series"] not in ("EQ", "BE") or b["rc"] < min_price:
            continue
        prev = [x for x in bars[-21:-1] if x["dp"] is not None]
        if len(prev) < 15:
            continue
        dq = b["v"] * b["dp"] / 100.0
        avg = sum(x["v"] * x["dp"] / 100.0 for x in prev) / len(prev)
        if avg <= 0 or b["dp"] < min_dp or b["to"] < min_turnover_lacs:
            continue
        ratio = dq / avg
        if ratio >= mult:
            chg = (b["rc"] / bars[-2]["rc"] - 1) * 100 if len(bars) > 1 and bars[-2]["rc"] else 0.0
            out.append({"symbol": sym, "close": round(b["rc"], 2), "change_pct": round(chg, 2),
                        "deliv_pct": round(b["dp"], 1), "deliv_x": round(ratio, 1),
                        "turnover_cr": round(b["to"] / 100.0, 2)})
    return sorted(out, key=lambda r: -r["deliv_x"])[:n]


# ---------------------------------------------------------------- bulk and block deals

def bulk_block(state_path, offline=None):
    """The day's biggest bulk and block deals through nse-bigmoney (history kept in journal/deals.json)."""
    cmd = [sys.executable, "-I", os.path.join(ROOT, "nse-bigmoney", "deals.py"), "top", "--state", state_path,
           "--days", "1", "--n", "12", "--json"]
    if offline:
        cmd += ["--offline", offline]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    if r.returncode != 0:
        return None, (r.stderr or r.stdout).strip().splitlines()[-1:] or ["deals.py failed"]
    try:
        return json.loads(r.stdout), []
    except ValueError:
        return None, ["deals.py answered something that is not JSON"]


# ---------------------------------------------------------------- shadow journal

def shadow_update(bt, strat, journal, day, costs, cfg):
    """Log today's signals; re-score everything since the journal began with the frozen parameters."""
    params = journal["params"]
    hits = strat.live_signals(params, day)
    if day.isoformat() not in {e["date"] for e in journal["signals"]}:
        journal["signals"].append({"date": day.isoformat(), "count": len(hits),
                                   "hits": [{k: h[k] for k in ("symbol", "signal_close", "stop_est", "max_entry",
                                                                "deliv_pct")} for h in hits]})
    start = datetime.date.fromisoformat(journal["started"])
    trades = [t for t in strat.trades(params, start, day, costs, cfg) if t["signal_d"] >= journal["started"]]
    st = bt.stats(trades)
    journal["score"] = {"as_of": day.isoformat(), "closed_trades": st.get("trades", 0), "stats": st,
                        "verdict": bt.verdict(st, [st.get("day_avg_r")] if st.get("trades") else [], 30)}
    journal["closed_trades"] = trades
    return hits, st


# ---------------------------------------------------------------- report

def render(day, spikes, deals, deal_notes, hits, journal, st, fetch_note, n_symbols):
    L = ["# NSE after-close report, %s" % day.strftime("%a %d %b %Y"), ""]
    L.append("Data: NSE bhavcopy for %s (%d stocks). %s Generated %s IST by the daily cloud job." % (
        day.isoformat(), n_symbols, fetch_note,
        datetime.datetime.now(IST).strftime("%Y-%m-%d %H:%M")))
    L.append("Not financial advice. Nothing here is a trade recommendation.")
    L += ["", "## 1. Delivery spikes", "",
          "Delivered quantity at least 3x the stock's own 20-session average, delivery 50%+, turnover ₹50 lakh+.", ""]
    if spikes:
        L.append("| stock | close | day % | delivery % | delivered vs avg | turnover |")
        L.append("| --- | ---: | ---: | ---: | ---: | ---: |")
        for s in spikes:
            L.append("| %s | %s | %+.2f%% | %.1f%% | %.1fx | ₹%.2f cr |" % (
                s["symbol"], inr(s["close"], 2), s["change_pct"], s["deliv_pct"], s["deliv_x"], s["turnover_cr"]))
    else:
        L.append("None today.")
    L += ["", "## 2. Bulk and block deals", ""]
    rows = (deals or {}).get("deals") or (deals or {}).get("top") or (deals if isinstance(deals, list) else [])
    if rows:
        L.append("| date | kind | stock | client | side | qty | price | value |")
        L.append("| --- | --- | --- | --- | --- | ---: | ---: | ---: |")
        for d in rows[:12]:
            val = d["value"] / 1e7 if isinstance(d.get("value"), (int, float)) else None
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                d.get("date", ""), d.get("kind", ""), d.get("symbol", ""), str(d.get("client", ""))[:40], d.get("side", ""),
                "{:,}".format(d["qty"]) if isinstance(d.get("qty"), int) else d.get("qty", ""), d.get("price", ""), ("₹%.2f cr" % val) if isinstance(val, (int, float)) else ""))
    else:
        L.append("No deals read%s." % ((": " + "; ".join(deal_notes)) if deal_notes else " for today"))
    L += ["", "## 3. Momentum setups (study only)", "",
          "Rules and parameters frozen from the 29 Oct 2026 walk-forward (params `%s`), which found **NO EDGE** "
          "after charges. Listed to build out-of-sample evidence, not to trade." % journal["params_hash"], ""]
    if hits:
        L.append("| stock | signal close | stop (est.) | skip above | delivery % |")
        L.append("| --- | ---: | ---: | ---: | ---: |")
        for h in hits[:25]:
            L.append("| %s | %s | %s | %s | %s |" % (h["symbol"], inr(h["signal_close"], 2), inr(h["stop_est"], 2),
                                                     inr(h["max_entry"], 2), h["deliv_pct"]))
    else:
        L.append("No setups today.")
    L += ["", "## 4. Shadow journal", ""]
    days_logged = len(journal["signals"])
    total = sum(e["count"] for e in journal["signals"])
    L.append("Started %s; %d session%s logged, %d signal%s." % (journal["started"], days_logged,
                                                                 "" if days_logged == 1 else "s", total,
                                                                 "" if total == 1 else "s"))
    if st.get("trades"):
        v, why = journal["score"]["verdict"]
        L.append("Closed shadow trades: %d, average %.3fR after charges (%.3fR before), win rate %.0f%%, "
                 "profit factor %s, max drawdown %.1fR." % (st["trades"], st["avg_r"], st["gross_r"],
                                                           st["win_rate"] * 100, st["profit_factor"], st["max_dd_r"]))
        L.append("Verdict so far: **%s**: %s" % (v, why))
    else:
        L.append("No shadow trades closed yet (exits take up to 40 sessions).")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bhav-dir", help="use this bhavcopy folder (default: a scratch cache)")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--no-deals", action="store_true")
    ap.add_argument("--deals-offline", help="read bulk.csv and block.csv from this folder")
    ap.add_argument("--date", help="report this session (YYYY-MM-DD) instead of the latest")
    a = ap.parse_args(argv)

    bt = load_backtest()
    settings = read_json(os.path.join(CLOUD, "settings.json"))
    if not settings:
        print("daily_eod: scripts/cloud/settings.json is missing", file=sys.stderr)
        return 1
    work = tempfile.mkdtemp(prefix="nse-eod-")
    os.makedirs(os.path.join(work, "nse"))
    write_json(os.path.join(work, "nse", "config.json"), settings["config"])
    os.chdir(work)                                   # the skills read nse/config.json from the working folder

    cache = a.bhav_dir or os.path.join(work, "bhav")
    today = datetime.datetime.now(IST).date()
    fetch_note = ""
    if not a.no_fetch:
        start = today - datetime.timedelta(days=LOOKBACK_DAYS)
        try:
            got, missing = bt.fetch_bhav(start, today, cache, pause=0.4)
        except Exception as e:                       # network refused: say how to fix, once
            got, missing = 0, 0
            fetch_note = "Download failed (%s)." % e
        if not got:
            print("daily_eod: no NSE bhavcopy could be downloaded. Allow nsearchives.nseindia.com and "
                  "archives.nseindia.com in claude.ai Settings > Capabilities (network access). %s" % fetch_note,
                  file=sys.stderr)
            return 2
        fetch_note = "%d files downloaded." % got
    daily, days = bt.load_daily(cache)
    day = datetime.date.fromisoformat(a.date) if a.date else days[-1]
    if day not in days:
        print("daily_eod: no bhavcopy for %s (holiday or not yet published)" % day, file=sys.stderr)
        return 3

    strat = bt.Momentum({s: [b for b in bars if b["d"] <= day] for s, bars in daily.items()},
                        [d for d in days if d <= day])
    spikes = delivery_spikes(strat.daily, day)

    deals, deal_notes = (None, ["skipped"]) if a.no_deals else bulk_block(DEALS_STATE, a.deals_offline)

    journal = read_json(SHADOW)
    if not journal:
        frozen = read_json(os.path.join(ROOT, settings["frozen_run"]))
        journal = {"strategy": "momentum", "started": day.isoformat(), "params": frozen["frozen_params"],
                   "params_hash": frozen["frozen_hash"], "frozen_from": settings["frozen_run"],
                   "costs": settings["costs"], "signals": []}
    hits, st = shadow_update(bt, strat, journal, day, journal["costs"], settings["config"])

    report = render(day, spikes, deals, deal_notes, hits, journal, st, fetch_note, len(strat.daily))
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, "%s.md" % day.isoformat())
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report)
    write_json(SHADOW, journal)
    if not a.bhav_dir:
        shutil.rmtree(work, ignore_errors=True)
    print("wrote %s (%d delivery spikes, %d momentum setups, %d shadow trades closed)" % (
        os.path.relpath(path, ROOT), len(spikes), len(hits), st.get("trades", 0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
