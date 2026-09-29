# -*- coding: utf-8 -*-
"""Page-verified sentences in the support audit (night/support_audit.py, coverage_audit.page_verified_sentences).

A block sentence the raw text cannot support (3.0501 §51ז: 0.48 on scrambled raw, and on the clean text too) is
not counted as low support when a curator read it on the page image and recorded it WITH page and section, exactly
as it stands. Without page or section, or after the sentence is edited, it counts again. apply_defs carries a def
clause's list into the order's `recurated` record.

    venv\\Scripts\\python.exe tests\\test_support_page_verified.py
"""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import night.support_audit as sa
from night import coverage_audit as ca
from night.recurate import apply_defs as AD

SENTENCE = "חייל בשירות קבע שמונה לתפקיד בחוץ לארץ יקבל תוספת שהות לפי החלטת ועדת המשכורות של אגף כוח האדם."
DOC = {"document_id": "X.1", "raw_text": "פרק א הגדרות. 1. בפקודה זו מסדר בוקר יתקיים בכל יום. 2. המפקד יקבע את השעה.",
       "sections": [{"id": "key-facts", "title": "עיקרי הפקודה",
                     "clauses": [{"number": "תוספת שהות בחו\"ל", "text": SENTENCE}]}]}
ENTRY = {"clause": "תוספת שהות בחו\"ל", "sentence": SENTENCE, "page": "עמ' 12", "src": "סעיף 51ז"}


def audit(doc):
    df, n = sa.build_df([doc])
    return sa.audit_doc(doc, df, n)


def with_entry(**change):
    d = copy.deepcopy(DOC)
    d["recurated"] = {"page_verified_sentences": [{**ENTRY, **change}]}
    return d


def test_a_low_support_sentence_counts_without_an_entry():
    r = audit(DOC)
    assert r["low_support"] == 1 and r["page_verified"] == 0, r


def test_a_page_verified_sentence_is_waived_and_counted_apart():
    r = audit(with_entry())
    assert r["low_support"] == 0 and r["page_verified"] == 1, r


def test_page_and_section_are_both_required():
    assert audit(with_entry(page=""))["low_support"] == 1
    assert audit(with_entry(src=""))["low_support"] == 1


def test_an_edited_sentence_must_be_read_again():
    d = with_entry()
    d["sections"][0]["clauses"][0]["text"] = SENTENCE.replace("ועדת המשכורות", "ועדת השכר")
    assert audit(d)["low_support"] == 1
    assert ca.is_page_verified_sentence(ca.page_verified_sentences(with_entry()), ENTRY["clause"],
                                        SENTENCE.replace("  ", " ")), "white space and quote marks do not matter"


def test_apply_defs_carries_the_list_into_the_record():
    defn = {"when": "2026-09-30", "clauses": [{"number": ENTRY["clause"], "text": SENTENCE,
                                               "page_verified_sentences": [{k: ENTRY[k] for k in ("sentence", "page", "src")}]}]}
    rec = AD.recurated_record({"recurated": {"page_verified_numbers": [{"clause": "a", "numbers": ["1"]}]}},
                              defn, "X.1.json", [], {})
    assert rec["page_verified_numbers"], "merge, never overwrite"
    got = rec["page_verified_sentences"]
    assert len(got) == 1 and got[0]["clause"] == ENTRY["clause"] and got[0]["page"] == "עמ' 12" and got[0]["def"] == "X.1.json"
    assert (ENTRY["clause"], ca._pv_sentence(SENTENCE)) in ca.page_verified_sentences({"recurated": rec})


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)
