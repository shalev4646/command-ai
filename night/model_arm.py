# -*- coding: utf-8 -*-
"""A model arm on the exact deliveries of a finished final test —
night/MODEL_ARM_CRITERION.md.

The first pass re-sends the user turns recorded in final161v2 VERBATIM (the
window each question got, byte for byte, with the same system prompt and
request parameters), so the only thing that changes is the answering model.
The second pass follows production (app.py, "The second search"): only where
the first answer declared a gap, composed through the production path, and the
kept answer is chosen the way the app chooses it — including
RETRIEVE_SECOND_PASS_KEEP_RULING, which night/final_arm did not apply before
27.09 (final_arm.production_choice, shared by both).

    venv\\Scripts\\python.exe -m night.model_arm dry     opus5v2   # FREE: count_tokens + base usage -> price
    venv\\Scripts\\python.exe -m night.model_arm p1      opus5v2   # PAID (batch): the 72 recorded user turns
    venv\\Scripts\\python.exe -m night.model_arm p2      opus5v2   # PAID (batch + Haiku compose): production second pass
    venv\\Scripts\\python.exe -m night.model_arm grade   opus5v2   # PAID (cents): production choice -> night.grade
    venv\\Scripts\\python.exe -m night.model_arm sheet   opus5v2   # FREE: stage-6 review sheet, paired with the base
    venv\\Scripts\\python.exe -m night.model_arm report  opus5v2   # FREE: the criterion, from the stage-6 review file
    venv\\Scripts\\python.exe -m night.model_arm collect opus5v2 p1  # recovery: a batch that outlived its process

`--model M` and `--base TAG` (anywhere on the line) name another arm on recorded deliveries: the
answering model, and the finished run whose user turns are re-sent. The defaults — claude-opus-5 on
final161v2 — are the opus5v2 arm. Every step of one arm must be given the same pair.

A refusal of the arm's model (stop_reason "refusal": the classifier declined,
content empty or partial) is recorded on the row and counted as a FAILURE of
that row — never dropped from the denominator. night.grade leaves rows without
an answer out of its file; `grade` writes them back as refused with 0 parts.
No `fallbacks`: the Batches API rejects the parameter, and a fallback to the
base model would contaminate the arm. Each paid step refuses to run twice.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from night import final_arm as FA  # noqa: E402  loads fly.toml [env] before backend is imported
from common import safe_print  # noqa: E402
from night import config as C  # noqa: E402

# The arm's model and the finished run whose deliveries it re-sends. The defaults are the opus5v2
# arm (night/MODEL_ARM_CRITERION.md) and reproduce its records exactly; `configure` (--model /
# --base) sets another pair — Opus 4.8 on final161v2, or Opus 5 on opus5v2 — for an arm that changes
# something else (a system-prompt flag) on the same recorded deliveries (the manager, 30.09).
DEFAULT_ARM_MODEL, DEFAULT_BASE_TAG = "claude-opus-5", "final161v2"
ARM_MODEL = DEFAULT_ARM_MODEL
BASE_TAG = DEFAULT_BASE_TAG
PARTS = C.OUT / "question_parts.json"
BASE_USAGE = C.OUT / f"usage_{BASE_TAG}.json"


def configure(model: str | None = None, base: str | None = None) -> None:
    """Set the arm's model and/or its base run; None leaves a value as it is."""
    global ARM_MODEL, BASE_TAG, BASE_USAGE
    if model:
        ARM_MODEL = model
    if base:
        BASE_TAG = base
        BASE_USAGE = C.OUT / f"usage_{BASE_TAG}.json"


# The criterion's row sets — fixed in night/MODEL_ARM_CRITERION.md before the run.
# (ב): the 34 rows the stage-6 review of final161v2 called full, plus rs055,
# whose production answer (first pass, kept by KEEP_RULING) was full.
PROTECTED = (
    "rs000", "rs002", "rs005", "rs006", "rs009", "rs010", "rs012", "rs015", "rs017", "rs019",
    "rs020", "rs021", "rs023", "rs025", "rs030", "rs033", "rs034", "rs035", "rs039", "rs040",
    "rs042", "rs045", "rs046", "rs048", "rs050", "rs051", "rs052", "rs057", "rs060", "rs062",
    "rs063", "rs064", "rs066", "rs069", "rs055",
)
ANSWER_SIDE = ("rs054", "rs055", "rs037", "rs026", "rs013")     # (ג), RUN_LOG 14 type ג
BASE_FULL_48 = {"official": 34, "production": 35}                # (ד)
PASS_48 = 41                                                     # FINAL_RULER_V2 (א): 85% of 48
FIELDS_OF_AN_ANSWER = ("answer", "sources", "context_words", "sent_user_content", "route", "stop_reason",
                       "stop_details", "refusal_stop", "truncated", "refused_flag", "usage", "model")
