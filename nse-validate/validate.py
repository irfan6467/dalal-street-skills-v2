#!/usr/bin/env python3
"""nse-validate: forward-test a CANDIDATE strategy on data that did not exist when it was backtested.

  validate.py start --run momentum-20261029-101500   # freeze the run's params; forward test starts tomorrow
  validate.py update                                  # replay every active test on the newest data
  validate.py status                                  # one line per test
  validate.py stop --hash 1a2b3c4d5e6f                # abandon a test

The forward test uses nse-backtest's own strategy code, frozen parameters, costs and fill rules, so the
numbers are comparable with the backtest. Only signals dated after the run was made count. Status:

  COLLECTING  not enough forward trades or days yet
  FAILED      the drawdown breaker tripped, or the finished sample lost money / profit factor too low
  PASSED      the finished sample made money net of charges with an acceptable drawdown

PASSED means "consistent with the backtest so far", not "proven". With "require_validated": true in
nse/config.json, nse-risk blocks live entries from any strategy that is not PASSED.
Standard library only; needs the nse-backtest skill installed next to this one.
"""

import argparse
import datetime
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join("nse", "validate")
DEFAULTS = {"min_trades": 30, "min_days": {"momentum": 60, "orb": 20}, "dd_breaker_x": 1.5, "dd_floor_r": 6.0,
            "pass_profit_factor": 1.1, "pass_min_t": 1.0}


class ValError(Exception):
    pass


def load_backtest():
    for cand in (os.path.join(os.path.dirname(HERE), "nse-backtest", "backtest.py"),
                 os.path.join(HERE, "backtest.py")):
        if os.path.exists(cand):
            spec = importlib.util.spec_from_file_location("nse_backtest_for_validate", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise ValError("nse-backtest is not installed next to nse-validate: install both skills")


def rules(cfg):
    r = dict(DEFAULTS)
    r.update((cfg.get("validate") or {}))
    return r


def read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, default=str)
    os.replace(tmp, path)


def tests():
    if not os.path.isdir(STATE):
        return []
    return sorted(os.path.join(STATE, f) for f in os.listdir(STATE) if f.endswith(".json"))


def ist_today():
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)).date()


def cmd_start(a, bt=None):
    bt = bt or load_backtest()
    run = bt.load_run(a.run)
    if run.get("verdict") != "CANDIDATE":
        raise ValError("run %s is %s: only a CANDIDATE is worth forward testing" % (a.run, run.get("verdict")))
    h = run["frozen_hash"]
    path = os.path.join(STATE, h + ".json")
    if os.path.exists(path) and not a.restart:
        raise ValError("a forward test for params %s already exists (status %s); --restart replaces it"
                       % (h, read(path).get("status")))
    period_end = datetime.date.fromisoformat(run["period"][1])
    made = datetime.date.fromisoformat(run["created_utc"][:10])
    start = max(period_end, made, ist_today() if not a.start else datetime.date.fromisoformat(a.start)) \
        + datetime.timedelta(days=1)
    tried = [r for r in bt.read_runs() if r["strategy"] == run["strategy"]]
    doc = {"strategy": run["strategy"], "run_id": a.run, "params_hash": h, "params": run["frozen_params"],
           "costs": run["costs"], "forward_start": start.isoformat(), "status": "COLLECTING",
           "started_utc": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
           "backtest": {k: run["oos"].get(k) for k in ("trades", "day_avg_r", "day_ci95", "avg_r", "max_dd_r",
                                                       "profit_factor", "win_rate")},
           "backtest_runs_of_this_strategy": len(tried),
           "backtest_variants_tried": sum(r.get("variants_tried_per_fold", 0) for r in tried),
           "history": []}
    write(path, doc)
    print("Forward test started for %s, params %s. Only signals from %s on count." % (run["strategy"], h, start))
    print("Run `validate.py update` after each trading day's data is in (fetch-bhav / fetch-bars).")
    if len(tried) > 3:
        print("Note: %d backtest runs of %s so far. The more variants tried, the more a CANDIDATE can be luck: "
              "this forward test is the honest check." % (len(tried), run["strategy"]))
    return 0


