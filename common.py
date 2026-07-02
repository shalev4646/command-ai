"""Shared constants and helpers used across app, backend, and ingestion."""
import sys

# Canonical role set — the single source of truth. UI labels/prompts live in
# app.py/backend.py; anything validating or enumerating roles imports this.
ROLES = ("soldier", "commander", "reserve")


def safe_print(msg: str) -> None:
    """print() that survives non-UTF-8 stdout (Windows pipes default to the
    ANSI codepage, where Hebrew text raises UnicodeEncodeError)."""
    try:
        print(msg)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((msg + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
