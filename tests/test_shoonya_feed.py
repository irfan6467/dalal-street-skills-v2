"""Shoonya feed: auth, symbol master, bar parsing, Yahoo-shape output, the hook in every skill.
Offline: a fake transport stands in for Shoonya."""
import datetime
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import ROOT, load  # noqa: E402

FEED = os.path.join(ROOT, "_shared", "shoonya_feed.py")

MASTER = ("Exchange,Token,LotSize,Symbol,TradingSymbol,Instrument,TickSize,\n"
          "NSE,2885,1,RELIANCE,RELIANCE-EQ,EQ,0.10,\n"
          "NSE,11536,1,TCS,TCS-EQ,EQ,0.10,\n"
          "NSE,99999,1,TINYCO,TINYCO-BE,EQ,0.01,\n")
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def feed_module():
    spec = importlib.util.spec_from_file_location("feed_under_test_%d" % id(object()), FEED)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.SLEEP = lambda s: None
    m.MIN_GAP_GLOBAL = 0
    m.MIN_GAP_PER_SYM = 0
    return m


def tp_rows(day, n=6, start_price=1000.0, newest_first=True):
    rows = []
    for i in range(n):
        t = datetime.datetime.combine(day, datetime.time(9, 15)) + datetime.timedelta(minutes=5 * i)
        p = start_price + i
        rows.append({"stat": "Ok", "time": t.strftime("%d-%m-%Y %H:%M:%S"), "into": "%.2f" % p,
                     "inth": "%.2f" % (p + 2), "intl": "%.2f" % (p - 1), "intc": "%.2f" % (p + 1),
                     "intv": str(100 + i), "v": str(1000 * (i + 1)), "oi": "0"})
    return list(reversed(rows)) if newest_first else rows


def eod_rows(days, price=990.0):
    return [{"time": d.strftime("%d-%b-%Y").upper(), "into": "%.2f" % price, "inth": "%.2f" % (price + 20),
             "intl": "%.2f" % (price - 10), "intc": "%.2f" % (price + 5 + k), "v": "500000", "oi": "0"}
            for k, d in enumerate(days)]


class FakeShoonya:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, body, headers):
        route = url.rsplit("/", 1)[-1]
        self.calls.append((route, body, headers))
        h = self.routes.get(route)
        if h is None:
            return json.dumps({"stat": "Not_Ok", "emsg": "no route %s" % route})
        return json.dumps(h(body, headers) if callable(h) else h)


def decode(body, headers):
    """Both modes send the SDK's form body jData=<raw JSON>; Bearer mode carries the token in a header."""
    text = body.decode()
    assert text.startswith("jData="), text
    js, _, rest = text[len("jData="):].partition("&jKey=")
    if headers.get("Authorization"):
        return json.loads(js), headers["Authorization"]
    return json.loads(js), (rest or None)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        os.makedirs("nse/cache/shoonya", exist_ok=True)
        with open("nse/cache/shoonya/NSE_symbols.txt", "w") as fh:
            fh.write(MASTER)
        self.F = feed_module()
        self.F.NOW = datetime.datetime(2026, 10, 28, 6, 0, tzinfo=datetime.timezone.utc)   # 11:30 IST Wed
        self.saved_env = {k: os.environ.pop(k) for k in ("NSE_DATA", "NSE_DATA_FALLBACK") if k in os.environ}

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()
        os.environ.update(self.saved_env)

    def login(self, uid="FA1234"):
        self.F.save_session(uid, "TOKEN123")

    def use_shoonya(self, fallback=False):
        with open("nse/config.json", "w") as fh:
            json.dump({"data": {"provider": "shoonya", "allow_yahoo_fallback": fallback}}, fh)


