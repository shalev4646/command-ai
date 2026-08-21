# -*- coding: utf-8 -*-
"""תשתית מדידה: לוג שאלות, משוב 👍/👎 ומכסות שימוש יומיות.

Storage is layered because Streamlit Cloud has no persistent disk (every
deploy/reboot wipes local files):
1. In-process ring buffers — always on, feed the admin dashboard instantly.
2. Local JSONL (storage/metrics_log.jsonl) — survives reruns on a real disk;
   on the cloud it's best-effort until the next reboot.
3. Google Sheets — the durable store, active only when st.secrets carries a
   [gcp_service_account] table and [metrics] sheet_url. Rows are appended
   from a daemon thread so the soldier never waits on Google's API.

Every storage layer is fail-soft: logging must never break answering.
"""
import json
import threading
import uuid
from collections import deque
from datetime import datetime, date
from pathlib import Path

import streamlit as st

from common import is_refusal

# ── מכסות (נבחרו 2026-07-09: תקרת תקציב ~27$/חודש בניצול מלא) ──
USER_DAILY_LIMIT = 5     # שאלות ליום לכל session (טאב דפדפן; אין auth)
# שאלות ליום לכל האפליקציה — השומר האמיתי על התקציב. 80 (הועלה 2026-07-23
# לקראת הפיילוט מ-50): מרווח ל-12+ בודקים × 5 שאלות בלי לפגוש את התקרה
# ביום הראשון; המכסה משותפת גם למחולל המכתבים (reserve זהה). תרחיש קצה
# של יום מלא ≈ $8 (Opus 4.8) — עדיין רצפת-ביטחון, לא שימוש צפוי.
GLOBAL_DAILY_LIMIT = 80

# Feedback rides no quota — it costs no API call — but it does write a row to a
# Sheet, and nothing stopped a script (or a bored thumb) from writing forever.
# The cap is per session per day and sits far above real use: a pilot tester
# rating every one of their 5 answers, changing their mind, and filing a report
# lands around 12. Passing it is a flood, not a user.
FEEDBACK_DAILY_LIMIT = 20

# Wrong admin passwords, process-wide. Per-SESSION would reset with every new
# tab, which is one keystroke for an attacker; the dashboard holds every
# question and report the pilot ever wrote, and ?admin=1 is public.
_ADMIN_FAIL_CAP = 8

# Opus 4.8 pricing, $/MTok — for the per-question cost estimate in the log
_PRICE_IN, _PRICE_OUT = 5.0, 25.0
_PRICE_CACHE_READ, _PRICE_CACHE_WRITE = 0.5, 6.25

_JSONL_PATH = Path(__file__).parent / "storage" / "metrics_log.jsonl"

# "device" is LAST on purpose, in both lists. _append_to_sheet writes rows
# POSITIONALLY against a header that was created on the tab's first-ever use —
# inserting a column in the middle would shift every future row against the
# existing header and silently corrupt the sheet. Appending is safe, and the
# stale header gets one repair pass (see _append_to_sheet).
# "refused" is appended last on purpose: _append_to_sheet repairs a tab whose
# header predates a new column, but only by extending it rightwards.
_QUESTION_COLUMNS = [
    "ts", "session", "role", "question", "search_query", "doc_ids",
    "input_tokens", "cache_read", "cache_write", "output_tokens",
    "cost_usd", "latency_s", "answer_preview", "device", "refused",
]
_FEEDBACK_COLUMNS = [
    "ts", "session", "role", "verdict", "question", "comment",
    "answer_preview", "doc_ids", "device", "refused",
]


@st.cache_resource(show_spinner=False)
def _store() -> dict:
    """Process-wide mutable state: daily counters + dashboard ring buffers.

    cache_resource makes it shared across all sessions of this server
    process; it resets on reboot, which is acceptable for daily quotas.
    """
    return {
        "lock": threading.Lock(),
        "day": date.today().isoformat(),
        "global_count": 0,
        "session_counts": {},
        "feedback_counts": {},
        "admin_fails": 0,
        "questions": deque(maxlen=200),
        "feedback": deque(maxlen=200),
        "sheets_status": "not_configured",  # not_configured | ok | error
        "sheets_error": "",
    }


