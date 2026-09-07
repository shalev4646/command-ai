"""The splash anchor comes from the launch PNG's own table (2026-09-04).

Frame-by-frame on the 21:58 device video: WebKit lays the splash out with
env(safe-area-inset-top)=0 for its first two frames, so the dissolving-in
splash sat ~56pt above the launch PNG — a dim second logo, the user's "two
screens". boot_shell's cai-pad script now writes --cai-pad from
pwa_assets._STARTUP_SAT (sat + 0.14 * screen.height, the PNG's exact formula)
at parse time, before the splash markup exists. These tests lock:

  * the table in the script IS _STARTUP_SAT (rendered, never copied);
  * the script rides in the first KB, between cai-micro and the boot style,
    above <body> — it must run before the splash is parsed;
  * the splash rule still consumes --cai-pad with the env() fallback;
  * the painted ping waits three frames (the lifted-frame fix).
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import boot_shell  # noqa: E402
import pwa_assets  # noqa: E402


def _table_from(js: str) -> dict:
    m = re.search(r"var T=(\{[^}]*\});", js)
    assert m, "cai-pad script carries no table"
    out = {}
    for key, val in re.findall(r'"(\d+x\d+x\d+)":(\d+)', m.group(1)):
        w, h, d = (int(x) for x in key.split("x"))
        out[(w, h, d)] = int(val)
    return out


def test_pad_table_is_the_png_table():
    js = boot_shell._pad_js()
    assert _table_from(js) == pwa_assets._STARTUP_SAT
    assert len(pwa_assets._STARTUP_SAT) >= 11


def test_pad_script_uses_the_png_formula():
    js = boot_shell._pad_js()
    # sat + 0.14 * h, h in CSS px (screen.height) — pwa_assets: sat + 0.14*(h/dpr)
    assert "T[k]+0.14*h" in js
    assert '"--cai-pad"' in js
    # unknown screens must fall back to the CSS env() formula, never guess
    assert "if(!(k in T))return;" in js
    # key = physical pixels x dpr, matching _STARTUP_SAT's (w, h, dpr) keys
    assert 'Math.round(w*d)+"x"+Math.round(h*d)+"x"+Math.round(d)' in js


def test_pad_script_is_in_the_first_kb_of_the_patched_index():
    assert boot_shell.patch_index_html(), "patch_index_html refused to write"
    src = boot_shell._index_path().read_text(encoding="utf-8")
    pad = src.index('<script id="cai-pad">')
    assert src.index('<style id="cai-micro"') < pad < src.index('<style id="cai-boot"')
    assert pad < src.index("<body>")
    assert pad < 1024, "cai-pad drifted out of the first flushed KB"
    assert _table_from(src[pad:pad + 2000]) == pwa_assets._STARTUP_SAT


def test_splash_rule_consumes_cai_pad_with_env_fallback():
    assert ("padding-top: var(--cai-pad, calc(env(safe-area-inset-top, 0px) + 14vh));"
            in boot_shell._HEAD_TEMPLATE)


def test_painted_ping_waits_five_frames():
    html = boot_shell._SPLASH_HTML
    assert "var hops = 5" in html
    assert "requestAnimationFrame(hop)" in html


def test_strip_removes_the_pad_script():
    src = boot_shell._index_path().read_text(encoding="utf-8")
    assert 'id="cai-pad"' in src
    assert 'id="cai-pad"' not in boot_shell._strip(src)


def test_production_runs_without_a_source_watcher():
    """The watcher re-scans sys.modules after every run before the run's last
    messages leave (2026-09-04, measured: running->notRunning 2.0s vs 0.55s).
    Nothing in the container edits files; the flag belongs on the prod CMD."""
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r'^CMD \[(.*)\]', docker, re.M)
    assert m, "Dockerfile has no CMD"
    assert '"--server.fileWatcherType", "none"' in m.group(1)


def test_websocket_compression_is_on():
    """345KB per boot uncompressed (2026-09-04 manifest); deflate is the cheap
    lever for the cellular boot. Lives in config.toml so dev and prod agree."""
    cfg = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    server = cfg.split("[server]", 1)[1].split("\n[", 1)[0]
    assert re.search(r"^enableWebsocketCompression\s*=\s*true", server, re.M)


def test_cover_mode_fast_path_pins_the_glass_without_kicks():
    """02:29 device video: the viewport pin landed ~200ms after the curtain
    and the composer strip dropped one status bar in the open. A shortfall
    equal to env(safe-area-inset-top) is the cover under-report: pin at once."""
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    i = app.index("COVER-MODE FAST PATH")
    body = app[i:i + 1600]
    assert 'px("env(safe-area-inset-top, 0px)")' in body
    assert "Math.abs((g - h) - satPx) <= 3" in body
    assert "h = g; window.__caiShort = 0;" in body
    # the fast path sits BEFORE the 8-kick shortfall guard, as an else-if chain
    assert body.index("h = g; window.__caiShort = 0;") < body.index("window.__caiShort = (window.__caiShort || 0) + 1")


def test_boot_curtain_waits_for_the_viewport_pin():
    """The shell's ready() must not pass in standalone until --cai-vvh is on
    <html> (bounded by PIN_WAIT_MS so a device that never pins still lifts)."""
    js = boot_shell._BOOT_JS if hasattr(boot_shell, "_BOOT_JS") else boot_shell._index_path().read_text(encoding="utf-8")
    assert "var PIN_WAIT_MS = 4000" in js
    assert "getPropertyValue('--cai-vvh')" in js
    gate = js.index("getPropertyValue('--cai-vvh')")
    assert js.index("[data-cai-settled]") < gate < js.index(".cai-entry, .st-key-cai_name_card, .cai-splash")


def test_composer_is_remeasured_at_the_lift_and_in_heal():
    """Device videos 2026-09-05/06: the chat capsule opened into a three-row
    box after the boot got faster — react-textarea-autosize measured the
    placeholder at the wrong width and never re-measured. The shell fires a
    resize at the lift and after the reap and drops a stale inline height on
    an empty composer; the engine's heal loop does the same mid-session."""
    js = boot_shell._index_path().read_text(encoding="utf-8")
    assert js.count("composerRemeasure()") >= 2, "lift and reap must both re-measure"
    i = js.index("var composerRemeasure = function")
    body = js[i:i + 900]
    assert "new Event('resize')" in body
    assert "ta.style.removeProperty('height')" in body
    assert "if (!ta || ta.value) return;" in body
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    j = app.index("stale composer height (2026-09-06)")
    heal = app[j:j + 2600]
    # since 22:15 the heal path PINS (inline !important) instead of removing and re-measuring
    assert 'ta.style.setProperty("height", "24px", "important")' in heal
    assert "ta.__caiGuard" in heal