class TestAuth(Base):
    def test_checksum_is_sha256_of_key_secret_code_trimmed(self):
        want = hashlib.sha256(b"APPKEYSECRETabc123").hexdigest()
        self.assertEqual(self.F.checksum(" APPKEY", "SECRET ", "abc123\n"), want)

    def test_code_from_redirect(self):
        self.assertEqual(self.F.code_from_redirect("https://127.0.0.1/?code=XyZ-9.8&state=1"), "XyZ-9.8")
        self.assertEqual(self.F.code_from_redirect("XyZ98"), "XyZ98")
        with self.assertRaises(self.F.ShoonyaError):
            self.F.code_from_redirect("not a code !!")

    def test_exchange_code_posts_checksum_and_saves_nothing_itself(self):
        fake = FakeShoonya({"GenAcsTok": {"stat": "Ok", "susertoken": "SESS", "actid": "FA1", "uname": "IRFAN"}})
        self.F.TRANSPORT = fake
        doc, tok = self.F.exchange_code("https://127.0.0.1/?code=C0DE", "APP", "SEC", uid="FA1")
        self.assertEqual(tok, "SESS")
        sent, key = decode(fake.calls[0][1], fake.calls[0][2])
        self.assertIsNone(key)                       # no session token on the token exchange
        self.assertEqual(sent["checksum"], hashlib.sha256(b"APPSECC0DE").hexdigest())
        self.assertEqual(sent["code"], "C0DE")

    def test_session_file_is_owner_only_and_expires_next_day(self):
        self.login()
        if os.name == "posix":
            self.assertEqual(os.stat(self.F.SESSION_PATH).st_mode & 0o777, 0o600)
        self.assertEqual(self.F.load_session()["token"], "TOKEN123")
        self.F.NOW = self.F.NOW + datetime.timedelta(days=1)
        with self.assertRaisesRegex(self.F.ShoonyaError, "one trading day"):
            self.F.load_session()

    def test_not_logged_in_says_how(self):
        with self.assertRaisesRegex(self.F.ShoonyaError, "login"):
            self.F.load_session()

    def test_jkey_and_bearer_transports(self):
        self.login()
        fake = FakeShoonya({"GetQuotes": {"stat": "Ok", "lp": "1"}})
        self.F.TRANSPORT = fake
        self.F.call("GetQuotes", {"uid": "FA1234"}, mode="jkey")
        sent, key = decode(fake.calls[-1][1], fake.calls[-1][2])
        self.assertEqual(key, "TOKEN123")
        self.F.call("GetQuotes", {"uid": "FA1234"}, mode="bearer")
        sent, auth = decode(fake.calls[-1][1], fake.calls[-1][2])
        self.assertEqual(auth, "Bearer TOKEN123")
        self.F.save_session("FA1234", "SUSER", {"access_token": "ACCESS"})
        self.F.call("GetQuotes", {"uid": "FA1234"}, mode="bearer")
        self.assertEqual(decode(fake.calls[-1][1], fake.calls[-1][2])[1], "Bearer ACCESS")   # OAuth access token

    def test_rejected_session_tells_user_to_log_in(self):
        self.login()
        self.F.TRANSPORT = FakeShoonya({"GetQuotes": {"stat": "Not_Ok", "emsg": "Session Expired :  Invalid Session Key"}})
        with self.assertRaisesRegex(self.F.ShoonyaError, "login again"):
            self.F.call("GetQuotes", {})


class TestMaster(Base):
    def test_parse_master_by_header_any_separator(self):
        rows = self.F.parse_master(MASTER.replace(",", "|"))
        self.assertEqual(rows[0]["tradingsymbol"], "RELIANCE-EQ")
        self.assertEqual(rows[0]["token"], "2885")

    def test_resolve_eq_be_and_index(self):
        self.assertEqual(self.F.resolve("RELIANCE.NS")[:3], ("NSE", "2885", "RELIANCE-EQ"))
        self.assertEqual(self.F.resolve("TINYCO.NS")[2], "TINYCO-BE")
        self.assertEqual(self.F.resolve("^NSEI")[:2], ("NSE", "26000"))
        with self.assertRaises(self.F.ShoonyaError):
            self.F.resolve("NOPE.NS")
        with self.assertRaises(self.F.ShoonyaError):
            self.F.resolve("AAPL")

    def test_master_zip_download_is_cached(self):
        os.remove("nse/cache/shoonya/NSE_symbols.txt")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("NSE_symbols.txt", MASTER)
        got = []
        idx = self.F.load_master("NSE", fetch=lambda url: got.append(url) or buf.getvalue())
        self.assertIn("RELIANCE-EQ", idx)
        self.assertEqual(len(got), 1)
        self.F.load_master("NSE", fetch=lambda url: got.append(url) or buf.getvalue())
        self.assertEqual(len(got), 1)                # second load from the day's cache