QUESTION_FIELDS = ("id", "q", "clean_q", "role", "band", "persona", "situation", "source", "target_doc", "ugly")
# What a row says about the ANSWER it got — never part of a delivery. A base that is itself an arm
# (opus5v2) carries all of them on its rows; final161v2's rows carry only the first three.
ANSWER_SIDE_FIELDS = ("answer", "truncated", "refused_flag", "model", "stop_reason", "stop_details",
                      "refusal_stop", "usage", "error")


def _path(tag: str, suffix: str = "") -> Path:
    return C.OUT / f"probe_{tag}{suffix}.jsonl"


def _backend():
    """backend, answering with the arm's model — or a refusal to go on."""
    import backend
    if backend.MODEL != ARM_MODEL:
        raise SystemExit(f"[model_arm] backend.MODEL is {backend.MODEL!r}, not {ARM_MODEL!r} — ANSWER_MODEL must be "
                         f"set before backend is imported (run this module as __main__)")
    return backend


# ── the requests ──────────────────────────────────────────────────────────────

def request_params(role: str, user_content: str, model: str | None = None) -> dict:
    """The answering request exactly as night.probe composes it (production's
    request shape) — only the model is the arm's. tests/test_model_arm.py pins
    the equality against probe.build_requests."""
    import backend
    system_prompt = backend.SYSTEM_PROMPTS.get(role, backend.SYSTEM_PROMPT_SOLDIER)
    return dict(
        model=model or ARM_MODEL,
        max_tokens=backend.MAX_OUTPUT_TOKENS,
        thinking={"type": "adaptive"},
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
    )


def _base_rows(suffix: str) -> dict[str, dict]:
    return {r["id"]: r for r in C.read_jsonl(_path(BASE_TAG, suffix))}


def _order() -> list[str]:
    """The ruler's id order — the only thing read from the ruler file. Wording
    and role come from the base's recorded rows, so a later edit of the ruler
    (rs007's role, 29.09) cannot change what the arm delivers or composes."""
    return [q["id"] for q in FA._questions(BASE_TAG)]


