"""The one browser launcher the ui_audit scripts share.

CAI_AUDIT_BROWSER picks the engine: a path to a Chromium-family executable,
or a Playwright channel name (msedge, chrome). The default is Edge on Windows —
the dev machine has it and no downloaded Playwright browsers — and Playwright's
bundled Chromium elsewhere.
"""
import os
import sys

VIEWPORT = {"width": 390, "height": 844}


def launch(p):
    choice = os.environ.get("CAI_AUDIT_BROWSER", "")
    if choice and os.path.exists(choice):
        return p.chromium.launch(executable_path=choice, args=["--no-sandbox"])
    channel = choice or ("msedge" if sys.platform == "win32" else "")
    if channel:
        return p.chromium.launch(channel=channel)
    return p.chromium.launch(args=["--no-sandbox"])
