"""Manual metadata overrides.

`metadata_override.json` (project root) maps a source PDF filename to fields
that take precedence over the LLM's automatic classification. Applied at
READ time only (backend.load_documents), never baked into the stored JSONs —
so adding, editing, or REMOVING an entry takes effect on the next load, and
the LLM's original classification is always preserved on disk.
"""
import json
from pathlib import Path

from common import ROLES, safe_print

OVERRIDE_FILE = Path(__file__).parent / "metadata_override.json"
_VALID_ROLES = set(ROLES)

# (mtime, parsed overrides) — the file is consulted once per loaded document,
# so re-read/re-warn only when it actually changes
_cache: tuple[float, dict] | None = None


def load_overrides() -> dict[str, dict]:
    """Read metadata_override.json → {source_file: {"roles": [...]}}.

    Keys starting with "_" are documentation/comments. Invalid role values
    are dropped; entries left with no valid roles are ignored entirely (so a
    typo can't accidentally hide a document from everyone).
    """
    global _cache
    if not OVERRIDE_FILE.exists():
        _cache = None
        return {}
    mtime = OVERRIDE_FILE.stat().st_mtime
    if _cache is not None and _cache[0] == mtime:
        return _cache[1]
    try:
        data = json.loads(OVERRIDE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("top-level JSON must be an object")
    except Exception as e:
        safe_print(f"[CommandAI] metadata_override.json invalid, ignoring: {e}")
        _cache = (mtime, {})
        return {}

    overrides: dict[str, dict] = {}
    for fname, cfg in data.items():
        if fname.startswith("_") or not isinstance(cfg, dict):
            continue
        roles = [r for r in (cfg.get("roles") or []) if r in _VALID_ROLES]
        if roles:
            overrides[fname] = {"roles": roles}
        else:
            safe_print(f"[CommandAI] override for {fname!r} has no valid roles, ignoring")
    _cache = (mtime, overrides)
    return overrides


def apply_overrides(doc: dict) -> dict:
    """Force manually-configured roles onto a document, matched by source_file."""
    override = load_overrides().get(doc.get("source_file") or "")
    if override:
        doc["roles"] = override["roles"]
    return doc
