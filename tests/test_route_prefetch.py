# -*- coding: utf-8 -*-
"""Speculative routing: start the router on the raw question while the rewrite
is still in flight, and JOIN it only if the rewrite came back verbatim.

The router needs the REWRITTEN query, so speculation is a bet: it wins ~1s when
the rewrite returns the question unchanged, and buys one extra ~$0.0025 Haiku
call when it does not. Measured free on the 33 logged questions: the rewrite
runs on 36% of them (18% follow-ups + 18% tripping the vocabulary gate); on the
other 64% `_standalone_question` returns instantly and the router is already
concurrent with the hypothetical, so there is nothing to win there. Hence the
flag is OFF until a paired measurement says otherwise - these tests pin the
mechanics either way.

Run: venv\Scripts\python.exe tests\test_route_prefetch.py
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend  # noqa: E402


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class FakeRouter:
    """Counts router calls and records which query text each one saw."""

    def __init__(self, delay=0.3, reply="31.0102"):
        self.delay = delay
        self.reply = reply
        self.calls = 0
        self.queries = []
        self.lock = threading.Lock()
        self.messages = self

    def with_options(self, **kwargs):
        return self

    def create(self, **kwargs):
        content = kwargs["messages"][0]["content"]
        with self.lock:
            self.calls += 1
            self.queries.append(content)
        time.sleep(self.delay)
        return _Resp(self.reply)


def _fresh(fake, flag=True):
    backend.client = fake
    backend.RETRIEVE_SPECULATIVE_ROUTE = flag
    backend._route_inflight.clear()


def check(name, cond, detail=""):
    line = ("PASS " if cond else "FAIL ") + name + ((" - " + detail) if detail else "")
    print(line.encode("ascii", "backslashreplace").decode("ascii"))
    return bool(cond)


def main():
    real_client, real_flag = backend.client, backend.RETRIEVE_SPECULATIVE_ROUTE
    real_allowed = backend._docs_for_role
    ok = True
    q = "כמה ימי חופשה מגיעים לי"
    rewritten = "כמה ימי חופשה שנתית מגיעים לחייל בשירות חובה"
    # the router intersects its picks with the role's real document ids
    backend._docs_for_role = lambda role: [{"document_id": "31.0102", "title": "t"}]
    try:
        # 1. rewrite returned the question verbatim -> join, one call, ~free
        fake = FakeRouter(delay=0.4)
        _fresh(fake)
        t0 = time.monotonic()
        backend.prefetch_route(q, "soldier")
        time.sleep(0.05)                       # the rewrite "runs"
        route = backend.route_for(q, "soldier", raw_question=q)
        elapsed = time.monotonic() - t0
        ok &= check("verbatim rewrite joins the speculation", fake.calls == 1, f"calls={fake.calls}")
        ok &= check("joined route is the real route", route == {"31.0102"}, str(route))
        ok &= check("join does not double the wait", elapsed < 0.75, f"{elapsed:.2f}s")

        # 2. rewrite CHANGED the text -> the speculative route must not be used
        fake = FakeRouter(delay=0.2)
        _fresh(fake)
        backend.prefetch_route(q, "soldier")
        route = backend.route_for(rewritten, "soldier", raw_question=q)
        ok &= check("changed rewrite re-routes on the new query", fake.calls == 2, f"calls={fake.calls}")
        ok &= check("the route served is the rewritten one",
                    any(rewritten in c for c in fake.queries),
                    f"{len(fake.queries)} router queries seen")
        ok &= check("the abandoned speculation is not left behind",
                    not backend._route_inflight, str(list(backend._route_inflight)))

        # 3. flag off = no speculation at all, exactly one call, old behaviour
        fake = FakeRouter(delay=0.1)
        _fresh(fake, flag=False)
        backend.prefetch_route(q, "soldier")
        time.sleep(0.15)
        ok &= check("flag off: prefetch spends nothing", fake.calls == 0, f"calls={fake.calls}")
        route = backend.route_for(q, "soldier", raw_question=q)
        ok &= check("flag off: route still computed", route == {"31.0102"} and fake.calls == 1,
                    f"calls={fake.calls} route={route}")

        # 4. role is part of the key - a commander must not inherit a soldier route
        fake = FakeRouter(delay=0.2)
        _fresh(fake)
        backend.prefetch_route(q, "soldier")
        backend.route_for(q, "commander", raw_question=q)
        ok &= check("role mismatch does not join", fake.calls == 2, f"calls={fake.calls}")

        # 5. router failure inside the thread degrades to an empty route, never raises
        class Boom(FakeRouter):
            def create(self, **kwargs):
                with self.lock:
                    self.calls += 1
                raise RuntimeError("router down")
        fake = Boom(delay=0)
        _fresh(fake)
        backend.prefetch_route(q, "soldier")
        route = backend.route_for(q, "soldier", raw_question=q)
        ok &= check("router failure -> empty route", route == set(), str(route))
        ok &= check("router failure is not retried by the joiner", fake.calls == 1, f"calls={fake.calls}")
    finally:
        backend.client = real_client
        backend.RETRIEVE_SPECULATIVE_ROUTE = real_flag
        backend._docs_for_role = real_allowed
        backend._route_inflight.clear()

    print("\n" + ("ALL PASS" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
