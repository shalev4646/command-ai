# -*- coding: utf-8 -*-
"""Production launcher: start the warm-up thread from PROCESS start, then run
Streamlit in this same process.

Why not `streamlit run app.py` directly: app.py runs per SESSION, so its
cached warm-up (_startup_ingest -> backend.warm_index) ran inside the first
session after every process start — a deploy, or Fly moving the machine
(10.09 14:49Z). That session blocked the event loop for ~15s (Fly: health
check failed 20:56:56Z, the second the pilot tapped the icon; again at
21:18:53Z after the 21:17Z deploy) and the first launch of the day took
43.7s. Sharing one process is what makes backend's module-level caches
(ONNX stack, index, parsed corpus) warm for every session that follows.

Usage (the Dockerfile CMD): python run_server.py --server.fileWatcherType none
Any extra arguments go to `streamlit run app.py` unchanged.
"""
import os
import sys

import backend

backend.start_keep_warm(
    interval_sec=float(os.environ.get("CAI_KEEP_WARM_SEC", "600")),
    delay_sec=float(os.environ.get("CAI_WARM_DELAY_SEC", "3")),
)

from streamlit.web import cli as stcli  # noqa: E402 — after the thread is started

sys.argv = ["streamlit", "run", "app.py"] + sys.argv[1:]
sys.exit(stcli.main())
