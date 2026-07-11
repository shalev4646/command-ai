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

# ── מכסות (נבחרו 2026-07-09: תקרת תקציב ~27$/חודש בניצול מלא) ──
USER_DAILY_LIMIT = 5     # שאלות ליום לכל session (טאב דפדפן; אין auth)
# שאלות ליום לכל האפליקציה — השומר האמיתי על התקציב. 50: פיילוט של ~10
# משתמשים × 5 שאלות, והמכסה משותפת גם למחולל המכתבים (reserve זהה);
# תרחיש קצה של יום מלא ≈ $2 — בתוך תקציב $20/חודש לשימוש אמיתי.
GLOBAL_DAILY_LIMIT = 50

# Opus 4.8 pricing, $/MTok — for the per-question cost estimate in the log
_PRICE_IN, _PRICE_OUT = 5.0, 25.0
_PRICE_CACHE_READ, _PRICE_CACHE_WRITE = 0.5, 6.25

_JSONL_PATH = Path(__file__).parent / "storage" / "metrics_log.jsonl"

_QUESTION_COLUMNS = [
    "ts", "session", "role", "question", "search_query", "doc_ids",
    "input_tokens", "cache_read", "cache_write", "output_tokens",
    "cost_usd", "latency_s", "answer_preview",
]
_FEEDBACK_COLUMNS = [
    "ts", "session", "role", "verdict", "question", "comment",
    "answer_preview", "doc_ids",
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
                 latency_s: float) -> None:
    usage = usage or {}
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "session": session_id,
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
    }
    _store()["questions"].appendleft(record)
    _persist("questions", _QUESTION_COLUMNS, record)


def log_feedback(session_id: str, role: str, verdict: str, question: str,
                 answer: str, sources: list[dict] | None, comment: str = "") -> None:
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "session": session_id,
        "role": role,
        "verdict": verdict,  # "up" | "down" | "comment"
        "question": question,
        "comment": comment,
        "answer_preview": answer[:1500],
        "doc_ids": ", ".join(s0["doc_id"] for s0 in (sources or [])),
    }
    _store()["feedback"].appendleft(record)
    _persist("feedback", _FEEDBACK_COLUMNS, record)


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
            "questions": list(s["questions"]),
            "feedback": list(s["feedback"]),
            "sheets_status": s["sheets_status"] if config else "not_configured",
            "sheets_error": s["sheets_error"],
            "sheet_url": config[1] if config else "",
        }


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]
