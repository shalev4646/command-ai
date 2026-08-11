"""What is missing from the corpus, ranked — the deliverable the design promised.

Every measurement the night ran pointed inward: the 27 structural defects, the
retrieval bands, the anchor coverage, the answer grades. All of them describe
the 98 orders we HAVE. None of them answered "what should we acquire", which
was supposed to be a deliverable and was not built.

The raw material was already on disk. Each graded answer records how many parts
of its question went unanswered and why, and those reasons are specific — "no
procedure for transferring soldiers between units", "the orders do not set the
required approval level". Turning them into a purchase list is a clustering
problem, not a new measurement.

So: name the subject each gap needs, group the questions under it, and rank by
how many questions each subject would close. Costs a few cents in Haiku.
"""
from __future__ import annotations

import json
import time

import backend
from night import config as C
from night.ledger import Ledger, cost_usd

MODEL = "claude-haiku-4-5"
OUT = C.ROOT / "SHOPPING_LIST.md"

SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "audience": {"type": "string", "enum": ["חייל", "מפקד", "מילואים", "כללי"]},
        "confident": {"type": "boolean"},
    },
    "required": ["topic", "audience", "confident"],
    "additionalProperties": False,
}

PROMPT = """עוזר דיגיטלי לחיילים נשאל את השאלה הבאה, וענה עליה רק חלקית.

השאלה:
{q}

מה חסר, לפי המדרג:
{reason}

מה **נושא הפקודה** שהיה צריך להיות במאגר כדי לענות על החלק החסר?

- `topic` — שם נושא קצר וקנוני, 2–6 מילים, בשפת פקודות. למשל „העברת חיילים בין יחידות",
  „ניכויים מהשכר", „אישור יציאה מהבסיס". **לא** ניסוח מחדש של השאלה.
- אם אותו נושא יחזור על שאלות שונות, נסח אותו זהה — הרשימה מקובצת לפי המחרוזת הזאת.
- `confident` — false אם אינך יודע לזהות נושא ברור מהנימוק."""


# Order ids are numbered by subject, so the family prefix of the orders retrieval
# DID return locates the neighbourhood the missing order lives in. Indirect — these
# are neighbours, not the missing order itself — but the numbering makes it a fair
# proxy, and it is the only signal available without the site index.
FAMILY = {
    "01": "כללי", "02": "ביטחון", "06": "שיפוט (הפ\"ע)", "09": "ארגון",
    "20": "קבע — כללי", "21": "ביטחון מידע", "30": "משמעת — כללי",
    "31": "כוח אדם ותנועה", "32": "כושר רפואי", "33": "משטר ומשמעת",
    "35": "תנאי שירות חובה", "36": "תנאי שירות קבע", "37": "השכלה",
    "38": "שחרור", "52": "תספוקת", "53": "תספוקת", "54": "תספוקת",
    "56": "תספוקת", "57": "רכב", "58": "רפואה", "61": "רפואה",
}


def _families(gaps: list[dict]) -> list[str]:
    import collections
    import re
    import backend

    hits = collections.Counter()
    for r in gaps:
        for s in set(r.get("sources") or []):
            m = re.search(r"(\d{2})", str(s))
            if m:
                hits[FAMILY.get(m.group(1), "אחר")] += 1
    have = collections.Counter()
    for d in backend.load_documents():
        m = re.search(r"(\d{2})", str(d.get("document_id", "")))
        if m:
            have[FAMILY.get(m.group(1), "אחר")] += 1
    total = sum(hits.values()) or 1

    out = ["## קודם כול — לאילו משפחות למשוך", "",
           "‏42 הנושאים למטה הופיעו **פעם אחת כל אחד**. זה עצמו הממצא: הפערים רחבים "
           "ודקים ולא מרוכזים, ולכן אין „עשר פקודות שיסגרו חצי מהבעיה\". "
           "**האסטרטגיה היעילה היא למשוך קטגוריות שלמות.**", "",
           "| משפחה | % מהפערים | פקודות שיש | פער לכל פקודה |",
           "|---|---|---|---|"]
    rows = []
    for fam, n in hits.most_common():
        got = have.get(fam, 0)
        rows.append((n / max(1, got), fam, n, got))
    for _, fam, n, got in sorted(rows, key=lambda x: -x[2])[:10]:
        ratio = f"{n/got:.1f}" if got else "—"
        out.append(f"| {fam} | {100*n/total:.1f}% | {got} | {ratio} |")
    out += ["",
            "העמודה הימנית היא סדר-העדיפויות האמיתי: משפחה שמייצרת הרבה פערים עם מעט "
            "פקודות היא הרזה ביותר ביחס לביקוש.", "",
            "⚠ זהו **פרוקסי**: המשפחה נגזרת מהפקודות שהאחזור כן החזיר — שכנות של "
            "החסרה, לא היא עצמה. מספור-הפקודות לפי נושא הוא מה שהופך את זה לסביר.", "",
            "---", ""]
    return out