def p1_requests() -> tuple[list, list[dict]]:
    """The 72 first-pass requests: the base's recorded user turns, in ruler order."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    base = _base_rows("_p1")
    order = _order()
    if set(base) != set(order):
        raise SystemExit(f"[model_arm] {BASE_TAG} p1 holds {len(base)} rows that are not the ruler's {len(order)}")
    reqs, meta = [], []
    for i, qid in enumerate(order):
        b = base[qid]
        reqs.append(Request(custom_id=f"p{i}", params=MessageCreateParamsNonStreaming(
            **request_params(b["role"], b["sent_user_content"]))))
        meta.append({k: v for k, v in b.items() if k not in ANSWER_SIDE_FIELDS})
    return reqs, meta


# ── the answers ───────────────────────────────────────────────────────────────

def answer_row(meta_row: dict, m) -> tuple[dict, float]:
    """One batch result -> the row and its dollars. The stop reason is kept:
    a refusal has empty (declined before output) or partial (declined mid-
    stream) content and must count as a failure, not read as an answer."""
    from night.ledger import cost_usd
    from night.probe import _is_refusal
    text = "".join(bl.text for bl in (m.content or []) if getattr(bl, "type", None) == "text")
    u = m.usage
    usage = {"input_tokens": getattr(u, "input_tokens", 0) or 0,
             "output_tokens": getattr(u, "output_tokens", 0) or 0,
             "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
             "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}
    usd = cost_usd(m.model or ARM_MODEL, input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                   cache_write_tokens=usage["cache_creation_input_tokens"],
                   cache_read_tokens=usage["cache_read_input_tokens"], batch=True)
    sd = getattr(m, "stop_details", None)
    row = {**meta_row, "answer": text, "model": m.model, "stop_reason": m.stop_reason,
           "stop_details": sd.model_dump() if hasattr(sd, "model_dump") else sd,
           "refusal_stop": m.stop_reason == "refusal", "truncated": m.stop_reason == "max_tokens",
           "refused_flag": _is_refusal(text), "usage": usage}
    return row, usd


def _collect(batch_id: str, meta: list[dict], rid: str | None, out_path: Path, label: str, ledger) -> None:
    import backend
    rows: list[dict | None] = [None] * len(meta)
    actual, errors = 0.0, 0
    for res in backend.client.messages.batches.results(batch_id):
        i = int(res.custom_id[1:])
        if res.result.type != "succeeded":
            rows[i] = {**meta[i], "answer": None, "error": res.result.type}
            errors += 1
            continue
        rows[i], usd = answer_row(meta[i], res.result.message)
        actual += usd
    for i, r in enumerate(rows):
        if r is None:
            rows[i] = {**meta[i], "answer": None, "error": "missing"}
            errors += 1
    if rid:
        ledger.settle(rid, actual)
    C.write_jsonl(out_path, rows)
    refusals = [r["id"] for r in rows if r.get("refusal_stop")]
    C.log(f"[model_arm] {label}: {sum(1 for r in rows if (r.get('answer') or '').strip())}/{len(rows)} non-empty "
          f"answers, ${actual:.2f} (ledger ${ledger.spent:.2f}); refusal stops {len(refusals)} {refusals}, "
          f"truncated {sum(1 for r in rows if r.get('truncated'))}, errors {errors}")


def _run_batch(reqs: list, meta: list[dict], label: str, out_path: Path, estimate: float) -> None:
    import backend
    from night.ledger import Ledger
    ledger = Ledger(C.LEDGER)
    rid = ledger.reserve(label, estimate)
    try:
        batch = backend.client.messages.batches.create(requests=reqs)
    except Exception:
        ledger.settle(rid, 0.0)          # nothing was submitted, nothing was bought
        raise
    ticket = C.OUT / f"batch_{label}.json"
    ticket.write_text(json.dumps({"batch_id": batch.id, "label": label, "rid": rid, "out_path": str(out_path),
                                  "meta": meta}, ensure_ascii=False), encoding="utf-8")
    C.log(f"[model_arm] {label}: batch {batch.id}, {len(reqs)} requests (reserved ${estimate:.2f}; "
          f"claim ticket {ticket.name}); polling every 60s")
    failures = 0
    while True:
        try:
            b = backend.client.messages.batches.retrieve(batch.id)
        except Exception as e:          # a poll is a safe GET; a blip must not kill the watcher
            failures += 1
            C.log(f"[model_arm]   poll error {failures}/10: {type(e).__name__}")
            if failures >= 10:
                raise
            time.sleep(60)
            continue
        failures = 0
        if b.processing_status == "ended":
            break
        C.log(f"[model_arm]   {b.processing_status} succeeded={b.request_counts.succeeded} "
              f"processing={b.request_counts.processing}")
        time.sleep(60)
    _collect(batch.id, meta, rid, out_path, label, ledger)


# ── the production choice ────────────────────────────────────────────────────

production_choice = FA.production_choice       # one definition for every final test


def final_rows(first: dict[str, dict], second: dict[str, dict], order: list[str]) -> list[dict]:
    out = []
    for i in order:
        f, s = first[i], second.get(i)
        kept = production_choice(f.get("answer") or "", s)
        row = {**f, "first_answer": f.get("answer"), "second_answer": (s or {}).get("answer"),
               "kept": kept, "second_pass": kept == "second", "p1_stop_reason": f.get("stop_reason")}
        if kept == "second":
            row.update({k: s.get(k) for k in FIELDS_OF_AN_ANSWER})
        out.append(row)
    return out


def complete_grades(final: list[dict], graded: list[dict], parts: dict[str, list[str]]) -> list[dict]:
    """night.grade drops rows without an answer and grades a partial refusal as
    text. Here every row of `final` comes back: an empty answer or a refusal
    stop is a failure — level `refused`, 0 parts — with whatever the grader
    said kept beside it."""
    by_id = {r["id"]: r for r in graded}
    out = []
    for r in final:
        g = by_id.get(r["id"])
        if r.get("refusal_stop") or not (r.get("answer") or "").strip():
            ps = parts.get(str(r["id"]), [])
            why = f"no usable answer (stop_reason={r.get('stop_reason')}, empty={not (r.get('answer') or '').strip()})"
            out.append({**(g or r), "grade": {"level": "refused", "reason": why, "answered_parts": 0,
                                              "unanswered_parts": len(ps), "answered": [False] * len(ps),
                                              "parts": ps},
                        "grade_haiku": (g or {}).get("grade")})
        else:
            out.append(g if g is not None else {**r, "grade": None})
    return out


# ── the criterion ────────────────────────────────────────────────────────────

def row_sets() -> tuple[list[str], list[str]]:
    """(the 48 of FINAL_RULER_V2 (א), the 24 NO_SUCH_RULE / NOT_IN_CORPUS) — from the base review sheet.
    The adjudication verdicts are the ruler's, the same on every run of it: a base whose own sheet is
    not built yet (an arm before its `sheet`) is read through the default base's."""
    path = C.OUT / f"review_sheet_{BASE_TAG}.json"
    if not path.exists():
        path = C.OUT / f"review_sheet_{DEFAULT_BASE_TAG}.json"
    sheet = json.loads(path.read_text(encoding="utf-8"))
    a48 = [x["id"] for x in sheet if x["verdict"].startswith("ANSWERED_IN_CORPUS") or x["verdict"] == "UNADJUDICATED"]
    n24 = [x["id"] for x in sheet if x["verdict"] in ("NO_SUCH_RULE", "NOT_IN_CORPUS")]
    return a48, n24


