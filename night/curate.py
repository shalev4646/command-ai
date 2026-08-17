"""Stage A — write a `key-facts` section for every order that lacks one.

The audit found 27 orders with no `sections` at all. That is not a cosmetic
gap: when an anchor wins, vector_store hands the model the winning document's
MERGED key-facts block (vector_store.py:569), so an order without one is
liftable but unanswerable — the lift arrives and delivers a raw chunk, which on
33.0304 turned out to be the publication colophon.

This runs Opus over each order's own raw_text and asks for the curated block,
then refuses to accept it unless it passes two automated faithfulness gates:

  citations   Every clause must cite clause numbers, and each cited number must
              actually exist as a clause marker in that order's raw_text. A
              model that invents "(סעיף 12)" fails here.
  no-invented
  -topics     A clause may not raise a high-stakes topic the order is silent
              about. This generalizes the 33.0304 trap: the order says nothing
              about a right to counsel, so a clause mentioning עורך דין would
              both state non-existent law AND make the order retrieve for
              questions it cannot answer — worse than staying silent.

Neither gate can confirm the content is CORRECT; they only catch the two
failure modes that are mechanically detectable. Human review is still owed
before this branch merges, and the report says so.
"""
from __future__ import annotations

import json
import os
import re

import backend
from night import config as C
from night.ledger import Ledger, BudgetExceeded, cost_usd
from night.rehearse import doc_path

# Overridable so a cheaper model can be tried against the same gates rather than
# argued about. Haiku is 5x cheaper on both sides, and the gates here are
# mechanical — a clause must cite numbers that exist in the source, and may not
# raise a flagged topic the order is silent about — so a weaker model shows up
# as rejections, not as quietly worse data. That makes the substitution a
# measurable question: what fraction survives, at what cost per accepted order.
MODEL = os.environ.get("CURATE_MODEL", "claude-opus-4-8")
MAX_RAW_WORDS = 9000          # 33.0304, the largest, is 5,532

# Topics where a fabricated sentence would be actively harmful: a soldier acting
# on invented procedure, and an order retrieved for questions it cannot answer.
RISK_TOPICS = {
    "עורך דין": ("עורך דין", "עו\"ד", "סניגור", "ייצוג משפטי", "להיוועץ"),
    "פיצוי כספי": ("פיצוי", "פיצויים", "שיפוי"),
    "ערעור": ("ערעור", "לערער", "השגה"),
    "מעצר": ("מעצר", "לעצור", "עציר"),
    "פיטורין/שחרור": ("שחרור מוקדם", "פיטורין", "הדחה"),
    "ועדה רפואית": ("ועדה רפואית", "פרופיל רפואי"),
}

PROMPT = """לפניך הטקסט הגולמי של פקודת מטכ"ל. כתוב עבורה סעיף "עיקרי הפקודה" (key-facts) —
בלוק מתוקנן שיוגש למודל שעונה לחיילים, במקום פסקאות גולמיות אקראיות.

כותרת הפקודה: {title}

הטקסט הגולמי:
{raw}

כללים מחייבים:
1. **רק מה שכתוב בפקודה.** אסור להסיק, להשלים מידע כללי, או לכתוב על נושא שהפקודה שותקת בו.
   אם הפקודה לא עוסקת בנושא כלשהו — פשוט אל תזכיר אותו. שתיקה עדיפה על ניחוש.
2. {cite_rule}
3. בין 4 ל-8 סעיפים. כל אחד 40–120 מילים.
4. השדה `number` הוא **תווית בשפת המשתמש** — איך חייל היה שואל את זה. לא כותרת משפטית.
   דוגמה טובה: "מתי עד רשאי לסרב לטביעות אצבע". דוגמה רעה: "סעיף 28 — נטילת ראיות".
5. השדה `text` הוא העובדות עצמן, בעברית ברורה, כולל מספרים, מועדים, דרגות ומי מאשר.
6. כסה את מה שחייל או מפקד באמת ישאל, לא את מה שהפקודה מדגישה פרוצדורלית.

"""

