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
from _browser import launch, phone_context

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent

SETTINGS = [("nav_personal", "06_personal"), ("nav_language", "07_language"),
            ("nav_access", "08_text_size"), ("nav_privacy", "09_privacy"),
            ("nav_about", "10_about"), ("nav_policy_about", "11_policy"),
            ("nav_a11y", "12_a11y"), ("nav_contact", "13_contact")]
# the open layer on those screens; what it covers is judged on its own screen
DRAWER = ".st-key-cai_drawer"
SHEET = ".st-key-cai_settings"
DIALOG = '[data-testid="stDialog"] [role="dialog"]'
LAST_ANSWER = '[data-audit-last="1"]'
# word in the question -> the canned answer run_stub.py streams back
ANSWERS = [("חופשה", "03a_answer_ruling"), ("מלגה", "03b_answer_missing"),
           ("מטען", "03c_answer_revoked")]
ANSWERS_JS = """() => [...document.querySelectorAll('[data-testid="stChatMessage"]')]
  .filter(m => !m.querySelector('[data-testid="stChatMessageAvatarUser"]')).length"""
MARK_LAST_JS = """() => {
  for (const m of document.querySelectorAll('[data-audit-last]')) m.removeAttribute('data-audit-last');
  const a = [...document.querySelectorAll('[data-testid="stChatMessage"]')]
    .filter(m => !m.querySelector('[data-testid="stChatMessageAvatarUser"]')).pop();
  if (a) { a.setAttribute('data-audit-last', '1'); a.scrollIntoView({block: 'center'}); }
}"""
TOOLS_JS = """() => [...document.querySelectorAll('.st-key-cai_tools [class*="st-key-open_"]')]
  .map(e => [...e.classList].find(c => c.startsWith('st-key-open_')).slice(7))"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8851/")
    ap.add_argument("--out", default="")
    ap.add_argument("--label", default="sweep")
    ap.add_argument("--no-selftest", action="store_true")
    ap.add_argument("--standalone", action="store_true",
                    help="audit the INSTALLED app's CSS world, not a browser tab")
    args = ap.parse_args()
    out = Path(args.out) if args.out else (
        Path(tempfile.gettempdir()) / "cai_ui_audit"
        / f"{args.label}-{time.strftime('%Y%m%d-%H%M%S')}")
    out.mkdir(parents=True, exist_ok=True)
    src = (HERE / "audit.js").read_text(encoding="utf-8")
    findings = collections.OrderedDict()

    with sync_playwright() as p:
        br = launch(p)
        ctx = phone_context(br, standalone=args.standalone, device_scale_factor=2)
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

        def back_to_settings(screen_key=None):
            # a reload is a new session; the device cookie restores role and
            # the approval, so it lands on the chat and the drawer is one tap away
            pg.reload(wait_until="load")
            pg.wait_for_timeout(5000)
            tap("drawer_open_btn", 1800)
            tap("open_settings", 3000)
            if screen_key:
                tap(screen_key, 2800)

        def ask(question):
            """Type into the real chat input; run_stub answers from its cans."""
            before = pg.evaluate(ANSWERS_JS)
            box = pg.locator('[data-testid="stChatInput"] textarea')
            box.fill(question)
            # Send with the ARROW, the way a thumb does. On a touch device the
            # app makes Return insert a newline on purpose (soldiers write
            # multi-line questions), so pressing Enter in a phone-shaped context
            # types into the box forever and the audit times out with no answer
            # — which is how the three answer screens went missing from the
            # first installed-app sweep (2026-09-19).
            send = pg.locator('[data-testid="stChatInputSubmitButton"]')
            if send.count():
                send.first.click()
            else:
                box.press("Enter")
            pg.wait_for_function("(n) => (" + ANSWERS_JS + ")() > n", arg=before,
                                 timeout=30000)
            pg.wait_for_timeout(2500)      # the stream and its reruns settle
            # judged on its own, centred: earlier turns scroll under the fixed
            # header, which is the chat working, not a defect
            pg.evaluate(MARK_LAST_JS)

        def confirm_card(screen, opener, no_key, scope=SHEET):
            """The question every destructive action now asks first."""
            try:
                tap(opener, 2600)
                run(screen, scope)
                tap(no_key, 2200)
            except Exception as e:
                print(f"{screen:20s} SKIP ({str(e).splitlines()[0][:70]})")

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
        # the full terms the tick approves, opened
        try:
            pg.click(".cai-tos-x summary", timeout=5000)
            pg.wait_for_timeout(500)
            run("01b_entry_terms_open")
            pg.click(".cai-tos-x summary")
            pg.wait_for_timeout(400)
        except Exception as e:
            print(f"{'01b_entry_terms_open':20s} SKIP ({str(e).splitlines()[0][:70]})")
        pg.fill('input[type="text"]', "שלו")
        pg.locator('label:has(input[type="checkbox"])').first.click()
        pg.wait_for_timeout(500)
        pg.get_by_role("button", name="המשך").click()
        pg.wait_for_timeout(3500)
        run("02_role_picker")

        pg.get_by_role("button", name="כניסת חיילים").click()
        pg.wait_for_timeout(4500)
        run("03_chat_home")

        # one answer of each shape the screen draws (run_stub.py's cans)
        for word, screen in ANSWERS:
            try:
                ask(f"שאלה על {word}")
                run(screen, LAST_ANSWER)
            except Exception as e:
                print(f"{screen:20s} SKIP ({str(e).splitlines()[0][:70]})")

        tap("drawer_open_btn", 2000)
        run("04_drawer", DRAWER)
        # every tool this role's drawer offers, each in its own dialog
        tools = pg.evaluate(TOOLS_JS)
        for key in tools:
            try:
                tap(key, 3000)
                run(f"04t_{key[5:]}", DIALOG)
                close = pg.locator('[data-testid="stDialog"] button[aria-label="Close"]')
                if close.count():
                    close.first.click()
                else:
                    pg.keyboard.press("Escape")
                pg.wait_for_timeout(1500)
            except Exception as e:
                print(f"{'04t_' + key[5:]:20s} SKIP ({str(e).splitlines()[0][:70]})")
                pg.reload(wait_until="load")    # a known state: the chat
                pg.wait_for_timeout(5000)
            # the button toggles, so read the state before pressing it
            if not pg.evaluate("() => document.documentElement.classList"
                               ".contains('cai-drawer-open')"):
                tap("drawer_open_btn", 1800)
        tap("open_settings", 3200)
        run("05_settings_hub", SHEET)

        for key, screen in SETTINGS:
            try:
                tap(key, 2800)
                run(screen, SHEET)
            except Exception as e:
                print(f"{screen:20s} SKIP ({str(e).splitlines()[0][:70]})")
            back_to_settings()

        # the questions in front of every destructive action (2026-09-17)
        confirm_card("05c_confirm_clearhist", "nav_clearhist", "clearhist_no")
        back_to_settings("nav_privacy")
        confirm_card("09c_confirm_clearhist2", "nav_clearhist2", "clearhist2_no")
        confirm_card("09d_confirm_wipe", "danger_wipe", "wipe_no")
        back_to_settings("nav_about")
        confirm_card("10c_confirm_revoke", "danger_tos_revoke", "tos_revoke_no")
        back_to_settings()

        # where the original report started: the screen logout lands on
        try:
            tap("danger_logout", 2600)
            run("05d_confirm_logout", SHEET)
            tap("danger_logout_yes", 3500)
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