def judge(review: dict, a48: list[str], n24: list[str]) -> dict:
    """The pass conditions of night/MODEL_ARM_CRITERION.md from the stage-6 review
    ({id: {verdict, answered_parts, note, confident_wrong?, invented?}})."""
    ids = a48 + n24
    missing = [i for i in ids if i not in review]
    cw = [i for i in ids if (review.get(i) or {}).get("confident_wrong")]
    drops = [i for i in PROTECTED if (review.get(i) or {}).get("verdict") != "full"]
    invented = [i for i in n24 if (review.get(i) or {}).get("invented")]
    full48 = [i for i in a48 if (review.get(i) or {}).get("verdict") == "full"]
    border48 = [i for i in a48 if (review.get(i) or {}).get("verdict") == "borderline"]
    return {"missing": missing, "confident_wrong": cw, "drops": drops, "invented": invented,
            "full48": full48, "border48": border48,
            "passed": not missing and not cw and not drops and not invented}


# ── commands ─────────────────────────────────────────────────────────────────

def _base_usage() -> dict:
    """Per-row token usage of the base's two batches (a free GET, cached to disk)."""
    if BASE_USAGE.exists():
        return json.loads(BASE_USAGE.read_text(encoding="utf-8"))
    import backend
    out = {}
    for which in ("p1", "p2"):
        t = json.loads((C.OUT / f"batch_probe-{BASE_TAG}_{which}.json").read_text(encoding="utf-8"))
        rows = {}
        for res in backend.client.messages.batches.results(t["batch_id"]):
            if res.result.type != "succeeded":
                continue
            m, u = res.result.message, res.result.message.usage
            rows[t["meta"][int(res.custom_id[1:])]["id"]] = {
                "model": m.model, "stop": m.stop_reason, "in": u.input_tokens, "out": u.output_tokens,
                "cw": getattr(u, "cache_creation_input_tokens", 0) or 0,
                "cr": getattr(u, "cache_read_input_tokens", 0) or 0}
        out[which] = rows
    BASE_USAGE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _usd(u: dict, part: str) -> float:
    """Batch dollars of one base row at Opus prices: input side or output side."""
    if part == "out":
        return u["out"] * 25 / 1e6 * 0.5
    return (u["in"] * 5 + u["cw"] * 5 * 1.25 + u["cr"] * 5 * 0.1) / 1e6 * 0.5


