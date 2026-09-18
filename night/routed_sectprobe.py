"""sectprobe with the REAL router's picks replayed — free, no API.

`night/sectprobe.py` runs with route=set(), so it is a floor: production seats
the Haiku router's picks (RETRIEVE_ROUTER_SLOTS) and serves more. This replays
the picks the real router gave on 2026-09-18 for the 82 adjudicated targets
(`night/out/routeprobe_plain.json`, paid once) through the same window code,
so a corpus or anchor change is measured with the router in at zero new spend.

Numbers of record (FULL_BLOCKS=1 DOC_BLOCKS=6 ROUTER_SLOTS=2, glossary OFF):
    18.09 before the anchors   35/82 sections, 11/20 real-style  (sect5_routed_base.json)
    18.09 after 68 anchors     37/82 sections, 12/20 real-style  (sect6_routed_anchors.json)
With the glossary on as well — the full production set — the same tree reads
37/82 and 12/20 with zero lost / zero gained against the record; seven windows
differ in size (sect7_routed_prodflags.json). A before/after pair must be run
on ONE flag set; the production set below is the faithful one.

The picks are a recording: an order added or renamed after 18.09 is never
picked here, so the number can only under-state what a fresh router would do.
A target that was served before a change and is not served after it is a real
loss either way — that is what the corpus gate reads (exit code 1).

    set RETRIEVE_GLOSSARY=1& set RETRIEVE_FULL_BLOCKS=1& set RETRIEVE_ROUTER_SLOTS=2& set RETRIEVE_DOC_BLOCKS=6
    python -m night.routed_sectprobe night/out/<result>.json [picks.json] [--against night/out/sect7_routed_prodflags.json]
"""
import json
import os
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# night/numbers.py shadows the stdlib `numbers` when night/ itself is on the path
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != ROOT / "night"]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("RETRIEVE_HYDE", "0")

import backend  # noqa: E402
import night.sectprobe as sp  # noqa: E402

PICKS = ROOT / "night" / "out" / "routeprobe_plain.json"


def run(picks_path: Path) -> dict:
    picks = {r["id"]: r["picks"] for r in json.loads(Path(picks_path).read_text(encoding="utf-8"))}
    per = []
    for t in sp.targets():
        allowed = {d["document_id"] for d in backend._docs_for_role(t["role"]) if d.get("document_id")}
        route = set(picks.get(t["id"], [])) & allowed          # exactly what _route_docs returns
        hyde = backend.RETRIEVE_HYDE
        backend.RETRIEVE_HYDE = False
        try:
            win = backend.retrieve_for_role(t["q"], t["role"], route=route, widen=False)
            win = backend.widen_context(win, t["q"], t["role"], route=route)
        finally:
            backend.RETRIEVE_HYDE = hyde
        served = [sp._norm(c["text"]) for c in win if c["doc_id"] == t["doc_id"]]
        verb = (any(q in s for q in t["quotes"] for s in served)
                or any(r in s for r in t["runs"] for s in served))
        cur = [c for c in win if c["doc_id"] == t["doc_id"] and "key-facts" in (c.get("section") or "")]
        ov = max((sp.content_overlap(t["quotes"], (c.get("clause") or "") + " " + c["text"]) for c in cur),
                 default=0.0)
        per.append({
            "id": t["id"], "doc_id": t["doc_id"],
            "doc_in_window": any(c["doc_id"] == t["doc_id"] for c in win),
            "sect_in_window": verb,
            "sect_content": bool(verb or ov >= sp.CONTENT_MIN), "sect_overlap": round(ov, 2),
            "window_words": sum(len(c["text"].split()) for c in win),
            "window_docs": len({c["doc_id"] for c in win}),
        })
    rs = [p for p in per if p["id"].startswith("rs")]
    agg = {
        "n": len(per),
        "doc_in_window": sum(p["doc_in_window"] for p in per),
        "sect_in_window": sum(p["sect_in_window"] for p in per),
        "sect_in_window_content": sum(p["sect_content"] for p in per),
        "rs_content": sum(p["sect_content"] for p in rs),
        "words_median": st.median(p["window_words"] for p in per),
        "docs_median": st.median(p["window_docs"] for p in per),
    }
    return {"agg": agg, "per": per}


def compare(before: dict, after: dict) -> tuple[list[str], list[str]]:
    """(lost, gained) target ids by the content rule. One lost target stops a corpus wave."""
    was = {p["id"]: p["sect_content"] for p in before["per"]}
    now = {p["id"]: p["sect_content"] for p in after["per"]}
    lost = sorted(i for i, ok in was.items() if ok and not now.get(i, False))
    gained = sorted(i for i, ok in now.items() if ok and not was.get(i, False))
    return lost, gained


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    against = None
    if "--against" in argv:
        against = argv[argv.index("--against") + 1]
        args = [a for a in args if a != against]
    if not args:
        print(__doc__)
        return 2
    out = Path(args[0])
    res = run(Path(args[1]) if len(args) > 1 else PICKS)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(res["agg"])
    if against:
        lost, gained = compare(json.loads(Path(against).read_text(encoding="utf-8")), res)
        print(f"against {against}: lost {len(lost)} {lost} | gained {len(gained)} {gained}")
        return 1 if lost else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
