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


def test_painted_ping_waits_three_frames():
    html = boot_shell._SPLASH_HTML
    assert html.count("requestAnimationFrame(") == 3


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
