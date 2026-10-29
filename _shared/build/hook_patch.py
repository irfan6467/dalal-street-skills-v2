"""One-time, idempotent: route every skill's yahoo_get through the Shoonya hook.

Patches _shared/india_core.py (the source of the generated scripts) and the
hand-synced copies in the other skill scripts. Run before build.py."""
import glob
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

HOOK = '''def _shoonya_feed():
    """shoonya_feed.py from this script's folder, loaded once (None when absent)."""
    mod = globals().get("_SHOONYA_FEED_MOD")
    if mod is None:
        import importlib.util
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, "shoonya_feed.py")
        if not os.path.exists(path):
            path = os.path.join(os.path.dirname(here), "_shared", "shoonya_feed.py")
        if not os.path.exists(path):
            return None
        spec = importlib.util.spec_from_file_location("shoonya_feed_" + str(abs(hash(path))), path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        globals()["_SHOONYA_FEED_MOD"] = mod
    return mod


def yahoo_get(path):
    """Price data for a Yahoo-style path. When nse/config.json picks Shoonya as the data provider the
    chart and spark paths are answered from Shoonya's live exchange data (same JSON shape); anything
    else, or the Yahoo provider, goes to Yahoo."""
    feed = _shoonya_feed()
    if feed is not None:
        try:
            hooked = feed.yahoo_hook(path)
        except feed.ShoonyaError as e:
            raise IndiaError(str(e))
        if hooked is not None:
            return hooked
    return _yahoo_http(path)


def _yahoo_http(path):'''

MARK = "def yahoo_get(path):"


def patch(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if "def _yahoo_http(path):" in text:
        return False
    if text.count(MARK) != 1:
        raise SystemExit("%s: expected exactly one %s" % (path, MARK))
    text = text.replace(MARK, HOOK, 1)
    if "\nimport os\n" not in text and "\nimport os," not in text and ", os\n" not in text:
        raise SystemExit("%s: needs `import os` at the top" % path)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return True


if __name__ == "__main__":
    targets = [os.path.join(ROOT, "_shared", "india_core.py")] + sorted(glob.glob(os.path.join(ROOT, "nse-*", "*.py")))
    for t in targets:
        with open(t, encoding="utf-8") as fh:
            if MARK not in fh.read():
                continue
        print("patched" if patch(t) else "already", os.path.relpath(t, ROOT))
