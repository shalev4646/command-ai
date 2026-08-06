"""Classify pilot thumbs-down feedback into the three failure classes.

A "bad answer" report is not actionable on its own — the fix is in a
different layer depending on *why* it was bad, and the three layers cost
wildly different amounts to fix:

    corpus     the orders simply don't cover what was asked
    retrieval  the answer is in the corpus, but the wrong orders were fetched
    answer     the right order was fetched and the wording still missed

This script decides which, from evidence already in the log, with no LLM
call and no API key.

The discriminator is the same term-matching rule retrieval itself uses
(`_term_variants`: prefix stripping, light stemming, finals folding), so a
verdict here means the same thing it would mean inside `retrieve`:

  * a topic word that matches nothing anywhere in the corpus  -> corpus gap
    (this is the 2026-08-06 dog-tag case: "דיסקית" is spelled "דסקית" and
    the obligation it asks about isn't in any of the 82 orders at all)
  * topic words that live in some order, but that order is absent from the
    row's own doc_ids                                          -> retrieval
  * topic words present and their orders were fetched          -> answer

"Topic word" is decided by document frequency rather than a hand-kept
stoplist: a word in most orders discriminates nothing, so it can't be the
reason the answer failed. That way the filter follows the corpus instead of
needing maintenance alongside it.

Known limits, so the counts aren't over-read:

  * A question with no discriminating word at all (a short follow-up like
    "אז למי מותר להחריג?") lands in `answer` by default. There is nothing in
    it to look up, so the bucket means "can't tell from the question", not
    "the answer layer is at fault". Read those rows individually.
  * Rarity is a proxy for topicality, and it occasionally promotes a generic
    word that just happens to be rare in 82 orders.
  * `corpus` means the word is absent *under the retrieval matching rule* —
    prefix stripping runs on the query, never on the corpus, so a question
    saying "טכנאי" does not reach an order that only writes "טכנאים". That
    is exactly what retrieval sees, which is the point, but it means a
    `corpus` verdict can also be read as "the matcher can't bridge these two
    forms" rather than "this subject is missing entirely".

Usage:
    python _triage_feedback.py                # local storage/metrics_log.jsonl
    python _triage_feedback.py --sheets       # the pilot's Google Sheet
    python _triage_feedback.py --all          # include 'up' rows too
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from storage.vector_store import _FINALS, _term_variants  # the live matching rule

# the whole report is Hebrew; a Windows console defaults to cp1252 and dies on
# the first line rather than the hundredth
sys.stdout.reconfigure(encoding="utf-8")

_ROOT = Path(__file__).parent
_JSONL = _ROOT / "storage" / "metrics_log.jsonl"
_JSON_STORE = _ROOT / "storage" / "json_store"

# A word carried by more than this share of the orders explains nothing about
# why one answer failed — "חייל" is in nearly every order. Tuned to the corpus
# size (82 orders), not to a fixed word list. Kept tight: at 0.25 a merely
# frequent word like "שעות" still qualified and dragged a dozen unrelated
# orders into the evidence, which reads as a finding and isn't one.
_COMMON_DOC_SHARE = 0.10

# The retrieval verdict the app prints when it finds nothing worth citing.
_NOT_FOUND_MARKS = ("לא נמצא במאגר", "המידע לא קיים")


def _load_corpus() -> dict[str, dict]:
    """doc_id -> {title, forms}. `forms` is every match-form appearing in the
    document, folded exactly like a query word, so membership tests here and
    ranking there agree."""
    corpus: dict[str, dict] = {}
    for path in sorted(_JSON_STORE.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc_id = doc.get("document_id")
        if not doc_id:
            continue
        # the whole record, not just raw_text: anchors and key-facts are as
        # retrievable as the order body, so a word living only in an anchor
        # still counts as present in the corpus
        blob = json.dumps(doc, ensure_ascii=False)
        forms = {
            w.strip("?.,:;!\"'()[]{}").translate(_FINALS)
            for w in blob.replace("\\n", " ").split()
        }
        title = doc.get("title", "")
        # Titles get the variant expansion applied to *them* as well, not just
        # to the query word. Prefix stripping is one-directional — a bare
        # "שינה" in the question never reaches "השינה" in the title — and a
        # title is small enough that expanding it costs nothing. (The body
        # index stays raw: ~400K words is too many to expand, and there the
        # sheer number of forms makes a hit likely anyway.)
        title_forms: set[str] = set()
        for w in title.split():
            title_forms |= _term_variants(w) or {_strip(w)}
        corpus[doc_id] = {
            "title": title,
            "forms": {f for f in forms if len(f) >= 2},
            "title_forms": title_forms,
        }
    return corpus


def _strip(word: str) -> str:
    """The word as the matcher sees it — for display and for keying, so
    "שינה?" and "שינה" don't count as two different topic words."""
    return word.strip("?.,:;!\"'()[]{}").translate(_FINALS)


def _docs_containing(word: str, corpus: dict[str, dict]) -> set[str]:
    """Orders whose text contains `word` in any of its match-forms."""
    variants = _term_variants(word)
    if not variants:
        return set()
    return {d for d, rec in corpus.items() if variants & rec["forms"]}


def _in_title(word: str, doc_id: str, corpus: dict[str, dict]) -> bool:
    variants = _term_variants(word)
    return bool(variants and variants & corpus[doc_id]["title_forms"])


