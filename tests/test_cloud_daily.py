"""scripts/cloud/daily_eod.py: report and shadow journal, offline on synthetic NSE data."""
import datetime
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import ROOT, FIX  # noqa: E402
from test_backtest import market, write_bhav, weekdays  # noqa: E402


def load_job():
    spec = importlib.util.spec_from_file_location("daily_eod", os.path.join(ROOT, "scripts", "cloud", "daily_eod.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class DailyJob(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.job = load_job()
        self.job.JOURNAL_DIR = os.path.join(self.tmp, "journal")
        self.job.REPORTS_DIR = os.path.join(self.tmp, "reports")
        self.job.SHADOW = os.path.join(self.job.JOURNAL_DIR, "shadow-momentum.json")
        self.job.DEALS_STATE = os.path.join(self.job.JOURNAL_DIR, "deals.json")
        self.bhav = os.path.join(self.tmp, "bhav")
        os.makedirs(self.bhav)
        write_bhav(self.bhav, market(n_syms=40, n_days=330, edge=0.006, seed=11))
        self.days = weekdays(datetime.date(2024, 1, 1), 330)
        self.deals = os.path.join(self.tmp, "deals")
        os.makedirs(self.deals)
        shutil.copy(os.path.join(FIX, "nse-bigmoney", "bulk-2026-10-23.csv"), os.path.join(self.deals, "bulk.csv"))
        shutil.copy(os.path.join(FIX, "nse-bigmoney", "block-synthetic.csv"), os.path.join(self.deals, "block.csv"))
        self.cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_day(self, d):
        return self.job.main(["--bhav-dir", self.bhav, "--no-fetch", "--deals-offline", self.deals,
                              "--date", d.isoformat()])

    def test_report_and_journal(self):
        self.assertEqual(self.run_day(self.days[300]), 0)
        rep = open(os.path.join(self.job.REPORTS_DIR, "%s.md" % self.days[300]), encoding="utf-8").read()
        for part in ("## 1. Delivery spikes", "## 2. Bulk and block deals", "## 3. Momentum setups (study only)",
                     "NO EDGE", "## 4. Shadow journal", "Not financial advice"):
            self.assertIn(part, rep)
        j = json.load(open(self.job.SHADOW))
        self.assertEqual(j["started"], self.days[300].isoformat())
        self.assertEqual(j["params_hash"], "981319a2c38f")
        self.assertEqual(len(j["signals"]), 1)

    def test_journal_grows_and_scores_only_after_its_start(self):
        for d in (self.days[280], self.days[300], self.days[329]):
            self.assertEqual(self.run_day(d), 0)
        j = json.load(open(self.job.SHADOW))
        self.assertEqual(len(j["signals"]), 3)
        self.assertTrue(all(t["signal_d"] >= j["started"] for t in j["closed_trades"]))
        self.assertEqual(self.run_day(self.days[329]), 0)              # a re-run of the same day adds nothing
        self.assertEqual(len(json.load(open(self.job.SHADOW))["signals"]), 3)

    def test_missing_session_is_refused(self):
        self.assertEqual(self.job.main(["--bhav-dir", self.bhav, "--no-fetch", "--no-deals", "--date",
                                        "2030-01-01"]), 3)

    def test_delivery_spike_rules(self):
        d = datetime.date(2026, 10, 29)
        base = [{"d": d - datetime.timedelta(days=30 - i), "v": 1000, "dp": 40.0, "rc": 100.0, "to": 100.0,
                 "series": "EQ"} for i in range(21)]
        spike = base[:-1] + [dict(base[-1], d=d, v=5000, dp=80.0)]     # delivered 4000 vs avg 400
        thin = base[:-1] + [dict(base[-1], d=d, v=5000, dp=80.0, to=10.0)]
        got = self.job.delivery_spikes({"A": spike, "B": thin, "C": base[:-1] + [dict(base[-1], d=d)]}, d)
        self.assertEqual([g["symbol"] for g in got], ["A"])
        self.assertEqual(got[0]["deliv_x"], 10.0)


if __name__ == "__main__":
    unittest.main()