def test_splash_bottom_is_pinned_to_the_glass():
    """15:56 device video: the wait block jumped +78/-19 rows as iOS re-reported
    the viewport mid-boot. The cai-pad script pins --cai-glass/--cai-vh14 from
    screen.height and the splash consumes them, viewport-free."""
    js = boot_shell._pad_js()
    assert '"--cai-glass",h+"px"' in js
    assert '"--cai-vh14",(0.14*h).toFixed(2)+"px"' in js
    css = boot_shell._HEAD_TEMPLATE
    assert "min-height: var(--cai-glass, 100vh)" in css, "the splash covers the viewport, never less than the glass"
    assert "top: calc(var(--cai-glass, 100vh) - var(--cai-vh14, 14vh)); transform: translateY(-100%)" in css


def test_empty_composer_is_capped_to_one_line():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '[data-testid="stChatInputTextArea"]:placeholder-shown {' not in app,         "the textarea's own :placeholder-shown rule was skipped by WebKit in half the launches (21:49 video)"
    i = app.index('[data-testid="stChatInput"]:has(textarea:placeholder-shown) textarea')
    rule = app[i:i + 700]
    assert "line-height: 24px !important" in rule
    assert "max-height: 24px !important" in rule
    assert "min-height: 0 !important" in rule
    assert "max-height: 1lh" not in rule.replace("`max-height: 1lh`", ""),         "lh resolved from the fallback font on iOS and clipped the placeholder (21:23 video)"


