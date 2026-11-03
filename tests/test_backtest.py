"""nse-backtest: the engine must find an edge that is really there, find none in noise,
and fill trades the way the Indian market would. Synthetic data, offline, seeded."""
import datetime
import json
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import load  # noqa: E402

bt = load("nse-backtest", "backtest")
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
COSTS = {"slippage_bps": 5.0, "notional_per_trade": 100000.0}
CFG = {"broker": "zerodha"}

HEAD = ("SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, "
        "TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER")


def weekdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def market(n_syms=80, n_days=520, edge=0.0, seed=1, split=None):
    """{date: [rows]}. A breakout (close above the 63-day high on a volume spike) is followed by 20 days
    of drift `edge` per day. With edge=0 the same breakouts happen and mean nothing."""
    rnd = random.Random(seed)
    days = weekdays(datetime.date(2024, 1, 1), n_days)
    out = {d: [] for d in days}
    for s in range(n_syms):
        sym = "SYM%03d" % s
        price = rnd.uniform(80, 900)
        closes, drift_left = [], 0
        for k, d in enumerate(days):
            spike = rnd.random() < 0.08
            vol = int(rnd.uniform(4e5, 6e5) * (3.0 if spike else 1.0))
            mu = edge if drift_left > 0 else 0.0
            drift_left = max(0, drift_left - 1)
            prev = price
            o = prev * (1 + rnd.gauss(0, 0.004))
            c = max(1.0, prev * (1 + mu + rnd.gauss(0, 0.02)))
            hi = max(o, c) * (1 + abs(rnd.gauss(0, 0.004)))
            lo = min(o, c) * (1 - abs(rnd.gauss(0, 0.008)))
            pc = prev
            if split and sym == split[0] and d == split[1]:
                pc, o, c, hi, lo, vol = prev / 2, o / 2, c / 2, hi / 2, lo / 2, vol * 2
            if len(closes) >= 63 and c > max(closes[-63:]) and spike and drift_left == 0:
                drift_left = 20
            closes.append(c if not (split and sym == split[0] and d >= split[1]) else c * 2)
            price = c
            to = c * vol / 1e5
            out[d].append("%s, EQ, %s, %.2f, %.2f, %.2f, %.2f, %.2f, %.2f, %.2f, %d, %.2f, 1000, %d, %.2f" % (
                sym, d.strftime("%d-%b-%Y"), pc, o, hi, lo, c, c, c, vol, to, vol // 2, 50.0))
    return out


def write_bhav(folder, mk):
    for d, rows in mk.items():
        with open(os.path.join(folder, "sec_bhavdata_full_%s.csv" % d.strftime("%d%m%Y")), "w") as fh:
            fh.write(HEAD + "\n" + "\n".join(rows) + "\n")


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()


class TestMomentumFindsRealEdgeOnly(Tmp):
    """Loosened hygiene filters so the synthetic universe produces enough trades; the logic is the same."""

    def run_wf(self, edge, seed):
        folder = "bhav_%s_%s" % (edge, seed)
        os.makedirs(folder)
        write_bhav(folder, market(edge=edge, seed=seed))
        daily, days = bt.load_daily(folder)
        strat = bt.Momentum(daily, days)
        strat.FIXED = dict(bt.Momentum.FIXED, rs_top_pct=60.0, min_turnover_lacs=1.0)
        strat.GRID = {"breakout_days": [63], "vol_mult": [2.0], "atr_stop": [2.0, 3.0], "exit_days": [10, 20]}
        type(strat).GRID, saved = strat.GRID, bt.Momentum.GRID
        try:
            return bt.walk_forward(strat, "momentum", days[0], days[-1], 6, 3, COSTS, CFG, min_trades=30)
        finally:
            bt.Momentum.GRID = saved

    def test_planted_edge_is_found(self):
        res = self.run_wf(edge=0.006, seed=11)
        self.assertEqual(res["verdict"], "CANDIDATE", res["why"])
        self.assertGreater(res["oos"]["day_ci95"][0], 0)

    def test_noise_is_not_called_an_edge(self):
        for seed in (21, 22, 23):
            res = self.run_wf(edge=0.0, seed=seed)
            self.assertNotEqual(res["verdict"], "CANDIDATE", "seed %d: %s" % (seed, res["why"]))

    def test_out_of_sample_trades_come_only_from_test_windows(self):
        res = self.run_wf(edge=0.006, seed=11)
        windows = {f["fold"]: f["test"] for f in res["folds"]}
        for t in res["trades"]:
            a, b = windows[t["fold"]]
            self.assertTrue(a <= t["signal_d"] <= b, t)
        first_test = res["folds"][0]["test"][0]
        self.assertTrue(all(t["signal_d"] >= first_test for t in res["trades"]))


class TestFills(Tmp):
    def bars(self, rows, start=datetime.date(2026, 1, 5)):
        days = weekdays(start, len(rows))
        return [{"d": d, "o": o, "h": h, "l": lo, "c": c, "rc": c, "pc": None, "v": 1000, "to": 100.0, "dp": 50.0,
                 "series": "EQ"} for d, (o, h, lo, c) in zip(days, rows)]

    def momentum_with(self, bars, signal_index):
        strat = bt.Momentum({"X": bars}, [b["d"] for b in bars])
        strat.signal_ok = lambda sym, i, p: i == signal_index
        strat.ind["X"]["atr"] = [1.0] * len(bars)
        return strat

    def flat(self, n, px=100.0):
        return [(px, px + 1, px - 1, px)] * n

    def test_gap_through_stop_fills_at_open_not_stop(self):
        rows = self.flat(70) + [(100, 101, 99, 100.5), (100.4, 101, 99.9, 100.8), (90, 91, 89, 90.5)] + self.flat(5, 90)
        strat = self.momentum_with(self.bars(rows), 70)
        t = strat.trades({"breakout_days": 63, "vol_mult": 1, "atr_stop": 2.0, "exit_days": 10}, rows and
                         datetime.date(2020, 1, 1), datetime.date(2030, 1, 1), {"slippage_bps": 0, "notional_per_trade": 1e5}, CFG)[0]
        self.assertEqual(t["why"], "gap")
        self.assertAlmostEqual(t["exit"], 90.0)
        self.assertLess(t["r"], -1.0)                  # a gap costs more than 1R

    def test_locked_day_cannot_exit(self):
        rows = self.flat(70) + [(100, 101, 99, 100.5), (100.4, 101, 99.9, 100.8), (95, 95, 95, 95),
                                (95, 95, 95, 95), (94, 96, 93, 95)] + self.flat(5, 95)
        strat = self.momentum_with(self.bars(rows), 70)
        t = strat.trades({"breakout_days": 63, "vol_mult": 1, "atr_stop": 2.0, "exit_days": 10},
                         datetime.date(2020, 1, 1), datetime.date(2030, 1, 1), {"slippage_bps": 0, "notional_per_trade": 1e5}, CFG)[0]
        self.assertEqual(t["exit_d"], self.bars(rows)[74]["d"].isoformat())   # out on the first unlocked day
        self.assertAlmostEqual(t["exit"], 94.0)

    def test_charges_are_real_indian_charges(self):
        t = bt.settle({"entry": 100.0, "exit": 110.0, "stop": 95.0}, {"slippage_bps": 0, "notional_per_trade": 100000},
                      "CNC", CFG)
        want = bt.trade_charges(100.0 * 1000, 110.0 * 1000, product="CNC", exchange="NSE", cfg=CFG)["total"]
        self.assertEqual(t["qty"], 1000)
        self.assertEqual(t["charges_inr"], want)
        self.assertAlmostEqual(t["r"], (10000 - want) / 5000.0, places=3)
        self.assertGreater(want, 200)                  # STT 0.1% each side alone is Rs 210

    def test_split_is_adjusted_from_prev_close(self):
        os.makedirs("bhav")
        mk = market(n_syms=3, n_days=120, seed=3, split=("SYM001", weekdays(datetime.date(2024, 1, 1), 120)[60]))
        write_bhav("bhav", mk)
        daily, _days = bt.load_daily("bhav")
        b = daily["SYM001"]
        jump = b[60]["o"] / b[59]["c"]
        self.assertTrue(0.9 < jump < 1.1, jump)        # continuous after adjustment
        self.assertEqual(b[60]["ca"], 0.5)
        self.assertAlmostEqual(b[59]["rc"] / b[59]["c"], 2.0, places=3)   # raw close kept for price filters


def five_min_day(d, path, vol=None):
    t0 = datetime.datetime.combine(d, datetime.time(9, 15), IST)
    out = []
    for k, (o, h, lo, c) in enumerate(path):
        out.append({"t": int((t0 + datetime.timedelta(minutes=5 * k)).timestamp()), "o": o, "h": h, "l": lo, "c": c,
                    "v": (vol[k] if vol else 1000)})
    return out


class TestORB(Tmp):
    P = {"or_minutes": 15, "stop_at": "or_low", "target_r": 2.0, "vol_mult": 1.0}

    def test_stop_and_target_in_one_bar_counts_the_stop(self):
        d = datetime.date(2026, 10, 27)
        path = [(100, 101, 99.5, 100.5)] * 3 + [(100.8, 101.5, 100.7, 101.4), (101.4, 120, 90, 101)] + [(101, 101.2, 100.8, 101)] * 60
        orb = bt.ORB({"X": {d: five_min_day(d, path)}})
        r = orb.day_trade("X", d, orb.intraday["X"][d], dict(bt.ORB.FIXED, **self.P), 0)
        self.assertEqual(r["why"], "stop")

    def test_flat_by_time(self):
        d = datetime.date(2026, 10, 27)
        path = [(100, 101, 99.5, 100.5)] * 3 + [(100.8, 101.5, 100.7, 101.4)] + [(101.4, 101.6, 101.2, 101.5)] * 80
        orb = bt.ORB({"X": {d: five_min_day(d, path)}})
        r = orb.day_trade("X", d, orb.intraday["X"][d], dict(bt.ORB.FIXED, **dict(self.P, target_r=0)), 0)
        self.assertEqual(r["why"], "flat")
        self.assertEqual(datetime.datetime.fromtimestamp(r["t_out"], IST).time(), datetime.time(15, 10))

    def synthetic(self, edge, seed, n_syms=12, n_days=90):
        rnd = random.Random(seed)
        data = {}
        for s in range(n_syms):
            days = {}
            for d in weekdays(datetime.date(2026, 3, 2), n_days):
                px, path, broke = rnd.uniform(200, 2000), [], False
                hi = None
                for k in range(75):
                    mu = edge if broke else 0.0
                    c = px * (1 + mu + rnd.gauss(0, 0.0025))
                    o = px
                    path.append((o, max(o, c) * 1.0007, min(o, c) * 0.9993, c))
                    px = c
                    if k == 2:
                        hi = max(p[1] for p in path)
                    if k > 2 and c > hi:
                        broke = True
                days[d] = five_min_day(d, path)
            data["S%02d" % s] = days
        return data

    def run_wf(self, edge, seed):
        orb = bt.ORB(self.synthetic(edge, seed))
        type(orb).GRID, saved = {"or_minutes": [15], "stop_at": ["or_low", "or_mid"], "target_r": [2.0, 0],
                                 "vol_mult": [1.0]}, bt.ORB.GRID
        try:
            d0 = datetime.date(2026, 3, 2)
            return bt.walk_forward(orb, "orb", d0, d0 + datetime.timedelta(days=130), 4, 2,
                                   {"slippage_bps": 2.0, "notional_per_trade": 100000.0}, CFG, 30, unit="weeks")
        finally:
            bt.ORB.GRID = saved

    def test_orb_planted_edge_found_noise_not(self):
        self.assertEqual(self.run_wf(0.0012, 5)["verdict"], "CANDIDATE")
        for seed in (6, 7):
            self.assertNotEqual(self.run_wf(0.0, seed)["verdict"], "CANDIDATE")


class TestVerdictAndRuns(Tmp):
    def test_verdicts(self):
        few = {"trades": 5, "entry_days": 5}
        self.assertEqual(bt.verdict(few, [0.1], 30)[0], "INSUFFICIENT DATA")
        neg = {"trades": 100, "entry_days": 60, "day_avg_r": -0.02, "day_ci95": [-0.1, 0.05], "profit_factor": 0.9}
        self.assertEqual(bt.verdict(neg, [0.1, -0.1], 30)[0], "NO EDGE")
        wide = dict(neg, day_avg_r=0.05, day_ci95=[-0.01, 0.12], profit_factor=1.2)
        self.assertEqual(bt.verdict(wide, [0.1, 0.1], 30)[0], "INCONCLUSIVE")
        good = dict(neg, day_avg_r=0.12, day_ci95=[0.04, 0.2], profit_factor=1.5)
        self.assertEqual(bt.verdict(good, [0.1, 0.2, -0.05], 30)[0], "CANDIDATE")
        self.assertEqual(bt.verdict(good, [0.1, -0.2, -0.05], 30)[0], "INCONCLUSIVE")

    def test_costs_must_be_set_by_the_user(self):
        with self.assertRaisesRegex(bt.BTError, "will not invent"):
            bt.costs_from({})
        self.assertEqual(bt.costs_from({"costs": {"slippage_bps": 3, "notional_per_trade": 50000}})["slippage_bps"], 3.0)

    def test_folds_never_overlap_and_tests_follow_train(self):
        fs = bt.folds(datetime.date(2024, 1, 1), datetime.date(2026, 6, 30), 12, 3)
        for a, b, c, d in fs:
            self.assertEqual(c, b + datetime.timedelta(days=1))
            self.assertLessEqual(c, d)
        tests = [(c, d) for _a, _b, c, d in fs]
        for (c1, d1), (c2, _d2) in zip(tests, tests[1:]):
            self.assertLess(d1, c2)

    def test_run_log_counts_trials(self):
        for _ in range(2):
            bt.save_run({"strategy": "orb", "verdict": "NO EDGE", "frozen_hash": "x", "variants_tried_per_fold": 24,
                         "oos": {"trades": 3}, "trades": []})
        self.assertIn("2 times (48 parameter variants", bt.trials_note("orb"))


class TestHolidayFiles(Tmp):
    """NSE answers a holiday's sec_bhavdata_full URL with the last session's file. Seen on 2026-11-03 for every
    2025-26 holiday (the 25 Dec 2025 file holds 24 Dec): 16 such files in 14 months of history."""

    def setUp(self):
        super().setUp()
        self.mk = market(n_syms=20, n_days=40, seed=3)
        self.days = sorted(self.mk)
        os.makedirs("bhav")
        write_bhav("bhav", self.mk)
        # sessions k and k+1 straddle a weekend; a file named for the Saturday between them holds session k,
        # exactly what a weekday holiday looks like (no session lost, one extra file)
        k = next(i for i in range(len(self.days) - 1) if (self.days[i + 1] - self.days[i]).days == 3)
        self.hol = self.days[k] + datetime.timedelta(days=1)
        with open(os.path.join("bhav", "sec_bhavdata_full_%s.csv" % self.days[k].strftime("%d%m%Y"))) as fh:
            self.copy = fh.read()
        with open(os.path.join("bhav", "sec_bhavdata_full_%s.csv" % self.hol.strftime("%d%m%Y")), "w") as fh:
            fh.write(self.copy)

    def test_the_loader_counts_a_session_once(self):
        daily, days = bt.load_daily("bhav")
        self.assertEqual(len(days), 40)
        self.assertNotIn(self.hol, days)
        for sym, bars in daily.items():
            self.assertEqual(len(bars), 40, sym)
            self.assertEqual(len({b["d"] for b in bars}), 40, sym)
            self.assertFalse(any("ca" in b for b in bars), sym)          # no split invented from a copy

    def test_the_fetcher_does_not_save_a_copy(self):
        served = {d.strftime("%d%m%Y"): (HEAD + "\n" + "\n".join(self.mk[d]) + "\n").encode() for d in self.days}
        hol = self.days[20]                                   # say session 20 was a holiday: NSE serves 19
        served[hol.strftime("%d%m%Y")] = served[self.days[19].strftime("%d%m%Y")]

        def fetch(url):
            return served[url.rsplit("_", 1)[1][:8]]
        got, skipped = bt.fetch_bhav(self.days[18], self.days[21], "fresh", pause=0, fetch=fetch)
        self.assertEqual((got, skipped), (3, 1))
        self.assertFalse(os.path.exists(os.path.join("fresh", "sec_bhavdata_full_%s.csv" % hol.strftime("%d%m%Y"))))
        self.assertEqual(bt.bhav_date(served[self.days[19].strftime("%d%m%Y")]), self.days[19])
        self.assertIsNone(bt.bhav_date(b"<html>not a bhavcopy</html>"))


if __name__ == "__main__":
    unittest.main()
