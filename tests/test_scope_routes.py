# -*- coding: utf-8 -*-
"""Rule 2א — the refusal tiers.

Guards the contract between three files that must agree or the feature silently
degrades to the old dead end: scope_routes defines the marker strings, backend
bakes them into the system prompt, and app.py matches them to pick the chip label.
A change to any one of the three alone breaks the chain with no runtime error.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Importing app.py runs _startup_ingest -> backend.ensure_pdfs_ingested, which
# ingests every PDF in pdf-ldf_law/ that no JSON claims — through the paid API,
# outside the night/ ledger, and writing over the destination JSON. Running this
# file on 2026-08-17 ingested four duplicate downloads and cost three orders
# their curated key-facts blocks. The ingest path now preserves them, but a test
# has no business ingesting at all: stub it BEFORE the import, because app.py
# binds the name at import time and a later patch is too late.
import backend  # noqa: E402

backend.ensure_pdfs_ingested = lambda *a, **k: []

import scope_routes  # noqa: E402


def test_markers_are_distinct_and_nonempty():
    assert scope_routes.MARK_OUT_OF_SCOPE.strip()
    assert scope_routes.MARK_MISSING.strip()
    assert scope_routes.MARK_OUT_OF_SCOPE != scope_routes.MARK_MISSING
    # neither may be a prefix of the other: app.py tests membership, and a
    # prefix relation would make the first branch swallow the second
    assert not scope_routes.MARK_OUT_OF_SCOPE.startswith(scope_routes.MARK_MISSING)
    assert not scope_routes.MARK_MISSING.startswith(scope_routes.MARK_OUT_OF_SCOPE)


def test_every_route_is_complete():
    for key, r in scope_routes.ROUTES.items():
        assert r["label"].strip(), key
        assert r["covers"].strip(), key
        assert r["basis"].strip(), key      # why we believe it — no unsourced referrals
        # no_source is the one route with nowhere to send anyone
        if key != "no_source":
            assert r["where"].strip(), key


def test_prompt_block_lists_every_route():
    block = scope_routes.prompt_block()
    for key, r in scope_routes.ROUTES.items():
        assert r["label"] in block, key
        assert r["covers"] in block, key
        if r["where"]:
            assert r["where"] in block, key


def test_prompt_block_never_exposes_the_internal_keys():
    """A live run had the model copy the dict key into a Hebrew answer
    ('**לא נקבע בפקודות מטכ"ל:** civil_law'). The model must never be shown a
    token it is not allowed to repeat."""
    block = scope_routes.prompt_block()
    for key in scope_routes.ROUTES:
        assert key not in block, key


def test_backend_bakes_the_markers_into_every_persona():
    import backend
    for p in backend.SYSTEM_PROMPTS.values():
        assert scope_routes.MARK_OUT_OF_SCOPE in p
        assert scope_routes.MARK_MISSING in p
        assert scope_routes.prompt_block() in p
        # an unsubstituted placeholder would reach the model verbatim
        assert "{MARK_OUT}" not in p and "{MARK_MISS}" not in p and "{ROUTE_BLOCK}" not in p


def _chip(content):
    """app.py's chip gate, exercised through the real module."""
    import app
    return app._verdict_chip(content)


REFUSAL = "המידע לא קיים בפקודות שסופקו."


def test_out_of_scope_refusal_gets_the_routed_label():
    body = f"{REFUSAL}\n\n{scope_routes.MARK_OUT_OF_SCOPE} המוסד לביטוח לאומי — תגמולי מילואים משולמים על ידו."
    chip, _ = _chip(body)
    assert chip and "לא נקבע בפקודות" in chip
    assert "לא נמצא במאגר" not in chip


def test_missing_order_refusal_gets_its_own_label():
    body = f"{REFUSAL}\n\n{scope_routes.MARK_MISSING} מועדי הודעה מוקדמת על צו מילואים."
    chip, _ = _chip(body)
    assert chip and "טרם במאגר" in chip


def test_unmarked_refusal_still_falls_back_to_the_old_chip():
    chip, _ = _chip(REFUSAL + " אין בקטעים כלל שחל על המקרה.")
    assert chip and "לא נמצא במאגר" in chip


def test_a_real_ruling_is_never_labelled_a_refusal():
    body = '**פסיקה:** אסור — למפקד להעליב חייל\n**מקור:** פ"מ 33.0302 סעיף 12'
    chip, _ = _chip(body)
    assert chip is None or "לא נמצא" not in chip
    assert chip is None or "טרם במאגר" not in chip


def test_marker_on_an_answered_reply_does_not_create_a_refusal_chip():
    """The rule forbids markers on answers; if the model slips anyway, the chip
    must still not tell the user the answer was not found."""
    body = ('**פסיקה:** מותר בתנאים\n**מקור:** פ"מ 35.0402\n\n'
            f'{scope_routes.MARK_OUT_OF_SCOPE} הדין האזרחי')
    chip, _ = _chip(body)
    assert chip is None or ("לא נמצא" not in chip and "טרם במאגר" not in chip)


def test_body_keeps_the_routing_sentence():
    """The chip is a label; the useful part — where to actually go — must stay
    in the rendered body."""
    body = f"{REFUSAL}\n\n{scope_routes.MARK_OUT_OF_SCOPE} האגף והקרן לחיילים משוחררים"
    _, shown = _chip(body)
    assert "האגף והקרן לחיילים משוחררים" in shown


if __name__ == "__main__":
    # Plain-assert runner, matching every other suite in tests/. Without it
    # `python tests/test_scope_routes.py` imported the module, ran nothing and
    # exited 0 — eleven tests that looked green because they never executed.
    failures = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print("PASS", _name)
            except AssertionError as _exc:
                failures += 1
                print("FAIL", _name, "-",
                      str(_exc).encode("ascii", "replace").decode())
    sys.exit(1 if failures else 0)