def _reset_if_new_day(s: dict) -> None:
    today = date.today().isoformat()
    if s["day"] != today:
        s["day"] = today
        s["global_count"] = 0
        s["session_counts"] = {}
        s["feedback_counts"] = {}


def reserve(session_id: str) -> str:
    """Claim one question against today's quotas.

    Returns "ok" (and counts the question), "user" (this session exhausted
    its daily allowance) or "global" (the whole app hit today's cap). On
    "ok" the caller must refund() if the API call ultimately fails, so
    errors don't burn quota.
    """
    s = _store()
    with s["lock"]:
        _reset_if_new_day(s)
        if s["global_count"] >= GLOBAL_DAILY_LIMIT:
            return "global"
        if s["session_counts"].get(session_id, 0) >= USER_DAILY_LIMIT:
            return "user"
        s["global_count"] += 1
        s["session_counts"][session_id] = s["session_counts"].get(session_id, 0) + 1
        return "ok"


def refund(session_id: str) -> None:
    s = _store()
    with s["lock"]:
        _reset_if_new_day(s)
        s["global_count"] = max(0, s["global_count"] - 1)
        if session_id in s["session_counts"]:
            s["session_counts"][session_id] = max(0, s["session_counts"][session_id] - 1)


def feedback_allowed(session_id: str) -> bool:
    """Claim one feedback row for this session today. False once past the cap.

    Deliberately NOT reserve(): feedback must never consume a question the
    soldier paid for, and a thumb must never be refused because the answers
    ran out.
    """
    s = _store()
    with s["lock"]:
        _reset_if_new_day(s)
        counts = s.setdefault("feedback_counts", {})
        n = counts.get(session_id, 0)
        if n >= FEEDBACK_DAILY_LIMIT:
            return False
        counts[session_id] = n + 1
        return True


def admin_backoff() -> float:
    """Seconds to stall the next admin attempt, doubling per wrong password."""
    s = _store()
    with s["lock"]:
        return float(min(2 ** s.get("admin_fails", 0), _ADMIN_FAIL_CAP))


def note_admin_attempt(ok: bool) -> None:
    s = _store()
    with s["lock"]:
        s["admin_fails"] = 0 if ok else s.get("admin_fails", 0) + 1


def estimate_cost(usage: dict | None) -> float:
    """Rough $ cost of one answer from its token usage (0.0 if unknown)."""
    if not usage:
        return 0.0
    return round(
        usage.get("input_tokens", 0) * _PRICE_IN / 1e6
        + usage.get("output_tokens", 0) * _PRICE_OUT / 1e6
        + usage.get("cache_read_input_tokens", 0) * _PRICE_CACHE_READ / 1e6
        + usage.get("cache_creation_input_tokens", 0) * _PRICE_CACHE_WRITE / 1e6,
        5,
    )


# ── Google Sheets (durable layer) ──

def _sheets_config() -> tuple[dict, str] | None:
    """(service-account info, sheet url) from st.secrets, or None."""
    try:
        info = dict(st.secrets["gcp_service_account"])
        url = st.secrets["metrics"]["sheet_url"]
        return (info, url) if info and url else None
    except Exception:
        return None


