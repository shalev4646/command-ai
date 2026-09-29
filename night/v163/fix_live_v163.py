# -*- coding: utf-8 -*-
"""v163 — wording corrections to LIVE curated clauses that a definition cannot reach (the
add-mode merge of night/recurate/apply_defs keeps an existing clause and skips a new one with
the same heading). Read on the page image by session A; the same shape as night/v161:
  REPLACE        [(title prefix, old, new, source)] — a surgical replacement, asserted to hit once
  PAGE_VERIFIED  [(title prefix, numbers, source)] — numbers of a clause read on the page, recorded in
                 recurated.page_verified_numbers so the support audit stops counting them
                 (coverage_audit.page_verified); no text changes, nothing to re-index
Gated by night.curate.check; re-indexed locally (no API). Runs in the current tree.
Without --write: a dry run that prints the gates only.

    venv\\Scripts\\python.exe night/v163/fix_live_v163.py [--write] [doc_id ...]

What reached the v163 corpus: 58.0202 (REPLACE) and 36.0406 (PAGE_VERIFIED). The 33-05-01 REPLACE
was measured in v163b2 and superseded by the full definition (v163 = b3, whose nail clause carries
§103), so it is not in the corpus — it stays here as the record of what b2 measured.
"""
import json
import sys
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
from night.rehearse import doc_path  # noqa: E402
from night.curate import check  # noqa: E402

WRITE = "--write" in sys.argv
ONLY = [a for a in sys.argv[1:] if not a.startswith("--")]

REPLACE: dict[str, list[tuple[str, str, str, str]]] = {
    # 58.0202 §45ב(2), page 18: „אם הורשע חייל בעבירת תנועה נוספת, שנעברה בתוך שישה חודשים ממועד
    # הפסילה כאמור בסעיף קטן 1 — ייפסל רישיונו לתקופה של שנה אחת". The six months bound when the
    # offence was COMMITTED; the live clause bound the conviction (session B, 29.09; the manager's
    # decision to fix it in v163).
    "58.0202": [(
        "פסילת רישיון צבאי על הצטברות עבירות תנועה",
        "הורשע בעבירה נוספת בתוך שישה חודשים ממועד הפסילה",
        "הורשע בעבירת תנועה נוספת שנעברה בתוך שישה חודשים ממועד הפסילה",
        "עמ' 18, סע' 45ב(2): „בעבירת תנועה נוספת, שנעברה בתוך שישה חודשים ממועד הפסילה\"",
    )],
    # 33-05-01 §101 and §103, page 21. The live clause gave the mandatory AND the career female soldier „clear,
    # light pink or pearl white ONLY"; §103 adds, for career service only, red, bordeaux, every pink, light
    # purple, light brown, light gray and black — a career soldier asking about red polish got a confident "no".
    # Found by session A (29.09) reading the live v161 blocks of the two orders v163 holds out (33-05-01,
    # 35.0210) on the page at the manager's request; 35.0210's five live clauses read clean. The live block
    # stays under the 600-word cut (460 words before).
    "33-05-01": [(
        "לק לציפורניים",
        "בלק שקוף, בלק ורוד בהיר או בלק לבן פנינה בלבד — ללא צבעים זוהרים, זרחניים ומנצנצים.",
        "בלק שקוף, בלק ורוד בהיר או בלק לבן פנינה; חיילת בשירות הקבע בלבד רשאית לצבוע גם בלק אדום, בורדו, "
        "ורוד בכל הגוונים, סגול בהיר, חום בהיר, אפור בהיר ושחור — ובכל מקרה ללא צבעים זוהרים, זרחניים ומנצנצים.",
        "עמ' 21, סע' 103: „בנוסף, חיילת בשירות הקבע בלבד, רשאית לצבוע את ציפורניה בלק בצבעים הבאים: אדום … בורדו, "
        "ורוד בכל הגוונים, סגול בהיר, חום בהיר, אפור בהיר ושחור\"",
    )],
}

