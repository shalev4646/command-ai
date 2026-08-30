# -*- coding: utf-8 -*-
"""Invariants for the lazy-chunk modulepreload hints -- plain-assert script
(no pytest in venv).

Run: venv\\Scripts\\python.exe tests\\test_boot_preload.py
Prints only ASCII (cp1252 console pitfall).

WHY THESE EXIST. Measured on production 2026-08-26 with a cold Chromium
profile: the entry bundle lands at 2.14s and the LAST lazy chunk at 6.15s --
four seconds spent on four serialised round-trips for 457KB that could have
been requested at once. The same four waves on a warm load (every chunk a
cache hit) cost 0.45s, which is the proof that the missing ~3.5s is network
and not the main thread.

Each assertion below guards a way this optimisation silently turns into a
pessimisation instead: a hint that misses its chunk buys nothing, a hint whose
fetch mode disagrees with the script tag downloads the chunk TWICE, and a hint
that drifts above the stream split grows the one chunk the whole launch-flash
mechanism depends on.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import boot_shell  # noqa: E402

SPLIT = "<!--/cai-boot-splash-->"


def _index_text():
    return boot_shell._index_path().read_text(encoding="utf-8")


def test_every_preloaded_chunk_resolves_to_exactly_one_file():
    """A hint for a chunk that no longer exists is dead weight, and a name
    that suddenly matches two files means Streamlit split it -- either way the
    list was written against a build that is gone. The code skips a name it
    cannot resolve (degrading to today's behaviour, never to a 404 the worker
    would read as a stale pair), so nothing breaks; this test is the only
    thing that would ever SAY so."""
    js = boot_shell._index_path().parent / "static" / "js"
    assert js.is_dir(), "streamlit static/js not found - venv install is broken"
    for name in boot_shell._PRELOAD_CHUNKS:
        hits = sorted(p.name for p in js.glob(name + ".*.js"))
        assert len(hits) == 1, (
            "chunk %r resolves to %d files (%s) - the preload list was written "
            "against a different Streamlit build" % (name, len(hits), hits)
        )


def test_hint_fetch_mode_matches_the_entry_script():
    """Streamlit's entry tag is <script type="module" crossorigin>, so the
    bundle graph is fetched in CORS mode. A preload whose mode disagrees is
    not a hint for that fetch at all -- it is a SECOND download of the same
    bytes, which is worse than having no hint."""
    src = _index_text()
    entry = re.search(r"<script[^>]*static/js/index[^>]*>", src)
    assert entry, "no entry <script> in index.html"
    assert "crossorigin" in entry.group(0), (
        "the entry script lost its crossorigin attribute - the hints must "
        "follow it or every chunk downloads twice"
    )
    links = boot_shell._modulepreload_links()
    assert links, "no preload hints were generated"
    for link in re.findall(r"<link[^>]*class=\"cai-preload\"[^>]*>", links):
        assert "crossorigin" in link, "hint without crossorigin: " + link
        assert 'rel="modulepreload"' in link, "wrong rel: " + link


def test_hints_land_after_the_split_and_never_grow_the_first_chunk():
    """THE load-bearing one. The service worker flushes everything up to and
    including SPLIT, holds, then sends the rest -- and that first chunk is a
    complete paintable screen whose size is the whole launch-flash argument
    (see _SW_JS). Hints in <head> would ride in it. They belong at the top of
    the TAIL: the splash is already on the glass, and the entry <script> up in
    the head has had the length of the hold as a head start."""
    assert boot_shell.patch_index_html(), "patch_index_html refused to write"
    src = _index_text()
    assert SPLIT in src, "no split marker - the worker would serve unheld"
    head_chunk = src.split(SPLIT)[0]
    assert "cai-preload" not in head_chunk, (
        "a preload hint drifted into the first streamed chunk"
    )
    assert src.count('class="cai-preload"') == len(
        re.findall(r"cai-preload", boot_shell._modulepreload_links())
    ), "the patched file does not carry every generated hint"


def test_the_worker_precache_regex_sees_the_hints():
    """The worker pulls the bundle out of the cached document with its own
    regex, so a cached index.html can never name an asset the cache lacks.
    Our href form has to be one it matches, or the hints would be the first
    URLs in the document that the worker is blind to."""
    worker_re = re.compile(r'(?:src|href)="\.(/static/[^"]+)"')
    links = boot_shell._modulepreload_links()
    found = worker_re.findall(links)
    assert len(found) == links.count("<link"), (
        "the worker's precache regex matches %d of %d hints - href form drifted"
        % (len(found), links.count("<link"))
    )


def test_strip_round_trips_byte_exactly():
    """A patch that cannot be removed exactly is a patch that stacks: every
    re-patch would leave one more copy of the hints behind. This is the same
    guard that caught an off-by-two-space indent bug on 2026-07-29."""
    pristine = boot_shell._strip(_index_text())
    assert "cai-preload" not in pristine, "_strip left a hint behind"
    assert boot_shell.patch_index_html(), "patch_index_html refused to write"
    assert boot_shell._strip(_index_text()) == pristine, (
        "strip(patch(x)) != x - the hints do not round-trip byte-exactly"
    )


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