# Rule 2 depends on whether the order actually carries clause markers. Asking
# for citations from an order that has none does not produce careful behaviour —
# it produces invented numbers, which is how 18 already-curated orders came to
# cite clauses that cannot exist. 41% of the wave-2 targets are in this state,
# so the instruction has to be right for both.
CITE_RULE = {
    True: '**כל סעיף חייב לצטט מספרי סעיפים** מהפקודה, בסוגריים בסוף: "(סעיף 43)" או\n'
          '   "(סעיפים 44–45)". המספרים חייבים להיות מספרי הסעיפים האמיתיים שמהם לקחת את התוכן.',
    False: '**לפקודה הזו אין מספור סעיפים קריא — אסור לך לצטט מספרי סעיפים בכלל.**\n'
           '   אל תכתוב "(סעיף 5)" או כל הפניה ממוספרת. חייל שינסה לחפש סעיף כזה לא ימצא אותו.\n'
           '   אם צריך להפנות, השתמש בשם הנושא כפי שהוא מופיע בפקודה.',
}

# The digit-free mode. 71 orders — a quarter of the corpus — sat outside
# curation because their PDF text layer scrambles digit runs (61.0110 renders
# התש"ל as 1796; 35.0203 has 6102 for 2016), and under RETRIEVE_CURATED_ONLY an
# uncurated order is not degraded, it is absent. Recovering the digits was
# tried first and does not work for this class: `night.unscramble` assumes a
# fixed per-document substitution table (which is what 32.0314 has) and 14 of
# 14 attempts here rejected with "readings do not form one table" — this is
# reordering within a run, not substitution, and only 4 of the 71 are a clean
# reversal. So the block is written WITHOUT numbers. What an order establishes,
# who is authorised, what the conditions and procedure are — none of that is a
# digit. What a soldier loses is the deadline and the amount, and the block
# says so in words, which is strictly better than the order not existing.
#
# The rule is enforced structurally in check(), not just requested here: the
# 2026-08-11 review found the model quietly repairing corrupted numbers from
# world knowledge (a "פ"מ 33.0316" that appears in neither the source nor
# reality), so a digit anywhere in a digit-free block is a rejection.
NO_DIGITS_RULE = (
    '**הספרות בטקסט הגולמי של הפקודה הזו משובשות בחילוץ — אסור לך לכתוב אף ספרה.**\n'
    '   לא מספרי סעיפים, לא ימים, לא שעות, לא סכומים, לא תאריכים, לא דרגות במספר, לא מספרי פקודות אחרות.\n'
    '   כתוב מה הפקודה קובעת, מי מוסמך, מה התנאים ומה ההליך — במילים בלבד.\n'
    '   היכן שהפקודה קובעת כמות או מועד, כתוב "הפקודה קובעת תקופה/סכום — יש לבדוק בנוסח המקורי"\n'
    '   ואל תנחש. אסור גם לכתוב מספר במילים ("שלושים יום") — זה אותו ניחוש בלבוש אחר.'
)
DIGIT_FREE_NOTE = "הבלוק נכתב ללא מספרים: הספרות במקור המחולץ אינן אמינות. מועדים וסכומים — לבדוק בנוסח המקורי."
# \b is useless on Hebrew (no word boundary between a prefix letter and the
# stem), so the number-word may carry ו/ב/ל/מ/כ/ה/ש glued on the front:
# the pilot let "וחמישה בעלי תפקידים" through on exactly that.
#
# ONE and TWO are deliberately not here. The first full run rejected 30 of 69
# orders and 34 of those 50 hits were אחד/אחת/שני/שתי — "כל אחד", "אחת מהן",
# "משני הצדדים", "כאחד": grammar, not quantity. The hazard this gate exists
# for is a repaired amount or deadline ("שבעה ימים", "שלושים יום"), and no
# corrupted digit run decodes to one or two — a scrambled "30" is never "1".
# The pattern that IS a quantity is a bare number-word followed by a unit, and
# that form still catches one and two ("שני חודשים").
_HEBREW_NUMBER_WORDS = re.compile(
    r"(?<![א-ת])[ובלמכהש]{0,2}"
    r"(?:(?:שלוש|שלושה|ארבע|ארבעה|חמש|חמישה|שש|שישה|שבע|שבעה|"
    r"שמונה|תשע|תשעה|עשר|עשרה|עשרים|שלושים|ארבעים|חמישים|שישים|שבעים|שמונים|תשעים|מאה|מאתיים|אלף)"
    r"(?:\s+(?:עשר|עשרה|ימים|יום|שעות|חודשים|חודש|שנים|שנה|אחוז|אחוזים|שקלים|ש\"ח))?"
    r"|(?:אחד|אחת|שניים|שתיים|שני|שתי)\s+(?:ימים|יום|שעות|חודשים|חודש|שנים|שנה|אחוז|אחוזים|שקלים|ש\"ח))"
    r"(?![א-ת])")

