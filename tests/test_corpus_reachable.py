# -*- coding: utf-8 -*-
r"""Every order in json_store must be reachable by retrieval, or be listed here.

Found 2026-08-26 while characterising the block-depth lever of the mini-pilot
150. `RETRIEVE_CURATED_ONLY` defaults to 1, and `retrieve_for_role` drops any
document without a `key-facts` section before it builds `doc_ids` — so a
document with no curated block is not merely ranked poorly, it is **absent from
the search space**. Verified directly: asking for 36.0301 with the router's
shortlist naming it outright still returns nothing.

Eleven of the 294 orders are in that state, and they are not small — 33.1010
(17K chars, serving a court-martial sentence), 31.0515 (17K, tracing absentees),
3.0502 (11K, career-service discharge grant), 36.0301 (9K, activity supplement),
31.0252 (9K, reserve officer appointments). 36.0301 is not hypothetical: the
adjudication named it as the document that answers q00177 ("השכר שלי השבוע היה
יותר נמוך…"), and no retrieval fix can reach it while it has no block.

This test does not demand they be curated — that costs money and is a decision
for whoever owns the corpus. It pins the set so it cannot grow silently, which
is the failure that actually hurts: a new order ingested without curation looks
present in every count and answers nothing. Curating one of these SHRINKS the
set and keeps the test green.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend

# Known-uncurated as of 2026-08-26. Shrinking this list is progress; a document
# outside it means something was ingested without a curated block.
KNOWN_UNCURATED = {
    "3.0502", "31.0252", "31.0515", "33.1010", "35.0203", "35.0232",
    "35.0810", "36.0301", "36.0313", "PM-33.0109", "PM-33.0342",
}


def _uncurated() -> set[str]:
    return {d["document_id"] for d in backend.load_documents()
            if d.get("document_id") and not backend._has_key_facts(d)}


def test_no_new_order_arrives_without_a_curated_block():
    surprises = _uncurated() - KNOWN_UNCURATED
    assert not surprises, (
        f"ingested without a curated block, so retrieval cannot reach them: "
        f"{sorted(surprises)}"
    )


def test_the_curated_filter_really_is_what_hides_them():
    """Pins the mechanism, not just the symptom."""
    assert backend.RETRIEVE_CURATED_ONLY, (
        "this test's premise is RETRIEVE_CURATED_ONLY=1; with it off the "
        "uncurated orders are reachable and the list above means nothing"
    )
    docs = backend._docs_for_role("commander")
    searchable = {d["document_id"] for d in docs if backend._has_key_facts(d)}
    for doc_id in _uncurated():
        assert doc_id not in searchable, doc_id


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all corpus-reachability tests passed")
