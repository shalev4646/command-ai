"""Soldier vocabulary → order vocabulary, appended to the retrieval query.

Found on 2026-08-18 from a live question a soldier's friend typed:
„תוך כמה זמן אני אמור לקבל תור לקפ"ס". Retrieval brought farewell ceremonies,
reprimands and sociometry. Re-run locally with the same words except the
last: „…לקב"ן" — still garbage; „…לקצין בריאות הנפש" — the mental-distress
order 33.0219, the individual-welfare order 35.0822 and the medical-transfer
order 31.0116 enter the window. The orders write „גורם ברה"ן", „בריאות הנפש",
„קצין בריאות הנפש"; soldiers write קב"ן, פסיכולוג, קפ"ס. The embedding does not
bridge the two vocabularies and the lexical bonus has no shared stem, so the
generic words of the question („תור", „זמן") pick the documents. Appending the
order's own words for the soldier's term gives the lexical rerank a stem to
match and moves the embedding toward the topic; the question itself is kept
whole, so a term that maps to nothing changes nothing.

This is deliberately NOT the model rewrite that was measured and rejected
(`night/rewrite.py`, 345/415): it is a fixed table, applied only to whole
tokens, additive, and it costs nothing per question. It is also not typo
repair — the typo pass (`_NORMALIZE_PROMPT`) still runs on unknown words.

Rules for an entry: the soldier form must be a term a soldier actually types
(abbreviation, slang, everyday word), the expansion must be words that appear
in the orders that answer that topic, and the mapping must be unambiguous in
army context. Off by default in code (`RETRIEVE_GLOSSARY`); measured with the
free 415-case gate before it is turned on anywhere.
"""
from __future__ import annotations

import os
import re

RETRIEVE_GLOSSARY = os.environ.get("RETRIEVE_GLOSSARY", "0") == "1"

