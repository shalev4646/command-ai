"""Publish the corpus PDFs under ``static/`` so Streamlit serves them directly.

Why this exists
---------------
The orders list in the drawer needs one URL per order. It used to get them from
Streamlit's media file manager (``media_file_mgr.add(bytes, ...)``), which means
the *bytes* of every order have to be in the server's hands before the list can
render. Measured on the corpus (80 PDFs / 52.5 MB) that is **~2.9 s of blocking
disk reads** the first time the list opens, plus 52.5 MB held resident for the
cache's lifetime — on a 1024 MB machine that already OOM'd once. Users saw it as
"sometimes it just hangs" (2026-07-30 device video).

Serving the same files as ordinary static assets costs the app nothing: the
server hands the path to the OS and streams it, so rendering a link is free and
no PDF is ever loaded into the Python heap. That in turn is what lets the list be
rendered on every drawer paint, which is what lets the accordion be a pure CSS
toggle with no rerun (see the drawer section in app.py).

Why a mirror instead of pointing at ``pdf-ldf_law/``
---------------------------------------------------
Streamlit resolves the request path with ``os.path.realpath`` and rejects
anything that lands outside the static root, so a symlink to the real corpus
directory answers 400, not 200 (verified against streamlit 1.58's
``build_safe_abspath``). The files have to *be* inside ``static/``. Hard links
make that free where the filesystem allows it, and the copy fallback keeps
Windows dev and exotic mounts working.

``sync()`` is called at import from app.py (a no-op costing ~80 stat calls once
the mirror is in place) AND baked at Docker build time, so a cold container
serves orders without doing the linking on the first user's request.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# Source of truth for the corpus — the same directory backend.get_pdf_bytes reads.
PDF_DIR = Path(__file__).parent / "pdf-ldf_law"
# Streamlit's app-static root: files here are served at /app/static/<name>.
STATIC_DIR = Path(__file__).parent / "static"


def _link_or_copy(src: Path, dst: Path) -> None:
    """Hard-link src to dst, falling back to a copy.

    A hard link costs one inode and no bytes, which matters because the corpus
    is 52 MB and would otherwise be duplicated in the image. It fails across
    filesystems and on some mounts — a copy is always correct, just fatter.
    """
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def sync() -> set[str]:
    """Mirror every corpus PDF into ``static/`` and return the filenames present.

    Idempotent and self-healing: a mirrored file is refreshed when its size no
    longer matches the source (an order re-published in place under the same
    name), and mirrored files whose source is gone are removed so the list can
    never link to a withdrawn order. Never raises — a mirror that cannot be
    written costs the PDF links, not the app, and the caller degrades to
    "no link on this row".
    """
    published: set[str] = set()
    try:
        STATIC_DIR.mkdir(parents=True, exist_ok=True)
        sources = {p.name: p for p in PDF_DIR.glob("*.pdf")}
    except OSError:
        return published

    for name, src in sources.items():
        dst = STATIC_DIR / name
        try:
            if dst.exists():
                if dst.stat().st_size == src.stat().st_size:
                    published.add(name)
                    continue
                dst.unlink()          # stale mirror of a re-published order
            _link_or_copy(src, dst)
            published.add(name)
        except OSError:
            continue                  # this order simply gets no PDF link

    # withdraw mirrors whose source PDF is gone
    try:
        for stale in STATIC_DIR.glob("*.pdf"):
            if stale.name not in sources:
                stale.unlink(missing_ok=True)
    except OSError:
        pass

    return published


if __name__ == "__main__":
    print(f"pdf_static: published {len(sync())} PDFs to {STATIC_DIR}")