PAGE_VERIFIED: dict[str, list[tuple[str, list[str], str]]] = {
    # 36.0406, the wave-10 definition's fertility clause: „חיילים וחיילות בשירות חובה נעדרים בגין טיפולי
    # פוריות לפי פ"מ 04.101" — the support ratchet counted 04.101 as an entity miss (the audit's supporting
    # passage in raw_text is a scrambled one about birth leave). Page 7, §33 carries it.
    "36.0406": [(
        "אני בטיפולי פוריות — כמה ימי היעדרות מגיעים לי",
        ["04.101"],
        "עמ' 7, סע' 33: „חיילים וחיילות בשירות חובה רשאים להיעדר מהשירות בתקופה שבה הם עוברים טיפול פוריות, "
        "על-פי התנאים המפורטים בפ\"מ 04.101 — \"חופשות לחיילים המשרתים בשירות חובה\"\"",
    )],
}


def record(did: str) -> int:
    """PAGE_VERIFIED: append the page-read numbers to recurated.page_verified_numbers (text untouched)."""
    p = doc_path(did)
    d = json.loads(p.read_text(encoding="utf-8"))
    rec = d.setdefault("recurated", {})
    have = list(rec.get("page_verified_numbers", []))
    new = []
    for prefix, nums, src in PAGE_VERIFIED[did]:
        hits = [c for s in d["sections"] if "key-facts" in s["id"]
                for c in s.get("clauses") or [] if c["number"].startswith(prefix)]
        assert len(hits) == 1, (did, prefix, len(hits))
        assert all(n in hits[0]["text"] for n in nums), (did, prefix, nums)
        e = {"clause": prefix, "numbers": nums, "def": "v163 page check (session A, 29.09)", "src": src}
        if e not in have:
            new.append(e)
    print(f"[v163] {did}: {len(new)} page-verified record(s)")
    for e in new:
        print(f"        PAGE    {e['clause'][:40]}: {e['numbers']} ({e['src'][:90]})")
    if not WRITE or not new:
        return 0
    rec["page_verified_numbers"] = have + new
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"        WRITTEN {p.name} — recurated only, no chunk changed")
    return 0


def apply(did: str) -> int:
    p = doc_path(did)
    d = json.loads(p.read_text(encoding="utf-8"))
    edits = []
    for prefix, old, new, src in REPLACE[did]:
        hits = [(s, c) for s in d["sections"] if "key-facts" in s["id"]
                for c in s.get("clauses") or [] if c["number"].startswith(prefix)]
        assert len(hits) == 1, (did, prefix, len(hits))
        sec, clause = hits[0]
        assert clause["text"].count(old) == 1, (did, prefix, old[:40])
        clause["text"] = clause["text"].replace(old, new)
        edits.append((sec, f"{prefix[:30]}: {old!r} -> {new!r} ({src})"))
    problems = []
    for sec in {id(s): s for s, _ in edits}.values():
        pr, _warn = check(sec, d["raw_text"], digit_free=bool(sec.get("digit_free")))
        problems += pr
    print(f"[v163] {did}: {len(edits)} edits · check problems {len(problems)}")
    for x in problems:
        print("        PROBLEM", str(x)[:160])
    for _, note in edits:
        print("        EDIT   ", note[:200])
    if problems or not WRITE:
        return 1 if problems else 0
    d.setdefault("recurated", {})["live_wording_v163_29.09"] = [note for _, note in edits]
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    from storage import vector_store as vs
    print(f"        WRITTEN {p.name} — re-indexed {vs.index_document(d, save_cache=True)} chunks")
    return 0


if __name__ == "__main__":
    todo = ONLY or list(REPLACE) + list(PAGE_VERIFIED)
    raise SystemExit(max([record(did) if did in PAGE_VERIFIED else apply(did) for did in todo] or [0]))
