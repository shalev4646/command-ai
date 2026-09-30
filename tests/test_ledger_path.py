# -*- coding: utf-8 -*-
"""LEDGER_PATH — one ledger for paid steps run from two worktrees (night/config.py) — no model, no API.

An arm's second pass is composed in a v161 worktree while the .env and the books live in the money
tree (30.09). Unset, night.config.LEDGER is the tree's own night/out/ledger.json, as before; set, it is
the named file, resolved — and night.ledger.Ledger books into it. Checked in fresh interpreters, since
the constant is read at import.

    venv\\Scripts\\python.exe tests\\test_ledger_path.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = "import sys; sys.path.insert(0, sys.argv[1]); from night import config as C; print('LEDGER=' + str(C.LEDGER))"


def _ledger_in_fresh_process(env: dict) -> Path:
    out = subprocess.run([sys.executable, "-c", CODE, str(ROOT)], env=env, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", cwd=str(ROOT))
    line = next(ln for ln in out.stdout.splitlines() if ln.startswith("LEDGER="))
    return Path(line[len("LEDGER="):])


def test_unset_is_the_trees_own_ledger():
    env = {k: v for k, v in os.environ.items() if k != "LEDGER_PATH"}
    assert _ledger_in_fresh_process(env) == ROOT / "night" / "out" / "ledger.json"


def test_set_is_the_named_file_and_the_ledger_books_there():
    with tempfile.TemporaryDirectory() as t:
        shared = Path(t) / "shared_ledger.json"
        shared.write_text(json.dumps({"spent": 1.5, "reserved": 0.0,
                                      "entries": [{"id": "x#1-0", "label": "x", "estimate": 1.5, "actual": 1.5}]}),
                          encoding="utf-8")
        env = dict(os.environ, LEDGER_PATH=str(shared))
        assert _ledger_in_fresh_process(env) == shared.resolve()
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from night import config as C; from night.ledger import Ledger; "
                "L = Ledger(C.LEDGER); print('SPENT=%.2f' % L.spent)")
        out = subprocess.run([sys.executable, "-c", code, str(ROOT)], env=env, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", cwd=str(ROOT))
        assert "SPENT=1.50" in out.stdout, out.stdout + out.stderr[-300:]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all ledger-path tests passed")