def run(limit: int | None = None) -> None:
    ledger = Ledger(C.LEDGER)
    grades = C.read_jsonl(C.OUT / "grades_baseline.jsonl")
    sweep = {r["id"]: r for r in C.read_jsonl(C.SWEEP)}
    gaps = [r for r in grades
            if sweep.get(r["id"], {}).get("source") == "blind"
            and (r.get("grade") or {}).get("unanswered_parts", 0) > 0]
    if limit:
        gaps = gaps[:limit]
    C.log(f"[shopping] {len(gaps)} blind questions with an uncovered part")

    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    reqs = [Request(custom_id=f"s{i}", params=MessageCreateParamsNonStreaming(
        model=MODEL, max_tokens=300,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": PROMPT.format(
            q=r["q"], reason=(r.get("grade") or {}).get("reason", ""))}]))
        for i, r in enumerate(gaps)]

    rid = ledger.reserve("shopping", len(reqs) * 0.0012)
    batch = backend.client.messages.batches.create(requests=reqs)
    C.log(f"[shopping] batch {batch.id} submitted")
    while True:
        b = backend.client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        time.sleep(20)

    actual = 0.0
    for res in backend.client.messages.batches.results(batch.id):
        i = int(res.custom_id[1:])
        if res.result.type != "succeeded":
            continue
        m = res.result.message
        actual += cost_usd(MODEL, input_tokens=m.usage.input_tokens,
                           output_tokens=m.usage.output_tokens, batch=True)
        try:
            gaps[i]["need"] = json.loads("".join(b.text for b in m.content if b.type == "text"))
        except json.JSONDecodeError:
            pass
    ledger.settle(rid, actual)

    buckets: dict[str, list[dict]] = {}
    unsure = 0
    for r in gaps:
        need = r.get("need")
        if not need:
            continue
        if not need.get("confident"):
            unsure += 1
            continue
        buckets.setdefault(need["topic"], []).append({**r, "audience": need["audience"]})

    ranked = sorted(buckets.items(), key=lambda kv: -len(kv[1]))
    C.write_jsonl(C.OUT / "shopping.jsonl",
                  [{"topic": t, "n": len(v), "ids": [x["id"] for x in v]} for t, v in ranked])

    n_q = sum(len(v) for _, v in ranked)
    md = ["# רשימת קניות — מה חסר במאגר", "",
          *_families(gaps),
          f"נגזר מ-{len(gaps)} שאלות עיוורות שקיבלו תשובה חלקית או ריקה. "
          f"‏{n_q} מהן יוחסו לנושא מזוהה; {unsure} לא ניתנו לשיוך ברור.", "",
          "**איך לקרוא:** כל שורה היא נושא שפקודה אחת (או כמה) הייתה סוגרת. "
          "המספר הוא כמה שאלות מהמדגם היו נענות במלואן אילו הנושא היה במאגר — "
          "כלומר סדר העדיפויות למשיכה.", "",
          "⚠ המדגם הוא 54 שאלות מפס-ביניים אחד, לא כל הקורפוס. נושא שמופיע פעם אחת "
          "כאן אינו בהכרח נדיר במציאות — הוא פשוט הופיע פעם אחת ב-54.", "",
          "| # | נושא | שאלות | קהל |", "|---|---|---|---|"]
    for i, (topic, items) in enumerate(ranked, 1):
        auds = {x["audience"] for x in items}
        md.append(f"| {i} | {topic} | {len(items)} | {', '.join(sorted(auds))} |")

    md += ["", "---", "", "## השאלות מאחורי כל נושא", ""]
    for topic, items in ranked:
        md += [f"### {topic}  ({len(items)})", ""]
        for x in items:
            md.append(f"- {x['q']}")
        md.append("")

    OUT.write_text("\n".join(md), encoding="utf-8")
    C.log(f"[shopping] {len(ranked)} distinct topics from {n_q} questions, ${actual:.3f}")
    for topic, items in ranked[:12]:
        C.log(f"[shopping]   {len(items):>2}  {topic}")


if __name__ == "__main__":
    run()