def _append_to_sheet(tab: str, columns: list[str], row: list, config: tuple) -> None:
    """Worker-thread body: append one row, creating the tab on first use."""
    s = _store()
    try:
        import gspread  # deferred: ~0.3s import, only needed here
        from google.oauth2.service_account import Credentials

        client = s.get("_gspread_client")
        if client is None:
            creds = Credentials.from_service_account_info(
                config[0], scopes=["https://www.googleapis.com/auth/spreadsheets"])
            client = gspread.authorize(creds)
            s["_gspread_client"] = client
        sheet = s.get("_spreadsheet")
        if sheet is None:
            sheet = client.open_by_url(config[1])
            s["_spreadsheet"] = sheet
        try:
            ws = sheet.worksheet(tab)
        except gspread.WorksheetNotFound:
            ws = sheet.add_worksheet(title=tab, rows=1000, cols=len(columns))
            ws.append_row(columns, value_input_option="RAW")
        else:
            # A tab created before a column was appended still carries the old
            # header, so the new values would land under a blank heading. Name
            # them once per tab per process. update_cell (not update) — its
            # signature is stable across gspread majors, and the loop runs at
            # most once since the header is only ever short before the repair.
            if tab not in s.setdefault("_hdr_checked", set()):
                s["_hdr_checked"].add(tab)
                head = ws.row_values(1)
                for i in range(len(head), len(columns)):
                    ws.update_cell(1, i + 1, columns[i])
        ws.append_row(row, value_input_option="RAW")
        s["sheets_status"] = "ok"
    except Exception as e:
        # keep serving; surface the problem on the admin dashboard only.
        # Don't disable permanently — Google hiccups are transient.
        s["sheets_status"] = "error"
        s["sheets_error"] = f"{type(e).__name__}: {e}"
        s["_gspread_client"] = None
        s["_spreadsheet"] = None


def _persist(tab: str, columns: list[str], record: dict) -> None:
    """Fan one record out to JSONL (inline) + Sheets (background thread)."""
    try:
        _JSONL_PATH.parent.mkdir(exist_ok=True)
        with _JSONL_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"tab": tab, **record}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    config = _sheets_config()
    if config:
        row = [record.get(c, "") for c in columns]
        threading.Thread(
            target=_append_to_sheet, args=(tab, columns, row, config), daemon=True,
        ).start()


def log_question(session_id: str, role: str, question: str, answer: str,
                 sources: list[dict] | None, usage: dict | None,
                 latency_s: float, device_id: str = "") -> None:
    """Log one answered question.

    device_id is LOG-ONLY — reserve()/refund() still key on session_id, so
    passing it changes no quota behaviour. It exists because session_id is
    per-TAB: without it the log cannot tell one person's two tabs from two
    people, which hides return-visits and per-person question counts. Optional
    so a caller that has no device (or a test) simply logs an empty cell.
    """
    usage = usage or {}
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "session": session_id,
        # sits next to "session" here for a readable JSONL/dashboard row; the
        # Sheets column order comes from _QUESTION_COLUMNS, where it is last
        "device": device_id,
        "role": role,
        "question": question,
        "search_query": usage.get("search_query", ""),
        "doc_ids": ", ".join(s0["doc_id"] for s0 in (sources or [])),
        "input_tokens": usage.get("input_tokens", 0),
        "cache_read": usage.get("cache_read_input_tokens", 0),
        "cache_write": usage.get("cache_creation_input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cost_usd": estimate_cost(usage),
        "latency_s": round(latency_s, 1),
        "answer_preview": answer[:1500],
        # Classified at write time, not by grepping answer_preview offline: the
        # preview truncates at 1500 chars and the offline tools had to re-derive
        # the rule. One column turns "read every answer" into "filter a column",
        # and it is what splits a pilot's failures into content gap / retrieval
        # miss / over-strict answer — each of which has a different fix.
        "refused": is_refusal(answer),
    }
    _store()["questions"].appendleft(record)
    _persist("questions", _QUESTION_COLUMNS, record)


def log_feedback(session_id: str, role: str, verdict: str, question: str,
                 answer: str, sources: list[dict] | None, comment: str = "",
                 device_id: str = "") -> bool:
    """Record one thumb / comment / report. False if the session hit the cap.

    The gate lives HERE rather than at the four call sites: a fifth one added
    later would silently reopen the flood.
    """
    if not feedback_allowed(session_id):
        return False
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "session": session_id,
        "device": device_id,  # log-only, same contract as log_question
        "role": role,
        "verdict": verdict,  # "up" | "down" | "comment"
        "question": question,
        "comment": comment,
        "answer_preview": answer[:1500],
        "doc_ids": ", ".join(s0["doc_id"] for s0 in (sources or [])),
        # a thumbs-down on a refusal means something different from a
        # thumbs-down on a wrong answer — keep the two separable
        "refused": is_refusal(answer),
    }
    _store()["feedback"].appendleft(record)
    _persist("feedback", _FEEDBACK_COLUMNS, record)
    return True