# soldier form (as typed; quotes normalised, finals as written) -> expansion
GLOSSARY: dict[str, str] = {
    # Two conditions, both required. The soldier form must be rare or absent in
    # the orders' own text, AND the expansion must be a phrase that points at
    # the answering document rather than at the whole subject area — a target
    # that appears in hundreds of chunks moves the query toward the average of
    # them and away from the one that answers (see מילואימניק below).
    # Only terms whose soldier form is rare or absent in the orders' own text
    # (checked against the index on 2026-08-18: קב"ן 14 chunks, גימלים 1,
    # סופ"ש 0, מילואימניק 3, סמ"פ 1, קפ"ץ 4). Abbreviations the orders use
    # themselves (מ"פ 115, ת"ש 396, שליש 375, פרופיל, ולת"ם, קל"ב, טירונות…)
    # are NOT here: expanding them adds generic words to a query the lexical
    # match already understands, and the first gate run showed exactly that —
    # 'מ"פ' + 'פרופיל' drifted a discharge question off its order for no gain.
    # mental health (קפ"ס = the Air Force's name for the same officer — the
    # user's friend typed it on 2026-08-18 and got "not in the orders")
    'קב"ן': 'קצין בריאות הנפש ברה"ן מצוקה נפשית',
    'קבן': 'קצין בריאות הנפש ברה"ן מצוקה נפשית',
    'קפ"ס': 'קצין בריאות הנפש ברה"ן מצוקה נפשית',
    'קפס': 'קצין בריאות הנפש ברה"ן מצוקה נפשית',
    'פסיכולוג': 'בריאות הנפש ברה"ן',
    'פסיכולוגית': 'בריאות הנפש ברה"ן',
    # sick days / medical
    'גימלים': 'ימי ג יום ג רופא',
    'גימל': 'יום ג רופא',
    "ג'ימלים": 'ימי ג יום ג רופא',
    'חדר מיון': 'בית חולים אזרחי טיפול רפואי דחוף',
    # leave
    'סופ"ש': 'סוף שבוע שבת חופשה',
    'סופש': 'סוף שבוע שבת חופשה',
    'חופש': 'חופשה',
    # reserve
    'צו 8': 'צו קריאה שירות מילואים בשעת חירום',
    'צו שמונה': 'צו קריאה שירות מילואים בשעת חירום',
    # מילואימניק/מילואימניקים are NOT here, and the reason is the second half
    # of the rule above, which the first version of this file left implicit:
    # the soldier form must be rare AND the expansion must DISCRIMINATE.
    # "מילואימניק" is rare enough (5 occurrences), but "חייל מילואים" appears
    # 471 times — appending it does not bridge to the answering document, it
    # dilutes the query toward every reserve document at once. Measured on the
    # 30 blind reserve questions of 2026-08-24: the glossary touched 8, changed
    # the served window in 8 of 8, and pushed the raw question's top-ranked
    # document OUT of the window in 3 — including "אני מילואימניק וסטודנט,
    # למה לא קיבלתי את דמי הלימודים", where זכויות-סטודנטים-מילואים was ranked
    # first and the router had already picked it. Contrast the entry that
    # works: קב"ן → "גורם ברה"ן", whose target appears 37 times.
    'תגמולים': 'תגמול ביטוח לאומי',
    # people / roles — the UNQUOTED slang spellings only; the quoted forms
    # are the orders' own
    'סמ"פ': 'סגן מפקד פלוגה',
    'סמפ': 'סגן מפקד פלוגה',
    'מפ': 'מפקד פלוגה',
    'מכ': 'מפקד כיתה',
    'תש': 'תנאי שירות',
    'קפ"ץ': 'קצין פניות הציבור',
    # money
    'בונוס': 'תוספת תשלום מענק',
    # discipline
    'לחטוף': 'עונש דין משמעתי',
    # misc
    'ולתם': 'ועדה לתיאום מילואים',
    'קלב': 'קרוב לבית העברה',
    'שק"ם': 'קנטינה',
    # ── 2026-09-18 batch — soldier words with ZERO (or near-zero) occurrences
    # in the index, each expanded to a phrase that lives almost entirely in
    # the answering order (counts: night/head100/out/glossary_counts.txt;
    # criterion written before measuring: night/head100/GLOSSARY_CRITERION.md).
    # work permit: soldiers say "אישור עבודה" (0 chunks); the order says
    # "היתר עבודה פרטית" (33.0115 carries 44 of the 61 "עבודה פרטית" chunks)
    'אישור עבודה': 'היתר עבודה פרטית',
    # "טרטור" (0 chunks) is the soldiers' word for what 33.0351 calls
    # "תרגול נוסף" (36 of 39 chunks)
    'טרטור': 'תרגול נוסף',
    'טרטורים': 'תרגול נוסף',
    'טירטור': 'תרגול נוסף',
    'טירטורים': 'תרגול נוסף',
    'לטרטר': 'תרגול נוסף',
    'מטרטר': 'תרגול נוסף',
    'מטרטרים': 'תרגול נוסף',
    # a weekend taken away as a response to conduct is "מניעת חופשה"
    # (PM-33.0352: 58 of 68 chunks). Only the PUNITIVE phrasings: "סוגר שבת"
    # is also the routine rotation and stays out on purpose.
    'הוריד לי שבת': 'מניעת חופשה',
    'הורידו לי שבת': 'מניעת חופשה',
    'הורידו לו שבת': 'מניעת חופשה',
    'להוריד שבת': 'מניעת חופשה',
    'שבת עונש': 'מניעת חופשה',
    'עונש קולקטיבי': 'מניעת חופשה באופן קולקטיבי',
    'ענישה קולקטיבית': 'מניעת חופשה באופן קולקטיבי',
    # discharge leave
    'חפשש': 'חופשת שחרור',
    'חפש"ש': 'חופשת שחרור',
    # a neutral synonym only — a phone question may be about restrictions
    # (21.0113) or about compensation for a broken one (35.0223), so the
    # expansion must not pick the order
    'פלאפון': 'טלפון',
    'פלאפונים': 'טלפון',
    'סמארטפון': 'טלפון',
    'סמארטפונים': 'טלפון',
    # the orders' own נקח"ל sits mostly in 30.0408 (archive files); the
    # full name lives only in 33.0336 (66 chunks)
    'נקחל': 'נציב קבילות החיילים',
    'נקח"ל': 'נציב קבילות החיילים',
    # how a soldier talks about a friend (or himself) in danger — none of
    # these phrases exists in the index; 33.0219 writes "מצוקה נפשית" and
    # "אובדני" (13 chunks, all there)
    'נמאס לו מהחיים': 'מצוקה נפשית אובדני',
    'נמאס לי מהחיים': 'מצוקה נפשית אובדני',
    'נמאס לה מהחיים': 'מצוקה נפשית אובדני',
    'לא רוצה לחיות': 'מצוקה נפשית אובדני',
    'לפגוע בעצמי': 'מצוקה נפשית אובדני',
    # sexual harassment is rarely named by the person it happened to
    'נגע בי': 'פגיעה על רקע מיני הטרדה מינית',
    'נגע בה': 'פגיעה על רקע מיני הטרדה מינית',
    'נגעו בי': 'פגיעה על רקע מיני הטרדה מינית',
    'הערות מיניות': 'פגיעה על רקע מיני הטרדה מינית',
    'הצעות מיניות': 'פגיעה על רקע מיני הטרדה מינית',
    # release money
    'כסף של השחרור': 'מענק שחרור פיקדון',
    'כסף מהשחרור': 'מענק שחרור פיקדון',
    # appearance
    'לק': 'לק לציפורניים',
    # "רגילה" alone is also an adjective (52 chunks) — only the noun forms
    'ימי רגילה': 'חופשה שנתית',
    'לרגילה': 'חופשה שנתית',
    'ברגילה': 'חופשה שנתית',
}

