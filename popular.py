# -*- coding: utf-8 -*-
"""Home-screen "🔥 הנשאלות השבוע" strip: surface the questions soldiers in a
given role actually asked, so a newcomer opening the app sees live, relevant
entry points and can tap one to ask it.

Popularity is derived purely from metrics' in-process question ring buffer
(no API, no disk, no network). That buffer resets whenever the server process
restarts — Streamlit Cloud reboots on every deploy — so until real traffic
accumulates this falls back to the curated per-role suggestions from
backend.get_suggested_questions, and the strip is never empty or embarrassing.

Everything here is fail-soft: a missing/empty buffer, an odd record shape, or
an unavailable backend all yield the curated fallback (or []), never an
exception, so the home screen can call top_questions() unconditionally.
"""
import re

# Letter-generator rows are logged with this prefix (see app.py's letters
# dialog); they are an internal tool action, not a question to resurface.
_LETTER_PREFIX = "[מכתב]"

# Privacy heuristic. Real regulation questions are short and rarely carry long
# digit runs; anything longer / more number-heavy smells like a soldier having
# pasted personal context (name, unit, ת"ז, phone), so we drop it rather than
# show it to strangers. Clause refs like "33.0110" (digit runs <= 4) and years
# like "2026" stay; ID / phone / personal numbers (7-10 digits) are cut.
_MAX_LEN_CHARS = 100
_ID_DIGIT_RUN = re.compile(r"\d{5,}")   # 5+ consecutive digits -> ID/phone-like
_SEPARATORS = re.compile(r"[\s\-]+")     # fold "050-123-4567" before the digit test


def _norm(q: str) -> str:
    """Whitespace-collapsed, trimmed question text — the counting/dedupe key."""
    return " ".join(q.split()).strip()


def _looks_personal(q: str) -> bool:
    """True when the question should be hidden for privacy: very long, or it
    contains a digit run that looks like an ID / phone / personal number."""
    if len(q) > _MAX_LEN_CHARS:
        return True
    return bool(_ID_DIGIT_RUN.search(_SEPARATORS.sub("", q)))


def _is_real_candidate(q: str) -> bool:
    """A logged question is eligible for the strip when it isn't a letter
    action and doesn't look like it carries a personal identifier."""
    if not q or q.startswith(_LETTER_PREFIX):
        return False
    return not _looks_personal(q)


def _curated(role: str) -> list[str]:
    """Trusted per-role fallback questions (authored at ingestion, no user
    data). Deferred import so a heavy/failed backend never breaks the strip;
    get_suggested_questions is an EXISTING backend name, so this is safe even
    against a backend module cached from a previous cloud build."""
    try:
        import backend
        return backend.get_suggested_questions(role) or []
    except Exception:
        return []


def top_questions(role: str, k: int = 4) -> list[str]:
    """Up to `k` most-asked real questions for `role`, newest-first on ties.

    Pure and fail-soft: reads only metrics' in-memory ring buffer, makes no
    API / network / disk call, and never raises. Real questions are filtered
    for privacy ("[מכתב]" actions, over-long text, ID/phone-like digit runs)
    and de-duplicated by normalized text; if fewer than `k` survive (cold
    start / fresh process / low traffic) the remainder is filled from the
    curated per-role suggestions so the strip is never empty.
    """
    result: list[str] = []
    seen: set[str] = set()

    # ── real, role-scoped questions from the in-memory metrics buffer ──
    try:
        import metrics
        from collections import Counter

        counts: "Counter[str]" = Counter()
        for rec in metrics.recent_questions():   # newest-first snapshot
            try:
                if rec.get("role") != role:
                    continue
                q = _norm(rec.get("question", ""))
            except Exception:
                continue
            if _is_real_candidate(q):
                counts[q] += 1
        # Counter.most_common sorts by count desc; sorted() is stable, so ties
        # keep first-seen order — i.e. the more-recent question of an equal
        # pair wins, because the scan above was newest-first.
        for q, _n in counts.most_common():
            if len(result) >= k:
                break
            if q not in seen:
                seen.add(q)
                result.append(q)
    except Exception:
        # never raise — fall through to the curated fill below
        pass

    # ── fill to k with curated suggestions (cold start / thin traffic) ──
    if len(result) < k:
        for q in _curated(role):
            if len(result) >= k:
                break
            nq = _norm(q)
            if nq and nq not in seen:
                seen.add(nq)
                result.append(nq)

    return result[:k]
