"""Shoonya (Finvasia) market data for the dalal-street skills.

Every skill fetches prices through one function, yahoo_get(path). When the data
provider is Shoonya, yahoo_hook(path) answers the chart and spark paths from
Shoonya's own API (live, exchange-sourced) and returns JSON in the exact shape
Yahoo would, so no skill's parser changes. Anything Shoonya cannot answer
(fundamentals, FX) returns None and the skill uses Yahoo as before.

Plain Python 3.8+, standard library only. This file is copied verbatim into
every skill folder by _shared/build/build.py; edit it here, never the copies.

Provider selection, first match wins:
  env NSE_DATA=shoonya|yahoo
  nse/config.json  {"data": {"provider": "shoonya", "allow_yahoo_fallback": false}}
  default: yahoo (the skills keep working with no account at all)

Secrets never go in chat or in config files. They come from the environment:
  SHOONYA_UID        your Shoonya client id (also the user id)
  SHOONYA_CLIENT_ID  the API app key (OAuth client id)
  SHOONYA_SECRET     the API secret
The session token from /nse-shoonya login is kept in nse/shoonya_session.json
(owner-only permissions), valid for the trading day.

API facts used here, from shoonya.com/api-documentation (read 2026-10-29) and
the official NorenRestApi SDK: REST base https://api.shoonya.com/NorenWClientAPI/,
OAuth token exchange at GenAcsTok with checksum sha256(app_key+secret+code),
requests as form body jData=<json>&jKey=<token> (the docs also show an
Authorization: Bearer form; "auth" picks one and selftest finds the right one),
TPSeries intervals 1,3,5,10,15,30,60,120,240 minutes, EODChartData for daily
bars, market data about 1 request per second per instrument. Shoonya marks
its own response schemas as not yet verified against a live capture, so the
parsers below accept every field spelling seen in the SDK and the docs, and
`selftest` prints what a live account actually returns.
"""

import datetime
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

BASE = "https://api.shoonya.com/NorenWClientAPI/"
OAUTH_AUTHORIZE = "https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=%s"
MASTER_URLS = {"NSE": "https://api.shoonya.com/NSE_symbols.txt.zip",
               "BSE": "https://api.shoonya.com/BSE_symbols.txt.zip"}
SESSION_PATH = os.path.join("nse", "shoonya_session.json")
CONFIG_PATH = os.path.join("nse", "config.json")
CACHE_DIR = os.path.join("nse", "cache", "shoonya")
UA = "dalal-street-skills/2.0 (shoonya feed)"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

TP_INTERVALS = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "60m": 60, "1h": 60,
                "120m": 120, "2h": 120, "240m": 240, "4h": 240}
DAILY_INTERVALS = ("1d",)
# Yahoo-style ranges -> trading sessions wanted. "max" is capped: Shoonya's
# EOD history depth is not documented.
RANGE_SESSIONS = {"1d": 1, "2d": 2, "5d": 5, "7d": 7, "10d": 10, "1mo": 22, "30d": 22, "60d": 60, "3mo": 63,
                  "6mo": 126, "1y": 250, "2y": 500, "5y": 1250, "10y": 2500, "ytd": 250, "max": 2500}
# Index tokens on NSE (widely published constants in Noren's NSE master).
INDEX_TOKENS = {"^NSEI": ("NSE", "26000", "Nifty 50"), "^NSEBANK": ("NSE", "26009", "Nifty Bank"),
                "^CNXFIN": ("NSE", "26037", "Nifty Fin Service"), "^INDIAVIX": ("NSE", "26017", "India VIX")}

MIN_GAP_GLOBAL = 0.25     # seconds between any two REST calls
MIN_GAP_PER_SYM = 1.0     # Shoonya: about 1 market-data request per second per instrument
_last_call = [0.0]
_last_sym = {}
SLEEP = time.sleep        # tests replace this
NOW = None                # tests set a fixed UTC datetime


class ShoonyaError(Exception):
    pass


# ---------------------------------------------------------------- config

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def data_cfg():
    return (_read_json(CONFIG_PATH).get("data") or {}) if os.path.exists(CONFIG_PATH) else {}