_QUOTES = str.maketrans({"״": '"', "”": '"', "“": '"', "׳": "'", "’": "'"})
_PREFIXES = "ולבמכשה"
_STRIP = "?.,:;!()[]…—-"


def _norm(tok: str) -> str:
    return tok.translate(_QUOTES).strip(_STRIP).strip("\"'")


def _candidates(tok: str) -> list[str]:
    """The token as written, then progressively prefix-stripped (a soldier
    writes לקב"ן, בסופ"ש, מהמ"פ). Keeps every intermediate form."""
    t = _norm(tok)
    out = [t]
    p = t
    while len(p) > 2 and p[0] in _PREFIXES:
        p = p[1:]
        out.append(p)
    return out


def expansions(query: str) -> list[str]:
    """Expansion phrases for the glossary terms present in `query` (whole
    tokens, and the two-word entries), in order of appearance, deduplicated."""
    toks = query.split()
    found: list[str] = []
    seen: set[str] = set()
    joined = " ".join(_norm(t) for t in toks)
    # two-word entries first (e.g. 'צו 8', 'חדר מיון'): match against the
    # normalised text, then single tokens. Up to two prefix letters may
    # precede the first word — a soldier writes "הקפיצו אותי בצו 8" and
    # "אמר שנמאס לו מהחיים", and single tokens already get the same
    # treatment in _candidates (2026-09-18, measured as gl2)
    for term, exp in GLOSSARY.items():
        if " " in term and re.search(
                rf"(?<![א-ת])[{_PREFIXES}]{{0,2}}{re.escape(term)}(?![א-ת])", joined):
            if exp not in seen:
                found.append(exp); seen.add(exp)
    for tok in toks:
        for cand in _candidates(tok):
            if len(cand) < 2 or " " in cand:
                continue
            exp = GLOSSARY.get(cand)
            if exp and exp not in seen:
                found.append(exp); seen.add(exp)
                break
    # RETRIEVE_HOMONYMS: the active senses of an ambiguous term (see HOMONYMS
    # below). Off ⇒ nothing here runs and the list above is byte-identical.
    if RETRIEVE_HOMONYMS:
        for exp in homonym_expansions(query):
            if exp not in seen:
                found.append(exp); seen.add(exp)
    return found


def expand(query: str) -> str:
    """The query with its expansions appended — the question itself is never
    altered, so an empty match returns it byte-for-byte."""
    ex = expansions(query)
    return f"{query} {' '.join(ex)}" if ex else query


