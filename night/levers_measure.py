# -*- coding: utf-8 -*-
"""The four free instruments for one window lever, one tag, production flags.

    venv\\Scripts\\python.exe -m night.levers_measure <tag> [KEY=VALUE ...]

Runs, in this order and as separate processes (each loads the model once):
  1. night.sectprobe            -> night/head100/out/ruler_<tag>.json   (82 frozen-ruler targets)
  2. night.head100.probe        -> night/head100/out/<tag>.json         (174, dev listed / held aggregate)
  3. night.gate                 -> night/head100/out/gate_<tag>.json    (431 retrieval cases)
  4. night.routed_sectprobe     -> night/out/routed_<tag>.json --against sect7_routed_prodflags.json
then `night.head100.compare <base> <tag>` pairs it against a base tag when one
is given as BASE=<tag>. Free: HyDE off, router replayed from the 18.09 record,
no .env in the measuring tree. Writes nothing under storage/ itself — but the
clause-embedding index saves its per-model vector cache on first build
(storage/clause_embed_cache_minilm.npz, tracked): restore it with git before
committing anything from the tree.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
H_OUT = ROOT / "night" / "head100" / "out"
N_OUT = ROOT / "night" / "out"
PROD = {"RETRIEVE_GLOSSARY": "1", "RETRIEVE_FULL_BLOCKS": "1", "RETRIEVE_ROUTER_SLOTS": "2",
        "RETRIEVE_DOC_BLOCKS": "6", "RETRIEVE_FULL_BLOCK_MAX_WORDS": "2000", "RETRIEVE_HYDE": "0"}


def _run(args: list[str], env: dict) -> int:
    t0 = time.time()
    print(f"[levers] $ {' '.join(args)}", flush=True)
    rc = subprocess.run([sys.executable, "-m", *args], cwd=ROOT, env=env).returncode
    print(f"[levers] rc={rc} in {time.time() - t0:.0f}s", flush=True)
    return rc


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    tag = argv[0]
    env = dict(os.environ)
    env.update(PROD)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("ANTHROPIC_API_KEY", None)          # belt and braces: nothing here may pay
    base = None
    for kv in argv[1:]:
        k, _, v = kv.partition("=")
        if k == "BASE":
            base = v
        else:
            env[k] = v
    H_OUT.mkdir(parents=True, exist_ok=True)
    rcs = []
    rcs.append(_run(["night.sectprobe", str(H_OUT / f"ruler_{tag}.json")], env))
    rcs.append(_run(["night.head100.probe", str(H_OUT / f"{tag}.json")], env))
    rcs.append(_run(["night.gate"], env))
    shutil.copyfile(N_OUT / "gate.json", H_OUT / f"gate_{tag}.json")
    rcs.append(_run(["night.routed_sectprobe", str(N_OUT / f"routed_{tag}.json"),
                     "--against", str(N_OUT / "sect7_routed_prodflags.json")], env))
    if base:
        rcs.append(_run(["night.head100.compare", base, tag], env))
    print(f"[levers] {tag}: return codes {rcs}", flush=True)
    return 0 if all(r == 0 for r in rcs[:3]) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
