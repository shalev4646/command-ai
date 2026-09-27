# -*- coding: utf-8 -*-
"""Page-verified numbers are not counted; unverified ones are (the manager's rule, 27.09).

A number read on the page image is right even when the scrambled raw_text lacks it (v161: 36.0505's
„60" and „1/30"). It is recorded on the order — doc["recurated"][...]["page_verified_numbers"] — and
then neither night.coverage_audit's check (d) nor night.support_audit's number check counts it, and
night.recurate.apply_defs lets it through the numbers gate when the def lists it in numbers_seen.
A number that entered without a page reading must still count: the scrambled orders are exactly the
dangerous ones. Both directions are locked here. No network, no model.

    venv\\Scripts\\python.exe tests\\test_page_verified_numbers.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from night import coverage_audit as ca
from night import support_audit as sa
from night.recurate import apply_defs as ad

LABEL = "כמה זמן יש לתפוס את הדיור לאחר הקצאה?"
TEXT = "לא נכנס החייל להתגורר בדיור הצבאי תוך 60 יום ממועד החלטת הוועדה, והוא יחויב בתשלום 1/30 של משכורתו."
RAW = ("28. לא נכנס החייל להתגורר בדיור הצבאי תוך 06 יום ממועד החלטת הוועדה לזכאותו, תפקע זכאותו, "
       "והוא יחויב בתשלום שכר דירה ותשלום 03/1 של משכורתו.")          # the scrambled layer, as in the corpus
V161_ENTRY = f"{LABEL}: ['60', '30']"                                   # session A's format, verbatim shape


def _clauses(label=LABEL, text=TEXT):
    return [{"number": label, "text": text, "digit_free": False}]


def test_session_a_format_is_read():
    pv = ca.page_verified({"recurated": {"numbers_fixed_v161_27.09": {"page_verified_numbers": [V161_ENTRY]}}})
    assert pv == {LABEL: {"60", "30"}}, pv
    assert ca.is_page_verified(pv, LABEL, "60") and ca.is_page_verified(pv, LABEL, "1/30")


def test_coverage_d_counts_unverified_and_skips_verified():
    assert ca.number_misses(RAW, _clauses()), "unverified numbers absent from raw must count"
    pv = ca.page_verified({"recurated": {"x": {"page_verified_numbers": [V161_ENTRY]}}})
    assert ca.number_misses(RAW, _clauses(), pv) == []


def test_verification_is_per_clause():
    pv = {"סעיף אחר לגמרי": {"60", "30"}}
    assert ca.number_misses(RAW, _clauses(), pv), "a reading recorded for another clause does not cover this one"


def test_a_wrong_value_next_to_a_verified_one_still_counts():
    pv = {LABEL: {"60", "30"}}
    miss = ca.number_misses(RAW, _clauses(text=TEXT.replace("60 יום", "45 יום")), pv)
    assert any("45" in m for m in miss), miss


def test_support_number_check_follows_the_same_rule():
    corps = ["הנדסה קרבית", "תותחנים ושריון", "מודיעין שדה", "תקשוב ואלקטרוניקה", "לוגיסטיקה ותחזוקה",
             "רפואה צבאית", "משטרה צבאית", "חינוך ונוער", "הגנה אווירית", "ספנות ונמלים"]
    body = " ".join(f"{40 + i}. לוחם במערך {c} זכאי ל-{11 + i} ימי השתלמות מקצועית בכל שנת עבודה."
                    for i, c in enumerate(corps))
    raw = RAW + " " + body + " 60. בחישוב הוותק יובאו בחשבון 45 ימי חופשה."
    good = ["לא נכנס החייל להתגורר בדיור הצבאי תוך 60 יום ממועד החלטת הוועדה, תפקע זכאותו."] + [
        f"לוחם במערך {c} זכאי ל-{11 + i} ימי השתלמות מקצועית בכל שנת עבודה." for i, c in enumerate(corps[:6])]
    doc = {"document_id": "T", "raw_text": raw,
           "sections": [{"id": "key-facts", "clauses": [{"number": LABEL if i == 0 else f"c{i}", "text": t}
                                                        for i, t in enumerate(good)]}]}
    df, n = sa.build_df([doc])
    r = sa.audit_doc(doc, df, n)
    assert r["trusted_digits"], r["numbers_local"]
    assert any("60" in f["numbers"] for f in r["flags"]), "unverified „60\" outside its passage (raw has 06) counts"
    doc["recurated"] = {"v161": {"page_verified_numbers": [V161_ENTRY]}}
    r = sa.audit_doc(doc, df, n)
    assert not any("60" in f["numbers"] for f in r["flags"]), r["flags"]


def test_apply_defs_takes_only_numbers_of_the_text_from_numbers_seen():
    defn = {"clauses": [{"number": LABEL, "text": TEXT,
                         "numbers_seen": ["60 יום", "1/30", "טופס 376 — בתמונה; ב-raw_text „372\""]}]}
    seen = ad.seen_numbers(defn)
    assert "60" in seen[LABEL] and "372" not in seen[LABEL] and "376" not in seen[LABEL], seen
    section = {"clauses": [{"number": LABEL, "text": TEXT}]}
    assert ad.gate(section, RAW, False)[2], "without numbers_seen the numbers gate rejects"
    assert not ad.gate(section, RAW, False, seen)[2], "page-read numbers pass the numbers gate"


def test_apply_defs_merges_the_record_and_keeps_earlier_readings():
    doc = {"recurated": {"numbers_fixed_v161_27.09": {"page_verified_numbers": [V161_ENTRY]}}}
    rec = ad.recurated_record(doc, {"when": "2026-09-28"}, "x.json", [], {"c1": ["14"]})
    assert "numbers_fixed_v161_27.09" in rec, "another wave's notes survive"
    pv = ca.page_verified({"recurated": rec})
    assert pv[LABEL] == {"60", "30"} and pv["c1"] == {"14"}, pv


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
