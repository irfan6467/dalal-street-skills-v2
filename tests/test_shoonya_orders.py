"""Shoonya order path: the ticket format, the charges it refuses to guess, the order intent, and every gate
in front of `ticket.py send`. Offline: a fake Shoonya answers PlaceOrder."""
import glob
import json
import os
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import load  # noqa: E402
from test_nse_broker import PaperBase, run, reliance, NOW, ticket  # noqa: E402  (the same module run() drives)
MASTER = "Exchange,Token,LotSize,Symbol,TradingSymbol,Instrument,TickSize,\nNSE,2885,1,RELIANCE,RELIANCE-EQ,EQ,0.10,\n"
SHOONYA_CHARGES = {"brokerage_pct_mis": 0.0, "brokerage_min_mis": 0.0, "brokerage_cap_mis": 0.0,
                   "brokerage_pct_cnc": 0.0, "brokerage_min_cnc": 0.0, "brokerage_cap_cnc": 0.0,
                   "dp_charge_cnc_sell": 15.0}


class Renderer(unittest.TestCase):
    CFG = {"broker": "shoonya", "charges": SHOONYA_CHARGES}

    def test_limit_entry_is_lmt_with_algo_id(self):
        tool, p, notes = ticket.entry_payload(reliance(), dict(self.CFG, shoonya={"algo_id": "ALG123"}))
        self.assertEqual((p["prctyp"], p["prd"], p["trantype"], p["ret"], p["ordersource"]), ("LMT", "I", "B", "DAY", "API"))
        self.assertEqual((p["qty"], p["prc"], p["tsym"], p["algo_id"]), ("118", "1196.10", "RELIANCE-EQ", "ALG123"))
        self.assertNotIn("trgprc", p)

    def test_market_entry_is_refused(self):
        o = reliance()
        o["entry"] = dict(o["entry"], order_type="MARKET", price=None)
        tool, p, notes = ticket.render_leg(o, self.CFG, "entry")
        self.assertIsNone(p)
        self.assertIn("rejects MKT", notes[0])

    def test_stop_needs_the_users_buffer(self):
        tool, p, notes = ticket.render_leg(reliance(), self.CFG, "stop", 118)
        self.assertIsNone(p)
        self.assertIn("sl_limit_buffer_pct", notes[0])
        tool, p, notes = ticket.render_leg(reliance(), dict(self.CFG, shoonya={"sl_limit_buffer_pct": 0.5}), "stop", 118)
        self.assertEqual((p["prctyp"], p["trgprc"], p["prc"], p["trantype"]), ("SL-LMT", "1188.50", "1182.55", "S"))

    def test_shoonya_charges_are_not_guessed(self):
        with self.assertRaisesRegex(ticket.IndiaError, "not built in"):
            ticket.trade_charges(100000, 101000, "MIS", cfg={"broker": "shoonya"})
        c = ticket.trade_charges(100000, 101000, "MIS", cfg=self.CFG)
        self.assertEqual(c["brokerage"], 0.0)
        self.assertGreater(c["stt"], 0)


class FakeShoonya:
    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer or {"stat": "Ok", "norenordno": "26102600001234"}

    def __call__(self, url, body, headers):
        q = urllib.parse.parse_qs(body.decode())
        self.calls.append((url.rsplit("/", 1)[-1], json.loads(q["jData"][0]), q.get("jKey", [None])[0]))
        return json.dumps(self.answer)


class SendBase(PaperBase):
    def setUp(self):
        super().setUp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.makedirs("nse/cache/shoonya", exist_ok=True)
        with open("nse/cache/shoonya/NSE_symbols.txt", "w") as fh:
            fh.write(MASTER)
        self.feed = ticket._shoonya_feed()
        self.feed.SLEEP = lambda s: None
        self.feed.MIN_GAP_GLOBAL = self.feed.MIN_GAP_PER_SYM = 0
        self.feed.save_session("FA1234", "TOKEN")
        self.fake = FakeShoonya()
        self.feed.TRANSPORT = self.fake
        self.feed._MASTERS.clear()
        self.set_cfg(broker="shoonya", paper=False, charges=SHOONYA_CHARGES,
                     squareoff={"cas": "15:15", "other": "15:20"},
                     shoonya={"orders_enabled": True, "algo_id": "ALG123", "sl_limit_buffer_pct": 0.5})
        self.assertEqual(self.run_risk(), 0)

    def tearDown(self):
        self.feed.TRANSPORT = None
        os.chdir(self.cwd)
        super().tearDown()

    def approve(self, now=NOW):
        code, text, err = run(self.args("approve", "--index", "1", "--reply", "yes", now=now))
        self.assertEqual(code, 0, err)
        return text

    def the_code(self, leg="entry"):
        rec = [r for r in self.state_doc()["orders"] if r["status"] == "approved"][-1]
        return ticket.consent_code(rec["fingerprint"], rec["at"], leg)

    def send(self, *extra, now=NOW):
        return run(self.args("send", "--index", "1", *extra, now=now))


