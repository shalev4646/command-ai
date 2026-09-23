# -*- coding: utf-8 -*-
"""The deploy-package measurement: RETRIEVE_HOMONYMS=1 + RETRIEVE_QUOTELESS=1 against
the SAME tree with both off, on whatever corpus that tree carries (22.09: prepared for
the moment session A's re-curated corpus is merged into main).

    venv\\Scripts\\python.exe -m night.package_measure [--quick] [REAL24=path/to/real_questions.json]

Runs, free (HyDE off, router replayed, no .env needed), in this order:
  base  = night.levers_measure pkg_base                       (ruler, head-100, gate, routed)
  arm   = night.levers_measure pkg_hq  HOMONYMS=1 QUOTELESS=1 BASE=pkg_base  (+ compare)
  then probe_variant for both arms on: the locked homonym set (12), hW3a alone (the
  tuned question the QUOTELESS lever was built for), phrasing c (38); and, when a
  real-questions file is given, the real24 window listing for both arms.
--quick runs only the three probe_variant pairs (≈2 min) — a smoke of the script, not
the measurement.

Criterion (night/QUOTELESS_CRITERION.md, unchanged for the package): zero lost on every
instrument against pkg_base, and hW3a's 35.0210 in the served window under pkg_hq.
The summary at the end prints exactly those numbers; anything lost is listed by id.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
H_OUT = ROOT / "night" / "head100" / "out"
N_OUT = ROOT / "night" / "out"
PROD = {"RETRIEVE_GLOSSARY": "1", "RETRIEVE_FULL_BLOCKS": "1", "RETRIEVE_ROUTER_SLOTS": "2",
        "RETRIEVE_DOC_BLOCKS": "6", "RETRIEVE_FULL_BLOCK_MAX_WORDS": "2000", "RETRIEVE_HYDE": "0"}
ARM = {"RETRIEVE_HOMONYMS": "1", "RETRIEVE_QUOTELESS": "1"}
OFF = {"RETRIEVE_HOMONYMS": "0", "RETRIEVE_QUOTELESS": "0"}
HW3A_ID = "hW3a"


def _env(extra: dict) -> dict:
    env = dict(os.environ)
    env.update(PROD)
    env.update(extra)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("ANTHROPIC_API_KEY", None)          # nothing here may pay
    return env


def _run(args: list[str], env: dict) -> int:
    t0 = time.time()
    print(f"[package] $ {' '.join(args)}", flush=True)
    rc = subprocess.run([sys.executable, "-m", *args], cwd=ROOT, env=env).returncode
    print(f"[package] rc={rc} in {time.time() - t0:.0f}s", flush=True)
    return rc


def _hw3a_targets() -> Path:
    """A one-row target file for hW3a, built from targets.json (a tuned question:
    night/head100/held_tuned.json — measured by name on purpose)."""
    rows = json.loads((ROOT / "night/head100/targets.json").read_text(encoding="utf-8"))
    one = [r for r in rows if r.get("id") == HW3A_ID]
    p = H_OUT / "_hw3a_targets.json"
    p.write_text(json.dumps(one, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def _probe_pair(targets: Path, tag: str) -> list[int]:
    return [_run(["night.head100.probe_variant", str(targets), str(H_OUT / f"{tag}_base.json")], _env(OFF)),
            _run(["night.head100.probe_variant", str(targets), str(H_OUT / f"{tag}_hq.json")], _env(ARM))]


def _per(tag: str) -> dict:
    r = json.loads((H_OUT / f"{tag}.json").read_text(encoding="utf-8"))
    return {p["id"]: p for p in r["per"]}


def _pair_summary(tag: str, label: str) -> bool:
    a, b = _per(f"{tag}_base"), _per(f"{tag}_hq")
    ca = sum(1 for p in a.values() if p.get("sect_content"))
    cb = sum(1 for p in b.values() if p.get("sect_content"))
    lost = [i for i in a if a[i].get("sect_content") and not b[i].get("sect_content")]
    gained = [i for i in a if not a[i].get("sect_content") and b[i].get("sect_content")]
    moved = [i for i in a if a[i].get("window_words") != b[i].get("window_words")]
    print(f"[package] {label}: content {ca}/{len(a)} -> {cb}/{len(b)}   lost {len(lost)} {lost}   "
          f"gained {len(gained)}{' ' + str(gained) if tag != 'pkg_homset' else ''}   rows whose window moved: {len(moved)}")
    return not lost


def main(argv: list[str]) -> int:
    quick = "--quick" in argv
    real24 = None
    for a in argv:
        if a.startswith("REAL24="):
            real24 = Path(a.partition("=")[2])
    H_OUT.mkdir(parents=True, exist_ok=True)
    rcs: list[int] = []
    if not quick:
        rcs.append(_run(["night.levers_measure", "pkg_base"], _env(OFF)))
        rcs.append(_run(["night.levers_measure", "pkg_hq", "RETRIEVE_HOMONYMS=1", "RETRIEVE_QUOTELESS=1",
                         "BASE=pkg_base"], _env({})))
    rcs += _probe_pair(ROOT / "night/head100/homonym_targets.json", "pkg_homset")
    rcs += _probe_pair(_hw3a_targets(), "pkg_hw3a")
    rcs += _probe_pair(ROOT / "night/head100/targets_c.json", "pkg_c")
    if real24 and real24.exists() and not quick:
        rcs.append(_run(["night.real24_window", str(real24), "pkg_base"], _env(OFF)))
        rcs.append(_run(["night.real24_window", str(real24), "pkg_hq", "BASE=pkg_base"], _env(ARM)))
    print("\n[package] ── summary (criterion: zero lost everywhere, hW3a in window under the arm) ──")
    ok = True
    ok &= _pair_summary("pkg_homset", "locked homonym set (12)")
    ok &= _pair_summary("pkg_c", "phrasing c (38)")
    hw = _per("pkg_hw3a_hq").get(HW3A_ID, {})
    hw_ok = bool(hw.get("sect_content"))
    print(f"[package] hW3a under the arm: doc_in_window={hw.get('doc_in_window')} sect_content={hw_ok} "
          f"rank={hw.get('sect_rank')} words={hw.get('window_words')}")
    ok &= hw_ok
    if not quick:
        print("[package] ruler / head-100 / gate / routed: see the compare block above (lost [] everywhere = pass)")
    print(f"[package] {'PASS' if ok else 'FAIL'} on the probe pairs; return codes {rcs}")
    return 0 if ok and all(r == 0 for r in rcs) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