def test_splash_chevrons_keep_the_png_box_model():
    """Device clip 2026-09-06 16:37: the splash chevrons shrank 32->26px when
    Streamlit's global border-box landed — the launch PNG is drawn as a 32px
    box (pwa_assets dd = 32*0.7071). content-box must be pinned explicitly."""
    css = boot_shell._HEAD_TEMPLATE
    chev = css[css.index("#cai-boot-splash .chev span {"):]
    chev = chev[:chev.index("}")]
    assert "box-sizing: content-box !important" in chev
    ring = css[css.index("#cai-boot-splash .w {"):]
    ring = ring[:ring.index("}")]
    assert "box-sizing: content-box !important" in ring
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    fb = app[app.index(".cai-splash-chev span {"):]
    assert "box-sizing:content-box !important" in fb[:fb.index("}")]


def test_document_canvas_is_olive_from_parse_time():
    """Videos 17:11/17:14 (and every one back to 04.09): Streamlit's stock
    #0E1117 body colour showed as a bottom band below the splash for up to
    1.5s. The painted script hands html/body over inline-important at parse."""
    html = boot_shell._SPLASH_HTML
    i = html.index("THE CANVAS IS OLIVE FROM THE FIRST FRAME")
    body = html[i:i + 1500]
    assert "document.documentElement.style.setProperty('background', '#14170E', 'important')" in body
    assert "document.body.style.setProperty('background', '#14170E', 'important')" in body


def test_apple_web_app_metas_are_static_in_the_first_bytes():
    """Until v31 the status-bar-style/capable/title metas arrived only via the
    runtime injector, 1-2s after load; iOS re-laid the web view when they
    landed (793->852pt): viewport step, spinner jump, dark bottom band, and
    the dissolve-time lifted frame. Static from the first byte, they never
    transition. The runtime upsert must not rewrite an equal value."""
    assert boot_shell.patch_index_html(), "patch_index_html refused to write"
    src = boot_shell._index_path().read_text(encoding="utf-8")
    body = src.index("<body>")
    for name, val in (("apple-mobile-web-app-status-bar-style", "black-translucent"),
                      ("apple-mobile-web-app-capable", "yes"),
                      ("mobile-web-app-capable", "yes"),
                      ("apple-mobile-web-app-title", "CommandAI")):
        m = re.search(r'<meta id="cai-[a-z]+" name="' + name + r'" content="([^"]*)">', src)
        assert m and m.group(1) == val, name
        assert m.start() < body
    assert 'id="cai-sbstyle"' not in boot_shell._strip(src)
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    i = app.index("var upsert = function (sel, tag, attrs)")
    assert "if (el.getAttribute(k) !== String(attrs[k])) el.setAttribute(k, attrs[k]);" in app[i:i + 600]


def test_chevron_block_is_43px_so_the_wordmark_sits_on_the_png():
    css = boot_shell._HEAD_TEMPLATE
    assert "#cai-boot-splash .chev { height: 43px; }" in css


def test_boot_nudge_no_longer_perturbs_the_viewport_meta():
    """18:47 device video: the 120ms viewport re-stamp un-anchored the curtain
    and the disclaimer blinked through under the spinner, once per launch."""
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    i = app.index("var nudge = function ()")
    nudge = app[i:app.index("var kick = function ()")]
    assert "minimum-scale=1" not in nudge
    assert "__caiNudged" not in nudge
    k = app.index("var kick = function ()")
    assert "if (document.getElementById('cai-boot-splash')) return;" in app[k:k + 900]


def test_empty_composer_wrappers_are_capped_too():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    i = app.index('[data-testid="stChatInput"]:has(textarea:placeholder-shown) div')
    rule = app[i:i + 200]
    assert "max-height: 44px !important" in rule and "min-height: 0 !important" in rule


def test_frosted_overlays_are_unpainted_under_the_curtain():
    """20:21 device video: the composer strip's backdrop-filter layer showed
    through the opaque curtain for 5 frames in every launch. The shell keeps
    html.cai-curtain on until lift(); app.py hides the two frosted overlays
    under it; the engine's heal() removes a class that outlived the curtain."""
    html = boot_shell._SPLASH_HTML
    assert "classList.add('cai-curtain')" in html
    boot = boot_shell._index_path().read_text(encoding="utf-8")
    lift = boot[boot.index("var lift = function ()"):]
    assert lift.index("classList.remove('cai-curtain')") < lift.index("el.style.opacity = '0.999'")
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    i = app.index('html.cai-curtain [data-testid="stBottom"],')
    rule = app[i:i + 320]
    assert "html.cai-curtain .cai-header" in rule
    assert "visibility: hidden !important" in rule and "backdrop-filter: none !important" in rule
    assert 'aroot.classList.contains("cai-curtain")' in app


