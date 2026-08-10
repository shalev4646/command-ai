import base64
import datetime as _dt
import html
import inspect
from pathlib import Path
import itertools
import json
import os
import random
import urllib.parse
import re
import time
import traceback
import uuid
import streamlit as st
import streamlit.components.v1 as components
from anthropic import APIConnectionError, APITimeoutError, BadRequestError

# KEEP THE LOGS USABLE. Streamlit 1.58 logs a two-line "replace
# st.components.v1.html with st.iframe" deprecation notice PER CALL, PER
# RERUN — this app makes ~8 component calls per run, so a few minutes of use
# buries everything else. On 2026-07-28 the whole retrievable Fly log buffer
# for the incident window was that one warning, repeated, and there was
# nothing left to diagnose with. Not migrating: st.iframe renders an HTML
# string in a sandboxed frame, and every engine here (viewport pin, drawer
# gestures, PWA metadata) reaches window.parent.document to install itself on
# the app page — that access dies in a sandbox and would take the whole
# client layer with it.
def _mute_component_deprecation() -> None:
    import logging

    class _Mute(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            try:
                return "st.components.v1.html" not in record.getMessage()
            except Exception:
                return True

    logging.getLogger("streamlit.deprecation_util").addFilter(_Mute())


_mute_component_deprecation()

import metrics
import escalation_paths
from escalation_paths import path_for
from boot_shell import patch_index_html
import pdf_static
import pwa_assets
from common import safe_print

# letters/doc_dates are sibling new modules — a cached cloud build can pair
# a fresh app.py with an older tree (see backend deploy note), so a missing
# module hides its feature instead of crashing the app
try:
    from letters import LETTER_TYPES, compose_letter
except Exception:
    LETTER_TYPES = None
try:
    from doc_dates import badge as _doc_date_badge
except Exception:
    def _doc_date_badge(_id):
        return None
try:
    import answer_format
except Exception:
    answer_format = None   # the answer body falls back to bare markdown
try:
    from verdict import verdict_clauses as _verdict_clauses, chip_clause as _chip_clause
    from verdict import CHIP_TERM_RE as _VERDICT_TERM_RE, QUAL_CONFLICT_RE as _QUAL_CONFLICT_RE
except Exception:
    def _verdict_clauses(_content):
        return []

    def _chip_clause(_clause):
        return None
    # never-matching stand-ins: every chip gate fails closed, the ruling
    # line stays body text — the same graceful "feature hides" degradation
    # as the sibling modules above
    _VERDICT_TERM_RE = _QUAL_CONFLICT_RE = re.compile(r"(?!x)x")
try:
    from scope_routes import MARK_OUT_OF_SCOPE as _MARK_OOS, MARK_MISSING as _MARK_MISS
except Exception:
    # same fail-closed degradation: with no markers to match, a refusal falls
    # back to the original neutral "לא נמצא במאגר" chip
    _MARK_OOS = _MARK_MISS = "\x00"
# Deterministic, order-cited lookup tools (no LLM, no quota). Defensive
# imports like the sibling modules above: a stale cached cloud build pairing
# a new app.py with an older tree just hides the tool's button.
try:
    import punishment_authority as _pa
except Exception:
    _pa = None
try:
    import entitlements
except Exception:
    entitlements = None
try:
    import miluim_benefits as _mb
except Exception:
    _mb = None
try:
    import miluim_guide as _mg
except Exception:
    _mg = None
# commander kit (2026-08-06 spec) — same defensive contract
try:
    import keva_benefits as _kb
except Exception:
    _kb = None
try:
    import absence_guide as _ab
except Exception:
    _ab = None
try:
    import distress_guide as _dg
except Exception:
    _dg = None
try:
    import incident_guide as _ig
except Exception:
    _ig = None
# soldier kit (2026-08-06 spec) — the conscript map replaces the entitlements
# calculator in the drawer; entitlements.py stays as its verified data source
try:
    import conscript_map as _cm
except Exception:
    _cm = None
try:
    import soldier_distress as _sd
except Exception:
    _sd = None
# curation of the greeting-screen chips (2026-08-08 sweep). Optional like the
# tools above: a missing module must degrade to the raw ingestion pool, never
# to a boot failure.
try:
    import question_bank
except Exception:
    class _NoBank:
        @staticmethod
        def curate(qs):
            return qs
    question_bank = _NoBank()

try:
    import backend
    from backend import stream_ai_answer, get_loaded_docs_info, get_pdf_bytes, ensure_pdfs_ingested, get_suggested_questions, warm_index
except Exception:
    st.set_page_config(page_title="CommandAI - Error", layout="wide")
    st.error("שגיאה בטעינת המערכת (import של backend נכשל):")
    st.code(traceback.format_exc())
    st.stop()

@st.cache_resource(show_spinner=False)
def _startup_ingest():
    ensure_pdfs_ingested()
    # build the vector index (model download + embedding) at boot, so the
    # first user question doesn't stall behind it
    warm_index()


@st.cache_resource(show_spinner=False)
def _start_media_reaper() -> bool:
    """Reap media-manager entries of dead sessions, every 5 minutes.

    Streamlit 1.58 parks a dropped WebSocket's session in a 120s TTL cache
    WITHOUT calling session.shutdown() when the TTL evicts it — and shutdown
    is the only path that releases the session's media registrations. Every
    iPhone PWA visit ends by backgrounding (never a clean close), so each
    visit that opened the orders accordion pinned its ~52MB of PDF bytes in
    RAM forever (2026-07-27 OOM audit). The reaper does what the missing
    shutdown would have: drop media refs of sessions the runtime no longer
    knows, then purge orphans. Private-API use is deliberate and fully
    guarded — on any AttributeError the reaper silently stops costing us
    nothing, and the leak returns to being bounded by machine restarts."""
    import threading

    def _reap():
        while True:
            time.sleep(300)
            try:
                from streamlit.runtime import get_instance
                rt = get_instance()
                mgr = rt.media_file_mgr
                live = {s.session.id for s in rt._session_mgr.list_sessions()}
                dead = [sid for sid in list(mgr._files_by_session_and_coord)
                        if sid not in live]
                for sid in dead:
                    mgr.clear_session_refs(sid)
                if dead:
                    mgr.remove_orphaned_files()
            except Exception:
                pass

    threading.Thread(target=_reap, daemon=True, name="cai-media-reaper").start()
    return True


@st.cache_resource(show_spinner=False)
def _patch_boot_shell() -> bool:
    """Brand Streamlit's static index.html with the instant olive splash.

    Thin runtime wrapper over boot_shell.patch_index_html (the single source
    of truth, shared with the Docker build that bakes the same patch into the
    image). Runs once per process; self-heals the file on the first session
    if a dependency reinstall reset it.

    No-op in practice on Streamlit Community Cloud (the platform serves its
    own index.html snapshot); it bites only where we own the served file —
    local dev and the self-hosted container. See boot_shell.py for detail.
    """
    return patch_index_html()

# PDF bytes for the ANSWER's source card, which still goes through the media
# manager and so must re-register (and therefore re-read) its one PDF on every
# rerun. The orders LIST used to come through here too — 80 files / 52MB per
# pass — until it moved to static links; see _pdf_static_url for the numbers.
# cache_RESOURCE, not cache_data: bytes are immutable so sharing the object is
# safe, while cache_data kept a pickled copy AND unpickled a fresh set on every
# rerun — three corpus copies + per-rerun churn on the 1024MB machine
# (2026-07-27 OOM audit). ttl bounds staleness: the process outlives
# deploys/git pulls, and a cache keyed only by filename would serve an order's
# OLD bytes forever after its PDF is updated in place.
_pdf_bytes_cached = st.cache_resource(show_spinner=False, ttl=3600)(get_pdf_bytes)

# The ORDERS LIST no longer goes through the media manager at all — it links
# the same PDFs as plain static assets, which is what makes drawing 80 rows
# free (see _pdf_static_url). Mirroring is idempotent and already baked into
# the image by the Dockerfile, so this is ~80 stat() calls at import; it is
# kept at runtime anyway so a locally added order shows up without a rebuild.
_STATIC_PDFS = pdf_static.sync()

st.set_page_config(
    page_title="CommandAI",
    page_icon="🛡️",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# True when the served index.html carries our boot shell — i.e. the shell IS
# the loading screen and the app must not draw a second one (see splash_active).
_shell_ok = _patch_boot_shell()

# ── Device-profile probe component (declared here, rendered after the boot
# splash). Community Cloud's edge strips custom cookies from the WebSocket
# handshake, so st.context.cookies below stays empty THERE (verified live:
# cookie present in document.cookie, absent server-side) — this tiny
# bidirectional component reads localStorage/cookie on the client and hands
# the payload back through the component channel instead. ──
_profile_probe = components.declare_component(
    "cai_profile_probe",
    path=str(Path(__file__).parent / "components" / "profile_probe"),
)

# ── Device profile cookie (cai_profile) — the app's only cross-visit memory.
# Written by a tiny JS component every run (see the sync block); read here
# from the WebSocket handshake via st.context.cookies where the platform
# passes it (local / self-hosted Fly), so a returning visitor lands straight
# in the chat ("היי שלו") instead of the role picker.
# Display-only data: the name never reaches the Anthropic API or the server
# logs. Seeded ONCE per session ("role" key absent) — switch-role/logout set
# role=None in-session and must not be re-overridden by the stale handshake
# cookie on the next rerun. ──
_ck = {}
try:
    _raw = st.context.cookies.get("cai_profile")
    if _raw:
        _ck = json.loads(urllib.parse.unquote(_raw))
    if not isinstance(_ck, dict):
        _ck = {}
except Exception:
    _ck = {}

# ── Session state (initialized before theming, since accent depends on role) ──
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None
if "role" not in st.session_state:
    st.session_state.role = (
        _ck.get("role") if _ck.get("role") in ("soldier", "commander", "reserve") else None
    )
if "conversation_history" not in st.session_state:
    st.session_state.conversation_history = []
if "session_id" not in st.session_state:
    # anonymous per-tab id — keys the daily usage quota and the metrics log
    st.session_state.session_id = metrics.new_session_id()
# Anonymous per-DEVICE id, restored from the profile cookie. LOG-ONLY: the
# quota still keys on session_id (metrics.reserve is untouched), so this
# changes no user-visible behaviour. It exists because session_id is per-tab —
# a person who reopens the app looks like a second person in the log, which
# hides the two numbers the pilot has to produce: return-visits, and how many
# questions ONE person asks per day.
if "device_id" not in st.session_state:
    _did = _ck.get("did")
    # cookie values are attacker-writable and this one lands in a log and a
    # spreadsheet — accept only the exact shape new_session_id() emits
    st.session_state.device_id = (
        _did if isinstance(_did, str) and len(_did) == 12
        and all(c in "0123456789abcdef" for c in _did)
        else metrics.new_session_id()
    )

# ── Profile & settings (session-only; no DB). These MIRROR the settings-dialog
# widgets: Streamlit drops a widget's session key on any run where the widget
# isn't rendered (dialog closed), so a stable mirror is what handle_question
# reads — exactly the pattern profile_saved uses for the status pills. Service
# type/track are folded into the answer ONLY after an explicit "save"
# (profile_customized), so an untouched user's API turn stays byte-identical to
# the pre-profile format (backend._compose_user_content). ──
st.session_state.setdefault("profile_name", str(_ck.get("name") or "")[:40])
# name_asked: the one-time name prompt (gate) was answered or skipped — never
# nag again on this device, on any later role switch
st.session_state.setdefault("name_asked", bool(_ck.get("asked")))
# role_picked_here: the role was chosen by a TAP in THIS session, not restored
# from the device cookie. The name gate is a first-run prompt that belongs after
# that tap — a remembered device (role in the cookie, name never answered) used
# to open the gate over the entry screen on the very first paint, before the
# user touched anything (2026-07-27 video, t=13). Trade-off, deliberate: a
# refresh in the middle of the gate now lands in the chat instead of back in the
# gate. The name is optional and settable in הגדרות, so a dropped prompt costs
# less than a gate that appears unprompted on every launch.
st.session_state.setdefault("role_picked_here", False)
# ── text scale ── The app pins maximum-scale=1 and preventDefaults the iOS
# gesture events (a deliberate, documented fix for the focus auto-zoom bug),
# and it sets -webkit-text-size-adjust:100% with every size in fixed px. The
# three together leave a low-vision user with NO way to enlarge anything:
# not pinch, not iOS Dynamic Type, not browser zoom. This is the replacement
# path — an app-owned multiplier applied to the READING surfaces only, so the
# fixed iOS geometry (header band, composer, drawer) is untouched.
_CK_FS = {"s": 1.0, "m": 1.15, "l": 1.3}
st.session_state.setdefault(
    "text_scale", _CK_FS.get(str(_ck.get("fs") or ""), 1.0))
st.session_state.setdefault("service_type", "סדיר")
st.session_state.setdefault("service_track", "")
st.session_state.setdefault("profile_customized", False)
# ── miluim profile (the "מה מגיע לי במילואים" inputs) — same mirror pattern.
# Seeded from the device cookie's "mil" slot; committed by the tool's form.
# mil_salary is used ONLY for the local tagmul estimate — it must never join
# the chat profile (see handle_question) or any Anthropic call.
_mil_ck = _ck.get("mil") if isinstance(_ck.get("mil"), dict) else {}


def _mil_int(v, cap):
    """Cookie values are attacker-writable device state — coerce or drop."""
    return max(0, min(int(v), cap)) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


st.session_state.setdefault("mil_days_year", _mil_int(_mil_ck.get("dy"), 400))
st.session_state.setdefault("mil_days_3y", _mil_int(_mil_ck.get("d3"), 1000))
st.session_state.setdefault("mil_emp", [e for e in (_mil_ck.get("emp") or [])
                                        if e in ("employee", "self_employed", "student")]
                            if isinstance(_mil_ck.get("emp"), list) else [])
st.session_state.setdefault("mil_salary", _mil_int(_mil_ck.get("sal"), 200000))
# saved only counts if both day-counts survived coercion — a half profile
# would render a map of zeros
st.session_state.setdefault("mil_saved", bool(_mil_ck.get("sv"))
                            and _mil_int(_mil_ck.get("dy"), 400) is not None
                            and _mil_int(_mil_ck.get("d3"), 1000) is not None)
# ── conscript profile ("מה מגיע לי בשירות חובה") — cookie slot "sol".
# Unlike the miluim map this profile gates NOTHING: the map renders in full
# with an empty profile, and these values only sharpen the sub-lines (soldier
# kit spec §4, "הקלט מחדד, לא פותח"). Dates ride the cookie as ISO strings.
_sol_ck = _ck.get("sol") if isinstance(_ck.get("sol"), dict) else {}


def _sol_date(v):
    """Cookie values are attacker-writable device state — parse or drop."""
    if not isinstance(v, str):
        return None
    try:
        return _dt.date.fromisoformat(v[:10])
    except ValueError:
        return None


st.session_state.setdefault("sol_enlist", _sol_date(_sol_ck.get("en")))
st.session_state.setdefault("sol_discharge", _sol_date(_sol_ck.get("di")))
st.session_state.setdefault("sol_track", _sol_ck.get("tr")
                            if _sol_ck.get("tr") in ("lohem", "tomekh", "oref") else None)
st.session_state.setdefault("sol_single", bool(_sol_ck.get("sg")))
st.session_state.setdefault("sol_married", bool(_sol_ck.get("mr")))
# "saved" only means the user touched the form — the map never depends on it
st.session_state.setdefault("sol_saved", bool(_sol_ck.get("sv")))
st.session_state.setdefault("share_analytics", True)
st.session_state.setdefault("show_settings", False)
st.session_state.setdefault("settings_screen", "hub")

# ── Boot splash — the very FIRST delta the browser receives ──
# Rendered before _startup_ingest() so the branded curtain (logo on the
# splash olive) covers the ENTIRE wait — cold-boot ingestion / model
# download and the heavy CSS build below — instead of a blank themed page
# (on a phone that blank stretch is most of what the user sees).
# Self-contained on purpose: own font import and boot* keyframes. The 30s
# fallback lift guarantees a mid-script exception can never leave the
# curtain stuck; the main CSS block re-arms the lift under a DIFFERENT
# animation name (curtainUp), which restarts the clock — so the curtain
# holds until the entry screen has actually rendered, then lifts after the
# standard 1.15s choreography.
_is_admin = st.query_params.get("admin") == "1"
# NOT gated on `role is None` any more (2026-07-27 video). A device that
# remembers its role got no splash at all — so the ~11s boot was covered by
# nothing, and the user watched Streamlit paint itself raw: unstyled entry
# text, then a red "Missing Submit Button" frame (a transient of progressive
# rendering — both forms do have submit buttons), then the styled app. It also
# made the entrance animation look "skipped", because it never ran. A returning
# visitor waits exactly as long as a new one and needs the cover more, not less;
# splash_shown alone is the right guard — once per session, not once per role.
#
# AND NOT when the boot shell is in place (2026-07-27 13:02 video). The shell in
# index.html and this splash draw almost the same picture, so the boot ran three
# near-identical olive screens in a row — OS launch image, shell, then this — and
# every hand-off showed: at t=2.8 the wordmark ghosted through the crossfade, at
# t=3.5 the subtitle blinked out and back as the shell faded while this one lit
# up. That IS the "it keeps switching screens" the user reported. So there is now
# exactly ONE loading screen: the shell, which holds until a real screen exists
# and then lifts as a curtain. This block survives only as the fallback for a
# host where the patch cannot be applied (Community Cloud serves its own HTML).
splash_active = (not _is_admin
                 and not st.session_state.get("splash_shown")
                 and not _shell_ok)
if not _is_admin:
    st.session_state.splash_shown = True
if splash_active:
    st.markdown("""
<style>
/* No @import here any more: boot_shell inlines Suez One as a data: URI in the
   served index.html, so the family is already defined by the time this block
   renders. A remote @import would be one more blocking fetch on the boot path
   — the very thing that was costing ~8s (see boot_shell._font_data_uri). The
   main CSS block still imports it as a fallback for hosts where the shell
   patch cannot be applied. */
/* the parked curtain must END invisible: it stays in the DOM above the
   viewport, and iOS Safari (no theme-color meta yet) SAMPLES it when tinting
   its chrome — an olive ghost kept the bars olive after the lift */
/* -202%, not -101%: the element IS the layout viewport, so -101% put the
   moment it clears the glass at 99% of the travel — inside the ease-out tail
   of cubic-bezier(.7,0,.3,1), which meant it crawled the last stretch at 6%
   of its peak speed. Twice the distance puts the crossing just under half the
   travel, where the curtain is still accelerating, and the duration grows to
   match so the visible sweep keeps its tempo. Same fix as the live curtain in
   boot_shell.lift(); this block only runs on hosts where the shell patch does
   not apply. */
@keyframes bootCurtainUp { 0% { transform:translateY(0); } 99% { opacity:1; } 100% { transform:translateY(-202%); opacity:0; visibility:hidden; } }
.cai-splash {
    /* dark chain (2026-08-03): splash matches the app backdrop — the sage
       splash read as a second, brighter screen on device */
    position: fixed; inset: 0; background: #14170E; z-index: 999990;
    display: flex; flex-direction: column; align-items: center; justify-content: flex-start; gap: 18px;
    /* top-anchor the logo where the entry screen lands it (~26% down) so the
       curtain lift reveals the same layout instead of the logo jumping up
       from dead-center. --cai-sat pushes it clear of the iOS notch. */
    padding-top: calc(var(--cai-sat, 0px) + 14vh);
    animation: bootCurtainUp 1.05s cubic-bezier(.7,0,.3,1) both; animation-delay: 30s;
    pointer-events: none;
}
/* NO entrance animation on ANY of the three: the OS launch image now paints
   the chevron, the wordmark AND the subtitle at these exact spots (see
   _startup_png) — animating them here reads as the logo "popping" during the
   image→splash handoff. The subtitle used to be the one element that entered,
   on the theory that the launch image lacked it; now that the image has it,
   an entrance would make it blink out and back. Typography mirrors
   boot_shell._HEAD_TEMPLATE exactly, for the same reason. */
.cai-splash-chev { display:flex; flex-direction:column; align-items:center; }
.cai-splash-chev span { display:block; width:26px; height:26px;
    border-top:6px solid #A3AE6E; border-left:6px solid #A3AE6E; transform:rotate(45deg); }
.cai-splash-chev span + span { border-color: rgba(163,174,110,.45); margin-top: -9px; }
.cai-splash-title { font: 400 34px 'Suez One', serif; color: #ECEDE6; }
.cai-splash-title b { color: #A3AE6E; font-weight: 400; }
/* layout D — mirrors #cai-boot-splash .s/.s1/.s2 in boot_shell exactly */
.cai-splash-sub { display:flex; flex-direction:column; align-items:center; gap:6px; }
.cai-splash-sub .s1 { display:flex; align-items:center; gap:12px;
    font: 400 13px 'Suez One', serif; letter-spacing: 2px; color: rgba(236,237,230,.59); }
.cai-splash-sub .s1::before, .cai-splash-sub .s1::after {
    content:''; width:26px; height:1px; background: rgba(236,237,230,.31); }
.cai-splash-sub .s2 { font: 400 9.5px 'Suez One', serif; letter-spacing: 7px; color: rgba(236,237,230,.37); }
/* Waiting ring, bottom-anchored (margin-top:auto against the flex-start
   column). Measured on the live app 2026-07-27: domComplete 8.6s and the last
   Streamlit chunk at 11.4s — the wait is latency-bound waves of tiny lazy
   chunks, so it cannot be engineered away from here. Past ~2.5s a motionless
   splash reads as a hang; the ring only says "still working" and is deliberately
   quiet — it fades in late so a fast load never shows it at all. */
@keyframes bootSpin { to { transform: rotate(360deg); } }
@keyframes bootFadeIn { from { opacity: 0; } to { opacity: 1; } }
.cai-splash-wait {
    width: 22px; height: 22px; margin: auto auto 14vh;
    border: 2px solid rgba(236,237,230,.20);
    border-top-color: rgba(236,237,230,.55);
    border-radius: 50%;
    animation: bootSpin .9s linear infinite, bootFadeIn .5s ease both;
    animation-delay: 0s, 2.5s;
}
</style>
<div class='cai-splash'>
<div class='cai-splash-chev'><span></span><span></span></div>
<div class='cai-splash-title'>Command<b>AI</b></div>
<div class='cai-splash-sub'><span class='s1'>מערכת פקודות</span><span class='s2'>בלמ"ס</span></div>
<div class='cai-splash-wait'></div>
</div>""", unsafe_allow_html=True)

# In-browser Safari tints its top/bottom chrome from <meta name="theme-color">;
# Streamlit never writes that meta, so until OUR injection lands Safari falls
# back to sampling the page — the olive boot splash — and the olive bars then
# outlive the curtain by however long the boot work below takes (seen live:
# olive bars over the dark entry screen). Pin the meta in a tiny self-contained
# frame BEFORE the heavy startup, so the bars are correct by first paint. The
# full PWA injection further down re-asserts it on every run.
components.html(
    r"""<script>try{
    var d = window.top.document,
        m = d.querySelector('meta[name="theme-color"]');
    if (!m) { m = d.createElement("meta"); m.setAttribute("name", "theme-color"); d.head.appendChild(m); }
    m.setAttribute("content", "#14170E");
    // iOS zoom lockdown (user request: no pinch-zoom at all). Three vectors:
    // (1) focus auto-zoom on <16px inputs — killed by maximum-scale=1 (iOS
    //     honors it for the AUTO zoom even where it ignores it for manual
    //     pinch); this was the "page suddenly enlarged after login" bug;
    // (2) manual pinch — preventDefault on the iOS-only gesture events below
    //     (works in Safari AND standalone);
    // (3) double-tap zoom — touch-action:manipulation in the app CSS.
    // Deliberately NOT user-scalable=no: standalone/home-screen web views
    // honor it and it regressed the standalone viewport height (fixed-bottom
    // composer/disclaimer pushed ~50px below the screen — the old stuck-
    // large-viewport symptom); it adds nothing the guards don't already do.
    var vp = d.querySelector('meta[name="viewport"]');
    if (vp) {
        var vc = vp.getAttribute("content") || "";
        if (!/maximum-scale/.test(vc))
            vp.setAttribute("content", vc + ", maximum-scale=1");
        // strip the harmful cap from clients that loaded the previous build
        if (/user-scalable=no/.test(vp.getAttribute("content")))
            vp.setAttribute("content",
                vp.getAttribute("content").replace(/,?\s*user-scalable=no/, ""));
    }
    if (!window.top.__caiNoZoom) {
        window.top.__caiNoZoom = true;
        ["gesturestart", "gesturechange"].forEach(function (t) {
            d.addEventListener(t, function (e) { e.preventDefault(); }, { passive: false });
        });
    }
    // Home-screen (standalone) cold launches render the in-flow content
    // ~56-59px (one status bar) below where it belongs until the first
    // native re-layout (keyboard open/close); the composer strip's tail
    // falls off the glass. ROUND-5 EVIDENCE (video badge, on-device): the
    // declared detection DOES pass (sa=111), the pin DOES apply (app=852),
    // and vv/innerHeight/screen all read 852 in BOTH the broken and the
    // settled state — no global metric moves at the fix moment; only the
    // fixed-position elements sit still while in-flow content shifts by
    // exactly one status bar. So round 6 stops trusting global metrics:
    // (a) an EMPIRICAL corrective — measure where stBottom's own rect ends
    //     vs the pinned height and pull the column up by the excess
    //     (--cai-vvoff, fixpoint-stable because the applied offset is added
    //     back before comparing);
    // (b) synthetic re-layout kicks at boot (scroll nudge + a one-frame
    //     viewport-meta perturbation) — the keyboard fixes the geometry by
    //     forcing exactly such a native re-layout;
    // (c) ALL timers live on window.top — round-5's second badge sample
    //     died silently because component-iframe timers are killed on every
    //     Streamlit rerun (the iframe is replaced);
    // (d) badge v2 adds the state discriminators the first badge lacked:
    //     env(safe-area-inset-top), dvh, scrollY, stApp/stBottom rects,
    //     visualViewport.offsetTop.
    // Measurements never run while an input is focused — the iOS keyboard
    // shrinks visualViewport and would squash the app.
    // ENGINE INJECTION: everything below must run in the TOP page's realm.
    // Scheduling from this component is worthless — even timers registered
    // via window.top.setTimeout are cancelled when Streamlit replaces this
    // component's iframe on the next rerun (the callback's realm dies with
    // the iframe: the ?caidbg badge never painted locally, and on-device
    // only the 3s sample ever fired before a rerun). So the component
    // SERIALIZES the engine (Function.toString) into a real <script> on the
    // top document — idempotent by element id, so reruns are no-ops and the
    // engine's timers/listeners live as long as the page itself. In the PWA
    // (start_url=/~/+/) and locally the top page IS the app page, which is
    // the only context where the standalone pin can arm; under the cloud
    // shell the engine idles harmlessly on the shell document.
    var engineFn = function () {
        var aroot = document.documentElement;
        // the navigator.standalone PROPERTY exists only on iOS WebKit
        var ios = navigator && ("standalone" in navigator);
        var glassH = function () {
            var sw = screen && screen.width, sh = screen && screen.height;
            if (!sw || !sh) return 0;
            var land = matchMedia && matchMedia("(orientation: landscape)").matches;
            return land ? Math.min(sw, sh) : Math.max(sw, sh);
        };
        var vvNow = function () { return window.visualViewport ? window.visualViewport.height : window.innerHeight; };
        var px = function (css) { // resolve a CSS length, in px
            var p = document.createElement("div");
            p.style.cssText = "position:fixed;top:0;left:0;width:0;visibility:hidden;" +
                "pointer-events:none;height:" + css + ";";
            document.body.appendChild(p);
            var v = p.getBoundingClientRect().height;
            p.remove();
            return v;
        };
        if ((navigator.standalone === true) ||
            (matchMedia && matchMedia("(display-mode: standalone)").matches)) window.__caiSA = true;
        // CONFIRM GATE (2026-07-26 video). Every measurement used to reach the
        // DOM immediately. On a cold standalone launch iOS reports the geometry
        // differently from one sample to the next while it settles, so the ~15
        // boot samples each stamped a DIFFERENT --cai-vvh/--cai-vvoff and
        // relaid out the whole column: the role picker jumps vertically for
        // ~7s after it first paints, and one of those frames is the one that
        // sits under the status bar. Proof it is the stamping and not the
        // safe-area: env(safe-area-inset-top) is static and cannot oscillate.
        // Confirmed on the live desktop page (healthy geometry): 6 restamps in
        // 9s, all 812px, one real change — so the repeats are harmless and it
        // is the VALUES that differ on device. The noise is iOS's; trusting
        // every single sample was ours. A candidate must now be repeated by the
        // next sample before it is applied, so transients never reach the DOM
        // and the layout moves once — when it has converged.
        var TOL = 2; // px: sub-pixel viewport jitter is not a real change
        var applyPin = function (h, off) {
            var hs = Math.round(h) + "px", os = off + "px";
            // write only on a real change — never rely on the engine to dedup
            // an identical restamp into a no-op
            if (aroot.style.getPropertyValue("--cai-vvh") !== hs)
                aroot.style.setProperty("--cai-vvh", hs);
            if (aroot.style.getPropertyValue("--cai-vvoff") !== os)
                aroot.style.setProperty("--cai-vvoff", os);
        };
        var setH = function () {
            try {
                var g = glassH();
                if (!window.__caiSA && ios && g >= 400 && Math.min(vvNow(), window.innerHeight) - g >= 12)
                    window.__caiSA = true; // symptom gate: taller-than-glass == ghost state
                if (!window.__caiSA) return;
                aroot.classList.add("cai-standalone");
                var ae = document.activeElement;
                // skip while typing ONLY if the pane is actually shrunken —
                // retained focus with the keyboard already closed must not
                // block a resync (2026-07-17 video: composer stuck mid-screen
                // after send, answer flowing below it)
                if (ae && /^(INPUT|TEXTAREA)$/.test(ae.tagName) && vvNow() < g * 0.95) return;
                var h = vvNow();
                // keyboard hard-guard: never pin --cai-vvh to a keyboard-
                // shrunken pane, even when focus tracking failed (Streamlit
                // replaces the focused textarea without a focusout)
                if (g >= 400 && h < g * 0.8) return;
                // GLASS-SHORTFALL GUARD (2026-07-28 device video). The app
                // came up with the composer strip and the disclaimer pinned
                // ~100px above the bottom of the screen, and it stayed that
                // way for the whole session. In standalone the web view IS
                // the glass, so with no keyboard up the visible pane can
                // only be the full screen height; anything meaningfully
                // shorter is iOS reporting a layout viewport that got stuck
                // during a slow launch. The keyboard guard above is no help
                // — it only catches a >20% shortfall, and this was ~12%.
                // Worse, the old code PINNED that reading and then could
                // never recover: the strip sits exactly ON the wrong value,
                // so heal()'s gap check finds nothing to disagree with. So:
                // force a real re-layout instead, and only accept a short
                // pane once it has survived the kicks (some other device
                // geometry might legitimately read short).
                if (g >= 400 && h < g - 24) {
                    window.__caiShort = (window.__caiShort || 0) + 1;
                    if (window.__caiShort <= 8) { kick(); return; }
                } else { window.__caiShort = 0; }
                if (g >= 400) h = Math.min(h, g); // ghost-viewport clamp (see above)
                if (h < 400) return;
                // empirical overflow corrective: where does the composer strip
                // REALLY end? Add back the already-applied offset so the
                // comparison sees the uncorrected position (fixpoint-stable).
                var sb = document.querySelector('[data-testid="stBottom"]');
                var cur = parseFloat(aroot.style.getPropertyValue("--cai-vvoff")) || 0;
                var off = 0;
                if (sb) {
                    var ex = Math.round(sb.getBoundingClientRect().bottom + cur - h);
                    if (ex >= 12 && ex <= 120) off = ex;
                }
                // hold the candidate; apply only once the NEXT sample agrees.
                // `cur` above is deliberately the APPLIED offset, not the
                // pending one — the fixpoint correction has to add back what
                // the strip is actually positioned by.
                var prev = window.__caiCand;
                window.__caiCand = { h: h, off: off };
                if (!prev || Math.abs(prev.h - h) > TOL || prev.off !== off) return;
                applyPin(h, off);
            } catch (e) {}
        };
        // synthetic re-layout kick — the keyboard cycle fixes the native
        // geometry by forcing a UIKit re-layout; imitate it cheaply at boot
        var nudge = function () {
            try {
                if (!window.__caiSA) return;
                window.scrollTo(0, 1); window.scrollTo(0, 0);
                var vp = document.querySelector('meta[name="viewport"]');
                if (vp && !window.__caiNudged) {
                    window.__caiNudged = true;
                    var c = vp.getAttribute("content") || "";
                    vp.setAttribute("content", c + ", minimum-scale=1");
                    setTimeout(function () { try { vp.setAttribute("content", c); } catch (e) {} }, 120);
                }
            } catch (e) {}
        };
        // kick = forced UIKit re-layout, callable repeatedly (unlike the
        // once-gated boot nudge): after the keyboard closes, iOS sometimes
        // leaves the LAYOUT viewport at the shrunken keyboard height while
        // the visual viewport is already full — every bottom-anchored fixed
        // element (the composer strip) then sits mid-screen with the streamed
        // answer flowing below it, and only a re-layout snaps it back
        // (2026-07-21 phone check: header stayed put = not a scroll offset).
        // Re-stamping the viewport meta makes iOS re-evaluate the viewport.
        var kick = function () {
            try {
                if (!window.__caiSA) return;
                var now = Date.now();
                if (window.__caiKickAt && now - window.__caiKickAt < 900) return;
                window.__caiKickAt = now;
                window.scrollTo(0, 1); window.scrollTo(0, 0);
                var vp = document.querySelector('meta[name="viewport"]');
                if (vp) {
                    var c = vp.getAttribute("content") || "";
                    vp.setAttribute("content", c + ", minimum-scale=1");
                    setTimeout(function () { try { vp.setAttribute("content", c); } catch (e) {} }, 60);
                }
            } catch (e) {}
        };
        // heal = measurement + keyboard-debris repair. With the keyboard
        // closed: zero any residual window/visual-viewport offset (the app
        // never scrolls the BODY legitimately), and if the composer strip's
        // bottom sits far above the visual bottom — the stuck-layout-viewport
        // signature — kick a re-layout instead of waiting for the answer
        // stream to finish.
        var heal = function () {
            setH();
            try {
                if (!window.__caiSA) return;
                var ae = document.activeElement;
                if (ae && /^(INPUT|TEXTAREA)$/.test(ae.tagName) && vvNow() < glassH() * 0.95) return;
                var vv = window.visualViewport;
                if ((window.scrollY || 0) > 2 || (vv && vv.offsetTop > 2)) window.scrollTo(0, 0);
                // outer containers must never hold a scroll offset — the
                // streaming scroll-to-bottom sometimes lands on them (the
                // 2026-07-21 traveling-composer report). The chat section
                // (.stMain / stAppScrollToBottomContainer) is NOT touched:
                // that's the one legitimate scroller.
                [document.documentElement, document.body,
                 document.querySelector('.stApp'),
                 document.querySelector('[data-testid="stAppViewContainer"]')
                ].forEach(function (el) {
                    if (el && el.scrollTop > 0) el.scrollTop = 0;
                });
                var sb = document.querySelector('[data-testid="stBottom"]');
                if (sb) {
                    var r = sb.getBoundingClientRect();
                    // the strip's own height feeds the chat section's
                    // padding-bottom (--cai-sbh), so the fixed overlay never
                    // hides the last message
                    if (r.height >= 60 && r.height <= 260)
                        aroot.style.setProperty("--cai-sbh", Math.round(r.height) + "px");
                    var gap = vvNow() - r.bottom;
                    if (gap > 60) kick();
                }
            } catch (e) {}
        };
        // keyboard glue (.cai-kb): while the composer textarea is focused
        // and the pane is keyboard-shrunken, the strip must ride the LIVE
        // keyboard top — iOS otherwise pans the whole layout viewport to
        // reveal the caret (chips under the clock, strip mid-air, canvas
        // band above the keyboard: the 21.7 phone report). --cai-kbb is the
        // keyboard top in layout coords (vv.offsetTop + vv.height); with
        // the strip self-glued, any reveal-pan is debris and gets zeroed.
        var kbSync = function () {
            try {
                if (!window.__caiSA) return;
                var vv = window.visualViewport;
                if (!vv) return;
                var ae = document.activeElement;
                var inComposer = !!(ae && /^(INPUT|TEXTAREA)$/.test(ae.tagName) &&
                    ae.closest && ae.closest('[data-testid="stBottom"]'));
                var g = glassH();
                if (inComposer && g >= 400 && vv.height < g * 0.8) {
                    aroot.classList.add("cai-kb");
                    aroot.style.setProperty("--cai-kbb",
                        Math.round(vv.offsetTop + vv.height) + "px");
                    if ((window.scrollY || 0) > 1) window.scrollTo(0, 0);
                    [document.documentElement, document.body,
                     document.querySelector('.stApp'),
                     document.querySelector('[data-testid="stAppViewContainer"]')
                    ].forEach(function (el) {
                        if (el && el.scrollTop > 0) el.scrollTop = 0;
                    });
                } else if (aroot.classList.contains("cai-kb")) {
                    aroot.classList.remove("cai-kb");
                    aroot.style.removeProperty("--cai-kbb");
                }
            } catch (e) {}
        };
        // the open/close animation reports geometry sparsely (often a single
        // resize at the end) — ride through it frame-by-frame
        var kbBurst = function () {
            var t0 = Date.now();
            var step = function () {
                kbSync();
                if (Date.now() - t0 < 900) requestAnimationFrame(step);
            };
            requestAnimationFrame(step);
        };
        if (!window.__caiVVH) {
            window.__caiVVH = true;
            [0, 300, 700, 1300, 2200, 3500, 5200, 7500].forEach(function (ms) { setTimeout(setH, ms); });
            setTimeout(nudge, 350);
            setTimeout(nudge, 1500);
            var iv = setInterval(heal, 600);
            setTimeout(function () {
                clearInterval(iv);
                // permanent slow resync: a mid-session stuck state (--cai-vvh
                // pinned to a keyboard pane after focus tracking missed the
                // close, or keyboard scroll residue) must heal even when no
                // viewport event ever fires again
                setInterval(heal, 1500);
            }, 30000);
            // a LONE setH can never clear the confirm gate, so the single-shot
            // triggers fire a short burst instead — a genuine change still
            // lands within ~260ms. `resize` needs none: it arrives in streaks
            // and self-confirms.
            var resample = function () { setH(); setTimeout(setH, 120); setTimeout(setH, 260); };
            window.addEventListener("orientationchange", function () { setTimeout(resample, 400); });
            window.addEventListener("resize", setH);
            window.addEventListener("pageshow", resample);
            // the send/✓-dismiss taps blur the composer — remeasure AND clear
            // the keyboard's scroll residue right after, so the composer is
            // back at the bottom for the whole streamed answer
            window.addEventListener("focusin", kbBurst, true);
            window.addEventListener("focusout", function () {
                kbBurst();
                [80, 350, 800].forEach(function (ms) { setTimeout(heal, ms); });
            }, true);
            if (window.visualViewport) {
                window.visualViewport.addEventListener("resize", function () {
                    setH(); kbSync();
                });
                // the keyboard pan fires vv-scroll; once focus is gone the
                // trailing offset is debris — heal shortly after it settles
                window.visualViewport.addEventListener("scroll", function () {
                    kbSync();
                    clearTimeout(window.__caiHealT);
                    window.__caiHealT = setTimeout(heal, 250);
                });
            }
            // NOTE: connection loss is NOT handled here. #cai-net-bar in
            // boot_shell.py owns it — it wraps window.WebSocket before
            // Streamlit's bundle loads, so it sees the real socket, and it
            // lives outside #root so no rerun can take it away. A second
            // banner in this engine was added and removed on 2026-08-10:
            // it duplicated the bar's message and could stack with it.
            // ── role-pick navigation veil ── Streamlit tears the entry screen
            // down piecewise on the role tap (header vanishes, cards float
            // ~0.2-0.9s on 3G — the "small stall" the user flagged). The tap
            // instantly raises an opaque cover in the HOME background color,
            // and it lifts once the chat header exists — the swap happens
            // under it, and the tap gets immediate visual feedback.
            var veil = function () {
                try {
                    if (document.getElementById("cai-nav-veil")) return;
                    var v = document.createElement("div");
                    v.id = "cai-nav-veil";
                    v.style.cssText = "position:fixed;inset:0;z-index:999980;" +
                        "background:#14170E;opacity:0;transition:opacity .12s ease;" +
                        "pointer-events:none;";
                    document.body.appendChild(v);
                    requestAnimationFrame(function () { v.style.opacity = "1"; });
                    var t0 = Date.now(), done = false;
                    var lift = function () {
                        if (done) return; done = true;
                        v.style.transition = "opacity .28s ease";
                        v.style.opacity = "0";
                        setTimeout(function () { try { v.remove(); } catch (e) {} }, 320);
                    };
                    var poll = setInterval(function () {
                        if (document.querySelector(".cai-header")) {
                            clearInterval(poll); setTimeout(lift, 120);
                        } else if (Date.now() - t0 > 4000) { clearInterval(poll); lift(); }
                    }, 80);
                } catch (e) {}
            };
            // ── composer keyboard manners (pilot feedback 2026-07-27) ──
            // (a) On touch devices Return inserts a NEWLINE instead of
            //     sending — soldiers write multi-line questions and the
            //     stock chat_input fired the send on every Return. Sending
            //     is the arrow button's job. execCommand keeps the React
            //     controlled-textarea state in sync (fires a real input
            //     event); desktop keeps Enter-to-send.
            // (b) The send tap blurs the composer so the iOS keyboard drops
            //     on its own — the pilot had to tap ✓ to dismiss it before
            //     seeing the answer. Capture-phase, after the click lands.
            var coarse = matchMedia && matchMedia("(pointer: coarse)").matches;
            document.addEventListener("keydown", function (e) {
                try {
                    if (!coarse || e.key !== "Enter" || e.shiftKey) return;
                    var t = e.target;
                    if (!t || !t.matches ||
                        !t.matches('[data-testid="stChatInput"] textarea')) return;
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    document.execCommand("insertText", false, "\n");
                } catch (err) {}
            }, true);
            document.addEventListener("click", function (e) {
                try {
                    if (!e.target || !e.target.closest) return;
                    if (e.target.closest('[data-testid="stChatInputSubmitButton"]')) {
                        var ta = document.querySelector('[data-testid="stChatInput"] textarea');
                        if (ta) setTimeout(function () { try { ta.blur(); } catch (er) {} }, 40);
                    }
                } catch (err) {}
            }, true);
            document.addEventListener("click", function (e) {
                try {
                    if (!e.target || !e.target.closest) return;
                    // name-gate continue/skip always leads to the chat screen
                    // (the only buttons inside the gate card are the two submits;
                    // keyed forms carry no st-key class in 1.58)
                    if (e.target.closest(".st-key-cai_name_card button")) { veil(); return; }
                    // a role tap veils ONLY when it goes straight to the chat;
                    // on a first visit (#cai-gate-pending) it opens the name
                    // gate, which has no .cai-header — the veil would hang
                    // opaque until its 4s timeout
                    if (e.target.closest(
                        ".st-key-role_soldier, .st-key-role_commander, .st-key-role_reserve")
                        && !document.getElementById("cai-gate-pending"))
                        veil();
                } catch (err) {}
            }, true);
        }
        // launch diagnosis v2 — OPT-IN only (?caidbg=1): the unconditional
        // iOS badge did its diagnostic job (rounds 5-6) and the user flagged
        // the black strip itself as a bug once the layout was fixed
        var dbg = function (tag) {
            try {
                var sb = document.querySelector('[data-testid="stBottom"]');
                var app = document.querySelector('.stApp');
                var sbr = sb ? sb.getBoundingClientRect() : null;
                var apr = app ? app.getBoundingClientRect() : null;
                var txt = tag +
                    " sa=" + (navigator.standalone === true ? 1 : 0) +
                    ((matchMedia && matchMedia("(display-mode: standalone)").matches) ? 1 : 0) +
                    (window.__caiSA ? 1 : 0) +
                    " env=" + Math.round(px("env(safe-area-inset-top,0px)")) +
                    " vvh=" + (aroot.style.getPropertyValue("--cai-vvh") || "-") +
                    " off=" + (aroot.style.getPropertyValue("--cai-vvoff") || "-") +
                    " vv=" + Math.round(vvNow()) + " in=" + window.innerHeight +
                    " scr=" + glassH() +
                    " svh=" + Math.round(px("100svh")) + " dvh=" + Math.round(px("100dvh")) +
                    " sY=" + Math.round(window.scrollY || 0) +
                    " aT=" + (apr ? Math.round(apr.top) : -1) +
                    " aB=" + (apr ? Math.round(apr.bottom) : -1) +
                    " sbB=" + (sbr ? Math.round(sbr.bottom) : -1) +
                    " voT=" + (window.visualViewport ? Math.round(window.visualViewport.offsetTop) : -1);
                var b = document.getElementById("cai-dbg");
                if (!b) {
                    b = document.createElement("div");
                    b.id = "cai-dbg";
                    b.style.cssText = "position:fixed;top:calc(env(safe-area-inset-top,0px) + 6px);" +
                        "left:50%;transform:translateX(-50%);z-index:2147483000;background:#000;" +
                        "color:#C4CE92;font:700 8px ui-monospace,monospace;padding:3px 8px;" +
                        "border-radius:8px;pointer-events:none;direction:ltr;max-width:94vw;text-align:center;";
                    document.body.appendChild(b);
                }
                b.textContent = txt;
            } catch (e) {}
        };
        if (!window.__caiDbg && /[?&]caidbg=1/.test(window.location.search || "")) {
            window.__caiDbg = true;
            // Round 7 films the SETTLING, not the settled state: rounds 5-6
            // sampled from 3s, by which time the cold-launch geometry has
            // already healed, so the frames that decide between "vvoff was
            // over-applied" and "env read 0" were never captured. These
            // straddle each setH tick ([0,300,700,1300,2200,3500,5200,7500])
            // and the two nudges (350, 1500).
            [250, 500, 900, 1500, 2400, 3600, 5400, 7800, 11000, 15000, 20000]
                .forEach(function (ms, i) {
                    setTimeout(function () { dbg("d7." + (i + 1)); }, ms);
                });
            setTimeout(function () {
                try { var b0 = document.getElementById("cai-dbg"); if (b0) b0.remove(); } catch (e) {}
            }, 26000);
        }
    };
    var hostDoc = window.top.document;
    if (!hostDoc.getElementById("cai-vvh-engine")) {
        var es = hostDoc.createElement("script");
        es.id = "cai-vvh-engine";
        es.textContent = "(" + engineFn.toString() + ")();";
        (hostDoc.head || hostDoc.documentElement).appendChild(es);
    }
    }catch(e){
        // surfacing catch: a silent death here is exactly what blinded
        // attempts 1-4 — paint the failure (iOS / debug param only)
        try {
            var _dbgq = false;
            try { _dbgq = /[?&]caidbg=1/.test(String(window.top.location.search || "")); } catch (q) {}
            if (_dbgq) {
                var _d = (window.parent || window).document;
                var _b = _d.createElement("div");
                _b.style.cssText = "position:fixed;top:6px;left:50%;transform:translateX(-50%);" +
                    "z-index:2147483000;background:#5a1111;color:#fff;font:700 9px monospace;" +
                    "padding:3px 8px;border-radius:8px;direction:ltr;max-width:94vw;";
                _b.textContent = "caiERR " + String(e && e.message || e).slice(0, 120);
                _d.body.appendChild(_b);
                setTimeout(function () { try { _b.remove(); } catch (x) {} }, 16000);
            }
        } catch (y) {}
    }</script>""",
    height=0,
)

# ── Device-profile probe render — only where the cookie fast-path came up
# empty (Community Cloud, or genuinely new device). Placed BEFORE the heavy
# startup so the client round-trip overlaps ingestion; the value arriving
# triggers one extra rerun, masked by the boot curtain. A live user choice
# always outranks the late-arriving probe: role is only seeded while still
# unset, and an in-session name is never overwritten.
if not st.session_state.get("cai_probe_done") and not _ck:
    _pv = _profile_probe(default=None)
    if _pv is not None:
        st.session_state.cai_probe_done = True
        try:
            _pd = json.loads(urllib.parse.unquote(_pv)) if _pv else {}
        except Exception:
            _pd = {}
        if isinstance(_pd, dict):
            if (st.session_state.role is None
                    and _pd.get("role") in ("soldier", "commander", "reserve")):
                st.session_state.role = _pd["role"]
            if (not (st.session_state.get("profile_name") or "").strip()
                    and _pd.get("name")):
                st.session_state.profile_name = str(_pd["name"])[:40]
            if _pd.get("asked"):
                st.session_state.name_asked = True

_startup_ingest()
_start_media_reaper()


def _secret(name: str, default: str = "") -> str:
    """A secret from the environment first, then secrets.toml.

    The environment wins on purpose. In production every st.secrets value
    arrives inside ONE base64 Fly secret that also carries the Google
    service-account private key, so changing just the admin password meant
    rebuilding and re-uploading that whole blob — an operation whose failure
    mode is silently replacing working credentials with broken ones and taking
    the metrics sheet down with them. A plain

        fly secrets set CAI_ADMIN_PASSWORD=... -a commandai

    now overrides one value without the blob ever being opened.

    Empty env values are ignored rather than honoured: an unset-but-declared
    variable must not be able to blank out a working password.
    """
    env = os.environ.get("CAI_" + name.upper())
    if env:
        return env
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def _render_admin():
    """Hidden ops dashboard — open the app with ?admin=1 (password-gated)."""
    # the theme backgroundColor is the splash olive (it paints the loading
    # skeleton — see config.toml); this page renders before the main CSS
    # block, so force the dark backdrop here
    st.markdown(
        "<style>[data-testid='stAppViewContainer'], [data-testid='stHeader'],"
        " body { background: #171A12 !important; }</style>",
        unsafe_allow_html=True,
    )
    st.title("📊 CommandAI — דשבורד מנהל")
    pw = _secret("admin_password")
    if not pw:
        st.error("כדי להשתמש בדשבורד, הגדר admin_password ב-secrets של האפליקציה.")
        return
    if not st.session_state.get("admin_ok"):
        entered = st.text_input("סיסמת מנהל", type="password")
        if entered and entered == pw:
            st.session_state.admin_ok = True
            st.rerun()
        elif entered:
            st.error("סיסמה שגויה")
        return

    d = metrics.dashboard_data()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("שאלות היום", f"{d['global_count']} / {d['global_limit']}")
    # Two numbers, not one. "טאבים" is what the quota actually keys on;
    # "מכשירים" is the closest thing we have to people. A gap between them is
    # someone who reopened the app — which is also how the per-tab quota gets
    # reset, so the gap is the bypass rate, readable at a glance.
    # .get: Streamlit Cloud can run this app.py against a metrics module still
    # cached from the previous build (same reason _FALLBACK_QUESTIONS exists).
    c2.metric("מכשירים היום", d.get("devices_today", 0))
    c3.metric("טאבים היום", d["sessions_today"])
    recent_cost = sum(q["cost_usd"] for q in d["questions"])
    c4.metric("עלות מצטברת (מאז אתחול)", f"${recent_cost:.2f}")

    # .get, not [..]: a deploy can pair a new metrics.py (which emits the new
    # "configured" state) with an app.py still cached from the previous build,
    # and a KeyError here would take the whole dashboard down.
    sheets_label = {
        "ok": "✅ מחובר — כל שאלה ומשוב נשמרים בגיליון",
        "error": f"⚠️ שגיאת חיבור: {d['sheets_error']}",
        "configured": "🕓 מוגדר, אך טרם נכתבה שורה מאז עליית השרת — החיבור לא נבדק בפועל",
        "not_configured": "❌ לא מוגדר — הנתונים נשמרים רק בזיכרון עד האתחול הבא",
    }.get(d["sheets_status"], f"סטטוס לא מוכר: {d['sheets_status']}")
    st.caption(f"Google Sheets: {sheets_label}")
    if d["sheet_url"]:
        st.markdown(f"🔗 [פתח את הגיליון המלא (כל ההיסטוריה)]({d['sheet_url']})")

    # The stake behind this whole block: fly.toml has no [mounts], so
    # storage/metrics_log.jsonl dies with every deploy and restart. If Sheets
    # is not writable, the pilot ends with nothing — so make that verifiable
    # here instead of by spending a paid question and hoping a row appears.
    if d["sheets_status"] != "ok":
        st.warning("אין אחסון קבוע לקובץ המקומי (ל-fly.toml אין [mounts]) — "
                   "הגיליון הוא השכבה העמידה היחידה. כל עוד הוא לא מאומת, "
                   "נתוני הפיילוט עלולים להימחק בדיפלוי הבא.")
    if st.button("בדוק חיבור לגיליון עכשיו", key="admin_sheets_probe"):
        with st.spinner("כותב שורת בדיקה לגיליון…"):
            _sheet_ok, _sheet_msg = metrics.check_sheets()
        (st.success if _sheet_ok else st.error)(_sheet_msg)
        st.caption("הבדיקה כותבת חותמת-זמן ללשונית _healthcheck בלבד — "
                   "היא לא נוגעת בנתוני המדדים.")
    st.caption(f"מכסות: {d['user_limit']} שאלות ליום לכל טאב, {d['global_limit']} ליום לכולם. "
               "הטבלאות למטה מציגות את הפעילות מאז האתחול האחרון של השרת; "
               "ההיסטוריה המלאה נשמרת בגיליון. עמודת device היא מזהה מכשיר "
               "אנונימי — קבצו לפיה כדי לספור אנשים ולא טאבים.")

    def _dark_dataframe(rows):
        # st.dataframe paints cell backgrounds with theme.backgroundColor on
        # a canvas (CSS can't reach it), which is now the splash olive — pin
        # readable dark cells via a pandas Styler instead
        import pandas as pd
        st.dataframe(
            pd.DataFrame(rows).style.set_properties(
                **{"background-color": "#21261A", "color": "#ECEDE6"}
            ),
            use_container_width=True,
        )

    st.subheader(f"👎/👍 משובים ({len(d['feedback'])})")
    if d["feedback"]:
        _dark_dataframe(d["feedback"])
    else:
        st.caption("אין עדיין משובים.")

    st.subheader(f"שאלות אחרונות ({len(d['questions'])})")
    if d["questions"]:
        _dark_dataframe(d["questions"])
    else:
        st.caption("אין עדיין שאלות.")

    st.download_button(
        "⬇️ הורד הכל (JSON)",
        json.dumps(d, ensure_ascii=False, indent=1, default=str),
        "commandai_metrics.json",
    )


if _is_admin:
    _render_admin()
    st.stop()

# ── Design tokens (from design_handoff_commandai) ──
# ONE accent for every role (user decision 2026-08-03): the olive brand color.
# The per-role palettes (commander tan #B29A72, reserve blue #8A9BC0) are
# retired — roles still differ in content (tools, suggestions, greeting),
# just not in color. 9a home palette: olive #99A26B → #A3AE6E, tints rebased
# on rgb(163,174,110); "bright" is the lightened accent for modal hero numbers.
_ACCENT_OLIVE = {
    "accent": "#A3AE6E", "accent_hover": "#B2BD7E",
    "soft": "rgba(163,174,110,.14)", "border": "rgba(163,174,110,.35)",
    "bright": "#C4CE92",
}
ROLE_META = {
    "soldier": {"label": "חייל", **_ACCENT_OLIVE},
    "commander": {"label": "מפקד", **_ACCENT_OLIVE},
    "reserve": {"label": "מילואים", **_ACCENT_OLIVE},
}
role_meta = ROLE_META.get(st.session_state.role, ROLE_META["soldier"])
role_label = role_meta["label"]


def _service_type_default() -> str:
    """The service type the entry role implies. The session default "סדיר"
    used to leak into every identity surface as a "שירות חובה" badge beside a
    reserve role — three contradictory surfaces on a fresh reserve profile
    (drawer, settings hub, service card; 2026-08-03 audit)."""
    return {"reserve": "מילואים", "commander": "קבע"}.get(st.session_state.role, "סדיר")


def _service_type_shown() -> str:
    """Display-only resolution: an explicit save in פרטים אישיים wins; until
    then the badge/card/form seed follow the role. Storage and the API path
    (profile_customized gate in handle_question) are untouched."""
    if st.session_state.get("profile_customized"):
        return st.session_state.get("service_type") or _service_type_default()
    return _service_type_default()


def _display_name() -> str:
    """First name for the greeting/pill/avatars — display-only; the full
    profile_name stays a settings field and is never sent to the API."""
    n = (st.session_state.get("profile_name") or "").strip()
    return n.split()[0][:20] if n else ""
ACCENT = role_meta["accent"]
ACCENT_HOVER = role_meta["accent_hover"]
ACCENT_SOFT = role_meta["soft"]
ACCENT_BORDER = role_meta["border"]
ACCENT_BRIGHT = role_meta["bright"]
# accent as an "r,g,b" triplet so drawer/settings tints can be role-aware via
# rgba(var(--accent-rgb), <alpha>) instead of a hardcoded olive.
ACCENT_RGB = ",".join(str(int(ACCENT.lstrip("#")[i:i + 2], 16)) for i in (0, 2, 4))
# Card/bubble fill (--surface below). A Python constant because the answer
# action row lives in a component IFRAME and has to paint this exact colour
# itself — CSS variables do not cross into a separate document, and any drift
# between the two would show up as a visible rectangle inside the bubble.
SURFACE = "#21261A"

# chat screen needs room under the fixed header band; entry has no header.
# --cai-sat is the iOS status-bar inset (measured on the shell doc, pushed
# into this frame by the PWA script) — the band grew by it, so clear it too.
# entry-like also covers the name gate (role picked, name not yet asked):
# the real entry screen keeps rendering under the gate overlay
# single source of truth for "the name gate is up" — the CSS padding below and
# the render at the entry gate must never disagree about it
_name_gate = (bool(st.session_state.get("role_picked_here"))
              and st.session_state.role is not None
              and not st.session_state.get("name_asked"))
_entry_like = st.session_state.role is None or _name_gate
MAIN_TOP_PADDING = "12px" if _entry_like else "calc(72px + var(--cai-sat, 0px))"

# entry elements stagger in around the boot splash curtain lift (delay 1.15s
# + .65s travel). 1.35s meant nothing STARTED fading until the lift was 30%
# done, leaving ~0.5s of pure dark-blank after the reveal (measured on the
# 2026-07-17 iPhone video, t=7.44-7.92, confirmed as a perceived stall).
# 0.9s starts the fades under the still-opaque curtain: the header is landing
# right as the lift finishes (the top edge is revealed LAST) and the role
# cards' rise is the only choreography left on screen — same look, -0.45s.
EHOLD = "0.9s" if splash_active else "0s"

# CSS-drawn role icons (chevron / bars / diamond) as inline SVG tiles.
# Shapes stay per-role; the color is the ONE olive accent (user decision
# 2026-08-03 — no more tan/blue category colors).
_ICON_SOLDIER = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='18' height='18'%3E%3Cpath d='M4 12 L9 6 L14 12' fill='none' stroke='%23A3AE6E' stroke-width='3'/%3E%3C/svg%3E\")"
_ICON_COMMANDER = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='18' height='18'%3E%3Crect x='1' y='4.5' width='16' height='3.5' rx='1' fill='%23A3AE6E'/%3E%3Crect x='1' y='11' width='16' height='3.5' rx='1' fill='%23A3AE6E'/%3E%3C/svg%3E\")"
_ICON_RESERVE = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='20' height='20'%3E%3Crect x='5.5' y='5.5' width='9' height='9' fill='none' stroke='%23A3AE6E' stroke-width='2.5' transform='rotate(45 10 10)'/%3E%3C/svg%3E\")"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;600;700;800&family=Suez+One&display=swap');

:root {{
    --bg: #14170E;
    --surface: {SURFACE};
    --surface-hover: #2A3120;
    --text: #EFF0E8;
    --text-sec: rgba(239,240,232,.6);
    --text-dim: rgba(239,240,232,.55);
    /* .55, not .4. Measured on the live page against --bg #14170E: .4 gives
       3.49:1, under the 4.5:1 floor, and it was carrying the legal
       disclaimer under the composer — the liability line ("not legal advice;
       official orders prevail") was the least readable text in the app, at
       10.5px, for users who read it outdoors in daylight. .50 = 4.75:1 clears
       the bar; .55 = 5.49:1 takes the sunlight headroom and matches
       --text-dim, which measured fine. */
    --text-faint: rgba(239,240,232,.55);
    --border: rgba(239,240,232,.12);
    --border-strong: rgba(239,240,232,.16);
    --accent: {ACCENT};
    --accent-hover: {ACCENT_HOVER};
    --accent-soft: {ACCENT_SOFT};
    --accent-border: {ACCENT_BORDER};
    --accent-bright: {ACCENT_BRIGHT};
    --accent-rgb: {ACCENT_RGB};
    --ehold: {EHOLD};
    /* reading-text multiplier — see the text_scale note at the top of the
       file. Applied ONLY to answer/question prose: scaling the chrome would
       move the header band, composer and drawer, which are pinned to measured
       iOS geometry and are the last thing that should follow a font setting. */
    --cai-fs: {st.session_state.get("text_scale", 1.0)};
}}

@keyframes enterUp {{ from {{ opacity:0; transform:translateY(18px); }} to {{ opacity:1; transform:none; }} }}
@keyframes enterScale {{ from {{ opacity:0; transform:scale(.6); }} to {{ opacity:1; transform:none; }} }}
/* mirror of bootCurtainUp: the re-armed curtain must also PARK invisible,
   or Safari keeps sampling the olive ghost for its chrome tint */
@keyframes curtainUp {{ 0% {{ transform:translateY(0); }} 99% {{ opacity:1; }} 100% {{ transform:translateY(-202%); opacity:0; visibility:hidden; }} }}

html, body, [data-testid="stApp"], [data-testid="stAppViewContainer"] {{
    font-family: Heebo, -apple-system, "Segoe UI", Arial, sans-serif;
    color: var(--text);
}}
/* ── ONE background: a single fixed gradient underlay ──
   The backdrop used to be painted twice — once on stAppViewContainer and
   again as a "bottom slice" on stBottom — with both copies sized by svh,
   which lies on iOS standalone, so the copies met at visibly different
   colors (the stripe the user circled above the composer and under the
   header). One fixed-position paint under everything makes a seam
   structurally impossible; the bars above it are translucent glass.
   (A ::before div, not background-attachment:fixed — iOS renders that
   black, see the old gradient note.) html keeps the dark base so
   overscroll never flashes light. body must NOT paint: an in-flow block's
   background covers negative-z descendants in paint order. */
html {{ background-color: var(--bg); }}
body {{ background: transparent !important; }}
[data-testid="stApp"], [data-testid="stAppViewContainer"] {{
    background: transparent !important;
}}
body::before {{
    content: ""; position: fixed; inset: 0; z-index: -1;
    background: linear-gradient(180deg, #14170E 0%, #161A0F 52%, #20270F 100%);
}}
/* standalone: inset:0 tracks the ghost-sized layout viewport at cold
   launch, which would stretch the ramp past the glass — pin the underlay
   to the clamped measurement so the visible ramp is exact */
html.cai-standalone body::before {{
    bottom: auto; height: var(--cai-vvh, 100svh);
}}
/* ── IN SAFARI, the same problem with a different cause. ──
   The bottom toolbar does not shrink the LAYOUT viewport: position:fixed,
   100vh and Streamlit's 100%-height shell all size to the tall one, so the
   last ~50px of everything sits UNDER the toolbar. On the underlay that
   truncates the ramp — the gradient's lightest stop never reaches the glass,
   so the soft glow reads as a hard cut across the bottom of the screen
   (2026-08-01 screenshots, browser only; the home-screen app is unaffected
   because it has no toolbar and --cai-vvh already pins it).

   dvh, not svh: svh is the toolbar-visible height and would leave a dead
   band the moment the toolbar collapsed, which is the objection that kept
   the standalone pin scoped away from Safari in the first place. dvh tracks
   the toolbar, so the fill is exact in both states. Cheap here because this
   is a fixed paint layer — no reflow — and because the page itself does not
   scroll (the chat scrolls inside .stMain), so in practice the toolbar never
   collapses and the value never moves.

   svh first as the fallback: a browser too old for dvh drops the second
   declaration and keeps today's behaviour rather than breaking. ── */
html:not(.cai-standalone) body::before {{
    bottom: auto;
    height: 100svh;
    height: 100dvh;
    /* ...and RESOLVE BACK TO THE BASE at the very bottom.
       Safari's toolbar is tinted #14170E (theme-color), the gradient's last
       stop is #20270F — 12/16/1 apart per channel, which on a near-black is a
       >60% luminance step. That step lands exactly on the toolbar's rounded
       top edge, which is what makes the rounding pop and reads as the design
       breaking off (2026-08-01 crop). The home-screen app has no toolbar, so
       there the glow correctly runs to the glass and this rule must not apply.

       Fixing it from the PAGE side, not by re-tinting the toolbar: theme-color
       paints the top chrome as well, so matching it to #20270F would only move
       the same seam to the status bar. Landing the page on #14170E instead is
       also robust to WHICH colour Safari picked — sampled or declared, it is
       that value at both ends.

       NO BRIGHT STOP NEAR THE BOTTOM. Keeping the glow and easing it out over
       the last 14% was tried and it traded the seam for a SMEAR: on the chat
       screen that put a 12/16/1 peak right behind the composer and dropped it
       again across ~250px, and a step that size over a distance that short is
       exactly what the eye reads as a smudge (2026-08-01 screenshots).

       The conflict is structural and worth stating: the design wants the glow
       brightest at the bottom, and in the browser the bottom IS a dark
       toolbar. There is no placement of a bright stop that satisfies both, so
       the browser gives the glow up — 4/5/1 of lift at midpoint, spread over
       ~985px, which is a vignette rather than a band, landing on the base
       colour at both ends. The home-screen app keeps the real thing. */
    background: linear-gradient(180deg,
        #14170E 0%, #181C0F 55%, #14170E 100%);
}}
/* iOS rubber-band overscroll must reveal the dark backdrop, never a light
   page edge; disable the bounce chain where the platform honors it */
html, body {{ overscroll-behavior-y: none; }}
/* no double-tap zoom (iOS 13+ honors manipulation = pan only + no dbl-tap);
   pinch + focus auto-zoom are killed by the viewport caps / gesture guards
   in the boot theme-pin component */
html, body {{ touch-action: manipulation; }}
/* iOS home-screen app: the layout viewport can stick LARGER than the
   physical screen (the 770dd2c phenomenon — dvh/ICB report ~56px extra at
   rest). Streamlit's shell is absolute-fill, so it inherits the ghost
   height and its sticky stBottom bottoms out BELOW the glass — composer
   low, disclaimer clipped. The (display-mode: standalone) media query and
   the svh unit BOTH failed to bite on-device, so the boot pin frame
   detects standalone in JS (html.cai-standalone) and feeds the MEASURED
   visible height as --cai-vvh; svh stays only as a fallback. Scoped to
   standalone: in Safari the URL bar collapses and the app must keep
   filling the grown viewport, so a pinned height would leave a dead band. */
/* .stMain is a CLASS on purpose: with a chat_input mounted the main section's
   data-testid flips to stAppScrollToBottomContainer (the class persists) —
   a testid selector left the chat screen's scroller unpinned. */
html.cai-standalone .stApp,
html.cai-standalone [data-testid="stAppViewContainer"],
html.cai-standalone .stMain {{
    height: var(--cai-vvh, 100svh) !important;
    min-height: var(--cai-vvh, 100svh) !important;
    max-height: var(--cai-vvh, 100svh) !important;
}}
/* Safari gets the same pin, from dvh instead of a measurement. The composer
   strip is STICKY inside .stMain, so it bottoms out wherever that scroller
   ends — and unpinned that is the tall layout viewport, which puts the
   composer and the disclaimer under the toolbar. Same defect the standalone
   note above describes, reached by a different route.
   The "dead band" the note warns about was the reason not to pin here, and
   it was a reason not to pin to a FIXED height: dvh follows the toolbar, so
   the app keeps filling the glass in both states. svh first as the fallback
   for engines without dvh. */
html:not(.cai-standalone) .stApp,
html:not(.cai-standalone) [data-testid="stAppViewContainer"],
html:not(.cai-standalone) .stMain {{
    height: 100svh !important;  min-height: 100svh !important;  max-height: 100svh !important;
    height: 100dvh !important;  min-height: 100dvh !important;  max-height: 100dvh !important;
}}
/* cold-launch overflow corrective: the boot pin frame MEASURES where the
   composer strip really ends and pulls the whole column up by the excess
   (--cai-vvoff, 0 whenever the geometry is healthy — see the pin script) */
html.cai-standalone [data-testid="stAppViewContainer"] {{
    margin-top: calc(-1 * var(--cai-vvoff, 0px)) !important;
}}
html.cai-standalone .st-key-cai_drawer,
html.cai-standalone .st-key-cai_settings,
html.cai-standalone .st-key-drawer_backdrop,
html.cai-standalone .st-key-settings_backdrop {{
    height: var(--cai-vvh, 100svh) !important; bottom: auto !important;
}}
/* ── streaming scroll runaway + finger-drag (2026-07-21 phone) ──
   Streamlit's composer strip is STICKY inside the chat scroller. While an
   answer streams, the auto scroll-to-bottom can land on an OUTER container
   (and a finger can drag it) — the sticky loses its anchor and travels
   mid-screen with the answer flowing below it, snapping back only when the
   stream ends. Structural fix, standalone-scoped: no outer element may
   scroll at all — the chat section (and the drawer/settings panels) are the
   only scrollers — and the composer strip is a true fixed overlay pinned to
   the MEASURED glass bottom (--cai-vvh; bottom:0 trusts the layout viewport,
   which ghosts larger than the glass on iOS). translateY self-compensates
   its unknown height; --cai-sbh (measured by the engine, fallback 134px)
   reserves scroll room so the last message is never hidden under it. */
html.cai-standalone, html.cai-standalone body,
html.cai-standalone .stApp,
html.cai-standalone [data-testid="stAppViewContainer"] {{
    overflow: hidden !important;
    overscroll-behavior: none !important;
}}
html.cai-standalone [data-testid="stBottom"] {{
    position: fixed !important;
    top: calc(var(--cai-vvh, 100svh) - var(--cai-vvoff, 0px)) !important;
    bottom: auto !important;
    left: 0 !important; right: 0 !important;
    transform: translateY(-100%);
    z-index: 99;
}}
html.cai-standalone .stMain {{
    padding-bottom: var(--cai-sbh, 134px) !important;
}}
/* ── keyboard mode (html.cai-kb) — armed by the engine while the composer
   textarea is focused AND the pane is keyboard-shrunken. The strip glues
   to the LIVE keyboard top (--cai-kbb = vv.offsetTop + vv.height, layout-
   viewport coords, restamped on every visual-viewport event), so it rides
   the keyboard instead of hiding behind it / floating mid-screen. ── */
html.cai-standalone.cai-kb [data-testid="stBottom"] {{
    top: var(--cai-kbb, calc(var(--cai-vvh, 100svh) - var(--cai-vvoff, 0px))) !important;
    /* iOS often reports the pane in ONE resize at animation end — animate
       the jump so the strip rides up instead of teleporting */
    transition: top .18s cubic-bezier(.2,.7,.2,1);
    /* 19:22 phone shot: the pill floated high above the glue line — the
       strip drags its home-indicator inset (env sab ≈ 34px) although that
       zone is behind the keyboard now; and the upward-fading tint read as
       a hazy block against the dimmed page. Tight padding + no tint/blur:
       the wash separates content, the near-opaque pill floats clean. */
    padding-bottom: 10px;
    background: transparent !important;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
}}
/* a residual reveal-pan uncovers the canvas below the fixed underlay —
   match it to the gradient tail so it can never read as a black band
   (!important: the splash darkener pins html/body dark with !important) */
html.cai-standalone.cai-kb {{ background-color: #20270F !important; }}
/* focus dim: a fixed wash between content and chrome (content z-auto <
   60 < strip 99 < header 100); taps pass through so the sample chips stay
   live; overdrawn 40vh past both edges to cover pan reveals */
html.cai-standalone body::after {{
    content: ""; position: fixed; inset: -40vh 0; z-index: 60;
    background: rgba(11,13,7,0); pointer-events: none;
    transition: background .22s ease;
}}
html.cai-standalone.cai-kb body::after {{ background: rgba(11,13,7,.55); }}
/* typing emphasis: the translucent pill goes near-opaque over the wash,
   and the disclaimer clears the strip (tighter hug, less noise) */
html.cai-standalone.cai-kb [data-testid="stChatInput"] {{
    background-color: rgba(26,30,15,.92) !important;
}}
html.cai-standalone.cai-kb [data-testid="stBottomBlockContainer"]::after {{
    display: none;
}}
/* iOS Safari "text autosizing" inflates long text blocks (cards, title,
   disclaimer) on the phone only — desktop matched the mock, iPhone didn't.
   Pin the rendered sizes to the authored ones. */
html {{ -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }}
/* the gradient itself lives on body::before above — the container only
   keeps its viewport-filling min-height (svh: never exceeds the visible
   glass, unlike dvh which sticks large on iOS standalone) */
[data-testid="stAppViewContainer"] {{
    min-height: 100vh;
    min-height: 100svh;
}}
/* hide the scroll bar (shows as a strip on the left edge in RTL).
   NB stAppScrollToBottomContainer: the main <section> is REPLACED by this
   testid once a chat input mounts — it's the chat screen's real scroller,
   and Streamlit gives it scrollbar-width:thin (the visible side line) */
[data-testid="stAppViewContainer"], [data-testid="stMain"],
[data-testid="stAppScrollToBottomContainer"], body {{
    scrollbar-width: none !important;
}}
[data-testid="stAppViewContainer"]::-webkit-scrollbar,
[data-testid="stMain"]::-webkit-scrollbar,
[data-testid="stAppScrollToBottomContainer"]::-webkit-scrollbar,
body::-webkit-scrollbar {{ display: none !important; width: 0 !important; }}
/* hide Streamlit Cloud viewer badges — the crown "hosted with Streamlit"
   pill and the creator-avatar bubble injected at the bottom corner (their
   class hashes vary by build, so match every known naming scheme).
   stStatusWidget is hidden by boot_shell.py's own rule, not here: it reports
   "Connecting" from an invisible corner while every server-backed control is
   dead, so #cai-net-bar (outside #root, socket-wrapped, with a refresh
   button) speaks for the connection instead. Do not "restore" it — that was
   tried on 2026-08-10 and is strictly worse than the bar. */
[class*="viewerBadge"],
[class*="_viewerBadge"],
[class*="_profileContainer"],
[class*="_profilePreview"],
[class*="_profileImage"],
[data-testid="appCreatorAvatar"],
[data-testid="stStatusWidget"],
a[href*="streamlit.io/cloud"],
a[href*="share.streamlit.io"] {{ display: none !important; }}
/* the shell-darkener injects `iframe{{background:#14170E}}` into every
   same-origin ancestor document INCLUDING this one; keep component iframes
   in THIS document out of it (the injected rule keeps its real job:
   darkening the cloud-shell documents above us).
   This is NOT what governs whether a component frame reads as transparent —
   an element background paints behind the frame's canvas, and the canvas is
   opaque whenever Streamlit's `color-scheme: normal` on the iframe disagrees
   with the scheme of the document inside it. Only that document can fix it;
   see the answer action row's own <style> for the full story. Every other
   component here is height=0, so this rule is all they need. */
[data-testid="stElementContainer"] iframe,
iframe[data-testid="stIFrame"] {{ background: transparent !important; }}
[data-testid="stAppViewContainer"], [data-testid="stBottom"], [data-testid="stSidebar"] {{ direction: rtl; }}
/* <body> keeps Streamlit's own dark base (#0E1117) rather than ours: the theme
   config's backgroundColor reaches stApp, not the body element. Nothing visible
   sat on it — every surface above is painted — so it went unnoticed until the
   2026-08-06 boot probe sampled the computed background frame by frame and
   printed it. Dark-on-dark, but it is still a second near-black in a palette
   that is supposed to have one, and it would show the moment anything above it
   goes translucent. html is set by boot_shell's micro-style; this is the pair
   to it. */
body {{ background: #14170E !important; }}

/* Hide Streamlit chrome, but keep the sidebar toggle (lives inside <header>) visible. */
#MainMenu, footer {{ visibility: hidden; }}
header {{ visibility: hidden; }}
[data-testid="stToolbarActions"] {{ display: none; }}

/* ── Sidebar open/close buttons — hamburger-style surface tile ── */
[data-testid="stExpandSidebarButton"],
[data-testid="stSidebarCollapseButton"] {{
    visibility: visible !important;
    background-color: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
    width: 40px !important;
    height: 40px !important;
}}
/* the hamburger lives INSIDE the fixed header band: same 430px column,
   vertically centered in the 64px bar, above it in z-order; drawn as 3
   bars per the design instead of Streamlit's arrow icon */
[data-testid="stExpandSidebarButton"] {{
    position: fixed !important;
    top: calc(var(--cai-sat, 0px) + 12px) !important;
    inset-inline-start: 20px !important;
    z-index: 110 !important;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='12'%3E%3Crect width='16' height='2' y='0' rx='1' fill='%23ECEDE6'/%3E%3Crect width='16' height='2' y='5' rx='1' fill='%23ECEDE6'/%3E%3Crect width='16' height='2' y='10' rx='1' fill='%23ECEDE6'/%3E%3C/svg%3E") !important;
    background-repeat: no-repeat !important;
    background-position: center !important;
}}
[data-testid="stExpandSidebarButton"] svg,
[data-testid="stExpandSidebarButton"] span {{ display: none !important; }}
[data-testid="stExpandSidebarButton"]:hover,
[data-testid="stSidebarCollapseButton"]:hover {{ background-color: var(--surface-hover) !important; }}
[data-testid="stExpandSidebarButton"] svg,
[data-testid="stSidebarCollapseButton"] svg {{ fill: var(--text) !important; }}

/* ── App-owned drawer + hamburger (replaces st.sidebar) ──
   The cloud platform force-suppresses the native sidebar: on *.streamlit.app
   stSidebar NEVER mounts (MutationObserver across the whole role-pick
   transition, 2026-07-13, on a build whose config.toml has no toolbarMode
   override), even though the identical code mounts it locally — platform
   client flags outrank config.toml. These elements are plain widgets, so no
   platform sidebar behavior can take them away. */
.st-key-drawer_open_btn {{
    position: fixed; top: calc(var(--cai-sat, 0px) + 11px); inset-inline-start: 18px;
    width: 44px; z-index: 110;
}}
/* 44px CIRCLE (was 42 — the header's padding-right of 60 = 18 inset + 42 button
   is updated to match below), olive-tinted, three 15×2 olive bars (gap 4) */
.st-key-drawer_open_btn button {{
    width: 44px !important; height: 44px !important; min-height: 44px !important;
    background-color: rgba(163,174,110,.14) !important;
    border: 1px solid rgba(163,174,110,.3) !important; border-radius: 50% !important;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='15' height='14'%3E%3Crect width='15' height='2' y='0' rx='1' fill='%23A3AE6E'/%3E%3Crect width='15' height='2' y='6' rx='1' fill='%23A3AE6E'/%3E%3Crect width='15' height='2' y='12' rx='1' fill='%23A3AE6E'/%3E%3C/svg%3E") !important;
    background-repeat: no-repeat !important; background-position: center !important;
}}
/* sr-only, NOT display:none — the icon is a background-image, so this <p> is
   the button's ONLY accessible name, and display:none deletes it from the
   accessibility tree. Absolute positioning keeps it out of flow, so the 42px
   circle and its centred background-image are unaffected. Same reasoning for
   the two backdrops below: a full-viewport button that VoiceOver announces as
   an unlabelled "button" is the worst offender on the page. */
.st-key-drawer_open_btn button p,
.st-key-drawer_backdrop button p {{
    position: absolute !important; width: 1px !important; height: 1px !important;
    padding: 0 !important; margin: -1px !important; overflow: hidden !important;
    clip-path: inset(50%) !important; white-space: nowrap !important;
    border: 0 !important;
}}
@media (hover: hover) {{
    .st-key-drawer_open_btn button:hover {{ background-color: rgba(163,174,110,.24) !important; }}
}}
/* THE SCRIM MUST ACTUALLY BE THE SCREEN. Streamlit stamps an INLINE width on
   every stElementContainer (here: 32px, the width of a button with no visible
   label), and an explicit width beats left+right on an out-of-flow box — so
   `inset: 0` never widened this one. The result was a 32×28 dark box in the
   top corner: the "small black square" from the 2026-07-27 phone report, and
   the reason tapping outside the drawer never closed it — only that square
   did. Force the whole chain to the viewport. */
.st-key-drawer_backdrop {{
    position: fixed; inset: 0; z-index: 125;
    width: 100% !important; height: 100% !important;
}}
.st-key-drawer_backdrop div[data-testid="stButton"] {{
    width: 100% !important; height: 100% !important; margin: 0 !important;
}}
.st-key-drawer_backdrop button {{
    width: 100% !important; height: 100% !important; min-height: 100% !important;
    background: rgba(9, 11, 7, .62) !important;
    border: none !important; border-radius: 0 !important; box-shadow: none !important;
}}
/* (its sr-only rule is grouped with the hamburger's above) */
.st-key-cai_drawer {{
    position: fixed; top: 0; bottom: 0; inset-inline-start: 0;
    width: min(78vw, 340px); z-index: 130;
    background: #14170E; border-inline-end: 1px solid var(--border);
    box-shadow: 0 0 40px rgba(0, 0, 0, .45);
    padding: calc(env(safe-area-inset-top, 0px) + 16px) 18px 24px;
    overflow-y: auto; overscroll-behavior: contain;
    /* no slide-in animation on purpose: Streamlit replaces the node on
       EVERY rerun (pill click, expander toggle), which restarts a CSS
       animation and makes the open drawer jump 12% sideways mid-use */
}}
.st-key-cai_drawer [data-testid="stElementContainer"] {{ margin-bottom: 8px; }}
/* 9a language inside the drawer: translucent card buttons (the solid
   var(--surface) look belongs to the previous design) — the solid-olive
   "+ שיחה חדשה" keeps its own !important styling */
.st-key-cai_drawer div[data-testid="stButton"] > button {{
    background-color: rgba(239,240,232,.045);
    border: 1px solid rgba(239,240,232,.12);
}}
.st-key-cai_drawer hr {{ border-color: var(--border) !important; margin: 14px 0 !important; }}
.st-key-drawer_close [data-testid="stElementContainer"],
.st-key-cai_drawer .st-key-drawer_close {{ margin-bottom: 2px; }}
.st-key-drawer_close {{ display: flex; justify-content: flex-end; }}
/* The close button is styled in _DS_CSS (44px square, radius 12, cool tint)
   alongside .st-key-open_settings — its top-corner twin. A 36px olive circle
   used to be declared HERE as well, with !important on every property, and
   the two disagreed on size, radius, background, border and colour; the only
   reason the 44px version won is that _DS_CSS is markdown'd later in the
   document. Deleted rather than reconciled — one owner per control. */

/* ── Main container — mobile-first column, max 430px ── */
[data-testid="stMainBlockContainer"], .main .block-container {{
    max-width: 560px;
    /* lateral max(): no-op in portrait, keeps the column off the Island when
       an in-browser tab is rotated (see the .cai-header note) */
    padding: {MAIN_TOP_PADDING}
             max(22px, env(safe-area-inset-right, 0px))
             7rem
             max(22px, env(safe-area-inset-left, 0px)) !important;
    margin: 0 auto;
}}

/* ── Splash re-arm: the boot curtain (first delta, top of script) has been
   covering the whole load; this rule landing with the entry screen swaps
   the animation NAME, which restarts the clock — hold 1.15s more, then
   lift. Element/child styles live in the boot block. ── */
.cai-splash {{
    animation: curtainUp 1.05s cubic-bezier(.7,0,.3,1) both; animation-delay: 1.15s;
}}

/* ── Entry screen header (staggers in after the splash lifts) ── */
.cai-entry {{ text-align: center; padding-top: 7vh; }}
.cai-entry > div {{ animation: enterUp .6s cubic-bezier(.2,.7,.2,1) both; }}
/* var(--accent), not the literal #99A26B: that was the pre-2026-08-03 olive,
   retired when the palette rebased to #A3AE6E (see _ACCENT_OLIVE). It kept
   shipping here and on the divider below, so the FIRST screen of the app was
   the one surface still painting the old accent — two olives, 6px apart in
   hue, side by side with the chevron's #A3AE6E. */
.cai-entry-classif {{ font: 600 11px ui-monospace, Menlo, monospace; letter-spacing: 3px; color: var(--accent);
    animation-delay: calc(var(--ehold) + .2s) !important; }}
.cai-entry-chev {{ display:flex; flex-direction:column; align-items:center; margin-top: 26px;
    animation-delay: calc(var(--ehold) + .3s) !important; }}
.cai-entry-chev span {{ display:block; width:22px; height:22px;
    border-top:5px solid #A3AE6E; border-left:5px solid #A3AE6E; transform:rotate(45deg); }}
.cai-entry-chev span + span {{ border-color: rgba(163,174,110,.45); margin-top:-8px; }}
.cai-entry-title {{ font: 400 40px 'Suez One', serif; color: var(--text); margin-top: 18px;
    animation-delay: calc(var(--ehold) + .38s) !important; }}
.cai-entry-sub {{ font: 400 15px Heebo, sans-serif; color: var(--text-sec); margin-top: 6px;
    animation-delay: calc(var(--ehold) + .46s) !important; }}
.cai-entry-divider {{ width: 44px; height: 2px; background: var(--accent); margin: 26px auto 0;
    animation-delay: calc(var(--ehold) + .54s) !important; }}
.cai-entry-choose {{ font: 500 13px Heebo, sans-serif; color: rgba(236,237,230,.55); margin: 26px 0 14px;
    animation-delay: calc(var(--ehold) + .62s) !important; }}
.cai-entry-footer {{ text-align: center; padding: 18px 0 8px;
    font: 500 10.5px ui-monospace, Menlo, monospace; letter-spacing: 2px; color: var(--text-faint);
    animation: enterUp .6s cubic-bezier(.2,.7,.2,1) both; animation-delay: calc(var(--ehold) + 1.05s); }}

/* ── Buttons — surface cards, radius 14, press scale ── */
div[data-testid="stButton"] > button {{
    width: 100%;
    border-radius: 14px;
    background-color: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: Heebo, sans-serif;
    font-size: 14px;
    font-weight: 400;
    padding: 14px 16px;
    line-height: 1.4;
    margin-bottom: 12px;
    white-space: normal;
    text-align: right;
    box-shadow: none;
    transition: background-color .18s ease, border-color .18s ease, transform .1s ease;
}}
/* hover only where a real pointer exists — iOS applies :hover on tap and
   KEEPS it (sticky hover): a touched suggestion card stayed lit with an
   olive border. Touch devices get the :active press feedback only. */
@media (hover: hover) {{
    div[data-testid="stButton"] > button:hover {{
        background-color: var(--surface-hover);
        border-color: var(--accent-border);
        color: var(--text);
    }}
}}
/* press feedback = composite, not scale alone: scale(.98) by itself was on
   phones since 13.07 and read as "no response" (2026-08-03 design review) —
   the surface tint + accent border are what the finger actually sees. The
   short transition-duration makes press-in instant; release rebounds on the
   base rule's .18s. */
div[data-testid="stButton"] > button:active {{
    transform: scale(.985);
    background-color: var(--surface-hover);
    border-color: var(--accent-border);
    transition-duration: .05s;
}}
/* accordion rows (answer sources, tools content) — same press language */
[data-testid="stExpander"] summary:active {{ background: rgba(236,237,230,.06); }}
/* the client-side orders accordion: card head + order rows */
.cai-kb-card:active {{ filter: brightness(1.18); }}
.cai-order-link:active {{ background: rgba(236,237,230,.07); }}

/* ── Entry role buttons: icon tile + title/subtitle, staggered entrance ── */
.st-key-role_soldier button, .st-key-role_commander button, .st-key-role_reserve button {{
    display: flex !important; align-items: center; gap: 14px;
    padding: 16px 18px !important;
    animation: enterUp .6s cubic-bezier(.2,.7,.2,1) both;
}}
.st-key-role_soldier button {{ animation-delay: calc(var(--ehold) + .7s); }}
.st-key-role_commander button {{ animation-delay: calc(var(--ehold) + .8s); }}
.st-key-role_reserve button {{ animation-delay: calc(var(--ehold) + .9s); }}
.st-key-role_soldier button::before, .st-key-role_commander button::before, .st-key-role_reserve button::before {{
    content: ""; width: 44px; height: 44px; border-radius: 12px; flex: none;
    background-repeat: no-repeat; background-position: center;
}}
.st-key-role_soldier button::before,
.st-key-role_commander button::before,
.st-key-role_reserve button::before {{
    background-color: rgba(163,174,110,.14); border: 1px solid rgba(163,174,110,.35);
}}
.st-key-role_soldier button::before {{ background-image: {_ICON_SOLDIER}; }}
.st-key-role_commander button::before {{ background-image: {_ICON_COMMANDER}; }}
.st-key-role_reserve button::before {{ background-image: {_ICON_RESERVE}; }}
@media (hover: hover) {{
    .st-key-role_soldier button:hover,
    .st-key-role_commander button:hover,
    .st-key-role_reserve button:hover {{ border-color: rgba(163,174,110,.5) !important; }}
}}
.st-key-role_soldier button p, .st-key-role_commander button p, .st-key-role_reserve button p {{
    font-size: 12.5px !important; color: var(--text-dim); text-align: right; margin: 0; line-height: 1.35;
}}
.st-key-role_soldier button p strong, .st-key-role_commander button p strong, .st-key-role_reserve button p strong {{
    display: block; font-size: 16px; font-weight: 600; color: var(--text); margin-bottom: 2px;
}}

/* ── Name gate (one-time, over the live entry screen): fixed scrim + card.
   Scrim sits under the splash/veil (9999xx) but over everything else; the
   entry buttons beneath stay rendered yet unreachable. Card is top-anchored
   (~15vh) so the iOS keyboard never covers the input. No entrance animation:
   Streamlit may replace the keyed VB on the text-commit rerun, and a replay
   would read as a blink. ── */
.st-key-cai_name_gate {{
    position: fixed; inset: 0; z-index: 999950;
    background: rgba(8,10,5,.62);
    display: flex; flex-direction: column; align-items: center;
    justify-content: flex-start;
    padding: calc(var(--cai-sat, 0px) + 15vh) 24px 0;
}}
.st-key-cai_name_card {{
    width: min(320px, 100%); flex: none;
    background: #1A1E12;
    border: 1px solid rgba(239,240,232,.14);
    border-radius: 18px;
    padding: 20px 18px 16px;
}}
/* ── Streamlit's "Missing Submit Button" warning, suppressed. ──
   A red developer error box, in English, over the name gate, on a soldier's
   first launch (2026-07-31 23:29 video, ~t=8s). It is a FALSE POSITIVE: the
   form has two form_submit_buttons and they work. Streamlit's form component
   renders the warning whenever, at the instant the script run flips to
   NOT_RUNNING, no submit button has registered itself with the widget manager
   yet — and the buttons register from a mount effect, a beat later. Reproduced
   locally with a MutationObserver: it appears and is gone 29ms later. The
   phone is slower, so there it lasts about a second.

   Scoped by SHAPE, not by guesswork. The form component renders exactly two
   children: the content block, which carries data-testid="stVerticalBlock",
   and this warning, in a bare wrapper with no data-testid at all. So an
   unlabelled DIRECT child of stForm is the warning and nothing else — a real
   st.error inside a form lives under the vertical block and is untouched.
   display:none rather than visibility, so it never takes layout and the card
   cannot jump when it comes and goes. ── */
[data-testid="stForm"] > div:not([data-testid]) {{ display: none !important; }}

.cai-gate-title {{ font: 600 17px Heebo, sans-serif; color: var(--text); text-align: right; }}
.cai-gate-sub {{ font: 400 12px Heebo, sans-serif; color: rgba(239,240,232,.5);
    margin: 5px 0 0; text-align: right; line-height: 1.5; }}
.st-key-cai_name_card [data-testid="stTextInput"] {{ margin: 12px 0 14px; }}
.st-key-cai_name_card [data-testid="stTextInput"] div[data-baseweb="input"],
.st-key-cai_name_card [data-testid="stTextInput"] div[data-baseweb="base-input"] {{
    background-color: rgba(239,240,232,.045) !important;
    border: 1px solid rgba(239,240,232,.16) !important;
    border-radius: 14px !important;
}}
.st-key-cai_name_card [data-testid="stTextInput"] div[data-baseweb="base-input"] {{ border: none !important; background: transparent !important; }}
.st-key-cai_name_card [data-testid="stTextInput"] input {{
    background: transparent !important; color: var(--text) !important;
    font: 400 15px Heebo, sans-serif !important; direction: rtl;
    padding: 12px 14px !important;
    /* 38px measured — under the 44px thumb floor, and this is the very first
       control a new user is asked to hit */
    min-height: 44px !important; box-sizing: border-box !important;
}}
.st-key-cai_name_card [data-testid="stTextInput"] input::placeholder {{
    color: rgba(239,240,232,.5) !important;
}}
/* המשך/דלג side by side even on phones (Streamlit stacks columns <640px) */
.st-key-cai_name_card [data-testid="stHorizontalBlock"] {{
    flex-wrap: nowrap !important; gap: 10px !important;
}}
.st-key-cai_name_card [data-testid="stHorizontalBlock"] [data-testid="stColumn"] {{
    width: auto !important; min-width: 0 !important; flex: 1 1 0 !important;
}}
/* keep the 5:3 המשך/דלג ratio the nowrap override flattened */
.st-key-cai_name_card [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:last-child {{
    flex: .62 1 0 !important;
}}
/* the gate form is layout-only: kill the stForm frame (keyed FORMS don't
   get an st-key-* class in 1.58 — scope through the card container) */
.st-key-cai_name_card [data-testid="stForm"] {{ border: none !important; padding: 0 !important; }}
/* Streamlit's own "Press Enter to submit form" hint (InputInstructions) is
   laid out in the field row and lands ON the RTL placeholder / typed text
   (2026-07-27 video, t=16-19.5). It also happens to be the only English
   string on the screen, and the card already has explicit המשך/דלג buttons —
   so it carries no information here. Same widget-in-a-form shape in the
   settings card, same collision, killed together. */
.st-key-cai_name_card [data-testid="InputInstructions"],
.st-key-cai_pf_form [data-testid="InputInstructions"] {{ display: none !important; }}
.st-key-cai_name_card [data-testid="stFormSubmitButton"] button {{
    justify-content: center !important; text-align: center !important;
    margin-bottom: 0 !important; padding: 11px 0 !important;
    border-radius: 12px !important;
}}
.st-key-cai_name_card button[kind="primaryFormSubmit"] {{
    background-color: var(--accent) !important;
    border: 1px solid var(--accent) !important;
}}
.st-key-cai_name_card button[kind="primaryFormSubmit"] p {{ color: #14170E !important; font-weight: 600 !important; }}
.st-key-cai_name_card button[kind="secondaryFormSubmit"] {{
    background-color: transparent !important;
    border: 1px solid rgba(239,240,232,.16) !important;
}}
.st-key-cai_name_card button[kind="secondaryFormSubmit"] p {{ color: rgba(239,240,232,.6) !important; }}
@media (hover: hover) {{
    .st-key-cai_name_card button[kind="primaryFormSubmit"]:hover {{ background-color: var(--accent-hover) !important; border-color: var(--accent-hover) !important; }}
    .st-key-cai_name_card button[kind="secondaryFormSubmit"]:hover {{ border-color: rgba(239,240,232,.3) !important; }}
}}

/* ── Chat header: FIXED top bar (sticky can't work here — Streamlit wraps
   the markdown in a container exactly as tall as the header, leaving it no
   room to stick, so it scrolled away). Full-width fixed band; side paddings
   center the content on the 430px column and clear the hamburger. ── */
.cai-header {{
    position: fixed; top: 0; left: 0; right: 0; z-index: 100;
    /* the band grows UP by the status-bar inset so its dark fill sits behind
       the translucent clock; content stays in the 64px below via padding */
    height: calc(64px + var(--cai-sat, 0px)); box-sizing: border-box;
    /* tint fades to nothing at the band's lower edge — a flat fill ended in
       a visible line against the page gradient (user-circled seam); the hue
       matches the gradient TOP so the fade is invisible on home */
    background: linear-gradient(180deg,
        rgba(20,23,14,.92) 0%, rgba(20,23,14,.82) 55%, rgba(20,23,14,0) 100%);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    /* gap 0, not 12: the wordmark centers via auto margins between the
       button-side padding edge and the pill — a fixed gap would bias it.
       Button-side padding 62 = 18px inset + 44px button, EXACTLY the drawer
       button's footprint, so the padding edge IS the button's inner edge
       (72 overshot by 12px and pulled the wordmark 6px off center).
       This tracks .st-key-drawer_open_btn: the button went 42->44 for the
       thumb floor on 2026-08-10, so this went 60->62 in the same change. */
    display: flex; align-items: center; gap: 0;
    /* the lateral max() pair is a no-op in portrait (both insets read 0) and
       only bites when an in-browser Safari tab is rotated — the manifest
       orientation lock only governs the installed/standalone app, so the
       browser path needs the insets to keep the drawer button and wordmark
       clear of the Dynamic Island. */
    padding: var(--cai-sat, 0px)
             max(62px, env(safe-area-inset-right, 0px))
             0
             max(18px, env(safe-area-inset-left, 0px));
    /* 9a: NO divider line beneath the header */
    /* no entrance animation: a transform on a fixed element re-anchors it
       and Streamlit can freeze the animation at its from-state (top: 18px) */
}}
/* in-flow with auto margins: the wordmark sits exactly midway between the
   drawer button (the 72px padding edge) and the pill — optical centering
   between its real neighbors, not on the viewport (user request 2026-08-03;
   replaces the old absolute left:50% screen-centering). line-height fills
   the 64px strip below the status-bar inset. */
.cai-wordmark {{ font: 400 20px 'Suez One', serif; color: var(--text);
    margin-inline: auto;
    line-height: 64px; white-space: nowrap; }}
/* 9a: two-tone wordmark — "Command" light, "AI" olive (header + entry title) */
.cai-wordmark .cai-wm-ai, .cai-entry-title .cai-wm-ai {{ color: var(--accent); }}
/* identity cluster — boxless two-line lockup at the trailing edge (replaces
   the old .cai-pill capsule, user pick 2026-08-03). No auto margin: the
   wordmark's margin-inline:auto already pushes it to the edge. Both lines
   NOWRAP + ellipsis inside a capped width, so a long name can never wrap the
   header or shove the centered wordmark around. */
.cai-ident {{
    flex: none; display: flex; flex-direction: column;
    align-items: flex-end; gap: 3px; max-width: 108px;
}}
.cai-ident .nm {{
    font: 500 13.5px Heebo, sans-serif; color: var(--text); line-height: 1.1;
    max-width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.cai-ident .rl {{
    font: 400 10.5px Heebo, sans-serif; color: var(--accent-bright);
    opacity: .8; line-height: 1; white-space: nowrap;
}}

/* ── Chat home greeting — 9a: title 30px + subtitle 13.5px, both CENTERED,
   7px apart (the previous right-aligned pass followed the older handoff;
   the 9a redesign centers the greeting block) ── */
.cai-greet {{ font: 400 30px 'Suez One', serif; color: var(--text); margin: 0 0 7px;
    text-align: center;
    animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .08s; }}
/* The greeting breaks into two DELIBERATE lines instead of letting the name
   decide. Measured at 375px (343px of text width, real Suez One metrics):
   "היי <name>, במה אפשר לעזור?" is 315px for a 3-letter name but 352px for
   "צוריאל" and 402px for a hyphenated one — so every name past ~4 letters
   overflowed, and the wrap orphaned the "?" onto a line of its own (device
   video 2026-08-05). Split, each line is safe for any name: the question is a
   fixed 213px, and the greeting line is 96-184px across the names tested. */
.cai-greet-hi, .cai-greet-q {{ display: block; }}
.cai-greet-q {{ white-space: nowrap; }}
.cai-greet-hi {{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
/* Stale-greeting guard: the greeting + suggestion cards are gated in Python
   (they don't re-render once a question exists), but Streamlit prunes
   un-re-rendered elements only when the SCRIPT RUN ENDS — and the first
   answer streams for ~20-30s, so the pilot saw the suggestion list sitting
   under the live answer the whole time (2026-07-27 video). The moment any
   chat message exists in the DOM, hide them by CSS instead of waiting. */
[data-testid="stAppViewContainer"]:has([data-testid="stChatMessage"]) .cai-greet,
[data-testid="stAppViewContainer"]:has([data-testid="stChatMessage"]) .cai-greet-sub,
[data-testid="stAppViewContainer"]:has([data-testid="stChatMessage"]) [class*="st-key-sug_"] {{
    display: none !important;
}}
.cai-greet-sub {{ font: 400 13.5px Heebo, sans-serif; color: var(--text-dim); margin-bottom: 0;
    text-align: center;
    animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .16s; }}

/* ── Chat home vertical layout — 9a: the greeting+cards block is VERTICALLY
   CENTERED between header and composer, with 78px bottom padding so it sits
   slightly above true center. Gated on .cai-greet (home only); the section
   testid swaps to stAppScrollToBottomContainer once chat_input mounts, so
   the gate anchors on stAppViewContainer. ── */
[data-testid="stAppViewContainer"]:has(.cai-greet) [data-testid="stMainBlockContainer"] {{
    display: flex; flex-direction: column;
    min-height: calc(100vh - 134px - env(safe-area-inset-bottom, 0px));  /* composer strip */
    min-height: calc(100svh - 134px - env(safe-area-inset-bottom, 0px)); /* svh: see gradient note */
    padding-top: calc(64px + var(--cai-sat, 0px)) !important; /* header band */
    padding-bottom: 78px !important;
}}
/* standalone: svh lies at cold launch (ghost viewport, see --cai-vvh notes),
   sinking the vertically-centered greeting ~28px until the first re-layout —
   center on the glass-clamped measurement instead */
html.cai-standalone [data-testid="stAppViewContainer"]:has(.cai-greet) [data-testid="stMainBlockContainer"] {{
    min-height: calc(var(--cai-vvh, 100svh) - 134px - env(safe-area-inset-bottom, 0px));
}}
/* the vertical block stretches to fill, so the centering happens inside it */
[data-testid="stAppViewContainer"]:has(.cai-greet)
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {{
    justify-content: center;
}}

/* ── Suggestion cards — 9a: translucent surface, radius 16, padding 16/18,
   14.5px/1.5, 11px apart, 18px under the subtitle. Must outrank the base
   `div[data-testid="stButton"] > button` rule on specificity. ── */
[class*="st-key-sug_"] div[data-testid="stButton"] > button {{
    background-color: rgba(239,240,232,.045);
    border: 1px solid rgba(239,240,232,.12);
    border-radius: 16px;
    padding: 16px 18px;
    font-size: 14.5px;
    line-height: 1.5;
    margin-bottom: 9px; /* + 2px wrapper margin = the 9a 11px card gap */
}}
/* 31px nets the 9a 18px sub→card gap after the ~13px the invisible
   markdown-wrapper chrome swallows (measured live) */
.st-key-sug_0 div[data-testid="stButton"] > button {{ margin-top: 31px; }}
@media (hover: hover) {{
    [class*="st-key-sug_"] div[data-testid="stButton"] > button:hover {{
        background-color: rgba(163,174,110,.08);
        border-color: rgba(163,174,110,.5);
    }}
}}

/* suggestion cards stagger */
.st-key-sug_0 button {{ animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .24s; }}
.st-key-sug_1 button {{ animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .32s; }}
.st-key-sug_2 button {{ animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .4s; }}
.st-key-sug_3 button {{ animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .48s; }}

/* ── Composer — pill bar + circular olive send ── */
/* the composer strip is translucent glass over the fixed underlay: the old
   "bottom slice of the gradient" repaint was sized by svh, which lies on
   iOS standalone, so its colors met the page gradient at a visible step
   (user-circled seam above the composer). The tint's hue is the gradient's
   BOTTOM color and fades to nothing at the strip's top edge — invisible on
   home, frosts messages scrolling beneath it on the chat screen */
[data-testid="stBottom"] {{
    background: linear-gradient(180deg,
        rgba(32,39,15,0) 0%, rgba(32,39,15,.42) 45%, rgba(32,39,15,.6) 100%) !important;
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    /* env() is 0 inside the cloud shell's iframe, so give the disclaimer a
       real floor — on iPhone it sat right on the home-indicator bar */
    padding-bottom: max(14px, env(safe-area-inset-bottom, 0px));
}}
/* ...and in the BROWSER that tint has to follow the underlay down.
   The rule above is tuned to the home-screen underlay, whose bottom really is
   #20270F — the tint matches its ground and is invisible. Ending the browser
   underlay on the base colour (see body::before above, done so the page meets
   Safari's toolbar without a seam) left this strip painting rgba(32,39,15) at
   42-60% over #14170E, which composites 6.7-9.6/255 brighter than the page
   around it: a lighter block sitting exactly where the composer is. That is
   the glow left on the chat screen after the seam was closed, and it was
   caused by fixing the seam — the two rules are one system and only the first
   half got moved.
   Same colour as the browser underlay's floor, so the step is 0. The blur and
   the opacity are untouched, so it still frosts messages scrolling beneath. */
html:not(.cai-standalone) [data-testid="stBottom"] {{
    background: linear-gradient(180deg,
        rgba(20,23,14,0) 0%, rgba(20,23,14,.42) 45%, rgba(20,23,14,.6) 100%) !important;
}}
/* the inner wrappers must not paint their own (near-black) theme color
   over the gradient strip */
[data-testid="stBottom"] > div,
[data-testid="stBottomBlockContainer"] {{
    background: transparent !important;
}}
[data-testid="stBottomBlockContainer"] {{
    max-width: 560px; margin: 0 auto; padding: 0.9rem 18px 0.4rem 18px !important;
}}
[data-testid="stChatInput"] * {{
    background-color: transparent !important; border: none !important; box-shadow: none !important;
}}
[data-testid="stChatInput"] {{
    background-color: rgba(239,240,232,.06) !important; /* 9a translucent pill */
    border: 1px solid var(--border-strong) !important;
    /* 27px, not 99px: with one line (~54px tall) this IS the capsule, but a
       long pasted question grows the textarea and a 99px radius kept drawing
       a squeezed capsule while the text painted past the top edge (pilot
       video 2026-07-27). 27px stays a rounded card at any height. */
    border-radius: 27px !important;
    padding: 5px 6px 5px 5px !important; /* 9a: 5/6/5/5, text carries its own 14px inset */
    /* flex-end, not center: as the textarea grows the send arrow hugs the
       bottom edge (the Claude-app composer behavior) instead of floating */
    align-items: flex-end !important;
    transition: border-color .15s ease;
}}
/* the baseweb wrapper adds 12px 16px of its own — it ballooned the pill;
   zeroed + stretched to the FULL pill width (Streamlit leaves it at its
   intrinsic ~240px, which left the send button floating mid-pill — visible
   on iPhone and at any viewport), and the textarea side grows to fill so
   the send button hugs the far (left) edge */
[data-testid="stChatInput"] > div {{
    padding: 0 !important;
    min-height: 0 !important;
    width: 100% !important;
    flex: 1 1 auto !important;
    min-width: 0 !important;
}}
[data-testid="stChatInput"] > div > *:has(textarea),
[data-testid="stChatInput"] > div > textarea {{
    flex: 1 1 auto !important; min-width: 0 !important;
}}
/* "Press Enter to apply" hint occupies row space next to the send button on
   iOS — never show it inside the composer */
[data-testid="stChatInput"] [data-testid="InputInstructions"] {{ display: none !important; }}
[data-testid="stChatInput"]:focus-within {{ border-color: var(--accent-border) !important; }}
[data-testid="stChatInputTextArea"] {{
    color: var(--text) !important; font: 400 15px Heebo, sans-serif !important; direction: rtl;
    padding: 0 14px !important;
    /* a pasted multi-line question must scroll INSIDE the pill, not paint
       over the header (pilot video 2026-07-27): ~6 lines, then scroll */
    max-height: 132px !important;
    overflow-y: auto !important;
}}
[data-testid="stChatInput"] textarea::placeholder {{ color: rgba(239,240,232,.5) !important; }}
[data-testid="stChatInputSubmitButton"] {{
    background-color: var(--accent) !important;
    border-radius: 50% !important;
    width: 44px !important; height: 44px !important;
    min-width: 44px !important; min-height: 44px !important;
    flex: 0 0 auto !important;
    padding: 0 !important; border: none !important;
    box-shadow: 0 0 24px rgba(163,174,110,.35) !important; /* 9a glow */
}}
[data-testid="stChatInputSubmitButton"]:hover {{ background-color: var(--accent-hover) !important; }}
[data-testid="stChatInputSubmitButton"] svg {{ fill: #14170E !important; }}
/* disclaimer under the composer */
[data-testid="stBottomBlockContainer"]::after {{
    content: "כלי עזר מבוסס בינה מלאכותית — אינו ייעוץ משפטי או פקודה מחייבת. בכל סתירה, פקודות מטכ״ל הרשמיות הן הקובעות.";
    display: block; text-align: center; margin-top: 10px;
    line-height: 1.55; max-width: 460px; margin-inline: auto;
    /* 11.5px, not 10.5: this is read in daylight, and it is the disclaimer */
    font: 400 11.5px Heebo, sans-serif; color: var(--text-faint);
}}

/* ── "thinking" indicator (in-bubble, until the first answer token) ── */
@keyframes caiThinkPulse {{
    0%, 80%, 100% {{ opacity:.25; transform:scale(.62); }}
    40% {{ opacity:1; transform:scale(1); }}
}}
/* answer skeleton — the wait shows the SHAPE of the coming answer (verdict
   chip + three text lines) instead of a spinner; one shimmer sweep, RTL */
.cai-skel {{ display:block; direction:rtl; padding:4px 2px 2px; }}
.cai-skel .skc, .cai-skel .skr {{
    display:block;
    background:linear-gradient(270deg,
        rgba(236,237,230,.055) 25%, rgba(236,237,230,.14) 50%,
        rgba(236,237,230,.055) 75%);
    background-size:220% 100%;
    animation:caiSkelShimmer 1.4s infinite linear;
}}
.cai-skel .skc {{ width:96px; height:26px; border-radius:99px; margin:2px 0 13px; }}
.cai-skel .skr {{ height:13px; border-radius:7px; margin:9px 0; }}
.cai-skel .skr.w84 {{ width:84%; }}
.cai-skel .skr.w58 {{ width:58%; }}
@keyframes caiSkelShimmer {{
    0% {{ background-position:220% 0; }}
    100% {{ background-position:-220% 0; }}
}}
.cai-thinking {{
    display:flex; align-items:center; gap:10px; direction:rtl;
    padding:3px 2px 5px; color:var(--text-dim);
    font:500 13.5px Heebo, sans-serif;
}}
.cai-thinking .cai-think-dots {{ display:inline-flex; gap:5px; }}
.cai-thinking .cai-think-dots i {{
    width:6px; height:6px; border-radius:50%;
    background:var(--accent);
    animation:caiThinkPulse 1.15s infinite ease-in-out;
}}
.cai-thinking .cai-think-dots i:nth-child(2) {{ animation-delay:.16s; }}
.cai-thinking .cai-think-dots i:nth-child(3) {{ animation-delay:.32s; }}

/* ── Chat messages ── */
[data-testid="stChatMessage"] {{
    background-color: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 12px 16px;
    margin-bottom: 10px;
    direction: rtl;
}}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {{
    background-color: var(--accent-soft);
    border-color: var(--accent-border);
}}
/* avatars: recolor Streamlit's red/orange squares to theme tones */
[data-testid="stChatMessage"] [data-testid^="stChatMessageAvatar"] {{
    background-color: var(--accent-soft) !important;
    border: 1px solid var(--accent-border) !important;
    color: var(--accent) !important;
}}
[data-testid="stChatMessage"] [data-testid^="stChatMessageAvatar"] svg {{
    fill: var(--accent) !important;
}}

/* ── Hebrew (RTL) typography inside answers: right-aligned flow, modest
   heading sizes, bullets/numbers on the right, RTL tables and quotes ── */
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {{
    direction: rtl;
    text-align: right;
}}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p {{
    font-size: calc(15px * var(--cai-fs, 1)) !important;
    line-height: 1.65 !important;
    text-align: right;
}}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] li {{
    font-size: calc(15px * var(--cai-fs, 1)) !important;
}}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h1,
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h2,
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h3,
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h4 {{
    font-family: Heebo, sans-serif !important;
    font-size: calc(16px * var(--cai-fs, 1)) !important;
    font-weight: 700 !important;
    color: var(--text) !important;
    text-align: right !important;
    margin: 14px 0 6px !important;
    padding: 0 !important;
}}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] ul,
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] ol {{
    direction: rtl;
    text-align: right;
    padding-right: 1.3rem !important;
    padding-left: 0 !important;
    margin-right: 0 !important;
}}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] li {{
    text-align: right;
    font-size: 15px;
    line-height: 1.65;
    margin-bottom: 2px;
}}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] table {{
    direction: rtl;
    text-align: right;
    border-collapse: collapse;
    margin: 8px 0;
}}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] th,
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] td {{
    text-align: right !important;
    border: 1px solid var(--border) !important;
    padding: 6px 10px !important;
}}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] blockquote {{
    border-right: 3px solid var(--accent-border) !important;
    border-left: none !important;
    margin: 8px 0 8px auto !important;
    padding: 2px 12px 2px 0 !important;
    color: var(--text-sec);
}}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] hr {{
    border-color: var(--border) !important;
    margin: 12px 0 !important;
}}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] strong {{
    color: var(--text);
}}

/* ── Verdict chip — the **פסיקה:** bottom line (מותר / אסור / מוסמך /
   ...בתנאים) as a scannable pill at the top of the answer card, replacing
   the raw line. Colors are desaturated to sit inside the olive theme. ── */
.verdict-chip {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    direction: rtl;
    border: 1px solid;
    border-radius: 99px;
    padding: 4px 13px;
    font: 600 12.5px Heebo, sans-serif;
    letter-spacing: .01em;
    white-space: nowrap;
}}
.verdict-yes  {{ color:#A9C687; background:rgba(148,183,110,.13); border-color:rgba(148,183,110,.4); }}
.verdict-cond {{ color:#D9B36A; background:rgba(217,179,106,.12); border-color:rgba(217,179,106,.4); }}
.verdict-no   {{ color:#D68C77; background:rgba(208,124,102,.12); border-color:rgba(208,124,102,.4); }}
.verdict-none {{ color:rgba(236,237,230,.6); background:rgba(236,237,230,.05); border-color:rgba(236,237,230,.2); }}
/* two-sided ruling (conflict questions): chips stack vertically, aligned
   to the reading edge — each clause keeps its own colour */
.verdict-stack {{
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
    direction: rtl;
    /* Streamlit's stMarkdownContainer carries margin-bottom:-1rem (offsets a
       <p>'s 16px). The stack is a raw <div> — no <p>, nothing cancels it, and
       the NEXT markdown block climbed 16px onto the second chip (phone
       screenshot, 2026-07-27). Padding, not margin: margins collapse.
       16px cancel + the 11px seam. Was 22 (a 6px gap) until the answer
       language made every other seam 14px measured and this one the odd
       one out — same mechanism, one number now. */
    padding-bottom: 27px;
}}
/* A SINGLE chip has no wrapper of its own, so nothing in its markdown block
   cancels the container's margin-bottom:-1rem — the same defect .verdict-stack
   was given padding-bottom for. It went unnoticed while the next element was
   a <p>, which sat 0-3px under the chip and read as "attached"; the answer
   language put a bordered row there instead, and it overlapped the chip by
   5px (measured). 27 = the 16px cancel + the 11px seam every block uses. */
.verdict-solo {{ padding-bottom: 27px; }}
/* the "לא נמצא..." no-rule clause carries a short sentence, not a badge
   term — let it wrap to two lines instead of overflowing the bubble */
.verdict-wrap {{
    white-space: normal;
    line-height: 1.45;
    border-radius: 16px;
    text-align: right;
}}

/* ── שפת התשובה — הדקדוק שהפרומפט מכתיב, לבוש. ──
   הפירוק חי ב-answer_format.py (טהור ונבדק); כאן רק המראה. הצ'יפ שמעל היה
   המילה הראשונה בשפה הזו — כל השאר ירד עד היום כמרקדאון חשוף, במשטח שכל
   משתמש רואה בכל שאלה.

   ‏`.cai-ans` הוא מעטפת-מרווח בלבד, ו**ריפוד** ולא שוליים. המודל נמדד
   באפליקציה החיה ולא נגזר מהתיאוריה: ההורה הוא `stVerticalBlock` שהוא
   **flex**, ולכן שוליים בין בלוקים אינם קורסים כלל; ובתוך כל בלוק
   ‏`stMarkdownContainer` נושא `margin-bottom:-1rem` שמקזז את השוליים של ה-
   ‏`<p>` האחרון. מכאן הנוסחה שנמדדה: מרווח = (ריפוד-תחתון של הקודם − 16) +
   ריפוד-עליון של הבא + 3. פסקה תורמת 0 בשני הכיוונים (16 השוליים שלה
   נאכלים), ולכן בלוק חייב 27 מלמטה כדי לתת 14 לפני פסקה — אבל אז שני בלוקים
   עוקבים היו מקבלים 25. משם הכלל הבא: בלוק שיושב אחרי בלוק אחר משלנו מוותר
   על הריפוד העליון שלו. בלי `:has` (מנוע ישן) התוצאה 25px — רווח, לא שבר.
   הקופסה המעוצבת יושבת בפנים, אחרת רקע או מסגרת היו נמתחים מתחת לטקסט שלהם.
   כל גופן נכפל ב---cai-fs כדי לכבד את הגדרת גודל-הטקסט. ── */
.cai-ans {{ direction: rtl; text-align: right; padding: 11px 0 27px; }}
[data-testid="stElementContainer"]:has(.cai-ans, .verdict-solo, .verdict-stack)
    + [data-testid="stElementContainer"] .cai-ans {{ padding-top: 0; }}
/* תווית שאין לה ערך היא כותרת לרשימה שמיד אחריה — נצמדת אליה (‏4px נמדדים) */
.cai-ans-solo {{ padding-bottom: 14px; }}

/* שורת-מקור. כפתור "הצג סעיף מקור" נשאר מתחת לתשובה — השורה הזו היא התווית
   הקריאה שלו, לא מתחרה בו על אותה פעולה. */
.cai-ans-src {{ display: flex; flex-direction: column; gap: 6px; }}
.cai-ans-src .r {{ display: flex; align-items: center; gap: 8px;
    border: 1px solid var(--accent-border); background: var(--accent-soft);
    border-radius: 10px; padding: 7px 10px; }}
.cai-ans-src .ic {{ flex: 0 0 16px; width: 16px; height: 16px; color: var(--accent); }}
.cai-ans-src .ic svg {{ width: 100%; height: 100%; display: block; }}
.cai-ans-src .t {{ flex: 1; min-width: 0;
    font: 500 calc(12.5px * var(--cai-fs, 1)) Heebo, sans-serif; color: var(--text); }}
.cai-ans-src .c {{ flex: 0 0 auto; white-space: nowrap;
    font: 500 calc(11px * var(--cai-fs, 1)) Heebo, sans-serif; color: var(--accent);
    border: 1px solid var(--accent-border); border-radius: 5px; padding: 1px 6px; }}

/* תווית תלויה — מה שהיה `**תנאים:**` מודגש בתוך הזרימה */
.cai-ans-f {{ display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }}
.cai-ans-f .l, .cai-ans-lead .l, .cai-ans-route .l {{
    font: 600 calc(11px * var(--cai-fs, 1)) Heebo, sans-serif;
    color: var(--text-faint); letter-spacing: .02em; }}
.cai-ans-f .v {{ flex: 1; min-width: 140px;
    font: 400 calc(15px * var(--cai-fs, 1)) Heebo, sans-serif;
    color: var(--text); line-height: 1.65; }}

/* פתיח — שורת פסיקה שהצ'יפ סירב לצבוע (פסיקה מורכבת), או **תשובה:** בשאלה
   עובדתית שאין לה צ'יפ כלל */
.cai-ans-lead {{ display: flex; flex-direction: column; gap: 2px; }}
.cai-ans-lead .v {{ font: 500 calc(15.5px * var(--cai-fs, 1)) Heebo, sans-serif;
    color: var(--text); line-height: 1.6; }}

/* עימות דו-צדדי — שני הצדדים כבלוקים מזווגים. בלי צבע: הצ'יפים שמעל כבר
   נושאים את ההכרעה הצבועה לכל צד, וצבע כאן היה מקודד אותה פעמיים */
.cai-ans-side {{ display: flex; flex-direction: column; gap: 3px;
    border-right: 2px solid var(--border); padding-right: 10px; }}
.cai-ans-side .p {{ font: 600 calc(12.5px * var(--cai-fs, 1)) Heebo, sans-serif; color: var(--text); }}
.cai-ans-side .v {{ font: 400 calc(14px * var(--cai-fs, 1)) Heebo, sans-serif;
    color: var(--text-sec); line-height: 1.6; }}

/* קולאאוט שקט — "מה הפקודות לא קובעות" והערות. שקט בכוונה: זו הסתייגות,
   לא אזהרה, ומשפחת צבעי-האזהרה שמורה למצבים שדורשים פעולה */
.cai-ans-note {{ display: flex; gap: 8px; align-items: flex-start;
    background: rgba(239,240,232,.04); border-radius: 8px; padding: 8px 10px; }}
.cai-ans-note .ic {{ flex: 0 0 auto; color: var(--text-faint);
    font-size: calc(13px * var(--cai-fs, 1)); line-height: 1.5; }}
.cai-ans-note .v {{ font: 400 calc(13px * var(--cai-fs, 1)) Heebo, sans-serif;
    color: var(--text-sec); line-height: 1.55; }}

/* ניתוב — "לא נקבע בפקודות מטכ\"ל" / "טרם במאגר". זה הרגע שבו התשובה מפנה
   הלאה במקום להכריע, והוא הכי שכיח שיש (מדידה עיוורת 10.08: 12 מ-16 השאלות
   שלא נענו). השברון הכפול של המותג בגרסה מעומעמת מסמן אותו כמצב מתוכנן */
.cai-ans-route {{ display: flex; gap: 10px; align-items: flex-start;
    border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; }}
.cai-ans-route .bd {{ display: flex; flex-direction: column; gap: 3px; min-width: 0; }}
.cai-ans-route .v {{ font: 400 calc(14px * var(--cai-fs, 1)) Heebo, sans-serif;
    color: var(--text); line-height: 1.6; }}
.cai-ans-chev {{ display: flex; flex-direction: column; align-items: center;
    flex: 0 0 auto; padding-top: 4px; }}
.cai-ans-chev span {{ display: block; width: 9px; height: 9px;
    border-top: 2px solid rgba(163,174,110,.55); border-left: 2px solid rgba(163,174,110,.55);
    transform: rotate(45deg); }}
.cai-ans-chev span + span {{ border-color: rgba(163,174,110,.25); margin-top: -3px; }}

/* ── Escalation strip — "למי פונים": one quiet line between the answer
   body and the action pills (deterministic lookup, see escalation_paths.py
   — general guidance, not part of the ruling). Label and chain share a
   single NOWRAP row that scrolls horizontally — the old numbered pills
   wrapped into a mess next to the wrapped action row on phones. ── */
/* padding-bottom 26 = the theme's stMarkdownContainer margin-bottom:-16px
   (every next element starts 16px INTO a markdown block — invisible under
   plain text, but it swallowed this strip's note under the pills iframe)
   + 10px of real breathing room. Padding, not margin: margins collapse
   through the wrapper and lose to its !important rules. */
.cai-escal {{ direction: rtl; text-align: right; margin: 10px 0 0; padding-bottom: 26px; }}
.cai-escal-row {{
    display: flex; align-items: center; gap: 7px;
    flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none;
}}
.cai-escal-row::-webkit-scrollbar {{ display: none; }}
.cai-escal-title {{
    font: 600 12px Heebo, sans-serif; color: var(--text-faint);
    white-space: nowrap; flex: 0 0 auto;
}}
.cai-escal-step {{
    background: rgba(236,237,230,.06); color: rgba(236,237,230,.8);
    border-radius: 8px; padding: 3px 10px; flex: 0 0 auto;
    font: 500 12px Heebo, sans-serif; white-space: nowrap;
}}
/* the arrow points LEFT: in RTL flow the next step sits to the left */
.cai-escal-sep {{ color: var(--text-faint); font-size: 11px; flex: 0 0 auto; }}
.cai-escal-note {{
    font: 400 11px Heebo, sans-serif; color: var(--text-faint);
    margin-top: 5px; line-height: 1.5;
}}

/* ── "הצג סעיף מקור" button — native (opens the in-app clause dialog, so
   it can reach Python, unlike the iframe pills). Styled to read as the
   trust/verify CTA: solid-ish outline, sits just under the answer. ── */
[class*="st-key-src_"] {{ margin: 2px 0 4px; }}
[class*="st-key-src_"] button {{
    background: var(--accent-soft) !important;
    border: 1px solid var(--accent) !important;
    color: var(--accent) !important;
    border-radius: 99px !important;
    min-height: 0 !important; width: auto !important;
    padding: 4px 15px !important;
}}
[class*="st-key-src_"] button p {{ font: 600 12.5px Heebo, sans-serif !important; }}
[class*="st-key-src_"] button:hover {{ background: var(--accent) !important; color: #14170E !important; }}
[class*="st-key-src_"] button:hover p {{ color: #14170E !important; }}
/* install-as-app hint (drawer expander) */
.cai-install-hint {{
    font: 400 12px/1.8 Heebo, sans-serif; color: var(--text-dim);
    direction: rtl; text-align: right;
}}

/* ── Section gaps — Streamlit's default 16px block gap balloons the
   card list; the design wants tight 10-12px rhythm (buttons carry their
   own 12px margin) ── */
[data-testid="stVerticalBlock"] {{ gap: 0 !important; }}
[data-testid="stVerticalBlock"] > div {{ margin-bottom: 0.1rem; }}
.stMarkdown {{ margin-bottom: 0.1rem !important; }}

/* ── Sidebar (drawer) ── */
[data-testid="stSidebar"] {{
    background-color: var(--bg);
    border-left: 1px solid rgba(236,237,230,.1);
}}
/* Streamlit's slide animation breaks under RTL: its max-width/transform
   transitions get stuck mid-flight, freezing the drawer as a squeezed
   sliver of vertical text. Kill the transitions and pin each state:
   closed is fully hidden; open is taken out of the flex flow entirely and
   rendered as a fixed overlay drawer from the right (78vw, max 340px —
   per the design spec), so no flex math can ever squeeze it again. */
[data-testid="stSidebar"] {{ transition: none !important; }}
/* open (or aria attribute missing — Streamlit's mobile mode drops it):
   fixed overlay from the right, out of the flex flow entirely */
[data-testid="stSidebar"]:not([aria-expanded="false"]) {{
    position: fixed !important;
    top: 0 !important; bottom: 0 !important;
    right: 0 !important; left: auto !important;
    height: 100dvh !important;
    width: min(78vw, 340px) !important;
    min-width: min(78vw, 340px) !important;
    max-width: 340px !important;
    transform: none !important;
    visibility: visible !important;
    z-index: 999980 !important;
    border-left: 1px solid rgba(236,237,230,.1) !important;
    box-shadow: -12px 0 40px rgba(0,0,0,.45);
}}
[data-testid="stSidebar"]:not([aria-expanded="false"]) > div {{
    width: 100% !important;
    min-width: 0 !important;
}}
/* explicitly collapsed */
[data-testid="stSidebar"][aria-expanded="false"] {{ display: none !important; }}
/* collapsed on builds that drop the aria attribute: the hamburger
   (expand) button only exists while the drawer is closed, so its mere
   presence means the sidebar must be fully hidden — no 25px sliver */
body:has([data-testid="stExpandSidebarButton"]) [data-testid="stSidebar"] {{ display: none !important; }}
[data-testid="stSidebar"] * {{ text-align: right; }}
[data-testid="stSidebar"] div[data-testid="stButton"] > button {{
    border-radius: 12px; padding: 13px 16px; font-weight: 600;
}}
/* compact drawer chrome: small 34px close button, tight top padding,
   content pinned so "+ שיחה חדשה" sits at the drawer bottom */
[data-testid="stSidebarHeader"] {{ padding: calc(max(env(safe-area-inset-top, 0px), var(--cai-sat, 0px)) + 12px) 16px 0 !important; }}
[data-testid="stSidebarCollapseButton"] {{ width: 34px !important; height: 34px !important; border-radius: 9px !important; }}
[data-testid="stSidebarUserContent"] {{ padding: 6px 20px 24px !important; }}
[data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] {{
    min-height: calc(100dvh - 110px);
}}
.st-key-new_chat {{ margin-top: auto !important; }}
[data-testid="stSidebar"] [data-testid="stLayoutWrapper"] {{
    background: transparent !important; border: none !important;
}}
[data-testid="stSidebar"] hr {{ margin: 14px 0 !important; }}

.cai-drawer-role {{ font: 400 12.5px Heebo, sans-serif; color: var(--text-dim); margin-bottom: 10px; }}
.cai-drawer-section {{
    display: flex; align-items: center; gap: 8px;
    font: 600 13.5px Heebo, sans-serif; color: var(--accent); margin: 4px 0 6px;
}}
.cai-drawer-section .dot {{ width: 13px; height: 13px; border: 1.5px solid var(--accent); border-radius: 50%; display: inline-block; }}
[data-testid="stSidebar"] hr {{ border-color: rgba(236,237,230,.1) !important; margin: 20px 0 !important; }}

/* ── Status tick-chips (סטטוס) — the four personal statuses that unlock
   entitlement calculations. Boxes you tick on a form, and what you tick is
   printed on the service card above: NOT toggle switches (a switch reads as a
   preference you flip often; these are facts you set once and never revisit)
   and NOT a third grouped card, which turned the screen monotonous right where
   the eye should still be holding onto the hero.

   The selected state is carried by a SOLID accent square. Measured in this
   palette: accent-soft over the chip composites to #383E27 against an
   unselected #262C1C = 1.29:1, and over the bare ground 1.00:1 — so the fill
   the previous rule leaned on as its state signal was invisible. The solid
   square is 6.05:1 on the chip and 7.63:1 on the ground (WCAG 1.4.11 asks for
   >= 3:1 on non-text state). On four booleans that release money, a tick that
   missed must not look like one that landed.

   Geometry is deliberately identical in both states — no weight bump, no
   padding change — so ticking one can never re-wrap the row under the thumb. */
.cai-profile-label {{ font: 400 12.5px Heebo, sans-serif; color: var(--text-dim); margin: 2px 0 4px; }}
/* never flex/grid stButtonGroup itself: it also holds the collapsed <label>,
   the same trap that stacked the service-type control into a 107px column */
.st-key-profile_statuses [data-testid="stButtonGroup"] {{ width: 100% !important; display: block !important; }}
.st-key-profile_statuses [role="group"] {{
    width: 100% !important; direction: rtl;
    display: flex !important; flex-wrap: wrap !important;
    gap: 8px !important; justify-content: flex-start !important;
}}
.st-key-profile_statuses button {{
    min-height: 44px !important;          /* was 32px — under the thumb floor */
    width: auto !important;
    padding: 0 13px 0 14px !important;
    border-radius: 12px !important;
    background: transparent !important;
    border: 1px solid rgba(239,240,232,.12) !important;
    box-shadow: none !important;
    display: flex !important; align-items: center !important;
    -webkit-tap-highlight-color: transparent;
    transition: background-color .13s linear, border-color .13s linear;
}}
/* the tick box. ::before is the RIGHTMOST child of an RTL flex row, so the box
   sits at the reading edge beside its own label instead of stranded at the far
   end of the chip. Physical margin-left on purpose — a stray direction:ltr
   must not flip it back through the label. */
.st-key-profile_statuses button::before {{
    content: ""; flex: none; width: 17px; height: 17px;
    border-radius: 5px; margin-left: 9px;
    border: 1.5px solid rgba(239,240,232,.40); background: transparent;
    transition: background-color .13s linear, border-color .13s linear;
}}
.st-key-profile_statuses button p {{
    font: 500 14px Heebo, sans-serif !important;
    color: rgba(239,240,232,.72) !important; margin: 0 !important; white-space: nowrap;
}}
/* hover only where hovering exists — the previous unguarded :hover left an
   accent border and accent label stuck on an untapped chip after an iOS tap */
@media (hover: hover) {{
    .st-key-profile_statuses button:hover {{ border-color: rgba(var(--accent-rgb),.5) !important; }}
}}
.st-key-profile_statuses button[data-testid="stBaseButton-pillsActive"] {{
    background: rgba(var(--accent-rgb),.14) !important;
    border-color: var(--accent) !important;
}}
.st-key-profile_statuses button[data-testid="stBaseButton-pillsActive"] p {{ color: var(--accent-bright) !important; }}
.st-key-profile_statuses button[data-testid="stBaseButton-pillsActive"]::before {{
    background: var(--accent) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='11' height='11' viewBox='0 0 12 12'%3E%3Cpath d='M2.5 6.3 L5 8.6 L9.5 3.4' fill='none' stroke='%2314170E' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") center / 11px no-repeat;
    border-color: var(--accent);
}}
/* the register line: one element that either explains that ticking nothing is
   fine, or reads back what is now printed on the card. .has-marks is set by
   pfSync — not :has() — because pfSync already knows the answer. */
.cai-pf-reg {{
    font: 500 12px/1.4 Heebo, sans-serif; color: rgba(239,240,232,.55);
    margin: 0 2px 11px; min-height: 17px; text-align: right;
}}
.cai-pf-reg .p {{ display: none; }}
.cai-pf-reg b {{ font-weight: 700; color: var(--accent-bright); }}
.st-key-cai_pf_status.has-marks .cai-pf-reg .z {{ display: none; }}
.st-key-cai_pf_status.has-marks .cai-pf-reg .p {{ display: inline; }}
.st-key-cai_pf_status {{ margin-bottom: 6px; }}
/* what the ticks put on the card */
.cai-svc-marks {{ color: var(--accent); font-weight: 500; }}

/* ── Letters dialog — the modal portals outside the chat column, so the
   app-wide RTL/font treatment doesn't reach it ── */
div[data-testid="stDialog"] > div {{ direction: rtl; }}
div[data-testid="stDialog"] textarea {{ direction: rtl; font: 400 14px/1.7 Heebo, sans-serif !important; }}

/* new-chat: solid olive, pinned look */
.st-key-new_chat button {{
    background-color: var(--accent) !important;
    border: none !important;
    color: #14170E !important;
    font: 700 15px Heebo, sans-serif !important;
    text-align: center !important;
    justify-content: center;
    /* measured 43px on-device widths — one pixel under the thumb floor, and
       this is the drawer's primary action */
    min-height: 44px !important;
}}
.st-key-new_chat button:hover {{ background-color: var(--accent-hover) !important; }}
.st-key-new_chat button p {{ color: #14170E !important; font-weight: 700 !important; text-align: center !important; }}

/* ── Expander (loaded orders) — flat row with count, no theme boxes ── */
[data-testid="stExpander"],
[data-testid="stExpander"] details,
[data-testid="stExpander"] summary,
[data-testid="stExpanderDetails"] {{
    background-color: transparent !important;
    background: transparent !important;
    border: none !important;
    border-radius: 0 !important;
    box-shadow: none !important;
}}
[data-testid="stExpander"] summary {{ color: var(--text) !important; font: 500 14.5px Heebo, sans-serif !important; padding: 10px 4px !important; }}
[data-testid="stExpander"] summary:hover {{ color: var(--accent) !important; }}
[data-testid="stExpander"] summary svg {{ fill: rgba(239,240,232,.4) !important; }}
/* only the orders list scrolls (capped like the design), not the drawer */
[data-testid="stExpanderDetails"] {{
    padding: 0 !important;
    max-height: 300px;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: rgba(239,240,232,.25) transparent;
}}
[data-testid="stExpanderDetails"]::-webkit-scrollbar {{ width: 5px; }}
[data-testid="stExpanderDetails"]::-webkit-scrollbar-thumb {{
    background: rgba(239,240,232,.25); border-radius: 3px;
}}

/* ── Loaded orders: each title IS the tap target that opens its PDF
   inline — styled as a flat list line (olive right rule, dim text) ── */
.cai-order-link {{
    /* flex, not a clipped block: with ellipsis on the whole row, a long title
       pushed the date badge past the clip edge — 11 of 20 badges were simply
       invisible (2026-08-03 audit). Now the title alone truncates and the
       badge, flex:none at the row end, always survives. */
    display: flex;
    align-items: baseline;
    gap: 6px;
    border-right: 2px solid var(--accent-border);
    color: rgba(239,240,232,.65) !important;
    font: 400 13px Heebo, sans-serif;
    text-align: right;
    text-decoration: none !important;
    padding: 7px 10px;
    margin: 0 8px 2px 0;
    direction: rtl;
    transition: color .15s ease, border-color .15s ease;
}}
.cai-order-tt {{
    flex: 0 1 auto;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}}
a.cai-order-link:hover {{
    color: var(--text) !important;
    border-right-color: var(--accent);
}}
/* freshness badge — the order's own version date, so "how current is
   this?" is answered in the list itself */
.cai-order-date {{
    font: 400 10.5px Heebo, sans-serif;
    color: var(--text-faint);
    flex: none;
    white-space: nowrap;
}}
/* orders search field — translucent pill matching the 9a drawer (rescoped
   from the dead [data-testid="stSidebar"] to the app-owned drawer) */
.st-key-cai_drawer [data-testid="stTextInput"] {{ margin: 4px 8px 8px 0; }}
.st-key-cai_drawer [data-testid="stTextInput"] div[data-baseweb="input"],
.st-key-cai_drawer [data-testid="stTextInput"] div[data-baseweb="base-input"] {{
    background-color: rgba(239,240,232,.045) !important;
    border: 1px solid var(--border-strong) !important;
    border-radius: 10px !important;
}}
.st-key-cai_drawer [data-testid="stTextInput"] div[data-baseweb="base-input"] {{ border: none !important; }}
.st-key-cai_drawer [data-testid="stTextInput"] input {{
    background-color: transparent !important;
    color: var(--text) !important;
    font: 400 13px Heebo, sans-serif !important;
    direction: rtl;
    padding: 8px 12px !important;
}}
.st-key-cai_drawer [data-testid="stTextInput"] input::placeholder {{
    color: rgba(239,240,232,.5) !important;
}}

/* ── Caption / small text ── */
.stCaption, small {{ color: var(--text-faint) !important; font-size: 0.8rem !important; }}

/* ── Spinner ── */
.stSpinner > div {{ border-top-color: var(--accent) !important; }}

/* ── Focus & disabled ── two states the app never defined.

   FOCUS: there was no :focus-visible rule anywhere, so keyboard and iOS
   Switch Control users navigated on whatever the UA happened to draw over a
   dark olive surface. :focus-visible (not :focus) is the point — it fires for
   keyboard/AT traversal and NOT for touch or mouse, so the ring never appears
   under a thumb. Paired with the existing -webkit-tap-highlight-color:
   transparent, which removed the touch affordance on four controls.

   DISABLED: the only "disabled" match in the file was a calendar gridcell
   selector, so a disabled control was indistinguishable from a live one —
   it just silently did nothing when pressed. */
:where(button, [role="button"], a, input, textarea, select,
       [tabindex]:not([tabindex="-1"])):focus-visible {{
    outline: 2px solid var(--accent) !important;
    outline-offset: 2px !important;
    border-radius: 6px;
}}
/* the composer is a pill with its own focus-within treatment — a rectangular
   ring around it would fight the capsule */
[data-testid="stChatInput"] textarea:focus-visible {{ outline: none !important; }}

button:disabled, button[disabled], [aria-disabled="true"] {{
    opacity: .42 !important;
    cursor: not-allowed !important;
    filter: grayscale(.35);
}}
button:disabled *, button[disabled] * {{ pointer-events: none; }}


/* ── Accessibility: honor prefers-reduced-motion — animations jump straight
   to their end state (splash still ends offscreen thanks to fill:both).
   transition-duration belongs here too: the block used to reset animations
   only, so all 21 `transition:` declarations (drawer slide, modal moves,
   border fades) still ran at full speed for a user who had explicitly asked
   the OS for less motion. ── */
@media (prefers-reduced-motion: reduce) {{
    * {{
        animation-duration: .01ms !important; animation-delay: 0s !important;
        transition-duration: .01ms !important; transition-delay: 0s !important;
        scroll-behavior: auto !important;
    }}
}}
</style>
""", unsafe_allow_html=True)

# ── Remove the Streamlit Cloud viewer badges (crown pill / creator avatar)
# on every screen. Their class hashes change each platform build, so CSS
# selectors rot. Four independent detection layers, because the platform
# has moved the badge between plain DOM, shadow DOM and iframes across
# builds: (1) links to streamlit.io/streamlit.app — the app itself never
# renders those; (2) the same links inside shadow roots, where neither CSS
# nor a plain querySelectorAll reaches, so the shadow *host* is hidden;
# (3) platform iframes (ours are srcdoc-only and have no external src);
# (4) positional last resort — any small fixed box glued to the viewport's
# bottom corner mounted directly on <body>, where the app mounts nothing. ──
components.html(
    """<script>
    // ── Shell escape: when the app is embedded in the platform shell, bounce
    // the top window to the direct /~/+/ frame — the shell's white page (and
    // its badge/scroll quirks) disappears entirely, and the app runs exactly
    // like the re-added PWA. This iframe's sandbox lacks allow-top-navigation
    // (verified live: allow-forms/modals/popups/same-origin/scripts/downloads
    // only), so navigating window.top from HERE throws — instead inject a
    // <script> into the shell document itself (allow-same-origin permits DOM
    // writes; inline scripts pass the shell's CSP — verified live) and let it
    // redirect from the shell's own unsandboxed context. Guards: only when an
    // extra shell layer exists (top !== parent; local and direct loads no-op),
    // idempotent by element id, and never when the top URL carries a query
    // (?admin=1 and debug flows).
    try {
        if (window.top !== window.parent && !window.top.location.search) {
            const tdoc = window.top.document;
            if (!tdoc.getElementById('cai-shell-escape')) {
                const esc = tdoc.createElement('script');
                esc.id = 'cai-shell-escape';
                esc.textContent = 'location.replace(' + JSON.stringify(window.parent.location.href) + ');';
                tdoc.head.appendChild(esc);
            }
        }
    } catch (e) {}
    // On Streamlit Cloud the app itself runs inside an iframe of a platform
    // shell page (same *.streamlit.app origin), and the viewer badges are
    // mounted on the SHELL document — one level above window.parent. Sweep
    // every same-origin ancestor document up to window.top; local runs have
    // parent === top, so this collapses to the old single-document behavior.
    const HIDE = el => el && el.style && el.style.setProperty('display', 'none', 'important');
    const BADGE_SEL = 'a[href*="streamlit.io"], a[href*="streamlit.app"], [class*="viewerBadge"], [class*="profileContainer"], [class*="profilePreview"]';
    const contexts = [];
    let w = window.parent;
    for (let hops = 0; hops < 5; hops++) {
        try { if (w.document && w.document.body) contexts.push(w); } catch (e) { break; } // cross-origin: stop
        if (w === w.parent) break;
        w = w.parent;
    }
    const sweep = (root, win) => {
        const doc = win.document;
        root.querySelectorAll(BADGE_SEL).forEach(el => {
            HIDE(el);
            // also hide its body-level container, unless that would take the app down with it
            let n = el;
            while (n.parentElement && n.parentElement !== doc.body) n = n.parentElement;
            if (n.parentElement === doc.body && !n.querySelector('[data-testid="stApp"]') && !n.querySelector('iframe')) HIDE(n);
        });
        root.querySelectorAll('iframe[src*="streamlit.io"], iframe[src*="share.streamlit"]').forEach(HIDE);
        root.querySelectorAll('*').forEach(el => {
            if (!el.shadowRoot) return;
            if (el.shadowRoot.querySelector(BADGE_SEL) && !el.querySelector('[data-testid="stApp"]') && !el.querySelector('iframe')) {
                HIDE(el);
            } else {
                sweep(el.shadowRoot, win);
            }
        });
    };
    const killBadges = () => contexts.forEach(win => {
        const doc = win.document;
        sweep(doc, win);
        // positional last resort: small fixed boxes glued to the bottom
        // corner, mounted on <body>. Never touch anything that contains the
        // app (stApp locally, the app iframe on the platform shell).
        Array.from(doc.body.children).forEach(el => {
            if (el.querySelector && (el.querySelector('[data-testid="stApp"]') || el.querySelector('iframe'))) return;
            if (win.getComputedStyle(el).position !== 'fixed') return;
            const r = el.getBoundingClientRect();
            if (r.height > 0 && r.height < 140 && r.width < 300 && win.innerHeight - r.bottom < 60) HIDE(el);
        });
    });
    // Darken every same-origin ancestor document (the cloud shell page is
    // WHITE by default — it's what shows through on iOS rubber-band
    // overscroll and as white gaps/dividers while scrolling the PWA).
    // Idempotent per document; same color as the app backdrop.
    const darkenShell = () => contexts.forEach(win => {
        try {
            const doc = win.document;
            if (doc.getElementById('cai-shell-dark')) return;
            const s = doc.createElement('style');
            s.id = 'cai-shell-dark';
            s.textContent = 'html,body{background:#14170E !important;margin:0;overscroll-behavior:none;}' +
                            'iframe{background:#14170E;}';
            doc.head.appendChild(s);
        } catch (e) {}
    });
    killBadges();
    darkenShell();
    setInterval(() => { killBadges(); darkenShell(); }, 1000);
    </script>""",
    height=0,
)


# ── Drawer gesture engine (swipe + tap, all client-side) ──
# Everything about opening and closing the drawer happens in the browser: the
# panel and the backdrop are always in the DOM (see the drawer section below)
# and one class on <html> decides whether they are on screen. That is the
# whole point — the server never hears about it, so nothing repaints. The old
# server-state version reran the script on every open AND every close, and on
# device that reads as the entire app reloading each time the menu moves
# (2026-07-27 phone video).
#
#   SWIPE OPEN   — start on the right edge (RTL: the drawer's own side) and
#                  drag inward; the real panel is pinned to the finger 1:1.
#   SWIPE CLOSE  — drag it back out from anywhere over the panel/backdrop.
#                  Either way, releasing past the commit point hands over to
#                  the CSS transition, which finishes the throw from wherever
#                  the finger left it; short of it, it springs back.
#   TAPS         — the hamburger, the « button and the backdrop are ordinary
#                  st.buttons (they keep their styling and their place in the
#                  layout), but their clicks are swallowed in the CAPTURE
#                  phase before React ever sees them, so a tap toggles the
#                  class instead of triggering a rerun.
#   ACTION TAPS  — "שיחה חדשה" and a conversation row DO rerun (they change
#                  real state); those are closed client-side first, so the
#                  panel is already gone when the rerun lands.
#   SAFETY NET   — if the hamburger is not in the DOM the drawer cannot be
#                  open (entry screen, name gate, after a logout), so the
#                  class is force-cleared. This is what replaces every
#                  drawer_open=False the server used to do.
#
# Serialized into a real <script> on the app document for the same reason as
# the viewport engine above: anything registered from a component iframe dies
# when Streamlit replaces that iframe on the next rerun.
components.html(
    r"""<script>
    var swipeFn = function () {
        if (window.__caiSwipe) return;
        window.__caiSwipe = true;
        var doc = document, root = doc.documentElement;
        var EDGE = 30;   // px of right edge that arms the open gesture
        var EDGE_BACK = 44;  // ditto for "back" while an overlay is up
        var SLOP = 12;   // px before the gesture commits to an axis

        var drawer = function () { return doc.querySelector(".st-key-cai_drawer"); };
        var backdrop = function () { return doc.querySelector(".st-key-drawer_backdrop"); };
        var hamburger = function () { return doc.querySelector(".st-key-drawer_open_btn button"); };
        var isOpen = function () { return root.classList.contains("cai-drawer-open"); };
        var W = function () {
            var d = drawer();
            var w = d ? d.getBoundingClientRect().width : 0;
            return w || Math.min(window.innerWidth * 0.85, 320);
        };
        // hand the panel back to CSS: drop the inline transform and the drag
        // class in the same frame, so the transition picks the throw up from
        // wherever the finger let go instead of jumping
        var settle = function (open) {
            var d = drawer(), b = backdrop();
            root.classList.toggle("cai-drawer-open", !!open);
            root.classList.remove("cai-drawer-drag");
            if (d) d.style.transform = "";
            if (b) b.style.opacity = "";
        };

        // ── the BACK target ──
        // Anything the user "went into" answers the same right-edge swipe that
        // every iOS app answers: settings and its four sub-screens, and the
        // tool dialogs. Both are overlays the server owns, so the gesture can
        // only move them — the navigation itself is the button they already
        // have, pressed for them.
        //
        // Returns the panel to drag and the control to press, or null when
        // nothing is open (then the drawer gestures own the screen).
        var backTarget = function () {
            var dlg = doc.querySelector('[data-testid="stDialog"]');
            if (dlg) {
                var close = dlg.querySelector('button[aria-label="Close"]');
                if (close) return { panel: dlg.querySelector('[role="dialog"]') || dlg, btn: close };
            }
            var set = doc.querySelector(".st-key-cai_settings");
            if (set) {
                var back = doc.querySelector(".st-key-settings_back button");
                if (back) return { panel: set, btn: back };
            }
            return null;
        };

        // a live drag ends in a click on whatever was under the finger.
        // Swallow the next real one — isTrusted keeps synthetic clicks alive.
        var swallowClick = function () {
            var kill = function (ev) {
                if (!ev.isTrusted) return;
                ev.stopPropagation();
                ev.preventDefault();
            };
            doc.addEventListener("click", kill, true);
            setTimeout(function () { doc.removeEventListener("click", kill, true); }, 400);
        };

        // ── swipe ──
        var g = null;   // the active gesture

        doc.addEventListener("touchstart", function (e) {
            g = null;
            if (e.touches.length !== 1) return;
            var t0 = e.touches[0];
            // never steal a caret drag inside the composer or a form field
            if (t0.target && t0.target.closest &&
                t0.target.closest("input, textarea, [contenteditable]")) return;
            // an overlay owns the screen: the same edge swipe means "back".
            // Edge-armed like iOS, and a little wider than the drawer's 30 —
            // nothing else lives on that strip while an overlay is up, and the
            // panels underneath scroll vertically, which still wins on dy.
            var bt = backTarget();
            if (bt) {
                if (t0.clientX >= window.innerWidth - EDGE_BACK) {
                    g = { mode: "back", x: t0.clientX, y: t0.clientY, live: false,
                          travel: 0, w: Math.max(1, bt.panel.getBoundingClientRect().width),
                          slop: SLOP, panel: bt.panel, btn: bt.btn };
                }
                return;
            }
            if (!hamburger()) return;                                    // no drawer on this screen
            var t = e.touches[0];
            if (isOpen()) {
                // a touch that STARTS on a tap target inside the panel (the
                // orders card, a tool button, an order link) is almost always
                // a tap, and a thumb tap rolls 10-20px sideways as it lifts.
                // At the flat 12px slop that roll went live as a micro-drag:
                // the panel jerked with the finger, sprang back, and the tap's
                // click died (no click after a prevented touchmove, and
                // swallowClick kills any straggler) — the 2026-07-28 "פקודות
                // מטכ"ל goes crazy and won't open" report, reproduced in DOM.
                // 30px still fires a real swipe (travel is 60px+) but no tap
                // reaches it; bare panel surface keeps the snappy 12.
                var onTap = t.target && t.target.closest &&
                    t.target.closest(".st-key-cai_drawer") &&
                    t.target.closest("button, a");
                g = { mode: "close", x: t.clientX, y: t.clientY, live: false,
                      travel: 0, w: W(), slop: onTap ? 30 : SLOP };
            } else if (t.clientX >= window.innerWidth - EDGE) {
                g = { mode: "open", x: t.clientX, y: t.clientY, live: false,
                      travel: 0, w: W(), slop: SLOP };
            }
        }, { passive: true, capture: true });

        doc.addEventListener("touchmove", function (e) {
            if (!g || !e.touches.length) return;
            var t = e.touches[0], dx = t.clientX - g.x, dy = t.clientY - g.y;
            if (!g.live) {
                if (Math.abs(dx) < g.slop) { if (Math.abs(dy) > SLOP) g = null; return; }
                if (Math.abs(dy) > Math.abs(dx)) { g = null; return; }         // vertical scroll wins
                // "close" travels right; "open" and "back" both travel left
                if (g.mode === "close" ? dx < 0 : dx > 0) { g = null; return; }
                if (g.mode !== "back" && !drawer()) { g = null; return; }
                g.live = true;
                if (g.mode === "back") {
                    g.panel.style.transition = "none";
                    // A back-drag that never gets its touchend leaves an
                    // overlay parked off-screen, which reads as a dead app.
                    // touchcancel covers the ordinary interruptions; this
                    // covers the ones it does not (a rerun swapping the node
                    // out from under the finger, a dropped event). Cleared in
                    // finish(), so it only ever fires on a gesture that died.
                    var stuck = g;
                    g.guard = setTimeout(function () {
                        if (g === stuck) { g = null; backReset(stuck.panel); }
                    }, 4000);
                }
                else { root.classList.add("cai-drawer-drag"); }
            }
            if (g.mode === "back") {
                // rubber-banded: the panel is allowed to leave, but a finger
                // that keeps going does not drag it a screen and a half
                g.travel = Math.max(0, Math.min(-dx, g.w));
                g.panel.style.transform = "translateX(" + (-g.travel) + "px)";
                g.panel.style.opacity = String(1 - (g.travel / g.w) * 0.35);
                e.preventDefault();
                return;
            }
            g.travel = Math.max(0, Math.min(g.mode === "open" ? -dx : dx, g.w));
            var off = g.mode === "open" ? g.w - g.travel : g.travel;
            var d = drawer(), b = backdrop();
            if (d) d.style.transform = "translateX(" + off + "px)";
            if (b) b.style.opacity = String(1 - (off / g.w) * 0.9);
            e.preventDefault();
        }, { passive: false, capture: true });

        // hand the panel back to the stylesheet. Called on spring-back, on the
        // far side of a commit, and defensively from the sweep — an overlay
        // stranded mid-transform is the one failure this gesture could leave
        // behind, and it would look exactly like the app had died.
        var backReset = function (panel) {
            if (!panel) return;
            panel.style.transition = "";
            panel.style.transform = "";
            panel.style.opacity = "";
        };

        var finish = function () {
            var cur = g;
            g = null;
            if (cur && cur.guard) clearTimeout(cur.guard);
            if (!cur || !cur.live) return;
            swallowClick();
            var commit = cur.travel > Math.min(90, cur.w * 0.3);
            if (cur.mode === "back") {
                var panel = cur.panel;
                panel.style.transition = "transform .19s cubic-bezier(.4,0,.2,1), opacity .19s ease";
                if (!commit) { panel.style.transform = ""; panel.style.opacity = ""; return; }
                // see it leave, then press the control the user would have
                panel.style.transform = "translateX(-100%)";
                panel.style.opacity = "0";
                cur.btn.click();
                // Streamlit answers in its own time. Whichever lands first is
                // fine: a fast rerun replaces this node (nothing to undo), a
                // slow one finds the panel back in place rather than stranded
                // off-screen. 380ms = the slide plus a beat.
                setTimeout(function () { backReset(panel); }, 380);
                return;
            }
            settle(cur.mode === "open" ? commit : !commit);
        };
        doc.addEventListener("touchend", finish, { passive: true, capture: true });
        doc.addEventListener("touchcancel", function () {
            if (g) g.travel = 0;   // an interrupted drag always springs back
            finish();
        }, { passive: true, capture: true });

        // ── accessible names for the glyph controls ──
        // Streamlit gives no way to set attributes on a button, and these two
        // carry a GLYPH as their label: "⚙" (hidden behind font-size:0 and a
        // background-image, but still the accessible name) and "«". VoiceOver
        // reads those as "gear" and "left-pointing double angle quotation
        // mark". Everything else on the page names itself from its Hebrew
        // label; only these two need to be told. Re-applied on every rerun
        // because Streamlit replaces the nodes.
        // The two backdrops and the hamburger DO carry an sr-only <p> now, but
        // an explicit aria-label removes any dependence on how a given engine
        // treats clipped text — it wins outright and costs nothing. The
        // sr-only text stays as the no-CSS fallback.
        var NAMES = {
            ".st-key-open_settings button": "הגדרות",
            ".st-key-drawer_close button": "סגירת התפריט",
            ".st-key-settings_back button": "חזרה",
            ".st-key-drawer_open_btn button": "תפריט",
            ".st-key-drawer_backdrop button": "סגירת התפריט",
            ".st-key-settings_backdrop button": "סגירת הגדרות"
        };
        var nameCtrls = function () {
            try {
                Object.keys(NAMES).forEach(function (sel) {
                    var el = doc.querySelector(sel);
                    if (el && el.getAttribute("aria-label") !== NAMES[sel])
                        el.setAttribute("aria-label", NAMES[sel]);
                });
            } catch (e) {}
        };
        nameCtrls();
        setInterval(nameCtrls, 1200);

        // ── taps ──
        var TOGGLES = ".st-key-drawer_open_btn button";
        var CLOSERS = ".st-key-drawer_close button, .st-key-drawer_backdrop button";
        // these DO rerun (they change real state) — close first, don't block
        var ACTIONS = '.st-key-new_chat button, [class*="st-key-hist_"] button';
        doc.addEventListener("click", function (e) {
            var el = e.target;
            if (!el || !el.closest) return;
            if (el.closest(TOGGLES)) {
                e.preventDefault(); e.stopPropagation();   // capture phase: React never sees it
                settle(!isOpen());
            } else if (el.closest(CLOSERS)) {
                e.preventDefault(); e.stopPropagation();
                settle(false);
            } else if (el.closest(ACTIONS)) {
                settle(false);
            }
        }, true);

        // ── orders accordion (same contract as the drawer: client-side only) ──
        // Expanding "פקודות מטכ״ל במערכת" and searching it used to be server
        // state. Every open, every close and every committed query was a full
        // rerun — ~3.5s on device — and the FIRST open additionally blocked the
        // server ~2.9s reading all 80 PDFs to mint media URLs (2026-07-30
        // "sometimes it just hangs"). The rows are now static links that are
        // always in the DOM, so both are a class flip and a substring test.
        var ORDERS_OPEN = "cai-orders-open";
        // must mirror _search_norm server-side: mobile keyboards emit ״/׳
        // where the titles store ASCII quotes
        var normQ = function (s) {
            return (s || "").replace(/״/g, '"').replace(/׳/g, "'")
                            .trim().toLowerCase();
        };
        var filterOrders = function (q) {
            var rows = doc.querySelectorAll(".cai-orders-scroll .cai-order-link");
            var hit = 0;
            for (var i = 0; i < rows.length; i++) {
                var on = !q || (rows[i].getAttribute("data-q") || "").indexOf(q) !== -1;
                rows[i].hidden = !on;
                if (on) hit++;
            }
            var none = doc.querySelector(".cai-orders-empty[data-none]");
            if (none) none.hidden = !(q && !hit);
        };
        doc.addEventListener("click", function (e) {
            var card = e.target && e.target.closest && e.target.closest(".cai-kb-card");
            if (!card) return;
            e.preventDefault();
            var open = root.classList.toggle(ORDERS_OPEN);
            card.setAttribute("aria-expanded", open ? "true" : "false");
        });
        // The query lives on <html> too, because Streamlit replaces the drawer
        // node on any rerun (asking a question, opening a tool) and a fresh
        // <input> would come back blank with the list still filtered by the
        // class-driven CSS. Re-applied by the observer below.
        doc.addEventListener("input", function (e) {
            var box = e.target;
            if (!box || !box.classList || !box.classList.contains("cai-orders-q")) return;
            var q = normQ(box.value);
            root.dataset.caiOrdersQ = q;
            filterOrders(q);
        });

        // ── personal details: live service card + save bar ──
        // The fields sit inside an st.form, so nothing they hold reaches the
        // server until submit — which is exactly why the card that mirrors them
        // has to be updated here. Reading the widgets is also how the save bar
        // knows whether anything actually changed.
        var pfShortTrack = function (t) {
            if (!t || t.indexOf("בחר") === 0) return "";
            return t.split(" (")[0].trim();
        };
        var pfRead = function () {
            var nm  = doc.querySelector(".st-key-pf_name_w input");
            if (!nm) return null;                       // form not mounted
            var seg = doc.querySelector('.st-key-pf_type_w [data-testid$="segmented_controlActive"]');
            var trk = doc.querySelector(".st-key-pf_track_w [data-baseweb='select']");
            var on  = doc.querySelectorAll('.st-key-profile_statuses [data-testid="stBaseButton-pillsActive"]');
            var marks = [];
            for (var i = 0; i < on.length; i++) marks.push(on[i].innerText.trim());
            return {
                name:  nm.value.trim(),
                type:  seg ? seg.innerText.trim() : "",
                track: pfShortTrack(trk ? trk.innerText.trim() : ""),
                // list keeps SELECTION ORDER for display; marks is the sorted
                // form the save bar diffs against — do not merge the two
                list:  marks,
                marks: marks.slice().sort().join("|")
            };
        };
        // never assign an unchanged value: the MutationObserver below calls this,
        // and writing textContent replaces a text node — an unguarded write would
        // re-trigger the observer forever
        var setText = function (el, v) { if (el && el.textContent !== v) el.textContent = v; };
        var pfSync = function () {
            var card = doc.querySelector(".cai-svc");
            if (!card) return;                        // not on this screen
            var s = pfRead();
            if (!s) return;
            var fallback = card.getAttribute("data-svc-fallback") || "";
            var shown = s.name || fallback;
            setText(card.querySelector("[data-svc-mono]"), shown.slice(0, 1));
            setText(card.querySelector("[data-svc-nm]"), shown);
            // mirror the server rule: a type equal to the role (card footer)
            // is dropped from the meta line — no "מילואים" twice on one card
            setText(card.querySelector("[data-svc-meta]"),
                    [s.type, s.track].filter(function (x) {
                        return x && x !== fallback;
                    }).join(" · "));

            // the ticks land on the card's footer — the slot has shipped empty
            // since the direction was chosen, and filling it is what turns the
            // status block from the one section that ignores the card into the
            // one that visibly amends it. Capped at two so a soldier who ticks
            // all four cannot overflow the footer at 320px.
            var lbl = s.list.length > 2
                ? s.list.slice(0, 2).join(" · ") + " +" + (s.list.length - 2)
                : s.list.join(" · ");
            setText(card.querySelector("[data-svc-marks]"), lbl);
            setText(doc.querySelector("[data-pf-reg]"), lbl);
            var statusBox = doc.querySelector(".st-key-cai_pf_status");
            if (statusBox && statusBox.classList.contains("has-marks") !== (s.list.length > 0))
                statusBox.classList.toggle("has-marks", s.list.length > 0);

            // diff against the SERVER's saved state, not a DOM snapshot
            var bar = doc.querySelector(".st-key-cai_pf_save");
            if (!bar) return;
            var base;
            try { base = JSON.parse(card.getAttribute("data-svc-base") || "null"); } catch (e) { base = null; }
            if (!base) return;                        // no baseline: never claim dirty
            var dirty = s.name !== base.name || s.type !== base.type ||
                        s.track !== base.track || s.marks !== base.marks;
            if (bar.classList.contains("dirty") !== dirty) bar.classList.toggle("dirty", dirty);
        };
        doc.addEventListener("input", pfSync);
        doc.addEventListener("click", function () { setTimeout(pfSync, 0); });

        // ── safety net ──
        // no hamburger in the DOM (entry screen, name gate, post-logout) means
        // the drawer cannot legitimately be open. Same pass restores the
        // orders query into a freshly re-rendered search box and re-syncs the
        // personal-details card after Streamlit replaces its node.
        var restoreOrders = function () {
            var box = doc.querySelector(".cai-orders-q");
            if (!box) return;
            var q = root.dataset.caiOrdersQ || "";
            if (box.value === q) return;   // in sync — costs one querySelector
            if (box !== doc.activeElement) box.value = q;
            filterOrders(q);
        };
        // ── datepicker: pin the calendar as a sheet, and speak Hebrew ──
        // BaseWeb portals the calendar to <body> and positions it with an
        // INLINE transform written by popper, which no stylesheet rule can
        // outrank — so the pinning has to happen here. Everything else about
        // the panel is CSS (see the calendar block in _MODAL_CSS).
        //
        // Why pin at all: popper anchors to the field's rect at open time, and
        // inside a dialog whose expander is still animating that rect is
        // stale — measured on device, the panel landed above the field and
        // covered it, over a fully transparent background. A centred sheet has
        // no anchor to go stale.
        var CAL_MONTHS = {
            January: "ינואר", February: "פברואר", March: "מרץ", April: "אפריל",
            May: "מאי", June: "יוני", July: "יולי", August: "אוגוסט",
            September: "ספטמבר", October: "אוקטובר", November: "נובמבר",
            December: "דצמבר"
        };
        var CAL_DAYS = { Su: "א", Mo: "ב", Tu: "ג", We: "ד", Th: "ה", Fr: "ו", Sa: "ש" };
        // The geometry is written INLINE, not via a class: the popover div is
        // React-owned and emotion rewrites its className on every re-render, so
        // a class we add survives until the first month change and no longer
        // (measured — the panel came back 326px wide at 0,0). An inline
        // declaration marked !important also outranks popper's own inline
        // write, which is non-important.
        var CAL_SHEET = [
            ["position", "fixed"],
            ["left", "50%"],
            ["right", "auto"],
            ["top", "auto"],
            ["bottom", "calc(env(safe-area-inset-bottom, 0px) + 20px)"],
            ["width", "min(340px, calc(100vw - 28px))"],
            ["margin", "0"],
            // translateX alone: popper's placement offset is what has to go
            ["transform", "translateX(-50%)"],
            ["z-index", "1000090"]
        ];
        var calSkin = function () {
            var cal = doc.querySelector('[data-baseweb="calendar"]');
            if (!cal) return;
            var pop = cal.closest('[data-baseweb="popover"]');
            if (pop) {
                for (var i = 0; i < CAL_SHEET.length; i++) {
                    var k = CAL_SHEET[i][0], v = CAL_SHEET[i][1];
                    // guarded: writing an unchanged value is still a mutation,
                    // and this runs FROM the MutationObserver
                    if (pop.style.getPropertyValue(k) !== v) {
                        pop.style.setProperty(k, v, "important");
                    }
                }
            }
            // Streamlit exposes no locale for st.date_input, so translate the
            // rendered labels. Exact matches only — day NUMBERS share this
            // subtree, and a substring pass would maul them.
            var w = doc.createTreeWalker(cal, NodeFilter.SHOW_TEXT);
            var n;
            while ((n = w.nextNode())) {
                var t = n.nodeValue.trim();
                var heb = CAL_MONTHS[t] || (t.length === 2 ? CAL_DAYS[t] : null);
                if (heb) n.nodeValue = heb;
            }
        };

        var sweep = function () {
            if (isOpen() && !hamburger()) settle(false);
            // an overlay that is on screen and not under a live finger must
            // never be carrying a drag transform
            if (!g) {
                var set = doc.querySelector(".st-key-cai_settings");
                if (set && set.style.transform && !set.style.transition) backReset(set);
                var card = doc.querySelector('[data-testid="stDialog"] [role="dialog"]');
                if (card && card.style.transform && !card.style.transition) backReset(card);
            }
            restoreOrders();
            pfSync();
            calSkin();
        };
        new MutationObserver(sweep).observe(doc.body, { childList: true, subtree: true });
        sweep();
    };
    try {
        var adoc = window.parent.document;   // the app document, one level up
        if (!adoc.getElementById("cai-swipe-js")) {
            var s = adoc.createElement("script");
            s.id = "cai-swipe-js";
            s.textContent = "(" + swipeFn.toString() + ")();";
            adoc.body.appendChild(s);
        }
    } catch (e) {}
    </script>""",
    height=0,
)


# ── PWA: home-screen metadata (icon, standalone, manifest) ──
@st.cache_data(show_spinner=False)
def _dbg_start_url() -> str:
    base = "/~/+/" if pwa_assets._ON_CLOUD else "/"
    return f"{base}?caidbg=1" if st.query_params.get("caidbg") == "1" else base


def _pwa_assets() -> dict | None:
    """Publish the PWA assets and return their URLs.

    The generation itself lives in pwa_assets.py so the Docker build can bake
    the files into the image — read that module's docstring for why the URLs
    must be stable AND the files must pre-exist. This wrapper only supplies the
    one thing a build step cannot know: start_url, which carries the ?caidbg=1
    flag through so a diagnostic install launches with the badge armed.

    On the cloud the platform serves the app document at /~/+/ (the React shell
    embeds it from there, and it answers 200 with no auth-redirect hop even
    cookieless). Launching the PWA straight at it skips the shell entirely: no
    white shell page, no shell JS bundle, no viewer badges. Locally the app
    really is served at /.

    Called every rerun; publish_static only writes on change, so this is a
    handful of stat() calls once the image already carries the files.
    """
    return pwa_assets.publish_all(_dbg_start_url())


_pwa = _pwa_assets()
if _pwa:
    # "Add to Home Screen" reads metadata off the TOP document (on the cloud
    # the app lives inside the platform shell, same-origin) — inject there,
    # like the badge watchdog above. Media URLs are app-frame relative, so
    # they're resolved against the app frame's directory (this component's
    # parent), which differs local (/) vs cloud shell (/~/+/). The shell
    # ships its OWN manifest / theme-color (#FFFFFF) / apple-touch-icon, and
    # for duplicate manifests the FIRST one wins — so existing tags are
    # REPLACED in place, not appended after. Idempotent by element id — the
    # top document survives Streamlit reruns. iOS snapshots all of this at
    # add-time: users who installed before must remove + re-add the icon.
    components.html(
        f"""
        <script>
        (function () {{
            var icon180 = {json.dumps(_pwa[180])};
            var manifest = {json.dumps(_pwa["manifest"])};
            var startup = {json.dumps(_pwa["startup"])};
            try {{
                var doc = window.top.document;
                // theme pin first, EVERY run (not just the first): a shell
                // script or reconnect can rewrite <head>, and Safari re-tints
                // its chrome live off this meta
                var tc = doc.querySelector('meta[name="theme-color"]');
                if (!tc) {{ tc = doc.createElement("meta"); tc.setAttribute("name", "theme-color"); doc.head.appendChild(tc); }}
                // write ONLY on change: boot_shell now ships this meta
                // statically, and on iOS standalone a theme-color TRANSITION
                // resizes the web view a few px — which yanked the splash's
                // bottom-anchored wait block mid-boot (2026-07-28, t=13.9s).
                // Idempotent writes keep the reconnect-protection this line
                // exists for without ever re-triggering that resize.
                if (tc.getAttribute("content") !== "#14170E") {{
                    tc.setAttribute("content", "#14170E");
                }}
                if (doc.getElementById("cai-pwa-manifest")) return;
                var loc = window.parent.location;
                var dir = loc.pathname.endsWith("/") ? loc.pathname : loc.pathname + "/";
                var base = loc.origin + dir;
                // /static/cai/... paths are ORIGIN-absolute and stable (see
                // boot_shell.publish_static) — they must not be rebased onto
                // the app frame's directory the way the old /media/<hash>
                // URLs were. Anything else still gets the legacy treatment.
                var abs = function (u) {{
                    u = String(u);
                    if (u.charAt(0) === "/") return loc.origin + u;
                    return base + u;
                }};
                var head = doc.head;
                var upsert = function (sel, tag, attrs) {{
                    var el = head.querySelector(sel);
                    if (!el) {{ el = doc.createElement(tag); head.appendChild(el); }}
                    for (var k in attrs) el.setAttribute(k, attrs[k]);
                }};
                upsert('link[rel="manifest"]', "link",
                       {{ id: "cai-pwa-manifest", rel: "manifest", href: abs(manifest) }});
                upsert('link[rel="apple-touch-icon"]', "link",
                       {{ rel: "apple-touch-icon", sizes: "180x180", href: abs(icon180) }});
                upsert('meta[name="apple-mobile-web-app-capable"]', "meta",
                       {{ name: "apple-mobile-web-app-capable", content: "yes" }});
                upsert('meta[name="mobile-web-app-capable"]', "meta",
                       {{ name: "mobile-web-app-capable", content: "yes" }});
                // translucent → the status bar goes transparent and the dark
                // -olive header band shows behind the clock (no black bar). The
                // web view then extends UNDER the clock, so the inset must be
                // reclaimed as top padding — see the --cai-sat probe below.
                upsert('meta[name="apple-mobile-web-app-status-bar-style"]', "meta",
                       {{ name: "apple-mobile-web-app-status-bar-style", content: "black-translucent" }});
                upsert('meta[name="apple-mobile-web-app-title"]', "meta",
                       {{ name: "apple-mobile-web-app-title", content: "CommandAI" }});
                // NO viewport-fit=cover. It was added here for years so that
                // black-translucent would give env(safe-area-inset-top) a real
                // value — but it was applied AFTER first paint, which resized
                // the web view mid-boot and made the splash jump up and drop
                // back down on every launch the pilot filmed (2026-07-29:
                // chevron ink row 171 -> 164 -> 171). Shipping it statically
                // instead fixed the jump and then broke something worse: with
                // cover the app's own layout, tuned for years against the
                // inset viewport, ended ~48px short of the bottom — content
                // shifted up, dead band under the disclaimer, for the whole
                // session. Both directions are regressions, so the viewport is
                // now left exactly as Streamlit ships it and the splash aligns
                // itself instead (boot_shell._PAD_JS). --cai-sat below already
                // supplies the real inset to everything that needs it.
                // env(safe-area-inset-top) reads 0 inside the app iframe, so
                // measure it HERE (the top/shell doc, where it's real) and push
                // it into the app frame's :root as --cai-sat. The header band,
                // wordmark, hamburger and drawer all clear the clock by it.
                var probe = doc.createElement("div");
                probe.style.cssText = "position:fixed;top:0;left:0;width:0;" +
                    "height:env(safe-area-inset-top,0px);visibility:hidden;pointer-events:none;";
                doc.body.appendChild(probe);
                var appRoot = window.parent.document.documentElement;
                var syncSat = function () {{
                    appRoot.style.setProperty("--cai-sat", (probe.offsetHeight || 0) + "px");
                }};
                syncSat();
                window.top.addEventListener("resize", syncSat);
                window.top.addEventListener("orientationchange", syncSat);
                // iOS launch screens — shown from icon tap to first paint,
                // which on a weak connection is most of the wait (the
                // alternative is a black void). One <link> per device class.
                startup.forEach(function (s) {{
                    var l = doc.createElement("link");
                    l.setAttribute("rel", "apple-touch-startup-image");
                    l.setAttribute("media",
                        "(device-width: " + (s[0] / s[2]) + "px) and " +
                        "(device-height: " + (s[1] / s[2]) + "px) and " +
                        "(-webkit-device-pixel-ratio: " + s[2] + ") and " +
                        "(orientation: portrait)");
                    l.setAttribute("href", abs(s[3]));
                    head.appendChild(l);
                }});
            }} catch (e) {{}}
        }})();
        </script>
        """,
        height=0,
    )

# ── Device profile cookie sync — mirrors the live state to cookie+localStorage:
# role picks, settings name edits, switch-role/logout and wipes. Also
# refreshes the 400-day expiry on each visit (resets Safari's 7-day ITP
# clock for browser-tab users; installed PWAs are exempt).
# CRITICAL GATE: rendered only once there is real state to remember (or an
# explicit wipe). On a cloud cold boot the session starts EMPTY (the edge
# strips cookies, so nothing is seeded yet) while the probe is still
# round-tripping — an unconditional writer here raced the probe and wiped
# the device memory with {role:null} before it could be read (verified live
# on Community Cloud). window.top: the component iframe is sandboxed but
# same-origin — the store must live on the APP document.
_sync_settled = (
    st.session_state.role is not None
    or st.session_state.get("name_asked")
    or bool((st.session_state.get("profile_name") or "").strip())
    or st.session_state.get("cai_wipe_pending")
)
_ck_dict = {
    "v": 1,
    "role": st.session_state.role,
    "name": (st.session_state.get("profile_name") or "")[:40],
    "asked": bool(st.session_state.get("name_asked")),
    # unconditional, unlike "mil" below: the id is worthless if it only
    # survives for users who filled a form. Written on the same run as the
    # first role tap, which is always before the first question.
    "did": st.session_state.device_id,
}
# text scale rides the device cookie, and like "mil"/"sol" it is written ONLY
# when it differs from the default — so the payload of everyone who never
# touched the setting stays byte-identical to the pre-text-scale format.
_fs_key = {1.15: "m", 1.3: "l"}.get(float(st.session_state.get("text_scale", 1.0)))
if _fs_key:
    _ck_dict["fs"] = _fs_key
# miluim-tool inputs ride the same device cookie — only once saved, so every
# other user's payload stays byte-identical to the pre-miluim format
if st.session_state.get("mil_saved"):
    _ck_dict["mil"] = {
        "dy": st.session_state.get("mil_days_year"),
        "d3": st.session_state.get("mil_days_3y"),
        "emp": list(st.session_state.get("mil_emp") or []),
        "sal": st.session_state.get("mil_salary"),
        "sv": True,
    }
# conscript-map inputs, same conditional contract: absent for anyone who never
# opened the tool, so their cookie payload stays byte-identical
if st.session_state.get("sol_saved"):
    _sol_en = st.session_state.get("sol_enlist")
    _sol_di = st.session_state.get("sol_discharge")
    _ck_dict["sol"] = {
        "en": _sol_en.isoformat() if _sol_en else None,
        "di": _sol_di.isoformat() if _sol_di else None,
        "tr": st.session_state.get("sol_track"),
        "sg": bool(st.session_state.get("sol_single")),
        "mr": bool(st.session_state.get("sol_married")),
        "sv": True,
    }
_ck_payload = urllib.parse.quote(json.dumps(_ck_dict, ensure_ascii=False), safe="")
if _sync_settled:
    components.html(
        "<script>try{"
        f"var v='{_ck_payload}';"
        "window.top.document.cookie='cai_profile='+v+';max-age=34560000;path=/;SameSite=Lax';"
        "try{window.top.localStorage.setItem('cai_profile',v);}catch(e2){}"
        "}catch(e){}</script>",
        height=0,
    )

# ── Entry / role gate + one-time name gate ──
# The name gate is deliberately NOT st.dialog (dialog close skips the full
# rerun — the bug that once left the drawer dead). It's an app-owned
# overlay: the real entry screen keeps rendering underneath, and a fixed
# scrim + card sit above it. The gate derives from name_asked rather than
# a session flag so a mid-gate refresh lands back IN the gate (cookie
# already carries the role) instead of silently dropping the question.
if st.session_state.role is None or _name_gate:
    st.markdown(
        "<div class='cai-entry'>"
        "<div class='cai-entry-classif'>מערכת פקודות · בלמ\"ס</div>"
        "<div class='cai-entry-chev'><span></span><span></span></div>"
        "<div class='cai-entry-title'>Command<span class='cai-wm-ai'>AI</span></div>"
        "<div class='cai-entry-sub'>העוזר החכם לפקודות מטכ\"ל</div>"
        "<div class='cai-entry-divider'></div>"
        "<div class='cai-entry-choose'>בחר את סוג הכניסה שלך</div>"
        # marker for the injected nav-veil: a role tap that leads to the name
        # gate (first visit) must NOT raise the veil — the gate has no
        # .cai-header, so the veil would sit opaque until its 4s timeout
        + ("<span id='cai-gate-pending'></span>"
           if not st.session_state.get("name_asked") else "")
        + "</div>",
        unsafe_allow_html=True,
    )

    # role_picked_here arms the name gate — see the setdefault near the top.
    # Set it on the tap, never on the cookie restore.
    if st.button("**כניסת חיילים**  \nחובה / סדיר", key="role_soldier", use_container_width=True):
        st.session_state.role = "soldier"
        st.session_state.role_picked_here = True
        st.rerun()
    if st.button("**כניסת מפקדים**  \nקבע", key="role_commander", use_container_width=True):
        st.session_state.role = "commander"
        st.session_state.role_picked_here = True
        st.rerun()
    if st.button("**כניסת מילואים**  \nמערך המילואים", key="role_reserve", use_container_width=True):
        st.session_state.role = "reserve"
        st.session_state.role_picked_here = True
        st.rerun()

    st.markdown("<div class='cai-entry-footer'>בלמ\"ס · לשימוש פנימי בלבד</div>", unsafe_allow_html=True)

    if _name_gate:
        # scrim (outer container) + card (inner) — plain keyed containers, no
        # entrance animation on purpose: Streamlit may replace the keyed VB
        # node on a rerun, which would replay the animation.
        # st.form is essential, not cosmetic: a bare st.button tap right
        # after typing loses the race with the text_input blur-commit rerun
        # (the tap lands on a replaced node — first tap swallowed). The form
        # bundles the field value and the press into ONE event, and Enter
        # submits too.
        with st.container(key="cai_name_gate"):
            with st.container(key="cai_name_card"):
                st.markdown(
                    "<div class='cai-gate-title'>איך קוראים לך?</div>"
                    "<div class='cai-gate-sub'>לברכה אישית בכניסה · נשמר במכשיר בלבד</div>",
                    unsafe_allow_html=True,
                )
                with st.form(key="cai_name_form", border=False):
                    st.text_input("שם פרטי", key="gate_name_w",
                                  label_visibility="collapsed",
                                  placeholder="השם הפרטי שלך", max_chars=20)
                    _gc1, _gc2 = st.columns([5, 3], gap="small")
                    _gate_go = _gc1.form_submit_button(
                        "המשך", use_container_width=True, type="primary")
                    _gate_skip = _gc2.form_submit_button(
                        "דלג", use_container_width=True)
                if _gate_go or _gate_skip:
                    if _gate_go:
                        _nm = (st.session_state.get("gate_name_w") or "").strip()
                        if _nm:
                            # display-only: feeds the greeting/pill and seeds
                            # the settings "שם מלא" field; never sent to the API
                            st.session_state.profile_name = _nm[:40]
                    st.session_state.name_asked = True
                    st.rerun()

    st.stop()

# UI-only fallback for the moment the question pool is empty (documents
# still loading during a redeploy). Defined here, not imported from backend:
# Streamlit Cloud can re-execute app.py against a backend module still
# cached from the previous build, so importing a newly-added name from
# backend crashes the whole boot with ImportError.
_FALLBACK_QUESTIONS = {
    "soldier": ["מה זכויותיי כחייל?", "האם מגיע לי שינה מספקת?", "מה העונש על עבירה משמעתית?"],
    "commander": ["אילו עונשים מוסמך מפקד להטיל בדין משמעתי?", "מה חובות הדיווח שלי כמפקד?"],
    "reserve": ["אילו תגמולים מגיעים לי כחייל מילואים?", "מה זכויותיי כחייל מילואים?"],
}

if "suggested" not in st.session_state:
    all_q = get_suggested_questions(role=st.session_state.role)
    # A chip is the app's only unprompted claim about what it can answer, so
    # the raw ingestion pool is curated before it is sampled — see
    # question_bank for the 86 questions that failed a cold read and why the
    # fix lives here and not in json_store (those strings are also retrieval
    # anchors). Guarded: a curation bug must not empty the greeting screen.
    try:
        all_q = question_bank.curate(all_q)
    except Exception:
        pass
    # older backend builds return the generic defaults instead of an empty
    # pool — treat both as "no real pool yet" and don't cache
    if all_q and all_q != _FALLBACK_QUESTIONS.get(st.session_state.role):
        st.session_state.suggested = random.sample(all_q, min(4, len(all_q)))
suggested_questions = st.session_state.get("suggested") or _FALLBACK_QUESTIONS.get(st.session_state.role, _FALLBACK_QUESTIONS["soldier"])


def queue_question(q: str):
    st.session_state.pending_question = q

def archive_current_conversation():
    """Save the active conversation into history before it's cleared."""
    if not st.session_state.messages:
        return
    first_user_msg = next(
        (m["content"] for m in st.session_state.messages if m["role"] == "user"),
        "שיחה",
    )
    st.session_state.conversation_history.insert(0, {
        "title": first_user_msg[:40],
        "messages": st.session_state.messages.copy(),
        "role": st.session_state.role,
    })
    st.session_state.conversation_history = st.session_state.conversation_history[:10]


_QUOTA_NOTICES = {
    "user": "**הגעת למכסת השאלות היומית שלך.**\n\n"
            "המכסה מתאפסת מחר. בינתיים אפשר להמשיך לעיין בפקודות המלאות "
            "ובחיפוש שבתפריט הצד — הם ללא הגבלה.",
    "global": "**המכסה היומית של המערכת נוצלה במלואה.**\n\n"
              "חזרו מחר! בינתיים אפשר להמשיך לעיין בפקודות המלאות ובחיפוש "
              "שבתפריט הצד — הם ללא הגבלה.",
}


# document glyph for the letters modal header (this feature drafts letters, so
# it gets a page mark instead of the shared chevron); accent-bright via
# currentColor so it re-tints per role
_LETTER_EMBLEM = (
    "<svg viewBox='0 0 24 24' width='21' height='21' fill='none' "
    "stroke='currentColor' stroke-width='1.7' stroke-linecap='round' "
    "stroke-linejoin='round' style='color:var(--accent-bright)'>"
    "<path d='M7 3h7l4 4v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z'/>"
    "<path d='M14 3v4h4'/><path d='M9.5 13h5M9.5 16.5h5'/></svg>"
)


@st.dialog("מחולל מכתבים", width="large")
def _letters_dialog():
    """Order-grounded formal-letter drafts (בקשת חופשה, ערר, קבילה...).

    One generation burns one daily-quota unit — the same reserve/refund
    contract as a chat question, so this flow cannot sidestep the global
    budget. The draft lands in an editable textarea; the download button
    exports whatever the user edited, not the raw model text.
    """
    # inline header (not _modal_header) so this feature's document emblem lives
    # entirely in the letters region — the shared header keeps its chevron
    st.markdown(
        "<div class='cai-mhead'>"
        f"<div class='cai-memblem'>{_LETTER_EMBLEM}</div>"
        "<div class='cai-mtitles'>"
        "<div class='cai-mtitle'>מחולל מכתבים</div>"
        "<div class='cai-msub'>מעוגן בפקודות מטכ״ל · בלמ״ס</div>"
        "</div></div>",
        unsafe_allow_html=True,
    )
    kind = st.selectbox(
        "סוג המכתב",
        list(LETTER_TYPES),
        format_func=lambda k: LETTER_TYPES[k]["title"],
        key="letter_kind",
    )
    details = {}
    # fields are (label, placeholder) or (label, placeholder, is_content) —
    # the 3rd element is a retrieval hint used by letters.py, ignored here
    for i, field in enumerate(LETTER_TYPES[kind]["fields"]):
        label, placeholder = field[0], field[1]
        details[label] = st.text_input(
            label, placeholder=placeholder or None, key=f"letter_{kind}_{i}"
        )
    # label has no ✍️ emoji — the colorful glyph clashed with the mock's clean
    # look; the pen is drawn by CSS (st-key-letter_go p::after mask) in accent
    if st.button("נסח טיוטה", key="letter_go", use_container_width=True):
        quota = metrics.reserve(st.session_state.session_id)
        if quota != "ok":
            st.warning(_QUOTA_NOTICES[quota])
        else:
            try:
                t0 = time.time()
                with st.spinner("מנסח טיוטה מעוגנת בפקודות..."):
                    draft = compose_letter(kind, details, role=st.session_state.role)
                st.session_state.letter_draft = {"kind": kind, **draft}
                # seed the textarea's state BEFORE it is instantiated below
                st.session_state.letter_edit = draft["text"]
                # letters burn the same quota as questions — log them the
                # same way too (the "[מכתב]" prefix separates them in the
                # sheet), or the pilot's usage/cost picture undercounts
                if st.session_state.get("share_analytics", True):
                    metrics.log_question(
                        session_id=st.session_state.session_id,
                        device_id=st.session_state.device_id,
                        role=st.session_state.role or "",
                        question=f"[מכתב] {LETTER_TYPES[kind]['title']}",
                        answer=draft["text"],
                        sources=draft.get("sources"),
                        usage=draft.get("usage"),
                        latency_s=time.time() - t0,
                    )
            except (APIConnectionError, APITimeoutError):
                metrics.refund(st.session_state.session_id)
                st.error("אין כרגע חיבור לשירות. בדוק את החיבור ונסה שוב בעוד רגע.")
            except BadRequestError as e:
                metrics.refund(st.session_state.session_id)
                # same monthly-spend-limit 400 as in handle_question
                st.error("⏸️ המערכת בהשהיה זמנית עקב מגבלת שימוש — נסה שוב מחר."
                         if "usage limits" in str(e)
                         else "אירעה שגיאה זמנית בניסוח. נסה לשלוח שוב.")
            except Exception as e:
                safe_print(f"[letters] draft failed: {e!r}")
                metrics.refund(st.session_state.session_id)
                st.error("אירעה שגיאה זמנית בניסוח. נסה לשלוח שוב.")
    # standing note under the button (matches the design mock): sets the
    # expectation that the output is an order-grounded draft to review
    st.markdown(
        "<div style='font:400 11.5px Heebo,sans-serif;color:rgba(236,237,230,.58);"
        "direction:rtl;text-align:right;margin:10px 2px 0;line-height:1.55'>"
        "הטיוטה נוסחה לפי לשון הפקודה — יש לעבור עליה לפני הגשה.</div>",
        unsafe_allow_html=True,
    )
    draft = st.session_state.get("letter_draft")
    # a draft from another letter type stays hidden instead of masquerading
    # as the currently selected one
    if draft and draft.get("kind") == kind:
        if draft.get("truncated"):
            st.warning("הטיוטה נקטעה באמצע בגלל אורך — קצר את הפרטים ונסח שוב, או השלם את הסיום ידנית.")
        st.text_area("הטיוטה — קרא, השלם את החסר וערוך לפני הגשה", height=320, key="letter_edit")
        st.download_button(
            "⬇️ הורד כקובץ",
            data=(st.session_state.get("letter_edit") or draft["text"]).encode("utf-8"),
            file_name="commandai-letter.txt",
            mime="text/plain",
            use_container_width=True,
            key="letter_dl",
        )
        srcs = draft.get("sources") or []
        if srcs:
            st.caption("מעוגן בפקודות: " + " · ".join(s["title"] for s in srcs[:2]))


# ── Shared "premium modal" design system ──────────────────────────────────
# One scoped stylesheet for all three side-drawer dialogs (letters, punishment
# authority, entitlements) plus the clause dialog. Kept as its own constant
# rather than folded into the global f-string block so each feature stays
# readable and we avoid that block's {{ }} escaping — but it is emitted ONCE at
# page level next to _DS_CSS, never from inside a dialog body (see there for
# why). :root tokens (--accent / --accent-bright / --surface /
# --text*) are global, so the whole modal re-tints per role (חייל / מפקד /
# מילואים) automatically. Rebuilt from design_handoff_entitlements_calculator:
# dark-olive surface, chevron-emblem header, segmented control, styled fields
# and an accent-railed result card — replacing the flat olive-splash look.
_MODAL_CSS = """
<style>
/* ---- Modal surface: Streamlit paints the VISIBLE card ([role="dialog"], the
   inner box) with the olive theme.backgroundColor (var(--accent)) — the > div behind
   it is only a full-viewport positioning layer. Force the dark gradient onto the
   card itself, or the whole modal reads olive/"cheap" no matter what's inside. ---- */
/* Backdrop: Streamlit's default overlay is a LIGHT cream tint that WASHES the
   olive app behind the modal; the design wants the surroundings dimmed dark.
   Darken the full-screen stDialog layer (the card below keeps its own bg). */
div[data-testid="stDialog"] { background: rgba(9,11,7,.66) !important; }
div[data-testid="stDialog"] > div { direction: rtl; background: transparent !important; }
/* 150ms entrance: fade the layer, rise the card (2026-08-03 review — dialogs
   popped in with zero transition; 150ms reads as response, not as delay).
   The layer fades via opacity, NOT background: the scrim color above is
   pinned with !important, and CSS animations lose to !important declarations. */
@keyframes caiModalIn { from { opacity: 0; } }
@keyframes caiCardIn { from { transform: translateY(9px) scale(.977); } }
div[data-testid="stDialog"] { animation: caiModalIn .15s ease-out; }
div[data-testid="stDialog"] [role="dialog"] { animation: caiCardIn .15s cubic-bezier(.2,.7,.3,1); }
@media (prefers-reduced-motion: reduce) {
  div[data-testid="stDialog"],
  div[data-testid="stDialog"] [role="dialog"] { animation: none; }
}
div[data-testid="stDialog"] [role="dialog"] {
    direction: rtl;
    background: linear-gradient(180deg,var(--surface) 0%,#181B12 100%) !important;
    border: 1px solid rgba(236,237,230,.10) !important;
    border-radius: 26px !important;
    box-shadow: 0 -1px 0 rgba(255,255,255,.05) inset,
                0 30px 60px -18px rgba(0,0,0,.65) !important;
    padding: 22px 22px 26px !important;  /* mock: airy card, not Streamlit's tight default */
}
/* the app-wide stVerticalBlock{gap:0} crushes the dialog's rhythm — restore
   the mock's ~14px breathing room between field groups (label carries 7px) */
div[data-testid="stDialog"] [data-testid="stSelectbox"],
div[data-testid="stDialog"] [data-testid="stTextInput"],
div[data-testid="stDialog"] [data-testid="stTextArea"],
div[data-testid="stDialog"] [data-testid="stRadio"] { margin-bottom: 12px; }
/* Streamlit renders the dialog title as a <p> in a markdown bar (NOT an <h2>) —
   it's the modal's first child. Hide that whole bar; we inject our own header in
   the body. The close button is a SEPARATE absolutely-positioned element (sibling
   of the bar), so hiding the bar keeps it. */
div[data-testid="stDialog"] [role="dialog"] > div:first-child { display: none !important; }
/* native close button -> premium 34px circle, pinned to the top-left corner */
div[data-testid="stDialog"] button[aria-label="Close"],
div[data-testid="stDialog"] [data-testid="stDialogCloseButton"] {
    position: absolute !important; top: 20px !important; left: 20px !important; right: auto !important;
    z-index: 6;
    width: 34px !important; height: 34px !important; border-radius: 50% !important;
    background: rgba(236,237,230,.06) !important;
    border: 1px solid rgba(236,237,230,.12) !important;
    color: rgba(236,237,230,.6) !important;
}
div[data-testid="stDialog"] button[aria-label="Close"]:hover,
div[data-testid="stDialog"] [data-testid="stDialogCloseButton"]:hover {
    background: rgba(236,237,230,.12) !important; color: var(--text) !important;
}

/* ---- Injected header: chevron emblem + Suez-One title + classification ---- */
.cai-mhead { display: flex; align-items: center; gap: 13px; direction: rtl;
    text-align: right; margin: 2px 0 18px; }
.cai-memblem { width: 42px; height: 42px; border-radius: 13px; flex: none;
    background: var(--accent-soft); border: 1px solid var(--accent-border);
    display: flex; flex-direction: column; align-items: center; justify-content: center; }
.cai-memblem span { display: block; width: 15px; height: 15px; transform: rotate(45deg);
    border-top: 3px solid var(--accent); border-left: 3px solid var(--accent); }
.cai-memblem span + span { border-top-color: var(--accent-border);
    border-left-color: var(--accent-border); margin-top: -6px; }
.cai-mtitles { flex: 1; min-width: 0; }
.cai-mtitle { font: 400 22px 'Suez One', serif; color: var(--text); line-height: 1.1; }
.cai-msub { font: 600 12.5px Heebo, sans-serif; letter-spacing: 1.2px; color: var(--accent);
    opacity: .9; margin-top: 6px; white-space: nowrap; }

/* ---- Field labels (selects / inputs / the segmented question) ---- */
div[data-testid="stDialog"] [data-testid="stSelectbox"] label,
div[data-testid="stDialog"] [data-testid="stTextInput"] label,
div[data-testid="stDialog"] [data-testid="stTextArea"] label,
div[data-testid="stDialog"] [data-testid="stRadio"] > label {
    font: 600 11px Heebo, sans-serif !important; letter-spacing: .02em;
    color: rgba(236,237,230,.45) !important; margin-bottom: 7px !important;
}
/* the label TEXT lives in an inner <p> (stWidgetLabel) with its own emotion
   font/color — the label-level shorthand above never reaches it on device,
   so phones showed big cream labels instead of the mock's small dim ones */
div[data-testid="stDialog"] [data-testid="stWidgetLabel"] p {
    font-size: 11px !important; font-weight: 600 !important;
    /* .45 measured 4.0:1 on the dialog fill — under AA for 11px text that
       names the field underneath it. .6 measures 5.4:1 and keeps the mock's
       hierarchy (still well below the field's own cream). */
    color: rgba(236,237,230,.6) !important; letter-spacing: .02em;
}

/* ---- Select fields -> dark pill with olive chevron ---- */
div[data-testid="stDialog"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
    background: var(--surface) !important; border: 1px solid var(--border) !important;
    border-radius: 12px !important; min-height: 50px; padding: 4px 12px !important;
    direction: rtl; transition: border-color .15s ease;
}
div[data-testid="stDialog"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover {
    border-color: var(--accent-border) !important;
}
div[data-testid="stDialog"] [data-testid="stSelectbox"] div[data-baseweb="select"] div[value],
div[data-testid="stDialog"] [data-testid="stSelectbox"] div[data-baseweb="select"] input,
div[data-testid="stDialog"] [data-testid="stSelectbox"] div[data-baseweb="select"] span {
    font: 600 14.5px Heebo, sans-serif !important; color: var(--text) !important;
}
div[data-testid="stDialog"] [data-testid="stSelectbox"] div[data-baseweb="select"] svg {
    fill: var(--accent) !important; color: var(--accent) !important;
}

/* ---- Text inputs (letters) -> same dark pill ---- */
div[data-testid="stDialog"] [data-testid="stTextInput"] div[data-baseweb="input"],
div[data-testid="stDialog"] [data-testid="stTextInput"] div[data-baseweb="base-input"] {
    background: var(--surface) !important; border: 1px solid var(--border) !important;
    border-radius: 12px !important;
}
div[data-testid="stDialog"] [data-testid="stTextInput"] div[data-baseweb="base-input"] {
    border: none !important; background: transparent !important;
}
div[data-testid="stDialog"] [data-testid="stTextInput"] input {
    background: transparent !important; color: var(--text) !important;
    font: 600 14.5px Heebo, sans-serif !important; direction: rtl; padding: 13px 15px !important;
}
div[data-testid="stDialog"] [data-testid="stTextInput"] input::placeholder {
    color: rgba(236,237,230,.35) !important; font-weight: 400 !important;
}
/* ---- Draft textarea -> dark pill ---- */
div[data-testid="stDialog"] [data-testid="stTextArea"] div[data-baseweb="base-input"],
div[data-testid="stDialog"] [data-testid="stTextArea"] textarea {
    background: var(--surface) !important; border-radius: 12px !important;
    border-color: var(--border) !important; color: var(--text) !important;
}

/* ---- Radio -> segmented control ("מה לחשב?") ---- */
div[data-testid="stDialog"] [data-testid="stRadio"] div[role="radiogroup"] {
    display: flex !important; flex-direction: row !important; gap: 4px;
    background: rgba(0,0,0,.25); border: 1px solid rgba(236,237,230,.08);
    border-radius: 13px; padding: 4px;
}
div[data-testid="stDialog"] [data-testid="stRadio"] div[role="radiogroup"] label {
    flex: 1; display: flex; align-items: center; justify-content: center;
    /* uniform min-height + slim side padding: the three tiles used to render
       40/60/80px tall with "אישי־משפחתי" fractured mid-word at ~77px width
       (2026-08-03). Two-line labels are fine; ragged tiles are not. */
    min-height: 52px; padding: 8px 6px; margin: 0 !important; border-radius: 10px;
    cursor: pointer; text-align: center;
    transition: background .15s ease;
}
div[data-testid="stDialog"] [data-testid="stRadio"] div[role="radiogroup"] label > div:first-child {
    display: none !important;  /* hide the radio dot */
}
div[data-testid="stDialog"] [data-testid="stRadio"] div[role="radiogroup"] label p {
    font: 600 13.5px/1.3 Heebo, sans-serif !important; color: rgba(236,237,230,.6) !important;
    margin: 0 !important; word-break: normal !important; overflow-wrap: normal !important;
}
div[data-testid="stDialog"] [data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) {
    background: linear-gradient(180deg, var(--accent-hover), var(--accent));
    box-shadow: 0 2px 8px -2px var(--accent-border);
}
div[data-testid="stDialog"] [data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) p {
    color: #171A12 !important; font-weight: 700 !important;
}

/* ---- Buttons inside the modal ---- */
div[data-testid="stDialog"] .stButton button,
div[data-testid="stDialog"] .stDownloadButton button {
    border-radius: 12px !important; font: 700 14px Heebo, sans-serif !important;
    padding: 11px !important;
}
/* dark fill + olive outline + olive text (the mock's OUTLINED button), not the
   green-tinted accent-soft fill that read as a solid olive block */
div[data-testid="stDialog"] .st-key-letter_go button {
    background: var(--surface) !important;
    border: 1px solid var(--accent-border) !important; box-shadow: none !important;
}
/* accent-hover, not accent-bright: var(--accent-bright) reads as plain white on phone
   panels — the mock's button text is a clearly-olive #AAB37C */
div[data-testid="stDialog"] .st-key-letter_go button p { color: var(--accent-hover) !important; font-weight: 700 !important; }
/* the mock's pen glyph: monochrome, accent-tinted via mask (an emoji in the
   label renders full-color and clashes). RTL puts ::after at the LEFT end. */
div[data-testid="stDialog"] .st-key-letter_go button p::after {
    content: ""; display: inline-block; width: 15px; height: 15px;
    margin-inline-start: 9px; vertical-align: -2px;
    background-color: var(--accent-hover);
    -webkit-mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M12 20h9' fill='none' stroke='black' stroke-width='2' stroke-linecap='round'/%3E%3Cpath d='M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4Z' fill='none' stroke='black' stroke-width='2' stroke-linejoin='round'/%3E%3C/svg%3E") center / contain no-repeat;
    mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M12 20h9' fill='none' stroke='black' stroke-width='2' stroke-linecap='round'/%3E%3Cpath d='M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4Z' fill='none' stroke='black' stroke-width='2' stroke-linejoin='round'/%3E%3C/svg%3E") center / contain no-repeat;
}
div[data-testid="stDialog"] .st-key-letter_go button:hover {
    background: var(--accent-soft) !important; border-color: var(--accent) !important;
}
div[data-testid="stDialog"] .stDownloadButton button {
    background: transparent !important; border: 1px solid var(--accent-border) !important;
}
div[data-testid="stDialog"] .stDownloadButton button p { color: var(--accent) !important; }
div[data-testid="stDialog"] .stDownloadButton button:hover { border-color: var(--accent) !important; }

/* ---- Selectbox dropdown menu (the OPEN options list) ----
   BaseWeb portals the dropdown to <body>, OUTSIDE stDialog, so it can't be
   scoped to the dialog — and it inherited the same olive theme.backgroundColor
   leak as the modal card. Style it globally (selects only appear in these
   dialogs). The <ul> is the visible menu; the popover + its wrapper divs must go
   transparent so only the dark <ul> shows. Options are already light-on-transparent.

   ⚠ "selects only appear in these dialogs" stopped being true when the conscript
   map added two st.date_input fields. A datepicker portals through the SAME
   popover, but its body is a [data-baseweb="calendar"] and not a <ul> — so these
   three rules stripped the background off the ONLY thing that paints it, and the
   calendar came up fully see-through with the tool's own cards reading through
   the date grid (user device screenshot 2026-08-08). The calendar block below
   paints itself back at HIGHER SPECIFICITY rather than these rules carving out
   a :has() exception: an unsupported :has() invalidates the whole selector, and
   that failure mode would hand every SELECT its olive default back. */
div[data-baseweb="popover"],
div[data-baseweb="popover"] > div,
div[data-baseweb="popover"] > div > div { background: transparent !important; }
div[data-baseweb="popover"] ul {
    background: var(--surface) !important; border: 1px solid var(--border) !important;
    border-radius: 12px !important; padding: 5px !important;
    box-shadow: 0 18px 40px -14px rgba(0,0,0,.6) !important;
}
div[data-baseweb="popover"] li[role="option"] {
    color: var(--text) !important; font: 500 14px Heebo, sans-serif !important;
    border-radius: 8px !important; direction: rtl; text-align: right;
}
div[data-baseweb="popover"] li[role="option"]:hover {
    background: rgba(236,237,230,.06) !important;
}
div[data-baseweb="popover"] li[role="option"][aria-selected="true"] {
    background: var(--accent-soft) !important; color: var(--accent-bright) !important;
}

/* ---- Datepicker calendar (st.date_input — the conscript map's two dates) ----
   Every selector here carries the popover ancestor so it outranks the three
   transparency rules above (0,1,3); the calendar rule alone (0,1,1) loses to
   them and the panel comes up see-through.

   The calendar is pinned CENTRE-SCREEN as a sheet rather than left where
   BaseWeb put it. Two reasons, both measured: popper anchors to the field's
   rect at open time, and inside a dialog whose expander is still animating
   that rect is stale — the panel landed 20px ABOVE the field it belongs to
   and covered it. And at 375px a 340px panel has nowhere to be but centred.
   The pinning itself is in the gesture engine: popper writes its offset as an
   inline transform, which no stylesheet rule can outrank.

   direction:rtl on the whole subtree puts Sunday on the right, where a Hebrew
   calendar has it (verified: day 2 lands at x=473, day 8 at x=221). The month
   names and day initials are translated there too — BaseWeb ships en-US and
   Streamlit exposes no locale. */
div[data-baseweb="popover"] div[data-baseweb="calendar"],
div[data-baseweb="popover"] > div > div[data-baseweb="calendar"],
div[data-baseweb="popover"] > div > div > div[data-baseweb="calendar"] {
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: 18px !important;
    box-shadow: 0 26px 60px -18px rgba(0,0,0,.75) !important;
    padding: 10px 8px 12px !important;
    width: 100% !important;
    direction: rtl !important;
    font-family: Heebo, sans-serif !important;
}
div[data-baseweb="popover"] div[data-baseweb="calendar"] * {
    direction: rtl !important; font-family: Heebo, sans-serif !important;
}
/* month/year buttons — BaseWeb's default is a light-theme pill */
div[data-baseweb="popover"] div[data-baseweb="calendar"] button {
    background: transparent !important; color: var(--text) !important;
    font: 600 15px Heebo, sans-serif !important; border-radius: 10px !important;
}
div[data-baseweb="popover"] div[data-baseweb="calendar"] button:hover {
    background: rgba(236,237,230,.07) !important;
}
div[data-baseweb="popover"] div[data-baseweb="calendar"] svg { fill: var(--text) !important; }
/* the arrows are drawn for LTR: in an RTL month strip "previous" sits on the
   right and must point right */
div[data-baseweb="popover"] div[data-baseweb="calendar"] button > svg[data-baseweb="icon"] {
    transform: scaleX(-1);
}
div[data-baseweb="popover"] div[data-baseweb="calendar"] [role="gridcell"] {
    color: var(--text) !important; font: 500 14px Heebo, sans-serif !important;
    border-radius: 10px !important;
}
/* the day-of-week strip and out-of-month days read as secondary */
div[data-baseweb="popover"] div[data-baseweb="calendar"] [role="gridcell"][aria-disabled="true"],
div[data-baseweb="popover"] div[data-baseweb="calendar"] [role="gridcell"][aria-hidden="true"] {
    color: rgba(236,237,230,.28) !important;
}
div[data-baseweb="popover"] div[data-baseweb="calendar"] [role="gridcell"][aria-selected="true"] > div,
div[data-baseweb="popover"] div[data-baseweb="calendar"] [aria-selected="true"] {
    background: var(--accent) !important; color: #14170E !important; font-weight: 700 !important;
}
/* The sheet is bottom-anchored rather than vertically centred: the panel is
   40px taller in a 6-week month than a 5-week one, and an anchor that moves
   with the month reads as broken. Its geometry is written inline by the gesture
   engine (emotion rewrites this node's className on re-render, so a class does
   not survive a month change) — only the scrim is left to CSS, because it is
   decorative and may safely be absent where :has() is not supported.
   pointer-events:none keeps the tap that dismisses the sheet flowing through
   to BaseWeb's own outside-click handler. */
div[data-baseweb="popover"]:has(div[data-baseweb="calendar"])::before {
    content: ""; position: fixed; inset: 0; z-index: -1;
    background: rgba(8,10,6,.58); pointer-events: none;
}

/* ---- Hide "Press Enter to apply" — it overlaps the typed RTL text and reads
   as leftover default chrome inside the styled fields ---- */
div[data-testid="stDialog"] [data-testid="InputInstructions"] { display: none !important; }

/* ---- Phone width: stop paying for the same gutter twice ----
   Measured at 375px: the card takes 16px of margin and 22px of its own
   padding, and then Streamlit's own dialog body adds ANOTHER 24px inside it.
   62px per side — a third of the screen — left a 243px content column, which
   is why the authority selectbox could not show a rank range and every card
   wrapped a line early. The card keeps a gutter; the inner one goes, and the
   column comes back to 306px (+26%). Phones only: on a desktop the dialog is
   nowhere near width-bound and the extra padding is what makes it read as a
   card rather than a page. */
@media (max-width: 520px) {
  div[data-testid="stDialog"] div[role="dialog"] {
      padding-left: 14px !important; padding-right: 14px !important;
  }
  div[data-testid="stDialog"] div[role="dialog"] > div {
      padding-left: 0 !important; padding-right: 0 !important;
  }
  /* Streamlit's close button is 33px — under the 44px thumb floor, and it is
     the control every one of these dialogs is dismissed with */
  div[data-testid="stDialog"] button[aria-label="Close"] {
      width: 44px !important; height: 44px !important;
  }
  /* BaseWeb ellipsises the selected option on one nowrap line. "קצין שיפוט
     זוטר (קש״ז) — דרגת סגן עד סרן" needs 263px and had 236, so the rank range
     — the entire point of the option — was the part that got cut. Let it wrap
     and let the control grow to meet it. */
  div[data-testid="stDialog"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
      height: auto !important; min-height: 44px !important;
  }
  div[data-testid="stDialog"] [data-testid="stSelectbox"] div[data-baseweb="select"] [class] {
      white-space: normal !important; text-overflow: clip !important;
  }
  /* number-input steppers ship 31px wide — the obvious way to bump "years of
     service" by one, and too narrow for the thumb that wants to */
  div[data-testid="stDialog"] [data-testid="stNumberInputStepUp"],
  div[data-testid="stDialog"] [data-testid="stNumberInputStepDown"] {
      width: 46px !important; min-width: 46px !important; height: 44px !important;
  }
  div[data-testid="stDialog"] [data-testid="stNumberInput"] input { min-height: 44px !important; }
}

/* ---- Result card (shared by all three) ---- */
.cai-ent-card { position: relative; overflow: hidden; border-radius: 18px;
    background: linear-gradient(180deg,#20261A 0%,#161A11 100%);
    border: 1px solid var(--accent-border); padding: 20px 20px 22px;
    margin: 18px 0 6px; direction: rtl; text-align: right; }
.cai-ent-card::before { content: ""; position: absolute; top: 0; right: 0; bottom: 0;
    width: 3px; background: linear-gradient(180deg, var(--accent-bright), var(--accent)); }
.cai-ent-value { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; direction: rtl; }
.cai-ent-num { font: 800 42px Heebo, sans-serif; color: var(--accent-bright);
    line-height: .95; letter-spacing: -.01em; }
.cai-ent-unit { font: 700 24px Heebo, sans-serif; color: var(--accent-bright); }
.cai-ent-unit.sm { font-size: 18px; font-weight: 700; line-height: 1.4; }
.cai-ent-sub { font: 500 13px Heebo, sans-serif; color: var(--text-sec); margin-top: 8px; line-height: 1.5; }
.cai-ent-h { font: 700 13px Heebo, sans-serif; color: var(--text); margin: 12px 0 4px; }
.cai-ent-rows { margin-top: 16px; border-top: 1px solid rgba(236,237,230,.08); }
.cai-ent-row { display: flex; justify-content: space-between; align-items: center;
    gap: 10px; padding: 11px 0; }
.cai-ent-row:not(:last-child) { border-bottom: 1px solid rgba(236,237,230,.07); }
.cai-ent-row span { font: 400 13px Heebo, sans-serif; color: rgba(236,237,230,.5); }
.cai-ent-row b { font: 600 13.5px Heebo, sans-serif; color: var(--text); }
.cai-ent-list { margin: 2px 8px 2px 0; padding-right: 18px; }
.cai-ent-list li { font: 400 13px Heebo, sans-serif; color: var(--text); line-height: 1.6; }
.cai-ent-note { font: 400 12px Heebo, sans-serif; color: rgba(236,237,230,.5); line-height: 1.6; margin-top: 6px; }
.cai-ent-cite { display: inline-flex; align-items: center; gap: 8px; margin-top: 15px;
    background: var(--accent-soft); border: 1px solid var(--accent-border);
    border-radius: 10px; padding: 7px 13px; direction: rtl;
    font: 600 12px Heebo, sans-serif; color: var(--accent-bright); }
.cai-ent-cite::before { content: ""; width: 12px; height: 12px; flex: none;
    border: 1.5px solid var(--accent); border-radius: 3px; transform: rotate(45deg); }
.cai-ent-disc { display: flex; gap: 7px; margin: 16px 2px 0; direction: rtl; text-align: right; }
/* another disclaimer, same reasoning as the composer's — routed through the
   token so it tracks --text-faint rather than drifting on its own */
.cai-ent-disc span.g { flex: none; font-size: 12px; line-height: 1.55; color: var(--text-faint); }
.cai-ent-disc span.t { font: 400 11px Heebo, sans-serif; color: var(--text-faint); line-height: 1.55; }

/* ---- Punishment-authority views (share the card shell) ---- */
.cai-pa-intro { direction: rtl; text-align: right; font: 400 12.5px/1.6 Heebo, sans-serif;
    color: var(--text-sec); margin: 2px 0 4px; }
.cai-pa-caps { border-radius: 18px; overflow: hidden; position: relative; margin-top: 16px;
    background: linear-gradient(180deg,#20261A 0%,#161A11 100%);
    border: 1px solid var(--accent-border); padding: 6px 18px; direction: rtl; }
.cai-pa-caps::before { content: ""; position: absolute; top: 0; right: 0; bottom: 0; width: 3px;
    background: linear-gradient(180deg, var(--accent-bright), var(--accent)); }
.cai-pa-row { direction: rtl; display: flex; align-items: center; justify-content: space-between;
    gap: 10px; padding: 12px 0; }
.cai-pa-row:not(:last-child) { border-bottom: 1px solid rgba(236,237,230,.07); }
.cai-pa-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.cai-pa-pun { font: 600 13.5px Heebo, sans-serif; color: var(--text); }
/* --text-faint (.4) measures 3.48:1 at 10.5px — under AA. Raised locally
   rather than at the token, which is also used for decorative monospace where
   the faintness is the point; the app-wide sweep is a separate job. */
.cai-pa-clause { font: 500 10.5px Heebo, sans-serif; color: rgba(239,240,232,.58); }
.cai-pa-max { flex: 0 0 auto; border-radius: 9px; padding: 4px 12px; white-space: nowrap;
    font: 700 12.5px Heebo, sans-serif; border: 1px solid; }
.cai-pa-max.ok    { color:#A9C687; background:rgba(148,183,110,.13); border-color:rgba(148,183,110,.4); }
.cai-pa-max.plain { color:var(--text-sec); background:rgba(236,237,230,.05); border-color:var(--border); }
.cai-pa-max.no    { color:#D68C77; background:rgba(208,124,102,.10); border-color:rgba(208,124,102,.35); }
.cai-pa-box { direction: rtl; text-align: right; border: 1px solid var(--border);
    border-radius: 12px; padding: 13px 15px; margin-top: 12px; background: rgba(236,237,230,.03); }
.cai-pa-box-title { font: 700 13px Heebo, sans-serif; color: var(--text); margin-bottom: 5px; }
.cai-pa-box-body { font: 400 12.5px/1.65 Heebo, sans-serif; color: var(--text-sec); }
.cai-pa-tag { display: inline-flex; align-items: center; gap: 7px; margin-top: 8px;
    font: 600 11px Heebo, sans-serif; color: var(--accent-bright);
    background: var(--accent-soft); border: 1px solid var(--accent-border);
    border-radius: 9px; padding: 5px 11px; }
.cai-pa-tag::before { content: ""; width: 11px; height: 11px; flex: none;
    border: 1.5px solid var(--accent); border-radius: 3px; transform: rotate(45deg); }
.cai-pa-note { margin: 4px 8px 0 0; padding-right: 18px; }
.cai-pa-note li { font: 400 12px/1.6 Heebo, sans-serif; color: var(--text-dim); margin-bottom: 6px; }
.cai-pa-disc { direction: rtl; text-align: right; font: 400 11px/1.55 Heebo, sans-serif;
    color: rgba(236,237,230,.4); border-top: 1px solid rgba(236,237,230,.08);
    padding-top: 12px; margin-top: 16px; }

/* ---- Source-clause modal (📄 סעיף המקור) — the in-app clause preview ---- */
/* document-icon emblem (this modal shows the order's page, not the chevron mark) */
.cai-sc-emblem { width: 42px; height: 42px; border-radius: 13px; flex: none;
    background: var(--accent-soft); border: 1px solid var(--accent-border);
    display: flex; align-items: center; justify-content: center; color: var(--accent-bright); }
.cai-sc-emblem svg { width: 20px; height: 20px; }
/* clause subject + dim caption */
.cai-sc-ctitle { font: 600 15px Heebo, sans-serif; color: var(--text);
    line-height: 1.5; direction: rtl; text-align: right; }
.cai-sc-ccap { font: 400 12.5px Heebo, sans-serif; color: var(--text-dim);
    margin-top: 3px; direction: rtl; text-align: right; }
/* framed page preview: caption bar + the real (or placeholder) page render */
.cai-sc-preview { margin-top: 16px; border-radius: 16px; overflow: hidden;
    border: 1px solid rgba(236,237,230,.12); background: #0F110A; }
.cai-sc-pbar { display: flex; align-items: center; justify-content: space-between;
    padding: 9px 14px; background: rgba(236,237,230,.04);
    border-bottom: 1px solid rgba(236,237,230,.08); }
.cai-sc-pbar .pg { font: 600 11px Heebo, sans-serif; color: rgba(236,237,230,.55); }
.cai-sc-pbar .tag { font: 600 9.5px ui-monospace, Menlo, monospace;
    letter-spacing: 1.5px; color: var(--accent); opacity: .85; }
.cai-sc-preview img { display: block; width: 100%; }
.cai-sc-ph { height: 230px; display: flex; flex-direction: column;
    align-items: center; justify-content: center; text-align: center; color: var(--accent);
    background: repeating-linear-gradient(135deg,#15180F 0 11px,#12140C 11px 22px); }
.cai-sc-ph svg { opacity: .6; margin-bottom: 10px; }
.cai-sc-ph div { font: 600 11px ui-monospace, Menlo, monospace;
    letter-spacing: 1px; color: var(--text-faint); }
/* full-order CTA restyled as a solid olive button (kept an <a> to the PDF) */
.cai-sc-cta { display: flex; align-items: center; justify-content: center; gap: 9px;
    width: 100%; margin-top: 16px; padding: 13px; border-radius: 13px; box-sizing: border-box;
    background: var(--accent-soft); border: 1px solid var(--accent-border);
    color: var(--accent-bright) !important; font: 600 13.5px Heebo, sans-serif;
    text-decoration: none !important; transition: background .15s ease, border-color .15s ease; }
.cai-sc-cta svg { flex: none; width: 16px; height: 16px; }
.cai-sc-cta:hover { background: color-mix(in srgb, var(--accent) 22%, transparent) !important;
    border-color: var(--accent) !important; }
.cai-sc-disc { text-align: center; font: 400 11px Heebo, sans-serif; direction: rtl;
    color: rgba(236,237,230,.56); margin-top: 10px; line-height: 1.5; }

/* ---- Miluim tools (מה מגיע לי / קיבלתי צו) ---- */
/* the section headings that split a tool into chapters — at .45 they measured
   3.98:1, which for 11px letter-spaced caps is the hardest text in the tool to
   read on a phone outdoors. .6 is 5.4:1 and still clearly a label, not a title. */
.cai-mil-sec { display: flex; justify-content: space-between; align-items: baseline;
    font: 600 11px Heebo, sans-serif; letter-spacing: .06em; direction: rtl;
    color: rgba(236,237,230,.6); margin: 16px 2px 2px; }
.cai-mil-hero { direction: rtl; text-align: right; border-radius: 14px; padding: 13px 14px;
    background: var(--accent-soft); border: 1px solid var(--accent-border); margin-top: 8px; }
.cai-mil-hero .t { font: 700 15.5px Heebo, sans-serif; color: var(--accent-bright); }
.cai-mil-hero .s { font: 400 12.5px Heebo, sans-serif; color: rgba(236,237,230,.75); margin-top: 3px; }
.cai-mil-tiers { display: flex; gap: 6px; flex-wrap: wrap; direction: rtl; margin-top: 8px; }
.cai-mil-chip { font: 600 11.5px Heebo, sans-serif; border-radius: 99px; padding: 3px 10px;
    color: var(--accent-bright); background: var(--accent-soft);
    border: 1px solid var(--accent-border); }
.cai-mil-chip.off { color: rgba(236,237,230,.45); background: transparent;
    border-color: rgba(236,237,230,.16); }
.cai-mil-bar { height: 5px; border-radius: 99px; background: var(--border);
    overflow: hidden; margin-top: 9px; direction: rtl; }
.cai-mil-bar > span { display: block; height: 100%; background: var(--accent); border-radius: 99px; }
.cai-mil-det { direction: rtl; text-align: right; border-radius: 13px; margin-top: 8px;
    background: var(--surface); border: 1px solid var(--border); }
.cai-mil-det summary { list-style: none; cursor: pointer; padding: 12px 13px;
    display: flex; align-items: center; gap: 9px; -webkit-tap-highlight-color: transparent; }
.cai-mil-det summary::-webkit-details-marker { display: none; }
.cai-mil-det summary::after { content: "‹"; margin-inline-start: auto;
    color: rgba(236,237,230,.35); font-size: 15px; transform: rotate(-90deg);
    transition: transform .18s ease; }
.cai-mil-det[open] summary::after { transform: rotate(90deg); }
/* press feedback — the 2026-08-04 composite language (tint + accent border;
   scale-alone read as "no response"). Without these the dialog cards were the
   only surface under a finger that stayed silent (2026-08-06 device pass).
   summary-scoped, so the static cards (מה אסור / קווי סיוע) invite no tap. */
.cai-mil-det summary:active { background: rgba(236,237,230,.06); }
.cai-mil-det:has(> summary:active) { border-color: var(--accent-border); }
div[data-testid="stDialog"] [data-testid="stRadio"] label:active { filter: brightness(1.18); }
.cai-mil-det .tt { font: 600 14px Heebo, sans-serif; color: var(--text); }
.cai-mil-det .sb { font: 400 12px Heebo, sans-serif; color: rgba(236,237,230,.6);
    margin-top: 2px; line-height: 1.45; }
.cai-mil-tag { font: 600 10.5px Heebo, sans-serif; color: var(--accent-bright);
    background: var(--accent-soft); border: 1px solid var(--accent-border);
    border-radius: 99px; padding: 1px 8px; flex: none;
    /* a pill is an atom: it may DROP to the next line whole, never split
       mid-text ("מקור/אזרחי", "כי/סימנת סטודנט" — 2026-08-03 audit) */
    display: inline-block; white-space: nowrap; vertical-align: middle; }
.cai-mil-body { border-top: 1px solid rgba(236,237,230,.09); padding: 4px 13px 12px; }
.cai-mil-how { display: flex; gap: 8px; align-items: flex-start; direction: rtl;
    font: 400 12.5px Heebo, sans-serif; color: rgba(236,237,230,.78);
    line-height: 1.5; margin-top: 8px; }
.cai-mil-how .g { color: var(--accent); flex: none; font-weight: 700; }
/* The hotline links. These were 18px tall and, for a bare number like "1201",
   28px wide — the smallest targets in the app, sitting in the one tool a
   soldier opens with a shaking thumb. Padding to a 44px box, and the number
   links get a visible pill so a dialled number reads as a button and not as
   running text. inline-flex + min-height rather than a fixed height so a
   two-line label still centres. */
.cai-mil-link { display: inline-flex; align-items: center;
    min-height: 44px; padding: 0 12px; border-radius: 11px;
    font: 600 12.5px Heebo, sans-serif;
    color: var(--accent-bright) !important; text-decoration: none !important;
    background: rgba(var(--accent-rgb),.10);
    border: 1px solid rgba(var(--accent-rgb),.26);
    margin-top: 9px; -webkit-tap-highlight-color: transparent; }
.cai-mil-link:active { background: rgba(var(--accent-rgb),.20); }
/* .42 on the card fill measured 3.5:1 — under AA for 11px, and these carry the
   clause the whole card rests on. .58 measures 4.9:1 and still reads as the
   quietest thing on the card. */
.cai-mil-cite { font: 400 11px Heebo, sans-serif; color: rgba(236,237,230,.58);
    margin-top: 8px; line-height: 1.5; }
/* the question a card raises (soldier kit §3.1) — quieter than a link, since
   it is a prompt to ask and not a control; the real buttons sit at the foot
   of the dialog */
.cai-mil-ask { font: 400 11.5px Heebo, sans-serif; color: var(--accent-bright);
    margin-top: 9px; line-height: 1.55; opacity: .85; }
.cai-mil-foot { font: 400 11px Heebo, sans-serif; color: rgba(236,237,230,.56);
    direction: rtl; text-align: right; margin-top: 14px; line-height: 1.6; }
.cai-mil-warn { direction: rtl; text-align: right; border-radius: 13px; padding: 11px 13px;
    background: rgba(233,214,150,.07); border: 1px solid rgba(233,214,150,.35); margin-top: 8px; }
.cai-mil-warn .t { font: 600 12.5px Heebo, sans-serif; color: #E9D696; line-height: 1.5; }
.cai-mil-warn .c { font: 400 10.5px Heebo, sans-serif; color: rgba(233,214,150,.6); margin-top: 4px; }
/* the distress tool's act-now card — the warn card's red-muted sibling */
.cai-mil-crit { direction: rtl; text-align: right; border-radius: 13px; padding: 11px 13px;
    background: rgba(226,120,110,.09); border: 1px solid rgba(226,120,110,.4); margin-top: 8px; }
.cai-mil-crit .t { font: 600 12.5px Heebo, sans-serif; color: #E8AFA5; line-height: 1.5; }
.cai-mil-crit .r { font: 400 12px Heebo, sans-serif; color: rgba(232,175,165,.85);
    line-height: 1.55; margin-top: 4px; }
.cai-mil-crit .c { font: 400 10.5px Heebo, sans-serif; color: rgba(232,175,165,.75); margin-top: 4px; }
.cai-mil-tline { display: flex; align-items: center; direction: rtl; margin-top: 10px; }
.cai-mil-tline .d { width: 10px; height: 10px; border-radius: 50%; flex: none;
    background: var(--accent-soft); border: 2px solid var(--accent); }
.cai-mil-tline .seg { flex: 1; height: 2px; background: var(--accent-border); }
.cai-mil-tcaps { display: flex; direction: rtl; margin-top: 5px; }
.cai-mil-tcaps span { flex: 1; text-align: center; font: 400 10.5px Heebo, sans-serif;
    color: rgba(236,237,230,.5); line-height: 1.35; }
.cai-mil-num { width: 22px; height: 22px; border-radius: 50%; flex: none;
    display: inline-flex; align-items: center; justify-content: center;
    font: 700 11px Heebo, sans-serif; color: var(--accent-bright);
    background: var(--accent-soft); border: 1px solid var(--accent-border); }
</style>
"""


def _modal_header(title: str) -> str:
    """The shared premium modal header — chevron emblem + Suez-One title +
    the standing 'מעוגן בפקודות מטכ״ל · בלמ״ס' classification sub-label.
    Replaces Streamlit's native (now hidden) dialog title across all three
    side dialogs, and re-tints per role via the :root accent tokens."""
    return (
        "<div class='cai-mhead'>"
        "<div class='cai-memblem'><span></span><span></span></div>"
        "<div class='cai-mtitles'>"
        f"<div class='cai-mtitle'>{html.escape(title)}</div>"
        "<div class='cai-msub'>מעוגן בפקודות מטכ״ל · בלמ״ס</div>"
        "</div></div>"
    )


@st.dialog("בודק סמכות עונש משמעתי", width="large")
def _punishment_dialog():
    """Deterministic authority-of-punishment lookup, grounded in PM-33.0302.

    Quasi-legal, so it is conservative BY DESIGN: it surfaces the order's own
    caps with clause citations and never declares a punishment "illegal" — the
    disclaimer routes an over-cap punishment to "check / consider an appeal".
    Pure data lookup (punishment_authority.py), no Anthropic call, so it burns
    NO quota — unlike the letters dialog it never touches metrics.reserve.
    """
    if not _pa:
        return
    st.markdown(_modal_header("בודק סמכות עונש"), unsafe_allow_html=True)
    # The deadline leads. Everything else in this dialog is information the
    # soldier can read next week; this is the one thing that expires, and it
    # used to sit in a paragraph at the very bottom. Plain language first,
    # official term alongside (soldier-kit spec §3.3).
    _ap = _pa.APPEAL
    if _ap.get("deadline_days"):
        st.markdown(
            "<div class='cai-mil-crit'>"
            f"<div class='t'>{html.escape(_ap['plain'])} יש לך "
            f"{_ap['deadline_days']} ימים</div>"
            f"<div class='s'>מיום מתן הפסק · {_ap['deadline_days_prosecutor']} ימים "
            "אם הדיון נערך על-פי הוראת פרקליט</div>"
            f"<div class='cai-mil-cite'>{html.escape(_ap['term_note'])} · "
            f"{html.escape(_ap['clause'])}</div></div>",
            unsafe_allow_html=True,
        )
    st.markdown(
        "<div class='cai-pa-intro'>בחר את סוג קצין השיפוט כדי לראות אילו עונשים "
        "מרביים הוא מוסמך להטיל בדין משמעתי, לפי פ\"מ 33.0302 — ואת נתיב הערר.</div>",
        unsafe_allow_html=True,
    )
    options = _pa.officer_options()  # [(key, label)] junior -> senior
    labels = dict(options)
    key = st.selectbox(
        "סוג קצין השיפוט",
        [k for k, _ in options],
        format_func=lambda k: labels[k],
        key="pa_officer",
    )
    rec = _pa.authority_for(key)
    if not rec:
        st.info("לא נמצאו נתונים לסוג קצין השיפוט שנבחר.")
        return

    # caps table — each row: punishment + its clause tag, and the max as a
    # colored pill (olive = an authorised cap, red-muted = "לא מוסמך", so a
    # soldier can scan at a glance what this officer may and may not impose).
    rows_html = []
    for cap in rec["caps"]:
        mx = cap["max"]
        cls = "no" if mx == "לא מוסמך" else "plain" if mx == "מוסמך" else "ok"
        rows_html.append(
            "<div class='cai-pa-row'>"
            "<div class='cai-pa-main'>"
            f"<span class='cai-pa-pun'>{html.escape(cap['punishment'])}</span>"
            # the dialog intro already anchors the whole table to פ"מ 33.0302 —
            # repeating the order id on every row buried the per-row uniques
            # (2026-08-03 audit); the clause alone is the information
            f"<span class='cai-pa-clause'>{html.escape(cap['clause'])}</span>"
            "</div>"
            f"<span class='cai-pa-max {cls}'>{html.escape(mx)}</span>"
            "</div>"
        )
    st.markdown(
        f"<div class='cai-pa-caps'>{''.join(rows_html)}</div>",
        unsafe_allow_html=True,
    )

    # rank-specific footnote (e.g. only אל"ם may jail an officer/senior NCO)
    if rec.get("note"):
        st.markdown(
            "<div class='cai-pa-box'><div class='cai-pa-box-body'>ℹ️ "
            f"{html.escape(rec['note'])}</div></div>",
            unsafe_allow_html=True,
        )

    # appeal path (ערר) — always shown; it's the soldier's recourse
    appeal = _pa.APPEAL
    st.markdown(
        "<div class='cai-pa-box'>"
        "<div class='cai-pa-box-title'>↩️ נתיב ערר</div>"
        f"<div class='cai-pa-box-body'>{html.escape(appeal['text'])}</div>"
        f"<span class='cai-pa-tag'>לפי פ\"מ 33.0302 · {html.escape(appeal['clause'])}</span>"
        "</div>",
        unsafe_allow_html=True,
    )

    # cross-cutting caveats that apply regardless of rank
    notes = getattr(_pa, "GENERAL_NOTES", None)
    if notes:
        items = "".join(
            f"<li>{html.escape(n['text'])} "
            f"<span class='cai-pa-clause'>({html.escape(n['clause'])})</span></li>"
            for n in notes
        )
        st.markdown(
            "<div class='cai-pa-box'>"
            "<div class='cai-pa-box-title'>נקודות נוספות מהפקודה</div>"
            f"<ul class='cai-pa-note'>{items}</ul></div>",
            unsafe_allow_html=True,
        )

    # conservative disclaimer — this is guidance, the order is binding
    st.markdown(
        f"<div class='cai-pa-disc'>{_isvg(_I_ALERT, size=12)} {html.escape(_pa.DISCLAIMER)}</div>",
        unsafe_allow_html=True,
    )


def _hero_html(value: str) -> str:
    """Render an entitlement headline as the two-size hero (big number + unit).

    "7 ימים" -> 42px "7" + 24px "ימים"; "50%" -> 42px "50%"; a full sentence
    like "אין מכסת שחרור ייעודית" has no leading count, so it renders whole at
    the smaller unit size rather than being awkwardly split."""
    m = re.match(r'^\s*(\d+)\s*(%?)\s*(.*)$', value.strip())
    if m and m.group(1):
        num = html.escape(m.group(1) + m.group(2))
        unit = m.group(3).strip()
        tail = f"<span class='cai-ent-unit'>{html.escape(unit)}</span>" if unit else ""
        return f"<span class='cai-ent-num'>{num}</span>{tail}"
    return f"<span class='cai-ent-unit'>{html.escape(value)}</span>"


def _ent_card(html_inner: str) -> None:
    """Render one entitlement result card + the standing disclaimer."""
    st.markdown(
        f"<div class='cai-ent-card'>{html_inner}</div>"
        f"<div class='cai-ent-disc'><span class='g'>{_isvg(_I_ALERT, size=12)}</span>"
        f"<span class='t'>{html.escape(entitlements.DISCLAIMER)}</span></div>",
        unsafe_allow_html=True,
    )


def _ent_leave_ui() -> None:
    """Calculator A — leave days (PM-35.0402), value + clause citation."""
    cats = entitlements.leave_categories()
    titles = dict(cats)
    cat_key = st.selectbox(
        "סוג החופשה", [k for k, _ in cats],
        format_func=lambda k: titles[k], key="ent_leave_cat",
    )
    cases = entitlements.leave_cases(cat_key)
    idx = 0
    pick = entitlements.leave_pick_label(cat_key)
    if pick:
        idx = st.selectbox(
            pick, list(range(len(cases))),
            format_func=lambda i: cases[i]["label"], key=f"ent_leave_case_{cat_key}",
        )
    r = entitlements.leave_result(cat_key, idx)
    note = (f"<div class='cai-ent-note'>{html.escape(r['note'])}</div>"
            if r.get("note") else "")
    _ent_card(
        f"<div class='cai-ent-value'>{_hero_html(r['days'])}</div>"
        f"<div class='cai-ent-sub'>{html.escape(titles[cat_key])} · "
        f"{html.escape(r['label'])}</div>"
        f"<div class='cai-ent-rows'>"
        f"<div class='cai-ent-row'><span>גורם מאשר</span><b>{html.escape(r['approver'])}</b></div>"
        f"<div class='cai-ent-row'><span>סל הזכאות</span><b>{html.escape(r['account'])}</b></div>"
        f"</div>{note}"
        f"<div class='cai-ent-cite'>{html.escape(r['citation'])}</div>"
    )


def _ent_pay_ui() -> None:
    """Calculator B — subsistence (35.0201) + family payments (35.0210).

    Grounded: neither source states a flat shekel figure. 35.0201 gives a
    structure (amount set by the CoS, CPI-updated); 35.0210 gives a percentage
    table of a "basic wage" that tracks the average wage — surfaced as-is.
    """
    kind = st.selectbox(
        "סוג התשלום", ["subsist", "family"],
        format_func=lambda k: {
            "subsist": 'דמי קיום חודשיים (פ"מ 35.0201)',
            "family": 'תשלום למשפחת החייל (פ"מ 35.0210)',
        }[k],
        key="ent_pay_kind",
    )
    if kind == "subsist":
        s = entitlements.subsistence_structure()
        comps = "".join(f"<li>{html.escape(c)}</li>" for c in s["components"])
        sups = "".join(f"<li>{html.escape(c)}</li>" for c in s["supplements"])
        _ent_card(
            f"<div class='cai-ent-value'><span class='cai-ent-unit sm'>"
            f"{html.escape(s['headline'])}</span></div>"
            f"<div class='cai-ent-note'>{html.escape(s['how_set'])}</div>"
            f"<div class='cai-ent-h'>רכיבי דמי הקיום</div>"
            f"<ul class='cai-ent-list'>{comps}</ul>"
            f"<div class='cai-ent-h'>תוספות כספיות</div>"
            f"<ul class='cai-ent-list'>{sups}</ul>"
            f"<div class='cai-ent-cite'>{html.escape(s['citation'])}</div>"
        )
        return
    recips = entitlements.family_recipients()
    rlabels = dict(recips)
    rk = st.selectbox(
        "מקבל התשלום", [k for k, _ in recips],
        format_func=lambda k: rlabels[k], key="ent_fam_recip",
    )
    band, band_label = None, ""
    if entitlements.family_needs_minors(rk):
        bands = entitlements.FAMILY_MINOR_BANDS
        blabels = dict(bands)
        band = st.selectbox(
            "מספר קטינים במשפחה", [k for k, _ in bands],
            format_func=lambda k: blabels[k], key="ent_fam_band",
        )
        band_label = " · " + blabels[band]
    p = entitlements.family_payment(rk, band)
    _ent_card(
        f"<div class='cai-ent-value'>{_hero_html(p['percent'])}</div>"
        f"<div class='cai-ent-sub'>מהשכר הבסיסי · {html.escape(p['label'])}"
        f"{html.escape(band_label)}</div>"
        f"<div class='cai-ent-note'>{html.escape(p['note'])}</div>"
        f"<div class='cai-ent-note'>{html.escape(p['base_note'])}</div>"
        f"<div class='cai-ent-note'>{html.escape(p['ceiling_note'])}</div>"
        f"<div class='cai-ent-cite'>{html.escape(p['citation'])}</div>"
    )


@st.dialog("מחשבון זכאויות", width="large")
def _entitlements_dialog():
    """Deterministic entitlement lookup: exact leave-day counts and the
    subsistence/family-payment structure, each value quoted to its clause.

    No daily quota and NO Anthropic call — it only reads curated, order-cited
    data from entitlements.py, so it can't burn budget or hallucinate a figure.
    """
    st.markdown(_modal_header("מחשבון זכאויות"), unsafe_allow_html=True)
    calc = st.radio(
        "מה לחשב?", ["leave", "pay"],
        format_func=lambda k: {"leave": "ימי חופשה",
                               "pay": "דמי קיום / תשלומים"}[k],
        key="ent_calc", horizontal=True,
    )
    if calc == "leave":
        _ent_leave_ui()
    else:
        _ent_pay_ui()


def _mil_details_row(r: dict) -> str:
    """One benefit row as a native <details> accordion — client-side open/
    close, zero rerun (the same reason the orders panel is client-side)."""
    tag = f"<span class='cai-mil-tag'>{html.escape(r['tag'])}</span>" if r.get("tag") else ""
    civil = "<span class='cai-mil-tag'>מקור אזרחי</span>" if r.get("civil") else ""
    hows = "".join(
        f"<div class='cai-mil-how'><span class='g'>✓</span><span>{html.escape(h)}</span></div>"
        for h in r["how"]
    )
    link = (f"<a class='cai-mil-link' href='{html.escape(r['link'], quote=True)}' "
            f"target='_blank' rel='noopener'>{html.escape(r.get('link_label') or 'למקור הרשמי')} ↗</a>"
            if r.get("link") else "")
    asof = f" · נכון ל-{html.escape(r['asof'])}" if r.get("asof") else ""
    # 2026-08-06 soldier-kit spec §3.1: a card is a funnel INTO the chat, which
    # is the product — so a row may carry the question it raises. Shown as text
    # and not a control on purpose: the whole map is ONE st.markdown, so a
    # per-row Streamlit button is impossible, and simulating a click from JS is
    # the class of thing that breaks in standalone Safari. The dialog's bottom
    # strip turns these same questions into real buttons.
    ask = (f"<div class='cai-mil-ask'>אפשר לשאול: «{html.escape(r['ask'])}»</div>"
           if r.get("ask") else "")
    return (
        "<details class='cai-mil-det'>"
        "<summary><span style='min-width:0'>"
        f"<span class='tt'>{html.escape(r['title'])}</span> {tag}{civil}"
        f"<div class='sb'>{html.escape(r['sub'])}</div>"
        "</span></summary>"
        f"<div class='cai-mil-body'>{hows}{link}"
        f"<div class='cai-mil-cite'>{html.escape(r['cite'])}{asof}</div>"
        f"{ask}</div></details>"
    )


def _ask_strip(rows: list[dict], key_prefix: str, limit: int = 4) -> None:
    """The funnel back into the chat (soldier-kit spec §3.1).

    The map is one st.markdown, so the per-row question can only be TEXT.
    These are the same questions as real buttons: tapping one closes the
    dialog (a full rerun does), queues the question, and lets the main script
    answer it — the tool's job is to hand the user off to the product.
    """
    asks = [r["ask"] for r in rows if r.get("ask")][:limit]
    if not asks:
        return
    st.markdown("<div class='cai-mil-sec'><span>לשאול על זה בצ׳אט</span>"
                f"<span>{len(asks)}</span></div>", unsafe_allow_html=True)
    for i, q in enumerate(asks):
        if st.button(q, key=f"{key_prefix}_{i}", use_container_width=True):
            queue_question(q)
            # the dialog opened over a drawer that is still open behind it;
            # the drawer is a client-side class, so drop it the same way the
            # cookie is written — otherwise the answer streams out of sight
            components.html(
                "<script>try{window.top.document.documentElement.classList"
                ".remove('cai-drawer-open','cai-drawer-drag');}catch(e){}</script>",
                height=0,
            )
            st.rerun()


@st.dialog("מה מגיע לי במילואים", width="large")
def _miluim_benefits_dialog():
    """The reserve flagship: a personal entitlements map from curated,
    source-cited data (miluim_benefits.py). Deterministic, NO Anthropic call,
    no quota. First open collects the profile (saved to the device cookie via
    the mil mirrors); afterwards it opens straight on the map. The salary is
    used only for the local estimate — never joins the chat profile."""
    if not _mb:
        return
    st.markdown(_modal_header("מה מגיע לי במילואים"), unsafe_allow_html=True)

    if not st.session_state.get("mil_saved"):
        st.markdown(
            "<div class='cai-pa-intro'>הזנה חד-פעמית — נשמרת במכשיר, וניתנת "
            "לעדכון בכל רגע. התוצאה: מפת הזכאויות האישית שלך, עם מקור לכל שורה.</div>",
            unsafe_allow_html=True,
        )
        dy = st.number_input("ימי מילואים בשנה הנוכחית", min_value=0, max_value=400,
                             step=1, value=st.session_state.get("mil_days_year"),
                             placeholder="למשל: 46", key="mil_dy_w")
        d3 = st.number_input("ימי מילואים בשלוש השנים האחרונות (כולל השנה)",
                             min_value=0, max_value=1000, step=1,
                             value=st.session_state.get("mil_days_3y"),
                             placeholder="למשל: 118", key="mil_d3_w")
        emp_labels = dict(_mb.EMP_OPTIONS)
        emp = st.pills("מה מתאר אותך? (אפשר כמה)", [k for k, _ in _mb.EMP_OPTIONS],
                       format_func=lambda k: emp_labels[k], selection_mode="multi",
                       default=[e for e in (st.session_state.get("mil_emp") or [])
                                if e in emp_labels], key="mil_emp_w")
        sal = st.number_input("שכר חודשי ברוטו — אופציונלי, להערכת תגמול בלבד",
                              min_value=0, max_value=200000, step=500,
                              value=st.session_state.get("mil_salary"),
                              placeholder="אפשר לדלג", key="mil_sal_w")
        if st.button("הצג מה מגיע לי", key="mil_go", use_container_width=True):
            if dy is None or d3 is None:
                st.warning("צריך את שני שדות הימים כדי לחשב את המפה.")
            else:
                st.session_state.mil_days_year = int(dy)
                # the 3-year window contains the current year — clamp quietly
                st.session_state.mil_days_3y = max(int(d3), int(dy))
                st.session_state.mil_emp = list(emp or [])
                st.session_state.mil_salary = int(sal) if sal else None
                st.session_state.mil_saved = True
                # fragment scope: a full rerun would CLOSE the dialog and
                # strand the user back in the drawer — this repaints the
                # dialog in place, straight onto the map
                st.rerun(scope="fragment")
        st.markdown(
            "<div class='cai-sc-disc'>הנתונים נשמרים במכשיר בלבד. השכר משמש "
            "לחישוב מקומי של הערכת התגמול — ולא נשלח לצ׳אט.</div>",
            unsafe_allow_html=True,
        )
        return

    # ── the map ──
    dy = int(st.session_state.get("mil_days_year") or 0)
    d3 = int(st.session_state.get("mil_days_3y") or 0)
    emp = set(st.session_state.get("mil_emp") or [])
    sal = st.session_state.get("mil_salary")

    status = _mb.active_status(d3)
    if status["active"]:
        hero = (f"<div class='cai-mil-hero'><div class='t'>משרת מילואים פעיל ✓</div>"
                f"<div class='s'>{d3} ימ\"מ בתלת-שנתי — מעל סף ה-{status['threshold']}</div>"
                f"<div class='cai-mil-cite'>{html.escape(status['cite'])}</div></div>")
    else:
        hero = (f"<div class='cai-mil-hero'><div class='t'>עוד {status['gap']} ימים למעמד משרת פעיל</div>"
                f"<div class='s'>{d3} ימ\"מ בתלת-שנתי מתוך {status['threshold']} הנדרשים</div>"
                f"<div class='cai-mil-cite'>{html.escape(status['cite'])}</div></div>")

    tiers = _mb.year_tiers(dy)
    chips = "".join(
        (f"<span class='cai-mil-chip'>{t['threshold']}+ ✓</span>" if t["passed"] else
         f"<span class='cai-mil-chip off'>{t['threshold']}+ · עוד {t['gap']}</span>")
        for t in tiers
    )
    nxt = next((t for t in tiers if not t["passed"]), None)
    pct = 100 if nxt is None else min(100, int(dy * 100 / nxt["threshold"]))
    tier_card = (
        "<div class='cai-mil-det' style='padding:12px 13px'>"
        f"<div class='sb' style='margin:0 0 7px'>מדרגות השנה · {dy} ימ\"מ"
        + (f" · הבאה: {html.escape(nxt['label'])}" if nxt else " · כל המדרגות הושגו")
        + f"</div><div class='cai-mil-tiers'>{chips}</div>"
        f"<div class='cai-mil-bar'><span style='width:{pct}%'></span></div></div>"
    )

    est_html = ""
    est = _mb.tagmul_estimate(sal) if sal else None
    if est:
        est_html = (
            "<div class='cai-mil-hero' style='margin-top:8px'>"
            f"<div class='t'>הערכת תגמול: ~{est['daily']:,.0f} ₪ ליום</div>"
            f"<div class='s'>בטווח {est['min_daily']:,.2f}–{est['max_daily']:,.2f} ₪ · "
            "הערכה בלבד — המחשבון הרשמי של ביטוח לאומי קובע</div>"
            f"<div class='cai-mil-cite'>{html.escape(est['cite'])} · נכון ל-{est['asof']}</div></div>"
        )

    rows = _mb.benefit_rows(dy, d3, emp, bool(sal))
    sections_html = []
    for sec in _mb.SECTION_ORDER:
        sec_rows = [r for r in rows if r["section"] == sec]
        if not sec_rows:
            continue
        sections_html.append(
            f"<div class='cai-mil-sec'><span>{html.escape(_mb.SECTION_LABELS[sec])}</span>"
            f"<span>{len(sec_rows)}</span></div>"
            + "".join(_mil_details_row(r) for r in sec_rows)
        )
    lp = _mb.LOCAL_POINTER
    pointer = (
        "<div class='cai-mil-det' style='border-style:dashed;padding:12px 13px'>"
        f"<span class='tt'>{html.escape(lp['title'])}</span>"
        f"<div class='sb'>{html.escape(lp['sub'])}</div>"
        f"<a class='cai-mil-link' href='{html.escape(lp['link'], quote=True)}' target='_blank' "
        "rel='noopener'>לריכוז באתר המילואים ↗</a></div>"
    )
    st.markdown(
        hero + tier_card + est_html + "".join(sections_html) + pointer
        + f"<div class='cai-mil-foot'>{_isvg(_I_REFRESH, size=11)} המקורות נבדקו לאחרונה: {_mb.LAST_VERIFIED}"
        f"<br>{_isvg(_I_ALERT, size=11)} {html.escape(_mb.DISCLAIMER)}</div>",
        unsafe_allow_html=True,
    )
    if st.button("עדכון נתונים", key="mil_edit", use_container_width=True):
        st.session_state.mil_saved = False
        st.rerun(scope="fragment")


@st.dialog("קיבלתי צו — מה עכשיו?", width="large")
def _miluim_guide_dialog():
    """The deferral route (ולת"ם), deterministic from פ"מ 31.0603 via
    miluim_guide.py — cause picker, timeline, the standing "the order binds"
    warning, and four expandable steps. The ONLY paid action is the letter
    button at the bottom, which follows the exact letters-dialog quota
    contract (reserve → compose → log, refund on failure)."""
    if not _mg:
        return
    st.markdown(_modal_header("קיבלתי צו — מה עכשיו?"), unsafe_allow_html=True)

    # timeline: 4 dots, painted RTL so the first stage is rightmost
    tline = ("<div class='cai-mil-tline'><span class='d'></span><span class='seg'></span>"
             "<span class='d'></span><span class='seg'></span><span class='d'></span>"
             "<span class='seg'></span><span class='d'></span></div>"
             "<div class='cai-mil-tcaps'>"
             + "".join(f"<span>{html.escape(t)}</span>" for t in _mg.TIMELINE)
             + "</div>")
    warn = (f"<div class='cai-mil-warn'><div class='t'>{_isvg(_I_ALERT, size=12)} {html.escape(_mg.STANDING_WARNING['text'])}</div>"
            f"<div class='c'>{html.escape(_mg.STANDING_WARNING['cite'])}</div></div>")
    st.markdown(tline + warn, unsafe_allow_html=True)

    labels = dict(_mg.CAUSES)
    cause = st.radio("מה הקושי שלך להתייצב?", [k for k, _ in _mg.CAUSES],
                     format_func=lambda k: labels[k], key="mil_cause", horizontal=True)
    g = _mg.guide_for(cause)
    note = (f"<div class='cai-mil-cite' style='margin:6px 2px 0'>{html.escape(g['note'])}</div>"
            if g.get("note") else "")
    steps_html = []
    for i, s in enumerate(g["steps"], start=1):
        lines = "".join(
            f"<div class='cai-mil-how'><span class='g'>✓</span><span>{html.escape(ln)}</span></div>"
            for ln in s["lines"]
        )
        steps_html.append(
            f"<details class='cai-mil-det'{' open' if i == 1 else ''}>"
            f"<summary><span class='cai-mil-num'>{i}</span>"
            f"<span class='tt'>{html.escape(s['title'])}</span></summary>"
            f"<div class='cai-mil-body'>{lines}"
            f"<div class='cai-mil-cite'>{html.escape(s['cite'])}</div></div></details>"
        )
    st.markdown(note + "".join(steps_html), unsafe_allow_html=True)

    # ── the letter step — the one paid action, same contract as the letters
    # dialog (quota reserve → compose → analytics log; refund on failure) ──
    st.markdown("<div class='cai-mil-sec'><span>צריך גם מכתב בקשה?</span></div>",
                unsafe_allow_html=True)
    if not (LETTER_TYPES and _mg.LETTER_KEY in LETTER_TYPES):
        st.caption("ניסוח המכתב אינו זמין כרגע.")
        return
    lt = LETTER_TYPES[_mg.LETTER_KEY]
    details = {}
    for i, field in enumerate(lt["fields"]):
        label, placeholder = field[0], field[1]
        details[label] = st.text_input(label, placeholder=placeholder or None,
                                       key=f"mil_letter_f{i}")
    if st.button("נסח לי את מכתב הבקשה", key="mil_letter_go", use_container_width=True):
        quota = metrics.reserve(st.session_state.session_id)
        if quota != "ok":
            st.warning(_QUOTA_NOTICES[quota])
        else:
            try:
                t0 = time.time()
                with st.spinner("מנסח טיוטה מעוגנת בפקודות..."):
                    draft = compose_letter(_mg.LETTER_KEY, details,
                                           role=st.session_state.role)
                st.session_state.mil_letter_draft = draft
                st.session_state.mil_letter_edit = draft["text"]
                if st.session_state.get("share_analytics", True):
                    metrics.log_question(
                        session_id=st.session_state.session_id,
                        device_id=st.session_state.device_id,
                        role=st.session_state.role or "",
                        question=f"[מכתב] {lt['title']}",
                        answer=draft["text"],
                        sources=draft.get("sources"),
                        usage=draft.get("usage"),
                        latency_s=time.time() - t0,
                    )
            except (APIConnectionError, APITimeoutError):
                metrics.refund(st.session_state.session_id)
                st.error("אין כרגע חיבור לשירות. בדוק את החיבור ונסה שוב בעוד רגע.")
            except BadRequestError as e:
                metrics.refund(st.session_state.session_id)
                st.error("⏸️ המערכת בהשהיה זמנית עקב מגבלת שימוש — נסה שוב מחר."
                         if "usage limits" in str(e)
                         else "אירעה שגיאה זמנית בניסוח. נסה לשלוח שוב.")
            except Exception as e:
                safe_print(f"[mil-letter] draft failed: {e!r}")
                metrics.refund(st.session_state.session_id)
                st.error("אירעה שגיאה זמנית בניסוח. נסה לשלוח שוב.")
    st.markdown(
        "<div class='cai-sc-disc'>זה הצעד היחיד בכלי שפונה למודל — בעלות של "
        "שאלה אחת מהמכסה היומית. הטיוטה דורשת קריאה והשלמה לפני הגשה.</div>",
        unsafe_allow_html=True,
    )
    draft = st.session_state.get("mil_letter_draft")
    if draft:
        if draft.get("truncated"):
            st.warning("הטיוטה נקטעה באמצע בגלל אורך — קצר את הפרטים ונסח שוב, או השלם את הסיום ידנית.")
        st.text_area("הטיוטה — קרא, השלם את החסר וערוך לפני הגשה", height=320,
                     key="mil_letter_edit")
        st.download_button(
            "⬇️ הורד כקובץ",
            data=(st.session_state.get("mil_letter_edit") or draft["text"]).encode("utf-8"),
            file_name="commandai-valtam-letter.txt",
            mime="text/plain",
            use_container_width=True,
            key="mil_letter_dl",
        )
        srcs = draft.get("sources") or []
        if srcs:
            st.caption("מעוגן בפקודות: " + " · ".join(s["title"] for s in srcs[:2]))


def _guide_steps_html(steps: list) -> str:
    """Numbered step cards for the commander guides — the miluim guide's
    markup, plus the spec-§4 body: rule lines (✓), action lines (‹), one
    official link, and the citation. First card opens by default."""
    out = []
    for i, s in enumerate(steps, start=1):
        lines = "".join(
            f"<div class='cai-mil-how'><span class='g'>✓</span><span>{html.escape(ln)}</span></div>"
            for ln in s["lines"]
        )
        hows = "".join(
            f"<div class='cai-mil-how'><span class='g'>‹</span><span>{html.escape(h)}</span></div>"
            for h in s.get("how") or []
        )
        link = (f"<a class='cai-mil-link' href='{html.escape(s['link'], quote=True)}' "
                f"target='_blank' rel='noopener'>{html.escape(s.get('link_label') or 'למקור הרשמי')} ↗</a>"
                if s.get("link") else "")
        out.append(
            f"<details class='cai-mil-det'{' open' if i == 1 else ''}>"
            f"<summary><span class='cai-mil-num'>{i}</span>"
            f"<span class='tt'>{html.escape(s['title'])}</span></summary>"
            f"<div class='cai-mil-body'>{lines}{hows}{link}"
            f"<div class='cai-mil-cite'>{html.escape(s['cite'])}</div></div></details>"
        )
    return "".join(out)


@st.dialog("מה מגיע לי בקבע", width="large")
def _keva_benefits_dialog():
    """The keva mirror of the miluim map: curated rows from keva_benefits.py,
    deterministic, NO Anthropic call, no quota. The two light inputs live in
    session state only — deliberately no device-cookie mirror, so every other
    user's cookie payload stays byte-identical to today's format."""
    if not _kb:
        return
    st.markdown(_modal_header("מה מגיע לי בקבע"), unsafe_allow_html=True)
    years = st.number_input("שנות שירות (כולל שירות החובה)", min_value=0, max_value=50,
                            step=1, value=st.session_state.get("kv_years"),
                            placeholder="למשל: 6", key="kv_years_w")
    family = st.toggle("יש בן/בת זוג או ילדים עד גיל 18",
                       value=bool(st.session_state.get("kv_family")), key="kv_family_w")
    st.session_state.kv_years = int(years) if years is not None else None
    st.session_state.kv_family = bool(family)

    if years is not None:
        lv = _kb.seniority_leave(int(years))
        st.markdown(
            f"<div class='cai-mil-hero'><div class='t'>{lv['days']} ימי חופשה שנתית</div>"
            f"<div class='s'>{html.escape(lv['tier_label'])}</div>"
            f"<div class='cai-mil-cite'>{html.escape(lv['cite'])}</div></div>",
            unsafe_allow_html=True,
        )

    rows = _kb.benefit_rows(int(years or 0), bool(family))
    by_sec: dict[str, list] = {}
    for r in rows:
        by_sec.setdefault(r["section"], []).append(r)
    parts = []
    for sec in _kb.SECTION_ORDER:
        if not by_sec.get(sec):
            continue
        parts.append(f"<div class='cai-mil-sec'><span>{html.escape(_kb.SECTION_LABELS[sec])}</span></div>")
        parts.extend(_mil_details_row(r) for r in by_sec[sec])
    lp = _kb.LOCAL_POINTER
    if lp:
        parts.append(
            "<div class='cai-mil-det' style='padding:12px 13px'>"
            f"<div class='tt'>{html.escape(lp['title'])}</div>"
            f"<div class='sb'>{html.escape(lp['sub'])}</div>"
            f"<a class='cai-mil-link' href='{html.escape(lp['link'], quote=True)}' "
            f"target='_blank' rel='noopener'>{html.escape(lp['cite'])} ↗</a></div>"
        )
    parts.append(
        f"<div class='cai-mil-foot'>🔄 המקורות נבדקו לאחרונה: {_kb.LAST_VERIFIED}"
        f"<br>⚠️ {html.escape(_kb.DISCLAIMER)}</div>"
    )
    st.markdown("".join(parts), unsafe_allow_html=True)


@st.dialog("מה מגיע לי בשירות חובה", width="large")
def _conscript_map_dialog():
    """The conscript flagship: a rights map on a SERVICE AXIS, from curated
    source-cited data (conscript_map.py). Deterministic, NO Anthropic call.

    Two spec rules shape this function:
      * "הקלט מחדד, לא פותח" — the map is rendered BEFORE the form is even
        offered, and every row appears with an empty profile. This is what
        lets it replace the entitlements calculator, which answered without
        asking anything.
      * The discharge date is an INPUT. Mandatory-service length is in no
        order in the corpus, so the tool never derives it — the soldier
        supplies a date they know by heart and everything else is arithmetic
        on their own data.
    """
    if not _cm:
        return
    st.markdown(_modal_header("מה מגיע לי בשירות חובה"), unsafe_allow_html=True)

    prof = {
        "enlist": st.session_state.get("sol_enlist"),
        "discharge": st.session_state.get("sol_discharge"),
        "track": st.session_state.get("sol_track"),
        "single": st.session_state.get("sol_single"),
        "married": st.session_state.get("sol_married"),
    }
    status = _cm.service_status(prof["enlist"], prof["discharge"])

    # ── the personalisation strip. An expander, not a gate: closed it costs
    # one line, and the map below is already complete without it. ──
    _has = any((prof["enlist"], prof["discharge"], prof["track"]))
    with st.expander("התאמה אישית" if not _has else "עדכון הנתונים שלי",
                     expanded=False):
        _c1, _c2 = st.columns(2)
        with _c1:
            _en = st.date_input("תאריך גיוס", value=prof["enlist"], format="DD/MM/YYYY",
                                min_value=_dt.date(2015, 1, 1),
                                max_value=_dt.date(2035, 12, 31), key="sol_en_w")
        with _c2:
            _di = st.date_input("תאריך שחרור צפוי", value=prof["discharge"],
                                format="DD/MM/YYYY", min_value=_dt.date(2015, 1, 1),
                                max_value=_dt.date(2035, 12, 31), key="sol_di_w")
        _tr_labels = [_cm.TRACKS[k]["label"] for k in _cm.TRACK_ORDER]
        _tr_idx = (_cm.TRACK_ORDER.index(prof["track"])
                   if prof["track"] in _cm.TRACK_ORDER else 0)
        _tr = st.radio("מסלול", _tr_labels, index=_tr_idx, horizontal=True,
                       key="sol_tr_w")
        _sg = st.checkbox("חייל בודד", value=bool(prof["single"]), key="sol_sg_w")
        _mr = st.checkbox("נשוי", value=bool(prof["married"]), key="sol_mr_w")
        if st.button("עדכון המפה", key="sol_go", use_container_width=True):
            st.session_state.sol_enlist = _en
            st.session_state.sol_discharge = _di
            st.session_state.sol_track = _cm.TRACK_ORDER[_tr_labels.index(_tr)]
            st.session_state.sol_single = _sg
            st.session_state.sol_married = _mr
            st.session_state.sol_saved = True
            # fragment scope: a full rerun would CLOSE the dialog and strand
            # the user back in the drawer (the miluim lesson)
            st.rerun(scope="fragment")
        st.markdown(
            "<div class='cai-sc-disc'>הנתונים נשמרים במכשיר בלבד ואינם נשלחים "
            "לצ׳אט. תאריך השחרור נלקח ממך ואינו מחושב — אורך שירות החובה נקבע "
            "בחקיקה ואינו מופיע בפקודות.</div>",
            unsafe_allow_html=True,
        )

    parts: list[str] = []
    if status["days_left"] is not None and not status["released"]:
        _served = (f" · {status['months_served']} חודשי שירות"
                   if status["months_served"] is not None else "")
        parts.append(
            "<div class='cai-mil-hero'><div class='t'>נשארו "
            f"{status['days_left']:,} ימים</div>"
            f"<div class='s'>עד {_cm._fmt(prof['discharge'])}{_served}</div></div>"
        )
    elif status["released"]:
        parts.append(
            "<div class='cai-mil-hero'><div class='t'>אחרי השחרור</div>"
            "<div class='s'>מוצגות גם הזכויות שממשיכות אחרי תום השירות</div></div>"
        )

    for sec, rows in _cm.rows_by_section(prof):
        parts.append(
            f"<div class='cai-mil-sec'><span>{html.escape(_cm.SECTION_LABELS[sec])}</span>"
            f"<span>{len(rows)}</span></div>"
            + "".join(_mil_details_row(r) for r in rows)
        )
    parts.append(
        f"<div class='cai-mil-foot'>{_isvg(_I_REFRESH, size=11)} המקורות נבדקו "
        f"לאחרונה: {_cm.LAST_VERIFIED}"
        f"<br>{_isvg(_I_ALERT, size=11)} {html.escape(_cm.DISCLAIMER)}</div>"
    )
    st.markdown("".join(parts), unsafe_allow_html=True)
    _ask_strip(_cm.benefit_rows(prof), "solask")


@st.dialog("אני במצוקה", width="large")
def _soldier_distress_dialog():
    """The soldier's side of distress. Separate module from distress_guide
    (the commander asks "how do I help my soldier", the soldier asks "what
    happens to me") — same three verified orders, different audience.

    The emergency card is rendered OUTSIDE the accordion on purpose: someone
    in a bad moment should not have to tap to reach a phone number.
    """
    if not _sd:
        return
    st.markdown(_modal_header("אני במצוקה"), unsafe_allow_html=True)

    lines = "".join(
        (f"<div class='cai-mil-how'><span class='g'>•</span><span>"
         f"{html.escape(h['name'])} — <b>{html.escape(h['phone'])}</b></span></div>")
        for h in _sd.hotlines()
    )
    parts = [
        "<div class='cai-mil-crit'>"
        f"<div class='t'>{html.escape(_sd.EMERGENCY['title'])}</div>"
        f"<div class='s'>{html.escape(_sd.EMERGENCY['sub'])}</div>"
        f"{lines}"
        f"<div class='cai-mil-cite'>{html.escape(_sd.EMERGENCY['life_threat'])}</div>"
        "</div>"
    ]
    parts.append(
        "<div class='cai-mil-sec'><span>מה חשוב לדעת</span>"
        f"<span>{len(_sd.cards())}</span></div>"
        + "".join(_mil_details_row(c) for c in _sd.cards())
    )
    parts.append(
        f"<div class='cai-mil-foot'>{_isvg(_I_REFRESH, size=11)} קווי הסיוע "
        f"אומתו: {_sd.HOTLINES_VERIFIED}"
        f"<br>{_isvg(_I_ALERT, size=11)} {html.escape(_sd.DISCLAIMER)}</div>"
    )
    st.markdown("".join(parts), unsafe_allow_html=True)
    _ask_strip(_sd.cards(), "sdask")


@st.dialog("חייל לא התייצב", width="large")
def _absence_dialog():
    """The commander's absence track in the "קיבלתי צו" shape: branch picker,
    timeline, the pinned check-before-declaring warning, cited steps.
    Deterministic (absence_guide.py), no quota."""
    if not _ab:
        return
    st.markdown(_modal_header("חייל לא התייצב"), unsafe_allow_html=True)
    tline = ("<div class='cai-mil-tline'><span class='d'></span><span class='seg'></span>"
             "<span class='d'></span><span class='seg'></span><span class='d'></span>"
             "<span class='seg'></span><span class='d'></span></div>"
             "<div class='cai-mil-tcaps'>"
             + "".join(f"<span>{html.escape(t)}</span>" for t in _ab.TIMELINE)
             + "</div>")
    warn = (f"<div class='cai-mil-warn'><div class='t'>{_isvg(_I_ALERT, size=12)} "
            f"{html.escape(_ab.STANDING_WARNING['text'])}</div>"
            f"<div class='c'>{html.escape(_ab.STANDING_WARNING['cite'])}</div></div>")
    st.markdown(tline + warn, unsafe_allow_html=True)
    labels = dict(_ab.BRANCHES)
    branch = st.radio("מי החייל?", [k for k, _ in _ab.BRANCHES],
                      format_func=lambda k: labels[k], horizontal=True, key="ab_branch")
    g = _ab.guide_for(branch)
    if not g:
        return
    st.markdown(_guide_steps_html(g["steps"]), unsafe_allow_html=True)
    st.markdown(f"<div class='cai-mil-foot'>⚠️ {html.escape(_ab.DISCLAIMER)}</div>",
                unsafe_allow_html=True)


@st.dialog("חייל במצוקה נפשית", width="large")
def _distress_dialog():
    """Emergency-first: the act-now card, the cited steps, the explicit
    prohibitions, and the externally-verified civilian hotlines. Deterministic
    (distress_guide.py), no quota."""
    if not _dg:
        return
    st.markdown(_modal_header("חייל במצוקה נפשית"), unsafe_allow_html=True)
    em = _dg.EMERGENCY
    crit = (f"<div class='cai-mil-crit'><div class='t'>{_isvg(_I_ALERT, size=12)} "
            f"{html.escape(em['title'])}</div>"
            + "".join(f"<div class='r'>{html.escape(l)}</div>" for l in em["lines"])
            + f"<div class='c'>{html.escape(em['cite'])}</div></div>")
    st.markdown(crit + _guide_steps_html(_dg.STEPS), unsafe_allow_html=True)
    forb = ["<div class='cai-mil-sec'><span>מה אסור</span></div>"]
    for f in _dg.FORBIDDEN:
        tag = f"<span class='cai-mil-tag'>{html.escape(f['tag'])}</span>" if f.get("tag") else ""
        forb.append(
            "<div class='cai-mil-det' style='padding:11px 13px'>"
            f"<div class='tt'>{html.escape(f['title'])} {tag}</div>"
            f"<div class='cai-mil-cite'>{html.escape(f['cite'])}</div></div>"
        )
    hot_rows = []
    for h in _dg.HOTLINES:
        nm = html.escape(h["name"])
        if h.get("link"):
            nm = (f"<a class='cai-mil-link' style='margin-top:0' "
                  f"href='{html.escape(h['link'], quote=True)}' "
                  f"target='_blank' rel='noopener'>{nm} ↗</a>")
        phone = (f" · <a class='cai-mil-link' style='margin-top:0' "
                 f"href='tel:{html.escape(h['phone'], quote=True)}'>{html.escape(h['phone'])}</a>"
                 if h.get("phone") else "")
        hot_rows.append(f"<div class='cai-mil-how'><span class='g'>‹</span><span>{nm}{phone}</span></div>")
    hot = ("<div class='cai-mil-sec'><span>קווי סיוע אזרחיים</span></div>"
           "<div class='cai-mil-det' style='padding:11px 13px'>" + "".join(hot_rows)
           + f"<div class='cai-mil-cite'>המספרים אומתו לאחרונה: {_dg.HOTLINES_VERIFIED}</div></div>")
    st.markdown("".join(forb) + hot
                + f"<div class='cai-mil-foot'>⚠️ {html.escape(_dg.DISCLAIMER)}</div>",
                unsafe_allow_html=True)


@st.dialog("אירוע ביחידה — למי מדווחים", width="large")
def _incident_dialog():
    """The incident router: pick what happened, get who-to-report, what not to
    do alone, and what to do until the investigators arrive. Deterministic
    (incident_guide.py), no quota."""
    if not _ig:
        return
    st.markdown(_modal_header("אירוע ביחידה"), unsafe_allow_html=True)
    warn = (f"<div class='cai-mil-warn'><div class='t'>{_isvg(_I_ALERT, size=12)} "
            f"{html.escape(_ig.STANDING_WARNING['text'])}</div>"
            f"<div class='c'>{html.escape(_ig.STANDING_WARNING['cite'])}</div></div>")
    st.markdown(warn, unsafe_allow_html=True)
    labels = dict(_ig.EVENTS)
    ev = st.radio("מה קרה?", [k for k, _ in _ig.EVENTS],
                  format_func=lambda k: labels[k], horizontal=True, key="ig_event")
    g = _ig.guide_for(ev)
    if not g:
        return
    st.markdown(_guide_steps_html(g["steps"]), unsafe_allow_html=True)
    st.markdown(f"<div class='cai-mil-foot'>⚠️ {html.escape(_ig.DISCLAIMER)}</div>",
                unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════
# Drawer + Settings — redesigned surface (mockup 2a + 8a–8e).
# The settings screens are an APP-OWNED overlay (a keyed st.container + a
# backdrop, driven by a settings_screen state machine) — the SAME proven
# pattern as the drawer. Not st.dialog: a dialog dismiss doesn't run the full
# script (so the state machine would strand), and dialogs can't nest. The
# overlay sidesteps both, and it fills the screen like the mockup.
# ═══════════════════════════════════════════════════════════════════════════
import urllib.parse as _uparse


def _svg(inner: str, stroke: str = "#AAB37C", sw: str = "1.7", w: int = 18) -> str:
    """A stroke-only 24-viewBox icon as a data: URI, for CSS background-image."""
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{w}' "
           f"viewBox='0 0 24 24' fill='none' stroke='{stroke}' stroke-width='{sw}' "
           f"stroke-linecap='round' stroke-linejoin='round'>{inner}</svg>")
    return "data:image/svg+xml," + _uparse.quote(svg)


def _isvg(inner: str, size: int = 13, dy: str = "-2px") -> str:
    """The same 24-viewBox stroke language as _ICON, inlined for HTML surfaces.
    currentColor — the icon takes the surrounding text color, so a faint
    disclaimer gets a faint mark (the color emoji it replaces ignored the
    text hierarchy and popped at full saturation on every device differently)."""
    return (f"<svg viewBox='0 0 24 24' width='{size}' height='{size}' fill='none' "
            f"stroke='currentColor' stroke-width='1.7' stroke-linecap='round' "
            f"stroke-linejoin='round' style='vertical-align:{dy};flex:none'>{inner}</svg>")


# inline marks for the answer/dialog surfaces (one line language, no emoji)
_I_ALERT = ("<path d='M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 "
            "1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z'/><path d='M12 9v4'/><path d='M12 17h.01'/>")
_I_REFRESH = "<path d='M2.5 4.5v5h5'/><path d='M4.2 15a9 9 0 1 0 1.8-9.2L2.5 9.5'/>"
_I_COMPASS = "<circle cx='12' cy='12' r='9'/><path d='m15.5 8.5-2 5-5 2 2-5z'/>"


_ICON = {
    "letters": _svg("<path d='M6 3h8l4 4v14H6z'/><path d='M14 3v4h4'/><path d='M9 12h6M9 16h6'/>"),
    "gavel": _svg("<path d='M12 3v18'/><path d='M9 21h6'/><path d='M5 7h14'/>"
                  "<path d='M5 7l-2.6 5a2.6 2.6 0 0 0 5.2 0z'/><path d='M19 7l-2.6 5a2.6 2.6 0 0 0 5.2 0z'/>"),
    "calc": _svg("<rect x='5' y='3' width='14' height='18' rx='2'/><path d='M8 7h8'/>"
                 "<path d='M9 12h.01M12 12h.01M15 12h.01M9 16h.01M12 16h.01M15 16h.01'/>"),
    # miluim tools: award medal ("מה מגיע לי") + clipboard route ("קיבלתי צו")
    "medal": _svg("<circle cx='12' cy='9' r='5'/><path d='M9 13.5 7 21l5-2 5 2-2-7.5'/>"),
    "clipboard": _svg("<rect x='6' y='4' width='12' height='17' rx='2'/>"
                      "<path d='M9 2h6v4H9z'/><path d='M9.5 11h5M9.5 15h5'/>"),
    # commander kit: star (keva map), heart (distress protocol); the absence
    # and incident rows reuse the existing clock/bell strokes
    "star": _svg("<path d='M12 3l2.7 5.6 6.2.9-4.5 4.3 1.1 6.2-5.5-2.9-5.5 2.9 "
                 "1.1-6.2L3.1 9.5l6.2-.9z'/>"),
    "heart": _svg("<path d='M19.5 12.6 12 20l-7.5-7.4a5 5 0 1 1 7.5-6.6 "
                  "5 5 0 1 1 7.5 6.6'/>"),
    "book": _svg("<rect x='4' y='3' width='12' height='16' rx='2'/><path d='M8 3v16'/>"
                 "<path d='M18 6v13a2 2 0 0 1-2 2H7'/>", stroke="#C4CE92"),
    "user": _svg("<path d='M20 21a8 8 0 0 0-16 0'/><circle cx='12' cy='7' r='4'/>"),
    "bell": _svg("<path d='M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9'/><path d='M13.7 21a2 2 0 0 1-3.4 0'/>"),
    "globe": _svg("<circle cx='12' cy='12' r='9'/><path d='M3 12h18'/>"
                  "<path d='M12 3a15 15 0 0 1 0 18 15 15 0 0 1 0-18'/>"),
    "trash": _svg("<path d='M3 6h18'/><path d='M8 6V4h8v2'/><path d='M6 6l1 14h10l1-14'/>"),
    "lock": _svg("<rect x='4' y='10' width='16' height='11' rx='2'/><path d='M8 10V7a4 4 0 0 1 8 0v3'/>"),
    "info": _svg("<circle cx='12' cy='12' r='9'/><path d='M12 16v-4'/><path d='M12 8h.01'/>"),
    "clock": _svg("<path d='M12 2a10 10 0 1 0 10 10'/><path d='M12 6v6l4 2'/>"),
    "chart": _svg("<path d='M3 3v18h18'/><path d='M7 14l4-4 3 3 5-6'/>"),
    "chat": _svg("<path d='M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z'/>"),
    "search": _svg("<circle cx='11' cy='11' r='7'/><path d='m20.5 20.5-4.6-4.6'/>",
                   stroke="#8A9077", w=15),
    "shield": _svg("<path d='M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z'/><path d='M9 12l2 2 4-4'/>", stroke="#C4CE92", w=24),
    "gear": _svg("<circle cx='12' cy='12' r='3'/><path d='M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z'/>", stroke="#ECEDE6"),
}

# CSS for the redesigned drawer + settings overlay. Plain string (single
# braces); icon data-URIs are spliced in by token so the CSS body stays literal.
_DS_CSS = """
<style id="cai-ds">
/* ═══ DRAWER — open/closed state (client-side) ═══
   The panel and its backdrop are ALWAYS rendered; one class on <html> —
   flipped by the gesture engine, never by the server — decides whether they
   are on screen. That is what makes open/close instant and repaint-free.
   visibility (not display) so the closed panel is out of the tab order and
   the hit-testing tree while the transform stays animatable; it flips at the
   END of the close transition (0s delay on open) so the slide-out is seen. */
.st-key-cai_drawer {
  transform: translateX(100%);
  visibility: hidden;
  transition: transform .26s cubic-bezier(.2,.7,.2,1), visibility 0s linear .26s;
}
.st-key-drawer_backdrop {
  opacity: 0; visibility: hidden; pointer-events: none;
  transition: opacity .26s ease, visibility 0s linear .26s;
}
html.cai-drawer-open .st-key-cai_drawer {
  transform: none; visibility: visible;
  transition: transform .26s cubic-bezier(.2,.7,.2,1), visibility 0s;
}
html.cai-drawer-open .st-key-drawer_backdrop {
  opacity: 1; visibility: visible; pointer-events: auto;
  transition: opacity .26s ease, visibility 0s;
}
/* mid-drag: the panel is pinned to the finger by an inline transform, so
   every transition must be off or it would lag behind by a quarter second */
html.cai-drawer-drag .st-key-cai_drawer,
html.cai-drawer-drag .st-key-drawer_backdrop {
  transition: none !important; visibility: visible !important;
}
html.cai-drawer-drag .st-key-drawer_backdrop { pointer-events: none !important; }
/* the page header ducks while the drawer moves or sits open: mid-drag the
   identity cluster floated over the incoming panel and the two "headers"
   interleaved (device screenshot 2026-08-03).

   ⚠ Duck the CONTENTS, never the header itself. `.cai-header` is not just a
   row of text — it is the only thing that paints the status-bar strip: it
   grows UP by --cai-sat precisely so its tint and blur sit behind the
   translucent clock (see its rule above, and the seam the same band caused
   once already). Fading the element took that fill away with it, so the
   instant a finger touched the drawer the strip behind the clock dropped to
   bare canvas and a lighter band appeared across the top — the 2026-08-08
   device screenshot. The children carry the interleaving; the band carries
   the strip. Only the children go. */
html.cai-drawer-open .cai-header > *, html.cai-drawer-drag .cai-header > * {
  opacity: 0; transition: opacity .18s ease;
}

/* ═══ DRAWER — redesigned (mockup 2a) ═══ */
.st-key-cai_drawer {
  width: min(85vw, 320px) !important;
  background: linear-gradient(180deg,#121509 0%,#0E1007 100%) !important;
  border-inline-end: 1px solid rgba(236,237,230,.08) !important;
  box-shadow: -14px 0 44px rgba(0,0,0,.5) !important;
  /* top inset from max(--cai-sat, env()): standalone iOS reports env()=0
     (no viewport-fit=cover), so env() alone put the gear/« under the clock
     on the device (user screenshot 2026-08-03) — same recipe as the
     settings pane below */
  padding: max(42px, calc(max(var(--cai-sat, 0px), env(safe-area-inset-top,0px)) + 12px)) 16px calc(env(safe-area-inset-bottom,0px) + 10px) !important;
  display: flex !important; flex-direction: column !important;
  /* the keyed container IS the stVerticalBlock (1.58) — no inner wrapper to
     size. Height comes from the fixed inset; margin-top:auto on the CTA pins
     it to the bottom; overflow-y (base CSS) scrolls when content is taller. */
  gap: 0 !important;
}
.st-key-cai_drawer [data-testid="stElementContainer"] { margin-bottom: 0; }
/* Streamlit gives stMarkdownContainer margin-bottom:-1rem (offsets the 16px
   bottom margin of a markdown <p>). Our blocks are raw <div>s with no <p>, so
   the -16px goes UNCANCELLED and every markdown pulls its successor 16px up —
   section labels land ON the card above and the recent-head row collapses.
   Zero it here; all rhythm comes from the blocks' own margins. Dialogs too:
   the miluim tools are built from the same raw <div>s — the letter-form and
   profile-form labels overlapped their headings by 6px, and the benefits foot
   lost its last line under the update button (measured 2026-08-03). */
.st-key-cai_drawer [data-testid="stMarkdownContainer"],
.st-key-cai_settings [data-testid="stMarkdownContainer"],
div[data-testid="stDialog"] [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
/* top row: gear (right) + close « (left) */
.st-key-cai_drawer div[data-testid="stHorizontalBlock"]:first-of-type { align-items: center; }
/* push each top-row button to the OUTER edge of its column (auto cross-axis
   margins — direction-agnostic; the element container is button-width). */
.st-key-open_settings { margin: 0 0 0 auto !important; }
.st-key-drawer_close { margin: 0 auto 0 0 !important; }
/* Streamlit stacks columns vertically in a narrow container — force our
   column rows (top bar, recent head, settings header) to stay horizontal. */
.st-key-cai_drawer div[data-testid="stHorizontalBlock"],
.st-key-cai_settings div[data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 8px !important; }
.st-key-cai_drawer div[data-testid="stColumn"],
.st-key-cai_settings div[data-testid="stColumn"] { min-width: 0 !important; }
/* 44px, not 36: these two sit in the top corners of the drawer — the least
   accurate reach on a phone — and 36 is under the tap floor. The glyphs keep
   their 18px size; only the boxes grow. Same call as .st-key-settings_back. */
.st-key-open_settings button, .st-key-drawer_close button {
  width: 44px !important; height: 44px !important; min-height: 44px !important;
  border-radius: 12px !important; padding: 0 !important;
  background-color: rgba(236,237,230,.06) !important;
  border: 1px solid rgba(236,237,230,.12) !important;
  color: rgba(236,237,230,.65) !important;
  display: flex; align-items: center; justify-content: center;
}
.st-key-open_settings button p { font-size: 0 !important; }
.st-key-open_settings button {
  background-image: url("ICON_GEAR") !important; background-repeat: no-repeat !important;
  background-position: center !important; background-size: 18px 18px !important;
}
.st-key-drawer_close button p { font: 600 16px Heebo !important; color: rgba(236,237,230,.6) !important; direction: ltr !important; }

/* role card */
.cai-role-card {
  display: flex; align-items: center; gap: 12px; margin-top: 8px;
  padding: 12px 13px; border-radius: 14px;
  background: linear-gradient(135deg,rgba(var(--accent-rgb),.16),rgba(var(--accent-rgb),.04));
  border: 1px solid rgba(var(--accent-rgb),.3);
}
.cai-role-av {
  width: 40px; height: 40px; border-radius: 12px; flex: none;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg,#AEB784,#8E9962);
  border: 1px solid rgba(196,206,146,.5);
  font: 700 20px 'Suez One', serif; color: #171A12;
}
.cai-role-meta { flex: 1; min-width: 0; }
.cai-role-k { font: 600 10px Heebo; letter-spacing: 1px; color: rgba(236,237,230,.6); }
.cai-role-nm { font: 400 17px 'Suez One', serif; color: var(--text); line-height: 1.15; margin-top: 1px; }
.cai-role-badge {
  font: 600 10.5px Heebo; color: rgba(196,206,146,.9); flex: none;
  background: rgba(var(--accent-rgb),.14); border: 1px solid rgba(var(--accent-rgb),.34);
  border-radius: 99px; padding: 4px 10px;
}

/* section label */
/* the drawer's group headings — same 4.5:1 floor as the settings ones */
.cai-sec-label { font: 600 11px Heebo; letter-spacing: 1px; color: rgba(236,237,230,.58); margin: 16px 0 8px; }
/* RTL hard-pin (user video, iPhone): Streamlit right-pane CSS lands
   text-align:left on plain markdown <div>s even under direction:rtl, so the
   section labels (מאגר הידע / כלים / שיחות אחרונות), the role-card texts and
   the settings/תקנון copy all hugged the LEFT edge. Force start-side
   alignment on every markdown text node inside the drawer + settings;
   flex rows (cards, pills, chevrons) are position-driven and unaffected. */
.st-key-cai_drawer [data-testid="stMarkdownContainer"],
.st-key-cai_drawer [data-testid="stMarkdownContainer"] div,
.st-key-cai_drawer [data-testid="stMarkdownContainer"] p,
.st-key-cai_settings [data-testid="stMarkdownContainer"],
.st-key-cai_settings [data-testid="stMarkdownContainer"] div,
.st-key-cai_settings [data-testid="stMarkdownContainer"] p {
  text-align: right;
  direction: rtl;
}
.cai-recent-head { direction: rtl; }

/* knowledge-base card — custom accent card (icon + title + count pill + ‹).
   A REAL <button>, for two reasons: the swipe engine's tap heuristic keys off
   `button, a` to grant the generous 30px thumb-roll slop, and it keeps the
   accordion keyboard-operable. Expansion is driven by one class on <html>
   (like the drawer itself) so it survives Streamlit replacing this node. */
.cai-kb-card {
  display: flex; align-items: center; gap: 12px; padding: 13px 14px; border-radius: 14px;
  background: linear-gradient(135deg,rgba(var(--accent-rgb),.18),rgba(var(--accent-rgb),.05));
  border: 1px solid rgba(var(--accent-rgb),.34);
  width: 100%; text-align: right; font: inherit; cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}
.cai-kb-card .kb-ic { width: 18px; height: 18px; flex: none; background: url("ICON_BOOK") center / 18px no-repeat; }
.cai-kb-card .kb-title { flex: 1; font: 700 14px Heebo; color: var(--text); }
.cai-kb-card .kb-badge { flex: none; font: 800 11px Heebo; color: #171A12; background: var(--accent); border-radius: 99px; padding: 2px 9px; }
.cai-kb-card .kb-chev { flex: none; color: rgba(196,206,146,.8); font-size: 15px; direction: ltr; transition: transform .18s ease; }
.cai-kb-card .kb-chev::before { content: "‹"; }
html.cai-orders-open .cai-kb-card .kb-chev { transform: rotate(90deg); }
/* the body is inert markup that is always present; only CSS decides whether
   it is on screen, which is why opening costs no round-trip */
.cai-kb-body { display: none; }
html.cai-orders-open .cai-kb-body { display: block; }
/* open state (mockup): card + search + list read as ONE bordered card with a
   darker well; the card head keeps only its top corners */
html.cai-orders-open .st-key-cai_kb .cai-kb {
  border: 1px solid rgba(var(--accent-rgb),.34); border-radius: 16px;
  background: rgba(0,0,0,.22);
}
html.cai-orders-open .cai-kb-card {
  border: none; border-radius: 16px 16px 0 0;
  border-bottom: 1px solid rgba(var(--accent-rgb),.18);
}
/* search box — a plain <input>, not st.text_input: a Streamlit widget commits
   on blur and reruns the script, so every query cost the same ~3.5s as the
   toggle used to. This one filters the rows already in the DOM. */
.cai-orders-q {
  display: block; width: calc(100% - 24px); margin: 10px 12px 0;
  background: rgba(239,240,232,.045);
  border: 1px solid var(--border-strong); border-radius: 10px;
  color: var(--text); font: 400 13px Heebo, sans-serif;
  direction: rtl; padding: 8px 12px; outline: none;
}
.cai-orders-q::placeholder { color: rgba(239,240,232,.5); }
.cai-orders-q:focus { border-color: rgba(var(--accent-rgb),.5); }
.cai-orders-q::-webkit-search-cancel-button { -webkit-appearance: none; }
.cai-orders-empty { font: 400 12px Heebo; color: var(--text-faint); padding: 10px 14px; }
/* [hidden] alone loses to .cai-order-link's display:block (author sheet beats
   the UA rule), so the filter's hidden rows need an explicit override */
.cai-order-link[hidden], .cai-orders-empty[hidden] { display: none !important; }
/* the expanded orders list scrolls INSIDE the card region (mockup: search
   stays put, only the lines scroll) instead of stretching the whole drawer */
.cai-orders-scroll {
  max-height: min(45svh, 330px);
  overflow-y: auto; overscroll-behavior: contain;
  -webkit-overflow-scrolling: touch;
  margin: 6px 12px 8px 8px;
}
.cai-orders-scroll::-webkit-scrollbar { width: 4px; }
.cai-orders-scroll::-webkit-scrollbar-thumb { background: rgba(236,237,230,.18); border-radius: 3px; }

/* grouped card of rows (tools + recent) */
.st-key-cai_tools, .st-key-cai_recent {
  border-radius: 15px; overflow: hidden;
  background: var(--surface); border: 1px solid var(--border);
}
.st-key-cai_tools [data-testid="stElementContainer"],
.st-key-cai_recent [data-testid="stElementContainer"] { margin: 0 !important; }
/* empty state: st.caption ships with zero inset and LTR left-alignment, so
   "אין שיחות קודמות" sat glued to the box corner (2026-08-03 audit) */
.st-key-cai_recent [data-testid="stCaptionContainer"] {
  padding: 13px 14px !important; text-align: center !important;
}
.st-key-cai_tools button, .st-key-cai_recent button {
  background: transparent !important; border: none !important; border-radius: 0 !important;
  padding: 13px 14px !important; margin: 0 !important; min-height: 0 !important;
  text-align: right; box-shadow: none !important;
  border-top: 1px solid rgba(236,237,230,.07) !important;
  position: relative; justify-content: flex-start !important;
}
.st-key-cai_tools [data-testid="stElementContainer"]:first-child button,
.st-key-cai_recent [data-testid="stElementContainer"]:first-child button { border-top: none !important; }
.st-key-cai_tools button p, .st-key-cai_recent button p {
  font: 500 14px Heebo !important; color: var(--text) !important; text-align: right !important;
  width: 100%; box-sizing: border-box;
  padding-inline-start: 40px; padding-inline-end: 24px;
}
/* Streamlit nests the label in content-width flex wrappers that center it —
   force the whole chain full-width so text-align:right actually right-aligns. */
.st-key-cai_tools button > div, .st-key-cai_recent button > div, [class*="st-key-cai_sgrp"] button > div,
.st-key-cai_tools button > div > span, .st-key-cai_recent button > div > span, [class*="st-key-cai_sgrp"] button > div > span,
.st-key-cai_tools button [data-testid="stMarkdownContainer"], .st-key-cai_recent button [data-testid="stMarkdownContainer"],
[class*="st-key-cai_sgrp"] button [data-testid="stMarkdownContainer"] { width: 100% !important; }
/* leading icon + trailing chevron on tool rows */
.st-key-cai_tools button::before {
  content: ""; position: absolute; inset-inline-start: 14px; top: 50%;
  transform: translateY(-50%); width: 18px; height: 18px;
  background-repeat: no-repeat; background-position: center; background-size: 18px;
}
.st-key-open_letters button::before { background-image: url("ICON_LETTERS"); }
.st-key-open_punishment button::before { background-image: url("ICON_GAVEL"); }
.st-key-open_entitlements button::before { background-image: url("ICON_CALC"); }
.st-key-open_mil_benefits button::before { background-image: url("ICON_MEDAL"); }
.st-key-open_mil_guide button::before { background-image: url("ICON_CLIPBOARD"); }
.st-key-open_keva_benefits button::before { background-image: url("ICON_STAR"); }
.st-key-open_absence button::before { background-image: url("ICON_CLOCK"); }
.st-key-open_distress button::before { background-image: url("ICON_HEART"); }
.st-key-open_incident button::before { background-image: url("ICON_BELL"); }
/* soldier kit — the map reuses the medal (it is the "מה מגיע לי" slot, same
   as the miluim map), distress reuses the heart from the commander's */
.st-key-open_cmap button::before,
.st-key-open_cmap_cmd button::before { background-image: url("ICON_MEDAL"); }
.st-key-open_sdistress button::before { background-image: url("ICON_HEART"); }
.st-key-cai_tools button::after, .st-key-cai_recent button::after {
  content: "‹"; position: absolute; inset-inline-end: 14px; top: 50%;
  transform: translateY(-50%); color: rgba(236,237,230,.3); font-size: 14px;
}
@media (hover: hover) {
  .st-key-cai_tools button:hover, .st-key-cai_recent button:hover { background: rgba(236,237,230,.04) !important; }
}

/* recent head row */
.cai-recent-head { display: flex; align-items: center; gap: 8px; margin: 16px 0 8px; }
.cai-recent-t { font: 600 11px Heebo; letter-spacing: 1px; color: rgba(236,237,230,.58); }
.cai-recent-n { font: 700 10px Heebo; color: rgba(196,206,146,.9); background: rgba(var(--accent-rgb),.14); border-radius: 99px; padding: 1px 7px; }
.st-key-clear_recent { display: flex; justify-content: flex-end; }
.st-key-clear_recent button {
  background: transparent !important; border: none !important; box-shadow: none !important;
  padding: 0 !important; min-height: 0 !important; margin: 16px 0 8px !important; width: auto !important;
}
.st-key-clear_recent button p { font: 500 11px Heebo !important; color: rgba(236,237,230,.35) !important; }

/* footer CTA — soft accent (mockup 2a) + classification */
.st-key-new_chat { margin-top: auto !important; padding-top: 10px; }
.st-key-new_chat button {
  background: rgba(var(--accent-rgb),.12) !important;
  border: 1px solid rgba(var(--accent-rgb),.4) !important;
  color: var(--accent-bright) !important; border-radius: 13px !important;
  font: 600 13.5px Heebo !important; padding: 11px !important;
}
.st-key-new_chat button p { color: var(--accent-bright) !important; font-weight: 600 !important; }
@media (hover: hover) { .st-key-new_chat button:hover { background: rgba(var(--accent-rgb),.2) !important; } }
.cai-drawer-foot {
  /* safe-area clearance now lives on the drawer's own padding-bottom */
  text-align: center; margin: 8px 0 2px;
  /* .3 at 9px measured 2.43:1. It is styled as a whisper on purpose, but this
     particular whisper is the CLASSIFICATION MARKING — the one string in the
     app that has to be readable on a bright parade ground. .5 is 4.0:1, still
     the quietest element on the panel. */
  font: 600 9px ui-monospace, Menlo, monospace; letter-spacing: 2px; color: rgba(236,237,230,.5);
}

/* ═══ SETTINGS overlay (mockup 8a–8e) ═══ */
/* same inline-width collapse as the drawer backdrop (see the base CSS): the
   element container is stamped with width:32px, so inset:0 never widened it
   and the scrim was a 32×28 dark box in the corner instead of the screen */
.st-key-settings_backdrop {
  position: fixed; inset: 0; z-index: 135;
  width: 100% !important; height: 100% !important;
}
.st-key-settings_backdrop div[data-testid="stButton"] {
  width: 100% !important; height: 100% !important; margin: 0 !important;
}
.st-key-settings_backdrop button {
  width: 100% !important; height: 100% !important; min-height: 100% !important;
  background: rgba(9,11,7,.85) !important; border: none !important;
  border-radius: 0 !important; box-shadow: none !important;
}
/* sr-only rather than display:none — this scrim covers the whole screen and
   "סגירת הגדרות" is its only accessible name (see the drawer backdrop note) */
.st-key-settings_backdrop button p {
  position: absolute !important; width: 1px !important; height: 1px !important;
  padding: 0 !important; margin: -1px !important; overflow: hidden !important;
  clip-path: inset(50%) !important; white-space: nowrap !important; border: 0 !important;
}
.st-key-cai_settings {
  position: fixed; inset: 0; z-index: 140;
  width: min(100vw, 440px); margin: 0 auto;
  background: linear-gradient(180deg,#141710 0%,#0E1007 100%);
  /* top padding MUST clear the ::before status-bar mask below, and must be
     built from the same max(--cai-sat, env()) expression: with the plain
     env() the two diverged and the mask (sat + 26px) painted over the top
     6px of the 36px ‹ back button, slicing its cap flat on device
     (2026-07-30 report). mask + 8px keeps a hairline of breathing room. */
  padding: calc(max(var(--cai-sat, 0px), env(safe-area-inset-top, 0px)) + 34px) 20px calc(env(safe-area-inset-bottom,0px) + 20px) !important;
  overflow-y: auto; overscroll-behavior: contain;
}
/* the settings overlay and the drawer are their own scroll containers —
   the base scrollbar-hiding rules (stMain et al) don't reach them, and on
   the iPhone the thumb showed as a light strip down the LEFT edge (RTL) */
.st-key-cai_settings, .st-key-cai_drawer { scrollbar-width: none !important; }
.st-key-cai_settings::-webkit-scrollbar,
.st-key-cai_drawer::-webkit-scrollbar { display: none !important; width: 0 !important; }
/* status-bar mask: settings has no fixed header band (its title scrolls away
   by design), so scrolled content collided with the clock / Dynamic Island.
   Same recipe as .cai-header — a fixed tint that fades to nothing + blur.
   Fixed (not sticky): a sticky ::before can't enter the container's padding
   zone, which is exactly the strip that needs covering. z-index beats the
   position:relative rows inside (same stacking context, z auto). */
.st-key-cai_settings::before {
  content: ""; position: fixed; top: 0; left: 50%;
  transform: translateX(-50%); width: min(100vw, 440px);
  height: calc(max(var(--cai-sat, 0px), env(safe-area-inset-top, 0px)) + 26px);
  background: linear-gradient(180deg,
      rgba(20,23,16,.95) 0%, rgba(20,23,16,.85) 55%, rgba(20,23,16,0) 100%);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  z-index: 10; pointer-events: none;
}
.st-key-cai_settings [data-testid="stElementContainer"] { margin-bottom: 0; }
/* header: back + title */
.st-key-cai_settings div[data-testid="stHorizontalBlock"]:first-of-type { align-items: center; gap: 12px; }
/* 44px, not the mock's 36: this is the control that leaves every settings
   screen, it sits in the top corner where the thumb is least accurate, and
   36px is under the tap floor. The glyph keeps its size — only the box grows. */
.st-key-settings_back button {
  width: 44px !important; height: 44px !important; min-height: 44px !important;
  border-radius: 12px !important; padding: 0 !important;
  background: rgba(236,237,230,.06) !important; border: 1px solid rgba(236,237,230,.12) !important;
}
.st-key-settings_back button p { font: 600 20px Heebo !important; color: rgba(236,237,230,.7) !important; }
.cai-set-title { font: 400 21px 'Suez One', serif; color: var(--text); padding: 4px 0; }
/* 10px letter-spaced caps at .38 measured 3.19:1 — the worst contrast in the
   app, on the six labels that tell you what each settings group IS. .56 is
   4.6:1 and keeps them subordinate to the rows they head. */
.cai-set-seclabel { font: 600 10px Heebo; letter-spacing: 2px; color: rgba(236,237,230,.56); margin: 22px 0 9px; }

/* settings grouped card + nav rows */
[class*="st-key-cai_sgrp"] {
  border-radius: 15px; overflow: hidden;
  background: var(--surface); border: 1px solid var(--border);
}
[class*="st-key-cai_sgrp"] [data-testid="stElementContainer"] { margin: 0 !important; }
[class*="st-key-cai_sgrp"] button {
  background: transparent !important; border: none !important; border-radius: 0 !important;
  padding: 14px !important; margin: 0 !important; min-height: 0 !important; box-shadow: none !important;
  text-align: right; position: relative; justify-content: flex-start !important;
  border-top: 1px solid rgba(236,237,230,.07) !important;
}
[class*="st-key-cai_sgrp"] [data-testid="stElementContainer"]:first-child button { border-top: none !important; }
[class*="st-key-cai_sgrp"] button p {
  font: 500 14px Heebo !important; color: var(--text) !important; text-align: right !important;
  width: 100%; box-sizing: border-box;
  padding-inline-start: 42px; padding-inline-end: 24px;
}
[class*="st-key-cai_sgrp"] button::before {
  content: ""; position: absolute; inset-inline-start: 14px; top: 50%;
  transform: translateY(-50%); width: 18px; height: 18px;
  background-repeat: no-repeat; background-position: center; background-size: 18px;
}
[class*="st-key-cai_sgrp"] button::after {
  content: "‹"; position: absolute; inset-inline-end: 14px; top: 50%;
  transform: translateY(-50%); color: rgba(236,237,230,.3); font-size: 14px;
}
.st-key-nav_personal button::before, .st-key-nav_personal2 button::before { background-image: url("ICON_USER"); }
.st-key-nav_language button::before { background-image: url("ICON_GLOBE"); }
.st-key-nav_clearhist button::before, .st-key-nav_clearhist2 button::before { background-image: url("ICON_CHAT"); }
.st-key-nav_privacy button::before { background-image: url("ICON_LOCK"); }
.st-key-nav_about button::before { background-image: url("ICON_INFO"); }
@media (hover: hover) { [class*="st-key-cai_sgrp"] button:hover { background: rgba(236,237,230,.03) !important; } }

/* hub profile card */
.cai-set-profile {
  display: flex; align-items: center; gap: 12px; padding: 14px; border-radius: 16px;
  background: linear-gradient(135deg,rgba(var(--accent-rgb),.16),rgba(var(--accent-rgb),.04));
  border: 1px solid rgba(var(--accent-rgb),.3);
}
.cai-set-profile .av {
  width: 46px; height: 46px; border-radius: 13px; flex: none;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg,#AEB784,#8E9962); border: 1px solid rgba(196,206,146,.5);
  font: 700 22px 'Suez One', serif; color: #171A12;
}
.cai-set-profile .m { flex: 1; min-width: 0; }
.cai-set-profile .nm { font: 700 16px Heebo; color: var(--text); }
.cai-set-profile .sub { font: 500 12px Heebo; color: rgba(196,206,146,.85); margin-top: 2px; }

/* toggle-display + coming-soon chip + inline row (for בקרוב items) */
.cai-row { display: flex; align-items: center; gap: 13px; padding: 14px; }
.cai-row .ic { width: 18px; height: 18px; flex: none; background-repeat: no-repeat; background-position: center; background-size: 18px; }
.cai-row .tx { flex: 1; }
.cai-row .t { font: 500 14px Heebo; color: var(--text); }
.cai-row .s { font: 400 11px Heebo; color: rgba(236,237,230,.6); margin-top: 1px; }
.cai-row .val { font: 600 12px Heebo; color: rgba(196,206,146,.85); }
.cai-row .chev { color: rgba(236,237,230,.3); font-size: 14px; flex: none; }
.cai-div { height: 1px; background: rgba(236,237,230,.07); margin: 0 14px; }
.cai-tgl { width: 44px; height: 26px; border-radius: 99px; background: rgba(236,237,230,.14); position: relative; flex: none; }
.cai-tgl .k { position: absolute; top: 3px; left: 3px; width: 20px; height: 20px; border-radius: 50%; background: rgba(236,237,230,.6); }
.cai-tgl.on { background: var(--accent); }
.cai-tgl.on .k { left: auto; right: 3px; background: #171A12; }
.cai-bakrov { font: 600 9.5px Heebo; letter-spacing: .3px; color: rgba(196,206,146,.85); background: rgba(var(--accent-rgb),.14); border: 1px solid rgba(var(--accent-rgb),.3); border-radius: 99px; padding: 2px 8px; flex: none; }
.cai-ic-bell { background-image: url("ICON_BELL"); }
.cai-ic-lock { background-image: url("ICON_LOCK"); }
.cai-ic-clock { background-image: url("ICON_CLOCK"); }
.cai-ic-chart { background-image: url("ICON_CHART"); }
.cai-ic-chat { background-image: url("ICON_CHAT"); }

/* banners */
.cai-banner { display: flex; align-items: center; gap: 12px; padding: 14px; border-radius: 16px; margin-top: 4px;
  background: linear-gradient(135deg,rgba(var(--accent-rgb),.16),rgba(var(--accent-rgb),.04)); border: 1px solid rgba(var(--accent-rgb),.3); }
.cai-banner .bi { width: 26px; height: 26px; flex: none; background-repeat: no-repeat; background-position: center; background-size: 26px; }
.cai-banner .bt { font: 700 14px Heebo; color: var(--text); }
.cai-banner .bs { font: 400 11.5px Heebo; color: rgba(196,206,146,.85); margin-top: 2px; line-height: 1.45; }
.cai-info { display: flex; align-items: center; gap: 9px; margin-top: 16px; padding: 12px 14px; border-radius: 13px;
  background: rgba(var(--accent-rgb),.08); border: 1px solid rgba(var(--accent-rgb),.2); }
.cai-info .ii { width: 16px; height: 16px; flex: none; background-image: url("ICON_INFO"); background-repeat: no-repeat; background-position: center; background-size: 16px; }
.cai-info span { font: 400 11.5px Heebo; color: rgba(236,237,230,.6); line-height: 1.5; }

/* ── personal: the "כרטיס חייל" hero ──────────────────────────────────────
   A service-card object, not a form header. The olive rail is its spine and is
   deliberately the ONLY rail on this screen. It replaced a 76px avatar whose
   only action was "שינוי תמונה · בקרוב" — prime space spent on a feature that
   does not exist (2026-07-30, direction ב chosen off the mockups). */
.cai-svc {
  position: relative; overflow: hidden;
  border-radius: 16px; padding: 15px 16px 13px; margin-bottom: 9px;
  background: linear-gradient(150deg,#252B1A 0%,#1A1E12 62%);
  border: 1px solid rgba(var(--accent-rgb),.35);
}
.cai-svc::after {
  content: ""; position: absolute; inset: 0 0 0 auto; width: 4px;
  background: linear-gradient(180deg, var(--accent), rgba(var(--accent-rgb),.18));
}
.cai-svc-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.cai-svc-brand { font: 400 12px 'Suez One', serif; color: var(--accent-bright); letter-spacing: .04em; }
.cai-svc-stamp {
  font: 600 8.5px Heebo; letter-spacing: .2em; color: var(--text-faint);
  border: 1px solid rgba(236,237,230,.12); border-radius: 4px; padding: 4px 6px;
}
.cai-svc-id { display: flex; align-items: center; gap: 12px; }
.cai-svc-mono {
  width: 46px; height: 46px; border-radius: 12px; flex: none;
  background: rgba(var(--accent-rgb),.14); border: 1px solid rgba(var(--accent-rgb),.35);
  color: var(--accent-bright); font: 400 22px 'Suez One', serif;
  display: flex; align-items: center; justify-content: center;
}
.cai-svc-txt { min-width: 0; }
.cai-svc-nm { font: 400 20px 'Suez One', serif; color: var(--text); line-height: 1.1;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cai-svc-meta { font: 500 11.5px Heebo; color: rgba(236,237,230,.55); margin-top: 4px; }
/* perforation: the one flourish, and it earns its place by reading as a card
   edge rather than as a divider */
.cai-svc-perf { margin: 14px 0 9px; height: 1px;
  background: repeating-linear-gradient(90deg, rgba(236,237,230,.12) 0 4px, transparent 4px 9px); }
.cai-svc-foot { display: flex; justify-content: space-between; gap: 10px;
  font: 400 10.5px Heebo; color: var(--text-faint); }
.cai-svc-hint { font: 400 11.5px Heebo; color: var(--text-faint); margin: 0 3px 4px; }

/* personal: grouped identity fields — hairline-separated rows inside one card,
   the same language as the settings hub so this screen belongs to it */
.st-key-cai_pf_ident {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 15px; overflow: hidden;
}
[class*="st-key-cai_pf_fld"] { padding: 12px 13px; }
[class*="st-key-cai_pf_fld"] + [class*="st-key-cai_pf_fld"] { border-top: 1px solid var(--border); }
.cai-fld-label { font: 600 11px Heebo; color: rgba(236,237,230,.45); margin: 0 0 7px; }
.cai-lang-note { font: 400 11.5px Heebo; color: rgba(236,237,230,.5); margin: 6px 2px 14px; line-height: 1.55; }

/* language rows */
/* ── text-size screen ── the sample reads at the CANDIDATE size (its own --s),
   not at the live --cai-fs, so the card shows what the setting will do */
.cai-fs-sample {
  border-radius: 15px; background: var(--surface);
  border: 1px solid var(--border); padding: 14px 16px 4px; margin-bottom: 14px;
  direction: rtl; text-align: right;
}
.cai-fs-sample .lb {
  font: 600 10px Heebo; letter-spacing: .18em; color: var(--text-faint);
  margin-bottom: 6px;
}
.cai-fs-sample p {
  margin: 0 0 10px; color: var(--text);
  font: 400 calc(15px * var(--s, 1))/1.65 Heebo, sans-serif;
}
.st-key-cai_fs_opts [data-testid="stColumn"] { min-width: 0 !important; }
.st-key-cai_fs_opts button { min-height: 44px !important; }
.st-key-cai_fs_opts button p { font: 500 13px Heebo !important; }

.cai-lang-card { border-radius: 15px; overflow: hidden; background: var(--surface); border: 1px solid var(--border); }
.cai-lang-row { display: flex; align-items: center; gap: 13px; padding: 15px 14px; }
.cai-lang-row .fl { font-size: 20px; flex: none; }
.cai-lang-row .nm { flex: 1; font: 600 14.5px Heebo; color: var(--text); }
.cai-lang-row.dim .nm { color: rgba(236,237,230,.5); font-weight: 500; }
.cai-lang-row .def { font: 400 11px Heebo; color: rgba(236,237,230,.4); margin-top: 1px; }
.cai-lang-row .ok { color: var(--accent); font-size: 18px; font-weight: 700; }

/* ToS */
.cai-tos-lead { font: 400 21px 'Suez One', serif; color: var(--text); margin-bottom: 4px; }
.cai-tos-sub { font: 500 12px Heebo; color: rgba(236,237,230,.4); margin-bottom: 20px; }
.cai-tos-h { font: 700 14.5px Heebo; color: var(--accent-bright); margin-bottom: 6px; }
.cai-tos-b { font: 400 13px Heebo; color: rgba(236,237,230,.78); line-height: 1.7; }
.cai-tos-sec { margin-bottom: 20px; }
.cai-set-foot { text-align: center; margin-top: 22px; padding-top: 16px; border-top: 1px solid rgba(236,237,230,.09); }
.cai-set-foot .a { font: 600 9px ui-monospace, Menlo, monospace; letter-spacing: 2px; color: rgba(236,237,230,.35); }
.cai-set-foot .b { font: 400 10.5px Heebo; color: rgba(236,237,230,.3); margin-top: 8px; line-height: 1.5; }

/* save / danger buttons — save is a form_submit_button (the form kills the
   blur-commit tap race); keyed forms get no st-key-* class in 1.58, so scope
   through the cai_pf_form wrapper and kill the layout-only stForm frame */
.st-key-cai_pf_form [data-testid="stForm"] { border: none !important; padding: 0 !important; }
/* the save bar sticks to the bottom of the settings overlay (its own scroll
   container), so on this tall screen the action is always reachable and the
   unsaved-changes line is always visible instead of below the fold */
/* FIXED, not sticky. position:sticky is dead on arrival inside Streamlit 1.58:
   every container is wrapped in a stLayoutWrapper that shrink-wraps to exactly
   its child's height (measured 104px for a 104px bar), and a sticky element
   cannot move inside a containing block with no slack. Same recipe as
   .st-key-cai_settings::before instead — the panel is itself position:fixed
   with no transformed ancestor, so a fixed child lands predictably and shares
   the panel's stacking context. */
.st-key-cai_pf_save {
  position: fixed; z-index: 6; bottom: 0; left: 50%;
  transform: translateX(-50%); width: min(100vw, 440px); margin: 0;
  padding: 10px 20px calc(env(safe-area-inset-bottom,0px) + 20px);
  background: linear-gradient(180deg, rgba(20,23,16,0) 0%, #141710 24%);
}
/* ...which takes the bar out of flow, so the form has to reserve its height or
   the last field hides behind it */
.st-key-cai_pf_form { padding-bottom: calc(env(safe-area-inset-bottom,0px) + 112px); }
.cai-pf-savenote { text-align: center; margin-bottom: 8px; font: 500 11px Heebo; min-height: 15px; }
.cai-pf-savenote .clean { color: rgba(236,237,230,.4); }
.cai-pf-savenote .changed { color: var(--accent-bright); display: none; }
.st-key-cai_pf_save.dirty .cai-pf-savenote .clean { display: none; }
.st-key-cai_pf_save.dirty .cai-pf-savenote .changed { display: inline; }
.st-key-cai_pf_form [data-testid="stFormSubmitButton"] button {
  background: var(--accent) !important; border: none !important; color: #171A12 !important;
  border-radius: 13px !important; font: 700 14px Heebo !important; padding: 13px !important;
}
/* until something changes the save button is a quiet outline, so the loudest
   thing on the screen stays the card — it fills in the moment there is
   something to save */
.st-key-cai_pf_save:not(.dirty) [data-testid="stFormSubmitButton"] button {
  background: transparent !important; border: 1px solid rgba(var(--accent-rgb),.4) !important;
}
.st-key-cai_pf_save:not(.dirty) [data-testid="stFormSubmitButton"] button p { color: var(--accent) !important; }
.st-key-cai_pf_form [data-testid="stFormSubmitButton"] button p { color: #171A12 !important; font-weight: 700 !important; text-align: center !important; }
@media (hover: hover) { .st-key-cai_pf_form [data-testid="stFormSubmitButton"] button:hover { background: #A6AF76 !important; } }
[class*="st-key-danger_"] button {
  background: rgba(198,120,110,.1) !important; border: 1px solid rgba(198,120,110,.35) !important;
  color: #D89189 !important; border-radius: 13px !important; font: 600 13.5px Heebo !important;
  padding: 13px !important; margin-top: 20px !important;
}
[class*="st-key-danger_"] button p { color: #D89189 !important; font-weight: 600 !important; text-align: center !important; }
@media (hover: hover) { [class*="st-key-danger_"] button:hover { background: rgba(198,120,110,.18) !important; } }

/* privacy banner icon + real analytics toggle */
.cai-banner .bi { background-image: url("ICON_SHIELD"); }
.st-key-cai_analytics { border-radius: 15px; background: var(--surface); border: 1px solid var(--border); padding: 10px 14px 12px; margin-bottom: 8px; }
.st-key-cai_analytics [data-testid="stElementContainer"] { margin: 0 !important; }
/* zero flex gap left the switch knob touching the first letter of the label
   ("שיתוף" read as swallowed — 2026-08-03 audit) */
.st-key-cai_analytics [data-testid="stCheckbox"] label { gap: 10px !important; }
.st-key-share_analytics_w label { font: 500 14px Heebo !important; color: var(--text) !important; }
.st-key-share_analytics_w [data-baseweb="checkbox"] > div:first-child { background: var(--accent) !important; }
.cai-analytics-sub { font: 400 11px Heebo; color: rgba(236,237,230,.45); margin: 2px 0 0; }

/* personal-details native widgets styled to the mockup fields (8b) */
.st-key-pf_name_w [data-baseweb="input"], .st-key-pf_name_w [data-baseweb="base-input"] {
  background: transparent !important; border: none !important; }
.st-key-pf_name_w input {
  background: var(--surface) !important; border: 1px solid var(--border) !important;
  border-radius: 12px !important; color: var(--text) !important;
  font: 500 14.5px Heebo !important; padding: 13px 15px !important; }
.st-key-pf_track_w [data-baseweb="select"] > div {
  background: var(--surface) !important; border: 1px solid var(--border) !important;
  border-radius: 12px !important; min-height: 48px !important; }
.st-key-pf_track_w [data-baseweb="select"] div, .st-key-pf_track_w [data-baseweb="select"] span {
  color: var(--text) !important; font: 500 14px Heebo !important; }
/* service-type: 3 equal separated tabs.
   The grid MUST sit on the inner [role="radiogroup"], not on stButtonGroup:
   in 1.58 stButtonGroup holds [collapsed <label>, radiogroup], so a
   3-column grid there gave the radiogroup a single 1fr track — measured 107px
   inside a 335px row — and the three buttons (width:100% of that) stacked
   into a narrow left-leaning column. That is what made the whole screen read
   as misaligned (2026-07-30 device video). */
/* the keyed stElementContainer itself shrink-wraps inside the flex column —
   every inner width:100% resolves against IT, so the "full-width" tabs were
   min-content coincidence (185px exposed once padding shrank, 2026-08-03) */
.st-key-pf_type_w { width: 100% !important; }
.st-key-pf_type_w [data-testid="stButtonGroup"] { width: 100% !important; display: block !important; }
.st-key-pf_type_w [data-testid="stButtonGroup"] [role="radiogroup"] {
  /* BaseWeb ships max-width:fit-content on the radiogroup — it silently caps
     width:100% at content size (185px), which is what kept the tabs narrow
     and "מילואים" one px from ellipsis (2026-08-03) */
  width: 100% !important; max-width: none !important; display: grid !important;
  grid-template-columns: 1fr 1fr 1fr !important; gap: 7px !important; }
.st-key-pf_type_w [data-testid="stButtonGroup"] button {
  width: 100% !important; border-radius: 11px !important; min-height: 44px !important;
  background: var(--surface) !important; border: 1px solid var(--border) !important;
  color: rgba(236,237,230,.7) !important;
  /* BaseWeb's 16px side padding left "מילואים" one px from ellipsis at the
     77px track (and phones DID ellipsize it — 2026-08-03 audit) */
  padding-inline: 6px !important; }
.st-key-pf_type_w [data-testid="stButtonGroup"] button p {
  color: rgba(236,237,230,.7) !important; font: 500 13px Heebo !important; }
.st-key-pf_type_w button[data-testid*="segmented_controlActive"],
.st-key-pf_type_w [data-testid="stButtonGroup"] button[aria-checked="true"] {
  background: rgba(var(--accent-rgb),.18) !important; border-color: var(--accent) !important; }
.st-key-pf_type_w button[data-testid*="segmented_controlActive"] p,
.st-key-pf_type_w [data-testid="stButtonGroup"] button[aria-checked="true"] p {
  color: var(--accent-bright) !important; font-weight: 700 !important; }

/* ═══ Reconcile with the 9a session's OLD-drawer CSS (merged) ═══
   Streamlit centers button labels by default and the old drawer added an 8px
   element margin — force THIS drawer's + settings' rows to lead their label
   from the reading edge (right, RTL) and sit tight. */
.st-key-cai_drawer [data-testid="stElementContainer"] { margin-bottom: 0 !important; }
.st-key-cai_tools button, .st-key-cai_recent button, [class*="st-key-cai_sgrp"] button {
  justify-content: flex-start !important;
}
.st-key-cai_tools button [data-testid="stMarkdownContainer"],
.st-key-cai_recent button [data-testid="stMarkdownContainer"],
[class*="st-key-cai_sgrp"] button [data-testid="stMarkdownContainer"] { width: 100% !important; }

/* ═══ 2026-08-04 iconography + touch round ═══ */
/* recent-conversation rows: same leading-icon language as the tool rows
   (the old 💬 label prefix rendered as a color emoji, differently per device) */
.st-key-cai_recent button::before {
  content: ""; position: absolute; inset-inline-start: 14px; top: 50%;
  transform: translateY(-50%); width: 18px; height: 18px;
  background: url("ICON_CHAT") center / 18px no-repeat;
}
/* "הצג סעיף מקור" under an answer — page mark, inline with the label */
[class*="st-key-src_"] button::before {
  content: ""; display: inline-block; width: 15px; height: 15px;
  margin-inline-end: 7px; vertical-align: -3px;
  background: url("ICON_LETTERS") center / 15px no-repeat;
}
/* the search field draws its own magnifier — the emoji in the placeholder
   was a color glyph on iOS and disappeared the moment typing started */
.cai-orders-q {
  padding-inline-start: 34px;
  background-image: url("ICON_SEARCH");
  background-repeat: no-repeat;
  background-position: right 11px center;
  background-size: 15px;
}
/* press feedback on the drawer surfaces (hover is pointer-only; on touch
   these rows gave no response at all — 2026-08-03 design review) */
.st-key-cai_tools button:active, .st-key-cai_recent button:active,
[class*="st-key-cai_sgrp"] button:active {
  background: rgba(236,237,230,.07) !important;
}
.st-key-open_settings button:active, .st-key-drawer_close button:active {
  background-color: var(--border) !important; transform: scale(.94);
}
.st-key-pf_type_w [data-testid="stButtonGroup"] button:not([aria-checked="true"]):active {
  background: var(--surface-hover) !important;
}
</style>
"""
for _k, _u in _ICON.items():
    _DS_CSS = _DS_CSS.replace("ICON_" + _k.upper(), _u)


# ── Personal-details options + Terms text (mockup 8b / 8e) ──
_SERVICE_TYPES = ["סדיר", "מילואים", "קבע"]
_SERVICE_TRACKS = [
    "לוחם/ת (תעודת לוחם)",
    "תומכ״ל / עורפי",
    "רמ״פ א׳ ומעלה (ללא תעודת לוחם)",
    "אחר / לא רלוונטי",
]
_STATUS_PILLS = ["חייל בודד", "עולה חדש", "הורה לילדים", "נשוי/אה"]
_TOS_SECTIONS = [
    ("1. הצהרה כללית",
     "אפליקציה זו (\"האפליקציה\") הינה כלי עזר פרטי שפותח על ידי מפתח עצמאי. האפליקציה אינה "
     "כלי רשמי של צה\"ל, משרד הביטחון או כל גוף ממלכתי אחר. השימוש באפליקציה הוא על אחריות המשתמש בלבד."),
    ("2. הגבלת אחריות",
     "השירות באפליקציה ניתן כמות שהוא (\"As-Is\"). המפתח אינו אחראי לדיוק, לשלמות או לעדכניות המידע "
     "המוצג באפליקציה. המשתמש מודע לכך שהאפליקציה מבוססת על מודלים של בינה מלאכותית (AI), אשר עלולים "
     "לספק מידע שגוי, חלקי או לא מדויק (\"הזיות\"). אין להסתמך על מידע זה כייעוץ צבאי, מקצועי או משפטי מחייב."),
    ("3. איסור הזנת מידע מסווג",
     "חל איסור מוחלט על המשתמשים להזין, להעלות או לשתף בתוך האפליקציה מידע מסווג, רגיש, או כל מידע "
     "שחשיפתו מהווה עבירת ביטחון שדה. המפתח אינו נושא באחריות לכל נזק או השלכה משפטית הנובעת מהפרת "
     "סעיף זה על ידי המשתמש."),
    ("4. פרטיות ונתונים",
     "המידע שאתה מזין לאפליקציה משמש לצורך הפעלת מודלי הבינה המלאכותית בלבד.<br><br>אנו נוקטים באמצעים "
     "טכניים סבירים כדי לשמור על פרטיות המשתמשים. עם זאת, אין אבטחה מוחלטת ברשת, והמשתמש לוקח על עצמו "
     "את הסיכון הכרוך בהזנת נתונים במערכת."),
    ("5. קניין רוחני",
     "כלל התוכן, העיצוב, הקוד המקור והלוגו של האפליקציה הינם קניינו הרוחני הבלעדי של המפתח. אין להעתיק, "
     "לשכפל או להשתמש בהם ללא אישור מראש ובכתב."),
]


def _clear_history():
    """Wipe archived conversations + the active chat (a deliberate cleanup)."""
    st.session_state.conversation_history = []
    st.session_state.messages = []


def _reset_identity():
    """Forget WHO this device is: role, name, profile, and the tool inputs.

    Shared by logout and by the full wipe, because the two drifting apart is
    exactly how the 2026-08-08 report happened — logout reset `role` alone, so
    the cookie kept `{"name": "...", "asked": true}` and the next role pick
    skipped the name gate and greeted the previous user by name.

    Everything cleared here is personal or derived from a person: the display
    name, the status pills, the service track/type, and the two tool profiles
    (`sol_*` carries enlistment and discharge dates, `mil_*` carries a salary).
    A sign-out that leaves a salary behind is not a sign-out.
    """
    st.session_state.profile_saved = []
    st.session_state.profile_customized = False
    st.session_state.profile_name = ""
    # back to the role picker, and the one-time name prompt asks again on the
    # next role pick. Without role=None the gate (derived from name_asked)
    # would pop over the settings screen. cai_wipe_pending lets the sync writer
    # render the all-empty payload — its settled-gate otherwise skips empty
    # states (the guard that stops a cold cloud boot from clobbering the
    # store), which would leave the OLD cookie, name and all, on the device.
    st.session_state.name_asked = False
    st.session_state.cai_wipe_pending = True
    st.session_state.role = None
    st.session_state.pending_question = None
    st.session_state.pop("suggested", None)
    st.session_state.show_settings = False
    st.session_state.settings_screen = "hub"
    st.session_state.service_track = ""
    st.session_state.service_type = "סדיר"
    # the tool profiles ride the same device cookie (see the sync writer): the
    # "sv" flags are what put them in the payload at all, so dropping the flag
    # is what actually removes them from the device
    for _k in ("sol_saved", "sol_enlist", "sol_discharge", "sol_track",
               "sol_single", "sol_married",
               "mil_saved", "mil_days_year", "mil_days_3y", "mil_emp", "mil_salary"):
        st.session_state.pop(_k, None)
    # drop the widgets' keys so they reseed from the reset mirrors
    for _k in ("profile_statuses", "pf_name_w", "pf_type_w", "pf_track_w", "gate_name_w",
               "sol_en_w", "sol_di_w", "sol_tr_w", "sol_sg_w", "sol_mr_w"):
        st.session_state.pop(_k, None)


def _wipe_all():
    """Full on-device wipe: chats + profile back to defaults (mockup 8d)."""
    _clear_history()
    _reset_identity()
    # "a fresh device" has to mean the analytics id too, or a user who tapped
    # מחק הכל stays joinable to everything they did before the wipe. Rotated
    # rather than deleted: the key must exist for the log call sites, and the
    # sync writer below persists the new value with the emptied payload. This
    # is the ONE thing logout does not do — logging out is still the same
    # device, and the pilot's usage numbers depend on that staying true.
    st.session_state.device_id = metrics.new_session_id()


def _settings_hub():
    """8a — settings home: profile card + grouped nav + logout."""
    _svc = _service_type_shown()
    _svc_label = "שירות חובה" if _svc == "סדיר" else _svc
    # a service label that merely repeats the role ("מילואים · מילואים") adds
    # nothing — show it only when it carries new information
    _sub = [] if _svc_label == role_label else [_svc_label]
    _pills = st.session_state.get("profile_saved") or []
    if _pills:
        _sub.append(_pills[0])
    # saved name leads the card; the role slides into the subtitle
    _dnh = _display_name()
    if _dnh:
        _sub.insert(0, role_label)
    st.markdown(
        "<div class='cai-set-profile'>"
        f"<div class='av'>{html.escape((_dnh or role_label)[:1])}</div>"
        f"<div class='m'><div class='nm'>{html.escape(_dnh or role_label)}</div>"
        f"<div class='sub'>{html.escape(' · '.join(_sub))}</div></div>"
        "</div>", unsafe_allow_html=True)

    st.markdown("<div class='cai-set-seclabel'>חשבון</div>", unsafe_allow_html=True)
    with st.container(key="cai_sgrp_acct"):
        if st.button("פרטים אישיים", key="nav_personal", use_container_width=True):
            st.session_state.settings_screen = "personal"
            st.rerun()

    st.markdown("<div class='cai-set-seclabel'>התראות</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='cai-lang-card'><div class='cai-row'>"
        "<div class='ic cai-ic-bell'></div>"
        "<div class='tx'><div class='t'>עדכוני פקודות מטכ\"ל</div>"
        "<div class='s'>התראה כשפקודה מתעדכנת</div></div>"
        "<span class='cai-bakrov'>בקרוב</span>"
        "<div class='cai-tgl'><span class='k'></span></div>"
        "</div></div>", unsafe_allow_html=True)

    st.markdown("<div class='cai-set-seclabel'>שפה ותצוגה</div>", unsafe_allow_html=True)
    with st.container(key="cai_sgrp_lang"):
        if st.button("שפה", key="nav_language", use_container_width=True):
            st.session_state.settings_screen = "language"
            st.rerun()
        if st.button("גודל טקסט", key="nav_access", use_container_width=True):
            st.session_state.settings_screen = "access"
            st.rerun()

    st.markdown("<div class='cai-set-seclabel'>פרטיות ונתונים</div>", unsafe_allow_html=True)
    with st.container(key="cai_sgrp_priv"):
        if st.button("נקה היסטוריית שיחות", key="nav_clearhist", use_container_width=True):
            _clear_history()
            st.rerun()
        if st.button("פרטיות ואבטחה", key="nav_privacy", use_container_width=True):
            st.session_state.settings_screen = "privacy"
            st.rerun()

    st.markdown("<div class='cai-set-seclabel'>אודות</div>", unsafe_allow_html=True)
    with st.container(key="cai_sgrp_about"):
        if st.button("אודות ותנאי שימוש", key="nav_about", use_container_width=True):
            st.session_state.settings_screen = "about"
            st.rerun()

    # logout = sign the person out, not just switch role (no real auth). It
    # used to clear `role` alone, which left the name in the device cookie and
    # greeted the next user as the last one; _reset_identity is the whole set.
    # Chats go too: the app returns to the role picker, and leaving the
    # previous user's conversations one tap inside "שיחות אחרונות" is not what
    # "התנתקות" says. They are session-state only, so nothing durable is lost.
    if st.button("התנתקות", key="danger_logout", use_container_width=True):
        _clear_history()
        _reset_identity()
        st.rerun()
    st.markdown("<div class='cai-drawer-foot'>בלמ\"ס · לשימוש פנימי בלבד</div>", unsafe_allow_html=True)


def _service_card(name: str, svc: str, track: str, marks: list[str]) -> str:
    """The "כרטיס חייל" hero — a service-card object, not a form header.

    Replaces the 76px avatar + "שינוי תמונה בקרוב" block, which spent the screen's
    best real estate on a feature that does not exist. The card shows what the
    app actually knows about the user, and the fields below amend it live.

    Live means CLIENT-side: the fields sit in an st.form, so their values do not
    reach the server until submit. pfSync in the gesture engine reads the widgets
    and writes the [data-svc-*] slots below.

    data-svc-base carries the SAVED state in exactly the shape pfRead() builds,
    which is what the save bar diffs against. Snapshotting the DOM instead raced
    the mount — the segmented control gains its active-state testid a beat after
    the input exists, so the first snapshot recorded an empty service type and
    the bar came up already claiming unsaved changes.
    """
    short_track = track.split(" (")[0] if track else ""
    initial = (name or role_label)[:1]
    # the card footer already prints the role — a meta line repeating the same
    # word (reserve default: type "מילואים" over foot "מילואים") says nothing
    meta = " · ".join(x for x in (svc, short_track) if x and x != role_label)
    base = json.dumps(
        {"name": name.strip(), "type": svc, "track": short_track,
         "marks": "|".join(sorted(marks))},
        ensure_ascii=False, separators=(",", ":"))
    return (
        f"<div class='cai-svc' data-svc-fallback=\"{html.escape(role_label, quote=True)}\""
        f" data-svc-base=\"{html.escape(base, quote=True)}\">"
        "<div class='cai-svc-top'>"
        "<span class='cai-svc-brand'>CommandAI</span>"
        "<span class='cai-svc-stamp'>בלמ\"ס</span>"
        "</div>"
        "<div class='cai-svc-id'>"
        f"<div class='cai-svc-mono' data-svc-mono>{html.escape(initial)}</div>"
        "<div class='cai-svc-txt'>"
        f"<div class='cai-svc-nm' data-svc-nm>{html.escape(name or role_label)}</div>"
        f"<div class='cai-svc-meta' data-svc-meta>{html.escape(meta)}</div>"
        "</div></div>"
        "<div class='cai-svc-perf'></div>"
        f"<div class='cai-svc-foot'><span>{html.escape(role_label)}</span>"
        "<span class='cai-svc-marks' data-svc-marks></span></div>"
        "</div>"
    )


def _settings_personal():
    """8b — personal details, "כרטיס חייל" direction: a live service card as the
    hero, the identity fields grouped beneath it, then the status picker."""
    # the card carries the FULL name (a service card would), while _display_name
    # stays the first-name-only form used for greetings and the hub avatar
    st.markdown(
        _service_card((st.session_state.get("profile_name") or "").strip(),
                      _service_type_shown(),
                      st.session_state.get("service_track") or "",
                      list(st.session_state.get("profile_saved") or [])),
        unsafe_allow_html=True)
    st.markdown("<div class='cai-svc-hint'>הכרטיס מתעדכן לפי מה שתמלא/י למטה.</div>",
                unsafe_allow_html=True)

    # Widgets edit their OWN keys only; the stable mirrors that handle_question
    # reads are committed on Save. Seed each widget from its mirror on first
    # render — on close the widget key drops (widget not rendered) and reopen
    # reseeds it, so an unsaved edit is discarded rather than leaking.
    # st.form, not bare widgets + st.button (same fix as the name gate): a
    # save tap right after typing lost the race with the text_input
    # blur-commit rerun — the tap landed on a replaced node, so users tapped
    # twice and could commit stale values. The form bundles every field value
    # and the press into ONE event. Safe to defer widget commits to submit:
    # all option lists are static and nothing reads the pf_* keys mid-edit.
    # Keyed forms get no st-key-* class in 1.58 — the cai_pf_form wrapper
    # carries the key the CSS scopes through.
    with st.container(key="cai_pf_form"):
        with st.form(key="pf_form", border=False):
            # ── זיהוי: one grouped card, hairline between fields — the same row
            # language as the settings hub, so the screen reads as one product ──
            st.markdown("<div class='cai-set-seclabel'>זיהוי</div>", unsafe_allow_html=True)
            with st.container(key="cai_pf_ident"):
                with st.container(key="cai_pf_fld_name"):
                    st.markdown("<div class='cai-fld-label'>שם מלא</div>", unsafe_allow_html=True)
                    if "pf_name_w" not in st.session_state:
                        st.session_state.pf_name_w = st.session_state.get("profile_name", "")
                    st.text_input("שם מלא", key="pf_name_w",
                                  label_visibility="collapsed", placeholder="ישראל ישראלי")

                with st.container(key="cai_pf_fld_type"):
                    st.markdown("<div class='cai-fld-label'>סוג שירות</div>", unsafe_allow_html=True)
                    if "pf_type_w" not in st.session_state:
                        # seed from the role-aware resolution: a reserve user's
                        # form opens on מילואים, not the conscript default
                        st.session_state.pf_type_w = _service_type_shown()
                    st.segmented_control("סוג שירות", _SERVICE_TYPES, key="pf_type_w",
                                         selection_mode="single", label_visibility="collapsed")

                with st.container(key="cai_pf_fld_track"):
                    st.markdown("<div class='cai-fld-label'>מסלול השירות</div>", unsafe_allow_html=True)
                    _tracks = ["בחר/י מסלול…"] + _SERVICE_TRACKS
                    if "pf_track_w" not in st.session_state:
                        _cur = st.session_state.get("service_track", "")
                        st.session_state.pf_track_w = _cur if _cur in _SERVICE_TRACKS else _tracks[0]
                    st.selectbox("מסלול השירות", _tracks, key="pf_track_w",
                                 label_visibility="collapsed")

            st.markdown("<div class='cai-set-seclabel'>סטטוס</div>", unsafe_allow_html=True)
            with st.container(key="cai_pf_status"):
                # one line doing two jobs: it tells the ~70% who tick nothing
                # that nothing is wrong, and otherwise reads back what is now
                # printed on the card. pfSync swaps it and fills the <b>.
                st.markdown(
                    "<div class='cai-pf-reg'>"
                    "<span class='z'>לא סומן סטטוס נוסף. סימון משנה חישובי זכאות וניסוח תשובות.</span>"
                    "<span class='p'>רשום בכרטיס: <b data-pf-reg></b></span>"
                    "</div>", unsafe_allow_html=True)
                if "profile_statuses" not in st.session_state and st.session_state.get("profile_saved"):
                    st.session_state.profile_statuses = st.session_state.profile_saved
                st.pills("סטטוס", _STATUS_PILLS, selection_mode="multi",
                         key="profile_statuses", label_visibility="collapsed")

            # Save bar, pinned to the bottom of the settings overlay. The old
            # button sat wherever the form happened to end, so on a screen this
            # tall it was below the fold with no signal that anything was
            # pending. caiPfDirty (gesture engine) flips .dirty on this bar.
            with st.container(key="cai_pf_save"):
                st.markdown(
                    "<div class='cai-pf-savenote'>"
                    "<span class='clean'>הפרטים נשמרים במכשיר בלבד</span>"
                    "<span class='changed'>יש שינויים שלא נשמרו</span>"
                    "</div>", unsafe_allow_html=True)
                _save = st.form_submit_button("שמירת שינויים", use_container_width=True)

    # Save COMMITS the widgets to their mirrors and flips profile_customized —
    # only now do the service fields reach the answer. An untouched user's API
    # turn stays byte-identical (see handle_question).
    if _save:
        st.session_state.profile_name = st.session_state.get("pf_name_w", "") or ""
        st.session_state.service_type = st.session_state.get("pf_type_w") or "סדיר"
        _tr = st.session_state.get("pf_track_w")
        st.session_state.service_track = "" if (not _tr or _tr == _tracks[0]) else _tr
        st.session_state.profile_saved = list(st.session_state.get("profile_statuses") or [])
        st.session_state.profile_customized = True
        st.session_state.settings_screen = "hub"
        st.rerun()


def _settings_language():
    """8c — language: Hebrew active; others honestly marked בקרוב."""
    st.markdown(
        "<div class='cai-lang-note'>בחירת השפה משנה את שפת הממשק והתשובות. החישובים זהים בכל השפות.</div>",
        unsafe_allow_html=True)
    st.markdown(
        "<div class='cai-lang-card'>"
        "<div class='cai-lang-row'><span class='fl'>🇮🇱</span><div style='flex:1'>"
        "<div class='nm'>עברית</div><div class='def'>ברירת מחדל</div></div><span class='ok'>✓</span></div>"
        "<div class='cai-div'></div>"
        "<div class='cai-lang-row dim'><span class='fl'>🇸🇦</span><div class='nm'>العربية</div><span class='cai-bakrov'>בקרוב</span></div>"
        "<div class='cai-div'></div>"
        "<div class='cai-lang-row dim'><span class='fl'>🇬🇧</span><div class='nm'>English</div><span class='cai-bakrov'>בקרוב</span></div>"
        "<div class='cai-div'></div>"
        "<div class='cai-lang-row dim'><span class='fl'>🇷🇺</span><div class='nm'>Русский</div><span class='cai-bakrov'>בקרוב</span></div>"
        "</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='cai-info'><div class='ii'></div>"
        "<span>שינוי שפה יחיל מיד את הכיווניות המתאימה לממשק.</span></div>", unsafe_allow_html=True)


def _settings_access():
    """Text size — the app's replacement for the zoom it deliberately blocks.

    Three steps, not a slider: a slider on a 44px-thumb surface invites a drag
    the user cannot land precisely, and there is no meaningful value between
    "normal" and "large" for a 15px base. The sample paragraph above the
    controls is the point — the setting is judged by reading, not by a number.
    """
    st.markdown(
        "<div class='cai-lang-note'>הגדלת הטקסט חלה על גוף התשובות והשאלות. "
        "התפריטים והכפתורים נשארים בגודלם כדי שהמסך לא יזוז.</div>",
        unsafe_allow_html=True)
    _cur = float(st.session_state.get("text_scale", 1.0))
    st.markdown(
        f"<div class='cai-fs-sample' style='--s:{_cur}'>"
        "<div class='lb'>דוגמה</div>"
        "<p>חייל זכאי לשבע שעות שינה רצופות. חריגה מחייבת אישור "
        "של הגורם שנקבע בפקודה.</p></div>",
        unsafe_allow_html=True)
    _opts = [("רגיל", 1.0), ("גדול", 1.15), ("גדול מאוד", 1.3)]
    with st.container(key="cai_fs_opts"):
        _cols = st.columns(len(_opts))
        for _c, (_lbl, _val) in zip(_cols, _opts):
            with _c:
                # type="primary" is the selected marker: it is the one visual
                # state Streamlit gives a button that survives our CSS reset
                if st.button(_lbl, key=f"fs_{_val}", use_container_width=True,
                             type="primary" if abs(_cur - _val) < 1e-6 else "secondary"):
                    st.session_state.text_scale = _val
                    st.rerun()


def _settings_privacy():
    """8d — privacy: honest בקרוב locks + a real analytics toggle + wipes."""
    st.markdown(
        "<div class='cai-banner'><div class='bi'></div>"
        "<div style='flex:1'><div class='bt'>הנתונים שלך מוגנים</div>"
        "<div class='bs'>המידע נשמר מוצפן במכשיר ואינו נשלח לשרת חיצוני.</div></div></div>",
        unsafe_allow_html=True)

    st.markdown("<div class='cai-set-seclabel'>גישה למכשיר</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='cai-lang-card'>"
        "<div class='cai-row'><div class='ic cai-ic-lock'></div>"
        "<div class='tx'><div class='t'>נעילה ביומטרית</div><div class='s'>Face ID לפתיחת האפליקציה</div></div>"
        "<span class='cai-bakrov'>בקרוב</span><div class='cai-tgl'><span class='k'></span></div></div>"
        "<div class='cai-div'></div>"
        "<div class='cai-row'><div class='ic cai-ic-clock'></div>"
        "<div class='tx'><div class='t'>נעילה אוטומטית</div><div class='s'>אחרי דקה של חוסר פעילות</div></div>"
        "<span class='cai-bakrov'>בקרוב</span></div>"
        "</div>", unsafe_allow_html=True)

    st.markdown("<div class='cai-set-seclabel'>נתונים</div>", unsafe_allow_html=True)
    if "share_analytics_w" not in st.session_state:
        st.session_state.share_analytics_w = st.session_state.get("share_analytics", True)
    with st.container(key="cai_analytics"):
        _share = st.toggle("שיתוף נתוני שימוש אנונימיים", key="share_analytics_w")
        st.session_state.share_analytics = _share
        st.markdown("<div class='cai-analytics-sub'>לשיפור המענה</div>", unsafe_allow_html=True)
    with st.container(key="cai_sgrp_data"):
        if st.button("נקה היסטוריית שיחות", key="nav_clearhist2", use_container_width=True):
            _clear_history()
            st.rerun()

    if st.button("מחיקת כל הנתונים מהמכשיר", key="danger_wipe", use_container_width=True):
        _wipe_all()
        st.rerun()


def _settings_about():
    """8e — about + terms of service (verbatim) + install hint."""
    st.markdown(
        "<div class='cai-banner' style='margin-bottom:18px'>"
        "<div style='width:34px;height:34px;border-radius:10px;flex:none;display:flex;"
        "align-items:center;justify-content:center;background:rgba(var(--accent-rgb),.22);"
        "color:var(--accent-bright);font-size:18px;font-weight:700'>✓</div>"
        "<div style='flex:1'><div class='bt' style='font-size:13.5px'>אישרת את התנאים</div>"
        "<div class='bs'>בהתקנה הראשונית · גרסה 2.4</div></div></div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='cai-tos-lead'>תנאי שימוש</div><div class='cai-tos-sub'>Terms of Service</div>",
        unsafe_allow_html=True)
    for _h, _b in _TOS_SECTIONS:
        st.markdown(
            f"<div class='cai-tos-sec'><div class='cai-tos-h'>{_h}</div><div class='cai-tos-b'>{_b}</div></div>",
            unsafe_allow_html=True)
    st.markdown("<div class='cai-set-seclabel'>התקנה כאפליקציה</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='cai-lang-card' style='padding:14px'><div class='cai-tos-b' style='color:rgba(236,237,230,.7)'>"
        "<b>אייפון:</b> בספארי — כפתור השיתוף ⬆️ ואז «הוסף למסך הבית».<br>"
        "<b>אנדרואיד:</b> בכרום — תפריט ⋮ ואז «הוספה למסך הבית».<br>"
        "האפליקציה תיפתח במסך מלא, עם אייקון CommandAI."
        "</div></div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='cai-set-foot'><div class='a'>מחשבון זכאויות · גרסה 2.4</div>"
        "<div class='b'>כלי עזר פרטי · אינו כלי רשמי של צה\"ל</div></div>", unsafe_allow_html=True)


def _render_settings():
    """App-owned settings overlay (mockup 8a–8e) — a screen state machine.
    Not st.dialog: a dialog dismiss doesn't run the full script (the machine
    would strand) and dialogs can't nest. This mirrors the drawer overlay."""
    st.markdown(_DS_CSS, unsafe_allow_html=True)
    # backdrop (the gutters on wide viewports) closes settings
    if st.button("סגירת הגדרות", key="settings_backdrop"):
        st.session_state.show_settings = False
        st.rerun()
    screen = st.session_state.get("settings_screen", "hub")
    titles = {"hub": "הגדרות", "personal": "פרטים אישיים", "language": "שפה",
              "access": "גודל טקסט",
              "privacy": "פרטיות ואבטחה", "about": "תנאי שימוש"}
    with st.container(key="cai_settings"):
        _cb, _ct = st.columns([1, 5])
        with _cb:
            if st.button("›", key="settings_back"):
                if screen == "hub":
                    st.session_state.show_settings = False
                else:
                    st.session_state.settings_screen = "hub"
                st.rerun()
        with _ct:
            st.markdown(f"<div class='cai-set-title'>{titles.get(screen, 'הגדרות')}</div>",
                        unsafe_allow_html=True)
        if screen == "personal":
            _settings_personal()
        elif screen == "language":
            _settings_language()
        elif screen == "access":
            _settings_access()
        elif screen == "privacy":
            _settings_privacy()
        elif screen == "about":
            _settings_about()
        else:
            _settings_hub()


def handle_question(question: str):
    quota = metrics.reserve(st.session_state.session_id)
    if quota != "ok":
        # error on the USER turn too: an unanswered question replayed into
        # the next request rides as a second consecutive user turn — the API
        # merges them and the model answers the stale question (bug-sweep
        # 2026-07-27). Same for every failure path below.
        st.session_state.messages.append({"role": "user", "content": question, "error": True})
        st.session_state.messages.append({
            "role": "assistant",
            "content": _QUOTA_NOTICES[quota],
            "error": True,  # UI-only, never replayed as LLM history
        })
        return
    user_msg = {"role": "user", "content": question}
    st.session_state.messages.append(user_msg)
    # error notices are UI-only — replaying them as LLM history would just
    # confuse the model. User turns replay the exact content that was sent
    # to the API (question + retrieved context, kept in api_content), so
    # follow-up requests share a byte-identical prefix and hit the prompt
    # cache; the bare question stays in "content" for display.
    history = [
        {"role": m["role"], "content": m.get("api_content", m["content"])}
        for m in st.session_state.messages[:-1]
        if not m.get("error")
    ]
    # The conversation loop already rendered without this turn, so draw the
    # user bubble now and stream the answer into a live assistant bubble
    # (chip-first, via _stream_answer); the rerun that follows re-renders
    # both from session state (adding the actions row).
    with st.chat_message("user"):
        st.markdown(question)
    t0 = time.time()
    # a stale cached backend from a previous cloud build may predate the
    # `profile` parameter (see deploy note in backend.py) — feature-detect
    # instead of crashing every question until the process restarts
    profile_kw = {}
    if "profile" in inspect.signature(stream_ai_answer).parameters:
        # Compose the asker's details: status pills (profile_saved) always,
        # plus service type/track ONLY after an explicit save. An untouched
        # user yields [] -> None, so the composed user turn stays
        # byte-identical to the pre-profile format (prompt-cache prefix).
        # These all mirror dialog/drawer widgets whose session keys Streamlit
        # drops on the runs where the widget isn't rendered.
        _injected = list(st.session_state.get("profile_saved") or [])
        if st.session_state.get("profile_customized"):
            _svc = st.session_state.get("service_type")
            if _svc:
                _injected.append(f"שירות {_svc}")
            _track = st.session_state.get("service_track")
            if _track:
                _injected.append(f"מסלול שירות: {_track}")
        # miluim-tool data enriches RESERVE-persona answers only. mil_salary is
        # deliberately absent — it exists for the local tagmul estimate and
        # must never reach the API.
        if st.session_state.role == "reserve" and st.session_state.get("mil_saved"):
            _dy, _d3 = st.session_state.get("mil_days_year"), st.session_state.get("mil_days_3y")
            if _dy is not None and _d3 is not None:
                _injected.append(f"ימי מילואים: {int(_dy)} השנה, {int(_d3)} בתלת-שנתי")
            _emp_labels = {"employee": "שכיר", "self_employed": "עצמאי", "student": "סטודנט"}
            _emp = [_emp_labels[e] for e in (st.session_state.get("mil_emp") or []) if e in _emp_labels]
            if _emp:
                _injected.append("במקביל למילואים: " + ", ".join(_emp))
        profile_kw["profile"] = _injected or None
    # chunks received so far, tapped by _stream_answer as they arrive: when a
    # RerunException detonates mid-stream (any widget event during the 15-30s
    # answer — a feedback thumb, the drawer, a second submit, an iOS reconnect
    # rerun), write_stream dies with the text unreachable and the tokens
    # already billed. The tap makes the partial salvageable (bug-sweep
    # 2026-07-27, CONFIRMED against streamlit 1.58 sources: RerunException
    # inherits BaseException precisely to bypass `except Exception`).
    acc: list[str] = []
    sources: list = []
    try:
        # The answer bubble opens IMMEDIATELY with a skeleton + a staged,
        # truthful status line — no generic spinner, no surface swap. The
        # stages mirror the real pipeline: stage 1 spans the rewrite +
        # retrieval inside stream_ai_answer; stage 2 the model's reading/
        # writing pause until the first token (2026-08-03 wait-experience
        # round; p50 ~19s in production made this THE first impression).
        # Error paths need no cleanup — the caller reruns unconditionally.
        with st.chat_message("assistant"):
            stage = st.empty()
            stage.markdown(_stage_html("מאתר פקודות רלוונטיות…"),
                           unsafe_allow_html=True)
            result = stream_ai_answer(question, history, role=st.session_state.role, **profile_kw)
            text_gen, sources = result[0], result[1]
            # Streamlit Cloud can pair a fresh app.py with a backend module
            # cached from a previous build (see note in backend.py) — older
            # builds returned 2 items and no sent-content
            if len(result) > 2:
                user_msg["api_content"] = result[2]
            stage.markdown(_stage_html("קורא את הסעיפים ומנסח…"),
                           unsafe_allow_html=True)
            text = _stream_answer(text_gen, acc, think=stage)
    except (APIConnectionError, APITimeoutError):
        user_msg["error"] = True
        metrics.refund(st.session_state.session_id)  # failures don't burn quota
        st.session_state.messages.append({
            "role": "assistant",
            "content": "**אין כרגע חיבור לשירות.**\n\n"
                       "בדוק את החיבור לאינטרנט ושלח את השאלה שוב בעוד רגע.",
            "error": True,
        })
        return
    except BadRequestError as e:
        # the monthly console spend limit returns a 400 with this exact
        # phrasing (hit live 2026-07-10); "try again" would gaslight the
        # user into resending a question that cannot succeed
        user_msg["error"] = True
        metrics.refund(st.session_state.session_id)
        if "usage limits" in str(e):
            msg = ("⏸️ **המערכת בהשהיה זמנית עקב מגבלת שימוש.**\n\n"
                   "זו לא תקלה אצלך ואין טעם לשלוח שוב עכשיו — נסה שוב מחר.")
        else:
            msg = "**אירעה שגיאה זמנית בעיבוד השאלה.**\n\nנסה לשלוח אותה שוב."
        st.session_state.messages.append({"role": "assistant", "content": msg, "error": True})
        return
    except Exception as e:
        # last-resort catch: the refund + generic message already cover the
        # user, but without a log a real production fault leaves no trace
        safe_print(f"[chat] answer failed: {e!r}")
        user_msg["error"] = True
        metrics.refund(st.session_state.session_id)
        st.session_state.messages.append({
            "role": "assistant",
            "content": "**אירעה שגיאה זמנית בעיבוד השאלה.**\n\n"
                       "נסה לשלוח אותה שוב.",
            "error": True,
        })
        return
    except BaseException:
        # RerunException / StopException land here (they bypass the handlers
        # above by design). Settle state before letting them propagate: with
        # received chunks — keep the partial answer (rendered on the rerun
        # under the existing truncation warning); with none — the user paid
        # quota for nothing, refund and leave a visible retry notice.
        if acc:
            st.session_state.messages.append({
                "role": "assistant",
                "content": "".join(acc),
                "sources": sources,
                "truncated": True,
            })
        else:
            user_msg["error"] = True
            metrics.refund(st.session_state.session_id)
            st.session_state.messages.append({
                "role": "assistant",
                "content": "**התשובה נקטעה באמצע.**\n\nשלח את השאלה שוב.",
                "error": True,
            })
        raise
    st.session_state.messages.append({
        "role": "assistant",
        "content": text,
        "sources": sources,
        # answer cut mid-sentence by the shared thinking+answer token cap —
        # the render loop warns instead of passing a half-answer as complete
        "truncated": bool(result[3].get("truncated")) if len(result) > 3 else False,
    })
    # analytics opt-out (privacy settings) suppresses ONLY this usage log —
    # never the quota reserve/refund, which the app needs to function.
    if st.session_state.get("share_analytics", True):
        metrics.log_question(
            session_id=st.session_state.session_id,
            device_id=st.session_state.device_id,
            role=st.session_state.role or "",
            question=question,
            answer=text,
            sources=sources,
            # usage rides back in the 4th return element (per-call, race-free);
            # the len guard + getattr fall back gracefully if a stale cached
            # backend from a previous cloud build predates this contract
            usage=(result[3] if len(result) > 3
                   else getattr(backend, "last_usage", None)),
            latency_s=time.time() - t0,
        )


def _pdf_media_url(source_file: str, coord: str) -> str | None:
    """Register the order's PDF with Streamlit's media file manager and
    return its serving URL (e.g. /media/<hash>.pdf).

    This is the channel st.download_button itself uses — served over the
    app's own protocol with Content-Type application/pdf, so a plain link
    to it OPENS in the browser's viewer instead of downloading, and it
    works identically locally and behind the Streamlit Cloud shell (unlike
    /app/static, which never served there). The manager dedups by content
    hash; `coord` keeps the entry alive for this element across reruns.
    """
    data = _pdf_bytes_cached(source_file)
    if not data:
        return None
    try:
        from streamlit.runtime import get_instance
        # no file_name: (a) it's part of the content-hash id, so this entry
        # never collides with a DOWNLOADABLE registration of the same bytes,
        # and (b) nameless MEDIA entries are served without
        # Content-Disposition — the browser opens the PDF inline instead of
        # downloading it
        return get_instance().media_file_mgr.add(data, "application/pdf", coord)
    except Exception:
        return None


def _search_norm(s: str) -> str:
    """Normalize a string for the orders search: Hebrew gershayim/geresh fold
    to ASCII quotes (mobile keyboards emit ״/׳ while titles store ") and
    Latin text is case-folded."""
    return s.replace("״", "\"").replace("׳", "'").strip().casefold()


def _pdf_static_url(source_file: str | None) -> str | None:
    """URL of an order's PDF, served straight off disk by Streamlit.

    Replaces the media-file-manager route for the ORDERS LIST specifically.
    That route needed the PDF's *bytes* in hand to mint a URL, so drawing a
    list of 80 orders meant reading 52.5 MB — measured at ~2.9 s of blocking
    I/O on a cold cache, and 52.5 MB resident afterwards. A static path costs
    a dict lookup, which is what makes the list cheap enough to render on
    every drawer paint (and therefore cheap enough to expand with no rerun).

    Relative on purpose, exactly like the media URLs it replaces: the app
    document sits at "/" here and at "/~/+/" inside the Streamlit Cloud
    shell, and "app/static/..." resolves correctly against both.
    """
    if not source_file or source_file not in _STATIC_PDFS:
        return None
    return "app/static/" + urllib.parse.quote(source_file)


def _order_link(title: str, url: str | None, date_badge: str | None = None,
                doc_id: str = "") -> str:
    """One order line for the sidebar list. When the PDF is on disk the
    title itself is the tap target that opens it INLINE in a new tab.

    `date_badge` is the order's own version date (doc_dates.badge) — orders
    without a confident date get no badge rather than a made-up one.

    data-q carries the row's searchable text, normalized server-side by the
    SAME rules the search box applies client-side (_search_norm), so filtering
    is a substring test in the browser instead of a round-trip per keystroke.
    """
    safe_title = f"<span class='cai-order-tt'>{html.escape(title)}</span>"
    tail = f"<span class='cai-order-date'>נוסח {date_badge}</span>" if date_badge else ""
    q = html.escape(_search_norm(f"{title} {doc_id}"), quote=True)
    if url:
        return (f"<a class='cai-order-link' data-q=\"{q}\" href='{url.lstrip('/')}'"
                f" target='_blank' rel='noopener'>{safe_title}{tail}</a>")
    return f"<div class='cai-order-link' data-q=\"{q}\">{safe_title}{tail}</div>"


def _orders_panel(docs: list[dict]) -> str:
    """The whole "פקודות מטכ״ל במערכת" accordion as ONE inert markup blob.

    Nothing here talks to the server. The card is a real <button> (so the
    swipe engine's tap heuristic, which keys off `button, a`, gives it the
    generous 30px slop meant for thumb taps), expanding is a class on <html>,
    and searching filters the rows in place. That is the entire fix for
    "sometimes it just hangs / takes forever to open": the previous version
    toggled st.session_state, and every open AND close paid a full rerun —
    ~3.5 s on device — on top of the cold-cache PDF reads.

    Rendering it unconditionally (open or closed) is only affordable because
    _pdf_static_url costs nothing; the rows are ~80 anchors of markup.
    """
    rows = "".join(
        _order_link(d["title"], _pdf_static_url(d.get("source_file")),
                    _doc_date_badge(d["id"]), str(d["id"]))
        for d in docs
    )
    if not docs:
        body = "<div class='cai-orders-empty'>אין פקודות טעונות</div>"
    else:
        body = (
            "<input class='cai-orders-q' type='search' autocomplete='off'"
            " enterkeyhint='search' aria-label='חיפוש פקודה'"
            " placeholder='חיפוש פקודה...'>"
            f"<div class='cai-orders-scroll'>{rows}</div>"
            "<div class='cai-orders-empty' data-none hidden>לא נמצאו פקודות מתאימות</div>"
        )
    return (
        "<div class='cai-kb'>"
        "<button type='button' class='cai-kb-card' aria-expanded='false'>"
        "<span class='kb-ic'></span>"
        "<span class='kb-title'>פקודות מטכ\"ל במערכת</span>"
        f"<span class='kb-badge'>{len(docs)}</span>"
        "<span class='kb-chev'></span></button>"
        f"<div class='cai-kb-body'>{body}</div>"
        "</div>"
    )


# ── Drawer (app-owned overlay) ──
# The native st.sidebar is force-suppressed by the cloud platform: on
# *.streamlit.app the frontend NEVER mounts stSidebar (verified 2026-07-13
# with a MutationObserver across the whole role-pick transition, on a build
# whose config.toml carries no toolbarMode override), even though the same
# code mounts it locally — the platform's client flags outrank config.toml.
# So the drawer is app-owned: a plain keyed container, fixed-positioned by our
# own CSS. No stSidebar machinery anywhere, so no platform build can take it
# away again.
# OPEN/CLOSE IS CLIENT-SIDE (2026-07-27 phone video). It used to be server
# state — drawer_open + st.rerun() — and every single open and close therefore
# repainted the entire app: component iframes reload, the conversation
# re-renders, the composer re-mounts. On device that reads as the whole screen
# reloading each time the menu moves. So the panel is now ALWAYS in the DOM and
# its state is one class on <html> (cai-drawer-open), flipped by the gesture
# engine — CSS does the sliding, the server is never told, nothing repaints.
# The three buttons below are pure tap targets: the engine intercepts their
# clicks in the capture phase, so their Python bodies never run.
st.button("תפריט", key="drawer_open_btn")
# full-viewport click-catcher UNDER the panel — tapping outside closes.
# Hidden (visibility + pointer-events) while the drawer is closed, so it
# cannot eat taps meant for the app.
st.button("סגירת התפריט", key="drawer_backdrop")
st.markdown(_DS_CSS, unsafe_allow_html=True)
# Modal stylesheet at PAGE level, not inside each dialog. Injected in the
# dialog body it arrived one frame LATE: Streamlit mounted the dialog, painted
# the card with the olive theme.backgroundColor at its default geometry, and
# only then did the <style> stream in and repaint it dark — a wrong-looking
# panel flashing on EVERY tool open (2026-07-30 device video, all three tools).
# Emitted here it is already in the document before any dialog can mount, so
# the first painted frame is the finished modal. Cost is one <style> per
# rerun instead of one per dialog open, and the block is scoped to
# stDialog/.cai-m* throughout (the only page-wide rules in it are the
# baseweb popover ones, which were written to be global anyway).
st.markdown(_MODAL_CSS, unsafe_allow_html=True)
with st.container(key="cai_drawer"):
    # ── top row: settings gear (right/leading) + close « (left/trailing) ──
    _c_gear, _c_close = st.columns(2)
    with _c_gear:
        # the dialog overlays a live drawer that stays open behind it —
        # the open class survives this rerun (it lives on <html>, which
        # Streamlit never replaces), so dismissing returns to the drawer
        if st.button("⚙", key="open_settings"):
            st.session_state.show_settings = True
            st.session_state.settings_screen = "hub"
            st.rerun()
    with _c_close:
        st.button("«", key="drawer_close")

    # ── role card (display only; role switching lives in Settings) ──
    _svc_type = _service_type_shown()
    _role_badge = "שירות חובה" if _svc_type == "סדיר" else _svc_type
    # with a saved name: initial + name up front, role folds into the
    # small key line ("מחובר כ־חייל"); without — exactly the old card
    _dnd = _display_name()
    _card_av = (_dnd or role_label)[:1]
    _card_k = f"מחובר כ־{role_label}" if _dnd else "מחובר כ־"
    _card_nm = _dnd or role_label
    # a badge that repeats what the card already says ("מילואים" beside
    # "מחובר כ־מילואים") is noise — render it only when it adds information
    _badge_html = (f"<span class='cai-role-badge'>{html.escape(_role_badge)}</span>"
                   if _role_badge != role_label else "")
    st.markdown(
        "<div class='cai-role-card'>"
        f"<div class='cai-role-av'>{html.escape(_card_av)}</div>"
        "<div class='cai-role-meta'>"
        f"<div class='cai-role-k'>{html.escape(_card_k)}</div>"
        f"<div class='cai-role-nm'>{html.escape(_card_nm)}</div></div>"
        f"{_badge_html}"
        "</div>",
        unsafe_allow_html=True,
    )

    # ── knowledge base — orders list ──
    # Rendered whole on every drawer paint, open or closed: expanding it and
    # searching it are both pure client-side (see _orders_panel and the swipe
    # engine's accordion section). The server is never told, so neither costs
    # a rerun. ONE markdown for the entire panel — per-row st.markdown calls
    # would each be siblings in the drawer flow and stretch it viewport-long.
    st.markdown("<div class='cai-sec-label'>מאגר הידע</div>", unsafe_allow_html=True)
    docs = get_loaded_docs_info(role=st.session_state.role)
    with st.container(key="cai_kb"):
        st.markdown(_orders_panel(docs), unsafe_allow_html=True)

    # ── tools (grouped card) — role-aware: each persona sees its own set.
    # RESERVE gets the miluim tools; COMMANDER gets the command kit (the חובה
    # entitlements calculator and the 4/5-soldier-oriented letters generator
    # left this drawer on purpose — 2026-08-06 commander-tools spec: everyday
    # entitlement questions belong in chat); SOLDIER keeps today's exact set. ──
    st.markdown("<div class='cai-sec-label'>כלים</div>", unsafe_allow_html=True)
    with st.container(key="cai_tools"):
        # the tool dialogs overlay a live drawer that stays open behind
        # them (same as the gear above), so dismissing one returns the
        # user straight to the menu.
        if st.session_state.role == "reserve":
            if _mb and st.button("מה מגיע לי במילואים", key="open_mil_benefits", use_container_width=True):
                _miluim_benefits_dialog()
            if _mg and st.button("קיבלתי צו — דחייה והתייצבות", key="open_mil_guide", use_container_width=True):
                _miluim_guide_dialog()
            if _pa and st.button("בודק סמכות עונש", key="open_punishment", use_container_width=True):
                _punishment_dialog()
        elif st.session_state.role == "commander":
            # the personal-value slot follows the profile's service type. The
            # 2026-08-06 commander spec left a חובה commander with NO map,
            # because no conscript map existed yet; now one does, and a מ"כ or
            # a קצין בחובה is a conscript for every entitlement purpose.
            if _kb and _service_type_shown() == "קבע" and st.button(
                    "מה מגיע לי בקבע", key="open_keva_benefits", use_container_width=True):
                _keva_benefits_dialog()
            if _cm and _service_type_shown() != "קבע" and st.button(
                    "מה מגיע לי בשירות חובה", key="open_cmap_cmd",
                    use_container_width=True):
                _conscript_map_dialog()
            if _ab and st.button("חייל לא התייצב", key="open_absence", use_container_width=True):
                _absence_dialog()
            if _dg and st.button("חייל במצוקה נפשית", key="open_distress", use_container_width=True):
                _distress_dialog()
            if _ig and st.button("אירוע ביחידה — למי מדווחים", key="open_incident", use_container_width=True):
                _incident_dialog()
            if _pa and st.button("בודק סמכות עונש", key="open_punishment", use_container_width=True):
                _punishment_dialog()
        else:
            # SOLDIER — four items, no category headers: a flat list of four is
            # scanned in a second, which is why the seven-tool version of the
            # spec was dropped. The map REPLACES the entitlements calculator
            # (entitlements.py stays on as its verified data source): two
            # buttons answering the same question force a choice the user
            # cannot yet make.
            if _cm and st.button("מה מגיע לי בשירות חובה", key="open_cmap",
                                 use_container_width=True):
                _conscript_map_dialog()
            if _sd and st.button("אני במצוקה", key="open_sdistress",
                                 use_container_width=True):
                _soldier_distress_dialog()
            # deterministic tools, zero-token, no quota — each gated on its module
            if _pa and st.button("בודק סמכות עונש", key="open_punishment", use_container_width=True):
                _punishment_dialog()
            if LETTER_TYPES and st.button("מחולל מכתבים", key="open_letters", use_container_width=True):
                _letters_dialog()

    # ── recent conversations — only this role's (restoring a cross-role
    # chat would mix personas/doc scopes in one thread) ──
    role_history = [
        (i, conv) for i, conv in enumerate(st.session_state.conversation_history)
        if conv.get("role") == st.session_state.role
    ]
    _rc_head, _rc_clear = st.columns([3, 1])
    with _rc_head:
        st.markdown(
            "<div class='cai-recent-head'><span class='cai-recent-t'>שיחות אחרונות</span>"
            f"<span class='cai-recent-n'>{len(role_history)}</span></div>",
            unsafe_allow_html=True,
        )
    with _rc_clear:
        if role_history and st.button("נקה הכל", key="clear_recent"):
            # drop only this role's archived conversations
            st.session_state.conversation_history = [
                c for c in st.session_state.conversation_history
                if c.get("role") != st.session_state.role
            ]
            st.rerun()
    with st.container(key="cai_recent"):
        if role_history:
            for i, conv in role_history:
                if st.button(conv["title"], key=f"hist_{i}", use_container_width=True):
                    # archive the active chat first, exactly like "שיחה חדשה"
                    # and logout do — otherwise switching conversations drops
                    # the current one for good
                    archive_current_conversation()
                    st.session_state.messages = conv["messages"].copy()
                    st.rerun()
        else:
            st.caption("אין שיחות קודמות")

    # ── footer CTA ──
    if st.button("שיחה חדשה", key="new_chat", use_container_width=True):
        archive_current_conversation()
        st.session_state.messages = []
        st.rerun()
    st.markdown("<div class='cai-drawer-foot'>בלמ\"ס · לשימוש פנימי בלבד</div>", unsafe_allow_html=True)

# settings overlay (state machine) — shown whenever the flag is set; opening it
# leaves the drawer open underneath so closing returns there.
if st.session_state.get("show_settings"):
    _render_settings()

# ── Header: wordmark + identity cluster (boxless, user pick 2026-08-03 —
# "variant 1": name over role as two quiet lines, no pill chrome; with no
# saved name the role alone takes the name slot) ──
_dn = _display_name()
if _dn:
    _ident = (f"<span class='cai-ident'><span class='nm'>{html.escape(_dn)}</span>"
              f"<span class='rl'>{html.escape(role_label)}</span></span>")
else:
    _ident = f"<span class='cai-ident'><span class='nm'>{html.escape(role_label)}</span></span>"
st.markdown(
    f"<div class='cai-header'>"
    f"<span class='cai-wordmark'>Command<span class='cai-wm-ai'>AI</span></span>"
    f"{_ident}"
    f"</div>",
    unsafe_allow_html=True,
)

# Bidi/zero-width marks the model occasionally emits around RTL text; \s
# matches none of them, so they must be tolerated explicitly wherever the
# line or the verdict is anchored/stripped — else the chip silently vanishes.
# LRM RLM ZWSP BOM, embedding/override controls, directional isolates.
_BIDI_MARKS = "\u200e\u200f\u200b\ufeff\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
# [^\S\n]*$ (not \s*$): the trailing-space eater must stop at the newline —
# a greedy \s*$ swallows it, and the paragraph join below needs the remainder
# to carry its own line breaks (partial stream buffers end ON the newline).
_VERDICT_RE = re.compile(
    r"^\s*[" + _BIDI_MARKS + r"]*\*\*פסיקה:\*\*\s*(.+?)[^\S\n]*$", re.MULTILINE
)
_REFUSAL_SENTENCE = "המידע לא קיים בפקודות שסופקו"  # mandated verbatim by _COMMON_RULES
# _VERDICT_TERM_RE / _QUAL_CONFLICT_RE — the chip's term gate and the
# compound-qualifier bar — moved to verdict.py (CHIP_TERM_RE /
# QUAL_CONFLICT_RE, imported at the top with the sibling defensive
# fallback) so the gate is unit-tested in eval's structural suite and
# shared with chip_clause(), the two-sided stacked-chip path's gate.
# Their full rationale (negation forms, topic openers, the badge cap)
# lives there.
# Past this length a chip stops being a pill and becomes a sentence, so it
# switches to .verdict-wrap (white-space:normal, softer radius) instead of
# overflowing a nowrap pill. Both chip paths use it — that shared wrap is
# what let the term gate settle on one qualifier cap (verdict.CHIP_QUAL_MAX).
_CHIP_WRAP_OVER = 28


def _verdict_chip(content: str) -> tuple[str | None, str]:
    """(chip_html, display_body) for an assistant answer.

    The system prompt mandates a `**פסיקה:** ...` line on ruling questions;
    when its leading clause opens with a recognized verdict term — bare
    ("מותר") or with a short qualifier ("אסור בתנועה רגלית") — that clause
    becomes a colored chip and leaves the displayed body (the copy/share
    payload keeps the original text). Topic-led, compound (the qualifier
    cites another verdict or a negation), or long free-form ruling lines
    stay in the body untouched — a wrong chip is worse than no chip.
    Honest refusals (the mandated sentence near the top) get a neutral
    chip so "no answer" reads as designed behavior.
    """
    m = _VERDICT_RE.search(content)
    if m:
        # The model often appends the explanation to the same line ("מותר
        # בתנאים — עישון אסור...", "אסור בתנועה רגלית; מותרת אוזניה..."):
        # the chip carries only the verdict clause, the remainder returns
        # to the body as its opening line.
        # ./:/; split only before whitespace, so סעיף 3.4 or 14:30 stay
        # whole; ־ only spaced, so חד־פעמי stays whole.
        raw = m.group(1).strip("* " + _BIDI_MARKS)
        # Two-sided ruling — conflict questions where the asker's own
        # conduct is judged alongside the asked question (pilot feedback
        # 2026-07-27): exactly two ';'-separated clauses, EACH independently
        # badge-worthy on its own (chip_clause: a verdict term + short
        # qualifier, or a ≤60-char "לא נמצא..." no-rule clause), render as
        # stacked chips with nothing returning to the body. Any clause that
        # can't stand alone falls through to the single-clause path below
        # unchanged. The 400-char stream spill guard in _stream_answer stays
        # sound: both chip caps (50-char qualifier / 60-char none clause)
        # sit far below 400, so a mid-line cut can never produce two valid
        # chips that the full-line rerun parse would reject.
        clauses = [c for c in raw.split(";") if c.strip()]
        if len(clauses) == 2:
            chips = [_chip_clause(c) for c in clauses]
            if all(chips):
                remainder = content[m.end():]
                body = content[: m.start()]
                if remainder:
                    body += "\n\n" + remainder.lstrip("\n")
                # .verdict-wrap by LENGTH, not clause kind: a clause may carry
                # a long qualifier (verdict.CHIP_QUAL_MAX) and a nowrap pill
                # would overflow the 290px breakpoint; short pills keep the
                # tighter nowrap look.
                stack = '<div class="verdict-stack">' + "".join(
                    f'<span class="verdict-chip verdict-{cls}'
                    f'{" verdict-wrap" if len(text) > _CHIP_WRAP_OVER else ""}">'
                    f"{icon} {html.escape(text)}</span>"
                    for cls, icon, text in chips
                ) + "</div>"
                return stack, body.lstrip()
        parts = re.split(r"\s*(—|–| - | ־ |[.:;](?=\s))\s*", raw, maxsplit=1)
        verdict = parts[0].strip("* ." + _BIDI_MARKS)
        sep = parts[1] if len(parts) > 2 else ""
        rest = parts[2].strip("* ") if len(parts) > 2 else ""
        # a ';' whose remainder is not itself a ruling clause is a list cut
        # mid-way ("אסור בשישי; שבת וחג") — chipping the first item would
        # misstate the ruling, so the line stays whole (and unchipped: the
        # qualifier charset bars ';').
        if sep == ";" and not _QUAL_CONFLICT_RE.search(rest):
            verdict, rest = raw.strip("* ." + _BIDI_MARKS), ""
        mt = _VERDICT_TERM_RE.match(verdict)
        qual = (mt.group("qual") or "").strip() if mt else ""
        if mt and (
            _QUAL_CONFLICT_RE.search(qual)                       # compound ruling
            or (mt.group("neg") and mt.group("term") == "אסור")  # לא אסור — double negative, no honest single color
            or (qual and mt.group("term") in ("ניתן", "אפשר") and not qual.startswith("ל"))  # ניתן צו... — passive verb, not the modal
            # a BARE authority/modal term answers nothing on its own — a
            # "✓ מוסמך" pill left the pilot user asking "מוסמך למה?"
            # (2026-07-21 phone report). מותר/אסור/זכאי/פטור/חייב carry a
            # complete ruling alone; מוסמך/רשאי/ניתן/אפשר need their object
            # ("מוסמך להטיל עד 30 יום") or they stay body text.
            or (not qual and mt.group("term") in ("מוסמך", "רשאי", "ניתן", "אפשר"))
            # a BARE verdict against an alternate ';' clause ("אסור; מותר
            # בתנאים") is compound — a flat chip would contradict the body's
            # first words. A QUALIFIED verdict is scoped and honest next to
            # it ("אסור בתנועה רגלית; מותרת אוזניה...").
            or (not qual and sep == ";" and _QUAL_CONFLICT_RE.search(rest))
        ):
            mt = None
        if mt:
            # the ⚠ shape is the mandated "X בתנאים / X חלקית" (possibly
            # continued: "בתנאים מסוימים"); בתנאים deeper in the qualifier
            # is scope, not a conditional verdict ("אסור לנוע בתנאים קשים"
            # is a plain אסור). Otherwise color follows the OPENING term.
            if qual.startswith(("בתנאים", "חלקית")):
                # U+FE0E forces TEXT presentation: bare U+26A0 is emoji-styled
                # on iOS, breaking the chip's monochrome tint (2026-08-04)
                icon, cls = "⚠︎", "cond"
            elif mt.group("neg") or mt.group("term") == "אסור":
                icon, cls = "✗", "no"
            else:
                icon, cls = "✓", "yes"
            # rest becomes its own paragraph — joined with "\n\n" so a
            # single-newline follow-up field (**מקור:** …) doesn't run into
            # it mid-paragraph; the remainder's own leading newlines fold
            # into the break, so the stream parse and the rerun parse render
            # byte-identically. An EMPTY remainder means the ruling line is
            # still streaming (or ends the message) — append nothing, the
            # next chunk continues the clause seamlessly. lstrip, not strip:
            # mid-stream the trailing break belongs ahead of the next chunk.
            remainder = content[m.end():]
            body = content[: m.start()] + rest
            if remainder:
                body += "\n\n" + remainder.lstrip("\n")
            body = body.lstrip()
            wrap = " verdict-wrap" if len(verdict) > _CHIP_WRAP_OVER else ""
            # .verdict-solo: the block-level wrapper that cancels Streamlit's
            # margin-bottom:-1rem, exactly as .verdict-stack does for the
            # two-chip case. A bare inline chip left the next block sitting on
            # top of it (see the CSS note).
            chip = (f'<div class="verdict-solo">'
                    f'<span class="verdict-chip verdict-{cls}{wrap}">'
                    f"{icon} {html.escape(verdict)}</span></div>")
            return chip, body
    # neutral chip only when the refusal IS the answer (sentence at the
    # top, incl. after a short topic prefix like "לגבי סכום המענק — ") —
    # substantive answers often carry the same sentence later, either as a
    # trailing scope caveat or as the ruling for only PART of a compound
    # question ("פטור בתנאים; ... — המידע לא קיים"), and those must not be
    # labeled "not found". 80 chars covers marker + topic prefix; a real
    # verdict before the sentence pushes it past that.
    idx = content.find(_REFUSAL_SENTENCE)
    if 0 <= idx < 80:
        # Rule 2א tiers the refusal: a question the orders were never the tool
        # for gets routed to the framework that DOES govern it, and one that
        # belongs in an order we simply do not hold says so. Both are honest
        # "no ruling" states, so both keep the neutral colour — only the label
        # changes, because "לא נמצא במאגר" reads as a dead end and was the
        # single most common thing users hit (10.08.2026 blind measurement:
        # 12 of 16 unanswered asks had no order behind them at all, and about a
        # third of those no order will ever answer). The bare chip stays as the
        # fallback for an answer that skipped the marker.
        label = "לא נמצא במאגר"
        if _MARK_OOS in content:
            label = "לא נקבע בפקודות"
        elif _MARK_MISS in content:
            label = "טרם במאגר"
        return (f'<div class="verdict-solo">'
                f'<span class="verdict-chip verdict-none">ⓘ {label}</span></div>'), content
    return None, content


def _render_body(body: str, chip: str | None = None) -> None:
    """Draw a settled answer body through the answer-language formatter.

    The prompt mandates a fixed label grammar (**מקור:**, **תנאים:**,
    **התנהלות X:**, the two scope_routes markers), and answer_format turns
    exactly those into styled rows — everything else stays a plain markdown
    run and renders identically to before. Two properties this call site
    depends on:

    * Prose runs arrive WHOLE. Streamlit's stMarkdownContainer carries
      margin-bottom:-1rem, so two adjacent st.markdown calls butt together
      with no gap at all; splitting a paragraph across calls would read as a
      rendering bug (see the run-merging note in answer_format).
    * unsafe_allow_html is set ONLY on the formatter's own markup. Model text
      inside it is html.escape'd there; prose still goes through the plain,
      HTML-free path it always used.

    `chip` is the already-rendered verdict pill, passed in only to spot the
    NEUTRAL one: when it fires, "לא נקבע בפקודות מטכ\"ל" is on screen three
    times over (pill, the model's refusal sentence, and the routing block's
    own label), and a reader took that for the app refusing on content the
    corpus holds. The routing block drops its label in that case and keeps it
    in every other, where the pill carries a real verdict instead.

    A stale cloud build without the module falls back to today's rendering.
    """
    if answer_format is None:
        st.markdown(body)
        return
    route_label = "verdict-none" not in (chip or "")
    for block in answer_format.blocks(body):
        markup = answer_format.to_html(block, route_label=route_label)
        if markup is None:
            st.markdown(block[1])
        else:
            st.markdown(markup, unsafe_allow_html=True)


# Shown inside the assistant bubble while the model reasons before its first
# token. Opus runs adaptive thinking with no deltas yielded, so the bubble
# would otherwise sit blank for several seconds after the retrieval spinner
# cleared — the "dead empty bubble" the 2026-07-21 phone video caught (~7s).
def _stage_html(text: str) -> str:
    """One staged wait surface: an answer-shaped skeleton (chip pill + three
    shimmering lines) with a truthful caption that advances with the real
    pipeline. The SHAPE stays fixed across stages — only the words move —
    so the bubble never jumps."""
    return (
        '<div class="cai-skel">'
        '<span class="skc"></span>'
        '<span class="skr"></span><span class="skr w84"></span>'
        '<span class="skr w58"></span>'
        '</div>'
        '<div class="cai-thinking"><span class="cai-think-dots">'
        '<i></i><i></i><i></i></span>'
        f'<span>{html.escape(text)}</span></div>'
    )


def _stream_answer(text_gen, acc: list[str] | None = None, think=None) -> str:
    """Render the live answer chip-first: hold the stream until the first
    line is complete; when it is a recognizable **פסיקה:** line, draw the
    chip immediately and stream only the body under it. Without this the
    raw ruling line flashes mid-stream and then jumps into a chip on the
    rerun (pilot phone feedback, 2026-07-10). Returns the FULL original
    text — session state and the copy/share payload keep the ruling line.

    `acc` collects every chunk AS RECEIVED (including the first-line buffer):
    when a mid-stream RerunException kills write_stream, the caller salvages
    the partial answer from it — the only copy that survives the unwind.
    """
    if acc is not None:
        def _tap(g):
            for c in g:
                acc.append(c)
                yield c
        text_gen = _tap(text_gen)
    it = iter(text_gen)
    # staged placeholder until the first real content is ready to paint —
    # covers both the pre-first-token thinking pause and the first-line buffer
    # below; cleared the moment we render the chip/stream. The caller passes
    # its own placeholder (the stage element already inside the bubble) so
    # the skeleton persists seamlessly from retrieval into this phase.
    if think is None:
        think = st.empty()
        think.markdown(_stage_html("קורא את הסעיפים ומנסח…"), unsafe_allow_html=True)
    buf = ""
    ended = True
    for chunk in it:
        buf += chunk
        if "\n" in buf or len(buf) > 400:
            ended = False
            break
    think.empty()
    chip, lead = None, buf
    # parse once the first line is DECIDED: a newline landed, the stream is
    # already over, or the 400-char spill guard hit — past 400 the chip
    # verdict cannot differ from the full-text rerun (either the clause
    # separator already arrived, or the clause is far beyond the badge cap
    # and both parses reject). A shorter mid-line cut must not chip.
    if "\n" in buf or len(buf) > 400 or ended:
        chip, lead = _verdict_chip(buf)
    if chip:
        st.markdown(chip, unsafe_allow_html=True)
    shown = st.write_stream(itertools.chain([lead], it)) or ""
    return buf + shown[len(lead):]


def _answer_actions(content: str, sources: list[dict] | None = None, pdf: tuple[str, str, int | None] | None = None) -> None:
    """Copy-to-clipboard + share-to-WhatsApp + share-card row under an
    assistant answer. `pdf` is (media_url, title, page) — used now only for
    the card's source-title footer; the cited-source view moved to a native
    button + in-app dialog (an iframe pill could only open a lost PDF tab).

    Rendered as a components.html iframe, so styles are inlined (the app's
    CSS can't reach in). Clipboard uses the async API with a textarea +
    execCommand fallback — navigator.clipboard is unavailable in non-secure
    or permission-restricted iframes (and flaky on iOS Safari).

    The card pill draws the answer onto a 1000px-wide canvas (brand header,
    the **פסיקה:** line boxed in the role accent, wrapped body, source
    footer) and hands the PNG to the OS share sheet where files are
    shareable; elsewhere it downloads. Canvas API only — no JS libs.
    """
    # json.dumps does NOT escape "<", so a literal "</script>" in the model's
    # answer (a user can coax it to echo one) would close this inline <script>
    # and run as markup — and this iframe is same-origin with the app document
    # (window.top reachable). Escaping "<" blocks the breakout on every payload.
    def _js(obj):
        return json.dumps(obj).replace("<", "\\u003c")
    payload = _js(content + "\n\n— CommandAI")
    src_title = _js(pdf[1] if pdf else None)
    # verdict clauses classified in Python (verdict.py) — the SINGLE source
    # of the card's colours; the card JS no longer classifies, only draws
    vclauses = _js(_verdict_clauses(content))
    components.html(
        f"""
        <!-- same Heebo/Suez One sheet the app imports: iframes don't inherit
             the parent's fonts, and the share-card canvas needs both loaded
             in THIS document -->
        <link href="https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;600;700;800&family=Suez+One&display=swap" rel="stylesheet">
        <style>
        /* ── THE FRAME PAINTS ITS OWN GROUND. ──
           Streamlit hardcodes `color-scheme: normal` on every component
           iframe (1.58, IFrame styled-component). When an iframe element's
           used colour scheme differs from the scheme of the document inside
           it, the engine stops compositing the frame transparently and fills
           its canvas opaquely, in the INNER document's scheme — and this
           document, having declared nothing, is light. That is the white slab
           behind העתק/וואטסאפ on the pilot's phone (2026-08-01 screenshot),
           and the same mechanism with the colours swapped produced the dark
           slab of 2026-07-18 that was pinned on the shell-darkener instead.
           Reproducible in any engine, no phone needed: put a srcdoc iframe on
           a dark page and force element and document to disagree — dark
           element over light document fills white, light element over dark
           document fills dark, and either way agreeing restores transparency.

           The app's `iframe {{ background: transparent !important }}` cannot
           reach this: that is the ELEMENT's background, which paints BEHIND
           the canvas the engine just made opaque. Nothing outside the frame
           can fix a canvas inside it.

           So stop depending on transparency at all: declare the scheme (no
           mismatch left to trigger the fill) AND paint the bubble's own
           colour (if some engine fills anyway, it fills with the colour that
           was already there). SURFACE is the same token --surface is built
           from, so the two cannot drift apart. */
        :root {{ color-scheme: dark; }}
        /* text-size-adjust: iOS Safari inflates small text inside iframes,
           blowing the pills up until the row wraps and the last pill (פתח
           PDF) is clipped by the fixed iframe height */
        html, body {{ -webkit-text-size-adjust: 100%; text-size-adjust: 100%;
                      background: {SURFACE}; }}
        body {{ margin:0; direction:rtl; }}
        /* one row, ALWAYS: wrapping used to rely on a ResizeObserver growing
           the iframe, but Streamlit keeps the layout slot at the declared
           height, so a wrapped second row painted OVER the content below
           (user's phone, 2026-07-12). Overflow scrolls horizontally instead
           — scrollbar hidden, pills clip at the edge as the affordance. */
        .row {{ display:flex; flex-wrap:nowrap; gap:8px; justify-content:flex-start;
                overflow-x:auto; scrollbar-width:none; font-family:Heebo,sans-serif; }}
        .row::-webkit-scrollbar {{ display:none; }}
        .act {{ display:inline-flex; align-items:center; gap:6px;
                background:rgba(236,237,230,.05); color:rgba(236,237,230,.75);
                border:1px solid rgba(236,237,230,.22); border-radius:99px;
                padding:5px 13px; font:500 12px Heebo,sans-serif;
                cursor:pointer; text-decoration:none; white-space:nowrap;
                transition:color .15s,border-color .15s,background .15s; }}
        .act:hover {{ color:{ACCENT}; border-color:{ACCENT};
                      background:rgba(236,237,230,.02); }}
        .act:active {{ color:{ACCENT}; border-color:{ACCENT};
                       background:rgba(236,237,230,.06); transform:scale(.96); }}
        /* fit all pills WITHOUT scrolling on phones: tighten the chrome and
           shorten שלח בוואטסאפ → וואטסאפ, "כרטיס" → icon only. 480, not 380: the
           user's iPhone gave the iframe ~390-430px and full labels
           overflowed — shrink well before the overflow point. */
        @media (max-width: 480px) {{
          .act {{ padding:5px 10px; }}
          .xtra {{ display:none; }}
        }}
        </style>
        <div class="row">
          <button class="act" id="copy"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> העתק</button>
          <!-- one wrapping span: the pill is inline-flex with gap, so bare
               text + .xtra as separate flex items would put the 6px gap
               INSIDE the word ("שתף ב וואטסאפ") -->
          <!-- inline WhatsApp glyph, not "✆": at pill size the dingbat read
               as a block/slash icon (2026-08-03 audit) -->
          <a class="act" id="wa" target="_blank" rel="noopener"><span><svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" style="vertical-align:-1px"><path d="M17.5 14.4c-.3-.15-1.76-.87-2.03-.97-.27-.1-.47-.15-.67.15-.2.3-.77.97-.94 1.17-.17.2-.35.22-.65.07-.3-.15-1.26-.46-2.4-1.48-.88-.79-1.48-1.76-1.65-2.06-.17-.3-.02-.46.13-.61.13-.13.3-.35.45-.52.15-.17.2-.3.3-.5.1-.2.05-.37-.02-.52-.08-.15-.67-1.62-.92-2.22-.24-.58-.49-.5-.67-.51h-.57c-.2 0-.52.07-.8.37-.27.3-1.04 1.02-1.04 2.5 0 1.47 1.07 2.9 1.22 3.1.15.2 2.11 3.22 5.1 4.51.71.31 1.27.49 1.7.63.72.23 1.37.2 1.88.12.58-.09 1.76-.72 2.01-1.41.25-.7.25-1.3.17-1.42-.07-.12-.27-.2-.57-.35zM12.05 21.6h-.01a9.53 9.53 0 0 1-4.86-1.33l-.35-.21-3.62.95.97-3.53-.23-.36a9.54 9.54 0 1 1 8.1 4.48zm0-21.1C5.7.5.55 5.65.55 12a11.4 11.4 0 0 0 1.53 5.73L.5 23.5l5.93-1.56a11.5 11.5 0 0 0 5.61 1.46h.01c6.35 0 11.5-5.15 11.5-11.5S18.4.5 12.05.5z"/></svg> <span class="xtra">שלח ב</span>וואטסאפ</span></a>
          <button class="act" id="card"><span><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.5"/><path d="m21 15-4.5-4.5L7 20"/></svg><span class="xtra"> כרטיס</span></span></button>
        </div>
        <script>
        const text = {payload};
        document.getElementById("wa").href =
            "https://wa.me/?text=" + encodeURIComponent(text);
        const btn = document.getElementById("copy");
        btn.addEventListener("click", async () => {{
            let ok = false;
            try {{ await navigator.clipboard.writeText(text); ok = true; }}
            catch (e) {{
                const ta = document.createElement("textarea");
                ta.value = text; document.body.appendChild(ta);
                ta.select();
                try {{ ok = document.execCommand("copy"); }} catch (e2) {{}}
                ta.remove();
            }}
            // innerHTML, not textContent — the label carries the inline copy
            // SVG, which a textContent round-trip would flatten away
            const prev = btn.innerHTML;
            btn.textContent = ok ? "✓ הועתק" : "ההעתקה נכשלה";
            setTimeout(() => {{ btn.innerHTML = prev; }}, 1600);
        }});
        // ── Share card: the answer drawn as a PNG (canvas API only) ──
        const cardBtn = document.getElementById("card");
        const srcTitle = {src_title};
        const VCLAUSES = {vclauses};
        const cardNote = (msg) => {{
            // same feedback pattern as the copy pill; innerHTML — the label
            // carries the .xtra span that textContent would flatten away
            const prev = cardBtn.innerHTML;
            cardBtn.textContent = msg;
            setTimeout(() => {{ cardBtn.innerHTML = prev; }}, 1600);
        }};
        function rrect(c, x, y, w, h, r) {{
            // ctx.roundRect is missing on pre-16 iOS Safari
            c.beginPath();
            c.moveTo(x + r, y);
            c.arcTo(x + w, y, x + w, y + h, r);
            c.arcTo(x + w, y + h, x, y + h, r);
            c.arcTo(x, y + h, x, y, r);
            c.arcTo(x, y, x + w, y, r);
            c.closePath();
        }}
        async function cardFonts() {{
            // the <link> above only DECLARES the faces — a face is fetched
            // when the DOM uses it, and the canvas-only weights never are;
            // fonts.load() forces them, failures fall back to sans-serif
            try {{
                await Promise.all([
                    document.fonts.load('400 40px "Suez One"'),
                    document.fonts.load("400 22px Heebo"),
                    document.fonts.load("600 20px Heebo"),
                    document.fonts.load("700 25px Heebo"),
                ]);
                await document.fonts.ready;
            }} catch (e) {{}}
        }}
        function drawCard() {{
            // palette mirrors the app CSS tokens (--bg/--surface gradient,
            // --text, role accent) so the card reads as the app's own
            const W = 1000, M = 64, xR = W - M, maxW = W - 2 * M;
            const cv = document.createElement("canvas");
            cv.width = W; cv.height = 8;
            const ctx = cv.getContext("2d");
            const FONTS = {{
                brand: '400 40px "Suez One", serif',
                tag: "400 20px Heebo, sans-serif",
                verdict: "700 25px Heebo, sans-serif",
                body: "400 22px Heebo, sans-serif",
                src: "600 20px Heebo, sans-serif",
                foot: "400 17px Heebo, sans-serif",
            }};
            const wrap = (t, mw) => {{
                const out = [];
                let cur = "";
                for (const w of t.split(/\\s+/).filter(Boolean)) {{
                    const cand = cur ? cur + " " + w : w;
                    if (cur && ctx.measureText(cand).width > mw) {{ out.push(cur); cur = w; }}
                    else cur = cand;
                }}
                if (cur) out.push(cur);
                return out;
            }};
            // strip the share suffix + markdown chrome; bidi/zero-width
            // marks break canvas run shaping (the chat renderer tolerates
            // them, ctx.fillText less so)
            const lines = text.replace(/\\n\\n— CommandAI$/, "")
                .split("\\n")
                .map((l) => l
                    .replace(/[\\u200e\\u200f\\u200b\\ufeff\\u202a-\\u202e\\u2066-\\u2069]/g, "")
                    .replace(/\\*\\*/g, "")
                    .replace(/^#+\\s*/, "")
                    .replace(/^\\s*[-*]\\s+/, "• ")
                    .trim());
            // verdict colors: text, box fill, box border — keyed to the
            // classes Python assigned (VCLAUSES, from verdict.py). The card
            // does NOT classify; it wraps + draws. A compound ruling
            // ("אסור אם X; מותר אם Y") arrives pre-split, one colored clause
            // per part.
            const VCOLORS = {{
                yes:  ["#A9C687", "rgba(148,183,110,.12)", "rgba(148,183,110,.5)"],
                cond: ["#D9B36A", "rgba(217,179,106,.11)", "rgba(217,179,106,.5)"],
                no:   ["#D68C77", "rgba(208,124,102,.11)", "rgba(208,124,102,.5)"],
                none: ["rgba(236,237,230,.75)", "rgba(236,237,230,.05)", "rgba(236,237,230,.28)"],
                accent: ["{ACCENT}", "{ACCENT_SOFT}", "{ACCENT_BORDER}"],
            }};
            ctx.font = FONTS.verdict;
            // drop the ruling line from the body — Python already parsed it
            // into VCLAUSES; the card must not print it twice
            if (lines.length && lines[0].indexOf("פסיקה:") === 0) lines.shift();
            const vClauses = VCLAUSES.map((c) => ({{ cls: c.cls, lines: wrap(c.text, maxW - 52) }}));
            const vLines = vClauses.reduce((n, c) => n + c.lines.length, 0);
            ctx.font = FONTS.body;
            const body = [];
            let nBody = 0, truncated = false;
            for (const line of lines) {{
                if (nBody >= 14) {{ truncated = truncated || !!line; continue; }}
                if (!line) {{
                    if (body.length && body[body.length - 1] !== "") body.push("");
                    continue;
                }}
                for (const wl of wrap(line, maxW)) {{
                    if (nBody >= 14) {{ truncated = true; break; }}
                    body.push(wl); nBody++;
                }}
            }}
            while (body.length && body[body.length - 1] === "") body.pop();
            if (truncated && body.length) body[body.length - 1] += " …";
            let title = srcTitle;
            if (title) {{
                ctx.font = FONTS.src;
                while (title.length > 2 && ctx.measureText(title).width > maxW) title = title.slice(0, -1);
                if (title !== srcTitle) title += "…";
            }}
            // vertical layout in baselines, then size the canvas to fit
            const boxTop = 184;
            const boxH = vLines ? vLines * 36 + 22 : 0;
            let y = vLines ? boxTop + boxH + 56 : boxTop + 18;
            const bodyPos = [];
            for (const l of body) {{
                if (l === "") {{ y += 14; continue; }}
                bodyPos.push([l, y]); y += 35;
            }}
            if (bodyPos.length) y -= 35;
            const sepY = y + 44;
            let fy = sepY + 44;
            const titleY = title ? fy : 0;
            if (title) fy += 31;
            const H = Math.ceil(fy + 50);
            cv.height = H;  // resizing wipes ctx state — set styles below
            const g = ctx.createLinearGradient(0, 0, 0, H);
            g.addColorStop(0, "#171A12"); g.addColorStop(.42, "#171A12");
            g.addColorStop(.68, "#1C2114"); g.addColorStop(.88, "#242C18");
            g.addColorStop(1, "#2A3420");
            ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
            ctx.strokeStyle = "rgba(236,237,230,.16)";
            ctx.strokeRect(.5, .5, W - 1, H - 1);
            ctx.direction = "rtl"; ctx.textAlign = "right";
            ctx.fillStyle = "#ECEDE6"; ctx.font = FONTS.brand;
            ctx.fillText("CommandAI", xR, 94);
            ctx.fillStyle = "rgba(236,237,230,.62)"; ctx.font = FONTS.tag;
            ctx.fillText("עוזר הפקודות של צה״ל", xR, 128);
            ctx.fillStyle = "{ACCENT}";
            ctx.fillRect(xR - 56, 146, 56, 3);
            if (vLines) {{
                // single clause: the box wears its verdict color like the
                // chat chip; compound: neutral box, each clause's TEXT in
                // its own color (a red box around a green מותר clause
                // would misstate the ruling)
                const boxC = vClauses.length === 1 ? VCOLORS[vClauses[0].cls] : VCOLORS.none;
                rrect(ctx, M, boxTop, maxW, boxH, 14);
                ctx.fillStyle = boxC[1]; ctx.fill();
                ctx.strokeStyle = boxC[2]; ctx.stroke();
                ctx.font = FONTS.verdict;
                let vi = 0;
                for (const c of vClauses) {{
                    ctx.fillStyle = VCOLORS[c.cls][0];
                    for (const l of c.lines) {{
                        ctx.fillText(l, xR - 26, boxTop + 33 + vi * 36);
                        vi++;
                    }}
                }}
            }}
            ctx.fillStyle = "rgba(236,237,230,.88)"; ctx.font = FONTS.body;
            for (const [l, ly] of bodyPos) ctx.fillText(l, xR, ly);
            ctx.fillStyle = "rgba(236,237,230,.16)";
            ctx.fillRect(M, sepY, maxW, 1);
            if (title) {{
                ctx.fillStyle = "rgba(236,237,230,.75)"; ctx.font = FONTS.src;
                ctx.fillText(title, xR, titleY);
            }}
            ctx.fillStyle = "rgba(236,237,230,.5)"; ctx.font = FONTS.foot;
            ctx.fillText("מבוסס על פקודות מטכ״ל · אינו ייעוץ משפטי", xR, fy);
            return cv;
        }}
        function cardDownload(blob) {{
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = "commandai-card.png";
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(a.href), 4000);
            cardNote("✓ ירד — צרף בוואטסאפ");
        }}
        cardBtn.addEventListener("click", async () => {{
            try {{
                await cardFonts();
                drawCard().toBlob((blob) => {{
                    if (!blob) {{ cardNote("היצירה נכשלה"); return; }}
                    const file = new File([blob], "commandai-card.png", {{ type: "image/png" }});
                    if (navigator.canShare && navigator.canShare({{ files: [file] }})) {{
                        // mobile share sheet (→ WhatsApp); a dismissed sheet
                        // is a user choice, only real failures fall back
                        navigator.share({{ files: [file] }}).catch((e) => {{
                            if (!e || e.name !== "AbortError") cardDownload(blob);
                        }});
                    }} else {{
                        cardDownload(blob);
                    }}
                }}, "image/png");
            }} catch (e) {{ cardNote("היצירה נכשלה"); }}
        }});
        // If the pills wrap (narrow phones, late font swap), grow the iframe
        // to fit — otherwise the second row is clipped and the PDF pill
        // disappears. A ResizeObserver on the row itself catches every
        // layout change (viewport resize, webfont load, copy-button text
        // swap), not just window resizes. srcdoc iframes are same-origin,
        // so frameElement is reachable.
        const row = document.querySelector(".row");
        const fitHeight = () => {{
            try {{
                const h = Math.ceil(row.getBoundingClientRect().height) + 4;
                window.frameElement.style.height = Math.max(38, h) + "px";
            }} catch (e) {{}}
        }};
        fitHeight();
        try {{ new ResizeObserver(fitHeight).observe(row); }}
        catch (e) {{ window.addEventListener("resize", fitHeight); }}
        </script>
        """,
        height=38,
    )


def _escalation_strip(sources: list[dict] | None, question: str = "") -> None:
    """"למי פונים" — the primary (top-ranked) source's referral chain as one
    quiet inline row between the answer body and the action pills, plus its
    note when one exists.

    A pure function of the message's sources + question: the chain is a
    deterministic document_id lookup (escalation_paths.path_for, zero LLM
    tokens, no session state), so the freshly-streamed answer and every
    history-replay rerun render the identical strip. No sources — no strip;
    and a pure information question gets no strip either (relevant_for):
    the chain earns its place only when there's something to pursue.
    """
    if not sources:
        return
    doc_id = sources[0].get("doc_id")
    # getattr: a stale cached cloud build may pair a fresh app.py with the
    # pre-gating module (see the backend deploy note) — then show, as before
    rel = getattr(escalation_paths, "relevant_for", None)
    if rel is not None and not rel(question, doc_id):
        return
    path = path_for(doc_id)
    steps = "<span class='cai-escal-sep'>←</span>".join(
        f"<span class='cai-escal-step'>{html.escape(step)}</span>"
        for step in path["steps"]
    )
    note = path.get("note")
    note_html = f"<div class='cai-escal-note'>{html.escape(note)}</div>" if note else ""
    st.markdown(
        f"<div class='cai-escal'>"
        f"<div class='cai-escal-row'>"
        f"<span class='cai-escal-title'>{_isvg(_I_COMPASS, size=12)} למי פונים</span>"
        f"{steps}"
        f"</div>"
        f"{note_html}"
        f"</div>",
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner="טוען את הסעיף...", ttl=3600, max_entries=64)
def _clause_image(source_file: str, page: int, highlight: str):
    """PNG of the cited clause's page, highlighted (backend.render_clause_image,
    cached — the render is deterministic and free). getattr: a stale cached
    cloud backend may predate the function; then None → the dialog shows only
    the full-PDF link."""
    fn = getattr(backend, "render_clause_image", None)
    return fn(source_file, page, highlight) if fn else None


@st.dialog("סעיף המקור", width="large")
def _clause_dialog(primary: dict, page: int | None, full_href: str | None) -> None:
    """Show the cited clause INSIDE the app: the order's page rendered with
    the passage highlighted, so a soldier verifies the source without a lost
    PDF tab and returns to the chat by closing the dialog (state intact).
    The full order stays one tap away for those who want the whole document.

    Rebuilt as the premium dark-olive modal (shares _MODAL_CSS with the three
    side dialogs) so every modal speaks one visual language. The page render is
    embedded as a base64 <img> INSIDE the framed preview card — st.image can't
    live between the card's caption bar and its border, and the seamless frame is
    the whole point of the redesign. Accent uses the role tokens, so it re-tints.
    """

    # classification sub-label: "פ״מ {order} · עמוד {n} · בלמ״ס" (dynamic,
    # unlike the fixed sub on the side dialogs). doc_id is our own id ("35.0402"
    # / "PM-35.0402") — drop the "PM-" prefix so it reads as a plain order number.
    did = (primary.get("doc_id") or "").strip()
    order = did[3:] if did.upper().startswith("PM-") else did
    sub_parts = []
    # civil sources (חוק, החלטת ממשלה) are not a פקודת מטכ"ל — never label
    # them "פ״מ" (the doc_id there is a slug, not an order number). Show
    # "מקור אזרחי · {kind}"; kind comes from the doc's civil_label and
    # defaults to "חוק" (the pre-existing single civil doc).
    if primary.get("civil_source"):
        kind = (primary.get("civil_label") or "חוק").strip() or "חוק"
        sub_parts.append(f"מקור אזרחי · {html.escape(kind)}")
    elif order:
        sub_parts.append(f"פ״מ {html.escape(order)}")
    if page:
        sub_parts.append(f"עמוד {page}")
    sub_parts.append("בלמ״ס")

    doc_svg = (
        "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.7' "
        "stroke-linecap='round' stroke-linejoin='round'><path d='M6 3h8l4 4v14H6z'></path>"
        "<path d='M14 3v4h4'></path><path d='M9 12h6M9 16h6'></path></svg>"
    )
    st.markdown(
        "<div class='cai-mhead'>"
        f"<div class='cai-sc-emblem'>{doc_svg}</div>"
        "<div class='cai-mtitles'>"
        "<div class='cai-mtitle'>סעיף המקור</div>"
        f"<div class='cai-msub'>{' · '.join(sub_parts)}</div>"
        "</div></div>",
        unsafe_allow_html=True,
    )

    title = primary.get("title", "")
    # the caption introduces the page PREVIEW — without one (no page metadata)
    # it read as a promise for content that never appeared (2026-08-03 audit)
    _cap = ("הסעיף הרלוונטי מתוך נוסח הפקודה הרשמי" if page
            else "הסעיף המדויק מופיע בנוסח הפקודה הרשמי — נפתח בכפתור למטה")
    st.markdown(
        f"<div class='cai-sc-ctitle'>{html.escape(title)}</div>"
        f"<div class='cai-sc-ccap'>{_cap}</div>",
        unsafe_allow_html=True,
    )

    # ── framed page preview: the real highlighted render, or a placeholder ──
    img = _clause_image(primary.get("source_file"), page, primary.get("highlight", "")) if page else None
    if img or page:
        if img:
            b64 = base64.b64encode(img).decode()
            body = f"<img src='data:image/png;base64,{b64}' alt='עמוד הפקודה'>"
        else:
            body = (
                "<div class='cai-sc-ph'><svg width='34' height='34' viewBox='0 0 24 24' "
                "fill='none' stroke='currentColor' stroke-width='1.4' stroke-linecap='round' "
                "stroke-linejoin='round'><path d='M6 3h8l4 4v14H6z'></path>"
                "<path d='M14 3v4h4'></path><path d='M8 12h8M8 15h8M8 18h5'></path></svg>"
                "<div>תצוגת עמוד הפקודה</div></div>"
            )
        pg_label = f"עמוד {page} מתוך הפקודה" if page else "עמוד מתוך הפקודה"
        st.markdown(
            "<div class='cai-sc-preview'><div class='cai-sc-pbar'>"
            f"<span class='pg'>{pg_label}</span><span class='tag'>PDF</span></div>"
            f"{body}</div>",
            unsafe_allow_html=True,
        )
    elif not full_href:
        st.markdown(
            "<div class='cai-sc-ccap' style='margin-top:16px'>"
            "לא נמצאה תצוגת סעיף לפקודה זו.</div>",
            unsafe_allow_html=True,
        )

    if full_href:
        ext_svg = (
            "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.9' "
            "stroke-linecap='round' stroke-linejoin='round'><path d='M14 3h7v7'></path>"
            "<path d='M21 3l-9 9'></path>"
            "<path d='M19 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5'></path></svg>"
        )
        st.markdown(
            f"<a class='cai-sc-cta' href='{html.escape(full_href, quote=True)}' "
            f"target='_blank' rel='noopener'>{ext_svg}"
            "<span>פתח את הפקודה המלאה (PDF)</span></a>"
            "<div class='cai-sc-disc'>הכוונה כללית — נוסח הפקודה הרשמי הוא הקובע.</div>",
            unsafe_allow_html=True,
        )


def _question_for(msg_i: int) -> str:
    """The user question that produced the answer at index msg_i."""
    for m in reversed(st.session_state.messages[:msg_i]):
        if m["role"] == "user":
            return m["content"]
    return ""


# ── Conversation ──
for msg_i, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        content = msg["content"]
        if msg["role"] == "assistant" and not msg.get("error"):
            chip, body = _verdict_chip(content)
            if chip:
                st.markdown(chip, unsafe_allow_html=True)
            _render_body(body, chip)
            if msg.get("truncated"):
                st.warning("התשובה נקטעה בגלל אורך. אפשר לשאול על חלק ממוקד יותר לתשובה שלמה.")
        else:
            st.markdown(content)
        if msg["role"] == "assistant" and not msg.get("error"):
            pdf = None
            full_href = None
            page = None
            primary = (msg.get("sources") or [None])[0]
            if primary and primary.get("source_file"):
                url = _pdf_media_url(primary["source_file"], f"pdfmsg_{msg_i}")
                if url:
                    # page of the cited clause (clause_pages.json); None —
                    # unknown clause, pre-deep-link sources, missing mapping.
                    # getattr: a stale cached backend from a previous cloud
                    # build may predate page_for_clause (see last_usage above)
                    _pfc = getattr(backend, "page_for_clause", None)
                    page = _pfc(primary["doc_id"], primary.get("clause")) if _pfc else None
                    pdf = (url, primary["title"], page)
                    # full-order link for the dialog: relative media href
                    # (resolves against the app base local + cloud), + #page
                    # for desktop/Android viewers (iOS ignores it — the
                    # in-app highlighted image is the iOS answer)
                    full_href = url.lstrip("/") + (f"#page={page}" if page else "")
            # the conversation loop is the one path that renders every
            # settled assistant message — a fresh stream is st.rerun()'d
            # into it immediately — so hooking here keeps everything
            # identical for live answers and history replays. Order: strip
            # (answer content) → source button + share pills (chrome).
            _escalation_strip(msg.get("sources"), _question_for(msg_i))
            if primary and primary.get("source_file"):
                if st.button("הצג סעיף מקור", key=f"src_{msg_i}"):
                    _clause_dialog(primary, page, full_href)
            _answer_actions(content, msg.get("sources"), pdf)
            # feedback keyed by a per-message id, NOT by position: widget
            # state lives in session_state by key, and positional keys leak
            # a previous conversation's thumb onto a new answer after clear
            mid = msg.setdefault("id", uuid.uuid4().hex[:8])
            fb = st.feedback("thumbs", key=f"fb_{mid}")
            if fb is not None and msg.get("fb_value") != fb:
                msg["fb_value"] = fb
                metrics.log_feedback(
                    session_id=st.session_state.session_id,
                    device_id=st.session_state.device_id,
                    role=st.session_state.role or "",
                    verdict="up" if fb == 1 else "down",
                    question=_question_for(msg_i),
                    answer=content,
                    sources=msg.get("sources"),
                )
            if msg.get("fb_value") == 0 and not msg.get("fb_comment_sent"):
                fb_col, send_col = st.columns([4, 1])
                fb_comment = fb_col.text_input(
                    "מה היה חסר או שגוי?", key=f"fbc_{mid}",
                    label_visibility="collapsed",
                    placeholder="מה היה חסר או שגוי? (לא חובה)",
                )
                if send_col.button("שלח", key=f"fbs_{mid}") and fb_comment.strip():
                    metrics.log_feedback(
                        session_id=st.session_state.session_id,
                        device_id=st.session_state.device_id,
                        role=st.session_state.role or "",
                        verdict="comment",
                        question=_question_for(msg_i),
                        answer=content,
                        sources=msg.get("sources"),
                        comment=fb_comment.strip(),
                    )
                    msg["fb_comment_sent"] = True
                    st.rerun()

# ── Chat input (always visible, sticky — renders at the viewport bottom
#    regardless of code position). Routed through the SAME queue as the
#    suggested-question taps and reran immediately, so the greeting screen
#    below clears on this run instead of lingering above the first streaming
#    answer (2026-07-21 phone video: title + all suggested cards stayed
#    pinned above the conversation for the whole ~18s of the first answer). ──
if prompt := st.chat_input("שאל על פקודה..."):
    queue_question(prompt)
    st.rerun()

# ── Greeting + suggested questions ── shown only before any conversation AND
# when nothing is queued: the instant a question is asked (tap or type) the
# welcome block must vanish, or it sits stale above the answer being streamed.
if not st.session_state.messages and not st.session_state.pending_question:
    _q = "<span class='cai-greet-q'>במה אפשר לעזור?</span>"
    _greet = (f"<span class='cai-greet-hi'>היי {html.escape(_dn)},</span>{_q}"
              if _dn else _q)
    st.markdown(
        f"<div class='cai-greet'>{_greet}</div>"
        f"<div class='cai-greet-sub'>שאלות נפוצות מפקודות המטכ\"ל במערכת</div>",
        unsafe_allow_html=True,
    )
    for i, q in enumerate(suggested_questions):
        if st.button(q, key=f"sug_{i}", use_container_width=True):
            queue_question(q)
            st.rerun()

# ── Process pending question ──
if st.session_state.pending_question:
    q = st.session_state.pending_question
    st.session_state.pending_question = None
    handle_question(q)
    st.rerun()

# (the old "auto-collapse the sidebar after role pick" JS is gone — the
# app-owned drawer above renders closed by default and never auto-opens)