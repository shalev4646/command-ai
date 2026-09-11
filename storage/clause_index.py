# -*- coding: utf-8 -*-
"""The clause-title index: the soldier's question is matched to QUESTIONS,
not to order text.

Why this exists (night/PLAN_ROUND4.md, שלב 1.1)
-----------------------------------------------
Production picks 8 chunks out of 10,324 by how much a chunk SOUNDS like the
question, and a soldier does not talk like an order: "אני חם וחלש, מותר לי
ללכת לקליניקה?" shares not one word with "חייל המעוניין בטיפול רפואי יפנה
למפקדו". Measured on the adjudicated targets (2026-08-28, 2026-09-10) the
answering ORDER reaches the window three times more often than the answering
CLAUSE (18/59 vs 5/59), and 11 of the 45 realstyle zeros are "the order is in
the window, its answering clause is not".

The curator already wrote every curated clause in soldier language: its
`number` field is a question-shaped title ("אילו תכשיטים מותר לענוד עם
מדים?"), and 2,357 of the 2,378 curated clauses carry one. The anchor
questions (storage/anchor_clauses.json, 1,379 of them attributed to a clause
by night/anchor_attrib.py) are more of the same. This module indexes THOSE —
the titles and the anchors, mapped to their clause — and scores the question
against them. What it returns is evidence about which clause the asker needs,
at clause granularity; backend.extend_with_clause_index turns that into a
served block (שלב 1.2).

The scorer
----------
Character 4-grams over Hebrew letters and spaces, IDF-weighted — the same
instrument as night/why_default.py (AUC 0.758 on its calibration set) and
backend.lack_clause_scores, and the same normalisation, which
tests/test_clause_index.py keeps in step. Character grams, not words: Hebrew
glues prefixes and suffixes to the stem, and "קעקועים" / "קעקוע" / "הקעקוע"
are three words and one 4-gram. Units are short (a title is 4–10 words), so
the unit-length normalisation is sqrt(|grams|), as calibrated.

One unit is one (document, section, clause, grams) row; a clause's score is
the best of its units, a document's score is the best of its clauses and of
its document-level units.

Which units — measured, not assumed (night/titleprobe.py, 2026-09-11, the 83
adjudicated targets, answering order in the index's top-2 / answering clause
served under the V2 rule, of 53 whose clause is known):

    titles only                      20 / 16
    titles + attributed anchors      16 / 12
    + the order's title              15 / 12
    + unattributed anchor questions  14 / 12

Every kind added on top of the titles LOST ground: the anchors are longer and
share the generic question frame ("האם מותר לי", "מה קורה אם") with every
other anchor, so they win on frame and lose on topic. Production indexes the
TITLES ONLY (`kinds` default); the other kinds stay available for the
ablation and for a future re-measure on a set this one has not seen.

Pure: stdlib only, no model, no network. Built once per corpus by the caller
(backend caches it by the corpus stamp) — ~7,000 units, a few milliseconds a
query through the inverted gram index.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

NGRAM = 4
# log(n/(1+df)) goes to zero and then negative once a gram sits in more than
# half the units; a negative weight penalises a match. See why_default.MIN_IDF.
MIN_IDF = 0.01
# A unit too short to carry this many distinct 4-grams matches on a single
# shared word. "מה זה אפטר" is 10 characters and 7 grams — the floor sits just
# under the shortest real title.
MIN_GRAMS = 6

_HEB = re.compile(r"[^֐-׿ ]+")
_WS = re.compile(r"\s+")
ANCHOR_CLAUSES_PATH = Path(__file__).resolve().parent / "anchor_clauses.json"


def grams(text: str) -> frozenset:
    """Character 4-grams over Hebrew letters and spaces only. MUST stay
    identical to backend._lack_grams and night.why_default.grams."""
    t = _WS.sub(" ", _HEB.sub(" ", text or "")).strip()
    return frozenset(t[i:i + NGRAM] for i in range(len(t) - NGRAM + 1))


def _attrib_norm(s: str) -> str:
    """MUST mirror night/anchor_attrib._norm — anchor_clauses.json is keyed
    by it (the order's title, a space, the anchor question)."""
    s = re.sub(r'["״׳\'“”‘’]', "", s or "")
    return _WS.sub(" ", s).strip()


def _is_titled(number) -> bool:
    """A question-shaped clause title, as opposed to a bare clause number."""
    n = str(number or "").strip()
    return bool(n) and not re.fullmatch(r"[\d.\-–/ ]+", n)


def _is_curated(section: dict) -> bool:
    # the same predicate as backend._has_key_facts / backend._full_block: the
    # served block and the indexed titles must be the same clauses
    return "key-facts" in (section.get("id") or "")


def _question_lists(doc: dict) -> list[str]:
    """suggested_questions (flat list or {role: [..]}) plus anchor_questions —
    the strings vector_store indexes as `sq` anchors."""
    sq = doc.get("suggested_questions")
    lists = [qs for qs in sq.values() if isinstance(qs, list)] if isinstance(sq, dict) else [sq or []]
    out, seen = [], set()
    for qs in lists + [doc.get("anchor_questions") or []]:
        for q in qs:
            if isinstance(q, str) and q.strip() and q not in seen:
                seen.add(q)
                out.append(q.strip())
    return out


def load_anchor_clauses(path: Path = ANCHOR_CLAUSES_PATH) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


@dataclass
class Unit:
    doc_id: str
    section: str | None      # None = document-level evidence
    clause: str | None
    kind: str                # title | anchor | doc_title | doc_anchor
    grams: frozenset


@dataclass
class Hits:
    """One question's ranking. `clauses`: (score, doc_id, section, clause),
    best first. `docs`: (score, doc_id), best first."""
    clauses: list[tuple[float, str, str, str]] = field(default_factory=list)
    docs: list[tuple[float, str]] = field(default_factory=list)

    def clause_scores(self, doc_id: str) -> dict[tuple[str, str], float]:
        return {(s, c): sc for sc, d, s, c in self.clauses if d == doc_id}


class ClauseIndex:
    """`kinds` picks which evidence is indexed — the ablation in
    night/titleprobe.py measures each; production uses the titles only (see
    the module docstring for the numbers)."""

    ALL_KINDS = ("title", "anchor", "doc_title", "doc_anchor")
    DEFAULT_KINDS = ("title",)

    def __init__(self, docs: list[dict], anchors: dict | None = None,
                 kinds: tuple[str, ...] = DEFAULT_KINDS, text_words: int = 0):
        self.kinds = tuple(kinds)
        self.text_words = int(text_words)
        if anchors is None:
            anchors = load_anchor_clauses() if "anchor" in self.kinds else {}
        self.units: list[Unit] = []
        for d in docs:
            self._add_doc(d, anchors.get(d.get("document_id") or "", {}) or {})
        df: dict[str, int] = {}
        self.post: dict[str, list[int]] = {}
        for i, u in enumerate(self.units):
            for g in u.grams:
                df[g] = df.get(g, 0) + 1
                self.post.setdefault(g, []).append(i)
        self.n = float(len(self.units)) or 1.0
        self.idf = {g: max(MIN_IDF, math.log(self.n / (1 + v))) for g, v in df.items()}

    # -- building ----------------------------------------------------------
    def _unit(self, doc_id, section, clause, kind, text) -> None:
        if kind not in self.kinds:
            return
        g = grams(text)
        if len(g) >= MIN_GRAMS:
            self.units.append(Unit(doc_id, section, clause, kind, g))

    def _add_doc(self, doc: dict, attributed: dict) -> None:
        doc_id = doc.get("document_id") or ""
        if not doc_id:
            return
        title = doc.get("title") or ""
        known: dict[tuple[str, str], bool] = {}
        for s in doc.get("sections") or []:
            if not _is_curated(s):
                continue
            sec = str(s.get("id") or "")
            for cl in s.get("clauses") or []:
                num = str(cl.get("number") or "")
                text = (cl.get("text") or "").strip()
                if not text:
                    continue
                known[(sec, num)] = True
                if _is_titled(num):
                    unit_text = num
                    if self.text_words > 0:
                        unit_text = f"{num} {' '.join(text.split()[:self.text_words])}"
                    self._unit(doc_id, sec, num, "title", unit_text)
                elif self.text_words > 0:
                    self._unit(doc_id, sec, num, "title", " ".join(text.split()[:self.text_words]))
        if not known:
            return   # nothing to serve from an uncurated order — nothing to index
        self._unit(doc_id, None, None, "doc_title", title)
        # anchors attributed to a clause (night/anchor_attrib): keyed by the
        # normalised "title question"; the question is what the soldier typed
        norm_title = _attrib_norm(title)
        attributed_qs: set[str] = set()
        for key, target in attributed.items():
            if not (isinstance(target, (list, tuple)) and len(target) == 2):
                continue
            sec, num = str(target[0]), str(target[1])
            if (sec, num) not in known:
                continue
            q = key[len(norm_title) + 1:] if norm_title and key.startswith(norm_title + " ") else key
            attributed_qs.add(_attrib_norm(q))
            self._unit(doc_id, sec, num, "anchor", q)
        # the order's remaining question anchors: evidence about the order,
        # silent about the paragraph
        for q in _question_lists(doc):
            if _attrib_norm(q) not in attributed_qs:
                self._unit(doc_id, None, None, "doc_anchor", q)

    # -- querying ----------------------------------------------------------
    def rank(self, question: str, doc_ids=None) -> Hits:
        q = grams(question)
        if not q:
            return Hits()
        default = math.log(self.n)
        qw = {g: self.idf.get(g, default) for g in q}
        nq = math.sqrt(sum(v * v for v in qw.values())) or 1.0
        acc: dict[int, float] = {}
        for g in q:
            w = qw[g]
            for i in self.post.get(g, ()):
                acc[i] = acc.get(i, 0.0) + w
        allowed = None if doc_ids is None else set(doc_ids)
        best_clause: dict[tuple[str, str, str], float] = {}
        best_doc: dict[str, float] = {}
        for i, a in acc.items():
            u = self.units[i]
            if allowed is not None and u.doc_id not in allowed:
                continue
            s = a / (nq * math.sqrt(len(u.grams)))
            if u.section is not None:
                k = (u.doc_id, u.section, u.clause)
                if s > best_clause.get(k, 0.0):
                    best_clause[k] = s
            if s > best_doc.get(u.doc_id, 0.0):
                best_doc[u.doc_id] = s
        clauses = sorted(((s, d, sec, cl) for (d, sec, cl), s in best_clause.items()),
                         key=lambda x: -x[0])
        docs = sorted(((s, d) for d, s in best_doc.items()), key=lambda x: -x[0])
        return Hits(clauses=clauses, docs=docs)

    def stats(self) -> dict:
        out: dict[str, int] = {}
        for u in self.units:
            out[u.kind] = out.get(u.kind, 0) + 1
        out["units"] = len(self.units)
        out["docs"] = len({u.doc_id for u in self.units})
        return out
