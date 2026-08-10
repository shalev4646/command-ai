"""Stage 1 — generate the question set.

Two sources that never see each other:

  blind       Personas x life-situations, written with NO corpus content in the
              prompt. This is the honest coverage metric: if the generator
              could see the orders it would only ask what we already answer.
  inside-out  Per-command, WITH the command in the prompt: "what would a
              soldier ask that this order answers?". Double weight for the 30
              orders that have no `sections` and are reachable only via
              anchors. This measures reachability, not coverage — a command
              that cannot retrieve itself is a pure bug.

A quarter of the output is then degraded on purpose (typos, slang, fragments,
OCR-mangled order numbers). Real soldiers type badly; the app's own normalizer
exists because "חפשש" and "להתשחרר" were live pilot questions. Degradation is
deterministic rather than model-generated: free, reproducible, and it keeps the
distribution under our control.

Generation runs synchronously, not through the Batch API. Batch would halve a
~$0.74 bill, but it can take up to an hour to return and nothing else in the
night can start until the questions exist — a bad trade against an 8-hour run.
"""
from __future__ import annotations

import json
import random
import re

import anthropic

import backend
from night import config as C
from night.ledger import Ledger, cost_usd

MODEL = "claude-haiku-4-5"
client = anthropic.Anthropic()
rng = random.Random(20260810)   # fixed: a re-run reproduces the same set

# --- blind generation --------------------------------------------------------
# Personas and situations are deliberately mundane and specific. "A soldier
# asks about vacation" produces textbook questions; "a lone soldier whose
# mother is hospitalized abroad" produces the kind that actually break things.

PERSONAS = {
    "soldier": [
        "חייל.ת בטירונות, חודש ראשון", "חייל.ת קרבי.ת בסדיר, שנה שנייה",
        "חייל.ת בודד.ה בלי משפחה בארץ", "חייל.ת בתפקיד מנהלה בבסיס עורפי",
        "חייל.ת נשוי.אה עם ילד", "חייל.ת דתי.ה", "חייל.ת עם פרופיל רפואי נמוך",
        "חייל.ת שלושה חודשים לפני שחרור", "חייל.ת בקורס מקצועי",
        "חייל.ת ממשפחה במצוקה כלכלית",
    ],
    "commander": [
        "מפקד.ת כיתה טרי.ה", "מ\"פ", "רס\"ר יחידה", "קצין.ת משאבי אנוש",
        "מפקד.ת שצריך.ה להחליט על ענישה", "מפקד.ת שחייל.ת דיווח.ה לו.ה על בעיה אישית",
        "מפקד.ת בסיס הדרכה",
    ],
    "reserve": [
        "חייל.ת מילואים עם עסק עצמאי", "חייל.ת מילואים שכיר.ה עם משפחה",
        "חייל.ת מילואים סטודנט.ית", "מילואימניק.ית אחרי צו 8 ארוך",
    ],
}

SITUATIONS = [
    "משהו קרה במשפחה והוא צריך לצאת דחוף",
    "הוא חושב שקיפחו אותו בתשלום או בזכאות",
    "הוא עשה משהו שהוא חושש שיביא לענישה",
    "הוא צריך אישור למשהו ולא יודע ממי",
    "מצב רפואי שלו או של בן משפחה",
    "משהו בתנאי השירות שנראה לו לא הוגן",
    "הוא רוצה לדעת מה מגיע לו לפני או אחרי שחרור",
    "התנהגות של מישהו אחר שמפריעה לו",
    "לוח זמנים, שעות, תורנויות או חופשות",
    "כסף — משכורת, החזרים, מענקים, פיקדון",
    "הוא נמצא בין שתי מסגרות ולא יודע מה חל עליו",
    "הוא כבר שאל מפקד וקיבל תשובה שנשמעת לו מוזרה",
]

_BLIND_PROMPT = """אתה כותב שאלות בדיקה לעוזר דיגיטלי שעונה לחיילי צה"ל על פקודות מטכ"ל.

הפרסונה: {persona}
המצב: {situation}

כתוב {n} שאלות שונות שאדם כזה, במצב כזה, היה מקליד לאפליקציה בטלפון.

כללים:
1. שפה של חייל אמיתי — קצר, ישיר, לפעמים סלנג. לא שפה משפטית ולא שפת פקודות.
2. אל תזכיר מספרי פקודות. המשתמש לא יודע אותם.
3. גיוון אמיתי: חלק שאלות "מה מגיע לי", חלק "מה קורה אם", חלק "מי מאשר", חלק סיפור קצר שנגמר בשאלה.
4. כל שאלה עומדת בפני עצמה, בלי הקשר קודם.
5. אל תחזור על אותה שאלה בניסוח אחר.

החזר JSON בלבד: {{"questions": ["...", "..."]}}"""

_INSIDE_OUT_PROMPT = """לפניך פקודת מטכ"ל. כתוב {n} שאלות שחייל היה שואל, שהפקודה הזאת עונה עליהן.

כותרת: {title}
{body}

כללים:
1. שפה של חייל, לא שפת הפקודה. אל תצטט את הפקודה — נסח כמו שמישהו באמת שואל.
2. אל תזכיר את מספר הפקודה.
3. כל שאלה חייבת להיות כזאת שהפקודה הזאת באמת עונה עליה.
4. גוון: חלק שאלות הגדרה, חלק מצביות ("קרה לי ש..."), חלק על תנאים וחריגים.

החזר JSON בלבד: {{"questions": ["...", "..."]}}"""


# --- deliberate degradation --------------------------------------------------

