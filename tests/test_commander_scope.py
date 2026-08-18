# -*- coding: utf-8 -*-
"""backend._docs_for_role: the commander persona sees the whole corpus.

2026-08-18: five held-out commander questions about a soldier's urgent family
leave could not be answered by any retrieval change, because the answering
order (פ"מ 35.0402, חופשות בשירות חובה) is tagged ['soldier'] and the commander
scope was tag-only. A commander asks on behalf of subordinates; the tag says
whom the order governs, not who may ask about it. Pinned here: commander ⊇
soldier ∪ reserve, the soldier scope is still tag-bound, and the env switch
restores the old behaviour for measurement.
"""
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend


def _ids(role):
    return {d["document_id"] for d in backend._docs_for_role(role) if d.get("document_id")}


def test_commander_sees_soldier_only_and_reserve_only_orders():
    assert backend.COMMANDER_SCOPE_ALL
    all_ids = {d["document_id"] for d in backend.load_documents() if d.get("document_id")}
    assert _ids("commander") == all_ids
    # the case that motivated it
    assert "PM-35.0402" in _ids("commander")


def test_soldier_scope_is_still_tag_bound():
    docs = {d["document_id"]: d for d in backend.load_documents() if d.get("document_id")}
    commander_only = [i for i, d in docs.items() if sorted(d.get("roles") or []) == ["commander"]]
    assert commander_only, "corpus has no commander-only order to test against"
    assert commander_only[0] not in _ids("soldier")


def test_env_switch_restores_tag_only_commander_scope():
    old = os.environ.get("COMMANDER_SCOPE_ALL")
    os.environ["COMMANDER_SCOPE_ALL"] = "0"
    try:
        importlib.reload(backend)
        assert not backend.COMMANDER_SCOPE_ALL
        assert "PM-35.0402" not in _ids("commander")
    finally:
        if old is None:
            os.environ.pop("COMMANDER_SCOPE_ALL", None)
        else:
            os.environ["COMMANDER_SCOPE_ALL"] = old
        importlib.reload(backend)
    assert backend.COMMANDER_SCOPE_ALL


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all commander-scope tests passed")
