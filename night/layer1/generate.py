# -*- coding: utf-8 -*-
"""Layer 1, step 1 — user questions for every curated clause (paid, Haiku; night/layer1/CRITERION.md).

Every clause of every curated block gets three questions that a user from the order's audience
would type, written by Haiku with the clause in the prompt — inside-out like night/genq.py, but
per clause, because the final test's misses were "the order arrived without the clause". The
reach check that consumes them (night/layer1/reach.py) is free; this is the only paid step.

Units, the held split and the prompt are fixed here before any run, so a re-run reproduces the
same set. It runs from session A's tree (the one with .env) after the v162 corpus write; the
units file carries a fingerprint of the corpus it was cut from, and reach.py refuses another.

    python -m night.layer1.generate --dry           # counts and the dollar estimate; no API
    python -m night.layer1.generate --pilot 40      # 40 dev clauses, synchronous
    python -m night.layer1.generate --full          # every clause, Batches API
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common import safe_print  # noqa: E402
from night.ledger import Ledger, cost_usd  # noqa: E402

STORE = ROOT / "storage" / "json_store"
OUT = ROOT / "night" / "layer1" / "out"
MODEL = "claude-haiku-4-5-20251001"
PER_REQUEST = 6          # clauses of one order per request; one order title, one instruction block
MAX_TOKENS = 1500
CAP_USD = 4.00           # the whole of layer 1: pilot, full run, retries (CRITERION.md, "מחיר")
HELD_MOD = 5             # 1 clause in 5 is held
SEED = 20260929
# the estimate only: Hebrew runs about 2 characters a token, the instruction block is fixed,
# and a clause's three questions with their JSON come to about 150 tokens (conservative)
CHARS_PER_TOKEN = 2.0
OUT_TOKENS_PER_CLAUSE = 150

PERSONA = {
    "soldier": "חייל או חיילת בשירות סדיר (חובה או קבע)",
    "reserve": "חייל או חיילת במילואים",
    "commander": "מפקד או מפקדת ביחידה (למשל מ\"כ, מ\"מ, מ\"פ או קצין/ת ת\"ש)",
}

PROMPT = """אתה כותב שאלות בדיקה לעוזר דיגיטלי שעונה על שאלות מתוך פקודות מטכ"ל.
השואל: {persona}

לפניך סעיפים מתוך הפקודה „{order_title}". לכל סעיף כתוב שלוש שאלות שונות שהשואל היה מקליד בטלפון, שהתשובה עליהן נמצאת בסעיף הזה.