# Structured outputs rather than "return JSON only": Hebrew legal text is full
# of literal quotes (צה"ל, פ"מ, "אתה חשוד בביצוע עבירה פלונית"), and the first
# run died on exactly that — the same hazard ingestion/pdf_to_json.py:119 calls
# out. Schema enforcement removes the failure mode instead of patching it up
# with a regex afterwards.
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "clauses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["number", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "clauses"],
    "additionalProperties": False,
}


def clause_numbers_in_raw(raw: str) -> set[int]:
    """Clause markers as they appear in the source: `43 . חשוד שנעצר`."""
    return {int(m.group(1)) for m in re.finditer(r"(?<!\d)(\d{1,3})\s*\.\s", raw)}


def cited_numbers(text: str) -> set[int]:
    out: set[int] = set()
    for m in re.finditer(r"סעיפים?\s*(\d{1,3})\s*[–\-—]\s*(\d{1,3})", text):
        out |= {int(m.group(1)), int(m.group(2))}
    # Comma lists — "(סעיפים 1, 7, 9)" / "(סעיפים 3, 4 ו-12)". The wave-1 review
    # found 31.0519 citing this way; the form was invisible here, so its numbers
    # were never checked against the source at all.
    for m in re.finditer(r"סעיפים\s*(\d{1,3}(?:\s*,\s*(?:ו-?\s*)?\d{1,3})+"
                         r"(?:\s*ו-?\s*\d{1,3})?)", text):
        out |= {int(n) for n in re.findall(r"\d{1,3}", m.group(1))}
    for m in re.finditer(r"סעיף\s*(\d{1,3})", text):
        out.add(int(m.group(1)))
    return out


# Structured output guarantees the envelope parses, not that the model kept the
# envelope out of the payload: 31.0519's clause text ended in `}]}}, 8]}], "`.
# Debris has no Hebrew letters, so the vocabulary gate cannot see it either.
_JSON_DEBRIS = re.compile(r'[\]\}]\s*[\]\},]|"\s*[\]\}]|[\]\}]\s*"$')


def has_json_debris(text: str) -> bool:
    return bool(_JSON_DEBRIS.search(text))


_HEB_PREFIXES = "הובלמכש"
_FINALS = str.maketrans("םןץףך", "מנצפכ")


_SUFFIXES = ("ים", "ות", "יים", "ה", "י", "ן", "ך", "ו")


def _norm(text: str) -> set[str]:
    """Content words reduced to comparable stems.

    Extends the retriever's prefix-strip + final-fold (vector_store.py:655,
    :666) with suffix stripping, because the first curation run rejected a
    faithful clause over words like המועברים / גנטיים: prefix-only handling
    leaves "מועברים" unable to match the order's own "מועבר", so a correct
    paraphrase scored as invented vocabulary.
    """
    out = set()
    for w in re.findall(r"[א-ת]{3,}", text):
        w = w.translate(_FINALS)
        forms = {w}
        if len(w) > 3 and w[0] in _HEB_PREFIXES:
            forms.add(w[1:])
        for f in list(forms):
            for suf in _SUFFIXES:
                if len(f) > len(suf) + 2 and f.endswith(suf):
                    forms.add(f[: -len(suf)])
        out |= forms
    return out


def is_numbered(raw: str) -> bool:
    """True when the order's clause numbering can actually be verified against.

    Two distinct failures live here, and only the first was handled before.
    Some orders carry no usable numbering at all, so demanding citations from
    them rejects every candidate forever rather than catching anything.

    The second is worse because it looks fine: the RTL extraction reverses digit
    runs, so an order's markers come out as a plausible-looking but wrong set.
    33.0808 yields {1,2,3,6,8,10,...,80,81,82,83,86,88} — 4, 5, 7 and 9 are
    missing while 80-88 are the mirrored forms of 08-88, and its own header
    reads "תוקף סעיפים1 עד82" where 82 is 28 reversed. A density test passes
    that happily, so the model cited clause 4 correctly, the gate could not find
    a "4" to match, and the order was rejected twice at full price for being
    right. 33 of the 164 wave-2 targets are in this state.

    Two tests are needed, because damage is not uniform across an order. An
    unbroken run from 1 catches the wholly-mirrored case, but 30.0401 keeps its
    low numbering intact and mirrors only the high end — markers come out as
    {1,2,3,4,5,7,9,10..15,17,19,50,51,53,55,59}, so it passes a run test and
    then rejects a correct citation of clause 20, twice, at full price.

    Density catches that: a healthy order's markers fill their range (35.0223
    yields 15 markers with a maximum of 15, 30.0603 yields 21 with a maximum of
    21 — density 1.0), while a damaged one is sparse and reaches absurd values
    (3.0110 and 33.0807 both land at density 0.01 with maxima near 900). The
    two populations are separated by two orders of magnitude, so the threshold
    is not delicate.

    Failing either test means citations cannot be verified, so they are
    forbidden rather than fabricated — an unverifiable reference is worth less
    than no reference.
    """
    nums = {n for n in clause_numbers_in_raw(raw) if n > 0}
    if not nums:
        return False
    run = 0
    while run + 1 in nums:
        run += 1
    return run >= 5 and len(nums) / max(nums) >= 0.6