def provider():
    env = (os.environ.get("NSE_DATA") or "").strip().lower()
    if env in ("shoonya", "yahoo"):
        return env
    return str(data_cfg().get("provider") or "yahoo").strip().lower()


def allow_fallback():
    env = (os.environ.get("NSE_DATA_FALLBACK") or "").strip().lower()
    if env in ("1", "true", "yes", "yahoo"):
        return True
    return bool(data_cfg().get("allow_yahoo_fallback", False))


def auth_mode():
    return str(data_cfg().get("auth") or "jkey").strip().lower()


def now_utc():
    return NOW or datetime.datetime.now(datetime.timezone.utc)


# ---------------------------------------------------------------- session

def load_session():
    s = _read_json(SESSION_PATH)
    if not s.get("token") or not s.get("uid"):
        raise ShoonyaError("not logged in to Shoonya: run /nse-shoonya login in your own terminal "
                           "(the token is never typed into chat)")
    issued = s.get("issued_ist_date")
    today = now_utc().astimezone(IST).date().isoformat()
    if issued and issued != today:
        raise ShoonyaError("the Shoonya session is from %s; sessions last one trading day: run "
                           "/nse-shoonya login again" % issued)
    return s


def save_session(uid, token, extra=None):
    os.makedirs(os.path.dirname(SESSION_PATH), exist_ok=True)
    doc = {"uid": uid, "token": token,
           "issued_ist_date": now_utc().astimezone(IST).date().isoformat(),
           "issued_utc": now_utc().replace(microsecond=0).isoformat()}
    doc.update(extra or {})
    fd = os.open(SESSION_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    try:
        os.chmod(SESSION_PATH, 0o600)
    except OSError:
        pass
    return doc


def checksum(app_key, secret, code):
    """Shoonya OAuth: sha256(app_key + secret + auth_code), lowercase hex, values trimmed."""
    raw = "%s%s%s" % (str(app_key).strip(), str(secret).strip(), str(code).strip())
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def authorize_url(client_id):
    return OAUTH_AUTHORIZE % urllib.parse.quote(str(client_id).strip(), safe="")


def code_from_redirect(text):
    """The auth code from a pasted redirect URL, or the bare code."""
    t = str(text).strip()
    if "code=" in t:
        q = urllib.parse.urlparse(t).query or t.split("?", 1)[-1]
        vals = urllib.parse.parse_qs(q).get("code")
        if vals:
            return vals[0].strip()
    if re.match(r"^[A-Za-z0-9._\-]+$", t):
        return t
    raise ShoonyaError("could not find an auth code in what was pasted")


# ---------------------------------------------------------------- transport

TRANSPORT = None   # tests replace this: fn(url, body_bytes, headers) -> text


def _http(url, body, headers):
    if TRANSPORT is not None:
        return TRANSPORT(url, body, headers)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            txt = e.read().decode("utf-8", "replace")
        finally:
            e.close()
        if txt:
            return txt
        raise ShoonyaError("Shoonya answered HTTP %d for %s" % (e.code, url))
    except (urllib.error.URLError, OSError) as e:
        raise ShoonyaError("could not reach Shoonya (%s): check the network and that this machine's IP is "
                           "whitelisted for your API app" % (getattr(e, "reason", None) or e))


def _throttle(key=None):
    gap = time.monotonic() - _last_call[0]
    if gap < MIN_GAP_GLOBAL:
        SLEEP(MIN_GAP_GLOBAL - gap)
    if key is not None:
        g2 = time.monotonic() - _last_sym.get(key, 0.0)
        if g2 < MIN_GAP_PER_SYM:
            SLEEP(MIN_GAP_PER_SYM - g2)
        _last_sym[key] = time.monotonic()
    _last_call[0] = time.monotonic()


def call(route, values, token=None, mode=None, throttle_key=None, need_auth=True):
    """POST one Noren route. Returns the decoded JSON (dict or list)."""
    mode = mode or auth_mode()
    sess = None
    if need_auth and token is None:
        sess = load_session()
        token = sess["token"]
    _throttle(throttle_key)
    url = BASE + route
    if sess is not None and mode == "bearer" and sess.get("access_token"):
        token = sess["access_token"]
    # Exactly what Shoonya's official NorenRestApiOAuth (0.0.41) sends: a form body "jData=<raw JSON>";
    # OAuth sessions add "Authorization: Bearer <access_token>", older ones add "&jKey=<susertoken>".
    # A trading symbol is percent-encoded inside the JSON, as the SDK does, so "&" (M&M) cannot split the form.
    if "tsym" in values:
        values = dict(values, tsym=urllib.parse.quote_plus(str(values["tsym"])))
    js = json.dumps(values)
    body = "jData=" + js
    headers = {"User-Agent": UA}
    if need_auth and mode == "bearer":
        headers.update({"Authorization": "Bearer %s" % token, "Content-Type": "application/json; charset=utf-8"})
    else:
        headers["Content-Type"] = "text/plain"
        if need_auth:
            body += "&jKey=" + str(token)
    body = body.encode("utf-8")
    text = _http(url, body, headers)
    try:
        doc = json.loads(text)
    except ValueError:
        raise ShoonyaError("Shoonya %s answered something that is not JSON: %s" % (route, text[:160]))
    if isinstance(doc, dict) and str(doc.get("stat", "Ok")).lower() not in ("ok",):
        msg = doc.get("emsg") or doc.get("stat")
        if re.search(r"session|token|jkey|unauthor|invalid user", str(msg), re.I):
            raise ShoonyaError("Shoonya rejected the session (%s): run /nse-shoonya login again" % msg)
        raise ShoonyaError("Shoonya %s: %s" % (route, msg))
    return doc


def exchange_code(code, client_id, secret, uid=None):
    """OAuth step 2: the redirect's code -> session token."""
    # exactly the documented body: {"code", "checksum"} (shoonya.com/api-documentation/manual-authentication-oauth)
    vals = {"code": code_from_redirect(code), "checksum": checksum(client_id, secret, code_from_redirect(code))}
    doc = call("GenAcsTok", vals, need_auth=False)
    tok = doc.get("susertoken") or doc.get("access_token") or doc.get("accesstoken")
    if not tok:
        raise ShoonyaError("Shoonya's token answer had no session token: %s" % sorted(doc))
    return doc, tok


# ---------------------------------------------------------------- symbol master

def _master_path(exch):
    return os.path.join(CACHE_DIR, "%s_symbols.txt" % exch)


def parse_master(text):
    """Noren symbol master (comma or pipe separated, header row first) -> list of dicts with
    lower-cased header names. Column order varies between segments, so the header decides."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    sep = "|" if lines[0].count("|") > lines[0].count(",") else ","
    head = [h.strip().lower().replace(" ", "") for h in lines[0].split(sep)]
    out = []
    for ln in lines[1:]:
        cells = [c.strip() for c in ln.split(sep)]
        if len(cells) < 3:
            continue
        out.append(dict(zip(head, cells)))
    return out


def load_master(exch, refresh=False, fetch=None):
    """{TRADINGSYMBOL: row} plus {SYMBOL: row} for one exchange, cached for the day."""
    exch = exch.upper()
    path = _master_path(exch)
    fresh = False
    if os.path.exists(path) and not refresh:
        age = time.time() - os.path.getmtime(path)
        fresh = age < 20 * 3600
    if not fresh:
        get = fetch or _download
        raw = get(MASTER_URLS[exch])
        if raw[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                name = [n for n in z.namelist() if n.lower().endswith(".txt")] or z.namelist()
                raw = z.read(name[0])
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(raw)
    with open(path, encoding="utf-8", errors="replace") as fh:
        rows = parse_master(fh.read())
    idx = {}
    for r in rows:
        ts = (r.get("tradingsymbol") or r.get("tsym") or "").upper()
        sym = (r.get("symbol") or "").upper()
        if ts:
            idx[ts] = r
        if sym and r.get("instrument", "EQ").upper() in ("EQ", "") and sym not in idx:
            idx[sym] = r
    return idx


def _download(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except (urllib.error.URLError, OSError) as e:
        raise ShoonyaError("could not download the Shoonya symbol master %s: %s" % (url, e))


_MASTERS = {}


def resolve(yahoo_sym, masters=None):
    """'RELIANCE.NS' -> (exch, token, tradingsymbol, tick). Indices via INDEX_TOKENS."""
    y = str(yahoo_sym).strip().upper()
    if y in INDEX_TOKENS:
        e, tok, name = INDEX_TOKENS[y]
        return e, tok, name, 0.05
    if y.endswith(".NS"):
        exch, base = "NSE", y[:-3]
    elif y.endswith(".BO"):
        exch, base = "BSE", y[:-3]
    else:
        raise ShoonyaError("%s is not an NSE (.NS) or BSE (.BO) symbol" % yahoo_sym)
    m = (masters or _MASTERS)
    if exch not in m:
        m[exch] = load_master(exch)
    idx = m[exch]
    for cand in ((base + "-EQ", base, base + "-BE", base + "-BZ", base + "-SM", base + "-ST") if exch == "NSE"
                 else (base, base + "-A", base + "-B")):
        r = idx.get(cand)
        if r and (r.get("token") or r.get("tk")):
            tick = r.get("ticksize") or r.get("ti") or "0.05"
            try:
                tick = float(tick)
            except ValueError:
                tick = 0.05
            return exch, str(r.get("token") or r.get("tk")), (r.get("tradingsymbol") or r.get("tsym") or cand).upper(), tick
    raise ShoonyaError("%s is not in Shoonya's %s symbol master" % (base, exch))


# ---------------------------------------------------------------- bars

_TIME_FORMATS = ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%d-%m-%Y",
                 "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def parse_time(v, daily=False):
    """Noren bar time -> epoch seconds (UTC). Daily bars are stamped at 09:15 IST, like Yahoo."""
    if isinstance(v, (int, float)) or (isinstance(v, str) and re.match(r"^\d{9,11}$", v.strip())):
        return int(float(v))
    s = str(v).strip()
    for f in _TIME_FORMATS:
        try:
            d = datetime.datetime.strptime(s.title() if "%b" in f else s, f)
        except ValueError:
            continue
        if daily or f in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
            d = d.replace(hour=9, minute=15, second=0)
        return int(d.replace(tzinfo=IST).timestamp())
    raise ShoonyaError("unknown bar time %r" % v)


def _num(row, *keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, "", "NaN"):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def bars_from_rows(rows, daily=False):
    """Noren TPSeries / EODChartData rows -> ascending bars {t,o,h,l,c,v}. Accepts the
    documented names (into/inth/intl/intc, intv per-bar volume, v) and ssboe epochs.
    Rows can come newest-first; duplicates by time keep the last seen."""
    if isinstance(rows, dict):
        rows = [rows]
    out = {}
    for r in rows or []:
        if isinstance(r, str):
            try:
                r = json.loads(r)
            except ValueError:
                continue
        if not isinstance(r, dict):
            continue
        t = r.get("ssboe") or r.get("time") or r.get("tm")
        if t in (None, ""):
            continue
        c = _num(r, "intc", "c", "close")
        if c is None:
            continue
        o = _num(r, "into", "o", "open")
        h = _num(r, "inth", "h", "high")
        lo = _num(r, "intl", "l", "low")
        v = _num(r, "intv", "v", "volume") or 0.0
        out[parse_time(t, daily)] = {"o": o if o is not None else c, "h": h if h is not None else c,
                                     "l": lo if lo is not None else c, "c": c, "v": int(v)}
    return [dict(t=k, **out[k]) for k in sorted(out)]


def _ist_date(t):
    return datetime.datetime.fromtimestamp(t, IST).date()


def keep_sessions(bars, n):
    """The last n distinct trading dates."""
    if not bars or n <= 0:
        return bars
    dates = sorted({_ist_date(b["t"]) for b in bars})
    keep = set(dates[-n:])
    return [b for b in bars if _ist_date(b["t"]) in keep]


def _span_for(sessions):
    """Calendar days to request so that `sessions` trading days are covered (weekends,
    holidays and a margin)."""
    return int(sessions * 7 / 5) + 6


def _cache_file(kind, exch, token, interval, day):
    return os.path.join(CACHE_DIR, "bars", exch, token, "%s_%s_%s.json" % (kind, interval, day))


def fetch_intraday(ysym, interval, sessions):
    uid = load_session()["uid"]          # fail on a missing login before any download
    exch, token, tsym, _tick = resolve(ysym)
    minutes = TP_INTERVALS[interval]
    end = now_utc()
    start = end - datetime.timedelta(days=_span_for(sessions))
    doc = call("TPSeries", {"uid": uid, "exch": exch, "token": token,
                            "st": str(int(start.timestamp())), "et": str(int(end.timestamp())),
                            "intrv": str(minutes)}, throttle_key=(exch, token))
    if not isinstance(doc, list):
        raise ShoonyaError("Shoonya has no %s bars for %s" % (interval, tsym))
    return exch, token, tsym, keep_sessions(bars_from_rows(doc), sessions)


def fetch_daily(ysym, sessions):
    uid = load_session()["uid"]
    exch, token, tsym, _tick = resolve(ysym)
    end = now_utc()
    start = end - datetime.timedelta(days=_span_for(sessions))
    doc = call("EODChartData", {"uid": uid, "sym": "%s:%s" % (exch, tsym),
                                "from": str(int(start.timestamp())), "to": str(int(end.timestamp()))},
               throttle_key=(exch, token))
    if not isinstance(doc, list):
        raise ShoonyaError("Shoonya has no daily bars for %s" % tsym)
    bars = bars_from_rows(doc, daily=True)
    return exch, token, tsym, bars[-sessions:] if sessions else bars


def quote(ysym):
    """GetQuotes for one symbol: dict with lp (last), c (previous close), h, l, o, and the circuit
    band lc / uc when Shoonya sends it."""
    uid = load_session()["uid"]
    exch, token, tsym, _tick = resolve(ysym)
    doc = call("GetQuotes", {"uid": uid, "exch": exch, "token": token},
               throttle_key=(exch, token, "q"))
    return doc


# ---------------------------------------------------------------- Yahoo shapes

def chart_doc(ysym, interval, bars, prev_close=None, last=None, day_high=None, day_low=None, tsym=None):
    """Bars -> the Yahoo v8 chart JSON every skill already parses."""
    ts = [b["t"] for b in bars]
    meta = {"symbol": str(ysym).upper(), "currency": "INR", "exchangeName": "NSI" if str(ysym).upper().endswith(
        ".NS") else "BSE", "exchangeTimezoneName": "Asia/Kolkata", "timezone": "IST", "gmtoffset": 19800,
            "dataGranularity": interval, "dataSource": "shoonya", "shoonyaSymbol": tsym}
    if bars:
        meta["regularMarketPrice"] = last if last is not None else bars[-1]["c"]
        meta["regularMarketTime"] = bars[-1]["t"]
    if prev_close is not None:
        meta["previousClose"] = prev_close
        meta["chartPreviousClose"] = prev_close
    if day_high is not None:
        meta["regularMarketDayHigh"] = day_high
    if day_low is not None:
        meta["regularMarketDayLow"] = day_low
    q = {"open": [b["o"] for b in bars], "high": [b["h"] for b in bars], "low": [b["l"] for b in bars],
         "close": [b["c"] for b in bars], "volume": [b["v"] for b in bars]}
    return {"chart": {"result": [{"meta": meta, "timestamp": ts, "indicators": {"quote": [q]}}], "error": None}}


def chart_error(ysym, why):
    return {"chart": {"result": None, "error": {"code": "Not Found", "description": "%s: %s" % (ysym, why)}}}


def _prev_close_from_daily(daily, intraday):
    """Close of the session before the first intraday date."""
    if not intraday:
        return daily[-2]["c"] if len(daily) >= 2 else None
    first = _ist_date(intraday[0]["t"])
    before = [b for b in daily if _ist_date(b["t"]) < first]
    return before[-1]["c"] if before else None


def answer_chart(ysym, rng, interval):
    sessions = RANGE_SESSIONS.get(rng)
    if sessions is None:
        m = re.match(r"^(\d+)d$", rng or "")
        sessions = int(m.group(1)) if m else None
    if sessions is None:
        return None
    if interval in DAILY_INTERVALS:
        _e, _tok, tsym, bars = fetch_daily(ysym, sessions + 1)
        prev = bars[-sessions - 1]["c"] if len(bars) > sessions else None
        bars = bars[-sessions:]
        return chart_doc(ysym, interval, bars, prev_close=prev, tsym=tsym)
    if interval not in TP_INTERVALS:
        return None
    _e, _tok, tsym, bars = fetch_intraday(ysym, interval, sessions)
    prev = None
    try:
        _e2, _t2, _s2, daily = fetch_daily(ysym, sessions + 3)
        prev = _prev_close_from_daily(daily, bars)
    except ShoonyaError:
        prev = None
    last_day = [b for b in bars if bars and _ist_date(b["t"]) == _ist_date(bars[-1]["t"])]
    hi = max(b["h"] for b in last_day) if last_day else None
    lo = min(b["l"] for b in last_day) if last_day else None
    return chart_doc(ysym, interval, bars, prev_close=prev, day_high=hi, day_low=lo, tsym=tsym)


def answer_spark(symbols, rng, interval):
    out = {}
    for y in symbols:
        try:
            doc = answer_chart(y, rng, interval)
        except ShoonyaError:
            continue
        if not doc:
            return None
        r = (doc["chart"]["result"] or [None])[0]
        if not r:
            continue
        out[y.upper()] = {"symbol": y.upper(), "timestamp": r["timestamp"],
                          "close": r["indicators"]["quote"][0]["close"],
                          "chartPreviousClose": r["meta"].get("chartPreviousClose"),
                          "previousClose": r["meta"].get("previousClose"), "dataGranularity": interval}
    return out


_CHART = re.compile(r"^/v8/finance/chart/([^?]+)\?(.*)$")
_SPARK = re.compile(r"^/v7/finance/spark\?(.*)$")


def _note(msg):
    try:
        sys.stderr.write("[data] %s\n" % msg)
    except Exception:
        pass


def yahoo_hook(path):
    """Answer a Yahoo path from Shoonya when Shoonya is the provider. Returns JSON text,
    or None to let the caller use Yahoo. Raises when Shoonya fails and fallback is off,
    so a skill never silently trades on delayed data."""
    if provider() != "shoonya":
        return None
    m = _CHART.match(path)
    sp = _SPARK.match(path)
    if not (m or sp):
        return None                      # fundamentals, FX: Shoonya has none
    q = urllib.parse.parse_qs((m.group(2) if m else sp.group(1)))
    rng = (q.get("range") or ["1d"])[0]
    interval = (q.get("interval") or ["1d"])[0]
    try:
        if m:
            ysym = urllib.parse.unquote(m.group(1))
            if ysym.upper().startswith(("USD", "EUR", "GBP")) or ysym.endswith("=X"):
                return None
            doc = answer_chart(ysym, rng, interval)
        else:
            syms = [s for s in urllib.parse.unquote((q.get("symbols") or [""])[0]).split(",") if s]
            doc = answer_spark(syms, rng, interval)
        if doc is None:
            if allow_fallback():
                _note("Shoonya has no %s/%s series: using Yahoo (delayed, unofficial)" % (rng, interval))
                return None
            raise ShoonyaError("Shoonya cannot serve range=%s interval=%s (it has 1,3,5,10,15,30,60,120,240 "
                               "minute and daily bars)" % (rng, interval))
        return json.dumps(doc)
    except ShoonyaError as e:
        if allow_fallback():
            _note("Shoonya failed (%s): using Yahoo (delayed, unofficial)" % e)
            return None
        raise