class TestBars(Base):
    def test_rows_newest_first_become_ascending_with_per_bar_volume(self):
        bars = self.F.bars_from_rows(tp_rows(datetime.date(2026, 10, 28)))
        self.assertEqual([b["t"] for b in bars], sorted(b["t"] for b in bars))
        self.assertEqual(bars[0]["v"], 100)          # intv, not the cumulative v
        t0 = datetime.datetime.fromtimestamp(bars[0]["t"], IST)
        self.assertEqual((t0.hour, t0.minute), (9, 15))

    def test_daily_rows_stamped_0915_ist_and_epoch_rows(self):
        b = self.F.bars_from_rows([{"time": "05-DEC-2022", "into": "1", "inth": "2", "intl": "0.5", "intc": "1.5",
                                    "v": "9"}], daily=True)[0]
        d = datetime.datetime.fromtimestamp(b["t"], IST)
        self.assertEqual((d.date(), d.hour, d.minute), (datetime.date(2022, 12, 5), 9, 15))
        e = self.F.bars_from_rows([{"ssboe": "1761637500", "intc": "10"}])[0]
        self.assertEqual(e["t"], 1761637500)
        self.assertEqual(e["o"], 10.0)

    def test_bad_rows_skipped_duplicates_collapse(self):
        rows = tp_rows(datetime.date(2026, 10, 28), 3, newest_first=False)
        rows.append(dict(rows[-1]))
        rows.append({"stat": "Not_Ok"})
        rows.append("garbage")
        self.assertEqual(len(self.F.bars_from_rows(rows)), 3)

    def test_keep_sessions(self):
        rows = tp_rows(datetime.date(2026, 10, 26), 3) + tp_rows(datetime.date(2026, 10, 27), 3) + \
            tp_rows(datetime.date(2026, 10, 28), 3)
        bars = self.F.keep_sessions(self.F.bars_from_rows(rows), 2)
        self.assertEqual(len({self.F._ist_date(b["t"]) for b in bars}), 2)


def routes_for_reliance():
    days = [datetime.date(2026, 10, d) for d in (20, 21, 23, 26, 27, 28)]
    return {
        "TPSeries": lambda body, h: tp_rows(datetime.date(2026, 10, 27), 6, 1000) + tp_rows(datetime.date(2026, 10, 28), 6, 1010),
        "EODChartData": lambda body, h: eod_rows(days),
        "GetQuotes": {"stat": "Ok", "tsym": "RELIANCE-EQ", "lp": "1015.00", "c": "1004.00", "lc": "903.60", "uc": "1104.40"},
    }