# A faithful paraphrase reuses the order's own vocabulary; an invented clause
# does not. 35% is deliberately loose — user-language labels ("מה קורה אם") are
# supposed to introduce words the legal text never uses.
MAX_UNGROUNDED = 0.50

# The prompt asks for 40-120 words per clause. Ten is far below anything a real
# answer needs and far above the two- and three-word fragments that truncation
# leaves behind, so it separates the two without judging terseness.
MIN_CLAUSE_WORDS = 10


def check(section: dict, raw: str, digit_free: bool = False) -> tuple[list[str], list[str]]:
    """The faithfulness gates.

    Returns (problems, warnings). Problems block acceptance — they are the
    mechanically detectable forms of invention. Warnings are recorded for the
    human review that is still owed, and do not block.

    `digit_free` is the gate for orders whose source digits are scrambled: any
    digit, or any number spelled out in Hebrew, in any clause is a rejection.
    Citation checks are moot in that mode — there is nothing to cite against.
    """
    problems: list[str] = []
    warnings: list[str] = []
    real = clause_numbers_in_raw(raw)
    numbered = is_numbered(raw) and not digit_free
    raw_vocab = _norm(raw)

    for cl in section.get("clauses", []):
        txt = cl.get("text", "")
        label = str(cl.get("number", "?"))[:40]
        if digit_free:
            blob = f"{cl.get('number', '')} {txt}"
            digits_found = re.findall(r"\d+", blob)
            words_found = _HEBREW_NUMBER_WORDS.findall(blob)
            if digits_found:
                problems.append(f"clause {label!r}: digit-free block contains "
                                f"{digits_found[:4]}")
            if words_found:
                problems.append(f"clause {label!r}: digit-free block spells out a "
                                f"number: {words_found[:3]}")
        if has_json_debris(txt):
            problems.append(f"clause {label!r}: JSON debris in user-facing text")
        # A clause cut off at a Hebrew gershayim: the model writes צה"ל or יו"ר
        # and the text ends at the quote, leaving "צה" or "יו" as the whole
        # clause. Structured output was supposed to close this, and mostly does
        # — 5 of 115 Haiku blocks and 1 of 98 Opus ones still land here — but the
        # vocabulary gate cannot see it, because a two-word clause has almost no
        # vocabulary to be ungrounded. The prompt asks for 40-120 words, so
        # anything this short is debris rather than a terse answer.
        if len(txt.split()) < MIN_CLAUSE_WORDS:
            problems.append(f"clause {label!r}: only {len(txt.split())} words — "
                            f"truncated, probably at a gershayim")
        cites = cited_numbers(txt)
        if numbered:
            # A MISSING citation is a formatting miss, not an unfaithful claim —
            # rejecting on it cost 29 of the first run's 32 rejections and taught
            # nothing. A WRONG citation is invention and still fails.
            if cites and cites - real:
                problems.append(f"clause {label!r}: cites {sorted(cites - real)}, "
                                f"absent from raw_text")
            elif not cites:
                warnings.append(f"clause {label!r}: no clause citation")
        elif cites:
            # An order with no clause markers cannot be cited by clause number,
            # so ANY citation here is invented — and the gate used to be skipped
            # entirely in this branch, which is how 18 already-curated orders
            # ended up telling soldiers to look up clauses that do not exist.
            # 41% of the wave-2 targets have no numbering, so this is the common
            # case, not the exception.
            problems.append(f"clause {label!r}: cites {sorted(cites)}, but this "
                            f"order has no clause numbering at all")
        words = _norm(txt)
        if words:
            ungrounded = words - raw_vocab
            share = len(ungrounded) / len(words)
            if share > MAX_UNGROUNDED:
                problems.append(
                    f"clause {label!r}: {share:.0%} of its vocabulary is absent from the "
                    f"order (e.g. {sorted(ungrounded)[:5]})")
    blob = " ".join(str(cl.get("number", "")) + " " + str(cl.get("text", ""))
                    for cl in section.get("clauses", []))
    for label, terms in RISK_TOPICS.items():
        if any(t in blob for t in terms) and not any(t in raw for t in terms):
            problems.append(f"raises {label!r}, which the order never mentions")
    return problems, warnings


