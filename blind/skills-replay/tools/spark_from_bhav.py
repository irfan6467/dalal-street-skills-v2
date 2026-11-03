"""NSE bhavcopies (only those named up to --to) -> a Yahoo-spark-shaped JSON of daily closes that
nse-scanner's `daily --offline` reads. Closes are split/bonus adjusted by nse-backtest's own loader, as Yahoo's are.

  python3 spark_from_bhav.py --bhav DIR --to 2026-10-14 --from 2026-06-01 --universe FILE --out spark.json
"""
import argparse
import datetime
import importlib.util
import json
import os
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def load(folder, name):
    spec = importlib.util.spec_from_file_location("x_" + name, os.path.join(ROOT, folder, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bhav", required=True)
    ap.add_argument("--from", dest="frm", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--universe", required=True, help="scan.py universe list (SYMBOL|Name lines) or the cached csv")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    bt, scan = load("nse-backtest", "backtest"), load("nse-scanner", "scan")
    frm, to = datetime.date.fromisoformat(a.frm), datetime.date.fromisoformat(a.to)
    tmp = tempfile.mkdtemp()
    try:
        for d, p in bt.bhav_files(a.bhav):          # the wall: copy only files named in [from, to]
            if frm <= d <= to:
                os.symlink(os.path.abspath(p), os.path.join(tmp, os.path.basename(p)))
        daily, days = bt.load_daily(tmp)
    finally:
        shutil.rmtree(tmp)
    if days[-1] > to:
        raise SystemExit("a file holds a session after %s" % to)
    with open(a.universe, encoding="utf-8") as fh:
        syms = [ln.split("|")[0].strip().upper() for ln in fh if ln.strip()]
    out, missing = {}, []
    for s in syms:
        bars = [b for b in daily.get(s, []) if b["series"] == "EQ"]
        if len(bars) < 2:
            missing.append(s)
            continue
        y = scan.parse_symbol(s)["yahoo"]
        ts = [int((datetime.datetime.combine(b["d"], datetime.time(3, 45)) - datetime.datetime(1970, 1, 1))
                  .total_seconds()) for b in bars]                                  # 09:15 IST, like Yahoo
        out[y] = {"symbol": y, "timestamp": ts, "close": [round(b["c"], 2) for b in bars],
                  "chartPreviousClose": round(bars[0]["c"], 2), "previousClose": None, "dataGranularity": "1d",
                  "dataSource": "nse-bhavcopy"}
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    print("%d symbols, sessions %s..%s (%d), missing %s" % (len(out), days[0], days[-1], len(days), missing))


if __name__ == "__main__":
    main()
