"""What is still missing from the download list, and what just landed.

Acquisition is the one manual step in this pipeline — the orders site serves its
index to a fetcher but not its content, and this project does not work around
that. So the job here is to make the manual part cheap: after any batch of
downloads, this says exactly which orders arrived, which are still outstanding,
and which of the outstanding ones are worth doing next.

    python -m night.progress

It reads DOWNLOAD_LINKS.md rather than a hand-kept list, so it cannot drift out
of step with what was actually handed over.
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

# Windows consoles default to cp1252, which cannot encode a single Hebrew
# letter — every print here would die on its first character.
# reconfigure rather than re-wrap: a second TextIOWrapper over the same buffer
# closes the first when it is collected, which kills stdout for anything that
# imports two of these modules at once.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
LINKS = ROOT / "DOWNLOAD_LINKS.md"
PDFS = ROOT / "pdf-ldf_law"

# "- ⭐ [33.0112](https://…) — מתנות, טובות הנאה…"
ROW = re.compile(r"^-\s*(⭐\s*)?\[(\d{1,2}\.\d{4})\]\((\S+?)\)\s*—\s*(.*)$")
HEAD = re.compile(r"^##\s*(.+?)\s*—")


def on_disk() -> set[str]:
    """Order numbers already downloaded, however the file happens to be named.

    Filenames arrive in three shapes — 330109.pdf, 33-0109-title.pdf, and
    Hebrew-titled ones ending in the digits — so match on the digit run rather
    than on any one convention.
    """
    out = set()
    for p in PDFS.glob("*.pdf"):
        for run in re.findall(r"\d{5,6}", p.stem):
            if len(run) == 6:
                out.add(f"{run[:2]}.{run[2:]}")
            elif len(run) == 5:
                out.add(f"{run[:1]}.{run[1:]}")
    return out


def wanted() -> list[dict]:
    if not LINKS.exists():
        raise SystemExit(f"no {LINKS.name} — nothing to track against")
    rows, section = [], ""
    for line in LINKS.read_text(encoding="utf-8").splitlines():
        h = HEAD.match(line)
        if h:
            section = h.group(1)
            continue
        m = ROW.match(line.strip())
        if m:
            rows.append({"num": m.group(2), "starred": bool(m.group(1)),
                         "url": m.group(3), "title": m.group(4), "family": section})
    return rows


def main() -> None:
    have, rows = on_disk(), wanted()
    done = [r for r in rows if r["num"] in have]
    todo = [r for r in rows if r["num"] not in have]
    star_todo = [r for r in todo if r["starred"]]

    pct = 100 * len(done) / max(1, len(rows))
    print(f"הגיעו {len(done)} מתוך {len(rows)} ({pct:.0f}%) · נותרו {len(todo)}")
    print(f"מהנותרות, מדורגות ⭐: {len(star_todo)}")
    print(f"סה\"כ PDF בתיקייה: {len(list(PDFS.glob('*.pdf')))}")

    if not todo:
        print("\nהרשימה הושלמה. להריץ: python -m night.intake")
        return

    by_fam: dict[str, list[dict]] = {}
    for r in todo:
        by_fam.setdefault(r["family"], []).append(r)
    print("\nמה שנותר, לפי משפחה:")
    for fam, rs in sorted(by_fam.items(), key=lambda kv: -sum(x["starred"] for x in kv[1])):
        s = sum(1 for x in rs if x["starred"])
        print(f"  {fam:<28} {len(rs):>3} נותרו ({s} מדורגות)")

    print("\nהבאות בתור:")
    for r in star_todo[:12]:
        print(f"  {r['num']}  {r['title'][:56]}")
        print(f"     {r['url']}")


if __name__ == "__main__":
    main()