RETRY_SUFFIX = """

הניסיון הקודם שלך נדחה בבדיקת נאמנות אוטומטית. הבעיות:
{problems}

כתוב מחדש. שים לב במיוחד: כל מספר סעיף שאתה מצטט חייב להופיע בטקסט הגולמי שלמעלה,
ואסור להזכיר נושא שהפקודה שותקת בו — עדיף לכתוב פחות סעיפים ולהיות מדויק."""


def curate_one(doc: dict, ledger: Ledger, problems: list[str] | None = None,
               digit_free: bool = False) -> tuple[dict | None, float, list[str]]:
    raw = " ".join(str(doc.get("raw_text", "")).split()[:MAX_RAW_WORDS])
    est = (len(raw.split()) * 1.6 * 5 + 1400 * 25) / 1_000_000    # generous
    rid = ledger.reserve(f"curate:{doc['document_id']}", est)
    rule = NO_DIGITS_RULE if digit_free else CITE_RULE[is_numbered(raw)]
    try:
        r = backend.client.messages.create(
            # 8000 truncated 33.0306 mid-JSON and the $0.35 bought nothing:
            # adaptive thinking at effort=high draws from this same budget, so a
            # long order spends it reasoning and gets cut before closing the
            # object. Raising the cap is free for the documents that do not need
            # it — output is billed per token generated, and the orders that
            # succeed land at $0.07-$0.18, nowhere near either ceiling. It only
            # changes the truncating case, from total loss to a usable result.
            model=MODEL, max_tokens=16000,
            # Haiku rejects both of these outright ("adaptive thinking is not
            # supported on this model"), so passing them unconditionally makes
            # the cheap model untestable rather than merely worse — five orders
            # came back as 400s before any comparison could be made.
            **({"thinking": {"type": "adaptive"}} if "opus" in MODEL else {}),
            output_config={**({"effort": "high"} if "opus" in MODEL else {}),
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": PROMPT.format(
                title=doc.get("title", ""), raw=raw, cite_rule=rule)
                + (RETRY_SUFFIX.format(problems="\n".join(f"- {p}" for p in problems))
                   if problems else "")}],
        )
    except Exception as e:
        ledger.settle(rid, 0.0)
        return None, 0.0, [f"api error: {type(e).__name__}: {e}"]
    usd = cost_usd(MODEL, input_tokens=r.usage.input_tokens, output_tokens=r.usage.output_tokens)
    ledger.settle(rid, usd)

    if r.stop_reason == "max_tokens":
        return None, usd, ["hit max_tokens — schema-valid JSON was truncated"]
    text = "".join(b.text for b in r.content if b.type == "text")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        return None, usd, [f"bad JSON despite schema: {e}"]

    section = {"id": "key-facts",
               "title": parsed.get("title") or f"עיקרי הפקודה — {doc.get('title','')}",
               "clauses": parsed.get("clauses") or []}
    if not section["clauses"]:
        return None, usd, ["no clauses"]
    problems, warnings = check(section, str(doc.get("raw_text", "")), digit_free=digit_free)
    if digit_free:
        # The answering model must see that numbers are deliberately absent, so
        # it says "the order sets a deadline — check the source" rather than
        # inventing one. The note rides in the section TITLE: index_document
        # prefixes every chunk with `title — section_title`, so it reaches the
        # model with each clause without repeating 20 words inside each one
        # (the pilot did that and bloated every chunk).
        section["id"] = "key-facts-nodigits"
        section["digit_free"] = True
        section["title"] = f"{section['title']} [{DIGIT_FREE_NOTE}]"
    section["_warnings"] = warnings
    return section, usd, problems