# ── Homonyms: one soldier token, more than one order meaning (22.09) ─────────
# The paid head-100 run (22.09) passed 38/40 full answers after review and
# failed its "zero confident-and-wrong" clause on two answers with ONE
# mechanism: an acronym or slang word with more than one army meaning, read
# in the wrong sense — once by retrieval (the answering order at rank 11, no
# glossary entry), once by the model (the answering order was in the sources
# and the acronym was read in its other sense). The table below is the class,
# not the two cases; every sense is grounded in an order that uses it
# (scratchpad/homonym_scan.py, 22.09 — where each candidate appears in the
# curated blocks and the raw text, with contexts). A candidate with no sense
# in the corpus (רמ"פ, משא"ז) is not here: there is nothing to anchor it to.
#
# Two consumers, two flags, both OFF (byte-identical) until measured against
# the locked set of night/HOMONYMS_CRITERION.md:
#   RETRIEVE_HOMONYMS  — expansions() also appends the ACTIVE senses'
#                        expansions (retrieval side; free instruments);
#   ANSWER_TERM_NOTE   — backend._compose_user_content adds one line of
#                        term clarification to the user turn (answer side;
#                        the paid mini-check).
# A sense is active when its `cue` matches the question; when no cue matches,
# every sense is active — a real ambiguity is passed on as one, never guessed.
# An empty `expand` means the orders use the acronym themselves (the existing
# glossary rule: expanding those dilutes) and the sense exists for the note.
RETRIEVE_HOMONYMS = os.environ.get("RETRIEVE_HOMONYMS", "0") == "1"

