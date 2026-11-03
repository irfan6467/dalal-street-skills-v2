"""Replay one NSE session through the Dalal Street skills, bar by bar, as rules.json phase_2_test_day says.

Every skill runs as its own script (the same commands as the 29 Oct live paper trade), in a scratch nse/ folder,
with --now pinned and its data cut at that clock: a call at 10:05 sees only bars that closed by 10:05.

  python3 replay.py --day 2026-10-15 --bars DIR --ws DIR --ref DIR --prev-bhav FILE --out DIR SYM ...
"""
import argparse
import copy
import csv
import datetime
import io
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
EPOCH = datetime.datetime(1970, 1, 1)
IST = datetime.timedelta(hours=5, minutes=30)
STEP = 300


def ist_of(ts):
    return EPOCH + datetime.timedelta(seconds=ts) + IST


def ts_of(ist):
    return int((ist - IST - EPOCH).total_seconds())


class Replay(object):
    def __init__(self, a):
        self.a = a
        self.day = datetime.date.fromisoformat(a.day)
        self.ws = os.path.abspath(a.ws)
        self.out = os.path.abspath(a.out)
        self.cut_dir = os.path.join(self.ws, "cut")
        os.makedirs(self.out, exist_ok=True)
        os.makedirs(self.cut_dir, exist_ok=True)
        self.n = 0
        self.events = []
        self.raw = {}
        for s in a.symbols:
            name = [f for f in os.listdir(a.bars) if f.startswith(s + ".NS_")][0]
            with open(os.path.join(a.bars, name), encoding="utf-8") as fh:
                self.raw[s] = json.load(fh)
        self.prev = {}
        text = open(a.prev_bhav, encoding="utf-8").read().replace(", ", ",")
        for r in csv.DictReader(io.StringIO(text)):
            if r["SERIES"].strip() == "EQ":
                self.prev[r["SYMBOL"].strip()] = float(r["CLOSE_PRICE"])
        self.empty_prices = os.path.join(self.ws, "no-prices.json")

    # ------------------------------------------------------------ data at a clock
    def cut(self, sym, t):
        """The chart doc with only bars that closed by IST time t, nothing after the test day."""
        doc = copy.deepcopy(self.raw[sym])
        r = doc["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        keep = [i for i, ts in enumerate(r["timestamp"])
                if ist_of(ts).date() <= self.day and ist_of(ts + STEP) <= t]
        r["timestamp"] = [r["timestamp"][i] for i in keep]
        for k in list(q):
            q[k] = [q[k][i] for i in keep]
        today = [i for i, ts in enumerate(r["timestamp"]) if ist_of(ts).date() == self.day]
        m = r["meta"]
        closes = [(r["timestamp"][i], q["close"][i]) for i in today if q["close"][i] is not None]
        if closes:
            m["regularMarketPrice"] = closes[-1][1]
            m["regularMarketTime"] = min(ts_of(t), closes[-1][0] + STEP)
            m["regularMarketDayHigh"] = max(q["high"][i] for i in today if q["high"][i] is not None)
            m["regularMarketDayLow"] = min(q["low"][i] for i in today if q["low"][i] is not None)
        m["previousClose"] = m["chartPreviousClose"] = self.prev.get(sym)
        m.pop("currentTradingPeriod", None)
        m.pop("tradingPeriods", None)
        path = os.path.join(self.cut_dir, "%s.NS-%s.json" % (sym, t.strftime("%H%M")))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        qdir = os.path.join(self.cut_dir, "q-" + t.strftime("%H%M"))
        os.makedirs(qdir, exist_ok=True)
        with open(os.path.join(qdir, "yahoo-%s.json" % sym), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        if not os.path.exists(os.path.join(qdir, "sec_list.csv")):
            with open(os.path.join(self.a.ref, "sec_list.csv"), "rb") as src, \
                    open(os.path.join(qdir, "sec_list.csv"), "wb") as dst:
                dst.write(src.read())
        return path, qdir

    # ------------------------------------------------------------ running a skill
    def run(self, label, script, args, t=None, sym=""):
        self.n += 1
        cmd = [sys.executable, "-u", os.path.join(ROOT, script)] + args
        if t is not None:
            cmd += ["--now", t.strftime("%Y-%m-%d %H:%M")]
        p = subprocess.run(cmd, cwd=self.ws, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=dict(os.environ, NSE_DATA="yahoo", PYTHONIOENCODING="utf-8"))
        name = "%02d-%s%s.log" % (self.n, label, ("-" + sym) if sym else "")
        shown = " ".join(c if not c.startswith(ROOT) else c[len(ROOT) + 1:] for c in cmd[2:])
        shown = shown.replace(self.ws + os.sep, "")
        with open(os.path.join(self.out, name), "w", encoding="utf-8") as fh:
            fh.write("$ %s\n(exit %d)\n%s%s" % (shown, p.returncode, p.stdout, p.stderr))
        self.events.append({"n": self.n, "step": label, "symbol": sym, "at": t.strftime("%H:%M") if t else None,
                            "exit": p.returncode, "log": name})
        return p

    def fo(self):
        return ["--fo-lots", os.path.join(self.a.ref, "fo_mktlots.csv")]

    def bars_args(self, t):
        out = []
        for s in self.a.symbols:
            path, _ = self.cut(s, t)
            out += ["--bars", "%s=%s" % (s, path)]
        return out

    def track(self, t):
        """Working orders and open positions against bars up to t (never against an old quote: open bug 2)."""
        return self.run("track", "nse-broker/ticket.py", ["track", "--prices", self.empty_prices] + self.bars_args(t), t)

    def alerts_check(self, t):
        qdir = None
        for s in self.a.symbols:
            _, qdir = self.cut(s, t)
        return self.run("alerts-check", "nse-alerts/alerts.py", ["check", "--offline", qdir], t)

    def open_positions(self):
        try:
            with open(os.path.join(self.ws, "nse", "portfolio.json"), encoding="utf-8") as fh:
                pf = json.load(fh)
        except (OSError, ValueError):
            return []
        return [p for p in pf.get("positions", []) if float(p.get("shares") or 0) > 0]

    def working(self):
        try:
            with open(os.path.join(self.ws, "nse", "state", "broker.json"), encoding="utf-8") as fh:
                st = json.load(fh)
        except (OSError, ValueError):
            return False
        return any(o.get("status") == "working" for o in st.get("orders", []))

    def enter(self, s, t):
        """One trigger at its own clock: entry, exit, time machine gate, quote, budget, risk, ticket, yes, alerts."""
        path, qdir = self.cut(s, t)
        row = {"symbol": s, "trigger_close": t.strftime("%H:%M")}
        self.run("entry", "nse-entry/entry.py", ["check", s, "--offline", path, "--write"] + self.fo(), t, s)
        if '"status": "triggered"' not in open(os.path.join(self.ws, "nse", "entry.json")).read():
            row["skipped"] = "entry not triggered at its own clock"
            return row
        p = self.run("exit", "nse-exit/exit.py", ["plan", "--write"] + self.fo(), t, s)
        if p.returncode:
            row["skipped"] = "exit plan refused"
            return row
        p = self.run("timemachine", "nse-timemachine/timemachine.py",
                     ["run", "--offline", path, "--json"] + self.fo(), t, s)
        try:
            tm = json.loads(p.stdout)
            row["tm_avg_R"] = tm["mc"]["avg_R"]
            row["tm_verdict"] = tm["verdict"]
        except (ValueError, KeyError):
            row["skipped"] = "time machine failed"
            return row
        if not row["tm_avg_R"] > 0 and not self.a.smoke_no_tm_gate:
            row["skipped"] = "time machine average R %.3f is not above 0" % row["tm_avg_R"]
            return row
        self.run("quote", "nse-alerts/alerts.py", ["quote", s, "--offline", qdir, "--write"], t, s)
        p = self.run("budget", "nse-budget/size.py", ["--write", "--offline"] + self.fo(), t, s)
        if p.returncode:
            row["skipped"] = "budget refused"
            return row
        p = self.run("risk", "nse-risk/check.py", ["--offline", "--seclist",
                                                   os.path.join(self.a.ref, "sec_list.csv")] + self.fo(), t, s)
        row["risk"] = "PASS" if re.search(r"^PASS\b", p.stdout, re.M) else "BLOCK"
        if row["risk"] != "PASS":
            row["skipped"] = "risk BLOCK"
            return row
        self.run("ticket", "nse-broker/ticket.py", ["show"], t, s)
        self.run("approve", "nse-broker/ticket.py", ["approve", "--index", "1", "--reply", "yes",
                                                     "--prices", "nse/prices.json"], t, s)
        self.run("alerts-add", "nse-alerts/alerts.py", ["add", "--from-plan", "--offline", qdir], t, s)
        with open(os.path.join(self.ws, "nse", "plan.json"), encoding="utf-8") as fh:
            plan = json.load(fh)
        row["plan"] = {k: plan.get(k) for k in ("entry", "stop", "target", "flat_by")}
        return row

    def flatten(self, s, t):
        """Close a position at its flat_by, through risk and a yes like any order (the 29 Oct flat-prep/approve)."""
        _, qdir = self.cut(s, t)
        self.run("flatten", "nse-broker/ticket.py", ["flatten", "--symbol", s, "--product", "MIS"], t, s)
        self.run("flat-quote", "nse-alerts/alerts.py", ["quote", s, "--offline", qdir, "--write"], t, s)
        self.run("flat-risk", "nse-risk/check.py", ["--offline", "--seclist",
                                                    os.path.join(self.a.ref, "sec_list.csv")] + self.fo(), t, s)
        self.run("flat-approve", "nse-broker/ticket.py", ["approve", "--index", "1", "--reply", "yes",
                                                          "--prices", "nse/prices.json"], t, s)

    # ------------------------------------------------------------ the day
    def main(self):
        with open(self.empty_prices, "w") as fh:
            fh.write("{}")
        day0 = datetime.datetime.combine(self.day, datetime.time(9, 0))
        self.run("clock", "nse-alerts/alerts.py", ["clock"], day0)
        end = datetime.datetime.combine(self.day, datetime.time(15, 30))
        triggers = []
        for rank, s in enumerate(self.a.symbols):
            path, _ = self.cut(s, end)
            p = self.run("entry-scan", "nse-entry/entry.py",
                         ["check", s, "--offline", path, "--json"] + self.fo(), end, s)
            try:
                res = json.loads(p.stdout)
            except ValueError:
                continue
            trig = res.get("trigger")
            if res.get("status") == "triggered" and trig:
                hh, mm = map(int, trig["close_time"].split(":"))
                triggers.append((datetime.datetime.combine(self.day, datetime.time(hh, mm)), rank, s))
        triggers.sort()
        summary = {"triggers": [{"symbol": s, "at": t.strftime("%H:%M")} for t, _, s in triggers], "trades": []}
        flat = {}                                                   # flat_by time -> symbols
        t = datetime.datetime.combine(self.day, datetime.time(9, 20))
        while t < end:
            t += datetime.timedelta(seconds=STEP)
            if self.open_positions() or self.working():
                self.track(t)                                       # every closed bar, like the live tracker
                self.alerts_check(t)
            for tt, _, s in triggers:
                if tt == t:
                    row = self.enter(s, t)
                    summary["trades"].append(row)
                    if "plan" in row:
                        hh, mm = map(int, row["plan"]["flat_by"].split(":"))
                        flat.setdefault(datetime.datetime.combine(self.day, datetime.time(hh, mm)), []).append(s)
            if t in flat:
                held = {str(p["ticker"]).upper() for p in self.open_positions()}
                for s in flat[t]:
                    if s in held:
                        self.flatten(s, t)
        self.track(end)
        self.run("log", "nse-broker/ticket.py", ["log"], end)
        with open(os.path.join(self.out, "events.json"), "w", encoding="utf-8") as fh:
            json.dump({"events": self.events, "summary": summary}, fh, indent=1)
        print(json.dumps(summary, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--bars", required=True)
    ap.add_argument("--ws", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--prev-bhav", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--smoke-no-tm-gate", action="store_true",
                    help="plumbing test only (used on the signal day, never on the test day)")
    ap.add_argument("symbols", nargs="+")
    a = ap.parse_args()
    a.ref = os.path.abspath(a.ref)
    a.bars = os.path.abspath(a.bars)
    Replay(a).main()


if __name__ == "__main__":
    main()
