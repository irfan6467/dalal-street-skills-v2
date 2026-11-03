#!/usr/bin/env python3
"""nse-backtest: walk-forward, out-of-sample backtests for NSE strategies, after Indian charges.

  backtest.py run --strategy momentum --bhav-dir DIR --from 2023-01-01 --to 2026-10-28
  backtest.py run --strategy orb --bars-dir DIR --from 2026-08-01 --to 2026-10-28
  backtest.py fetch-bhav --from 2024-01-01 --to 2026-10-28          # NSE bhavcopies into nse/cache/bhav
  backtest.py fetch-bars RELIANCE TCS ... --interval 5m --range 60d # intraday bars into nse/cache/bars
  backtest.py signals --run RUN_ID [--bhav-dir DIR | --bars-dir DIR] # today's signals with the run's frozen params
  backtest.py runs                                                   # every run, and how many variants were tried

What it guarantees, and what it does not:
- Parameters are chosen on a training window and judged ONLY on the next, unseen window (walk-forward).
  The verdict uses out-of-sample trades alone.
- Every trade pays the full statutory charges (STT, exchange, SEBI, stamp, GST), brokerage, DP charge and
  your slippage. Gaps through a stop fill at the open. A bar that touches stop and target counts the stop.
  A circuit-locked day cannot be traded in or out of.
- Daily data is NSE's own bhavcopy for each day, so delisted and suspended stocks are in the universe the
  day they traded (no survivorship bias). Splits and bonuses are adjusted from NSE's adjusted PREV_CLOSE.
- Trades opened the same day are correlated, so significance is computed on day-level returns.
- It does NOT prove an edge. Its best verdict is CANDIDATE: worth paper trading with /nse-validate.
  Surveillance lists (ASM/GSM) are not available historically, so the backtest cannot exclude them as they
  stood each day; the live scan does.

Standard library only. Generated from _shared/build/tpl/backtest.py by _shared/build/build.py.
"""

import argparse
import bisect
import collections
import csv
import datetime
import glob
import gzip
import hashlib
import io
import json
import math
import os
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

#@@INDIA_CORE@@


class BTError(IndiaError):
    pass


STATE_DIR = os.path.join("nse", "backtest")
BHAV_CACHE = os.path.join("nse", "cache", "bhav")
BARS_CACHE = os.path.join("nse", "cache", "bars")
CONFIG = os.path.join("nse", "backtest.json")
BHAV_URLS = ("https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_%s.csv",
             "https://archives.nseindia.com/products/content/sec_bhavdata_full_%s.csv")
CA_THRESHOLD = 0.02            # PREV_CLOSE differing from yesterday's close by more than this = corporate action
LOCK_TOL = 0.001               # high == low == close within 0.1%: the day traded at one price (circuit lock)


# ================================================================ config

REQUIRED_COSTS = {
    "slippage_bps": "slippage per side in basis points (1 bp = 0.01%), on top of charges",
    "notional_per_trade": "rupees per trade, used to turn fixed charges (DP, minimum brokerage) into R",
}


def load_config(path=CONFIG, overrides=None):
    cfg = {}
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except ValueError as e:
            raise BTError("%s is not valid JSON: %s" % (path, e))
    root_cfg = {}
    if os.path.exists(os.path.join("nse", "config.json")):
        try:
            with open(os.path.join("nse", "config.json"), encoding="utf-8") as fh:
                root_cfg = json.load(fh)
        except ValueError:
            root_cfg = {}
    merged = dict(root_cfg)
    merged.update(cfg)
    for k, v in (overrides or {}).items():
        if v is not None:
            merged[k] = v
    return merged


def costs_from(cfg):
    c = dict(cfg.get("costs") or {})
    for k in REQUIRED_COSTS:
        if cfg.get(k) is not None:
            c[k] = cfg[k]
    missing = [k for k in REQUIRED_COSTS if c.get(k) is None]
    if missing:
        raise BTError("set %s in nse/backtest.json \"costs\" (or pass --%s). These are your assumptions, so "
                      "the tool will not invent them:\n%s" % (
                          " and ".join(missing), missing[0].replace("_", "-"),
                          "\n".join("  %s: %s" % (k, REQUIRED_COSTS[k]) for k in missing)))
    c["slippage_bps"] = float(c["slippage_bps"])
    c["notional_per_trade"] = float(c["notional_per_trade"])
    if c["notional_per_trade"] <= 0 or c["slippage_bps"] < 0:
        raise BTError("notional_per_trade must be positive and slippage_bps not negative")
    return c


# ================================================================ daily data (bhavcopy)

def _f(x):
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return None


def parse_bhav_text(text):
    """sec_bhavdata_full CSV -> (date, [row dicts]). Keeps every series; the strategies choose."""
    rd = csv.reader(io.StringIO(text))
    head = None
    rows = []
    day = None
    for cells in rd:
        cells = [c.strip() for c in cells]
        if not cells or not any(cells):
            continue
        if head is None:
            head = [c.upper() for c in cells]
            need = {"SYMBOL", "SERIES", "DATE1", "PREV_CLOSE", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE",
                    "CLOSE_PRICE", "TTL_TRD_QNTY", "TURNOVER_LACS"}
            if not need <= set(head):
                raise BTError("not an NSE sec_bhavdata_full file (missing %s)" % sorted(need - set(head)))
            continue
        r = dict(zip(head, cells))
        if day is None:
            try:
                day = datetime.datetime.strptime(r["DATE1"], "%d-%b-%Y").date()
            except ValueError:
                raise BTError("bad DATE1 %r" % r.get("DATE1"))
        o, h, lo, c = (_f(r.get(k)) for k in ("OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE"))
        if None in (o, h, lo, c) or c <= 0:
            continue
        dp = _f(r.get("DELIV_PER"))
        rows.append({"sym": r["SYMBOL"].upper(), "series": r["SERIES"].upper(), "o": o, "h": h, "l": lo, "c": c,
                     "pc": _f(r.get("PREV_CLOSE")), "v": int(_f(r.get("TTL_TRD_QNTY")) or 0),
                     "to": _f(r.get("TURNOVER_LACS")) or 0.0, "dp": dp})
    if day is None:
        raise BTError("empty bhavcopy")
    return day, rows