def evaluate(doc, forward, data_end, r):
    bt_ref = doc["backtest"]
    st = forward
    n = st.get("trades", 0)
    start = datetime.date.fromisoformat(doc["forward_start"])
    elapsed = max(0, int((data_end - start).days * 5 / 7)) if data_end else 0
    min_days = r["min_days"].get(doc["strategy"], 40) if isinstance(r["min_days"], dict) else int(r["min_days"])
    breaker = max(r["dd_floor_r"], r["dd_breaker_x"] * (bt_ref.get("max_dd_r") or 0))
    if n and (st.get("max_dd_r") or 0) > breaker:
        return "FAILED", "drawdown %.1fR passed the breaker %.1fR (%.1fx the backtest's %.1fR)" % (
            st["max_dd_r"], breaker, r["dd_breaker_x"], bt_ref.get("max_dd_r") or 0)
    if n < r["min_trades"] or elapsed < min_days:
        return "COLLECTING", "%d of %d trades, about %d of %d trading days" % (n, r["min_trades"], elapsed, min_days)
    pf = st.get("profit_factor") or 0
    t = st.get("day_t") or 0
    if st["day_avg_r"] > 0 and pf >= r["pass_profit_factor"] and t >= r["pass_min_t"]:
        return "PASSED", ("forward %.3fR per entry day (backtest %.3fR), profit factor %.2f over %d trades: "
                          "consistent with the backtest so far, not proof" % (
                              st["day_avg_r"], bt_ref.get("day_avg_r") or 0, pf, n))
    return "FAILED", "forward %.3fR per entry day, profit factor %.2f, t %.2f over %d trades (backtest %.3fR)" % (
        st["day_avg_r"], pf, t, n, bt_ref.get("day_avg_r") or 0)


def update_one(path, bt, a, r):
    doc = read(path)
    if doc["status"] in ("FAILED", "PASSED", "ABANDONED") and not a.recheck:
        return doc, False
    ns = argparse.Namespace(bhav_dir=a.bhav_dir, bars_dir=a.bars_dir)
    strat = bt.build_strategy(doc["strategy"], ns)
    start = datetime.date.fromisoformat(doc["forward_start"])
    end = ist_today() if not a.today else datetime.date.fromisoformat(a.today)
    trades = strat.trades(doc["params"], start, end, doc["costs"], a.cfg)
    trades = [t for t in trades if t["signal_d"] >= doc["forward_start"]]
    st = bt.stats(trades)
    if doc["strategy"] == "momentum":
        data_end = strat.days[-1] if strat.days else None
    else:
        ds = [d for days in strat.intraday.values() for d in days]
        data_end = max(ds) if ds else None
    status, why = evaluate(doc, st, data_end, r)
    doc.update({"status": status, "why": why, "forward": st, "data_end": str(data_end),
                "updated_utc": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
                "trades": trades})
    doc["history"] = (doc.get("history") or [])[-200:] + [{"at": doc["updated_utc"], "status": status,
                                                           "trades": st.get("trades", 0),
                                                           "day_avg_r": st.get("day_avg_r")}]
    write(path, doc)
    return doc, True


def cmd_update(a, bt=None):
    bt = bt or load_backtest()
    a.cfg = bt.load_config()
    r = rules(a.cfg)
    paths = tests() if not a.hash else [os.path.join(STATE, a.hash + ".json")]
    if not paths:
        print("no forward tests (validate.py start --run RUN_ID)")
        return 0
    for p in paths:
        if not os.path.exists(p):
            raise ValError("no forward test %s" % os.path.basename(p))
        doc, changed = update_one(p, bt, a, r)
        print(line(doc) + ("" if changed else "  (final; --recheck to recompute)"))
    return 0


def line(doc):
    st = doc.get("forward") or {}
    return "%-9s %-12s %-10s from %s  %3d trades  %s R/day  %s" % (
        doc["strategy"], doc["params_hash"], doc["status"], doc["forward_start"], st.get("trades", 0),
        st.get("day_avg_r"), doc.get("why", ""))


def cmd_status(a, bt=None):
    paths = tests()
    if not paths:
        print("no forward tests")
        return 0
    for p in paths:
        print(line(read(p)))
    return 0


def cmd_stop(a, bt=None):
    p = os.path.join(STATE, a.hash + ".json")
    if not os.path.exists(p):
        raise ValError("no forward test %s" % a.hash)
    doc = read(p)
    doc["status"], doc["why"] = "ABANDONED", a.reason or "stopped by the user"
    write(p, doc)
    print(line(doc))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="validate.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--run", required=True)
    s.add_argument("--start", help="YYYY-MM-DD, later than the default only")
    s.add_argument("--restart", action="store_true")
    u = sub.add_parser("update")
    u.add_argument("--hash")
    u.add_argument("--bhav-dir")
    u.add_argument("--bars-dir")
    u.add_argument("--today")
    u.add_argument("--recheck", action="store_true")
    sub.add_parser("status")
    t = sub.add_parser("stop")
    t.add_argument("--hash", required=True)
    t.add_argument("--reason")
    a = ap.parse_args(argv)
    try:
        return {"start": cmd_start, "update": cmd_update, "status": cmd_status, "stop": cmd_stop}[a.cmd](a)
    except ValError as e:
        print("nse-validate: %s" % e, file=sys.stderr)
        return 1
    except Exception as e:     # errors from nse-backtest (missing data, costs not set) are plain lines too
        if e.__class__.__name__ in ("BTError", "IndiaError"):
            print("nse-validate: %s" % e, file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