class Intents(SendBase):
    def test_approval_writes_a_neutral_intent_and_sends_nothing(self):
        text = self.approve()
        self.assertIn("Nothing has been sent", text)
        self.assertEqual(self.fake.calls, [])
        path = glob.glob("nse/intents/*.json")[0]
        with open(path) as fh:
            it = json.load(fh)
        self.assertEqual((it["schema"], it["mode"], it["broker"], it["side"], it["quantity"]),
                         ("dalal-street/order-intent", "live", "shoonya", "BUY", 118))
        self.assertEqual(it["risk_check"]["verdict"], "PASS")
        self.assertEqual(it["algo_id"], "ALG123")
        self.assertEqual(it["stop"]["trigger"], 1188.5)
        with open("nse/intents/intents.jsonl") as fh:
            self.assertEqual(len(fh.readlines()), 1)

    def test_intent_matches_the_schema_file(self):
        self.approve()
        from _load import ROOT
        with open(os.path.join(ROOT, "docs", "order-intent.schema.json")) as fh:
            schema = json.load(fh)
        with open(glob.glob("nse/intents/*.json")[0]) as fh:
            it = json.load(fh)
        for k in schema["required"]:
            self.assertIn(k, it)
        for k in it:
            self.assertIn(k, schema["properties"], "intent field %s missing from the schema" % k)


class Gates(SendBase):
    def assert_refused(self, why, *extra, now=NOW):
        code, text, err = self.send(*extra, now=now)
        self.assertEqual(code, 1, text)
        self.assertIn(why, err)
        self.assertEqual(self.fake.calls, [], "something was sent")

    def test_happy_path_with_the_right_code(self):
        self.approve()
        code, text, err = self.send("--code", self.the_code())
        self.assertEqual(code, 0, err)
        route, payload, jkey = self.fake.calls[0]
        self.assertEqual((route, jkey), ("PlaceOrder", "TOKEN"))
        self.assertEqual((payload["uid"], payload["actid"], payload["tsym"], payload["algo_id"], payload["prctyp"]),
                         ("FA1234", "FA1234", "RELIANCE-EQ", "ALG123", "LMT"))
        self.assertIn("26102600001234", text)
        placed = [r for r in self.state_doc()["orders"] if r["status"] == "placed"]
        self.assertEqual(placed[0]["broker_order_id"], "26102600001234")
        with open("nse/state/shoonya_audit.jsonl") as fh:
            a = json.loads(fh.readline())
        self.assertNotIn("TOKEN", json.dumps(a))       # the session token never lands in the audit log
        self.assertEqual(a["consent"], "terminal")

    def test_no_duplicate_send(self):
        self.approve()
        self.assertEqual(self.send("--code", self.the_code())[0], 0)
        self.fake.calls.clear()
        self.assert_refused("no yes recorded", "--code", self.the_code())

    def test_wrong_code(self):
        self.approve()
        self.assert_refused("did not match", "--code", "ABC123")

    def test_terminal_mode_will_not_run_without_a_human(self):
        self.approve()
        self.assert_refused("own terminal")              # tests have no tty and no --code

    def test_no_yes_no_send(self):
        self.assert_refused("no yes recorded", "--code", "XXXXXX")

    def test_stale_yes(self):
        self.approve()
        self.assert_refused("minutes old", "--code", self.the_code(), now="2026-10-26 09:55")

    def test_paper_mode(self):
        self.approve()
        self.set_cfg(paper=True)
        self.assert_refused("paper mode", "--code", self.the_code())

    def test_orders_off_by_default(self):
        self.approve()
        self.set_cfg(shoonya={"algo_id": "ALG123"})
        self.assert_refused("orders are off", "--code", self.the_code())

    def test_algo_id_required(self):
        self.approve()
        self.set_cfg(shoonya={"orders_enabled": True})
        self.assert_refused("Algo ID", "--code", self.the_code())

    def test_kill_switch(self):
        self.approve()
        open("nse/KILL", "w").close()
        self.assert_refused("kill switch", "--code", self.the_code())

    def test_chat_mode_needs_the_users_own_words(self):
        self.set_cfg(shoonya={"orders_enabled": True, "algo_id": "ALG123", "consent": "chat"})
        self.approve()
        c = self.the_code()
        self.assert_refused("SEND %s" % c, "--reply", "yes go")
        self.assert_refused("SEND %s" % c, "--reply", "SEND %s" % c)          # symbol missing
        code, text, err = self.send("--reply", "SEND %s RELIANCE" % c)
        self.assertEqual(code, 0, err)

    def test_rejection_is_recorded_as_failed(self):
        self.fake.answer = {"stat": "Not_Ok", "emsg": "RMS:Margin Exceeds"}
        self.approve()
        code, text, err = self.send("--code", self.the_code())
        self.assertEqual(code, 1)
        self.assertIn("REJECTED", text)
        failed = [r for r in self.state_doc()["orders"] if r["status"] == "failed"]
        self.assertIn("Margin", failed[0]["note"])

    def test_stop_leg_only_after_a_placed_entry(self):
        self.approve()
        self.assert_refused("follows a placed entry", "--leg", "stop", "--code", "X")
        self.assertEqual(self.send("--code", self.the_code())[0], 0)
        self.fake.calls.clear()
        rec = [r for r in self.state_doc()["orders"] if r["status"] == "placed"][-1]
        code, text, err = self.send("--leg", "stop", "--qty", "118", "--code",
                                    ticket.consent_code(rec["fingerprint"], rec["at"], "stop"))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.fake.calls[-1][1]["prctyp"], "SL-LMT")
        self.assertEqual(self.fake.calls[-1][1]["trantype"], "S")

    def test_dry_run_sends_nothing(self):
        self.approve()
        code, text, err = self.send("--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("Consent code", text)
        self.assertEqual(self.fake.calls, [])


if __name__ == "__main__":
    unittest.main()