_P = f"[{_PREFIXES}]{{0,2}}"
HOMONYMS: list[dict] = [
    {"term": 'ת"ש', "pattern": rf'(?<![א-ת]){_P}ת"ש(?![א-ת])',
     "senses": [
         # 27 orders: ענף ת"ש, קצין הת"ש, רכז הת"ש, רכב ת"ש
         {"label": 'תנאי שירות (משק"ית ת"ש, קצין הת"ש)',
          "cue": r'משק|מש"ק|קצינ|רכז|ענף|מדור|תנאי|רכב|סיוע', "expand": ""},
         # PM-33.0213: „שעת טרום השינה (שעת ט"ש)" — a soldier writes שעת ת"ש
         {"label": 'שעת טרום השינה (ט"ש)',
          "cue": r"שע[הת]|שעות|שינה|לישון|ישן|לילה|ערב|להעיר|לפני",
          "expand": 'שעת טרום השינה ט"ש שינה סדורה'},
     ]},
    {"term": "יום ב'", "pattern": rf"(?<![א-ת]){_P}(?:יום|ימי) ב(?![א-ת])",
     "senses": [
         # 61.0104: „יום ב — אישור שחייל מוגבל בכושר עבודתו"
         {"label": "אישור רפואי „יום ב'\" (הגבלה בכושר עבודה)",
          "cue": r"רופא|חולה|מחלה|מרפאה|אישור|רפוא|חובש|ימי ב",
          "expand": "יום ב אישור רופא הגבלה בכושר עבודה"},
         {"label": "יום שני בשבוע",
          "cue": r"בשבוע|שבוע|יום א|יום ג|ראשון|שלישי|מחר|אתמול|בבוקר|בערב|תאריך", "expand": ""},
     ]},
    {"term": 'מ"מ', "pattern": rf'(?<![א-ת]){_P}מ"מ(?![א-ת])',
     "senses": [
         # 31.0215, 32.0201, 32.0316 — מפקד מחלקה
         {"label": "מפקד מחלקה", "cue": r'(?<!\d)(?<!\d )מ"מ', "expand": "מפקד מחלקה"},
         # 33-05-01: „קוטר עד 3 מ"מ"
         {"label": "מילימטר", "cue": r'\d\s*מ"מ', "expand": ""},
     ]},
    {"term": "שליש", "pattern": rf"(?<![א-ת]){_P}שליש(?![א-ת])",
     "senses": [
         # 18 orders — the orders use the word, so no expansion (dilution rule)
         {"label": "קצין השלישות (שליש היחידה)",
          "cue": r"שליש ה?יחיד|לשליש|השליש|שלישות|לפנות|לבקש|שאל", "expand": ""},
         # 36.0505: „שכ"ד בגובה שליש מן המשכורת"
         {"label": "חלק שלישי (שליש מן הסכום)",
          "cue": r"\d\s*שליש|שליש מ(?:ן|ה)|שלישים|משכורת|סכום|מהשכר", "expand": ""},
     ]},
    {"term": 'תב"ן', "pattern": rf'(?<![א-ת]){_P}תב"ן(?![א-ת])',
     "senses": [
         # 35.0809: „ביטול תב"ן" — the unpaid extension of service
         {"label": "תקופה בלתי נמנית — הארכת השירות (ביטול תב\"ן, 35.0809)",
          "cue": r"ביטול|לבטל|הארכ|שחרור|לשחרר|מאריכ|תוספת שירות|להשתחרר", "expand": ""},
         # PM-33.0302: „מה זה תב"ן ומתי המחבוש שלי נחשב תקופה בלתי-נמנית"
         {"label": "תקופה בלתי נמנית — מחבוש שאינו נמנה בשירות (דין משמעתי)",
          "cue": r"מחבוש|עונש|נשפט|דין|כלא|קצין שיפוט", "expand": ""},
     ]},
    # single-sense acronyms the orders spell out differently — the same
    # table, the same flag, the same measurement
    {"term": 'תשמ"ש', "pattern": rf'(?<![א-ת]){_P}תשמ"ש(?![א-ת])',
     "senses": [
         # 35.0210 „מדור תשמ"ש ופרט" / „בקשת החייל לתשלום משפחתי"; 56.0131 „זכאי תשמ"ש"
         {"label": "תשלומי משפחה (תשלום משפחתי)", "cue": "", "expand": "תשלום משפחתי תשלומי משפחה למשפחות חיילים"},
     ]},
    {"term": 'קל"ב', "pattern": rf'(?<![א-ת]){_P}קל"ב(?![א-ת])',
     "senses": [
         # 31.0116: „שיבוץ קרוב לבית (קל"ב)"
         {"label": "שיבוץ קרוב לבית", "cue": "", "expand": "שיבוץ קרוב לבית"},
     ]},
    {"term": 'ש"ג', "pattern": rf'(?<![א-ת]){_P}ש"ג(?![א-ת])',
     "senses": [
         # PM-33.0302 „מש"ק ש"ג"; הק"א 33-05-01 §5 „חייל היוצא מהמחנה"
         {"label": "שער המחנה (השומר בשער)", "cue": "", "expand": "שער המחנה יציאה מהמחנה כניסה למחנה"},
     ]},
]


def homonym_senses(query: str) -> list[tuple[str, list[dict]]]:
    """(term, active senses) for every homonym present in `query`. A sense is
    active when its cue matches the question; no cue matching ⇒ all senses."""
    joined = " ".join(_norm(t) for t in query.split())
    out: list[tuple[str, list[dict]]] = []
    for h in HOMONYMS:
        if not re.search(h["pattern"], joined):
            continue
        active = [s for s in h["senses"] if s.get("cue") and re.search(s["cue"], joined)]
        out.append((h["term"], active or list(h["senses"])))
    return out


def homonym_expansions(query: str) -> list[str]:
    """The active senses' expansion phrases, in order, deduplicated."""
    found: list[str] = []
    for _term, senses in homonym_senses(query):
        for s in senses:
            if s.get("expand") and s["expand"] not in found:
                found.append(s["expand"])
    return found


def term_note(query: str) -> str:
    """One line for the answer side (ANSWER_TERM_NOTE): what the ambiguous
    term in the question means here — the one active sense when the context
    settles it, all of them when it does not. Empty when nothing is ambiguous."""
    parts: list[str] = []
    for term, senses in homonym_senses(query):
        if len(senses) == 1:
            parts.append(f"{term} = {senses[0]['label']}")
        else:
            parts.append(f"{term} — {' או '.join(s['label'] for s in senses)}, לפי ההקשר")
    return "; ".join(parts)
