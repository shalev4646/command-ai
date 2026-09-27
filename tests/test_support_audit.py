# -*- coding: utf-8 -*-
"""night.support_audit — the reverse of the coverage audit: block sentences the order does not support.

The cases are the ones that made the tool (27.09): 36.0505's old block turned סע' 28 („60 יום…
1/30 של משכורתו") into „30 יום… שליש מן המשכורת", and a review then wrongly called סע' 29 invented
because a text search missed the acronym „עת"ש". No network, no model; synthetic orders.

    venv\\Scripts\\python.exe tests\\test_support_audit.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from night import support_audit as sa

RAW_0505 = ("26. קצין ת\"ש, שבתחומו נמצא הדיור, יפנה את החייל לנציג מחלקת השירותים ואחזקה, מצויד באישור מתאים בכתב. "
            "27. התייצב החייל בפני נציג מחלקת שירותים ואחזקה והציג את האישור על הקצאת דיור צבאי, יחתום על המסמכים. "
            "28. לא נכנס החייל להתגורר בדיור הצבאי תוך 60 יום ממועד החלטת הוועדה לזכאותו לדיור הצבאי, תפקע זכאותו "
            "לדיור הצבאי, והוא יחויב בתשלום שכר דירה עבור תקופה זו, ותשלום 1/30 של משכורתו. "
            "29. ראש עת\"ש קבע באכ\"א-פרט יהיה רשאי, במקרים חריגים, לאשר לחייל להיכנס להתגורר בדיור הצבאי במועד מאוחר "
            "מהמצוין בסעיף 28 לעיל. 30. חייל המתגורר בדיור, יחויב במס הכנסה על הכנסה זקופה, בהתאם להוראות מס הכנסה. "
            + " ".join(f"{32 + i}. ועד הדיירים יטפל בבעיות המוניציפליות של השכונה ויעביר את בקשות הדיירים לקצין הרלוונטי."
                       for i in range(8)) +
            " 41. בחישוב הוותק יובאו בחשבון 16 ימי חופשה שנתית בכל שנה, לפי הכללים הקבועים בפקודות.")
OTHER = ("5. חייל בשירות חובה זכאי לחופשה שנתית של ימים בכל שנה בתיאום עם מפקדו. 6. מפקד היחידה רשאי לאשר "
         "חופשה מיוחדת במקרים חריגים. 7. הדגל יונף בכל יום לפני שעת ההשכמה ויורד לפני השקיעה.")


def _doc(raw, sentences, did="X"):
    return {"document_id": did, "raw_text": raw,
            "sections": [{"id": "key-facts", "clauses": [{"number": f"c{i}", "text": s} for i, s in enumerate(sentences)]}]}


def _audit(doc):
    corpus = [doc, _doc(OTHER, [], "Y"), _doc(RAW_0505, [], "Z")]
    df, n = sa.build_df(corpus)
    return sa.audit_doc(doc, df, n)


def test_the_acronym_of_the_order_supports_the_spelled_out_role():
    words = sa._words("בנסיבות חריגות ראש ענף תנאי שירות קבע באכ\"א יכול לאשר כניסה מאוחרת יותר")
    hit = sa.acronym_covered(words, sa.acronyms(RAW_0505))
    assert {words.index("ענף"), words.index("תנאי"), words.index("שירות")} <= hit, (words, hit)
    r = _audit(_doc(RAW_0505, ["בנסיבות חריגות, ראש ענף תנאי שירות קבע באכ\"א יכול לאשר לחייל להיכנס לדיור במועד מאוחר."]))
    assert not any(f["support"] < sa.SUPPORT_MIN for f in r["flags"]), r["flags"]


def test_a_fraction_the_passage_does_not_carry_is_flagged():
    r = _audit(_doc(RAW_0505, ["אם לא התגורר בדיור, זכאותו תפקע ויחויב בתשלום שכר דירה בגובה שליש מן המשכורת."]))
    assert any("שליש" in f["fractions"] for f in r["flags"]), r["flags"]


def test_a_number_from_elsewhere_in_the_order_is_flagged_locally():
    """„16" is in the order (סע' 31), so the old whole-text numbers gate passes it; the passage that
    carries the rule (סע' 28) says 60."""
    good = ["לא נכנס החייל להתגורר בדיור הצבאי תוך 60 יום ממועד החלטת הוועדה, תפקע זכאותו לדיור הצבאי.",
            "בחישוב הוותק יובאו בחשבון 16 ימי חופשה שנתית בכל שנה לפי הכללים.",
            "החייל יחויב בתשלום שכר דירה עבור תקופה זו ובתשלום 1/30 של משכורתו.",
            "הזכאות לדיור הצבאי פוקעת אם החייל לא נכנס להתגורר בו תוך 60 יום ממועד החלטת הוועדה לזכאותו.",
            "בכל שנה יובאו בחשבון הוותק 16 ימי חופשה שנתית לפי הכללים הקבועים בפקודות."]
    bad = good + ["לא נכנס החייל להתגורר בדיור הצבאי תוך 16 יום ממועד החלטת הוועדה, תפקע זכאותו לדיור."]
    rg, rb = _audit(_doc(RAW_0505, good)), _audit(_doc(RAW_0505, bad))
    assert rg["trusted_digits"] and not any(f["numbers"] for f in rg["flags"]), rg["flags"]
    assert any("16" in f["numbers"] for f in rb["flags"]), rb["flags"]


def test_a_sentence_from_another_order_has_no_support():
    r = _audit(_doc(RAW_0505, ["הדגל יונף בכל יום לפני שעת ההשכמה ויורד לפני השקיעה בכל יחידה."]))
    assert any(f["support"] < sa.SUPPORT_MIN for f in r["flags"]), r["flags"]


def test_clause_citations_are_not_numbers():
    assert sa.numbers_of("תוך 72 שעות (סעיפים 7, 9, 13, 23), כאמור בסעיף 45ב–ה, 1/30 של המשכורת, בשעה 08:00") == \
        ["72", "1/30", "08:00"]


def test_a_number_spelled_in_words_counts():
    assert sa.number_in("15", "עד חמישה עשר ימים לפני מועד שחרורו") and sa.number_in("60", "תוך שישים ימים")


def test_ocr_and_dropped_gershayim_are_the_same_acronym():
    raw = 'רמייח מופיית רשאי לאשר. אכ א ענף ת"ש. צה ל'
    assert sa._in_order('רמ"ח', raw, raw.replace('"', ""))
    assert sa._in_order('אכ"א', raw, raw.replace('"', ""))
    assert not sa._in_order('רנג"ד', raw, raw.replace('"', ""))


def test_ratchet_detects_a_rise():
    base = {"X": {"low_support": 1, "entity_misses": 0}}
    assert sa.regressions({"X": {"low_support": 1, "entity_misses": 0}}, base) == []
    assert sa.regressions({"X": {"low_support": 2, "entity_misses": 1}}, base)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {str(e)[:400]}")
    raise SystemExit(1 if fails else 0)