class TestYahooShape(Base):
    def setUp(self):
        super().setUp()
        self.login()
        self.fake = FakeShoonya(routes_for_reliance())
        self.F.TRANSPORT = self.fake

    def test_intraday_chart_has_yahoo_fields_and_prev_close_from_daily(self):
        doc = self.F.answer_chart("RELIANCE.NS", "1d", "5m")
        r = doc["chart"]["result"][0]
        self.assertEqual(len(r["timestamp"]), 6)     # only the last session
        self.assertEqual(r["meta"]["gmtoffset"], 19800)
        self.assertEqual(r["meta"]["dataSource"], "shoonya")
        self.assertEqual(r["meta"]["previousClose"], 999.0)   # close of the 27 Oct daily bar (990+5+4)
        sent, _ = decode(self.fake.calls[0][1], self.fake.calls[0][2])
        self.assertEqual(sent["intrv"], "5")
        self.assertEqual(sent["token"], "2885")

    def test_daily_chart_and_interval_not_offered(self):
        doc = self.F.answer_chart("RELIANCE.NS", "5d", "1d")
        self.assertEqual(len(doc["chart"]["result"][0]["timestamp"]), 5)
        self.assertIsNone(self.F.answer_chart("RELIANCE.NS", "1d", "2m"))

    def test_entry_skill_parses_shoonya_chart(self):
        entry = load("nse-entry", "entry")
        got = entry.parse_chart(json.dumps(self.F.answer_chart("RELIANCE.NS", "1d", "5m")), "RELIANCE.NS")
        self.assertEqual(len(got["bars"]), 6)
        self.assertEqual(got["prev_close"], 999.0)

    def test_scanner_parses_shoonya_spark(self):
        scan = load("nse-scanner", "scan")
        doc = self.F.answer_spark(["RELIANCE.NS", "NOPE.NS"], "5d", "1d")
        got = scan.parse_spark(json.dumps(doc))
        self.assertIn("RELIANCE.NS", got)
        self.assertNotIn("NOPE.NS", got)             # unknown symbols missing, as Yahoo does


class TestHook(Base):
    def setUp(self):
        super().setUp()
        self.login()
        self.F.TRANSPORT = FakeShoonya(routes_for_reliance())

    def test_yahoo_provider_passes_through(self):
        self.assertIsNone(self.F.yahoo_hook("/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m"))

    def test_shoonya_provider_answers_chart_not_fundamentals(self):
        self.use_shoonya()
        txt = self.F.yahoo_hook("/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m")
        self.assertEqual(json.loads(txt)["chart"]["result"][0]["meta"]["symbol"], "RELIANCE.NS")
        self.assertIsNone(self.F.yahoo_hook("/ws/fundamentals-timeseries/v1/finance/timeseries/TCS.NS?type=x"))
        self.assertIsNone(self.F.yahoo_hook("/v8/finance/chart/USDINR=X?range=5y&interval=1d"))

    def test_failure_raises_unless_fallback(self):
        self.use_shoonya()
        self.F.TRANSPORT = FakeShoonya({})
        with self.assertRaises(self.F.ShoonyaError):
            self.F.yahoo_hook("/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m")
        self.use_shoonya(fallback=True)
        self.assertIsNone(self.F.yahoo_hook("/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m"))

    def test_env_overrides_config(self):
        self.use_shoonya()
        os.environ["NSE_DATA"] = "yahoo"
        try:
            self.assertIsNone(self.F.yahoo_hook("/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m"))
        finally:
            del os.environ["NSE_DATA"]


class TestEverySkillHasTheHook(unittest.TestCase):
    def test_all_skills_route_yahoo_get_through_the_feed(self):
        import glob
        scripts = [p for p in glob.glob(os.path.join(ROOT, "nse-*", "*.py"))
                   if not p.endswith(("shoonya_feed.py", "shoonya.py"))]
        for p in scripts:
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
            if "def _yahoo_http(path):" not in text:
                continue
            self.assertIn("feed.yahoo_hook(path)", text, p)
            folder = os.path.dirname(p)
            self.assertTrue(os.path.exists(os.path.join(folder, "shoonya_feed.py")), folder)
            with open(os.path.join(folder, "shoonya_feed.py"), encoding="utf-8") as a, open(FEED, encoding="utf-8") as b:
                self.assertEqual(a.read(), b.read(), "stale copy in %s: run _shared/build/build.py" % folder)

    def test_a_skill_turns_shoonya_errors_into_its_own(self):
        tmp = tempfile.TemporaryDirectory()
        cwd = os.getcwd()
        os.chdir(tmp.name)
        try:
            os.makedirs("nse")
            with open("nse/config.json", "w") as fh:
                json.dump({"data": {"provider": "shoonya"}}, fh)
            entry = load("nse-entry", "entry")
            with self.assertRaisesRegex(entry.IndiaError, "not logged in"):
                entry.yahoo_get("/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m")
        finally:
            os.chdir(cwd)
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