def bhav_files(folder):
    out = []
    for p in glob.glob(os.path.join(folder, "sec_bhavdata_full_*.csv*")):
        m = re.search(r"sec_bhavdata_full_(\d{8})\.csv", os.path.basename(p))
        if m:
            out.append((datetime.datetime.strptime(m.group(1), "%d%m%Y").date(), p))
    return sorted(out)


def _read_text(p):
    if p.endswith(".gz"):
        with gzip.open(p, "rt", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


TRADABLE_SERIES = ("EQ", "BE", "BZ", "SM", "ST")


def load_daily(folder, start=None, end=None, series=TRADABLE_SERIES):
    """{SYMBOL: [bar ...]} ascending, with split/bonus-adjusted o/h/l/c and the raw close kept as rc.
    One series per symbol per day (EQ preferred)."""
    files = [(d, p) for d, p in bhav_files(folder) if (start is None or d >= start) and (end is None or d <= end)]
    if not files:
        raise BTError("no sec_bhavdata_full_DDMMYYYY.csv files in %s for that period (run fetch-bhav)" % folder)
    rank = {s: i for i, s in enumerate(series)}
    per = collections.defaultdict(list)
    days = []
    for d, p in files:
        day, rows = parse_bhav_text(_read_text(p))
        if day in days:
            continue       # NSE serves the last session's file under a holiday's name: one session counts once
        days.append(day)
        best = {}
        for r in rows:
            if r["series"] not in rank:
                continue
            k = r["sym"]
            if k not in best or rank[r["series"]] < rank[best[k]["series"]]:
                best[k] = r
        for k, r in best.items():
            per[k].append({"d": day, "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "rc": r["c"],
                           "pc": r["pc"], "v": r["v"], "to": r["to"], "dp": r["dp"], "series": r["series"]})
    for k in per:
        adjust_series(per[k])
    return dict(per), sorted(set(days))


def adjust_series(bars):
    """Back-adjust for splits and bonuses: on an ex-date NSE publishes PREV_CLOSE already adjusted, so
    PREV_CLOSE / yesterday's close is the factor. Applied to everything before that day."""
    factor = 1.0
    for i in range(len(bars) - 1, 0, -1):
        b, prev = bars[i], bars[i - 1]
        if b["pc"] and prev["rc"]:
            f = b["pc"] / prev["rc"]
            if abs(f - 1.0) > CA_THRESHOLD and 0.02 < f < 50:
                factor *= f
                b["ca"] = round(f, 6)
        if factor != 1.0:
            for k in ("o", "h", "l", "c"):
                prev[k] = prev[k] * factor          # rc (the raw close) stays as traded
            prev["v"] = int(round(prev["v"] / factor))
            prev["adj"] = factor
    return bars


def locked(b):
    """Traded at one price all day: a circuit lock (or a trade-for-trade stub). No fills either way."""
    return b["h"] > 0 and (b["h"] - b["l"]) / b["h"] <= LOCK_TOL


# ================================================================ intraday data

def load_chart_doc(path):
    txt = _read_text(path)
    doc = json.loads(txt)
    r = (doc.get("chart") or {}).get("result") or []
    if not r:
        raise BTError("%s has no chart result" % path)
    r = r[0]
    q = (r.get("indicators") or {}).get("quote", [{}])[0]
    out = []
    for i, t in enumerate(r.get("timestamp") or []):
        def at(k):
            a = q.get(k) or []
            return a[i] if i < len(a) else None
        c = at("close")
        if t is None or c is None:
            continue
        o, h, lo = at("open"), at("high"), at("low")
        out.append({"t": int(t), "o": float(o if o is not None else c), "h": float(h if h is not None else c),
                    "l": float(lo if lo is not None else c), "c": float(c), "v": int(at("volume") or 0)})
    return str((r.get("meta") or {}).get("symbol") or os.path.basename(path).split(".json")[0]).upper(), out


def load_intraday(folder, start=None, end=None):
    """{SYMBOL: {date: [5m bars]}} from a folder of chart JSON files (one per symbol, .json or .json.gz).
    Several files for one symbol (fetched on different days) are merged."""
    per = collections.defaultdict(dict)
    paths = glob.glob(os.path.join(folder, "*.json")) + glob.glob(os.path.join(folder, "*.json.gz"))
    if not paths:
        raise BTError("no chart .json files in %s (run fetch-bars)" % folder)
    for p in sorted(paths):
        sym, bars = load_chart_doc(p)
        sym = re.sub(r"(_\d{8})?$", "", sym)
        days = per[sym]
        for b in bars:
            d = datetime.datetime.fromtimestamp(b["t"], IST_TZ).date()
            if (start and d < start) or (end and d > end):
                continue
            days.setdefault(d, {})[b["t"]] = b
    out = {}
    for s, days in per.items():
        out[s] = {d: [bb[t] for t in sorted(bb)] for d, bb in days.items()}
    return out


IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def ist_time(t):
    return datetime.datetime.fromtimestamp(t, IST_TZ).time()


# ================================================================ indicators

def rolling_max(a, n):
    out = [None] * len(a)
    dq = collections.deque()
    for i, x in enumerate(a):
        while dq and a[dq[-1]] <= x:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - n:
            dq.popleft()
        if i >= n - 1:
            out[i] = a[dq[0]]
    return out


def rolling_min(a, n):
    neg = rolling_max([-x for x in a], n)
    return [None if x is None else -x for x in neg]


def rolling_mean(a, n):
    out = [None] * len(a)
    s = 0.0
    for i, x in enumerate(a):
        s += x
        if i >= n:
            s -= a[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def rolling_median(a, n):
    out = [None] * len(a)
    for i in range(n - 1, len(a)):
        out[i] = statistics.median(a[i - n + 1:i + 1])
    return out


def atr(bars, n=14):
    tr = []
    for i, b in enumerate(bars):
        if i == 0:
            tr.append(b["h"] - b["l"])
        else:
            pc = bars[i - 1]["c"]
            tr.append(max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)))
    out = [None] * len(bars)
    if len(bars) >= n:
        a = sum(tr[:n]) / n
        out[n - 1] = a
        for i in range(n, len(bars)):
            a = (a * (n - 1) + tr[i]) / n
            out[i] = a
    return out