def run(limit: int | None = None, digit_free: bool = False) -> None:
    """`digit_free=True` targets the OTHER population — orders whose digits did
    not survive extraction — and writes numberless blocks for them. The two
    populations are disjoint by construction, so the two modes never race."""
    ledger = Ledger(C.LEDGER)
    from night.audit import _section_ids
    # Orders deliberately left without key-facts. Each has no `sections`, so it
    # looks like a fresh target on every run — this set is what stops a reviewed
    # decision from being silently undone by automation.
    #
    # Both ids are listed for the discharge-grant order because it was renamed:
    # the header digits that produced "20.0502" were themselves reversed and it
    # is really הפ"ע 3.0502. A rename that does not update this set breaks the
    # guard silently, which is exactly what happened once already.
    #
    #   3.0502 / 20.0502  curated, reviewed, pulled — the source's digits are
    #                     demonstrably scrambled ("25 בדצמבר3..2"=2003) and two
    #                     money thresholds could not be traced to it.
    #   33.1010           rejected twice, ~$0.39 each, for citing clause [103]
    #                     that does not exist. Its header announces "סעיפים 5 עד
    #                     68" while the body only ever numbers 1-9, so the
    #                     corrupted banner invites the model to cite clauses the
    #                     text does not have. Retrying costs money and fails the
    #                     same way; it needs a clean source PDF, not another run.
    NEVER = {"20.0502", "3.0502", "33.1010"}
    # Orders whose digits did not survive extraction are excluded outright, not
    # gated harder. Every gate downstream checks a clause against the same raw
    # text, so on a document with substituted digits they all confirm the
    # corruption in unison — and the model quietly repairs some of it from world
    # knowledge, producing a number that reads perfectly and appears nowhere in
    # the order. A soldier acting on an invented deadline is worse off than one
    # told nothing, and unlike silence it cannot be walked back.
    from night.digits import trustworthy
    all_targets = [d for d in backend.load_documents()
                   if d.get("document_id") and d["document_id"] not in NEVER
                   and not _section_ids(d)]
    if digit_free:
        targets = [d for d in all_targets if not trustworthy(d)]
        C.log(f"[curate] DIGIT-FREE mode: {len(targets)} orders with scrambled digits")
    else:
        targets = [d for d in all_targets if trustworthy(d)]
        skipped = len(all_targets) - len(targets)
        if skipped:
            C.log(f"[curate] skipping {skipped} orders whose digits are not verifiable "
                  f"(run with --digit-free to curate them without numbers)")
    if limit:
        targets = targets[:limit]
    C.log(f"[curate] {len(targets)} orders need a key-facts section "
          f"(budget left ${ledger.remaining():.2f})")

    from storage.vector_store import index_document
    done = failed = 0
    for i, doc in enumerate(targets, 1):
        doc_id = doc["document_id"]
        spent_here = 0.0
        try:
            section, usd, problems = curate_one(doc, ledger, digit_free=digit_free)
            spent_here += usd
            if problems:
                # one retry with the gate's own complaints fed back — a rejection
                # that is not retried is money spent to learn nothing
                C.log(f"[curate] {i}/{len(targets)} {doc_id} retry: "
                      f"{'; '.join(problems)[:140]}")
                section, usd, problems = curate_one(doc, ledger, problems, digit_free=digit_free)
                spent_here += usd
        except BudgetExceeded as e:
            C.log(f"[curate] STOPPING — {e}")
            break
        usd = spent_here
        if section is None or problems:
            failed += 1
            C.log(f"[curate] {i}/{len(targets)} {doc_id} REJECTED (${usd:.3f}): "
                  f"{'; '.join(problems)[:200]}")
            C.append_jsonl(C.OUT / "curate_rejected.jsonl",
                           {"doc_id": doc_id, "problems": problems,
                            "section": section, "usd": usd})
            continue

        path = doc_path(doc_id)
        raw_doc = json.loads(path.read_text(encoding="utf-8"))
        raw_doc["sections"] = [section]
        path.write_text(json.dumps(raw_doc, ensure_ascii=False, indent=2), encoding="utf-8")
        n = index_document(json.loads(path.read_text(encoding="utf-8")))
        done += 1
        C.log(f"[curate] {i}/{len(targets)} {doc_id} OK  "
              f"{len(section['clauses'])} clauses, {n} chunks, ${usd:.3f}  "
              f"| spent ${ledger.spent:.2f}")
        C.append_jsonl(C.OUT / "curate_accepted.jsonl",
                       {"doc_id": doc_id, "section": section, "usd": usd})

    C.log(f"[curate] done: {done} accepted, {failed} rejected, spent ${ledger.spent:.2f}")
    C.log(ledger.summary())


if __name__ == "__main__":
    import sys
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    run(limit=int(args[0]) if args else None, digit_free="--digit-free" in sys.argv)
