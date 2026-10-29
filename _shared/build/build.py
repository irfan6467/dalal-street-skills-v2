"""Regenerate nse-budget/size.py, nse-risk/check.py, nse-broker/ticket.py and nse-backtest/backtest.py from tpl/ + _shared/india_core.py
(pasted whole, verbatim) + tpl/fo.py (budget and risk only). Run from anywhere: py _shared/build/build.py"""
import os
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def read(*p):
    with open(os.path.join(*p), encoding="utf-8") as fh:
        return fh.read()


core = read(ROOT, "_shared", "india_core.py").split("\n")
body = "\n".join(core[11:])          # from the first import, after the module docstring
fo = read(HERE, "tpl", "fo.py")
jobs = {"size": ("nse-budget", True), "check": ("nse-risk", True), "ticket": ("nse-broker", False),
        "backtest": ("nse-backtest", False)}
for name, (folder, with_fo) in jobs.items():
    t = read(HERE, "tpl", name + ".py")
    hdr = "# ---- india_core (pasted verbatim from _shared/india_core.py; do not edit here) ----\n"
    out = t.replace("#@@INDIA_CORE@@", hdr + body + "\n# ---- end india_core ----\n")
    out = out.replace("#@@FO@@\n", fo + "\n" if with_fo else "")
    with open(os.path.join(ROOT, folder, name + ".py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(out)
    print("built", folder, name)

# Every skill folder carries its own copy of the Shoonya feed, so each skill
# installs on its own (claude.ai uploads one folder per skill).
import glob
import shutil
feed = os.path.join(ROOT, "_shared", "shoonya_feed.py")
for d in sorted(glob.glob(os.path.join(ROOT, "nse-*"))):
    if os.path.isdir(d):
        shutil.copyfile(feed, os.path.join(d, "shoonya_feed.py"))
        print("copied shoonya_feed.py ->", os.path.basename(d))

# The broker table and charges live in india_core.py; three hand-written scripts carry a copy of that region.
REGION = ("UNVERIFIED_SQUAREOFF = ", "def trade_charges(")
core_txt = read(ROOT, "_shared", "india_core.py")
region = core_txt[core_txt.index(REGION[0]):core_txt.index(REGION[1])]
for rel in ("nse-alerts/alerts.py", "nse-exit/exit.py", "nse-timemachine/timemachine.py"):
    path = os.path.join(ROOT, rel)
    txt = read(path)
    a, b = txt.index(REGION[0]), txt.index(REGION[1])
    if txt[a:b] != region:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(txt[:a] + region + txt[b:])
        print("synced brokers and charges ->", rel)
