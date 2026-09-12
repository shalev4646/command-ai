"""UI gaps from the 31.08 mobile audit, items c1 + c2 of the tracker (2026-09-11).

c1 -- the knowledge-base order rows (`.cai-order-link`, 168 of them) measured
      33px tall on device: under the 44px touch target, so a thumb lands on
      the neighbouring order. The rule must declare the target explicitly.
c2 -- six secondary texts sat at 10.5-11px: the order's date badge, the role
      line in the drawer identity block, the entitlement disclaimer, the
      pay-map clause label and disclaimer, and the reserve tag. 12px is the
      floor, and they take the reader's text-size setting (--cai-fs) like the
      answer body does.

These are lock tests on the CSS source: the live geometry was measured in the
running app when the change landed (see the tracker note); the tests keep the
declarations from drifting back.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "app.py").read_text(encoding="utf-8")

SMALL_TEXT_SELECTORS = [
    ".cai-ident .rl",
    ".cai-order-date",
    ".cai-ent-disc span.t",
    ".cai-pa-clause",
    ".cai-pa-disc",
    ".cai-mil-tag",
]


def _rule(selector: str) -> str:
    """Body of the first CSS rule that starts with exactly this selector.

    app.py keeps its CSS in f-strings ({{ }}) and plain strings ({ }); the
    pattern accepts both, and requires the brace right after the selector so
    `.cai-order-link[hidden]` or `a.cai-order-link:hover` never match.
    """
    pat = r"(?m)^\s*" + re.escape(selector) + r"\s*\{\{?(.*?)\}\}?"
    m = re.search(pat, SRC, re.S)
    assert m, "no CSS rule for %s" % selector
    return m.group(1)


def _font_px(body: str) -> float:
    """The px size in a `font:` shorthand or a `font-size:` declaration."""
    m = re.search(r"font(?:-size)?:\s*(?:\d{3}\s+)?(?:calc\(\s*)?([\d.]+)px", body)
    assert m, "no px font size in %r" % body[:90]
    return float(m.group(1))


def test_order_rows_declare_the_touch_target():
    body = _rule(".cai-order-link")
    assert "min-height: 44px" in body, "order rows do not declare min-height: 44px"


def test_secondary_text_is_at_least_12px():
    small = {sel: _font_px(_rule(sel)) for sel in SMALL_TEXT_SELECTORS}
    under = {sel: px for sel, px in small.items() if px < 12}
    assert not under, "under 12px: %s" % under


def test_secondary_text_follows_the_text_size_setting():
    missing = [sel for sel in SMALL_TEXT_SELECTORS if "var(--cai-fs" not in _rule(sel)]
    assert not missing, "not scaled by --cai-fs: %s" % missing


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as exc:
                failures += 1
                print("FAIL", name, "-", str(exc).encode("ascii", "replace").decode())
            except Exception as exc:  # a missing symbol is a failure, not a crash
                failures += 1
                print("ERROR", name, "-",
                      repr(exc).encode("ascii", "replace").decode())
    sys.exit(1 if failures else 0)
