"""Load a script from a skill folder (the folder names have hyphens).

    from _load import load, FIX
    entry = load("nse-entry", "entry")
"""

import importlib.util
import os
import tempfile

# Tests never run inside a real trading folder: the skills read and write nse/ relative to the working
# directory (config, Shoonya session and symbol master, intents, ledgers), so a test run from a live folder
# would read the user's real state and write test records into it. Every run starts in an empty folder.
_HERMETIC = tempfile.mkdtemp(prefix="nse-tests-")
os.chdir(_HERMETIC)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")


def load(folder, name):
    path = os.path.join(ROOT, folder, name + ".py")
    spec = importlib.util.spec_from_file_location("nse_" + folder.replace("-", "_") + "_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