כללים:
1. שפה של השואל, לא של הפקודה: קצר, ישיר, בגוף ראשון, מותר סלנג וקיצורים צבאיים מקובלים. אל תצטט את הסעיף ואל תעתיק או תנסח מחדש את הכותרת שלו.
2. אל תזכיר מספרי פקודות או סעיפים. בראשי-תיבות כתוב גרשיים עבריים (״) ולא מירכאות (") — צה״ל, שמ״פ, רמטכ״ל, אכ״א.
3. שלוש שאלות בשלושה סגנונות: (א) שאלה קצרה כמו בהודעה; (ב) מצב קצר שקרה לשואל, שנגמר בשאלה; (ג) שאלה על תנאי, חריג, סכום, מועד או מי מאשר — לפי מה שהסעיף באמת קובע.
4. כל שאלה חייבת להיות כזאת שהסעיף הזה עונה עליה — לא רק שאלה על נושא דומה, ולא פרט שהסעיף אינו קובע (סכום, מועד, מידה או תנאי שאינם כתובים בו).
5. אם שום שואל כזה לא היה שואל על הסעיף (נוהל פנימי, טופס, הגדרה טכנית) — החזר עבורו skip עם סיבה קצרה, ורשימת שאלות ריקה. אחרת skip הוא מחרוזת ריקה.

הסעיפים:
{clauses}

החזר JSON בלבד: {{"items": [{{"n": 1, "questions": ["...", "...", "..."], "skip": ""}}]}}"""

PROMPT_FULLTEXT = """אתה כותב שאלות בדיקה לעוזר דיגיטלי שעונה על שאלות מתוך פקודות מטכ"ל.
השואל: {persona}

לפניך כללים מתוך נוסח הפקודה „{order_title}", כל אחד עם הפסקה שהוא נמצא בה. לכל כלל כתוב שלוש שאלות שונות שהשואל היה מקליד בטלפון, שהתשובה עליהן היא הכלל הזה.

כללים:
1. שפה של השואל, לא של הפקודה: קצר, ישיר, בגוף ראשון, מותר סלנג וקיצורים צבאיים מקובלים. אל תצטט את הכלל.
2. אל תזכיר מספרי פקודות או סעיפים. בראשי-תיבות כתוב גרשיים עבריים (״) ולא מירכאות (") — צה״ל, שמ״פ, רמטכ״ל, אכ״א.
3. שלוש שאלות בשלושה סגנונות: (א) שאלה קצרה כמו בהודעה; (ב) מצב קצר שקרה לשואל, שנגמר בשאלה; (ג) שאלה על תנאי, חריג, סכום, מועד או מי מאשר — לפי מה שהכלל באמת קובע.
4. כל שאלה חייבת להיות כזאת שהכלל הזה עונה עליה — לא הפסקה כולה, לא נושא דומה, ולא פרט שהכלל אינו קובע (סכום, מועד, מידה או תנאי שאינם כתובים בו).
5. אם שום שואל כזה לא היה שואל על הכלל (נוהל פנימי, טופס, הגדרה טכנית) — החזר עבורו skip עם סיבה קצרה, ורשימת שאלות ריקה. אחרת skip הוא מחרוזת ריקה.

הכללים:
{clauses}

החזר JSON בלבד: {{"items": [{{"n": 1, "questions": ["...", "...", "..."], "skip": ""}}]}}"""

SOURCE = ROOT / "night" / "out" / "source"     # night/cleantext/build_source.py (clean text, or raw where better)
CONTEXT_WORDS = 120

SCHEMA = {
    # no minItems/maxItems: the Batch API rejects them on arrays (night/grade.py PARTS_SCHEMA)
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": {
            "type": "object",
            "properties": {"n": {"type": "integer"},
                           "questions": {"type": "array", "items": {"type": "string"}},
                           "skip": {"type": "string"}},
            "required": ["n", "questions", "skip"],
            "additionalProperties": False}},
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _role(roles: list[str]) -> str:
    """The persona and the retrieval role: the order's widest audience, soldier first."""
    roles = roles or ["soldier"]     # no tag = every role (backend.ALL_ROLES)
    return "soldier" if "soldier" in roles else "reserve" if "reserve" in roles else "commander"


def build_units(store: Path = STORE) -> list[dict]:
    """One unit per (order, clause title) across all curated sections; the longer twin wins
    (a digit block over its nodigits copy). The split and the held phrasing are hashed from
    the key, so they never depend on the order of files or on the other clauses."""
    units: dict[tuple, dict] = {}
    for p in sorted(Path(store).glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        did = d.get("document_id")
        if not did:
            continue
        for s in d.get("sections") or []:
            for c in s.get("clauses") or []:
                clause = re.sub(r"\s+", " ", str(c.get("number") or "").strip())
                text = str(c.get("text") or "").strip()
                if not text:
                    continue
                key = (did, clause)
                if key in units and len(units[key]["text"]) >= len(text):
                    continue
                units[key] = {"doc_id": did, "order_title": d.get("title") or "", "clause": clause,
                              "text": text, "context": text, "section": s.get("id") or "",
                              "role": _role(d.get("roles") or [])}
    out = []
    for (did, clause), u in sorted(units.items()):
        h = int(hashlib.sha1(f"{did}|{clause}".encode("utf-8")).hexdigest(), 16)
        u["uid"] = f"u{h % 16 ** 12:012x}"
        u["split"] = "held" if h % HELD_MOD == 0 else "dev"
        u["held_q"] = (h // HELD_MOD) % 3      # the clause's own held phrasing, for dev clauses
        out.append(u)
    return out


def build_fulltext_units(store: Path = STORE, source: Path = SOURCE) -> list[dict]:
    """One unit per RULE of the order's full text — not only what a curated block carries (29.09, the
    manager: otherwise layer 1 measures only what the summary already has). A rule is the coverage audit's:
    a unit of the order's text with a normative marker, not administrative, with enough content words
    (night/coverage_audit.py). The text is the order's best one (clean, or raw where that is better —
    night/cleantext/build_source.py); `context` is the paragraph it sits in, for the question writer only.
    `in_block`: some curated clause of the order carries it (the audit's overlap ≥ CONTENT_MIN) — so the
    results split into what the summary has and what only the order has. Split and held phrasing are
    hashed from (order, rule text), as for the curated units."""
    from night import coverage_audit as ca
    units: dict[tuple, dict] = {}
    for p in sorted(Path(store).glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        did = d.get("document_id")
        if not did:
            continue
        sp = Path(source) / f"{did}.json"
        if sp.exists():
            paras = [str(x.get("text") or "") for x in json.loads(sp.read_text(encoding="utf-8"))["paragraphs"]]
        else:
            paras = [ln for ln in (d.get("raw_text") or "").split("\n") if ln.strip()]
        flat = [re.sub(r"\s+", " ", x).strip() for x in paras if x.strip()]
        texts = [c["number"] + " " + c["text"] for c in ca.block_clauses(d)]
        for u in ca.rule_units("\n".join(paras)):
            if not u["normative"] or u["admin"] or len(ca.content_words(u["text"])) < ca.MIN_CONTENT:
                continue
            t = re.sub(r"\s+", " ", u["text"]).strip()
            if (did, t) in units:
                continue
            head = " ".join(t.split()[:6])
            ctx = next((x for x in flat if head in x), t)
            units[(did, t)] = {
                "doc_id": did, "order_title": d.get("title") or "", "clause": "כלל: " + " ".join(t.split()[:8]),
                "text": t, "context": " ".join(ctx.split()[:CONTEXT_WORDS]), "section": "fulltext",
                "in_block": max((ca.overlap(t, x) for x in texts), default=0.0) >= ca.CONTENT_MIN,
                "soldier": bool(u["soldier"]), "role": _role(d.get("roles") or [])}
    out = []
    for (did, t), u in sorted(units.items()):
        h = int(hashlib.sha1(f"{did}|{t}".encode("utf-8")).hexdigest(), 16)
        u["uid"] = f"f{h % 16 ** 12:012x}"
        u["split"] = "held" if h % HELD_MOD == 0 else "dev"
        u["held_q"] = (h // HELD_MOD) % 3
        out.append(u)
    return out


# ── Pilot 3 (30.09): units only from text that can be trusted (night/layer1/CRITERION.md, pilot 3) ──────
PILOT_SALT = "pilot3-20260930"
PILOT12 = ROOT / "night" / "layer1" / "pilot12_uids.json"   # the 40 rules pilots 1-2 were written on
_WORD_END_DIGIT = re.compile(r"[\u05D0-\u05EA]{2,}\d(?=[\s,;:)]|$)", re.M)
_CONT = re.compile(r"^(?:ו[א-ת]+|אלא|זאת|לבין|או|כאמור|לרבות|בתנאי|למעט|אך|וכן)(?=[\s,])")
_GLUED = re.compile(r"[א-ת]\d|\d[א-ת]")


def order_reliable(doc: dict, source: dict) -> bool:
    """An order's full text may seed questions only when its digits are vouched for (read from the page image,
    or night/digits.py's year/numbering test), no punctuation is coded as a digit (57 orders), and it is not
    OCR of a scan (15). Pilot 2 (30.09): about half its defects came from rules whose text was garbled —
    the question writer filled the gaps by invention."""
    from night import digits as dg
    if doc.get("ingested_from_text"):
        return False
    text = "\n".join(str(x.get("text") or "") for x in source.get("paragraphs") or [])
    if len(_WORD_END_DIGIT.findall(text)) * 1000 / max(len(text.split()), 1) >= 5:
        return False
    return source.get("digits") == "read" or bool(doc.get("digits_fixed")) or dg.trustworthy(doc)


def unit_clean(text: str) -> bool:
    """A rule of a reliable order is still left out when it starts mid-sentence (a conjunction, „זאת, בתנאי ש…",
    „אלא לאחר…") or glues a digit to a letter („האינטרנט1", „2 ה2") — the pieces pilot 2 invented around."""
    t = (text or "").strip()
    return bool(re.match(r"[א-ת]", t)) and not _CONT.match(t) and not _GLUED.search(t)


def build_mixed_units(store: Path = STORE, source: Path = SOURCE) -> list[dict]:
    """Rules of the full text for reliable orders (that pass unit_clean); the curated clauses for every other
    order — so a question is always written from text that can be trusted. One order is never both."""
    reliable: dict[str, bool] = {}
    for p in sorted(Path(store).glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        sp = Path(source) / f"{d.get('document_id')}.json"
        rec = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {
            "digits": "raw", "paragraphs": [{"text": ln} for ln in (d.get("raw_text") or "").split("\n")]}
        reliable[d.get("document_id")] = order_reliable(d, rec)
    ft = [u for u in build_fulltext_units(store, source) if reliable.get(u["doc_id"]) and unit_clean(u["text"])]
    cur = [u for u in build_units(store) if not reliable.get(u["doc_id"])]
    return sorted(ft + cur, key=lambda u: (u["doc_id"], u["uid"]))


def pick_pilot(units: list[dict], n: int, exclude: set[str], salt: str = PILOT_SALT) -> list[dict]:
    """n dev units, never one of `exclude`, in the order of sha1(salt|uid) — fixed before any run."""
    pool = [u for u in units if u["split"] == "dev" and u["uid"] not in exclude]
    return sorted(pool, key=lambda u: hashlib.sha1(f"{salt}|{u['uid']}".encode("utf-8")).hexdigest())[:n]


def fingerprint(units: list[dict]) -> str:
    h = hashlib.sha1()
    for u in units:
        h.update(f"{u['doc_id']}|{u['clause']}|{u['text']}\n".encode("utf-8"))
    return h.hexdigest()[:16]


def requests_for(units: list[dict]) -> list[tuple[str, list[dict], str]]:
    """(custom_id, units, prompt): clauses of one order and one persona, PER_REQUEST at a time."""
    groups: dict[tuple, list[dict]] = {}
    for u in units:
        groups.setdefault((u["doc_id"], u["role"]), []).append(u)
    out = []
    for (did, role), us in sorted(groups.items()):
        for i in range(0, len(us), PER_REQUEST):
            part = us[i:i + PER_REQUEST]
            if part[0].get("section") == "fulltext":
                body = "\n\n".join(f"[{n}] כלל: {u['text']}\nהקשר: {u['context']}" for n, u in enumerate(part, 1))
                prompt = PROMPT_FULLTEXT.format(persona=PERSONA[role], order_title=part[0]["order_title"],
                                                clauses=body)
            else:
                body = "\n\n".join(f"[{n}] כותרת: {u['clause']}\nטקסט: {u['text']}" for n, u in enumerate(part, 1))
                prompt = PROMPT.format(persona=PERSONA[role], order_title=part[0]["order_title"], clauses=body)
            out.append((f"r{len(out):05d}", part, prompt))
    return out


def estimate(reqs) -> tuple[int, int, float, float]:
    tin = sum(int(len(p) / CHARS_PER_TOKEN) for _, _, p in reqs)
    tout = sum(len(us) * OUT_TOKENS_PER_CLAUSE for _, us, _ in reqs)
    return (tin, tout, cost_usd(MODEL, input_tokens=tin, output_tokens=tout),
            cost_usd(MODEL, input_tokens=tin, output_tokens=tout, batch=True))


_ABBR: set[str] | None = None
_ABBR_STORE = [STORE]     # the corpus the units are cut from (--store); the abbreviations are read there too


def _abbr_prefixes() -> set[str]:
    """The part before the quote of every abbreviation in the corpus: שמ"פ -> שמ, רמטכ"ל -> רמטכ."""
    global _ABBR
    if _ABBR is None:
        out: set[str] = set()
        for p in sorted(Path(_ABBR_STORE[0]).glob("*.json")):
            raw = json.loads(p.read_text(encoding="utf-8")).get("raw_text") or ""
            out.update(re.findall(r'([א-ת]{1,6})["״][א-ת]{1,2}(?![א-ת])', raw))
        _ABBR = out
    return _ABBR


def _fragment(q: str) -> bool:
    """A piece of a question, not a question. The first full-text pilot (30.09) cut 7 of 93 questions at the
    ASCII quote of an abbreviation and handed back the halves: a first half that stops, with no question
    mark, on an abbreviation's opening letters („אני בשמ", „…מהרמטכ"), and a second half that starts with
    punctuation or with the abbreviation's lone last letter („. כמה סמלים", „פ למטרה…")."""
    t = q.strip()
    if not t or t[0] in ".,;:-–—)\"״" or re.match(r"^[א-ת](?:\s|[.,])", t):
        return True
    words = re.findall(r"[א-ת]+", t)
    if t[-1] not in "?!." and words:
        w = words[-1]
        if {w[i:] for i in range(0, 3) if len(w) - i >= 2} & _abbr_prefixes():
            return True
    return False


def parse(text: str, units: list[dict]) -> dict[str, dict]:
    """{uid: {"questions": [...], "skip": str}} for the items that parse; the rest are absent."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        items = json.loads(m.group())["items"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}
    got = {}
    for it in items if isinstance(items, list) else []:
        try:
            u = units[int(it["n"]) - 1]
        except (KeyError, ValueError, TypeError, IndexError):
            continue
        qs = [q.strip() for q in it.get("questions") or [] if isinstance(q, str) and q.strip()]
        skip = str(it.get("skip") or "").strip()
        if not skip and any(_fragment(q) for q in qs[:3]):
            continue   # a question cut at an abbreviation's quote (pilot 30.09: „אני בשמ") — not parsed, never used
        if skip or len(qs) >= 3:
            got[u["uid"]] = {"questions": [] if skip else qs[:3], "skip": skip}
    return got


def rows_for(units_by_uid: dict[str, dict], got: dict[str, dict], req_of: dict[str, str]) -> list[dict]:
    rows = []
    for uid, g in got.items():
        u = units_by_uid[uid]
        base = {k: u[k] for k in ("uid", "doc_id", "clause", "section", "role", "split")}
        base.update({k: u[k] for k in ("in_block", "soldier") if k in u})
        if g["skip"]:
            rows.append({**base, "qid": f"{uid}_skip", "i": None, "q": None, "phrasing": None,
                         "skip": g["skip"], "req": req_of[uid]})
            continue
        for i, q in enumerate(g["questions"]):
            held = u["split"] == "held" or i == u["held_q"]
            rows.append({**base, "qid": f"{uid}_{i}", "i": i, "q": q, "phrasing": "held" if held else "dev",
                         "skip": "", "req": req_of[uid]})
    return rows


def _layer1_committed(ledger: Ledger) -> float:
    return sum((e["actual"] if e["actual"] is not None else e["estimate"])
               for e in ledger._state["entries"] if str(e.get("label", "")).startswith("layer1"))


def _guard(ledger: Ledger, est: float, label: str) -> str:
    ledger._merge_disk()
    have = _layer1_committed(ledger)
    if have + est > CAP_USD:
        raise SystemExit(f"[layer1] {label}: ${est:.2f} on top of ${have:.2f} crosses the ${CAP_USD:.2f} cap — not sent")
    return ledger.reserve(label, est)


def run_sync(reqs, label: str) -> tuple[dict[str, dict], float]:
    import backend
    ledger = Ledger(ROOT / "night" / "out" / "ledger.json")
    rid = _guard(ledger, estimate(reqs)[2] * 1.3, label)
    got, usd = {}, 0.0
    try:
        for cid, us, prompt in reqs:
            for attempt in (1, 2):
                r = backend.client.messages.create(
                    model=MODEL, max_tokens=MAX_TOKENS,
                    output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                    messages=[{"role": "user", "content": prompt}])
                usd += cost_usd(MODEL, input_tokens=r.usage.input_tokens, output_tokens=r.usage.output_tokens)
                g = parse("".join(b.text for b in r.content if b.type == "text"), us)
                if len(g) == len(us) or attempt == 2:
                    got.update(g)
                    break
    finally:
        ledger.settle(rid, usd)
    return got, usd


def run_batch(reqs, label: str) -> tuple[dict[str, dict], float]:
    import backend
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    ledger = Ledger(ROOT / "night" / "out" / "ledger.json")
    rid = _guard(ledger, estimate(reqs)[3] * 1.3, label)
    by_id = {cid: us for cid, us, _ in reqs}
    batch = backend.client.messages.batches.create(requests=[
        Request(custom_id=cid, params=MessageCreateParamsNonStreaming(
            model=MODEL, max_tokens=MAX_TOKENS,
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": prompt}]))
        for cid, _, prompt in reqs])
    (OUT / f"{label}_batch_id.txt").write_text(batch.id, encoding="utf-8")   # a crash can collect it later
    safe_print(f"[layer1] batch {batch.id}: {len(reqs)} requests")
    failures = 0
    while True:
        try:
            b = backend.client.messages.batches.retrieve(batch.id)
        except Exception as e:  # noqa: BLE001 — a poll error; the batch is safe server-side
            failures += 1
            if failures >= 10:
                raise
            safe_print(f"[layer1]   poll error {failures}/10: {type(e).__name__}")
            time.sleep(30)
            continue
        failures = 0
        if b.processing_status == "ended":
            break
        time.sleep(30)
    got, usd = {}, 0.0
    for res in backend.client.messages.batches.results(batch.id):
        if res.result.type != "succeeded":
            continue
        m = res.result.message
        usd += cost_usd(MODEL, input_tokens=m.usage.input_tokens, output_tokens=m.usage.output_tokens, batch=True)
        got.update(parse("".join(x.text for x in m.content if x.type == "text"), by_id[res.custom_id]))
    ledger.settle(rid, usd)
    return got, usd


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry", action="store_true")
    g.add_argument("--pilot", type=int, metavar="N")
    g.add_argument("--full", action="store_true")
    ap.add_argument("--units", choices=["curated", "fulltext", "mixed"], default="curated",
                    help="curated clauses (the first criterion) or every rule of the order's full text")
    ap.add_argument("--source", default=str(SOURCE))
    ap.add_argument("--store", default=str(STORE),
                    help="the corpus to cut units from (a read-only copy of the measured main); default: this tree")
    args = ap.parse_args()

    store = Path(args.store)
    _ABBR_STORE[0] = store
    units = (build_units(store) if args.units == "curated" else
             build_fulltext_units(store, Path(args.source)) if args.units == "fulltext" else
             build_mixed_units(store, Path(args.source)))
    fp = fingerprint(units)
    held = sum(1 for u in units if u["split"] == "held")
    roles = {r: sum(1 for u in units if u["role"] == r) for r in PERSONA}
    safe_print(f"[layer1] {args.units}: {len(units)} units in {len({u['doc_id'] for u in units})} orders, "
               f"corpus {fp}; held {held}, dev {len(units) - held}; roles {roles}")
    if args.units == "fulltext":
        inb = sum(1 for u in units if u["in_block"])
        sol = sum(1 for u in units if u["soldier"])
        safe_print(f"[layer1] fulltext: in a block {inb}, only in the order's text {len(units) - inb}; "
                   f"soldier-worded {sol} ({sum(1 for u in units if u['soldier'] and not u['in_block'])} "
                   f"of them only in the text)")

    prefix = {"curated": "layer1", "fulltext": "layer1ft", "mixed": "layer1mx"}[args.units]
    if args.units == "mixed":
        n_ft = sum(1 for u in units if u["section"] == "fulltext")
        safe_print(f"[layer1] mixed: {n_ft} full-text rules of reliable orders, {len(units) - n_ft} curated "
                   f"clauses of the others")
    if args.pilot and args.units == "mixed":
        seen = set(json.loads(PILOT12.read_text(encoding="utf-8")))
        chosen = pick_pilot(units, args.pilot, seen)
        reqs, label = requests_for(chosen), f"{prefix}-pilot"
    elif args.pilot:
        pool = [u for u in units if u["split"] == "dev"]
        chosen = random.Random(SEED).sample(pool, min(args.pilot, len(pool)))
        reqs, label = requests_for(chosen), f"{prefix}-pilot"
    else:
        reqs, label = requests_for(units), f"{prefix}-full"
    tin, tout, usd, usd_b = estimate(reqs)
    safe_print(f"[layer1] {label}: {len(reqs)} requests, ~{tin:,} tokens in, ~{tout:,} out — "
               f"~${usd:.2f} standard, ~${usd_b:.2f} batch (cap ${CAP_USD:.2f})")
    if args.dry:
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    done_units = [u for _, us, _ in reqs for u in us]
    (OUT / f"{label}_units.json").write_text(json.dumps(
        {"corpus": fp, "model": MODEL, "units": done_units}, ensure_ascii=False, indent=1), encoding="utf-8")
    got, spent = run_sync(reqs, label) if args.pilot else run_batch(reqs, label)
    req_of = {u["uid"]: cid for cid, us, _ in reqs for u in us}
    rows = rows_for({u["uid"]: u for u in done_units}, got, req_of)
    with open(OUT / f"{label}.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"corpus": fp, **r}, ensure_ascii=False) + "\n")
    skipped = sum(1 for g in got.values() if g["skip"])
    safe_print(f"[layer1] {label}: {len(got)}/{len(done_units)} clauses parsed ({skipped} skip), "
               f"{sum(1 for r in rows if r['q'])} questions, ${spent:.3f} — {OUT / (label + '.jsonl')}")
    return 0 if len(got) >= 0.95 * len(done_units) else 1


if __name__ == "__main__":
    raise SystemExit(main())