def dashboard_data() -> dict:
    """Snapshot for the admin page: today's usage + recent activity."""
    s = _store()
    with s["lock"]:
        _reset_if_new_day(s)
        config = _sheets_config()
        return {
            "day": s["day"],
            "global_count": s["global_count"],
            "global_limit": GLOBAL_DAILY_LIMIT,
            "user_limit": USER_DAILY_LIMIT,
            "sessions_today": len(s["session_counts"]),
            # distinct devices, derived from the ring buffer rather than a
            # counter: session_counts is keyed per tab, so it over-counts a
            # person who reopened the app. Filtered to today because the ring
            # buffer spans server uptime, which _reset_if_new_day never clears.
            "devices_today": len({q["device"] for q in s["questions"]
                                  if q.get("device") and q["ts"][:10] == s["day"]}),
            "questions": list(s["questions"]),
            "feedback": list(s["feedback"]),
            # Four states, not three. "configured" means the secrets are there
            # but nothing has been written since this process booted, so the
            # chain is untested — which is the NORMAL state right after a
            # deploy, and used to render as "not configured". Those two look
            # identical to the reader and mean opposite things: one sends you
            # hunting a config bug that isn't there, the other lets you trust a
            # sheet that was never reachable. check_sheets() settles it.
            "sheets_status": (
                "not_configured" if not config
                else s["sheets_status"] if s["sheets_status"] in ("ok", "error")
                else "configured"
            ),
            "sheets_error": s["sheets_error"],
            "sheet_url": config[1] if config else "",
        }


def recent_questions() -> list[dict]:
    """Newest-first snapshot of the in-process question log (the dashboard ring
    buffer, maxlen 200). Read-only, no API/network — feeds the home-screen
    "popular questions" strip. Fail-soft: returns [] if state isn't ready."""
    try:
        s = _store()
        with s["lock"]:
            return list(s["questions"])
    except Exception:
        return []


def check_sheets() -> tuple[bool, str]:
    """Probe the whole Sheets chain on demand and say plainly what happened.

    Exists because the passive status only turns "ok" after a real append, so
    a freshly booted machine that is configured correctly is indistinguishable
    from one that is not — and the only other way to tell them apart is to
    spend a paid question and see whether a row lands.

    It performs a real WRITE, not just a read: append permission is what the
    logger actually needs, and a sheet shared read-only would pass any
    read-only probe and then silently drop every row. The write goes to its
    own _healthcheck tab so it never touches the metrics data.

    Synchronous, unlike _append_to_sheet — this runs from the admin page,
    where waiting a second for a true answer beats a thread nobody watches.
    """
    config = _sheets_config()
    if not config:
        return False, ("אין הגדרות — gcp_service_account או metrics.sheet_url "
                       "חסרים ב-secrets. בפרודקשן זה בדרך כלל אומר שהסוד "
                       "STREAMLIT_SECRETS_TOML_B64 לא מוגדר או לא הגיע לקונטיינר.")
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_info(
            config[0], scopes=["https://www.googleapis.com/auth/spreadsheets"])
        sheet = gspread.authorize(creds).open_by_url(config[1])
        try:
            ws = sheet.worksheet("_healthcheck")
        except gspread.WorksheetNotFound:
            ws = sheet.add_worksheet(title="_healthcheck", rows=10, cols=2)
        stamp = datetime.now().isoformat(timespec="seconds")
        ws.update_cell(1, 1, "last ok: " + stamp)
        # a successful write is the same proof an append gives, so promote the
        # passive indicator too — the admin should not have to remember that
        # the badge above the button is now stale
        _store()["sheets_status"] = "ok"
        return True, "כתיבה הצליחה לגיליון '%s' בשעה %s. שורות המדדים יישמרו." % (
            sheet.title, stamp)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]
