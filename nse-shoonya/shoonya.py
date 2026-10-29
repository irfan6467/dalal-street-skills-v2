#!/usr/bin/env python3
"""nse-shoonya: connect the dalal-street skills to Shoonya (Finvasia) live data.

  python3 shoonya.py login                 OAuth login, in YOUR terminal (reads secrets from env)
  python3 shoonya.py login --code URL      finish a login with the pasted redirect URL / code
  python3 shoonya.py status                session, provider and config in one screen
  python3 shoonya.py use shoonya|yahoo     pick the data provider for every skill
  python3 shoonya.py selftest              live check: quote, 5m bars, daily bars, schema, auth mode
  python3 shoonya.py quote RELIANCE        last price, previous close, circuit band
  python3 shoonya.py bars RELIANCE --interval 5m --range 1d

Secrets: SHOONYA_UID, SHOONYA_CLIENT_ID, SHOONYA_SECRET in the environment. Never paste them into chat.
Standard library only.
"""

import argparse
import datetime
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_feed():
    path = os.path.join(HERE, "shoonya_feed.py")
    if not os.path.exists(path):
        path = os.path.join(os.path.dirname(HERE), "_shared", "shoonya_feed.py")
    spec = importlib.util.spec_from_file_location("shoonya_feed_cli", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


F = _load_feed()
Err = F.ShoonyaError


def env(name, required=True):
    v = (os.environ.get(name) or "").strip()
    if required and not v:
        raise Err("%s is not set in the environment. Set it in your shell (never in chat), for example:\n"
                  "  export %s=...   (Windows: set %s=...)" % (name, name, name))
    return v


def ysym(text):
    t = str(text).strip().upper()
    if t in F.INDEX_TOKENS or t.endswith((".NS", ".BO")):
        return t
    if t.startswith("BSE:"):
        return t[4:] + ".BO"
    if t.startswith("NSE:"):
        t = t[4:]
    return {"NIFTY": "^NSEI", "NIFTY50": "^NSEI", "BANKNIFTY": "^NSEBANK", "FINNIFTY": "^CNXFIN",
            "VIX": "^INDIAVIX", "INDIAVIX": "^INDIAVIX"}.get(t, t + ".NS")


def read_cfg():
    return F._read_json(F.CONFIG_PATH)


def write_cfg(cfg):
    os.makedirs(os.path.dirname(F.CONFIG_PATH), exist_ok=True)
    with open(F.CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


def set_data(**kv):
    cfg = read_cfg()
    d = dict(cfg.get("data") or {})
    d.update(kv)
    cfg["data"] = d
    write_cfg(cfg)
    return d


# ---------------------------------------------------------------- commands

def cmd_login(a):
    uid = env("SHOONYA_UID")
    cid = env("SHOONYA_CLIENT_ID")
    sec = env("SHOONYA_SECRET")
    code = a.code
    if not code:
        print("1. Open this URL and log in with your Shoonya id, password and TOTP:\n\n   %s\n" % F.authorize_url(cid))
        print("2. The browser is sent to a 127.0.0.1 address that will not load. That is expected.")
        print("   Copy the whole address from the address bar (it contains ?code=...).")
        print("   The code works once, for about 2 minutes.\n")
        if not sys.stdin.isatty():
            print("Then run:  python3 shoonya.py login --code '<the address>'")
            return 0
        code = input("3. Paste the address here: ").strip()
    doc, tok = F.exchange_code(code, cid, sec, uid=uid)
    F.save_session(doc.get("actid") or doc.get("USERID") or uid, tok,
                   {"name": doc.get("uname"), "exchanges": doc.get("exarr")})
    print("Logged in as %s. Session saved to %s (owner-only), valid today."
          % (doc.get("uname") or uid, F.SESSION_PATH))
    print("Next: python3 shoonya.py selftest")
    return 0


def cmd_status(a):
    print("Data provider : %s%s" % (F.provider(), "  (Yahoo fallback allowed)" if F.allow_fallback() else ""))
    print("Auth mode     : %s" % F.auth_mode())
    try:
        s = F.load_session()
        print("Session       : %s, issued %s IST date %s" % (s.get("uid"), s.get("issued_utc"), s.get("issued_ist_date")))
    except Err as e:
        print("Session       : none (%s)" % e)
    for n in ("SHOONYA_UID", "SHOONYA_CLIENT_ID", "SHOONYA_SECRET"):
        print("%-14s: %s" % (n, "set" if os.environ.get(n) else "not set"))
    st = read_cfg().get("selftest")
    if st:
        print("Last selftest : %s, %s" % (st.get("at"), "PASS" if st.get("ok") else "FAIL"))
    return 0


def cmd_use(a):
    d = set_data(provider=a.provider, allow_yahoo_fallback=bool(a.allow_fallback))
    print("Every skill now reads prices from %s%s." % (
        a.provider, " (Yahoo only if Shoonya fails)" if d["allow_yahoo_fallback"] else ""))
    if a.provider == "shoonya":
        print("Run selftest once so the schema and auth mode are confirmed on your account.")
    return 0


def check_bars(bars, what):
    issues = []
    if not bars:
        return ["%s: no bars" % what]
    for i, b in enumerate(bars):
        if not (b["l"] <= min(b["o"], b["c"]) and b["h"] >= max(b["o"], b["c"])):
            issues.append("%s bar %d: OHLC not consistent (%s)" % (what, i, b))
            break
    ts = [b["t"] for b in bars]
    if ts != sorted(ts) or len(set(ts)) != len(ts):
        issues.append("%s: timestamps not strictly ascending" % what)
    return issues


def cmd_selftest(a):
    """Live, read-only. Tries the configured auth mode, then the other one, and records which works."""
    sym = ysym(a.symbol)
    report = {"at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(), "symbol": sym}
    s = F.load_session()
    modes = [F.auth_mode()] + [m for m in ("jkey", "bearer") if m != F.auth_mode()]
    exch, token, tsym, tick = F.resolve(sym)
    print("Symbol master : %s -> %s token %s, tick %s" % (sym, tsym, token, tick))
    q, used = None, None
    for m in modes:
        try:
            q = F.call("GetQuotes", {"uid": s["uid"], "exch": exch, "token": token}, mode=m,
                       throttle_key=(exch, token, "st"))
            used = m
            break
        except Err as e:
            print("Auth %-6s   : failed (%s)" % (m, e))
    if q is None:
        report["ok"] = False
        set_data(selftest=None)
        raise Err("no auth mode worked: log in again, and check the IP whitelist for your API app")
    if used != F.auth_mode():
        set_data(auth=used)
    print("Auth mode     : %s works" % used)
    report["auth"] = used
    report["quote_fields"] = sorted(q) if isinstance(q, dict) else str(type(q))
    print("Quote fields  : %s" % ", ".join(report["quote_fields"]))
    print("Quote         : last %s, prev close %s, band %s - %s" % (q.get("lp"), q.get("c"), q.get("lc"), q.get("uc")))
    issues = []
    _e, _t, _s, intra = F.fetch_intraday(sym, "5m", 2)
    issues += check_bars(intra, "5m")
    print("5m bars       : %d over %d sessions, last %s IST" % (
        len(intra), len({F._ist_date(b["t"]) for b in intra}),
        datetime.datetime.fromtimestamp(intra[-1]["t"], F.IST).strftime("%d-%b %H:%M") if intra else "-"))
    _e, _t, _s, daily = F.fetch_daily(sym, 30)
    issues += check_bars(daily, "daily")
    print("Daily bars    : %d, last %s" % (len(daily), F._ist_date(daily[-1]["t"]) if daily else "-"))
    if intra and daily and F._ist_date(intra[-1]["t"]) == F._ist_date(daily[-1]["t"]):
        dh = max(b["h"] for b in intra if F._ist_date(b["t"]) == F._ist_date(intra[-1]["t"]))
        if abs(dh - daily[-1]["h"]) / daily[-1]["h"] > 0.002:
            issues.append("today's 5m high %.2f vs daily high %.2f differ by more than 0.2%%" % (dh, daily[-1]["h"]))
    if intra:
        vols = [b["v"] for b in intra]
        if len(vols) > 3 and vols == sorted(vols) and vols[-1] > 10 * max(1, vols[0]):
            issues.append("5m volume only rises: the feed may send cumulative volume; check 'intv' vs 'v'")
    report["issues"] = issues
    report["ok"] = not issues
    set_data(selftest=report)
    for i in issues:
        print("ISSUE         : %s" % i)
    print("Selftest      : %s" % ("PASS" if not issues else "FAIL (see issues; keep provider on yahoo until fixed)"))
    return 0 if not issues else 2


def cmd_quote(a):
    q = F.quote(ysym(a.symbol))
    keys = ("tsym", "lp", "c", "o", "h", "l", "v", "lc", "uc", "ap", "bp1", "sp1")
    print(json.dumps({k: q.get(k) for k in keys if k in q} if not a.raw else q, indent=2))
    return 0


def cmd_bars(a):
    doc = F.answer_chart(ysym(a.symbol), a.range, a.interval)
    if doc is None:
        raise Err("Shoonya has no %s bars for range %s" % (a.interval, a.range))
    r = doc["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    rows = list(zip(r["timestamp"], q["open"], q["high"], q["low"], q["close"], q["volume"]))
    if a.json:
        print(json.dumps(doc))
        return 0
    for t, o, h, lo, c, v in rows[-a.last:]:
        print("%s  O %9.2f  H %9.2f  L %9.2f  C %9.2f  V %d" % (
            datetime.datetime.fromtimestamp(t, F.IST).strftime("%Y-%m-%d %H:%M"), o, h, lo, c, v))
    print("(%d bars; previous close %s)" % (len(rows), r["meta"].get("previousClose")))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="shoonya.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("login")
    s.add_argument("--code", help="the redirect URL or code, if you already have it")
    sub.add_parser("status")
    s = sub.add_parser("use")
    s.add_argument("provider", choices=("shoonya", "yahoo"))
    s.add_argument("--allow-fallback", action="store_true",
                   help="if Shoonya fails, use Yahoo (delayed, unofficial) instead of stopping")
    s = sub.add_parser("selftest")
    s.add_argument("--symbol", default="RELIANCE")
    s = sub.add_parser("quote")
    s.add_argument("symbol")
    s.add_argument("--raw", action="store_true")
    s = sub.add_parser("bars")
    s.add_argument("symbol")
    s.add_argument("--interval", default="5m")
    s.add_argument("--range", default="1d")
    s.add_argument("--last", type=int, default=20)
    s.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    try:
        return {"login": cmd_login, "status": cmd_status, "use": cmd_use, "selftest": cmd_selftest,
                "quote": cmd_quote, "bars": cmd_bars}[a.cmd](a)
    except Err as e:
        print("nse-shoonya: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
