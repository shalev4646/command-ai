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
2. אל תזכיר מספרי פקודות או סעיפים.
3. שלוש שאלות בשלושה סגנונות: (א) שאלה קצרה כמו בהודעה; (ב) מצב קצר שקרה לשואל, שנגמר בשאלה; (ג) שאלה על תנאי, חריג, סכום, מועד או מי מאשר — לפי מה שהסעיף באמת קובע.
4. כל שאלה חייבת להיות כזאת שהסעיף הזה עונה עליה, ולא רק שאלה על נושא דומה.
5. אם שום שואל כזה לא היה שואל על הסעיף (נוהל פנימי, טופס, הגדרה טכנית) — החזר עבורו skip עם סיבה קצרה, ורשימת שאלות ריקה. אחרת skip הוא מחרוזת ריקה.

הסעיפים:
{clauses}

החזר JSON בלבד: {{"items": [{{"n": 1, "questions": ["...", "...", "..."], "skip": ""}}]}}"""

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
                              "text": text, "section": s.get("id") or "", "role": _role(d.get("roles") or [])}
    out = []
    for (did, clause), u in sorted(units.items()):
        h = int(hashlib.sha1(f"{did}|{clause}".encode("utf-8")).hexdigest(), 16)
        u["uid"] = f"u{h % 16 ** 12:012x}"
        u["split"] = "held" if h % HELD_MOD == 0 else "dev"
        u["held_q"] = (h // HELD_MOD) % 3      # the clause's own held phrasing, for dev clauses
        out.append(u)
    return out


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
            body = "\n\n".join(f"[{n}] כותרת: {u['clause']}\nטקסט: {u['text']}" for n, u in enumerate(part, 1))
            prompt = PROMPT.format(persona=PERSONA[role], order_title=part[0]["order_title"], clauses=body)
            out.append((f"r{len(out):05d}", part, prompt))
    return out


def estimate(reqs) -> tuple[int, int, float, float]:
    tin = sum(int(len(p) / CHARS_PER_TOKEN) for _, _, p in reqs)
    tout = sum(len(us) * OUT_TOKENS_PER_CLAUSE for _, us, _ in reqs)
    return (tin, tout, cost_usd(MODEL, input_tokens=tin, output_tokens=tout),
            cost_usd(MODEL, input_tokens=tin, output_tokens=tout, batch=True))


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
        if skip or len(qs) >= 3:
            got[u["uid"]] = {"questions": [] if skip else qs[:3], "skip": skip}
    return got


def rows_for(units_by_uid: dict[str, dict], got: dict[str, dict], req_of: dict[str, str]) -> list[dict]:
    rows = []
    for uid, g in got.items():
        u = units_by_uid[uid]
        base = {k: u[k] for k in ("uid", "doc_id", "clause", "section", "role", "split")}
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
    args = ap.parse_args()

    units = build_units()
    fp = fingerprint(units)
    held = sum(1 for u in units if u["split"] == "held")
    roles = {r: sum(1 for u in units if u["role"] == r) for r in PERSONA}
    safe_print(f"[layer1] {len(units)} clauses in {len({u['doc_id'] for u in units})} orders, corpus {fp}; "
               f"held {held}, dev {len(units) - held}; roles {roles}")

    if args.pilot:
        pool = [u for u in units if u["split"] == "dev"]
        chosen = random.Random(SEED).sample(pool, min(args.pilot, len(pool)))
        reqs, label = requests_for(chosen), "layer1-pilot"
    else:
        reqs, label = requests_for(units), "layer1-full"
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