def estimate() -> dict:
    usage = _base_usage()
    p1, p2 = usage["p1"].values(), usage["p2"].values()
    e = {"p1_in": sum(_usd(u, "in") for u in p1), "p1_out": sum(_usd(u, "out") for u in p1),
         "p2_in_each": sum(_usd(u, "in") for u in p2) / len(p2), "p2_out_each": sum(_usd(u, "out") for u in p2) / len(p2),
         "n2_base": len(p2), "out_mean_p1": sum(u["out"] for u in p1) / len(p1)}
    e["total"] = lambda f, n2: (e["p1_in"] + f * e["p1_out"] + n2 * (e["p2_in_each"] + f * e["p2_out_each"])
                                + n2 * FA.COMPOSE_USD + FA.GRADE_USD)
    return e


def cmd_dry(tag: str) -> int:
    """Free: the 72 requests counted (count_tokens) against what the base was billed, then the price."""
    FA._need_key()
    import backend
    from night.ledger import Ledger
    usage = _base_usage()
    reqs, meta = p1_requests()
    same, differ = 0, []
    for req, m in zip(reqs, meta):
        p = req["params"]
        system = [{"type": "text", "text": b["text"]} for b in p["system"]]
        for attempt in range(5):
            try:
                n = backend.client.messages.count_tokens(model=p["model"], system=system, messages=p["messages"],
                                                         thinking=p["thinking"]).input_tokens
                break
            except Exception as e:  # count_tokens is rate-limited per minute; it is free
                if attempt == 4:
                    raise
                time.sleep(5 * (attempt + 1))
        u = usage["p1"][m["id"]]
        if n == u["in"] + u["cw"] + u["cr"]:
            same += 1
        else:
            differ.append((m["id"], n, u["in"] + u["cw"] + u["cr"]))
    safe_print(f"[model_arm] {tag} dry — model {ARM_MODEL} — flags: {FA._flag_line()}")
    safe_print(f"[model_arm] first pass: {same}/{len(reqs)} requests count exactly the input tokens {BASE_TAG} was "
               f"billed for" + (f"; DIFFER: {differ}" if differ else ""))
    e = estimate()
    safe_print(f"[model_arm] base {BASE_TAG}: p1 input ${e['p1_in']:.2f} + output ${e['p1_out']:.2f} "
               f"(mean {e['out_mean_p1']:.0f} output tokens); p2 ${e['p2_in_each'] + e['p2_out_each']:.4f} each "
               f"x {e['n2_base']}; compose ${FA.COMPOSE_USD:.3f} per second pass; grading ${FA.GRADE_USD:.2f}")
    for f in (1.0, 1.5, 2.0, 3.0):
        cells = "  ".join(f"{n2} second passes ${e['total'](f, n2):.2f}" for n2 in (e["n2_base"], 72))
        safe_print(f"[model_arm]   output x{f:.1f}: {cells}")
    ledger = Ledger(C.LEDGER)
    safe_print(f"[model_arm] ledger ${ledger.spent:.2f} spent, ${ledger.remaining():.2f} under the ceiling")
    return 1 if differ else 0


def cmd_p1(tag: str) -> int:
    FA._need_key()
    _backend()
    out = _path(tag, "_p1")
    if out.exists():
        safe_print(f"[model_arm] {out.name} already on disk — refusing to pay twice."); return 1
    reqs, meta = p1_requests()
    e = estimate()
    safe_print(f"[model_arm] {tag} p1: {len(reqs)} recorded user turns -> {ARM_MODEL} — flags: {FA._flag_line()}")
    _run_batch(reqs, meta, f"probe-{tag}_p1", out, e["p1_in"] + 3 * e["p1_out"])
    return 0


