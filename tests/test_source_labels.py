"""Output names the feed that actually answered (open bug 3 of the 29 Oct test: Shoonya data was labelled
"Yahoo"). shoonya_feed stamps meta.dataSource = "shoonya" on every chart and spark entry it builds; a skill
reads that stamp instead of assuming Yahoo. The fixtures are real Yahoo documents; a Shoonya one is the same
document with the stamp, which is exactly what shoonya_feed.chart_doc adds."""
import copy
import gzip
import json
import os
import re
import unittest

from _load import load, FIX, ROOT

entry = load("nse-entry", "entry")
alerts = load("nse-alerts", "alerts")
tm = load("nse-timemachine", "timemachine")


def read(*p):
    with open(os.path.join(*p), encoding="utf-8") as fh:
        return fh.read()


def stamped(doc):
    d = copy.deepcopy(doc)
    d["chart"]["result"][0]["meta"]["dataSource"] = "shoonya"
    return d


REL = json.loads(read(FIX, "nse-entry", "RELIANCE.NS.json"))


class Entry(unittest.TestCase):
    def check(self, doc, feed, note):
        c = entry.parse_chart(json.dumps(doc), "RELIANCE.NS")
        r = entry.evaluate(c, entry.parse_now("2026-10-26 10:30"))
        self.assertEqual(r["source"], feed)
        self.assertIn(note, entry.describe(r)[0])
        self.assertTrue(r["lag_note"].startswith(entry.FEED_NAME[feed] + "'s last trade is 10:27:20"))
        self.assertEqual(entry.entry_doc(r, entry.parse_now("2026-10-26 10:30"),
                                         entry.parse_symbol("RELIANCE"))["source"], feed)
        return r

    def test_yahoo_stays_yahoo(self):
        r = self.check(REL, "yahoo", "(Yahoo, free, can lag)")
        self.assertIn("once Yahoo catches up", r["lag_note"])

    def test_shoonya_is_named(self):
        r = self.check(stamped(REL), "shoonya", "(Shoonya, exchange data)")
        self.assertNotIn("Yahoo", r["lag_note"])
        self.assertNotIn("Yahoo", "\n".join(entry.describe(r)))


class Alerts(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(read(FIX, "nse-alerts", "yahoo-RELIANCE.json"))

    def test_quote_and_prices_json_name_the_feed(self):
        y = alerts.parse_yahoo(json.dumps(self.doc), "RELIANCE")
        s = alerts.parse_yahoo(json.dumps(stamped(self.doc)), "RELIANCE")
        self.assertEqual((y["source"], s["source"]), ("yahoo", "shoonya"))
        path = os.path.join(os.getcwd(), "prices-source-test.json")
        try:
            doc = alerts.write_prices(path, {"RELIANCE": s}, alerts.parse_now("2026-10-26 10:30"))
            self.assertEqual((doc["source"], doc["detail"]["RELIANCE"]["source"]), ("shoonya", "shoonya"))
            self.assertFalse(doc["note"].startswith("Delayed"))
            os.remove(path)
            doc = alerts.write_prices(path, {"RELIANCE": s, "TCS": dict(y, symbol="TCS")},
                                      alerts.parse_now("2026-10-26 10:30"))
            self.assertEqual(doc["source"], "shoonya+yahoo")
            self.assertTrue(doc["note"].startswith("Delayed quotes"))
        finally:
            if os.path.exists(path):
                os.remove(path)


class TimeMachine(unittest.TestCase):
    PLAN = {"created_by": "nse-exit", "symbol": "RELIANCE", "exchange": "NSE", "yahoo": "RELIANCE.NS",
            "product": "MIS", "side": "buy", "date": "2026-10-26", "setup": "orb", "entry": 1193.60,
            "entry_time": "10:00", "entry_bar": 8, "interval": "5m",
            "opening_range": {"high": 1192.0, "low": 1175.3, "minutes": 15}, "stop": 1183.65, "stop_rule": "or_mid",
            "target": 1203.55, "target_r": 1.0, "risk_per_share": 9.95, "reward_risk": 1.0, "flat_by": "15:10"}

    def test_source_field(self):
        with gzip.open(os.path.join(FIX, "nse-timemachine", "RELIANCE-60d5m.json.gz"), "rt", encoding="utf-8") as fh:
            doc = json.load(fh)
        now, plan = tm.parse_now("2026-10-26 10:30"), tm.normalize_plan(dict(self.PLAN))
        self.assertEqual(tm.time_machine(json.dumps(doc), plan, now, replays=20)["source"], "yahoo")
        self.assertEqual(tm.time_machine(json.dumps(stamped(doc)), plan, now, replays=20)["source"], "shoonya")
        self.assertEqual(tm.time_machine(json.dumps(stamped(doc)), plan, now, replays=20, source="offline")["source"],
                         "offline")


class Helpers(unittest.TestCase):
    def test_data_source_reads_documents_metas_and_spark_entries(self):
        self.assertEqual(entry.data_source(stamped(REL)), "shoonya")
        self.assertEqual(entry.data_source(stamped(REL)["chart"]["result"][0]["meta"]), "shoonya")
        self.assertEqual(entry.data_source({"close": [1], "timestamp": [1], "dataSource": "shoonya"}), "shoonya")
        for other in (REL, {}, None, "text", {"chart": {"result": None}}):
            self.assertEqual(entry.data_source(other), "yahoo")
        self.assertEqual(entry.mixed_source(["shoonya", "shoonya"]), "shoonya")
        self.assertEqual(entry.mixed_source(["yahoo", "shoonya"]), "shoonya+yahoo")

    def test_shoonya_spark_entries_are_stamped(self):
        src = read(ROOT, "_shared", "shoonya_feed.py")
        body = src[src.index("def answer_spark("):src.index("_CHART = ")]
        self.assertIn('"dataSource": "shoonya"', body)

    def test_one_helper_everywhere(self):
        """The helper block is pasted, not imported: every copy equals india_core's, byte for byte."""
        core = read(ROOT, "_shared", "india_core.py")
        block = core[core.index("FEED_NAME = {"):core.index("\n\ndef _yahoo_http(path):")]
        for rel in ("nse-scanner/scan.py", "nse-entry/entry.py", "nse-alerts/alerts.py", "nse-news/news.py",
                    "nse-timemachine/timemachine.py", "nse-broker/ticket.py", "nse-budget/size.py",
                    "nse-risk/check.py", "nse-backtest/backtest.py"):
            self.assertIn(block, read(ROOT, rel), rel)

    def test_no_hard_coded_yahoo_labels_left(self):
        """The strings the 29 Oct run printed for Shoonya data are gone as fixed text."""
        for rel, bad in (("nse-scanner/scan.py", r'5-minute bars, Yahoo\."'),
                         ("nse-scanner/scan.py", r"Yahoo spark, daily closes only"),
                         ("nse-scanner/scan.py", r'"source": "yahoo"'),
                         ("nse-entry/entry.py", r'"source": "yahoo"'),
                         ("nse-alerts/alerts.py", r'"source": "yahoo"'),
                         ("nse-broker/ticket.py", r'"Yahoo 5-minute bars since the entry"'),
                         ("nse-broker/ticket.py", r'"Yahoo 1-minute bars"')):
            self.assertIsNone(re.search(bad, read(ROOT, rel)), "%s: %s" % (rel, bad))


if __name__ == "__main__":
    unittest.main()
