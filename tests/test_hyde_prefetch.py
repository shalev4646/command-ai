# -*- coding: utf-8 -*-
"""The hypothetical is fetched ONCE per question, even when prefetched.

The pre-answer phase is serial: rewrite (Haiku) -> router (Haiku) -> retrieval
-> hypothetical (Haiku). The hypothetical depends only on the raw question, so
it can run alongside the rest — but only if a second caller JOINS the in-flight
call instead of issuing its own. `_hyde_cache` is filled at the END of the call,
so a plain fire-and-forget thread would leave the window open and buy the same
paragraph twice (real money, and the night measurements assume one draw).

Run: venv\\Scripts\\python.exe tests\\test_hyde_prefetch.py
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


class FakeClient:
    """Counts calls and sleeps, so overlap is measurable."""

    def __init__(self, delay=0.4, fail=False):
        self.delay = delay
        self.fail = fail
        self.calls = 0
        self.lock = threading.Lock()
        self.messages = self

    def with_options(self, **kwargs):
        return self

    def create(self, **kwargs):
        with self.lock:
            self.calls += 1
        time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("simulated API failure")
        return _Resp("חייל אשר מבקש חופשה מיוחדת יהיה זכאי")


def _fresh(fake):
    backend.client = fake
    # RETRIEVE_HYDE defaults to OFF in code and is switched on in fly.toml, so a
    # test that leaves it alone measures a no-op prefetch and passes for the
    # wrong reason — five of these checks did exactly that on the first run.
    # Production is the condition under test.
    backend.RETRIEVE_HYDE = True
    backend._hyde_cache.clear()
    if hasattr(backend, "_hyde_inflight"):
        backend._hyde_inflight.clear()


def check(name, cond, detail=""):
    # Windows pipes this through cp1252; Hebrew detail must not kill the run
    line = ("PASS " if cond else "FAIL ") + name + ((" - " + detail) if detail else "")
    print(line.encode("ascii", "backslashreplace").decode("ascii"))
    return bool(cond)


def main():
    real_client = backend.client
    real_flag = backend.RETRIEVE_HYDE
    ok = True
    q = "תוך כמה זמן אני אמור לקבל תור לקבן"
    try:
        # 1. prefetch + later call = exactly ONE API call, same text
        fake = FakeClient(delay=0.4)
        _fresh(fake)
        backend.prefetch_hypothetical(q)
        text = backend.hypothetical(q)
        ok &= check("prefetch then call buys once", fake.calls == 1, f"calls={fake.calls}")
        ok &= check("prefetched text returned", text.startswith("חייל אשר"), repr(text[:20]))

        # 2. the joining caller must WAIT for the in-flight result, not race past
        #    it with an empty string
        fake = FakeClient(delay=0.6)
        _fresh(fake)
        t0 = time.monotonic()
        backend.prefetch_hypothetical(q)
        time.sleep(0.05)          # caller arrives while the call is in flight
        text = backend.hypothetical(q)
        elapsed = time.monotonic() - t0
        ok &= check("joiner waits for in-flight text", bool(text), repr(text[:20]))
        ok &= check("joiner does not re-buy", fake.calls == 1, f"calls={fake.calls}")
        ok &= check("no double wait (overlap real)", elapsed < 1.0, f"{elapsed:.2f}s")

        # 3. overlap actually saves wall-clock vs the serial order
        fake = FakeClient(delay=0.5)
        _fresh(fake)
        t0 = time.monotonic()
        backend.prefetch_hypothetical(q)
        time.sleep(0.5)           # stands in for rewrite+router+retrieval
        backend.hypothetical(q)
        overlapped = time.monotonic() - t0
        fake2 = FakeClient(delay=0.5)
        _fresh(fake2)
        t0 = time.monotonic()
        time.sleep(0.5)
        backend.hypothetical(q)
        serial = time.monotonic() - t0
        ok &= check("overlap beats serial", overlapped < serial - 0.2,
                    f"overlapped={overlapped:.2f}s serial={serial:.2f}s")

        # 4. failure keeps the production contract: "" and retrieval proceeds
        fake = FakeClient(delay=0.1, fail=True)
        _fresh(fake)
        backend.prefetch_hypothetical(q)
        text = backend.hypothetical(q)
        ok &= check("failure returns empty", text == "", repr(text))
        ok &= check("failure is not retried by the joiner", fake.calls == 1, f"calls={fake.calls}")

        # 5. night/hyde.py preloads _hyde_cache directly - a preloaded question
        #    must never reach the API, prefetch or not
        fake = FakeClient(delay=0.4)
        _fresh(fake)
        backend._hyde_cache[q] = "טקסט ששולם עליו כבר"
        backend.prefetch_hypothetical(q)
        text = backend.hypothetical(q)
        ok &= check("preloaded cache short-circuits", fake.calls == 0 and text == "טקסט ששולם עליו כבר",
                    f"calls={fake.calls}")

        # 6. the flag is honoured: with HyDE off, prefetch must not spend
        fake = FakeClient(delay=0.1)
        _fresh(fake)
        saved = backend.RETRIEVE_HYDE  # _fresh set it True; flip it back off
        backend.RETRIEVE_HYDE = False
        try:
            backend.prefetch_hypothetical(q)
            time.sleep(0.2)
            ok &= check("prefetch respects RETRIEVE_HYDE=0", fake.calls == 0, f"calls={fake.calls}")
        finally:
            backend.RETRIEVE_HYDE = saved

        # 7. two different questions still get their own draw
        fake = FakeClient(delay=0.2)
        _fresh(fake)
        backend.prefetch_hypothetical("שאלה א")
        backend.prefetch_hypothetical("שאלה ב")
        backend.hypothetical("שאלה א")
        backend.hypothetical("שאלה ב")
        ok &= check("distinct questions buy separately", fake.calls == 2, f"calls={fake.calls}")
    finally:
        backend.client = real_client
        backend.RETRIEVE_HYDE = real_flag
        backend._hyde_cache.clear()
        if hasattr(backend, "_hyde_inflight"):
            backend._hyde_inflight.clear()

    print("\n" + ("ALL PASS" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