def cmd_p2(tag: str) -> int:
    FA._need_key()
    backend = _backend()
    from night.ledger import Ledger
    from night.probe import build_requests
    out = _path(tag, "_p2")
    if out.exists():
        safe_print(f"[model_arm] {out.name} already on disk — refusing to pay twice."); return 1
    order = _order()
    first = {r["id"]: r for r in C.read_jsonl(_path(tag, "_p1"))}
    if set(first) != set(order):
        safe_print(f"[model_arm] first pass has {len(first)} rows, expected {len(order)} — collect it first."); return 1
    failed = sorted(i for i, r in first.items() if r.get("error"))
    if failed:
        safe_print(f"[model_arm] first pass has failed requests {failed} — resend them before the second pass."); return 1
    # production: a second search only where the answer declared a gap (app.py);
    # the question as the base delivered it (wording and role recorded in p1)
    rows = [{**{k: first[i][k] for k in QUESTION_FIELDS if k in first[i]}, "first_answer": first[i]["answer"]}
            for i in order if backend.lacked_from(first[i].get("answer") or "")]
    safe_print(f"[model_arm] {tag} p2: {len(rows)} of {len(order)} first answers declared a gap — flags: {FA._flag_line()}")
    if not rows:
        C.write_jsonl(out, []); return 0
    ledger = Ledger(C.LEDGER)
    # HyDE, the router and the rewrite are Haiku calls inside backend that bypass
    # the ledger (its KNOWN GAP): book them up front at the project's rate
    rid = ledger.reserve(f"compose-{tag}_p2", FA.COMPOSE_USD * len(rows))
    try:
        reqs, meta = build_requests(rows)          # the production path, strict: aborts on a degraded pipeline
    finally:
        ledger.settle(rid, FA.COMPOSE_USD * len(rows))
    models = {r["params"]["model"] for r in reqs}
    if models != {ARM_MODEL}:
        raise SystemExit(f"[model_arm] composed requests name {models}, not {ARM_MODEL}")
    e = estimate()
    _run_batch(reqs, meta, f"probe-{tag}_p2", out, len(reqs) * (e["p2_in_each"] + 3 * e["p2_out_each"]))
    return 0


def cmd_grade(tag: str) -> int:
    FA._need_key()
    from night.grade import grade_file
    from night.ledger import Ledger
    final = _path(tag)
    graded = C.OUT / f"grades_grade-{tag}.jsonl"
    if graded.exists():
        safe_print(f"[model_arm] {graded.name} already on disk — refusing to pay twice."); return 1
    if not final.exists():
        p1 = {r["id"]: r for r in C.read_jsonl(_path(tag, "_p1"))}
        p2 = {r["id"]: r for r in C.read_jsonl(_path(tag, "_p2"))}
        rows = final_rows(p1, p2, _order())
        C.write_jsonl(final, rows)
        from collections import Counter
        safe_print(f"[model_arm] {final.name}: {len(rows)} rows — kept: {dict(Counter(r['kept'] for r in rows))}")
    grade_file(final, Ledger(C.LEDGER), f"grade-{tag}")
    parts = json.loads(PARTS.read_text(encoding="utf-8"))
    rows = complete_grades(C.read_jsonl(final), C.read_jsonl(graded) if graded.exists() else [], parts)
    C.write_jsonl(graded, rows)
    safe_print(f"[model_arm] {graded.name}: {len(rows)} rows, "
               f"{sum(1 for r in rows if (r.get('grade') or {}).get('reason', '').startswith('no usable'))} "
               f"written back as failures (refusal stop / empty)")
    return 0


