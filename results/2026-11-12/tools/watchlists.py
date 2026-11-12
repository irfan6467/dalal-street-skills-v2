"""The scanner's watchlist for each test day, built only from the session before it (as on 3 Nov for 15 Oct):
nse-scanner daily scan on closes from NSE files up to the signal day, plus its delivery scan of that day's
bhavcopy; union, ranked by the signal day's turnover, top 10.

  python3 watchlists.py --bhav nse/cache/bhav --universe nifty200.txt --days 2026-09-15,2026-09-16 --out FILE
"""
import argparse
import csv
import datetime
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SPARK = os.path.join(ROOT, "blind", "skills-replay", "tools", "spark_from_bhav.py")
SCAN = os.path.join(ROOT, "nse-scanner", "scan.py")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bhav", required=True)
    ap.add_argument("--universe", required=True)
    ap.add_argument("--days", required=True, help="comma-separated test days")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    spec = importlib.util.spec_from_file_location("bt_w", os.path.join(ROOT, "nse-backtest", "backtest.py"))
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)
    bhav, uni = os.path.abspath(a.bhav), os.path.abspath(a.universe)
    out = {}
    for t in a.days.split(","):
        T = datetime.date.fromisoformat(t)
        S = T - datetime.timedelta(days=1)
        while not bt.trading_day(S)[0]:
            S -= datetime.timedelta(days=1)
        with tempfile.TemporaryDirectory() as tmp:
            spark = os.path.join(tmp, "spark.json")
            frm = (S - datetime.timedelta(days=135)).isoformat()
            subprocess.run([sys.executable, SPARK, "--bhav", bhav, "--from", frm, "--to", S.isoformat(),
                            "--universe", uni, "--out", spark], check=True, capture_output=True, cwd=tmp)
            hits = {}
            for mode, extra, now in (("daily", ["--offline", spark], "16:00"), ("delivery", ["--lists", bhav], "18:30")):
                wl = os.path.join(tmp, mode + ".json")
                subprocess.run([sys.executable, SCAN, mode, "--universe-file", uni, "--now", "%s %s" % (S, now),
                                "--state-dir", os.path.join(tmp, "state"), "--out", wl, "--quiet"] + extra,
                               capture_output=True, cwd=tmp)
                if os.path.exists(wl):
                    for h in json.load(open(wl))["hits"]:
                        hits.setdefault(h["symbol"], set()).update(h["setups"])
        turn = {}
        text = open(os.path.join(bhav, "sec_bhavdata_full_%s.csv" % S.strftime("%d%m%Y"))).read().replace(", ", ",")
        for r in csv.DictReader(io.StringIO(text)):
            if r["SERIES"].strip() == "EQ":
                turn[r["SYMBOL"].strip()] = float(r["TURNOVER_LACS"])
        ranked = sorted(hits, key=lambda s: -turn.get(s, 0))
        out[t] = {"signal_day": S.isoformat(), "union": len(ranked), "top10": ranked[:10],
                  "setups": {s: sorted(hits[s]) for s in ranked[:10]}}
        print(t, S, len(ranked), ranked[:10], file=sys.stderr)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)


if __name__ == "__main__":
    main()