# ================================================================ costs

def fill_buy(px, slip_bps):
    return px * (1 + slip_bps / 1e4)


def fill_sell(px, slip_bps):
    return px * (1 - slip_bps / 1e4)


def settle(trade, costs, product, cfg):
    """Fills -> net R and rupees after charges, at the configured notional per trade."""
    e, x, s = trade["entry"], trade["exit"], trade["stop"]
    risk_ps = e - s
    if risk_ps <= 0:
        return None
    qty = int(costs["notional_per_trade"] // e)
    if qty < 1:
        return None
    gross = (x - e) * qty
    ch = trade_charges(e * qty, x * qty, product=product, exchange="NSE", cfg=cfg)["total"]
    risk = risk_ps * qty
    trade.update({"qty": qty, "gross_inr": round(gross, 2), "charges_inr": ch, "net_inr": round(gross - ch, 2),
                  "risk_inr": round(risk, 2), "r_gross": round(gross / risk, 4), "r": round((gross - ch) / risk, 4)})
    return trade


# ================================================================ strategy: momentum (daily, delivery)

class Momentum:
    """Swing momentum for small and micro caps, built to avoid the traps that wreck it in India:
    illiquid names, pump-and-dump runs, circuit locks and low-delivery spikes.

    Signal at day t's close: close at an N-day high, volume >= vol_mult x its 20-day average, close in the
    top quarter of the day's range, 63-day return in the top rs_top_pct of the universe, and it passes the
    hygiene filters. Entry: next open, skipped if it gaps more than max_gap_pct above the signal close or the
    day is locked. Stop: the lower of the signal day's low and entry - atr_stop x ATR(14). Exit: close below
    the exit_days low (trailing), the stop intraday, or max_hold days at the close."""
    name = "momentum"
    product = "CNC"
    GRID = {"breakout_days": [63, 126, 252], "vol_mult": [1.5, 2.5], "atr_stop": [2.0, 3.0], "exit_days": [10, 20]}
    FIXED = {"min_price": 20.0, "min_turnover_lacs": 50.0, "max_gap_pct": 5.0, "max_run5_pct": 40.0,
             "max_locked_10": 2, "min_deliv_pct": 30.0, "rs_top_pct": 20.0, "max_hold": 40,
             "series": ["EQ", "BE"]}
    WARMUP = 260

    def __init__(self, daily, days):
        self.daily = daily
        self.days = days
        self.ind = {}
        for sym, bars in daily.items():
            if len(bars) < 70:
                continue
            c = [b["c"] for b in bars]
            h = [b["h"] for b in bars]
            lo = [b["l"] for b in bars]
            v = [float(b["v"]) for b in bars]
            self.ind[sym] = {
                "idx": {b["d"]: i for i, b in enumerate(bars)},
                "atr": atr(bars, 14), "vavg": rolling_mean(v, 20), "to_med": rolling_median([b["to"] for b in bars], 20),
                "hi": {n: rolling_max(h, n) for n in self.GRID["breakout_days"]},
                "lo": {n: rolling_min(lo, n) for n in self.GRID["exit_days"]},
                "ret63": [None if i < 63 else c[i] / c[i - 63] - 1 for i in range(len(c))],
                "ret5": [None if i < 5 else c[i] / c[i - 5] - 1 for i in range(len(c))],
                "lock10": rolling_mean([1.0 if locked(b) else 0.0 for b in bars], 10),
            }
        self._rs = {}

    def rs_cut(self, d, top_pct):
        """The 63-day return a stock needs on day d to be in the top top_pct of the universe that day."""
        key = (d, top_pct)
        if key not in self._rs:
            vals = []
            for sym, ind in self.ind.items():
                i = ind["idx"].get(d)
                if i is not None and ind["ret63"][i] is not None:
                    vals.append(ind["ret63"][i])
            vals.sort()
            self._rs[key] = vals[int(len(vals) * (1 - top_pct / 100.0))] if len(vals) >= 20 else float("inf")
        return self._rs[key]

    def signal_ok(self, sym, i, p):
        bars, ind = self.daily[sym], self.ind[sym]
        b = bars[i]
        n = p["breakout_days"]
        if i < n or ind["hi"][n][i - 1] is None or ind["atr"][i] is None or ind["vavg"][i - 1] is None:
            return False
        if b["series"] not in p["series"] or b["rc"] < p["min_price"] or locked(b):
            return False
        if (ind["to_med"][i] or 0) < p["min_turnover_lacs"]:
            return False
        if b["c"] <= ind["hi"][n][i - 1]:
            return False
        if ind["vavg"][i - 1] <= 0 or b["v"] < p["vol_mult"] * ind["vavg"][i - 1]:
            return False
        rng = b["h"] - b["l"]
        if rng <= 0 or (b["c"] - b["l"]) / rng < 0.75:
            return False
        if ind["ret5"][i] is not None and ind["ret5"][i] * 100 > p["max_run5_pct"]:
            return False
        if (ind["lock10"][i] or 0) * 10 > p["max_locked_10"]:
            return False
        if b["dp"] is not None and b["dp"] < p["min_deliv_pct"]:
            return False
        if ind["ret63"][i] is None or ind["ret63"][i] < self.rs_cut(b["d"], p["rs_top_pct"]):
            return False
        return True

    def trades(self, p, start, end, costs, cfg):
        p = dict(self.FIXED, **p)
        slip = costs["slippage_bps"]
        out = []
        for sym, bars in self.daily.items():
            if sym not in self.ind:
                continue
            ind = self.ind[sym]
            i = 1
            busy_until = -1
            while i < len(bars) - 1:
                d = bars[i]["d"]
                if d < start:
                    i += 1
                    continue
                if d > end:
                    break
                if i <= busy_until or not self.signal_ok(sym, i, p):
                    i += 1
                    continue
                j = i + 1
                nb = bars[j]
                if locked(nb) or nb["o"] > bars[i]["c"] * (1 + p["max_gap_pct"] / 100.0):
                    i += 1
                    continue
                entry = fill_buy(nb["o"], slip)
                stop = min(bars[i]["l"], entry - p["atr_stop"] * ind["atr"][i])
                if stop <= 0 or stop >= entry:
                    i += 1
                    continue
                exit_px, exit_d, why = None, None, None
                lows = ind["lo"][p["exit_days"]]
                k = j
                while k < len(bars):
                    b = bars[k]
                    if k == j:                          # entry day: in at the open, the stop can still hit
                        if b["l"] <= stop:
                            exit_px, exit_d, why = fill_sell(stop, slip), b["d"], "stop"
                            break
                    elif locked(b):
                        k += 1                          # one price all day (a circuit lock): no exit possible
                        continue
                    elif b["o"] <= stop:
                        exit_px, exit_d, why = fill_sell(b["o"], slip), b["d"], "gap"
                        break
                    elif b["l"] <= stop:
                        exit_px, exit_d, why = fill_sell(stop, slip), b["d"], "stop"
                        break
                    elif lows[k - 1] is not None and b["c"] < lows[k - 1]:
                        exit_px, exit_d, why = fill_sell(b["c"], slip), b["d"], "trail"
                        break
                    if k - j + 1 >= p["max_hold"] and not locked(b):
                        exit_px, exit_d, why = fill_sell(b["c"], slip), b["d"], "time"
                        break
                    k += 1
                if exit_px is None:
                    break                   # still open at the end of the data: not counted
                t = settle({"sym": sym, "signal_d": d.isoformat(), "entry_d": nb["d"].isoformat(),
                            "exit_d": exit_d.isoformat(), "entry": round(entry, 4), "stop": round(stop, 4),
                            "exit": round(exit_px, 4), "why": why}, costs, self.product, cfg)
                if t:
                    out.append(t)
                busy_until = k
                i = k + 1
        return out

    def live_signals(self, p, d):
        p = dict(self.FIXED, **p)
        hits = []
        for sym, bars in self.daily.items():
            ind = self.ind.get(sym)
            if not ind or d not in ind["idx"]:
                continue
            i = ind["idx"][d]
            if self.signal_ok(sym, i, p):
                b = bars[i]
                stop_est = min(b["l"], b["c"] - p["atr_stop"] * ind["atr"][i])
                hits.append({"symbol": sym, "yahoo": sym + ".NS", "signal_close": round(b["rc"], 2),
                             "level": round(b["rc"], 2), "stop_est": round(stop_est * b["rc"] / b["c"], 2),
                             "max_entry": round(b["rc"] * (1 + p["max_gap_pct"] / 100.0), 2),
                             "deliv_pct": b["dp"], "turnover_lacs_med20": round(ind["to_med"][i] or 0, 1)})
        return sorted(hits, key=lambda h: -h["turnover_lacs_med20"])


# ================================================================ strategy: opening-range breakout (intraday)

class ORB:
    """Intraday opening-range breakout, long only, MIS.

    The opening range is the first or_minutes from 09:15. Entry: the open of the bar after the first bar that
    CLOSES above the range high, if that close happens by 14:00 and the breakout bar's volume is at least
    vol_mult x the average opening-range bar. Skipped when the range is narrower than 0.3% or wider than 3% of
    price. Stop: the range low or its midpoint. Target: target_r x risk, or none. Flat at flat_by (default 15:10,
    and never after 15:10 for stocks in the 3:15 closing auction)."""
    name = "orb"
    product = "MIS"
    GRID = {"or_minutes": [15, 30], "stop_at": ["or_low", "or_mid"], "target_r": [1.5, 2.0, 0], "vol_mult": [1.0, 1.5]}
    FIXED = {"last_entry": "14:00", "flat_by": "15:10", "min_or_pct": 0.3, "max_or_pct": 3.0}

    def __init__(self, intraday):
        self.intraday = intraday

    def day_trade(self, sym, d, bars, p, slip):
        open_t = datetime.time(9, 15)
        or_end = (datetime.datetime.combine(d, open_t) + datetime.timedelta(minutes=p["or_minutes"])).time()
        last_entry = parse_hhmm(p["last_entry"], "last_entry")
        flat_by = parse_hhmm(p["flat_by"], "flat_by")
        orb = [b for b in bars if open_t <= ist_time(b["t"]) < or_end]
        rest = [b for b in bars if ist_time(b["t"]) >= or_end]
        if len(orb) < 2 or not rest:
            return None
        hi, lo = max(b["h"] for b in orb), min(b["l"] for b in orb)
        mid = (hi + lo) / 2
        width = (hi - lo) / hi * 100
        if not (p["min_or_pct"] <= width <= p["max_or_pct"]):
            return None
        vavg = sum(b["v"] for b in orb) / len(orb)
        for k, b in enumerate(rest[:-1]):
            if ist_time(b["t"]) > last_entry:
                return None
            if b["c"] > hi and (vavg <= 0 or b["v"] >= p["vol_mult"] * vavg):
                eb = rest[k + 1]
                entry = fill_buy(eb["o"], slip)
                stop = lo if p["stop_at"] == "or_low" else mid
                if entry <= stop:
                    return None
                target = entry + p["target_r"] * (entry - stop) if p["target_r"] else None
                for m in range(k + 1, len(rest)):
                    x = rest[m]
                    if ist_time(x["t"]) >= flat_by:
                        return {"exit": fill_sell(x["o"], slip), "why": "flat", "entry": entry, "stop": stop,
                                "t_in": eb["t"], "t_out": x["t"]}
                    if x["l"] <= stop:
                        px = x["o"] if x["o"] < stop else stop
                        return {"exit": fill_sell(px, slip), "why": "stop", "entry": entry, "stop": stop,
                                "t_in": eb["t"], "t_out": x["t"]}
                    if target and x["h"] >= target:
                        px = x["o"] if x["o"] > target else target
                        return {"exit": fill_sell(px, slip), "why": "target", "entry": entry, "stop": stop,
                                "t_in": eb["t"], "t_out": x["t"]}
                last = rest[-1]
                return {"exit": fill_sell(last["c"], slip), "why": "close", "entry": entry, "stop": stop,
                        "t_in": eb["t"], "t_out": last["t"]}
        return None

    def trades(self, p, start, end, costs, cfg):
        p = dict(self.FIXED, **p)
        out = []
        for sym, days in self.intraday.items():
            for d in sorted(days):
                if d < start or d > end:
                    continue
                r = self.day_trade(sym, d, days[d], p, costs["slippage_bps"])
                if not r:
                    continue
                t = settle({"sym": sym, "signal_d": d.isoformat(), "entry_d": d.isoformat(), "exit_d": d.isoformat(),
                            "entry": round(r["entry"], 4), "stop": round(r["stop"], 4), "exit": round(r["exit"], 4),
                            "why": r["why"]}, costs, self.product, cfg)
                if t:
                    out.append(t)
        return out


STRATEGIES = {"momentum": Momentum, "orb": ORB}


def grid(strategy_cls, size="full"):
    g = strategy_cls.GRID
    keys = sorted(g)
    combos = [{}]
    for k in keys:
        vals = g[k] if size == "full" else g[k][:1] + g[k][-1:] if len(g[k]) > 1 else g[k]
        vals = list(dict.fromkeys(vals))
        combos = [dict(c, **{k: v}) for c in combos for v in vals]
    return combos


def params_hash(strategy, params):
    blob = json.dumps({"strategy": strategy, "params": params}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


# ================================================================ statistics

def day_level(trades):
    """Average R per entry day: the honest sample when same-day trades are correlated."""
    by = collections.defaultdict(list)
    for t in trades:
        by[t["entry_d"]].append(t["r"])
    return [sum(v) / len(v) for _k, v in sorted(by.items())]


def bootstrap_ci(xs, n=2000, seed=7, alpha=0.05):
    if len(xs) < 2:
        return None, None
    rnd = random.Random(seed)
    m = len(xs)
    means = sorted(sum(xs[rnd.randrange(m)] for _ in range(m)) / m for _ in range(n))
    return means[int(n * alpha / 2)], means[int(n * (1 - alpha / 2)) - 1]


def max_drawdown(rs):
    peak = cur = dd = 0.0
    for r in rs:
        cur += r
        peak = max(peak, cur)
        dd = max(dd, peak - cur)
    return dd


def stats(trades):
    rs = [t["r"] for t in trades]
    n = len(rs)
    if n == 0:
        return {"trades": 0}
    wins = [r for r in rs if r > 0]
    losses = [-r for r in rs if r <= 0]
    dl = day_level(trades)
    mean = sum(rs) / n
    sd = statistics.stdev(rs) if n > 1 else 0.0
    dmean = sum(dl) / len(dl)
    dsd = statistics.stdev(dl) if len(dl) > 1 else 0.0
    lo, hi = bootstrap_ci(dl)
    streak = best = 0
    for r in rs:
        streak = streak + 1 if r <= 0 else 0
        best = max(best, streak)
    return {
        "trades": n, "entry_days": len(dl), "win_rate": round(len(wins) / n, 4),
        "avg_r": round(mean, 4), "sd_r": round(sd, 4), "avg_win_r": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "avg_loss_r": round(-sum(losses) / len(losses), 4) if losses else 0.0,
        "profit_factor": round(sum(wins) / sum(losses), 3) if losses and sum(losses) > 0 else None,
        "day_avg_r": round(dmean, 4), "day_t": round(dmean / (dsd / math.sqrt(len(dl))), 3) if dsd > 0 and len(dl) > 1 else None,
        "day_ci95": [None if lo is None else round(lo, 4), None if hi is None else round(hi, 4)],
        "max_dd_r": round(max_drawdown(rs), 3), "worst_losing_streak": best,
        "net_inr": round(sum(t["net_inr"] for t in trades), 2), "charges_inr": round(sum(t["charges_inr"] for t in trades), 2),
        "gross_r": round(sum(t["r_gross"] for t in trades) / n, 4),
    }


def verdict(st, fold_means, min_trades):
    """NO EDGE / INSUFFICIENT DATA / INCONCLUSIVE / CANDIDATE. Never 'proven'."""
    if st.get("trades", 0) < min_trades or st.get("entry_days", 0) < max(10, min_trades // 3):
        return "INSUFFICIENT DATA", "only %d out-of-sample trades on %d days (need %d+)" % (
            st.get("trades", 0), st.get("entry_days", 0), min_trades)
    if st["day_avg_r"] <= 0:
        return "NO EDGE", "out-of-sample average is %.3fR per entry day after charges" % st["day_avg_r"]
    lo = st["day_ci95"][0]
    pos_folds = sum(1 for m in fold_means if m is not None and m > 0)
    traded = sum(1 for m in fold_means if m is not None)
    if lo is None or lo <= 0:
        return "INCONCLUSIVE", "average %.3fR is positive but the 95%% interval (%.3f to %.3f) includes zero" % (
            st["day_avg_r"], lo or 0, st["day_ci95"][1] or 0)
    if (st.get("profit_factor") or 0) <= 1.0 or traded == 0 or pos_folds / traded < 0.6:
        return "INCONCLUSIVE", "positive overall but only %d of %d test windows were positive" % (pos_folds, traded)
    return "CANDIDATE", ("out-of-sample %.3fR per entry day, 95%% interval %.3f to %.3f, %d of %d windows positive. "
                         "Next: paper trade it with /nse-validate. This is not proof of an edge." % (
                             st["day_avg_r"], lo, st["day_ci95"][1], pos_folds, traded))


# ================================================================ walk-forward

def add_months(d, m):
    y, mo = divmod(d.month - 1 + m, 12)
    y += d.year
    mo += 1
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1]
    return datetime.date(y, mo, min(d.day, last))


def folds(start, end, train_m, test_m, unit="months"):
    out = []
    step = (lambda d, n: add_months(d, n)) if unit == "months" else (lambda d, n: d + datetime.timedelta(days=7 * n))
    tr_start = start
    while True:
        tr_end = step(tr_start, train_m) - datetime.timedelta(days=1)
        te_start = tr_end + datetime.timedelta(days=1)
        te_end = step(te_start, test_m) - datetime.timedelta(days=1)
        if te_start > end:
            break
        out.append((tr_start, tr_end, te_start, min(te_end, end)))
        tr_start = step(tr_start, test_m)
    return out


def objective(trades, min_trades):
    """In-sample score: day-level mean R, shrunk toward zero when there are few trades."""
    if len(trades) < max(5, min_trades // 3):
        return -1e9
    dl = day_level(trades)
    m = sum(dl) / len(dl)
    return m * len(dl) / (len(dl) + 20.0)


def walk_forward(strat, strategy_name, start, end, train_m, test_m, costs, cfg, min_trades, size="full",
                 unit="months", progress=None):
    combos = grid(type(strat), size)
    fs = folds(start, end, train_m, test_m, unit)
    if not fs:
        raise BTError("the period is too short for a %d+%d %s walk-forward" % (train_m, test_m, unit))
    # Each parameter set is simulated once over the whole period, then sliced by signal date. A decision on
    # day t only ever reads data up to day t, so slicing changes nothing except that a position still open
    # from the previous window can block a new one, as it would in real trading.
    full = {}

    def window(p, a, b):
        key = json.dumps(p, sort_keys=True)
        if key not in full:
            full[key] = strat.trades(p, start, end, costs, cfg)
        lo, hi = a.isoformat(), b.isoformat()
        return [dict(t) for t in full[key] if lo <= t["signal_d"] <= hi]

    oos, fold_rows = [], []
    for n, (a, b, c, d) in enumerate(fs, 1):
        best, best_score, best_is = None, -1e18, None
        for p in combos:
            tr = window(p, a, b)
            sc = objective(tr, min_trades)
            if sc > best_score:
                best, best_score, best_is = p, sc, tr
        te = window(best, c, d) if best_score > -1e9 else []
        for t in te:
            t["fold"] = n
        oos += te
        is_st = stats(best_is or [])
        te_st = stats(te)
        fold_rows.append({"fold": n, "train": [a.isoformat(), b.isoformat()], "test": [c.isoformat(), d.isoformat()],
                          "params": best if best_score > -1e9 else None, "is_trades": is_st.get("trades", 0),
                          "is_avg_r": is_st.get("avg_r"), "oos_trades": te_st.get("trades", 0),
                          "oos_avg_r": te_st.get("avg_r"), "oos_day_avg_r": te_st.get("day_avg_r")})
        if progress:
            progress("fold %d/%d  test %s..%s  params %s  IS %s trades %.3fR  OOS %s trades %s" % (
                n, len(fs), c, d, best, is_st.get("trades", 0), is_st.get("avg_r") or 0, te_st.get("trades", 0),
                "%.3fR" % te_st["avg_r"] if te_st.get("trades") else "-"))
    st = stats(oos)
    v, why = verdict(st, [f["oos_day_avg_r"] for f in fold_rows], min_trades)
    is_vals = [f["is_avg_r"] for f in fold_rows if f["is_avg_r"] is not None]
    final = fold_rows[-1]["params"]
    return {"strategy": strategy_name, "period": [start.isoformat(), end.isoformat()],
            "walk_forward": {"train": train_m, "test": test_m, "unit": unit, "folds": len(fs)},
            "variants_tried_per_fold": len(combos), "costs": costs, "oos": st, "verdict": v, "why": why,
            "is_avg_r_mean": round(sum(is_vals) / len(is_vals), 4) if is_vals else None,
            "folds": fold_rows, "frozen_params": final,
            "frozen_hash": params_hash(strategy_name, final) if final else None, "trades": oos}


# ================================================================ run log

def run_id(strategy):
    return "%s-%s" % (strategy, utc_now().strftime("%Y%m%d-%H%M%S"))


def save_run(res):
    os.makedirs(STATE_DIR, exist_ok=True)
    rid = run_id(res["strategy"])
    res["run_id"] = rid
    res["created_utc"] = utc_now().replace(microsecond=0).isoformat() + "Z"
    with open(os.path.join(STATE_DIR, rid + ".json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, default=str)
    line = {k: res[k] for k in ("run_id", "strategy", "verdict", "frozen_hash", "variants_tried_per_fold", "created_utc")}
    line["oos_trades"] = res["oos"].get("trades", 0)
    line["oos_day_avg_r"] = res["oos"].get("day_avg_r")
    with open(os.path.join(STATE_DIR, "runs.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    return rid


def read_runs():
    p = os.path.join(STATE_DIR, "runs.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def load_run(rid):
    p = os.path.join(STATE_DIR, rid + ".json")
    if not os.path.exists(p):
        raise BTError("no run %s in %s" % (rid, STATE_DIR))
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def trials_note(strategy):
    runs = [r for r in read_runs() if r["strategy"] == strategy]
    tried = sum(r.get("variants_tried_per_fold", 0) for r in runs)
    if len(runs) <= 1:
        return None
    return ("you have now run %s %d times (%d parameter variants in all). Every rerun after looking at results "
            "is another chance to fit noise: trust the first honest run, and let paper trading decide."
            % (strategy, len(runs), tried))


# ================================================================ report

def report(res):
    st = res["oos"]
    L = []
    L.append("%s  walk-forward %s..%s  (%d windows: %d %s train, %d test)" % (
        res["strategy"].upper(), res["period"][0], res["period"][1], res["walk_forward"]["folds"],
        res["walk_forward"]["train"], res["walk_forward"]["unit"], res["walk_forward"]["test"]))
    L.append("costs: slippage %.1f bp/side, %s per trade, charges for %s" % (
        res["costs"]["slippage_bps"], inr(res["costs"]["notional_per_trade"], 0), res.get("broker") or "your broker"))
    L.append("")
    L.append("VERDICT: %s" % res["verdict"])
    L.append("  %s" % res["why"])
    L.append("")
    if st.get("trades"):
        L.append("Out-of-sample only (%d trades, %d entry days):" % (st["trades"], st["entry_days"]))
        L.append("  avg %.3fR net  (%.3fR before charges)   win rate %.0f%%   profit factor %s" % (
            st["avg_r"], st["gross_r"], st["win_rate"] * 100, st["profit_factor"]))
        L.append("  per entry day %.3fR, 95%% interval %s to %s, t %s" % (
            st["day_avg_r"], st["day_ci95"][0], st["day_ci95"][1], st["day_t"]))
        L.append("  max drawdown %.1fR   worst losing streak %d   net %s after %s charges" % (
            st["max_dd_r"], st["worst_losing_streak"], inr(st["net_inr"], 0), inr(st["charges_inr"], 0)))
        if res.get("is_avg_r_mean") is not None:
            L.append("  in-sample (chosen params) averaged %.3fR: the gap to out-of-sample is the overfit" % res["is_avg_r_mean"])
    L.append("")
    L.append("%-4s %-23s %-34s %6s %8s" % ("win", "test window", "params", "trades", "avg R"))
    for f in res["folds"]:
        L.append("%-4d %-23s %-34s %6d %8s" % (f["fold"], "%s..%s" % (f["test"][0], f["test"][1]),
                                              params_short(f["params"]), f["oos_trades"],
                                              "%.3f" % f["oos_avg_r"] if f["oos_avg_r"] is not None else "-"))
    L.append("")
    L.append("Frozen params (last window) %s  hash %s" % (params_short(res["frozen_params"]), res["frozen_hash"]))
    note = trials_note(res["strategy"])
    if note:
        L.append("NOTE: " + note)
    return "\n".join(L)


def params_short(p):
    if not p:
        return "none (too few trades)"
    short = {"atr_stop": "atr", "breakout_days": "brk", "exit_days": "exit", "vol_mult": "vol", "or_minutes": "or",
             "stop_at": "stop", "target_r": "tgt"}
    return " ".join("%s=%s" % (short.get(k, k), v) for k, v in sorted(p.items()))


# ================================================================ fetching

def _http_bytes(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Referer": "https://www.nseindia.com/"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def bhav_date(data):
    """DATE1 of a sec_bhavdata_full file's first row (bytes or text), or None. On a holiday NSE answers with
    the last session's file under the holiday's name, so the name alone does not date a file."""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    head = None
    for cells in csv.reader(io.StringIO(text[:8000])):
        cells = [c.strip() for c in cells]
        if not any(cells):
            continue
        if head is None:
            head = [c.upper() for c in cells]
            continue
        try:
            return datetime.datetime.strptime(dict(zip(head, cells)).get("DATE1", ""), "%d-%b-%Y").date()
        except ValueError:
            return None
    return None


def fetch_bhav(start, end, folder=BHAV_CACHE, pause=0.6, fetch=None, progress=None):
    os.makedirs(folder, exist_ok=True)
    got = skipped = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            name = "sec_bhavdata_full_%s.csv" % d.strftime("%d%m%Y")
            path = os.path.join(folder, name)
            if not os.path.exists(path):
                data = None
                for u in BHAV_URLS:
                    try:
                        data = (fetch or _http_bytes)(u % d.strftime("%d%m%Y"))
                        break
                    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
                        continue
                if data and b"SYMBOL" in data[:200] and bhav_date(data) == d:
                    with open(path, "wb") as fh:
                        fh.write(data)
                    got += 1
                    if pause:
                        time.sleep(pause)
                else:
                    # holiday (NSE answers with the last session's file under the holiday's name), or not published
                    skipped += 1
        d += datetime.timedelta(days=1)
    if progress:
        progress("bhavcopies: %d downloaded, %d days with none (holidays or not reachable), in %s" % (got, skipped, folder))
    return got, skipped


def fetch_bars(symbols, interval, rng, folder=BARS_CACHE, progress=None):
    """Bars through the same data path as every skill (Shoonya when it is the provider)."""
    os.makedirs(folder, exist_ok=True)
    stamp = utc_now().strftime("%Y%m%d")
    done = []
    for s in symbols:
        ys = parse_symbol(s)["yahoo"]
        txt = yahoo_get(chart_path(ys, rng, interval))
        path = os.path.join(folder, "%s_%s.json" % (ys, stamp))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(txt)
        done.append(path)
        if progress:
            progress("saved %s" % path)
    return done


# ================================================================ CLI

def _date(s):
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise BTError("dates are YYYY-MM-DD, not %r" % s)


def _progress(msg):
    print(msg, file=sys.stderr, flush=True)


def build_strategy(name, a):
    if name == "momentum":
        folder = a.bhav_dir or BHAV_CACHE
        daily, days = load_daily(folder)
        return Momentum(daily, days)
    if name == "orb":
        folder = a.bars_dir or BARS_CACHE
        return ORB(load_intraday(folder))
    raise BTError("strategy is momentum or orb")


def cmd_run(a):
    cfg = load_config(a.config, {"slippage_bps": a.slippage_bps, "notional_per_trade": a.notional_per_trade})
    costs = costs_from(cfg)
    strat = build_strategy(a.strategy, a)
    unit = a.unit or ("weeks" if a.strategy == "orb" else "months")
    train = a.train or (4 if unit == "weeks" else 12)
    test = a.test or (2 if unit == "weeks" else 3)
    res = walk_forward(strat, a.strategy, _date(a.date_from), _date(a.date_to), train, test, costs, cfg,
                       a.min_trades, a.grid, unit, progress=None if a.json else _progress)
    res["broker"] = broker_profile(cfg)["name"]
    rid = save_run(res)
    if a.json:
        print(json.dumps({k: v for k, v in res.items() if k != "trades"}, indent=1, default=str))
    else:
        print(report(res))
        print("\nrun %s saved in %s (all out-of-sample trades included)" % (rid, STATE_DIR))
    return 0


def cmd_signals(a):
    res = load_run(a.run)
    if not res.get("frozen_params"):
        raise BTError("run %s has no frozen params (too few trades)" % a.run)
    if res["verdict"] != "CANDIDATE" and not a.force:
        raise BTError("run %s is %s, not CANDIDATE: no signals (pass --force to see them for study only)" % (
            a.run, res["verdict"]))
    strat = build_strategy(res["strategy"], a)
    out = {"run_id": a.run, "strategy": res["strategy"], "params_hash": res["frozen_hash"],
           "params": res["frozen_params"], "verdict": res["verdict"]}
    if res["strategy"] == "momentum":
        d = _date(a.date) if a.date else strat.days[-1]
        out["date"] = d.isoformat()
        out["hits"] = strat.live_signals(res["frozen_params"], d)
        out["note"] = "Signals at the close of %s; entry is the next open, skipped above max_entry." % d
    else:
        out["note"] = ("ORB triggers intraday: watch each symbol with /nse-entry --setup orb --or-minutes %s"
                       % res["frozen_params"]["or_minutes"])
        out["hits"] = []
    path = os.path.join(STATE_DIR, "signals-%s.json" % res["strategy"])
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps(out, indent=1) if a.json else "%d %s signals for %s (params %s) -> %s" % (
        len(out["hits"]), res["strategy"], out.get("date", "today"), res["frozen_hash"], path))
    if not a.json:
        for h in out["hits"][:25]:
            print("  %-12s close %9s  stop~ %9s  skip above %9s  deliv %s%%" % (
                h["symbol"], h["signal_close"], h["stop_est"], h["max_entry"], h["deliv_pct"]))
    return 0


def cmd_runs(a):
    rows = read_runs()
    if not rows:
        print("no runs yet")
        return 0
    for r in rows:
        print("%-28s %-9s %-17s %5s trades  %s R/day  params %s" % (
            r["run_id"], r["strategy"], r["verdict"], r.get("oos_trades"), r.get("oos_day_avg_r"), r.get("frozen_hash")))
    for s in sorted({r["strategy"] for r in rows}):
        n = trials_note(s)
        if n:
            print("NOTE: " + n)
    return 0


def cmd_fetch_bhav(a):
    fetch_bhav(_date(a.date_from), _date(a.date_to), a.out or BHAV_CACHE, a.pause, progress=_progress)
    return 0


def cmd_fetch_bars(a):
    fetch_bars(a.symbols, a.interval, a.range, a.out or BARS_CACHE, progress=_progress)
    return 0


def main(argv=None):
    utf8_stdout()
    ap = argparse.ArgumentParser(prog="backtest.py", description="Walk-forward backtests for NSE, after charges.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--strategy", required=True, choices=sorted(STRATEGIES))
    r.add_argument("--from", dest="date_from", required=True)
    r.add_argument("--to", dest="date_to", required=True)
    r.add_argument("--bhav-dir")
    r.add_argument("--bars-dir")
    r.add_argument("--train", type=int)
    r.add_argument("--test", type=int)
    r.add_argument("--unit", choices=("months", "weeks"))
    r.add_argument("--grid", choices=("full", "small"), default="full")
    r.add_argument("--min-trades", type=int, default=30)
    r.add_argument("--slippage-bps", type=float)
    r.add_argument("--notional-per-trade", type=float)
    r.add_argument("--config", default=CONFIG)
    r.add_argument("--json", action="store_true")
    s = sub.add_parser("signals")
    s.add_argument("--run", required=True)
    s.add_argument("--bhav-dir")
    s.add_argument("--bars-dir")
    s.add_argument("--date")
    s.add_argument("--force", action="store_true")
    s.add_argument("--json", action="store_true")
    sub.add_parser("runs")
    f = sub.add_parser("fetch-bhav")
    f.add_argument("--from", dest="date_from", required=True)
    f.add_argument("--to", dest="date_to", required=True)
    f.add_argument("--out")
    f.add_argument("--pause", type=float, default=0.6)
    b = sub.add_parser("fetch-bars")
    b.add_argument("symbols", nargs="+")
    b.add_argument("--interval", default="5m")
    b.add_argument("--range", default="60d")
    b.add_argument("--out")
    a = ap.parse_args(argv)
    try:
        return {"run": cmd_run, "signals": cmd_signals, "runs": cmd_runs, "fetch-bhav": cmd_fetch_bhav,
                "fetch-bars": cmd_fetch_bars}[a.cmd](a)
    except IndiaError as e:
        print("nse-backtest: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
