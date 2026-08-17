"""The hypothetical-answer extension must ADD and never reorder.

That is the whole finding, and it is a one-line difference in the code with a
13-question difference in the gate: three slot-taking variants each cost 12-13
of the 415 gate cases their top-3 placement, while appending costs zero. A
future edit that "tidies" this into a merge-and-sort would silently reintroduce
the regression, and the gate run that would catch it costs a paid Haiku pass
over 415 questions. These assertions are free.

    venv\\Scripts\\python.exe tests\\test_hyde_retrieval.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend

QUESTION = "כמה ימי חופשה מגיעים לחייל בשירות סדיר?"
ROLE = "soldier"
CALLS: list[str] = []


class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Response:
    def __init__(self, text):
        self.content = [_Block(text)]


class _FakeClient:
    """Stands in for the Anthropic client at the API boundary.

    Stubbing `backend.hypothetical` itself was the first attempt and it made
    the cache assertion vacuous — the stub had no cache, so the test reported
    two calls and blamed the code for the test's own shape. Patching one level
    down exercises the real function, cache included.
    """
    def __init__(self, text, fail=False):
        self._text, self._fail = text, fail

    def with_options(self, **_kw):
        return self

    @property
    def messages(self):
        return self

    def create(self, **kw):
        CALLS.append(kw["messages"][0]["content"])
        if self._fail:
            raise RuntimeError("simulated API failure")
        return _Response(self._text)


def with_flags(*, hyde: bool, extra: int = 1):
    backend.RETRIEVE_HYDE = hyde
    backend.HYDE_EXTRA_CHUNKS = extra
    backend.RETRIEVE_ROUTER_SLOTS = 0
    backend.RETRIEVE_FULL_BLOCKS = 0
    backend._hyde_cache.clear()
    CALLS.clear()


def key(c):
    return (c["doc_id"], c.get("section"), c.get("clause"))


def main() -> int:
    real = backend.client
    failed = []
    canned = "חייל אשר השלים שנת שירות יהיה זכאי לחופשה שנתית."

    def check(name, cond, detail=""):
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
        if not cond:
            failed.append(name)

    try:
        # baseline: flag off, nothing bought, exactly the production window
        with_flags(hyde=False)
        backend.client = _FakeClient(canned)
        base = backend.retrieve_for_role(QUESTION, ROLE, route=set())
        check("flag off returns MAX_CONTEXT_CHUNKS",
              len(base) == backend.MAX_CONTEXT_CHUNKS, f"got {len(base)}")
        check("flag off buys no hypothetical", CALLS == [], f"calls={len(CALLS)}")

        # flag on: one extra chunk, and the first eight are byte-identical
        with_flags(hyde=True)
        wide = backend.retrieve_for_role(QUESTION, ROLE, route=set())
        check("flag on adds exactly HYDE_EXTRA_CHUNKS",
              len(wide) == len(base) + 1, f"got {len(wide)} vs {len(base)}")
        check("the question's own ranking is untouched",
              [key(c) for c in wide[:len(base)]] == [key(c) for c in base])
        check("the appended chunk is new, not a duplicate",
              key(wide[-1]) not in {key(c) for c in base})

        # the cache is what stops stream_ai_answer paying twice per question
        with_flags(hyde=True)
        backend.retrieve_for_role(QUESTION, ROLE, route=set())
        backend.retrieve_for_role(QUESTION, ROLE, route=set())
        check("one hypothetical per question, not per retrieval",
              len(CALLS) == 1, f"calls={len(CALLS)}")

        # a failed generation must degrade to the question alone
        with_flags(hyde=True)
        backend.client = _FakeClient("", fail=True)
        empty = backend.retrieve_for_role(QUESTION, ROLE, route=set())
        check("a failed generation falls back to the plain window",
              [key(c) for c in empty] == [key(c) for c in base], f"got {len(empty)}")

        # widen=False is what keeps stream_ai_answer's union from paying twice
        with_flags(hyde=True)
        backend.client = _FakeClient(canned)
        narrow = backend.retrieve_for_role(QUESTION, ROLE, route=set(), widen=False)
        check("widen=False suppresses the extension",
              len(narrow) == len(base) and CALLS == [], f"got {len(narrow)}, calls={len(CALLS)}")


        # --- router seats and full blocks -----------------------------------
        # both off: byte-identical to hyde1
        with_flags(hyde=True)
        backend.client = _FakeClient(canned)
        backend.RETRIEVE_ROUTER_SLOTS = 0
        backend.RETRIEVE_FULL_BLOCKS = 0
        w0 = backend.retrieve_for_role(QUESTION, ROLE, route=set())

        # router seats: appended, never reordering; needs a route to act on
        backend.RETRIEVE_ROUTER_SLOTS = 2
        docs = [d["document_id"] for d in backend._docs_for_role(ROLE)
                if d.get("document_id") and backend._has_key_facts(d)]
        # pick two orders that are NOT already in the window
        present = {c["doc_id"] for c in w0}
        seats = [d for d in docs if d not in present][:2]
        w_r = backend.retrieve_for_role(QUESTION, ROLE, route=set(seats))
        check("router seats append exactly RETRIEVE_ROUTER_SLOTS chunks",
              len(w_r) == len(w0) + 2, f"got {len(w_r)} vs {len(w0)}")
        check("router seats do not reorder the prefix",
              [key(c) for c in w_r[:len(w0)]] == [key(c) for c in w0])
        check("router seats are from the routed orders",
              {c["doc_id"] for c in w_r[len(w0):]} <= set(seats))
        check("router seats without a route are a no-op",
              len(backend.retrieve_for_role(QUESTION, ROLE, route=set())) == len(w0))
        backend.RETRIEVE_ROUTER_SLOTS = 0

        # full blocks: the leading order's whole curated block is present
        backend.RETRIEVE_FULL_BLOCKS = 1
        w_f = backend.retrieve_for_role(QUESTION, ROLE, route=set())
        lead = w0[0]["doc_id"]
        lead_doc = next(d for d in backend._docs_for_role(ROLE) if d.get("document_id") == lead)
        n_block = len(backend._full_block(lead_doc))
        served = sum(1 for c in w_f if c["doc_id"] == lead
                     and "key-facts" in str(c.get("section", "")))
        check("full block: every curated clause of the lead order is served",
              n_block > 0 and served >= n_block, f"block={n_block} served={served}")
        check("full block does not reorder the prefix",
              [key(c) for c in w_f[:len(w0)]] == [key(c) for c in w0])
        check("full block deduplicates against chunks already present",
              len({key(c) for c in w_f}) == len(w_f))
        backend.RETRIEVE_FULL_BLOCKS = 0

        # more than one extra chunk, still appended
        with_flags(hyde=True, extra=3)
        wider = backend.retrieve_for_role(QUESTION, ROLE, route=set())
        check("HYDE_EXTRA_CHUNKS=3 appends three",
              len(wider) == len(base) + 3, f"got {len(wider)}")
        check("still no reordering at extra=3",
              [key(c) for c in wider[:len(base)]] == [key(c) for c in base])
    finally:
        backend.client = real
        with_flags(hyde=False)

    print(f"\n{'FAILED: ' + ', '.join(failed) if failed else 'all checks passed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
