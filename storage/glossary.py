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
    'מילואימניק': 'חייל מילואים',
    'מילואימניקים': 'חיילי מילואים',
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
    # normalised text, then single tokens
    for term, exp in GLOSSARY.items():
        if " " in term and re.search(rf"(?<![א-ת]){re.escape(term)}(?![א-ת])", joined):
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
    return found


def expand(query: str) -> str:
    """The query with its expansions appended — the question itself is never
    altered, so an empty match returns it byte-for-byte."""
    ex = expansions(query)
    return f"{query} {' '.join(ex)}" if ex else query
