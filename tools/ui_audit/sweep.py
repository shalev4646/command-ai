"""Walk the app's screens with audit.js — only once the checker has proved itself.

    python tools/ui_audit/sweep.py [--url http://localhost:8851/] [--out DIR]

Before the first screen is judged, the self-test baits run on this very page
(audit_selftest, without mutants), and one miss aborts the sweep: a checker
that cannot see a planted defect reports "clean" about everything. The order
used to be a line in the README; it is enforced here instead.
--no-selftest skips it, for debugging the walk itself.

Every finding is printed, not just a count. Screenshots and findings.json go
to --out (default: a timestamped directory under the temp dir).
"""
import argparse
import collections
import json
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

import audit_selftest
from _browser import VIEWPORT, launch

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent

SETTINGS = [("nav_personal", "06_personal"), ("nav_language", "07_language"),
            ("nav_access", "08_text_size"), ("nav_privacy", "09_privacy"),
            ("nav_about", "10_about"), ("nav_policy_about", "11_policy"),
            ("nav_a11y", "12_a11y"), ("nav_contact", "13_contact")]
# the open layer on those screens; what it covers is judged on its own screen
DRAWER = ".st-key-cai_drawer"
SHEET = ".st-key-cai_settings"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8851/")
    ap.add_argument("--out", default="")
    ap.add_argument("--label", default="sweep")
    ap.add_argument("--no-selftest", action="store_true")
    args = ap.parse_args()
    out = Path(args.out) if args.out else (
        Path(tempfile.gettempdir()) / "cai_ui_audit"
        / f"{args.label}-{time.strftime('%Y%m%d-%H%M%S')}")
    out.mkdir(parents=True, exist_ok=True)
    src = (HERE / "audit.js").read_text(encoding="utf-8")
    findings = collections.OrderedDict()

    with sync_playwright() as p:
        br = launch(p)
        ctx = br.new_context(viewport=VIEWPORT, device_scale_factor=2, locale="he-IL")
        pg = ctx.new_page()

        def run(screen, scope=None):
            res = pg.evaluate(src, scope)
            findings[screen] = res
            pg.screenshot(path=str(out / f"{screen}.png"), full_page=True)
            print(f"{screen:20s} {len(res)} finding(s)" + (f"   [layer {scope}]" if scope else ""))
            for f in res:
                print(f"    [{f['k']}] {f['what']}" + (f"  | {f['t']}" if f.get("t") else ""))

        def tap(key, ms=2600):
            pg.click(f".st-key-{key} button", timeout=8000)
            pg.wait_for_timeout(ms)

        def back_to_settings():
            # a reload is a new session; the device cookie restores role and
            # consent, so it lands on the chat and the drawer is one tap away
            pg.reload(wait_until="load")
            pg.wait_for_timeout(5000)
            tap("drawer_open_btn", 1800)
            tap("open_settings", 3000)

        pg.goto(args.url, wait_until="load", timeout=60000)
        pg.wait_for_timeout(6500)
        if not args.no_selftest:
            _, res = audit_selftest.run_baits(pg, src)
            missed = [r["bait"]["id"] for r in res if not r["ok"]]
            if missed:
                print("self-test FAILED on this page: " + ", ".join(missed))
                print("no sweep - run audit_selftest.py and fix the checker first")
                br.close()
                sys.exit(1)
            print(f"self-test on this page: {len(res)}/{len(res)} baits caught\n")

        run("01_entry")
        pg.fill('input[type="text"]', "שלו")
        pg.locator('label:has(input[type="checkbox"])').first.click()
        pg.wait_for_timeout(500)
        pg.get_by_role("button", name="המשך").click()
        pg.wait_for_timeout(3500)
        run("02_role_picker")

        pg.get_by_role("button", name="כניסת חיילים").click()
        pg.wait_for_timeout(4500)
        run("03_chat_home")

        tap("drawer_open_btn", 2000)
        run("04_drawer", DRAWER)
        tap("open_settings", 3200)
        run("05_settings_hub", SHEET)

        for key, screen in SETTINGS:
            try:
                tap(key, 2800)
                run(screen, SHEET)
            except Exception as e:
                print(f"{screen:20s} SKIP ({str(e).splitlines()[0][:70]})")
            back_to_settings()

        # where the original report started: the screen logout lands on.
        # The button lives on the settings hub, where the loop left us.
        try:
            tap("danger_logout", 3500)
            run("14_after_logout")
        except Exception as e:
            print(f"{'14_after_logout':20s} SKIP ({str(e).splitlines()[0][:70]})")
        br.close()

    (out / "findings.json").write_text(
        json.dumps(findings, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(v) for v in findings.values())
    print(f"\nTOTAL {total} findings across {len(findings)} screens -> {out}")
    for k, n in collections.Counter(f["k"] for v in findings.values() for f in v).most_common():
        print(f"  {k:18s} {n}")


if __name__ == "__main__":
    main()
