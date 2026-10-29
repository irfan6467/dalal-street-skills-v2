"""nse-validate: forward tests use only post-backtest signals, the same rules and costs, and a gate."""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import load  # noqa: E402
from test_backtest import market, write_bhav, weekdays, COSTS, CFG  # noqa: E402

bt = load("nse-backtest", "backtest")
val = load("nse-validate", "validate")

FAST = {"breakout_days": [63], "vol_mult": [2.0], "atr_stop": [2.0, 3.0], "exit_days": [10, 20]}
LOOSE = dict(bt.Momentum.FIXED, rs_top_pct=60.0, min_turnover_lacs=1.0)


class Forward(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.saved = (bt.Momentum.GRID, bt.Momentum.FIXED, val.ist_today)
        bt.Momentum.GRID, bt.Momentum.FIXED = FAST, LOOSE

    def tearDown(self):
        bt.Momentum.GRID, bt.Momentum.FIXED, val.ist_today = self.saved
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def make_run(self, edge, seed, n_days=760, split_at=480):
        write_bhav_dir = "nse/cache/bhav"
        os.makedirs(write_bhav_dir)
        write_bhav(write_bhav_dir, market(edge=edge, seed=seed, n_days=n_days))
        days = weekdays(datetime.date(2024, 1, 1), n_days)
        daily, _ = bt.load_daily(write_bhav_dir, end=days[split_at])
        res = bt.walk_forward(bt.Momentum(daily, days[:split_at + 1]), "momentum", days[0], days[split_at], 6, 3,
                              COSTS, CFG, 30)
        rid = bt.save_run(res)
        doc = bt.load_run(rid)
        doc["created_utc"] = days[split_at].isoformat() + "T12:00:00Z"
        with open(os.path.join(bt.STATE_DIR, rid + ".json"), "w") as fh:
            json.dump(doc, fh)
        val.ist_today = lambda: days[split_at]
        return rid, doc, days

    def args(self, **kw):
        import argparse
        return argparse.Namespace(**dict({"hash": None, "bhav_dir": None, "bars_dir": None, "today": None,
                                          "recheck": False, "run": None, "start": None, "restart": False}, **kw))

    def test_planted_edge_passes_forward_and_only_new_signals_count(self):
        rid, doc, days = self.make_run(0.006, 11)
        self.assertEqual(doc["verdict"], "CANDIDATE")
        self.assertEqual(val.cmd_start(self.args(run=rid), bt), 0)
        val.cmd_update(self.args(today=days[-1].isoformat()), bt)
        with open(os.path.join(val.STATE, doc["frozen_hash"] + ".json")) as fh:
            t = json.load(fh)
        self.assertEqual(t["status"], "PASSED", t["why"])
        self.assertTrue(all(x["signal_d"] > days[480].isoformat() for x in t["trades"]))

    def test_refuses_a_run_that_is_not_a_candidate(self):
        rid, doc, days = self.make_run(0.0, 22)
        if doc["verdict"] == "CANDIDATE":
            self.skipTest("noise produced a candidate on this seed")
        with self.assertRaises(val.ValError):
            val.cmd_start(self.args(run=rid), bt)

    def test_gate_logic(self):
        doc = {"strategy": "momentum", "forward_start": "2026-01-01", "backtest": {"max_dd_r": 8.0, "day_avg_r": 0.3}}
        r = dict(val.DEFAULTS)
        end = datetime.date(2026, 6, 1)
        good = {"trades": 40, "day_avg_r": 0.3, "profit_factor": 1.4, "day_t": 1.8, "max_dd_r": 5}
        self.assertEqual(val.evaluate(doc, good, end, r)[0], "PASSED")
        self.assertEqual(val.evaluate(doc, dict(good, trades=10), end, r)[0], "COLLECTING")
        self.assertEqual(val.evaluate(doc, dict(good, max_dd_r=13), end, r)[0], "FAILED")      # breaker 12R
        self.assertEqual(val.evaluate(doc, dict(good, day_t=0.5), end, r)[0], "FAILED")
        self.assertEqual(val.evaluate(doc, dict(good, day_avg_r=-0.1), end, r)[0], "FAILED")
        self.assertEqual(val.evaluate(doc, good, datetime.date(2026, 2, 1), r)[0], "COLLECTING")  # too few days


class RiskGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.check = load("nse-risk", "check")

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_untagged_and_unvalidated_are_blocked_passed_is_not(self):
        vb = self.check.validation_block
        self.assertIn("no strategy tag", vb({})[0])
        o = {"strategy": {"name": "momentum", "run_id": "momentum-x", "params_hash": "abc123abc123"}}
        self.assertIn("no forward test", vb(o)[0])
        os.makedirs("nse/validate")
        with open("nse/validate/abc123abc123.json", "w") as fh:
            json.dump({"status": "COLLECTING"}, fh)
        self.assertIn("COLLECTING", vb(o)[0])
        with open("nse/validate/abc123abc123.json", "w") as fh:
            json.dump({"status": "PASSED"}, fh)
        self.assertEqual(vb(o), [])


if __name__ == "__main__":
    unittest.main()
