"""Prove audit.js sees what it claims to — run this BEFORE believing sweep.py.

    python tools/ui_audit/audit_selftest.py [--url http://localhost:8851/]
    python tools/ui_audit/audit_selftest.py --mutants

The checker once reported zero findings on 13 screens while blind to two
defects already found by eye (README: three blind spots). So every category it
reports, and every blind spot it has had, gets a bait here: an element
injected into the RUNNING app — inside Streamlit's own tree, where those blind
spots lived — audited on its own and removed again. A bait passes only when
the finding it should cause appears AND is attributable to it (its marker
text); a negative control passes only when nothing is reported for it.

The exit status is the verdict: 0 when every bait behaves, 1 otherwise.

--mutants turns the question around. Each entry in MUTANTS breaks audit.js in
one known way, and the baits must FAIL on every one of them. A mutant that
survives names a regression this file cannot see; a mutant whose pattern is
not found exactly once, or whose source does not run, is reported as invalid
rather than counted as caught.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from _browser import VIEWPORT, launch

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent

APP = "app"    # inside Streamlit's tree: the zero-height wrapper is an ancestor
BODY = "body"  # at the page root, for things positioned against the page
FIXED = "position:fixed;top:150px;left:16px;right:16px;z-index:2147483000;"
LIGHT = "color:#ECEDE6;font-size:14px"  # high contrast on the page: no noise

BAITS = [
    dict(id="contrast", expect="contrast",
         html='<div style="color:#3A3F30;font-size:14px">BAIT-dim טקסט חלש מדי</div>'),
    # README blind spot 2: real UI copy contains <b>/<br>/<span>
    dict(id="contrast-with-inline-child", expect="contrast",
         html='<div style="color:#3A3F30;font-size:14px">BAIT-inline טקסט עם '
              '<b>הדגשה</b> באמצע</div>'),
    # v1 read a 4.5% white wash as solid white
    dict(id="contrast-on-translucent-wash", expect="contrast",
         html='<div style="background:rgba(239,240,232,.045);padding:10px">'
              '<div style="color:#2B2F24;font-size:13px">BAIT-wash טקסט על שכבה שקופה'
              '</div></div>'),
    # a painted backdrop must not switch contrast off, and the WORST stop of a
    # ramp decides: this text is fine on the light end, unreadable on the dark
    dict(id="contrast-on-gradient", expect="contrast",
         html='<div style="background:linear-gradient(90deg,#171A12,#C9D19A);'
              'color:#2E3226;font-size:14px;padding:6px">BAIT-ramp טקסט על מדרג</div>'),
    # README blind spot 3, the drawer avatar: ~9:1 in reality, once read as 1.03:1
    dict(id="no-contrast-on-light-gradient", expect=None, not_kind="contrast",
         html='<div style="background:linear-gradient(135deg,#C9D19A,#A3AE6E);'
              'color:#14170E;font-size:14px;padding:6px">BAIT-avatar כהה על זית בהיר</div>'),
    # an ancestor faded to nothing is not on screen (v1 noise)
    dict(id="no-contrast-when-faded-out", expect=None, not_kind="contrast",
         html='<div style="opacity:0"><div style="color:#3A3F30;font-size:14px">'
              'BAIT-faded טקסט במיכל שקוף</div></div>'),
    dict(id="clipped-text", expect="clipped-text",
         html=f'<div style="width:60px;overflow:hidden;white-space:nowrap;{LIGHT}">'
              'BAIT-clip this text is far too long for sixty pixels</div>'),
    # the card carries the fill; the node on top of the text is a transparent
    # child of it — the stack must be read, not just the topmost node
    dict(id="occluded", expect="occluded",
         html='<div style="position:relative;height:40px">'
              f'<div style="{LIGHT};line-height:40px">BAIT-under טקסט מתחת לכרטיס</div>'
              '<div style="position:absolute;inset:0;background:#20270F">'
              '<div style="position:absolute;inset:0"></div></div></div>'),
    # the boot curtain and the navigation veil both pass taps through by design
    dict(id="occluded-by-tap-through-layer", expect="occluded",
         html='<div style="position:relative;height:40px">'
              f'<div style="{LIGHT};line-height:40px">BAIT-veil טקסט מתחת לווילון</div>'
              '<div style="position:absolute;inset:0;background:#20270F;'
              'pointer-events:none"><div style="position:absolute;inset:0"></div>'
              '</div></div>'),
    # an ancestor's own overlay (the settings sheet's sticky top fade) is hit
    # as the ancestor itself, and its own background lies UNDER the text
    dict(id="occluded-by-ancestor-overlay", expect="occluded",
         html='<style>#cai-audit-bait .fade{position:relative}'
              '#cai-audit-bait .fade::before{content:"";position:absolute;inset:0;'
              'background:#20270F;z-index:1;pointer-events:none}</style>'
              f'<div class="fade"><div style="{LIGHT};line-height:40px">'
              'BAIT-fade טקסט מתחת לדהייה</div></div>'),
    dict(id="no-occlusion-by-faint-ancestor-overlay", expect=None, not_kind="occluded",
         html='<style>#cai-audit-bait .faint{position:relative;background:#14170E}'
              '#cai-audit-bait .faint::before{content:"";position:absolute;inset:0;'
              'background:rgba(32,39,15,.2);z-index:1;pointer-events:none}</style>'
              f'<div class="faint"><div style="{LIGHT};line-height:40px">'
              'BAIT-faint טקסט מתחת לשכבה קלה</div></div>'),
    # a 30% veil dims the text, it does not hide it
    dict(id="no-occlusion-by-thin-veil", expect=None, not_kind="occluded",
         html='<div style="position:relative;height:40px">'
              f'<div style="{LIGHT};line-height:40px">BAIT-thin טקסט מתחת לשכבה דקה</div>'
              '<div style="position:absolute;inset:0;background:#20270F;opacity:.3;'
              'pointer-events:none"></div></div>'),
    dict(id="duplicate-copy", expect="duplicate-copy",
         html=f'<div style="{LIGHT}">BAIT-twice המשפט הזה מופיע פעמיים על אותו המסך בדיוק</div>'
              f'<div style="{LIGHT};margin-top:8px">BAIT-twice המשפט הזה מופיע פעמיים על '
              'אותו המסך בדיוק</div>'),
    dict(id="placeholder-word", expect="placeholder-leak",
         html=f'<div style="{LIGHT}">BAIT-raw שלום undefined</div>'),
    dict(id="placeholder-braces", expect="placeholder-leak",
         html=f'<div style="{LIGHT}">BAIT-tmpl שלום {{{{name}}}}</div>'),
    # a Python string that lost its f prefix
    dict(id="placeholder-format-field", expect="placeholder-leak",
         html=f'<div style="{LIGHT}">BAIT-fmt שלום {{name}}</div>'),
    dict(id="small-target", expect="small-target",
         html='<button style="height:20px;width:140px;background:#444;color:#fff;'
              'border:0">BAIT-small</button>'),
    dict(id="unnamed-control", expect="unnamed-control", by="what",
         marker="cai-bait-unnamed",
         html='<button class="cai-bait-unnamed" style="height:60px;width:60px;'
              'background:#444;border:0"></button>'),
    dict(id="off-screen", expect="off-screen", where=BODY, box="",
         html='<div style="position:absolute;left:{W_20}px;top:300px;width:200px;'
              'height:30px;background:#333;color:#fff">BAIT-edge</div>'),
    dict(id="overflow-x", expect="overflow-x", where=BODY, box="", by="kind",
         html='<div style="width:{W_150}px;height:8px"></div>'),
    # the page ramp is a fixed ::before (body::before in the app). This grey
    # passes on the dark base colour (~4.8:1) and fails on the ramp's light
    # stop (~2.7:1): only a checker that reads the underlay reports it
    dict(id="contrast-on-page-underlay", expect="contrast",
         html='<style>#cai-audit-bait .ul::before{content:"";position:fixed;top:0;'
              'left:0;right:0;bottom:0;z-index:-1;'
              'background:linear-gradient(#14170E,#3C4628)}</style>'
              '<div class="ul"><div style="color:rgb(131,131,131);font-size:14px">'
              'BAIT-underlay טקסט על רקע הדף</div></div>'),
    # ...and a negative-z underlay is painted BELOW an opaque background
    dict(id="no-contrast-from-buried-underlay", expect=None, not_kind="contrast",
         html='<style>#cai-audit-bait .ul2::before{content:"";position:fixed;top:0;'
              'left:0;right:0;bottom:0;z-index:-1;'
              'background:linear-gradient(#C9D19A,#E0E4C8)}</style>'
              '<div class="ul2" style="background:#14170E">'
              '<div style="color:rgb(131,131,131);font-size:14px">'
              'BAIT-buried רקע בהיר שמתחת לכרטיס כהה</div></div>'),
    # a collapsed st.expander is a closed <details>: Chromium still gives the
    # hidden content a box, and its text is empty — the button below must
    # produce nothing at all (matched by its class: it has no text to match)
    dict(id="nothing-inside-a-closed-details", expect=None, not_kind="*", by="what",
         marker="cai-bait-folded",
         html=f'<details><summary style="{LIGHT}">BAIT-summary כותרת מקופלת</summary>'
              '<button class="cai-bait-folded" style="height:20px;width:140px;'
              'background:#444;color:#fff;border:0">BAIT-folded</button></details>'),
    # scope: the sweep audits only the open layer on drawer/settings screens
    dict(id="scope-judges-only-its-layer", expect="contrast", scope="#cai-audit-layer",
         absent="BAIT-outside",
         html='<div id="cai-audit-layer"><div style="color:#3A3F30;font-size:14px">'
              'BAIT-inside טקסט בתוך השכבה</div></div>'
              '<div style="color:#3A3F30;font-size:14px">BAIT-outside טקסט מחוץ לשכבה</div>'),
    dict(id="scope-that-matches-nothing", expect="scope-missing", by="kind",
         scope="#cai-audit-nowhere", html='<div></div>'),
    dict(id="scope-that-is-not-on-screen", expect="scope-hidden", by="kind",
         scope="#cai-audit-offstage",
         html='<div id="cai-audit-offstage" style="display:none">'
              '<div style="color:#3A3F30;font-size:14px">BAIT-offstage</div></div>'),
]
for _b in BAITS:
    _b.setdefault("where", APP)
    _b.setdefault("box", FIXED)
    _b.setdefault("by", "text")
    _b.setdefault("scope", None)
    if "marker" not in _b:
        _m = re.search(r"BAIT-[a-z]+", _b["html"])
        _b["marker"] = _m.group(0) if _m else ""

# (id, exact text in audit.js, replacement). Each re-creates one known way the
# checker was, or could quietly become, blind. See --mutants above.
MUTANTS = [
    ("hidden: ancestor box size (README 1)",
     "      const s = getComputedStyle(n), r = n.getBoundingClientRect();\n",
     "      const s = getComputedStyle(n), r = n.getBoundingClientRect();\n"
     "      if (r.width <= 2 || r.height <= 2) return true;\n"),
    ("hidden: closed <details> content shown",
     "      if (n.tagName === 'DETAILS' && !n.open && prev && prev.tagName !== 'SUMMARY') return true;",
     "      ;"),
    ("hidden: opacity ignored",
     "      if (+s.opacity < .05) return true;",
     "      ;"),
    ("owns: leaf elements only (README 2)",
     "    for (const c of e.children) if ((c.innerText || '').trim() === t) return false;",
     "    if (e.children.length) return false;"),
    ("backdrop: first colour as solid (v1)",
     "        layers.push([c]);",
     "        layers.push([[c[0], c[1], c[2]]]); break;"),
    ("backdrop: page underlay ignored",
     "      if (u && u.behind) layers.push(...u.got);",
     "      ;"),
    ("backdrop: buried underlay on top",
     "      if (u && !u.behind) layers.push(...u.got);",
     "      if (u) layers.push(...u.got);"),
    ("scope: ignored",
     "  const root = scope ? document.querySelector(scope) : document.body;",
     "  const root = document.body;"),
    ("scope: a missing layer passes silently",
     "  if (!root) return [{k: 'scope-missing', what: `no element matches ${scope}`}];",
     "  if (!root) return [];"),
    ("scope: a hidden layer passes silently",
     "  if (scope && hidden(root)) return [{k: 'scope-hidden', what: `${scope} is not on screen`}];",
     "  ;"),
    ("backdrop: through gradients (README 3)",
     "        if (stops.length) layers.push(stops);",
     "        ;"),
    ("backdrop: gradients skipped",
     "        if (stops.length) layers.push(stops);",
     "        return null;"),
    ("contrast: best stop decides",
     "      ratio = Math.min(ratio, (Math.max(L1,L2)+.05) / (Math.min(L1,L2)+.05));",
     "      ratio = ratio === Infinity ? (Math.max(L1,L2)+.05) / (Math.min(L1,L2)+.05)"
     " : Math.max(ratio, (Math.max(L1,L2)+.05) / (Math.min(L1,L2)+.05));"),
    ("contrast: floor dropped to 1",
     "    const floor = (size >= 24 || (size >= 18.66 && bold)) ? 3 : 4.5;",
     "    const floor = 1;"),
    ("clipped-text: off",
     "    if ((s.overflowX === 'hidden' || s.overflow === 'hidden') && e.scrollWidth > e.clientWidth + 2)",
     "    if (false)"),
    ("occlusion: tap-through skipped",
     "  document.head.appendChild(tapThrough);",
     "  ;"),
    ("occlusion: topmost node only",
     "          for (const n of at < 0 ? [] : stack.slice(0, at)) {",
     "          for (const n of at < 1 ? [] : [stack[0]]) {"),
    ("occlusion: opacity ignored",
     "            const a = (n.contains(e) ? pseudoFill(n) : fill(n)) * opacity(n);",
     "            const a = (n.contains(e) ? pseudoFill(n) : fill(n));"),
    ("occlusion: ancestor by its own background",
     "            const a = (n.contains(e) ? pseudoFill(n) : fill(n)) * opacity(n);",
     "            const a = fill(n) * opacity(n);"),
    ("occlusion: off",
     "          if (cover >= .5)",
     "          if (cover >= 5)"),
    ("duplicate-copy: off",
     "            if (a.includes(b))",
     "            if (false)"),
    ("placeholder: the old \\b-wrapped regex",
     r"      if (leaf && /\b(undefined|NaN|None|null|TODO|FIXME|lorem)\b|\[object Object\]|\{\{|\{[A-Za-z_][\w.]*\}/i.test(t))",
     r"      if (leaf && /\b(undefined|NaN|None|null|\[object Object\]|\{\{|TODO|FIXME|lorem)\b/i.test(t))"),
    ("small-target: floor at 20px",
     "    if (r.height < 44)",
     "    if (r.height < 20)"),
    ("unnamed-control: off",
     "    if (!n) out.push(",
     "    if (false) out.push("),
    ("off-screen: off",
     "    if (r.width < W && (r.right > W + 1 || r.left < -1) && s.position !== 'fixed')",
     "    if (false)"),
    ("overflow-x: off",
     "  if (de.scrollWidth > W + 1)",
     "  if (false)"),
]

INJECT = """(b) => {
  const host = b.where === 'body' ? document.body
    : (document.querySelector('[data-testid="stMainBlockContainer"]')
       || document.querySelector('[data-testid="stMain"]') || document.body);
  const c = document.createElement('div');
  c.id = 'cai-audit-bait';
  c.style.cssText = b.box;
  c.innerHTML = b.html.split('{W_20}').join(String(innerWidth - 20))
                      .split('{W_150}').join(String(innerWidth + 150));
  host.appendChild(c);
  let zero = false;
  for (let n = c.parentElement; n && n.nodeType === 1; n = n.parentElement)
    if (n.getBoundingClientRect().height === 0) { zero = true; break; }
  return {host: host.getAttribute('data-testid') || host.tagName, zero};
}"""
REMOVE = "() => { const c = document.getElementById('cai-audit-bait'); if (c) c.remove(); }"


def _key(f):
    return json.dumps(f, sort_keys=True, ensure_ascii=False)


def judge(bt, new, base_kinds):
    marker = bt["marker"]
    leaked = [f for f in new if bt.get("absent") and bt["absent"] in (f.get("t") or "")]
    if leaked:
        return False, [f"reported outside its scope: {leaked[0]}"]
    if bt["expect"] is None:
        field = "what" if bt["by"] == "what" else "t"
        bad = [f for f in new if bt["not_kind"] in ("*", f["k"])
               and marker in (f.get(field) or "")]
        return not bad, bad
    kind = bt["expect"]
    if bt["by"] == "kind":
        if kind in base_kinds:
            return False, [f"unprovable: the page reports {kind} before the bait"]
        hit = [f for f in new if f["k"] == kind]
    elif bt["by"] == "what":
        hit = [f for f in new if f["k"] == kind and marker in f.get("what", "")]
    else:
        hit = [f for f in new if f["k"] == kind and marker in (f.get("t") or "")]
    return bool(hit), hit


def run_baits(page, src):
    base = page.evaluate(src)
    base_keys = {_key(f) for f in base}
    base_kinds = {f["k"] for f in base}
    out = []
    for bt in BAITS:
        info = page.evaluate(INJECT, bt)
        page.wait_for_timeout(120)
        try:
            found = page.evaluate(src, bt["scope"])
        finally:
            page.evaluate(REMOVE)
        new = [f for f in found if _key(f) not in base_keys]
        ok, why = judge(bt, new, base_kinds)
        out.append(dict(bait=bt, ok=ok, why=why, info=info))
    return base, out


def report(results):
    failed = 0
    for r in results:
        bt = r["bait"]
        want = bt["expect"] or f"no {bt['not_kind']}"
        if r["ok"]:
            print(f"  ok      {bt['id']:34s} -> {want}")
        else:
            failed += 1
            print(f"  MISSED  {bt['id']:34s} -> {want}   got: {r['why'][:2]}")
    return failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8851/")
    ap.add_argument("--audit", default=str(HERE / "audit.js"))
    ap.add_argument("--mutants", action="store_true")
    ap.add_argument("--settle", type=int, default=6500,
                    help="ms to wait after load before the first audit")
    args = ap.parse_args()
    src = Path(args.audit).read_text(encoding="utf-8")

    with sync_playwright() as p:
        br = launch(p)
        page = br.new_context(viewport=VIEWPORT, locale="he-IL").new_page()
        page.goto(args.url, wait_until="load", timeout=60000)
        page.wait_for_timeout(args.settle)

        base, results = run_baits(page, src)
        app_info = next(r["info"] for r in results if r["bait"]["where"] == APP)
        print(f"page before any bait: {len(base)} finding(s)")
        print(f"app baits hosted in {app_info['host']}; zero-height ancestor "
              f"(README blind spot 1) {'present' if app_info['zero'] else 'ABSENT'}")
        failed = report(results)
        print(f"\n{len(results) - failed}/{len(results)} baits behaved")

        survived = invalid = 0
        if args.mutants:
            if failed:
                print("\ncontrol run failed - mutants mean nothing until it passes")
            elif not MUTANTS:
                print("\nno mutants defined")
                invalid += 1
            else:
                print(f"\nmutants ({len(MUTANTS)}): each must make at least one bait fail")
                for mid, old, new in MUTANTS:
                    n = src.count(old)
                    if n != 1:
                        invalid += 1
                        print(f"  INVALID  {mid:30s} pattern found {n} times")
                        continue
                    try:
                        _, mres = run_baits(page, src.replace(old, new))
                    except Exception as e:  # a mutant that does not run proves nothing
                        invalid += 1
                        print(f"  INVALID  {mid:30s} does not run: {str(e).splitlines()[0][:70]}")
                        continue
                    missed = [r["bait"]["id"] for r in mres if not r["ok"]]
                    if missed:
                        print(f"  killed   {mid:30s} by {', '.join(missed)}")
                    else:
                        survived += 1
                        print(f"  SURVIVED {mid:30s} - no bait noticed")
                print(f"\nmutants: {len(MUTANTS) - survived - invalid} killed, "
                      f"{survived} survived, {invalid} invalid")
        br.close()
    sys.exit(1 if (failed or survived or invalid) else 0)


if __name__ == "__main__":
    main()