def cmd_sheet(tag: str) -> int:
    """Free: every row with the base's answer and verdict beside the arm's — for stage 6."""
    base_sheet = {x["id"]: x for x in json.loads((C.OUT / f"review_sheet_{BASE_TAG}.json").read_text(encoding="utf-8"))}
    base_final, base_p1, base_p2 = _base_rows(""), _base_rows("_p1"), _base_rows("_p2")
    base_review = json.loads((C.OUT / f"review_grade-{BASE_TAG}.json").read_text(encoding="utf-8"))
    base_grades = {r["id"]: r for r in C.read_jsonl(C.OUT / f"grades_grade-{BASE_TAG}.jsonl")}
    arm = {r["id"]: r for r in C.read_jsonl(_path(tag))}
    arm_grades = {r["id"]: r for r in C.read_jsonl(C.OUT / f"grades_grade-{tag}.jsonl")}
    level = lambda g: ((g or {}).get("grade") or {})
    sheet = []
    for i in _order():
        bs, a, q = base_sheet[i], arm[i], base_p1[i]
        base_choice = production_choice(base_p1[i]["answer"], base_p2.get(i))
        base_prod = base_p2[i]["answer"] if base_choice == "second" else base_p1[i]["answer"]
        sheet.append({
            "id": i, "role": q["role"], "q": q["q"], "verdict": bs["verdict"], "doc": bs.get("doc"),
            "parts": bs.get("parts"), "quotes": bs.get("quotes"),
            "base": {"answer": base_final[i]["answer"], "second_pass": bool(base_final[i].get("second_pass")),
                     "production_answer": base_prod if base_prod != base_final[i]["answer"] else None,
                     "review": base_review.get(i), "grade": level(base_grades.get(i)).get("level"),
                     "sources": base_final[i].get("sources")},
            "arm": {"answer": a.get("answer"), "kept": a.get("kept"),
                    "other_answer": a.get("first_answer") if a.get("kept") == "second" else a.get("second_answer"),
                    "stop_reason": a.get("stop_reason"), "p1_stop_reason": a.get("p1_stop_reason"),
                    "stop_details": a.get("stop_details"), "truncated": a.get("truncated"),
                    "grade": level(arm_grades.get(i)).get("level"), "grade_reason": level(arm_grades.get(i)).get("reason"),
                    "answered_parts": level(arm_grades.get(i)).get("answered_parts"),
                    "sources": a.get("sources"), "context_words": a.get("context_words"),
                    "output_tokens": (a.get("usage") or {}).get("output_tokens")},
        })
    out = C.OUT / f"review_sheet_{tag}.json"
    out.write_text(json.dumps(sheet, ensure_ascii=False, indent=1), encoding="utf-8")
    safe_print(f"[model_arm] {out.name}: {len(sheet)} rows")
    return 0


def cmd_report(tag: str) -> int:
    from collections import Counter
    from night.ledger import Ledger
    review_path = C.OUT / f"review_grade-{tag}.json"
    review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
    a48, n24 = row_sets()
    j = judge(review, a48, n24)
    if (ARM_MODEL, BASE_TAG) != (DEFAULT_ARM_MODEL, DEFAULT_BASE_TAG):
        safe_print(f"[model_arm] note: the protected rows, the answer-side rows and the 48-row bar below are those of "
                   f"MODEL_ARM_CRITERION.md ({DEFAULT_ARM_MODEL} on {DEFAULT_BASE_TAG}); this arm ({ARM_MODEL} on "
                   f"{BASE_TAG}) is judged by its own criterion")
    arm = {r["id"]: r for r in C.read_jsonl(_path(tag))}
    grades = {r["id"]: r for r in C.read_jsonl(C.OUT / f"grades_grade-{tag}.jsonl")}
    base_grades = {r["id"]: r for r in C.read_jsonl(C.OUT / f"grades_grade-{BASE_TAG}.jsonl")}
    base_review = json.loads((C.OUT / f"review_grade-{BASE_TAG}.json").read_text(encoding="utf-8"))
    lv = lambda r: ((r or {}).get("grade") or {}).get("level", "ungraded")
    if j["missing"]:
        safe_print(f"[model_arm] stage-6 review incomplete: {len(j['missing'])} rows without a verdict {j['missing']}")
    safe_print(f"[model_arm] (א) confident-and-wrong: {len(j['confident_wrong'])} {j['confident_wrong']}")
    safe_print(f"[model_arm] (ב) drops among the {len(PROTECTED)} protected rows: {len(j['drops'])} {j['drops']}")
    safe_print(f"[model_arm] (ג) answer-side rows:")
    for i in ANSWER_SIDE:
        b, a = base_review.get(i) or {}, review.get(i) or {}
        safe_print(f"   {i}: {BASE_TAG} {b.get('verdict')} -> {tag} {a.get('verdict')} — {a.get('note', '')}")
    safe_print(f"[model_arm] (ד) FINAL_RULER_V2 (א): {len(j['full48'])}/48 full "
               f"(+{len(j['border48'])} borderline) vs 4.8 {BASE_FULL_48['official']}/48 official, "
               f"{BASE_FULL_48['production']}/48 production logic; the ruler's bar {PASS_48}")
    safe_print(f"[model_arm] (ה) invented rules in the 24: {len(j['invented'])} {j['invented']}")
    safe_print(f"[model_arm] {'PASSES' if j['passed'] else 'FAILS'} (א)+(ב)+(ה)")
    # for the record
    safe_print(f"[model_arm] grader: {BASE_TAG} {dict(Counter(lv(r) for r in base_grades.values()))} -> "
               f"{tag} {dict(Counter(lv(r) for r in grades.values()))}")
    safe_print(f"[model_arm] kept: {dict(Counter(r.get('kept') for r in arm.values()))}; refusal stops "
               f"{sorted(i for i, r in arm.items() if r.get('refusal_stop') or r.get('p1_stop_reason') == 'refusal')}; "
               f"truncated {sorted(i for i, r in arm.items() if r.get('truncated'))}")
    p1 = C.read_jsonl(_path(tag, "_p1"))
    p2 = C.read_jsonl(_path(tag, "_p2")) if _path(tag, "_p2").exists() else []
    mean_out = lambda rows: sum((r.get("usage") or {}).get("output_tokens", 0) for r in rows) / max(1, len(rows))
    safe_print(f"[model_arm] output tokens mean p1 {mean_out(p1):.0f} (4.8: 868), p2 {mean_out(p2):.0f} (4.8: 865); "
               f"second passes {len(p2)} (4.8: 56); window words mean "
               f"{sum(r.get('context_words') or 0 for r in arm.values()) // max(1, len(arm))}")
    ledger = Ledger(C.LEDGER)
    spent = {e["label"]: e["actual"] for e in ledger._state["entries"]
             if e["label"] in (f"probe-{tag}_p1", f"probe-{tag}_p2", f"compose-{tag}_p2", f"grade-grade-{tag}")}
    safe_print(f"[model_arm] spent {spent} = ${sum(v or 0 for v in spent.values()):.2f} "
               f"(${sum(v or 0 for v in spent.values()) / 72:.3f} per question); ledger ${ledger.spent:.2f}")
    return 0 if j["passed"] else 1


