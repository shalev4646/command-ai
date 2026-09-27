# -*- coding: utf-8 -*-
"""Free diagnosis of the final161v2 misses (night/head100/RUN_LOG.md section 14).

For the 14 rows the stage-6 review did not count as full: is the order, and the
clause that answers, in the free window? v161 flags from fly.toml, HyDE off, and
the router picks REPLAYED from the paid record (`route` of probe_final161v2_p1) —
so the only thing missing against production is HyDE. Both wordings run: v2 (the
test) and the frozen ruler (old picks, night/out/routeprobe_plain.json).

The exact window the model got is in the record itself (`sent_user_content` of
probe_final161v2_p1/_p2); this tool answers the counterfactuals, all free:
    python night/diag_final161v2.py <tree-root> [out.json]
    set CAND_GLOSS={"term": "expansion"}   # dictionary entries added IN MEMORY only
Run it in a throwaway worktree after `apply_defs --write` to test a definition;
it never writes the corpus. A dictionary entry written for these rows and tested
on them is a mirror (CLAUDE.md rule 5) — indicative only, never evidence."""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
os.chdir(ROOT)
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != ROOT / "night"]
sys.path.insert(0, str(ROOT))
inside = False
for line in (ROOT / "fly.toml").read_text(encoding="utf-8").splitlines():
    s = line.strip()
    if s.startswith("["):
        inside = s == "[env]"
        continue
    m = re.match(r'^([A-Z_][A-Z0-9_]*)\s*=\s*"(.*)"\s*$', s)
    if inside and m:
        os.environ[m.group(1)] = m.group(2)
os.environ["RETRIEVE_HYDE"] = "0"
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.stdout.reconfigure(encoding="utf-8")

import backend  # noqa: E402
import storage.glossary as _g  # noqa: E402

_g.GLOSSARY.update(json.loads(os.environ.get("CAND_GLOSS", "{}")))

OUT = Path(__file__).resolve().parents[1] / "night" / "out"
rec = {json.loads(l)["id"]: json.loads(l) for l in open(OUT / "probe_final161v2_p1.jsonl", encoding="utf-8")}
qf = {r["id"]: r for r in json.loads((OUT / "realstyle_questions.json").read_text(encoding="utf-8"))}
qv = {r["id"]: r for r in json.loads((OUT / "realstyle_v2_questions.json").read_text(encoding="utf-8"))}
old_picks = {r["id"]: r["picks"] for r in json.loads((OUT / "routeprobe_plain.json").read_text(encoding="utf-8"))}

# row -> (doc, regex that identifies the answering clause by its text or title)
T = {
    "rs014": ("33-05-01", r"שערות ראשו|שיער ותספורת לחייל"),
    "rs024": ("33-05-01", r"מרפק|לקפל את ש"),
    "rs054": ("35.0807", r"לינת בית|לינה בבית"),
    "rs059": ("HKA-31-08-01", r"לאשר במהלך השמ|חופשה ארוכה יותר|מפקד היחידה (רשאי|מאשר)[^.]{0,40}חופשה"),
    "rs067": ("31.0103", r"צו הצבה|מעטפת"),
    "rs070": ("CHOK-SHIPUT-1955", r"לא קיימתי פקודה|שלא קיים פקודה"),
    "rs071": ("33.0220", r"חיפוש|פרקליט"),
    "rs008": ("PM-33.0302", r"איחרתי|איחור ליחידה"),
    "rs007": ("35.0206", r"ביטוח לאומי|3010"),
    "rs018": ("CHOK-SHIPUT-1955", r"שנעדר משירותו|נעדרתי מהשירות"),
    "rs055": ("PM-33.0213", r"השלמה של ?3"),
    "rs037": ("30.0117", r"טקס הפר"),
    "rs026": ("35.0210", r"מי זכאי לקבל את התשלום|זכאי"),
}


def window(q, role, route):
    allowed = {d["document_id"] for d in backend._docs_for_role(role) if d.get("document_id")}
    route = set(route) & allowed
    win = backend.retrieve_for_role(q, role, route=route, widen=False)
    return backend.widen_context(win, q, role, route=route)


res = {}
for i, (doc, pat) in T.items():
    role = qv[i]["role"]
    row = {}
    for tag, q, route in (("v2", qv[i]["q"], rec[i].get("route") or []),
                          ("frozen", qf[i]["q"], old_picks.get(i, rec[i].get("route") or []))):
        win = window(q, role, route)
        mine = [c for c in win if c["doc_id"] == doc]
        hit = [c.get("clause") for c in mine if re.search(pat, (c.get("clause") or "") + " " + c["text"])]
        row[tag] = {"doc": bool(mine), "clauses": len(mine), "clause_hit": hit[:3],
                    "words": sum(len(c["text"].split()) for c in win)}
    res[i] = row
    print(f"{i} {doc:18} v2: doc={row['v2']['doc']!s:5} n={row['v2']['clauses']:2} hit={row['v2']['clause_hit'] or '-'}"
          f" | frozen: doc={row['frozen']['doc']!s:5} n={row['frozen']['clauses']:2} hit={row['frozen']['clause_hit'] or '-'}")
if len(sys.argv) > 2:
    Path(sys.argv[2]).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
