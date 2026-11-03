"""blind/blind_test.py: the wall between screening and the test day, the lock on picks, and the scoring."""
import datetime
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import ROOT  # noqa: E402
from test_backtest import market, write_bhav, weekdays  # noqa: E402


def load():
    spec = importlib.util.spec_from_file_location("blind_test", os.path.join(ROOT, "blind", "blind_test.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Blind(unittest.TestCase):
    def setUp(self):
        self.bl = load()
        self.tmp = tempfile.mkdtemp()
        days = weekdays(datetime.date(2024, 1, 1), 300)
        self.test_day = days[122]             # day 121 has momentum and breakout qualifiers (seed 11)
        mk = market(n_syms=60, n_days=300, edge=0.006, seed=11)
        self.hist = os.path.join(self.tmp, "hist")
        self.day = os.path.join(self.tmp, "day")
        os.makedirs(self.hist)
        os.makedirs(self.day)
        write_bhav(self.hist, {k: v for k, v in mk.items() if k < self.test_day})        # later days never written
        write_bhav(self.day, {self.test_day: mk[self.test_day]})
        self.folder = os.path.join(self.tmp, "t")
        os.makedirs(self.folder)
        R = json.load(open(os.path.join(ROOT, "blind", "2026-11-02", "rules.json")))
        R["test_day"] = self.test_day.isoformat()
        R["universe"]["min_turnover_lacs_med20"] = 1.0
        R["set_a_momentum"]["params"]["breakout_days"] = 63
        R["set_b_breakout"]["min_up_pct"] = 1.0
        json.dump(R, open(os.path.join(self.folder, "rules.json"), "w"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sha(self, p):
        return hashlib.sha256(open(p, "rb").read()).hexdigest()

    def test_screen_refuses_if_the_test_day_is_in_its_data(self):
        shutil.copy(os.path.join(self.day, os.listdir(self.day)[0]), self.hist)
        with self.assertRaisesRegex(SystemExit, "wall is broken"):
            self.bl.cmd_screen(self.folder, self.hist, fetch=False)

    def test_full_run_and_locks(self):
        self.assertEqual(self.bl.cmd_screen(self.folder, self.hist, fetch=False), 0)
        P = json.load(open(os.path.join(self.folder, "picks.json")))
        self.assertLess(P["signal_day"], self.test_day.isoformat())
        self.assertGreater(P["universe_size"], 0)
        with self.assertRaisesRegex(SystemExit, "locked"):
            self.bl.cmd_screen(self.folder, self.hist, fetch=False)
        sha = self.sha(os.path.join(self.folder, "picks.json"))
        self.assertEqual(self.bl.cmd_reveal(self.folder, self.day, fetch=False, recorded_sha=sha), 0)
        res = json.load(open(os.path.join(self.folder, "result.json")))
        self.assertEqual((P["picks"]["A"]["qualified"], P["picks"]["B"]["qualified"]), (2, 2))
        traded = [t for k in ("A", "B") for t in res["sets"][k]["trades"] if "net_inr" in t]
        self.assertGreater(len(traded), 0, "the run must actually place trades")
        for k in ("A", "B"):
            for t in res["sets"][k]["trades"]:
                if "net_inr" in t:
                    self.assertLessEqual(t["risk_inr"] + t["charges_inr"], 500.01 + t["charges_inr"])
                    self.assertLessEqual(t["qty"] * t["entry"], 8333.34)
                    if t["exit_reason"] == "stop":
                        self.assertGreaterEqual(t["net_inr"], -500.01)
        self.assertIsNotNone(res["benchmark"]["avg_open_to_close_pct"])
        self.assertTrue(os.path.exists(os.path.join(self.folder, "RESULT.md")))

    def test_tampering_voids_the_reveal(self):
        self.bl.cmd_screen(self.folder, self.hist, fetch=False)
        sha = self.sha(os.path.join(self.folder, "picks.json"))
        p = os.path.join(self.folder, "picks.json")
        doc = json.load(open(p))
        doc["picks"]["A"]["taken"] = []
        json.dump(doc, open(p, "w"))
        with self.assertRaisesRegex(SystemExit, "changed after it was committed"):
            self.bl.cmd_reveal(self.folder, self.day, fetch=False, recorded_sha=sha)
        R = json.load(open(os.path.join(self.folder, "rules.json")))
        R["max_positions"] = 5
        json.dump(R, open(os.path.join(self.folder, "rules.json"), "w"))
        with self.assertRaisesRegex(SystemExit, "rules.json changed"):
            self.bl.cmd_reveal(self.folder, self.day, fetch=False)

    def test_no_session_is_void(self):
        self.bl.cmd_screen(self.folder, self.hist, fetch=False)
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        self.bl.cmd_reveal(self.folder, empty, fetch=False)
        self.assertIn("void", json.load(open(os.path.join(self.folder, "result.json"))))

    def test_stop_assumed_first_when_both_hit(self):
        bt = self.bl.load_bt()
        R = json.load(open(os.path.join(self.folder, "rules.json")))
        rec = {"symbol": "X", "signal_close": 100.0, "signal_low": 97.0, "atr14": 2.0}
        bar = {"o": 100.0, "h": 120.0, "l": 90.0, "c": 110.0}
        t = self.bl.size_and_play(bt, R, rec, bar, lambda r, e: max(r["signal_low"], e - 1.5 * r["atr14"]))
        self.assertEqual(t["exit_reason"], "stop")
        self.assertLess(t["net_inr"], 0)


if __name__ == "__main__":
    unittest.main()