_SLANG = {
    "חופשה": "חפשוש", "מפקד": "מ\"פ", "משכורת": "צ'ק", "בסיס": "בסיס",
    "להשתחרר": "להתשחרר", "רגילה": "רגילה", "מילואים": "מילואימים",
    "אישור": "אישור", "יציאה": "יציאה", "שחרור": "שיחרור",
}
_HEB = "אבגדהוזחטיכלמנסעפצקרשת"


def _typo(w: str) -> str:
    """One realistic Hebrew keyboard slip."""
    if len(w) < 3:
        return w
    i = rng.randrange(len(w) - 1)
    kind = rng.choice(("swap", "double", "drop", "near"))
    if kind == "swap":
        return w[:i] + w[i + 1] + w[i] + w[i + 2:]
    if kind == "double":
        return w[:i] + w[i] * 2 + w[i:][1:]
    if kind == "drop":
        return w[:i] + w[i + 1:]
    return w[:i] + rng.choice(_HEB) + w[i + 1:]


def uglify(q: str) -> tuple[str, str]:
    """Degrade a clean question the way a phone keyboard and a hurry would.

    Returns (degraded, kind) so the sweep can report band-by-degradation —
    if ugly questions fail far more often, that is a normalizer finding, not
    a corpus finding, and the two must not be confused in the report.
    """
    kind = rng.choice(("typo", "fragment", "slang", "ocr", "noPunct"))
    words = q.split()
    if kind == "typo" and len(words) > 2:
        for i in rng.sample(range(len(words)), k=min(2, len(words))):
            words[i] = _typo(words[i])
        return " ".join(words), kind
    if kind == "fragment" and len(words) > 4:
        return " ".join(words[: max(3, len(words) - rng.randrange(2, 4))]), kind
    if kind == "slang":
        out = q
        for k, v in _SLANG.items():
            if k in out:
                out = out.replace(k, v, 1)
                break
        return out, kind
    if kind == "ocr":
        # the corpus' own OCR noise, aimed back at us: 7<->1, 0<->9, 3<->8
        sub = {"7": "1", "1": "7", "0": "9", "9": "0", "3": "8", "8": "3"}
        return re.sub(r"\d", lambda m: sub.get(m.group(), m.group()), q), kind
    return q.replace("?", "").replace(",", "").replace("״", ""), "noPunct"


# --- driver ------------------------------------------------------------------

def _ask(prompt: str, max_tokens: int = 1500) -> tuple[list[str], float]:
    r = client.messages.create(
        model=MODEL, max_tokens=max_tokens, temperature=1.0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in r.content if b.type == "text")
    usd = cost_usd(MODEL, input_tokens=r.usage.input_tokens,
                   output_tokens=r.usage.output_tokens)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return [], usd
    try:
        return [q.strip() for q in json.loads(m.group())["questions"] if q.strip()], usd
    except (json.JSONDecodeError, KeyError, TypeError):
        return [], usd


def _sectionless_doc_ids(docs: list[dict]) -> set[str]:
    """Orders with no `sections` — reachable only via anchors, so double-weighted."""
    return {
        d["document_id"] for d in docs
        if d.get("document_id") and not d.get("sections")
    }


def generate(ledger: Ledger) -> int:
    docs = backend.load_documents()
    sectionless = _sectionless_doc_ids(docs)
    C.log(f"[genq] {len(docs)} docs, {len(sectionless)} without sections (double-weighted)")

    rid = ledger.reserve("genq", 0.90)
    spent = 0.0
    rows: list[dict] = []

    # --- blind ---------------------------------------------------------------
    combos = [(role, p, s) for role, ps in PERSONAS.items() for p in ps for s in SITUATIONS]
    rng.shuffle(combos)
    per_call = 8
    needed = -(-C.N_BLIND // per_call)
    for role, persona, situation in combos[:needed]:
        qs, usd = _ask(_BLIND_PROMPT.format(persona=persona, situation=situation, n=per_call))
        spent += usd
        for q in qs:
            rows.append({"q": q, "source": "blind", "role": role,
                         "persona": persona, "situation": situation, "target_doc": None})
        if len(rows) % 200 < per_call:
            C.log(f"[genq] blind: {len(rows)} questions, ${spent:.2f}")

    # --- inside-out ----------------------------------------------------------
    n_blind = len(rows)
    for d in docs:
        doc_id = d.get("document_id")
        if not doc_id:
            continue
        n = 8 if doc_id in sectionless else 4
        secs = d.get("sections") or {}
        body = "\n".join(f"{k}: {str(v)[:300]}" for k, v in list(secs.items())[:6]) \
            or str(d.get("raw_text", ""))[:1800]
        qs, usd = _ask(_INSIDE_OUT_PROMPT.format(title=d.get("title", ""), body=body, n=n))
        spent += usd
        role = "reserve" if "מילואים" in str(d.get("title", "")) else "soldier"
        for q in qs:
            rows.append({"q": q, "source": "inside_out", "role": role,
                         "persona": None, "situation": None, "target_doc": doc_id})
    C.log(f"[genq] inside-out: {len(rows) - n_blind} questions, ${spent:.2f} total")

    # --- degrade a quarter ---------------------------------------------------
    for i, row in enumerate(rows):
        row["id"] = f"q{i:05d}"
        row["clean_q"] = row["q"]
        row["ugly"] = None
    for row in rng.sample(rows, k=int(len(rows) * C.UGLY_FRACTION)):
        row["q"], row["ugly"] = uglify(row["clean_q"])

    ledger.settle(rid, spent)
    n = C.write_jsonl(C.QUESTIONS, rows)
    C.log(f"[genq] wrote {n} questions ({sum(1 for r in rows if r['ugly'])} degraded) "
          f"for ${spent:.2f}")
    return n


if __name__ == "__main__":
    generate(Ledger(C.LEDGER))