def test_composer_guard_pins_an_empty_textarea_to_one_line():
    """22:15 device video: placeholder cut in half in 3 of 5 launches with all
    CSS caps present. The engine's heal() attaches a style-attribute observer
    that pins an EMPTY textarea back to 24px whenever autosize writes a tall
    inline height, and never touches a typed value."""
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    i = app.index("COMPOSER GUARD (2026-09-06 22:15")
    g = app[i:app.index("var sb = document.querySelector('[data-testid=\"stBottom\"]');", i)]
    assert 'el.style.setProperty("max-height", "44px", "important")' in g, "wrapper pins"
    assert 'classList.toggle("cai-empty", ta.value === "")' in g
    assert 'if (ta.value !== "") return;' in g
    assert 'ta.style.setProperty("height", "24px", "important")' in g
    assert 'attributeFilter: ["style"]' in g
    assert "ta.__caiGuard" in g


def test_launch_image_carries_the_logo_again():
    """v42 (2026-09-07): the plain olive image of option A gave the pilot an
    empty field brightening from black and a logo 0.8s late. Re-measured on
    the 06.09 22:15/23:06 videos: launch-image and shell rows identical in all
    12 launches (wordmark 255-272, subtitle 307-314 / 328-333) — the "2px
    jitter" was the zoom's spring tail plus the bright frame. So the launch
    image is the shell's first frame again: chevron, wordmark, subtitle."""
    import io
    from PIL import Image
    for (w, h, d) in ((1179, 2556, 3), (750, 1334, 2), (1320, 2868, 3)):
        im = Image.open(io.BytesIO(pwa_assets._startup_png(w, h, d))).convert("RGB")
        assert im.size == (w, h)
        px = im.load()
        assert px[0, 0] == (20, 23, 14) and px[w - 1, h - 1] == (20, 23, 14), "olive field"
        sat = pwa_assets._STARTUP_SAT[(w, h, d)]
        pad = (sat + 0.14 * (h / d)) * d
        band = im.crop((0, int(pad - 10 * d), w, int(pad + 110 * d)))
        colors = band.getcolors(maxcolors=1 << 20)
        assert colors and len(colors) > 50, "chevron and wordmark ink in the identity band"
        assert any(c == (236, 237, 230) for _, c in colors), "cream wordmark ink"
        assert any(c == (163, 174, 110) for _, c in colors), "olive chevron ink"
        below = im.crop((0, int(pad + 200 * d), w, h - 40 * d))
        assert below.getcolors(maxcolors=4) == [(below.size[0] * below.size[1], (20, 23, 14))], "nothing but olive under the identity"


def test_splash_shows_its_logo_from_the_first_paint():
    """No veil, no fade: the launch image and the splash are the same picture,
    so the identity must be at full opacity on the very first painted frame,
    and the iOS-only subtitle nudge (v35) is back with the subtitle in the image."""
    html = boot_shell._SPLASH_HTML
    assert '<div id="cai-boot-splash" dir="rtl">' in html
    assert "cai-veiled" not in html and "unveil" not in html
    assert "__caiVP" not in html and "vpDiag" not in boot_shell._index_path().read_text(encoding="utf-8")
    css = boot_shell._HEAD_TEMPLATE
    assert "cai-veiled" not in css
    assert "transition: opacity .36s" not in css
    assert "@supports (-webkit-touch-callout: none)" in css
    assert "#cai-boot-splash .s2 { margin-top: -2px; }" in css


def test_bundle_is_parser_inserted_again():
    """v40 deferred the bundle behind load; the 17:07 device video showed the
    one-frame handover brightening back (+1..+4 in 4/6) after 0.0 in 5/5 on
    v39, and the launch screen did not drop any sooner. Reverted."""
    assert boot_shell.patch_index_html(), "patch_index_html refused to write"
    src = boot_shell._index_path().read_text(encoding="utf-8")
    assert src.count('<script type="module" crossorigin src="./static/js/index') == 1
    assert "cai-bundle" not in src and "cai/module" not in src

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
