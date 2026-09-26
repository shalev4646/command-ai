# -*- coding: utf-8 -*-
"""Weekly triage of „not found" answers: was the rule really absent, or did retrieval miss it?

The room question of 27.09 („may a commander enter my locked room through the window…") got
„not in the orders" while 33.0309 §129 and 21.0113 §17 both carry the search authority — the
rule was in the corpus and the order never reached the window. This reads the pilot's question
log, keeps every answer that refused or declared a gap (out_of_scope.declares_gap, or the logged
`refused` flag), and for each one asks two free questions:

  1. retrieval: which orders the free window serves (production flags, route=∅, no HyDE), and
     which orders the unwindowed ranking holds just outside it (sectprobe._global_ranking);
  2. the corpus: which rule units in ANY order's raw_text or block share most of the question's
     content stems (the rule units of night.coverage_audit).

Verdict per question: „ייתכן פספוס" (an order outside the window carries a matching rule, or
ranks just outside it), „בחלון ונענה כחסר" (a matching rule's order WAS served), or „כנראה באמת
אין". The output is for a human to verify; a verified miss goes into night/out/real_misses.json
(--add-miss) and tests/test_real_misses.py keeps it from coming back.

Measured 27.09 on the motivating case: none of the three answering orders is in the top 200 of
the unwindowed ranking, and the rare-word candidates list 21.0113 among eight — not 33.0309 or
33.0220. A gap between the soldier's words and the rule's („entered my room, looked for me" vs
„search") is what the per-clause reach test (layer 1) is for. This tool's job is to put every
refusal of the week in front of a human with its window and its lexical candidates.

Weekly, on the user's machine (a scheduled task, like night/outbox_watch.py):
    schtasks /Create /TN "CommandAI triage" /SC WEEKLY /D SUN /ST 09:00 ^
      /TR "cmd /c cd /d D:\\app_soldier && venv\\Scripts\\python.exe -m night.triage_notfound --sheets --days 7"

Users' question text never enters git: the report is written to night/out/ (gitignored) only.
No model, no money. The Sheet is read with the read-only scope, from the secrets file where it
already is (--secrets; default: this tree's, else the main checkout's) — never copied.

    venv\\Scripts\\python.exe -m night.triage_notfound --sheets                  # the pilot Sheet
    venv\\Scripts\\python.exe -m night.triage_notfound --jsonl storage/metrics_log.jsonl
    venv\\Scripts\\python.exe -m night.triage_notfound --add-miss "…" --targets PM-33.0213 --role soldier
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from night import coverage_audit as ca  # noqa: E402

OUT = ROOT / "night" / "out"
NEAR_DEPTH = 30          # chunks of the unwindowed ranking that count as „just outside the window"
TOP_UNITS = 8
# words that carry no topic in a soldier's question
Q_GENERIC = {"מותר", "אסור", "מגיע", "אפשר", "צריך", "חייב", "יכול", "יכולה", "האם", "חייל", "חיילת", "מפקד",
             "שלי", "אותי", "אותו", "לעשות", "קורה", "עושים", "למה", "איך", "כמה", "מתי", "צבא", "בצבא", "יש",
             "היה", "היתה", "הייתי", "שאפשר", "אפשרי", "רוצה", "רציתי", "צריכה", "מישהו", "משהו", "גם", "עכשיו"}


# ── the rows ────────────────────────────────────────────────────────────────
def main_checkout() -> Path | None:
    """The main worktree (its night/out holds the shared, gitignored data)."""
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        return Path(common).parent if common else None
    except Exception:
        return None


def default_secrets() -> Path | None:
    for base in (ROOT, main_checkout()):
        if base and (base / ".streamlit" / "secrets.toml").exists():
            return base / ".streamlit" / "secrets.toml"
    return None


def rows_from_sheets(secrets: Path | None) -> list[dict]:
    import _triage_feedback as tf
    rows = tf._question_rows_from_sheets(secrets_path=secrets)
    return rows


def rows_from_jsonl(path: Path) -> list[dict]:
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [r for r in rows if r.get("tab", "questions") == "questions"]


def _truthy(v) -> bool:
    return v is True or str(v).strip().lower() in ("true", "1", "yes")


def select(rows: list[dict]) -> list[dict]:
    """Answers that refused or declared a gap — the ones a retrieval miss hides behind."""
    from out_of_scope import declares_gap
    out = []
    for r in rows:
        q = str(r.get("question") or "").strip()
        if not q:
            continue
        kind = "refused" if _truthy(r.get("refused")) else declares_gap(str(r.get("answer_preview") or ""))
        if kind:
            out.append({**r, "gap": kind})
    return out


# ── the corpus side ─────────────────────────────────────────────────────────
# Rare question words, not overlap shares. Measured on the motivating case (27.09): the room
# question shares ONE meaningful word with the search rules („חיפשו" / „לחפש", „חיפוש"), and none
# of the three answering orders is anywhere in the top 200 of the unwindowed ranking — so neither a
# share threshold nor „just outside the window" can see it. A word that few orders use, found in a
# rule of an order outside the window, can. Recall first: a human reads this list every week.
MAX_DF = 60              # a word used by more orders than this says nothing about which one answers


def _bases(w: str) -> set[str]:
    """The word and up to two prefix letters stripped („מהחלון" -> „החלון" -> „חלון")."""
    out = {w}
    for _ in range(2):
        out |= {b[1:] for b in out if len(b) > 3 and b[0] in "ובלמהשכ"}
    return out


def _word_forms(w: str) -> set[str]:
    """Stems of one word (4+ letters — shorter ones match everything), plus the defective spelling
    of each base: a soldier writes „חיפשו", the order „לחפש" / „חיפוש" — without the inner ו/י
    all are „חפש"."""
    out: set[str] = set()
    for base in _bases(w):
        out |= {f for f in ca._stems(base) if len(f) >= 5}   # shorter suffix-strips („החלו") match anything
        if len(base) >= 3:
            out.add(base.translate(ca._FINALS))      # a three-letter root („נהג", „חדר") is itself
        if len(base) >= 4:
            d = base[0] + re.sub(r"[וי]", "", base[1:])
            if len(d) >= 3:
                out.add(d.translate(ca._FINALS))
    return out


def _forms(text: str) -> set[str]:
    out: set[str] = set()
    for w in re.findall(r"[א-ת]{3,}", text or ""):
        out |= _word_forms(w)
    return out


def q_words(q: str) -> list[str]:
    """The question's content words, one per stem group, generic question words dropped."""
    out: list[str] = []
    seen: set[str] = set()
    for w in re.findall(r"[א-ת\"״']{3,}", q or ""):
        w = w.strip("\"'״")
        bare = w[1:] if len(w) > 3 and w[0] in "ובלמהשכ" else w
        if (w in Q_GENERIC or bare in Q_GENERIC or w in ca._STOP
                or len(re.sub(r"[^א-ת]", "", w)) < 3):
            continue
        f = _word_forms(w)
        if f & seen:
            continue                      # „לחדר" and „החדר" are one word
        seen |= f
        out.append(w)
    return out


def q_stems(q: str) -> set[str]:
    return _forms(" ".join(q_words(q)))


def build_index(docs: list[dict] | None = None) -> dict:
    """Every rule unit of every order's raw_text and every block clause, with its forms, and the
    orders each form occurs in (for the rare-word test)."""
    docs = docs if docs is not None else ca.load_corpus()
    units: list[dict] = []
    df: dict[str, set[str]] = {}
    for d in docs:
        did = d.get("document_id")
        for f in _forms(d.get("raw_text") or ""):
            df.setdefault(f, set()).add(did)
        for u in ca.rule_units(d.get("raw_text") or ""):
            if not u["normative"] or u["admin"] or len(ca.content_words(u["text"])) < ca.MIN_CONTENT:
                continue
            units.append({"doc": did, "src": "raw", "text": u["text"], "stems": _forms(u["text"])})
        for c in ca.block_clauses(d):
            t = f"{c['number']} {c['text']}"
            units.append({"doc": did, "src": "block", "text": t, "stems": _forms(t)})
    return {"units": units, "df": df, "n_docs": len(docs)}


def lexical_hits(q: str, index: dict) -> list[dict]:
    """Per order, the rule unit (normative raw unit or block clause) whose matched question words
    are rarest in the corpus (sum of log(N/df)). Words used by more than MAX_DF orders are dropped."""
    import math
    n = max(index.get("n_docs") or 1, 1)
    weights: dict[str, float] = {}
    forms: dict[str, set[str]] = {}
    for w in q_words(q):
        f = _word_forms(w)
        docs = set().union(*(index["df"].get(x, set()) for x in f)) if f else set()
        if 0 < len(docs) <= MAX_DF:
            weights[w], forms[w] = math.log(n / len(docs)), f
    if not weights:
        return []
    hits = []
    for u in index["units"]:
        m = [w for w in weights if forms[w] & u["stems"]]
        if not m:
            continue
        hits.append({"doc": u["doc"], "src": u["src"], "rare": m, "score": round(sum(weights[w] for w in m), 2),
                     "text": " ".join(u["text"].split()[:40])})
    hits.sort(key=lambda h: (-h["score"], h["src"] != "block"))
    best: dict[str, dict] = {}
    for h in hits:
        best.setdefault(h["doc"], h)
    return list(best.values())[:TOP_UNITS]


# ── the retrieval side ──────────────────────────────────────────────────────
def _prod_env() -> None:
    for k, v in ca.V159.items():
        os.environ[k] = v
    os.environ.pop("ANTHROPIC_API_KEY", None)


def window_docs(q: str, role: str) -> list[str]:
    from night import sectprobe as sp
    out: list[str] = []
    for c in sp._free_window(q, role):
        if c["doc_id"] not in out:
            out.append(c["doc_id"])
    return out


def near_docs(q: str, role: str, served: list[str]) -> list[tuple[str, int]]:
    from night import sectprobe as sp
    seen: dict[str, int] = {}
    for rank, c in enumerate(sp._global_ranking(q, role)[:NEAR_DEPTH], 1):
        if c["doc_id"] not in served and c["doc_id"] not in seen:
            seen[c["doc_id"]] = rank
    return list(seen.items())[:TOP_UNITS]


def analyze(row: dict, index: list[dict], window_fn=window_docs, near_fn=near_docs) -> dict:
    q, role = str(row["question"]), str(row.get("role") or "soldier")
    served = window_fn(q, role)
    lex = lexical_hits(q, index)
    near = near_fn(q, role, served)
    outside = [h for h in lex if h["doc"] not in served]
    inside = [h for h in lex if h["doc"] in served]
    if outside:
        verdict = "ייתכן פספוס"
    elif inside:
        verdict = "בחלון ונענה כחסר"
    else:
        verdict = "כנראה באמת אין"
    return {"ts": row.get("ts"), "question": q, "role": role, "gap": row.get("gap"), "verdict": verdict,
            "window": served, "near": near, "lexical": lex}


# ── the real-miss ratchet set ───────────────────────────────────────────────
def misses_path(create: bool = False) -> Path:
    for base in (ROOT, main_checkout()):
        if base and (base / "night" / "out" / "real_misses.json").exists():
            return base / "night" / "out" / "real_misses.json"
    base = main_checkout() or ROOT
    return base / "night" / "out" / "real_misses.json"


def add_miss(q: str, targets: list[str], role: str, status: str, note: str) -> dict:
    p = misses_path()
    rows = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    entry = {"id": f"rm{len(rows) + 1:03d}", "q": q, "role": role, "targets": targets, "status": status,
             "added": date.today().isoformat(), "note": note}
    rows.append(entry)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    return entry


# ── report ──────────────────────────────────────────────────────────────────
def write_report(results: list[dict], tag: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"triage_notfound_{tag}.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    L = [f"# טריאז' „לא נמצא״ — {tag}", "", f"{len(results)} תשובות שסירבו או הצהירו על חוסר.", ""]
    for verdict in ("ייתכן פספוס", "בחלון ונענה כחסר", "כנראה באמת אין"):
        rs = [r for r in results if r["verdict"] == verdict]
        L += [f"## {verdict} ({len(rs)})", ""]
        for r in rs:
            L.append(f"**{r['question']}** ({r['role']}, {r['gap']}, {r['ts']})")
            L.append(f"- בחלון: {', '.join(r['window'][:8]) or '—'}")
            if r["near"]:
                L.append("- ממש מחוץ לחלון: " + ", ".join(f"{d} (#{k})" for d, k in r["near"]))
            for h in r["lexical"]:
                where = "בחלון" if h["doc"] in r["window"] else "**לא בחלון**"
                L.append(f"- {h['doc']} [{h['src']}, {h['score']}: {', '.join(h['rare'])}, {where}]: „{h['text']}״")
            L.append("")
    L += ["---", "פספוס שאומת: `python -m night.triage_notfound --add-miss \"<ניסוח>\" --targets <פקודה> --role <תפקיד>`",
          "(status pending עד שהתיקון נכנס; active כשהפקודה כבר אמורה להגיע — tests/test_real_misses.py נועל)."]
    p = OUT / f"triage_notfound_{tag}.md"
    p.write_text("\n".join(L) + "\n", encoding="utf-8")
    return p


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--sheets", action="store_true")
    src.add_argument("--jsonl")
    ap.add_argument("--secrets", help="path to the existing .streamlit/secrets.toml (default: found, never copied)")
    ap.add_argument("--since", help="only rows with ts >= this (YYYY-MM-DD)")
    ap.add_argument("--days", type=int, help="only the last N days (the weekly run: --days 7)")
    ap.add_argument("--add-miss")
    ap.add_argument("--targets", default="")
    ap.add_argument("--role", default="soldier")
    ap.add_argument("--status", default="active", choices=("active", "pending"))
    ap.add_argument("--note", default="")
    a = ap.parse_args(argv)
    if a.add_miss:
        e = add_miss(a.add_miss, [t for t in a.targets.split(",") if t], a.role, a.status, a.note)
        print(f"[real_misses] {e['id']} added ({e['status']}, targets {e['targets']}) -> {misses_path()}")
        return 0
    if a.sheets:
        rows = rows_from_sheets(Path(a.secrets) if a.secrets else default_secrets())
    elif a.jsonl:
        rows = rows_from_jsonl(Path(a.jsonl))
    else:
        ap.error("--sheets or --jsonl")
    since = a.since
    if a.days:
        from datetime import timedelta
        since = (date.today() - timedelta(days=a.days)).isoformat()
    if since:
        rows = [r for r in rows if str(r.get("ts") or "") >= since]
    picked = select(rows)
    print(f"[triage] {len(rows)} questions, {len(picked)} refused or declared a gap")
    if not picked:
        return 0
    _prod_env()
    index = build_index()
    results = [analyze(r, index) for r in picked]
    p = write_report(results, date.today().isoformat())
    counts = {v: sum(1 for r in results if r["verdict"] == v) for v in ("ייתכן פספוס", "בחלון ונענה כחסר", "כנראה באמת אין")}
    print(f"[triage] {counts} -> {p}  (local only; never commit it)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
