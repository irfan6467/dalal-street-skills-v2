"""Save Google News RSS for past dates in the file names nse-news reads with --offline, using nse-news's own
query (company name or ticker) with a date range in place of `when:48h`. news.py then cuts by publication time
with --now; the raw files are never printed (they can hold items after the cut).

  python3 news_fetch.py --after 2026-10-11 --before 2026-10-16 --out DIR TCS INFY ...
"""
import argparse
import importlib.util
import os
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--after", required=True)
    ap.add_argument("--before", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("tickers", nargs="+")
    a = ap.parse_args()
    spec = importlib.util.spec_from_file_location("news_x", os.path.join(ROOT, "nse-news", "news.py"))
    news = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(news)
    os.makedirs(a.out, exist_ok=True)
    f = news.Fetcher(pause=1.0)
    eq = f.get(news.EQUITY_L_URL, "EQUITY_L.csv")
    with open(os.path.join(a.out, "EQUITY_L.csv"), "wb") as fh:
        fh.write(eq)
    names = news.parse_equity_l(eq)
    for t in a.tickers:
        q = news.build_query(t, names.get(t), 48).rsplit(" when:", 1)[0]
        q += " after:%s before:%s" % (a.after, a.before)
        data = f.get(news.GNEWS % urllib.parse.quote_plus(q), "")
        with open(os.path.join(a.out, "gnews-%s.xml" % news.safe(t)), "wb") as fh:
            fh.write(data)
        print("%-11s %6d bytes  query: %s" % (t, len(data), q))


if __name__ == "__main__":
    main()