def classify(question: str, retrieved: list[str], corpus: dict[str, dict]) -> dict:
    """Which layer is at fault, plus the evidence that says so."""
    n_docs = len(corpus) or 1
    unknown: list[str] = []
    carriers_by_word: dict[str, set[str]] = {}

    for raw in question.split():
        if not _term_variants(raw):
            continue  # too short to carry meaning under the retrieval rule
        word = _strip(raw)
        docs = _docs_containing(word, corpus)
        if not docs:
            unknown.append(word)
        elif len(docs) / n_docs <= _COMMON_DOC_SHARE:
            carriers_by_word[word] = docs

    # Score orders by how many of the question's topic words they carry, and
    # keep only the best-covering ones. The union would nominate any order
    # holding a single topic word, which on a two-word question is most of the
    # corpus — the intersection is what actually names a candidate.
    #
    # A title hit counts double. On a one-topic-word question every carrier
    # ties at 1 and the "evidence" degenerates into a list of everywhere the
    # word happens to appear; the order that puts it in its *title* is the one
    # actually about it ("שעות השינה של חיילים בצה\"ל" vs an order that
    # mentions sleep in passing).
    coverage: Counter[str] = Counter()
    for word, docs in carriers_by_word.items():
        coverage.update(docs)
        coverage.update(d for d in docs if _in_title(word, d, corpus))
    best = max(coverage.values(), default=0)
    candidates = {d for d, n in coverage.items() if n == best} if best else set()

    if unknown:
        verdict = "corpus"
    elif not candidates:
        # nothing in the question discriminates between orders — too generic
        # to pin on retrieval, so read it as an answer-layer issue
        verdict = "answer"
    elif candidates & set(retrieved):
        verdict = "answer"
    else:
        verdict = "retrieval"

    return {
        "verdict": verdict,
        "unknown": unknown,
        "topic": sorted(carriers_by_word, key=lambda w: len(carriers_by_word[w])),
        # orders carrying the most topic words that never reached the model —
        # only meaningful when retrieval is the one being blamed
        "missed": sorted(candidates - set(retrieved))[:5] if verdict == "retrieval" else [],
        "no_docs": not retrieved,
    }


def _rows_from_jsonl() -> list[dict]:
    if not _JSONL.exists():
        sys.exit(f"אין קובץ יומן ב-{_JSONL}")
    rows = [json.loads(line) for line in _JSONL.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("tab") == "feedback"]


def _rows_from_sheets() -> list[dict]:
    """The pilot's real feedback. Reads the service account straight out of
    .streamlit/secrets.toml — st.secrets needs a Streamlit runtime, and this
    is a command-line tool."""
    try:
        import tomllib

        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as e:
        sys.exit(f"חסרה חבילה למסלול הגיליון: {e.name}  (pip install gspread google-auth)")

    secrets_path = _ROOT / ".streamlit" / "secrets.toml"
    if not secrets_path.exists():
        sys.exit(f"אין {secrets_path}")
    secrets = tomllib.loads(secrets_path.read_text(encoding="utf-8"))
    info, url = secrets.get("gcp_service_account"), secrets.get("metrics", {}).get("sheet_url")
    if not info or not url:
        sys.exit("חסר gcp_service_account או metrics.sheet_url ב-secrets.toml")

    creds = Credentials.from_service_account_info(
        dict(info), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    ws = gspread.authorize(creds).open_by_url(url).worksheet("feedback")
    return ws.get_all_records()


def main() -> int:
    ap = argparse.ArgumentParser(description="סיווג פידבק הפיילוט לשלוש מחלקות-כשל")
    ap.add_argument("--sheets", action="store_true", help="לקרוא מהגיליון במקום מהיומן המקומי")
    ap.add_argument("--all", action="store_true", help="לכלול גם אגודל-למעלה")
    args = ap.parse_args()

    rows = _rows_from_sheets() if args.sheets else _rows_from_jsonl()
    if not args.all:
        rows = [r for r in rows if str(r.get("verdict", "")).lower() in ("down", "comment")]
    if not rows:
        print("אין רשומות פידבק שליליות — אין מה לסווג.")
        return 0

    corpus = _load_corpus()
    print(f"קורפוס: {len(corpus)} פקודות | רשומות לסיווג: {len(rows)}\n")

    label = {
        "corpus": "פער-תוכן — צריך להטמיע פקודה",
        "retrieval": "אחזור — התשובה במאגר ולא הובאה",
        "answer": "שכבת-התשובה — הפקודה הנכונה כן הובאה",
    }
    tally: Counter[str] = Counter()

    for r in rows:
        q = str(r.get("question", "")).strip()
        retrieved = [d.strip() for d in str(r.get("doc_ids", "")).split(",") if d.strip()]
        res = classify(q, retrieved, corpus)
        tally[res["verdict"]] += 1

        print(f"[{res['verdict']:<9}] {q[:80]}")
        print(f"   {label[res['verdict']]}")
        if str(r.get("comment", "")).strip():
            print(f"   תגובת המשתמש: {str(r['comment']).strip()[:90]}")
        if res["unknown"]:
            print(f"   מילים שאין להן זכר בקורפוס: {', '.join(res['unknown'])}")
        if res["topic"]:
            print(f"   מילות-הנושא: {', '.join(res['topic'])}")
        if res["missed"]:
            named = ", ".join(f"{d} ({corpus[d]['title'][:34]})" for d in res["missed"])
            print(f"   מחזיקות אותן ולא אוחזרו: {named}")
        print(f"   אוחזרו: {', '.join(retrieved) or '— (כלום)'}")
        if any(m in str(r.get("answer_preview", "")) for m in _NOT_FOUND_MARKS):
            print("   התשובה הודתה שלא מצאה")
        print()

    print("=" * 64)
    for verdict, n in tally.most_common():
        print(f"{n:>3}  {label[verdict]}")
    print("=" * 64)
    print("הרוב ב'אחזור' ⇒ הרחבת-שאילתה/מקודד. הרוב ב'פער-תוכן' ⇒ הכסף הולך לקורפוס.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