def cmd_collect(tag: str, which: str) -> int:
    """Recovery: finish a batch whose process died (the claim ticket holds the map)."""
    FA._need_key()
    import backend
    from night.ledger import Ledger
    t = json.loads((C.OUT / f"batch_probe-{tag}_{which}.json").read_text(encoding="utf-8"))
    out = Path(t["out_path"])
    if out.exists():
        safe_print(f"[model_arm] {out.name} already on disk."); return 1
    b = backend.client.messages.batches.retrieve(t["batch_id"])
    if b.processing_status != "ended":
        safe_print(f"[model_arm] batch {t['batch_id']} is {b.processing_status} — try later."); return 1
    _collect(t["batch_id"], t["meta"], t["rid"], out, t["label"], Ledger(C.LEDGER))
    return 0


def _parse(argv: list[str]) -> tuple[str, str, list[str], str | None, str | None]:
    """(command, tag, further positionals, --model, --base). The options may stand anywhere, as
    `--model X` or `--model=X`; the positionals are what they were before the options existed."""
    model = base = None
    pos: list[str] = []
    it = iter(argv[1:])
    for a in it:
        name, eq, val = a.partition("=")
        if name in ("--model", "--base"):
            if not eq:
                val = next(it, "")
            if not val:
                raise SystemExit(f"[model_arm] {name} needs a value")
            if name == "--model":
                model = val
            else:
                base = val
        else:
            pos.append(a)
    return (pos[0] if pos else "dry"), (pos[1] if len(pos) > 1 else "opus5v2"), pos[2:], model, base


def _check_tag(tag: str) -> None:
    if tag == BASE_TAG:
        raise SystemExit(f"[model_arm] the arm's tag is its base's ({BASE_TAG}) — an arm is compared WITH its base, "
                         f"it cannot write over it")


def main(argv: list[str]) -> int:
    cmd, tag, rest, model, base = _parse(argv)
    configure(model, base)
    _check_tag(tag)
    # before backend is imported anywhere: backend.MODEL is read once, at import
    os.environ["ANSWER_MODEL"] = ARM_MODEL
    fn = {"dry": cmd_dry, "p1": cmd_p1, "p2": cmd_p2, "grade": cmd_grade, "sheet": cmd_sheet,
          "report": cmd_report}.get(cmd)
    if cmd == "collect":
        if not rest:
            raise SystemExit(__doc__)
        return cmd_collect(tag, rest[0])
    if fn is None:
        raise SystemExit(__doc__)
    return fn(tag)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
