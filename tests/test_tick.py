"""Per-stock tick sizes: a price off the stock's tick is rejected by the exchange (RELIANCE is 0.10)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import load  # noqa: E402

exit_ = load("nse-exit", "exit")
size = load("nse-budget", "size")
check = load("nse-risk", "check")
ticket = load("nse-broker", "ticket")

MASTER = ("Exchange,Token,LotSize,Symbol,TradingSymbol,Instrument,TickSize,\n"
          "NSE,2885,1,RELIANCE,RELIANCE-EQ,EQ,0.10,\nNSE,11536,1,TCS,TCS-EQ,EQ,0.05,\n")


class Ticks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        os.makedirs("nse/cache/shoonya")
        with open("nse/cache/shoonya/NSE_symbols.txt", "w") as fh:
            fh.write(MASTER)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_tick_from_config_then_master_then_default(self):
        for mod in (exit_, size, check, ticket):
            self.assertEqual(mod.symbol_tick("RELIANCE", "NSE", {}), 0.10, mod.__name__)
            self.assertEqual(mod.symbol_tick("TCS", "NSE", {}), 0.05)
            self.assertEqual(mod.symbol_tick("INFY", "NSE", {}), 0.05)            # not in master: fallback
            self.assertEqual(mod.symbol_tick("RELIANCE", "NSE", {"ticks": {"RELIANCE": 1.0}}), 1.0)

    def test_on_tick(self):
        self.assertTrue(check.on_tick(1188.6, 0.10))
        self.assertFalse(check.on_tick(1188.55, 0.10))
        self.assertTrue(check.on_tick(1188.55, 0.05))

    def test_shoonya_stop_limit_lands_on_the_stocks_tick(self):
        cfg = {"broker": "shoonya", "shoonya": {"sl_limit_buffer_pct": 0.5}}
        o = {"symbol": "RELIANCE", "exchange": "NSE", "product": "MIS", "side": "BUY", "qty": 10,
             "order_type": "LIMIT", "price": 1196.1, "stop_trigger": 1188.6, "target_price": 1211.3,
             "entry": {"exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "BUY", "quantity": 10,
                       "product": "MIS", "order_type": "LIMIT", "price": 1196.1},
             "stop": {"exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "SELL", "quantity": 10,
                      "product": "MIS", "order_type": "SL-M", "trigger_price": 1188.6}}
        _t, p, _n = ticket.render_leg(o, cfg, "stop", 10)
        self.assertTrue(check.on_tick(float(p["prc"]), 0.10), p["prc"])            # 1182.6, not 1182.65

    def test_exit_rounds_stop_to_ten_paise(self):
        exit_.DTICK = exit_.Decimal("0.10")
        try:
            self.assertEqual(float(exit_.tick_floor(1183.65)), 1183.6)
            self.assertEqual(float(exit_.tick_ceil(1193.61)), 1193.7)
        finally:
            exit_.DTICK = exit_.Decimal("0.05")


if __name__ == "__main__":
    unittest.main()
