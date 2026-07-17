import base64
import html
import inspect
from pathlib import Path
import itertools
import json
import random
import re
import time
import traceback
import uuid
import streamlit as st
import streamlit.components.v1 as components
from anthropic import APIConnectionError, APITimeoutError, BadRequestError

import metrics
import escalation_paths
from escalation_paths import path_for
from boot_shell import patch_index_html
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
    from verdict import verdict_clauses as _verdict_clauses
except Exception:
    def _verdict_clauses(_content):
        return []
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

# PDF bytes are re-read on every rerun to keep their media-manager entries
# alive (see _pdf_media_url); cache the disk reads — ~40 multi-hundred-KB
# files per rerun otherwise. ttl bounds staleness: on Streamlit Cloud the
# process outlives git pulls, and a cache keyed only by filename would serve
# an order's OLD bytes forever after its PDF is updated in place.
_pdf_bytes_cached = st.cache_data(show_spinner=False, ttl=3600)(get_pdf_bytes)

st.set_page_config(
    page_title="CommandAI",
    page_icon="🛡️",
    layout="centered",
    initial_sidebar_state="collapsed",
)

_patch_boot_shell()

# ── Session state (initialized before theming, since accent depends on role) ──
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None
if "role" not in st.session_state:
    st.session_state.role = None
if "conversation_history" not in st.session_state:
    st.session_state.conversation_history = []
if "session_id" not in st.session_state:
    # anonymous per-tab id — keys the daily usage quota and the metrics log
    st.session_state.session_id = metrics.new_session_id()

# ── Profile & settings (session-only; no DB). These MIRROR the settings-dialog
# widgets: Streamlit drops a widget's session key on any run where the widget
# isn't rendered (dialog closed), so a stable mirror is what handle_question
# reads — exactly the pattern profile_saved uses for the status pills. Service
# type/track are folded into the answer ONLY after an explicit "save"
# (profile_customized), so an untouched user's API turn stays byte-identical to
# the pre-profile format (backend._compose_user_content). ──
st.session_state.setdefault("profile_name", "")
st.session_state.setdefault("service_type", "סדיר")
st.session_state.setdefault("service_track", "")
st.session_state.setdefault("profile_customized", False)
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
splash_active = (not _is_admin
                 and st.session_state.role is None
                 and not st.session_state.get("splash_shown"))
if not _is_admin:
    st.session_state.splash_shown = True
if splash_active:
    st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Suez+One&display=swap');
@keyframes bootEnterUp { from { opacity:0; transform:translateY(18px); } to { opacity:1; transform:none; } }
@keyframes bootEnterScale { from { opacity:0; transform:scale(.6); } to { opacity:1; transform:none; } }
/* the parked curtain must END invisible: it stays in the DOM above the
   viewport, and iOS Safari (no theme-color meta yet) SAMPLES it when tinting
   its chrome — an olive ghost kept the bars olive after the lift */
@keyframes bootCurtainUp { 0% { transform:translateY(0); } 99% { opacity:1; } 100% { transform:translateY(-101%); opacity:0; visibility:hidden; } }
.cai-splash {
    position: fixed; inset: 0; background: #99A26B; z-index: 999990;
    display: flex; flex-direction: column; align-items: center; justify-content: flex-start; gap: 18px;
    /* top-anchor the logo where the entry screen lands it (~26% down) so the
       curtain lift reveals the same layout instead of the logo jumping up
       from dead-center. --cai-sat pushes it clear of the iOS notch. */
    padding-top: calc(var(--cai-sat, 0px) + 14vh);
    animation: bootCurtainUp .65s cubic-bezier(.7,0,.3,1) both; animation-delay: 30s;
    pointer-events: none;
}
/* NO entrance animation on the chevron: the OS launch image already shows
   it at this exact spot (see _startup_png) — a scale-in here reads as the
   logo "popping" during the image→splash handoff; only the text enters */
.cai-splash-chev { display:flex; flex-direction:column; align-items:center; }
.cai-splash-chev span { display:block; width:26px; height:26px;
    border-top:6px solid #171A12; border-left:6px solid #171A12; transform:rotate(45deg); }
.cai-splash-chev span + span { border-color: rgba(23,26,18,.45); margin-top: -9px; }
.cai-splash-title { font: 400 34px 'Suez One', serif; color: #171A12;
    animation: bootEnterUp .6s cubic-bezier(.2,.7,.2,1) both; animation-delay: .3s; }
.cai-splash-sub { font: 600 11px ui-monospace, Menlo, monospace; letter-spacing: 3px; color: rgba(23,26,18,.6);
    animation: bootEnterUp .6s cubic-bezier(.2,.7,.2,1) both; animation-delay: .45s; }
</style>
<div class='cai-splash'>
<div class='cai-splash-chev'><span></span><span></span></div>
<div class='cai-splash-title'>CommandAI</div>
<div class='cai-splash-sub'>מערכת פקודות · בלמ"ס</div>
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
                if (g >= 400) h = Math.min(h, g); // ghost-viewport clamp (see above)
                if (h < 400) return;
                aroot.style.setProperty("--cai-vvh", Math.round(h) + "px");
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
                if (off !== Math.round(cur)) aroot.style.setProperty("--cai-vvoff", off + "px");
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
        if (!window.__caiVVH) {
            window.__caiVVH = true;
            [0, 300, 700, 1300, 2200, 3500, 5200, 7500].forEach(function (ms) { setTimeout(setH, ms); });
            setTimeout(nudge, 350);
            setTimeout(nudge, 1500);
            var iv = setInterval(setH, 600);
            setTimeout(function () {
                clearInterval(iv);
                // permanent slow resync: a mid-session stuck state (--cai-vvh
                // pinned to a keyboard pane after focus tracking missed the
                // close) must heal even when no viewport event ever fires again
                setInterval(setH, 1500);
            }, 30000);
            window.addEventListener("orientationchange", function () { setTimeout(setH, 400); });
            window.addEventListener("resize", setH);
            window.addEventListener("pageshow", setH);
            // the ✓-dismiss blurs the composer — remeasure right after
            window.addEventListener("focusout", function () {
                [80, 350, 800].forEach(function (ms) { setTimeout(setH, ms); });
            }, true);
            if (window.visualViewport) window.visualViewport.addEventListener("resize", setH);
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
            document.addEventListener("click", function (e) {
                try {
                    if (e.target && e.target.closest && e.target.closest(
                        ".st-key-role_soldier, .st-key-role_commander, .st-key-role_reserve"))
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
            [3000, 6500, 10500, 15000].forEach(function (ms, i) {
                setTimeout(function () { dbg("d6." + (i + 1)); }, ms);
            });
            setTimeout(function () {
                try { var b0 = document.getElementById("cai-dbg"); if (b0) b0.remove(); } catch (e) {}
            }, 21000);
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

_startup_ingest()


def _secret(name: str, default: str = "") -> str:
    """st.secrets.get that tolerates a missing secrets.toml entirely."""
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
    c1, c2, c3 = st.columns(3)
    c1.metric("שאלות היום", f"{d['global_count']} / {d['global_limit']}")
    c2.metric("משתמשים היום", d["sessions_today"])
    recent_cost = sum(q["cost_usd"] for q in d["questions"])
    c3.metric("עלות מצטברת (מאז אתחול)", f"${recent_cost:.2f}")

    sheets_label = {
        "ok": "✅ מחובר — כל שאלה ומשוב נשמרים בגיליון",
        "error": f"⚠️ שגיאת חיבור: {d['sheets_error']}",
        "not_configured": "❌ לא מוגדר — הנתונים נשמרים רק בזיכרון עד האתחול הבא",
    }[d["sheets_status"]]
    st.caption(f"Google Sheets: {sheets_label}")
    if d["sheet_url"]:
        st.markdown(f"🔗 [פתח את הגיליון המלא (כל ההיסטוריה)]({d['sheet_url']})")
    st.caption(f"מכסות: {d['user_limit']} שאלות ליום למשתמש, {d['global_limit']} ליום לכולם. "
               "הטבלאות למטה מציגות את הפעילות מאז האתחול האחרון של השרת; "
               "ההיסטוריה המלאה נשמרת בגיליון.")

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
# Dark-olive theme; role accents: soldier olive, commander tan, reserve blue.
ROLE_META = {
    "soldier": {
        # 9a home redesign palette (design_handoff_commandai_home): olive
        # lightened #99A26B → #A3AE6E, tints rebased on rgb(163,174,110)
        "label": "חייל", "accent": "#A3AE6E", "accent_hover": "#B2BD7E",
        "soft": "rgba(163,174,110,.14)", "border": "rgba(163,174,110,.35)",
        "bright": "#C4CE92",  # lightened accent for the modal hero number
    },
    "commander": {
        "label": "מפקד", "accent": "#B29A72", "accent_hover": "#C4AC84",
        "soft": "rgba(178,154,114,.14)", "border": "rgba(178,154,114,.4)",
        "bright": "#D6C193",
    },
    "reserve": {
        "label": "מילואים", "accent": "#8A9BC0", "accent_hover": "#9DAECE",
        "soft": "rgba(138,155,192,.12)", "border": "rgba(138,155,192,.38)",
        "bright": "#B4C3E0",
    },
}
role_meta = ROLE_META.get(st.session_state.role, ROLE_META["soldier"])
role_label = role_meta["label"]
ACCENT = role_meta["accent"]
ACCENT_HOVER = role_meta["accent_hover"]
ACCENT_SOFT = role_meta["soft"]
ACCENT_BORDER = role_meta["border"]
ACCENT_BRIGHT = role_meta["bright"]
# accent as an "r,g,b" triplet so drawer/settings tints can be role-aware via
# rgba(var(--accent-rgb), <alpha>) instead of a hardcoded olive.
ACCENT_RGB = ",".join(str(int(ACCENT.lstrip("#")[i:i + 2], 16)) for i in (0, 2, 4))

# chat screen needs room under the fixed header band; entry has no header.
# --cai-sat is the iOS status-bar inset (measured on the shell doc, pushed
# into this frame by the PWA script) — the band grew by it, so clear it too.
MAIN_TOP_PADDING = "12px" if st.session_state.role is None else "calc(72px + var(--cai-sat, 0px))"

# entry elements stagger in around the boot splash curtain lift (delay 1.15s
# + .65s travel). 1.35s meant nothing STARTED fading until the lift was 30%
# done, leaving ~0.5s of pure dark-blank after the reveal (measured on the
# 2026-07-17 iPhone video, t=7.44-7.92, confirmed as a perceived stall).
# 0.9s starts the fades under the still-opaque curtain: the header is landing
# right as the lift finishes (the top edge is revealed LAST) and the role
# cards' rise is the only choreography left on screen — same look, -0.45s.
EHOLD = "0.9s" if splash_active else "0s"

# CSS-drawn role icons (chevron / bars / diamond) as inline SVG tiles
_ICON_SOLDIER = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='18' height='18'%3E%3Cpath d='M4 12 L9 6 L14 12' fill='none' stroke='%2399A26B' stroke-width='3'/%3E%3C/svg%3E\")"
_ICON_COMMANDER = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='18' height='18'%3E%3Crect x='1' y='4.5' width='16' height='3.5' rx='1' fill='%23B29A72'/%3E%3Crect x='1' y='11' width='16' height='3.5' rx='1' fill='%23B29A72'/%3E%3C/svg%3E\")"
_ICON_RESERVE = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='20' height='20'%3E%3Crect x='5.5' y='5.5' width='9' height='9' fill='none' stroke='%238A9BC0' stroke-width='2.5' transform='rotate(45 10 10)'/%3E%3C/svg%3E\")"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;600;700;800&family=Suez+One&display=swap');

:root {{
    --bg: #14170E;
    --surface: #21261A;
    --surface-hover: #2A3120;
    --text: #EFF0E8;
    --text-sec: rgba(239,240,232,.6);
    --text-dim: rgba(239,240,232,.55);
    --text-faint: rgba(239,240,232,.4);
    --border: rgba(239,240,232,.12);
    --border-strong: rgba(239,240,232,.16);
    --accent: {ACCENT};
    --accent-hover: {ACCENT_HOVER};
    --accent-soft: {ACCENT_SOFT};
    --accent-border: {ACCENT_BORDER};
    --accent-bright: {ACCENT_BRIGHT};
    --accent-rgb: {ACCENT_RGB};
    --ehold: {EHOLD};
}}

@keyframes enterUp {{ from {{ opacity:0; transform:translateY(18px); }} to {{ opacity:1; transform:none; }} }}
@keyframes enterScale {{ from {{ opacity:0; transform:scale(.6); }} to {{ opacity:1; transform:none; }} }}
/* mirror of bootCurtainUp: the re-armed curtain must also PARK invisible,
   or Safari keeps sampling the olive ghost for its chrome tint */
@keyframes curtainUp {{ 0% {{ transform:translateY(0); }} 99% {{ opacity:1; }} 100% {{ transform:translateY(-101%); opacity:0; visibility:hidden; }} }}

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
   class hashes vary by build, so match every known naming scheme) */
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
   same-origin ancestor document INCLUDING this one; on the answer action
   row (transparent-bodied pills iframe) that painted an opaque dark slab
   over the page gradient — the "black mark" behind העתק/וואטסאפ. Component
   iframes in THIS document must stay transparent (the injected rule keeps
   its real job: darkening the cloud-shell documents above us). */
[data-testid="stElementContainer"] iframe,
iframe[data-testid="stIFrame"] {{ background: transparent !important; }}
[data-testid="stAppViewContainer"], [data-testid="stBottom"], [data-testid="stSidebar"] {{ direction: rtl; }}

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
    width: 42px; z-index: 110;
}}
/* 9a: 42px CIRCLE, olive-tinted, three 15×2 olive bars (gap 4) */
.st-key-drawer_open_btn button {{
    width: 42px !important; height: 42px !important; min-height: 42px !important;
    background-color: rgba(163,174,110,.14) !important;
    border: 1px solid rgba(163,174,110,.3) !important; border-radius: 50% !important;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='15' height='14'%3E%3Crect width='15' height='2' y='0' rx='1' fill='%23A3AE6E'/%3E%3Crect width='15' height='2' y='6' rx='1' fill='%23A3AE6E'/%3E%3Crect width='15' height='2' y='12' rx='1' fill='%23A3AE6E'/%3E%3C/svg%3E") !important;
    background-repeat: no-repeat !important; background-position: center !important;
}}
.st-key-drawer_open_btn button p {{ display: none; }}
@media (hover: hover) {{
    .st-key-drawer_open_btn button:hover {{ background-color: rgba(163,174,110,.24) !important; }}
}}
.st-key-drawer_backdrop {{ position: fixed; inset: 0; z-index: 125; }}
.st-key-drawer_backdrop button {{
    width: 100% !important; height: 100% !important; min-height: 100% !important;
    background: rgba(9, 11, 7, .62) !important;
    border: none !important; border-radius: 0 !important; box-shadow: none !important;
}}
.st-key-drawer_backdrop button p {{ display: none; }}
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
/* close button — small circle in the hamburger's olive-tint style */
.st-key-drawer_close button {{
    width: 36px !important; height: 36px !important; min-height: 36px !important;
    border-radius: 50% !important;
    background-color: rgba(163,174,110,.14) !important;
    border: 1px solid rgba(163,174,110,.3) !important;
    color: var(--accent) !important;
}}
.st-key-drawer_close button p {{ color: var(--accent) !important; }}

/* ── Main container — mobile-first column, max 430px ── */
[data-testid="stMainBlockContainer"], .main .block-container {{
    max-width: 560px;
    padding: {MAIN_TOP_PADDING} 22px 7rem 22px !important;
    margin: 0 auto;
}}

/* ── Splash re-arm: the boot curtain (first delta, top of script) has been
   covering the whole load; this rule landing with the entry screen swaps
   the animation NAME, which restarts the clock — hold 1.15s more, then
   lift. Element/child styles live in the boot block. ── */
.cai-splash {{
    animation: curtainUp .65s cubic-bezier(.7,0,.3,1) both; animation-delay: 1.15s;
}}

/* ── Entry screen header (staggers in after the splash lifts) ── */
.cai-entry {{ text-align: center; padding-top: 7vh; }}
.cai-entry > div {{ animation: enterUp .6s cubic-bezier(.2,.7,.2,1) both; }}
.cai-entry-classif {{ font: 600 11px ui-monospace, Menlo, monospace; letter-spacing: 3px; color: #99A26B;
    animation-delay: calc(var(--ehold) + .2s) !important; }}
.cai-entry-chev {{ display:flex; flex-direction:column; align-items:center; margin-top: 26px;
    animation-delay: calc(var(--ehold) + .3s) !important; }}
.cai-entry-chev span {{ display:block; width:22px; height:22px;
    border-top:5px solid #99A26B; border-left:5px solid #99A26B; transform:rotate(45deg); }}
.cai-entry-chev span + span {{ border-color: rgba(153,162,107,.45); margin-top:-8px; }}
.cai-entry-title {{ font: 400 40px 'Suez One', serif; color: var(--text); margin-top: 18px;
    animation-delay: calc(var(--ehold) + .38s) !important; }}
.cai-entry-sub {{ font: 400 15px Heebo, sans-serif; color: var(--text-sec); margin-top: 6px;
    animation-delay: calc(var(--ehold) + .46s) !important; }}
.cai-entry-divider {{ width: 44px; height: 2px; background: #99A26B; margin: 26px auto 0;
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
div[data-testid="stButton"] > button:active {{ transform: scale(.98); }}

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
.st-key-role_soldier button::before {{
    background-color: rgba(153,162,107,.14); border: 1px solid rgba(153,162,107,.35);
    background-image: {_ICON_SOLDIER};
}}
.st-key-role_commander button::before {{
    background-color: rgba(178,154,114,.14); border: 1px solid rgba(178,154,114,.4);
    background-image: {_ICON_COMMANDER};
}}
.st-key-role_reserve button::before {{
    background-color: rgba(138,155,192,.12); border: 1px solid rgba(138,155,192,.38);
    background-image: {_ICON_RESERVE};
}}
@media (hover: hover) {{
    .st-key-role_soldier button:hover {{ border-color: rgba(153,162,107,.5) !important; }}
    .st-key-role_commander button:hover {{ border-color: rgba(178,154,114,.5) !important; }}
    .st-key-role_reserve button:hover {{ border-color: rgba(138,155,192,.5) !important; }}
}}
.st-key-role_soldier button p, .st-key-role_commander button p, .st-key-role_reserve button p {{
    font-size: 12.5px !important; color: var(--text-dim); text-align: right; margin: 0; line-height: 1.35;
}}
.st-key-role_soldier button p strong, .st-key-role_commander button p strong, .st-key-role_reserve button p strong {{
    display: block; font-size: 16px; font-weight: 600; color: var(--text); margin-bottom: 2px;
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
    display: flex; align-items: center; gap: 12px;
    padding: var(--cai-sat, 0px) 72px 0 18px;
    /* 9a: NO divider line beneath the header */
    /* no entrance animation: a transform on a fixed element re-anchors it
       and Streamlit can freeze the animation at its from-state (top: 18px) */
}}
/* flows in the header row right after the hamburger (right-of-center), per
   the design — space-between flow puts the wordmark next to the menu button,
   cx≈258 on a 390 viewport. NOT screen-centered. */
.cai-wordmark {{ font: 400 20px 'Suez One', serif; color: var(--text); }}
/* 9a: two-tone wordmark — "Command" light, "AI" olive */
.cai-wordmark .cai-wm-ai {{ color: var(--accent); }}
.cai-pill {{
    margin-inline-start: auto;
    font: 600 12px Heebo, sans-serif; color: var(--accent);
    background: var(--accent-soft); border: 1px solid var(--accent-border);
    border-radius: 99px; padding: 6px 13px; white-space: nowrap;
}}

/* ── Chat home greeting — 9a: title 30px + subtitle 13.5px, both CENTERED,
   7px apart (the previous right-aligned pass followed the older handoff;
   the 9a redesign centers the greeting block) ── */
.cai-greet {{ font: 400 30px 'Suez One', serif; color: var(--text); margin: 0 0 7px;
    text-align: center;
    animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .08s; }}
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
    border-radius: 99px !important;
    padding: 5px 6px 5px 5px !important; /* 9a: 5/6/5/5, text carries its own 14px inset */
    align-items: center !important;
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
}}
[data-testid="stChatInput"] textarea::placeholder {{ color: rgba(239,240,232,.4) !important; }}
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
    font: 400 10.5px Heebo, sans-serif; color: var(--text-faint);
}}

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
    font-size: 15px !important;
    line-height: 1.65 !important;
    text-align: right;
}}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h1,
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h2,
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h3,
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] h4 {{
    font-family: Heebo, sans-serif !important;
    font-size: 16px !important;
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

/* ── Profile pills (התאמה אישית) — personal statuses that change
   entitlements. Same outline-pill chrome as the answer action row;
   selected = accent, so the active statuses read at a glance. ── */
.cai-profile-label {{ font: 400 12.5px Heebo, sans-serif; color: var(--text-dim); margin: 2px 0 4px; }}
.st-key-profile_statuses [data-testid="stPills"] {{ direction: rtl; gap: 6px; }}
.st-key-profile_statuses button {{
    background: rgba(239,240,232,.045) !important;
    border: 1px solid rgba(239,240,232,.22) !important;
    border-radius: 99px !important;
    color: rgba(239,240,232,.75) !important;
    min-height: 0 !important;
    padding: 3px 12px !important;
}}
.st-key-profile_statuses button p {{ font: 500 12px Heebo, sans-serif !important; }}
.st-key-profile_statuses button:hover {{ border-color: var(--accent) !important; color: var(--accent) !important; }}
.st-key-profile_statuses button[data-testid="stBaseButton-pillsActive"] {{
    background: var(--accent-soft) !important;
    border-color: var(--accent) !important;
    color: var(--accent) !important;
}}
.st-key-profile_statuses button[data-testid="stBaseButton-pillsActive"] p {{ color: var(--accent) !important; }}

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
    display: block;
    border-right: 2px solid var(--accent-border);
    color: rgba(239,240,232,.65) !important;
    font: 400 13px Heebo, sans-serif;
    text-align: right;
    text-decoration: none !important;
    padding: 7px 10px;
    margin: 0 8px 2px 0;
    direction: rtl;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    transition: color .15s ease, border-color .15s ease;
}}
a.cai-order-link:hover {{
    color: var(--text) !important;
    border-right-color: var(--accent);
}}
/* freshness badge — the order's own version date, so "how current is
   this?" is answered in the list itself */
.cai-order-date {{
    font: 400 10.5px Heebo, sans-serif;
    color: rgba(239,240,232,.38);
    margin-right: 6px;
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
    color: rgba(239,240,232,.4) !important;
}}

/* ── Caption / small text ── */
.stCaption, small {{ color: var(--text-faint) !important; font-size: 0.8rem !important; }}

/* ── Spinner ── */
.stSpinner > div {{ border-top-color: var(--accent) !important; }}

/* ── Accessibility: honor prefers-reduced-motion — animations jump straight
   to their end state (splash still ends offscreen thanks to fill:both) ── */
@media (prefers-reduced-motion: reduce) {{
    * {{ animation-duration: .01ms !important; animation-delay: 0s !important; }}
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


# ── PWA: home-screen metadata (icon, standalone, manifest) ──
@st.cache_data(show_spinner=False)
def _icon_bytes(name: str) -> bytes | None:
    # branding/, not static/ — static/* is gitignored (runtime PDF mirror),
    # and an icon that never reaches the cloud breaks A2HS silently
    try:
        return (Path(__file__).parent / "branding" / "icons" / name).read_bytes()
    except Exception:
        return None


# Streamlit Community Cloud mounts the repo at /mount/src — absent locally.
_ON_CLOUD = Path("/mount/src").exists()

# iOS launch screens: (device px width, height, device-pixel-ratio) per
# iPhone class. iOS shows the matching image from the moment the icon is
# tapped until the page's first paint — on a weak connection that is most
# of the wait, and without these it is a black void (see 2026-07-13 video:
# ~15s of black before anything web-controlled can run).
_STARTUP_SIZES = [
    (640, 1136, 2), (750, 1334, 2), (828, 1792, 2),
    (1125, 2436, 3), (1170, 2532, 3), (1179, 2556, 3), (1206, 2622, 3),
    (1242, 2688, 3), (1284, 2778, 3), (1290, 2796, 3), (1320, 2868, 3),
]


# status-bar heights (pt) per launch-image class — the ONLY per-device input
# the PNG needs to land its chevron where the splash draws its own (the
# splash pads by env(safe-area-inset-top), which iOS reports as these).
# Keyed by the pixel triple because 828×1792@2 (XR, 48pt) and 1242×2688@3
# (XS Max, 44pt) share a pt-size with different bars.
_STARTUP_SAT = {
    (640, 1136, 2): 20, (750, 1334, 2): 20, (828, 1792, 2): 48,
    (1125, 2436, 3): 44, (1170, 2532, 3): 47, (1179, 2556, 3): 59,
    (1206, 2622, 3): 62, (1242, 2688, 3): 44, (1284, 2778, 3): 47,
    (1290, 2796, 3): 59, (1320, 2868, 3): 62,
}


@st.cache_data(show_spinner=False)
def _startup_png(w: int, h: int, dpr: int) -> bytes:
    """Olive launch screen with the double-chevron mark at the SPLASH's
    chevron position, so the OS launch image morphs into the splash without
    a jump (the user filmed the old centered chevron leaping to the splash's
    top-aligned one). The splash chevron's first apex sits at
    env(safe-area-inset-top) + 14vh − 6.6px: the .cai-splash padding is
    sat+14vh, and a 26px box + 6px border rotated 45° overhangs its layout
    top by (32·√2−32)/2 ≈ 6.6px. Verified against the launch video: apex at
    ~176pt on a 393×852 device = 59 + 119.3 − 6.6."""
    import io
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), "#99A26B")
    draw = ImageDraw.Draw(img, "RGBA")
    dd = 32 * 0.7071 * dpr          # apex-to-arm-tip reach of the 32px box
    tv = 6 * 1.4142 * dpr           # vertical band thickness of a 6px stroke
    cx = w / 2
    sat = _STARTUP_SAT.get((w, h, dpr), 47)
    apex = (sat + 0.14 * (h / dpr) - 6.6) * dpr
    for i, color in enumerate([(23, 26, 18, 255), (23, 26, 18, 115)]):
        ay = apex + i * 23 * dpr
        draw.polygon(
            [(cx - dd, ay + dd), (cx, ay), (cx + dd, ay + dd),
             (cx + dd, ay + dd + tv), (cx, ay + tv), (cx - dd, ay + dd + tv)],
            fill=color,
        )
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _pwa_assets() -> dict | None:
    """Register the PWA assets (icons + web-app manifest) with the media
    file manager and return their URLs. Called EVERY rerun — entries whose
    coord isn't re-registered are purged at the rerun's end (same rule as
    the PDFs). The manifest's icon srcs are the icons' BASENAMES: manifest
    and icons are served from the same /media/ directory, so relative
    resolution works identically locally and behind the cloud shell (whose
    app base path differs). Media ids are content hashes, so the URLs — and
    therefore the manifest bytes — are stable across reruns.
    """
    try:
        from streamlit.runtime import get_instance
        mgr = get_instance().media_file_mgr
        urls = {}
        for size in (180, 192, 512):
            data = _icon_bytes(f"icon-{size}.png")
            if not data:
                return None
            urls[size] = mgr.add(data, "image/png", f"pwa_icon_{size}")
        manifest = {
            "name": "CommandAI — עוזר הפקודות של צה\"ל",
            "short_name": "CommandAI",
            "lang": "he",
            "dir": "rtl",
            # On the cloud the platform serves the app document itself at
            # /~/+/ (the React shell embeds it from there, and it answers
            # 200 with no auth-redirect hop even cookieless). Launching the
            # PWA straight at it skips the shell entirely: no white shell
            # page, no shell JS bundle, no viewer badges — the boot goes
            # launch-image → olive loading theme → splash. Locally the app
            # really is served at /.
            "start_url": "/~/+/" if _ON_CLOUD else "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#99A26B",  # the boot-splash olive
            "theme_color": "#14170E",
            "icons": [
                {"src": urls[192].rsplit("/", 1)[-1], "sizes": "192x192",
                 "type": "image/png", "purpose": "any maskable"},
                {"src": urls[512].rsplit("/", 1)[-1], "sizes": "512x512",
                 "type": "image/png", "purpose": "any maskable"},
            ],
        }
        data = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        urls["manifest"] = mgr.add(data, "application/json", "pwa_manifest")
        urls["startup"] = [
            (w, h, r, mgr.add(_startup_png(w, h, r), "image/png",
                              f"pwa_launch_{w}x{h}"))
            for (w, h, r) in _STARTUP_SIZES
        ]
        return urls
    except Exception:
        return None


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
                tc.setAttribute("content", "#14170E");
                if (doc.getElementById("cai-pwa-manifest")) return;
                var loc = window.parent.location;
                var dir = loc.pathname.endsWith("/") ? loc.pathname : loc.pathname + "/";
                var base = loc.origin + dir;
                var abs = function (u) {{ return base + String(u).replace(/^\\//, ""); }};
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
                // black-translucent needs the layout viewport to cover the
                // safe area, else env(safe-area-inset-top) stays 0 even here on
                // the top doc. Extend the existing viewport meta, don't clobber.
                var vp = doc.querySelector('meta[name="viewport"]');
                if (vp) {{
                    var vc = vp.getAttribute("content") || "";
                    if (!/viewport-fit/.test(vc))
                        vp.setAttribute("content", vc + ", viewport-fit=cover");
                }}
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

# ── Entry / role gate ──
if st.session_state.role is None:
    st.markdown(
        "<div class='cai-entry'>"
        "<div class='cai-entry-classif'>מערכת פקודות · בלמ\"ס</div>"
        "<div class='cai-entry-chev'><span></span><span></span></div>"
        "<div class='cai-entry-title'>CommandAI</div>"
        "<div class='cai-entry-sub'>העוזר החכם לפקודות מטכ\"ל</div>"
        "<div class='cai-entry-divider'></div>"
        "<div class='cai-entry-choose'>בחר את סוג הכניסה שלך</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    if st.button("**כניסת חיילים**  \nחובה / סדיר", key="role_soldier", use_container_width=True):
        st.session_state.role = "soldier"
        st.rerun()
    if st.button("**כניסת מפקדים**  \nקבע", key="role_commander", use_container_width=True):
        st.session_state.role = "commander"
        st.rerun()
    if st.button("**כניסת מילואים**  \nמערך המילואים", key="role_reserve", use_container_width=True):
        st.session_state.role = "reserve"
        st.rerun()

    st.markdown("<div class='cai-entry-footer'>בלמ\"ס · לשימוש פנימי בלבד</div>", unsafe_allow_html=True)
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
    "user": "🕐 **הגעת למכסת השאלות היומית שלך.**\n\n"
            "המכסה מתאפסת מחר. בינתיים אפשר להמשיך לעיין בפקודות המלאות "
            "ובחיפוש שבתפריט הצד — הם ללא הגבלה.",
    "global": "🕐 **המכסה היומית של המערכת נוצלה במלואה.**\n\n"
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


@st.dialog("📄 מחולל מכתבים", width="large")
def _letters_dialog():
    """Order-grounded formal-letter drafts (בקשת חופשה, ערר, קבילה...).

    One generation burns one daily-quota unit — the same reserve/refund
    contract as a chat question, so this flow cannot sidestep the global
    budget. The draft lands in an editable textarea; the download button
    exports whatever the user edited, not the raw model text.
    """
    st.markdown(_MODAL_CSS, unsafe_allow_html=True)
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
                        role=st.session_state.role or "",
                        question=f"[מכתב] {LETTER_TYPES[kind]['title']}",
                        answer=draft["text"],
                        sources=draft.get("sources"),
                        usage=draft.get("usage"),
                        latency_s=time.time() - t0,
                    )
            except (APIConnectionError, APITimeoutError):
                metrics.refund(st.session_state.session_id)
                st.error("⚠️ אין כרגע חיבור לשירות. בדוק את החיבור ונסה שוב בעוד רגע.")
            except BadRequestError as e:
                metrics.refund(st.session_state.session_id)
                # same monthly-spend-limit 400 as in handle_question
                st.error("⏸️ המערכת בהשהיה זמנית עקב מגבלת שימוש — נסה שוב מחר."
                         if "usage limits" in str(e)
                         else "⚠️ אירעה שגיאה זמנית בניסוח. נסה לשלוח שוב.")
            except Exception as e:
                safe_print(f"[letters] draft failed: {e!r}")
                metrics.refund(st.session_state.session_id)
                st.error("⚠️ אירעה שגיאה זמנית בניסוח. נסה לשלוח שוב.")
    # standing note under the button (matches the design mock): sets the
    # expectation that the output is an order-grounded draft to review
    st.markdown(
        "<div style='font:400 11.5px Heebo,sans-serif;color:rgba(236,237,230,.42);"
        "direction:rtl;text-align:right;margin:10px 2px 0;line-height:1.55'>"
        "הטיוטה נוסחה לפי לשון הפקודה — יש לעבור עליה לפני הגשה.</div>",
        unsafe_allow_html=True,
    )
    draft = st.session_state.get("letter_draft")
    # a draft from another letter type stays hidden instead of masquerading
    # as the currently selected one
    if draft and draft.get("kind") == kind:
        if draft.get("truncated"):
            st.warning("✂️ הטיוטה נקטעה באמצע בגלל אורך — קצר את הפרטים ונסח שוב, או השלם את הסיום ידנית.")
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
# authority, entitlements). Injected inside each dialog rather than into the
# global f-string block so each feature stays self-contained and we avoid that
# block's {{ }} escaping. :root tokens (--accent / --accent-bright / --surface /
# --text*) are global, so the whole modal re-tints per role (חייל / מפקד /
# מילואים) automatically. Rebuilt from design_handoff_entitlements_calculator:
# dark-olive surface, chevron-emblem header, segmented control, styled fields
# and an accent-railed result card — replacing the flat olive-splash look.
_MODAL_CSS = """
<style>
/* ---- Modal surface: Streamlit paints the VISIBLE card ([role="dialog"], the
   inner box) with the olive theme.backgroundColor (#99A26B) — the > div behind
   it is only a full-viewport positioning layer. Force the dark gradient onto the
   card itself, or the whole modal reads olive/"cheap" no matter what's inside. ---- */
/* Backdrop: Streamlit's default overlay is a LIGHT cream tint that WASHES the
   olive app behind the modal; the design wants the surroundings dimmed dark.
   Darken the full-screen stDialog layer (the card below keeps its own bg). */
div[data-testid="stDialog"] { background: rgba(9,11,7,.66) !important; }
div[data-testid="stDialog"] > div { direction: rtl; background: transparent !important; }
div[data-testid="stDialog"] [role="dialog"] {
    direction: rtl;
    background: linear-gradient(180deg,#1E2216 0%,#181B12 100%) !important;
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
    color: rgba(236,237,230,.45) !important; letter-spacing: .02em;
}

/* ---- Select fields -> dark pill with olive chevron ---- */
div[data-testid="stDialog"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
    background: #22271A !important; border: 1px solid rgba(236,237,230,.13) !important;
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
    background: #22271A !important; border: 1px solid rgba(236,237,230,.13) !important;
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
    background: #22271A !important; border-radius: 12px !important;
    border-color: rgba(236,237,230,.13) !important; color: var(--text) !important;
}

/* ---- Radio -> segmented control ("מה לחשב?") ---- */
div[data-testid="stDialog"] [data-testid="stRadio"] div[role="radiogroup"] {
    display: flex !important; flex-direction: row !important; gap: 4px;
    background: rgba(0,0,0,.25); border: 1px solid rgba(236,237,230,.08);
    border-radius: 13px; padding: 4px;
}
div[data-testid="stDialog"] [data-testid="stRadio"] div[role="radiogroup"] label {
    flex: 1; display: flex; align-items: center; justify-content: center;
    padding: 10px; margin: 0 !important; border-radius: 10px; cursor: pointer;
    transition: background .15s ease;
}
div[data-testid="stDialog"] [data-testid="stRadio"] div[role="radiogroup"] label > div:first-child {
    display: none !important;  /* hide the radio dot */
}
div[data-testid="stDialog"] [data-testid="stRadio"] div[role="radiogroup"] label p {
    font: 600 13.5px Heebo, sans-serif !important; color: rgba(236,237,230,.6) !important;
    margin: 0 !important;
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
    background: #22271A !important;
    border: 1px solid var(--accent-border) !important; box-shadow: none !important;
}
/* accent-hover, not accent-bright: #C4CE92 reads as plain white on phone
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
   transparent so only the dark <ul> shows. Options are already light-on-transparent. */
div[data-baseweb="popover"],
div[data-baseweb="popover"] > div,
div[data-baseweb="popover"] > div > div { background: transparent !important; }
div[data-baseweb="popover"] ul {
    background: #1E2216 !important; border: 1px solid rgba(236,237,230,.13) !important;
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

/* ---- Hide "Press Enter to apply" — it overlaps the typed RTL text and reads
   as leftover default chrome inside the styled fields ---- */
div[data-testid="stDialog"] [data-testid="InputInstructions"] { display: none !important; }

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
.cai-ent-disc span.g { flex: none; font-size: 12px; line-height: 1.55; }
.cai-ent-disc span.t { font: 400 11px Heebo, sans-serif; color: rgba(236,237,230,.4); line-height: 1.55; }

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
.cai-pa-clause { font: 500 10.5px Heebo, sans-serif; color: var(--text-faint); }
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
    letter-spacing: 1px; color: rgba(236,237,230,.4); }
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
    color: rgba(236,237,230,.4); margin-top: 10px; line-height: 1.5; }
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


@st.dialog("⚖️ בודק סמכות עונש משמעתי", width="large")
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
    st.markdown(_MODAL_CSS, unsafe_allow_html=True)
    st.markdown(_modal_header("בודק סמכות עונש"), unsafe_allow_html=True)
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
            f"<span class='cai-pa-clause'>לפי פ\"מ 33.0302 · {html.escape(cap['clause'])}</span>"
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
        f"<div class='cai-pa-disc'>⚠️ {html.escape(_pa.DISCLAIMER)}</div>",
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
        "<div class='cai-ent-disc'><span class='g'>⚠️</span>"
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


@st.dialog("🧮 מחשבון זכאויות", width="large")
def _entitlements_dialog():
    """Deterministic entitlement lookup: exact leave-day counts and the
    subsistence/family-payment structure, each value quoted to its clause.

    No daily quota and NO Anthropic call — it only reads curated, order-cited
    data from entitlements.py, so it can't burn budget or hallucinate a figure.
    """
    st.markdown(_MODAL_CSS, unsafe_allow_html=True)
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


_ICON = {
    "letters": _svg("<path d='M6 3h8l4 4v14H6z'/><path d='M14 3v4h4'/><path d='M9 12h6M9 16h6'/>"),
    "gavel": _svg("<path d='M12 3v18'/><path d='M9 21h6'/><path d='M5 7h14'/>"
                  "<path d='M5 7l-2.6 5a2.6 2.6 0 0 0 5.2 0z'/><path d='M19 7l-2.6 5a2.6 2.6 0 0 0 5.2 0z'/>"),
    "calc": _svg("<rect x='5' y='3' width='14' height='18' rx='2'/><path d='M8 7h8'/>"
                 "<path d='M9 12h.01M12 12h.01M15 12h.01M9 16h.01M12 16h.01M15 16h.01'/>"),
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
    "shield": _svg("<path d='M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z'/><path d='M9 12l2 2 4-4'/>", stroke="#C4CE92", w=24),
    "gear": _svg("<circle cx='12' cy='12' r='3'/><path d='M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z'/>", stroke="#ECEDE6"),
}

# CSS for the redesigned drawer + settings overlay. Plain string (single
# braces); icon data-URIs are spliced in by token so the CSS body stays literal.
_DS_CSS = """
<style id="cai-ds">
/* ═══ DRAWER — redesigned (mockup 2a) ═══ */
.st-key-cai_drawer {
  width: min(85vw, 320px) !important;
  background: linear-gradient(180deg,#121509 0%,#0E1007 100%) !important;
  border-inline-end: 1px solid rgba(236,237,230,.08) !important;
  box-shadow: -14px 0 44px rgba(0,0,0,.5) !important;
  padding: max(42px, calc(env(safe-area-inset-top,0px) + 12px)) 16px calc(env(safe-area-inset-bottom,0px) + 10px) !important;
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
   Zero it here; all rhythm comes from the blocks' own margins. */
.st-key-cai_drawer [data-testid="stMarkdownContainer"],
.st-key-cai_settings [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
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
.st-key-open_settings button, .st-key-drawer_close button {
  width: 36px !important; height: 36px !important; min-height: 36px !important;
  border-radius: 10px !important; padding: 0 !important;
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
.cai-role-k { font: 600 10px Heebo; letter-spacing: 1px; color: rgba(236,237,230,.45); }
.cai-role-nm { font: 400 17px 'Suez One', serif; color: #ECEDE6; line-height: 1.15; margin-top: 1px; }
.cai-role-badge {
  font: 600 10.5px Heebo; color: rgba(196,206,146,.9); flex: none;
  background: rgba(var(--accent-rgb),.14); border: 1px solid rgba(var(--accent-rgb),.34);
  border-radius: 99px; padding: 4px 10px;
}

/* section label */
.cai-sec-label { font: 600 11px Heebo; letter-spacing: 1px; color: rgba(236,237,230,.4); margin: 16px 0 8px; }
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

/* knowledge-base card — custom accent card (icon + title + count pill + ‹) with
   a transparent st.button overlaying it to capture the tap */
.st-key-cai_kb { position: relative; }
.cai-kb-card {
  display: flex; align-items: center; gap: 12px; padding: 13px 14px; border-radius: 14px;
  background: linear-gradient(135deg,rgba(var(--accent-rgb),.18),rgba(var(--accent-rgb),.05));
  border: 1px solid rgba(var(--accent-rgb),.34);
}
.cai-kb-card .kb-ic { width: 18px; height: 18px; flex: none; background: url("ICON_BOOK") center / 18px no-repeat; }
.cai-kb-card .kb-title { flex: 1; font: 700 14px Heebo; color: #ECEDE6; }
.cai-kb-card .kb-badge { flex: none; font: 800 11px Heebo; color: #171A12; background: var(--accent); border-radius: 99px; padding: 2px 9px; }
.cai-kb-card .kb-chev { flex: none; color: rgba(196,206,146,.8); font-size: 15px; direction: ltr; transition: transform .18s ease; }
.cai-kb-card .kb-chev::before { content: "‹"; }
.cai-kb-card.open .kb-chev { transform: rotate(90deg); }
.st-key-toggle_orders { position: absolute !important; top: 0; inset-inline: 0; margin: 0 !important; z-index: 3; }
.st-key-toggle_orders button { opacity: 0 !important; height: 52px !important; min-height: 52px !important; padding: 0 !important; margin: 0 !important; border: none !important; }
.st-key-cai_kb [data-testid="stTextInput"] { margin-top: 10px; }
/* open state (mockup): card + search + list read as ONE bordered card with a
   darker well; the card head keeps only its top corners */
.st-key-cai_kb:has(.cai-kb-card.open) {
  border: 1px solid rgba(var(--accent-rgb),.34); border-radius: 16px;
  background: rgba(0,0,0,.22);
}
.st-key-cai_kb:has(.cai-kb-card.open) .cai-kb-card {
  border: none; border-radius: 16px 16px 0 0;
  border-bottom: 1px solid rgba(var(--accent-rgb),.18);
}
.st-key-cai_kb:has(.cai-kb-card.open) [data-testid="stTextInput"] { margin: 10px 12px 0; }
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
  background: #232A18; border: 1px solid rgba(236,237,230,.1);
}
.st-key-cai_tools [data-testid="stElementContainer"],
.st-key-cai_recent [data-testid="stElementContainer"] { margin: 0 !important; }
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
  font: 500 14px Heebo !important; color: #ECEDE6 !important; text-align: right !important;
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
.st-key-cai_tools button::after, .st-key-cai_recent button::after {
  content: "‹"; position: absolute; inset-inline-end: 14px; top: 50%;
  transform: translateY(-50%); color: rgba(236,237,230,.3); font-size: 14px;
}
@media (hover: hover) {
  .st-key-cai_tools button:hover, .st-key-cai_recent button:hover { background: rgba(236,237,230,.04) !important; }
}

/* recent head row */
.cai-recent-head { display: flex; align-items: center; gap: 8px; margin: 16px 0 8px; }
.cai-recent-t { font: 600 11px Heebo; letter-spacing: 1px; color: rgba(236,237,230,.4); }
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
  font: 600 9px ui-monospace, Menlo, monospace; letter-spacing: 2px; color: rgba(236,237,230,.3);
}

/* ═══ SETTINGS overlay (mockup 8a–8e) ═══ */
.st-key-settings_backdrop { position: fixed; inset: 0; z-index: 135; }
.st-key-settings_backdrop button {
  width: 100% !important; height: 100% !important; min-height: 100% !important;
  background: rgba(9,11,7,.85) !important; border: none !important;
  border-radius: 0 !important; box-shadow: none !important;
}
.st-key-settings_backdrop button p { display: none; }
.st-key-cai_settings {
  position: fixed; inset: 0; z-index: 140;
  width: min(100vw, 440px); margin: 0 auto;
  background: linear-gradient(180deg,#141710 0%,#0E1007 100%);
  padding: calc(env(safe-area-inset-top,0px) + 20px) 20px calc(env(safe-area-inset-bottom,0px) + 20px) !important;
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
.st-key-settings_back button {
  width: 36px !important; height: 36px !important; min-height: 36px !important;
  border-radius: 10px !important; padding: 0 !important;
  background: rgba(236,237,230,.06) !important; border: 1px solid rgba(236,237,230,.12) !important;
}
.st-key-settings_back button p { font: 600 20px Heebo !important; color: rgba(236,237,230,.7) !important; }
.cai-set-title { font: 400 21px 'Suez One', serif; color: #ECEDE6; padding: 4px 0; }
.cai-set-seclabel { font: 600 10px Heebo; letter-spacing: 2px; color: rgba(236,237,230,.38); margin: 22px 0 9px; }

/* settings grouped card + nav rows */
[class*="st-key-cai_sgrp"] {
  border-radius: 15px; overflow: hidden;
  background: #1E2416; border: 1px solid rgba(236,237,230,.1);
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
  font: 500 14px Heebo !important; color: #ECEDE6 !important; text-align: right !important;
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
.cai-set-profile .nm { font: 700 16px Heebo; color: #ECEDE6; }
.cai-set-profile .sub { font: 500 12px Heebo; color: rgba(196,206,146,.85); margin-top: 2px; }

/* toggle-display + coming-soon chip + inline row (for בקרוב items) */
.cai-row { display: flex; align-items: center; gap: 13px; padding: 14px; }
.cai-row .ic { width: 18px; height: 18px; flex: none; background-repeat: no-repeat; background-position: center; background-size: 18px; }
.cai-row .tx { flex: 1; }
.cai-row .t { font: 500 14px Heebo; color: #ECEDE6; }
.cai-row .s { font: 400 11px Heebo; color: rgba(236,237,230,.45); margin-top: 1px; }
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
.cai-banner .bt { font: 700 14px Heebo; color: #ECEDE6; }
.cai-banner .bs { font: 400 11.5px Heebo; color: rgba(196,206,146,.85); margin-top: 2px; line-height: 1.45; }
.cai-info { display: flex; align-items: center; gap: 9px; margin-top: 16px; padding: 12px 14px; border-radius: 13px;
  background: rgba(var(--accent-rgb),.08); border: 1px solid rgba(var(--accent-rgb),.2); }
.cai-info .ii { width: 16px; height: 16px; flex: none; background-image: url("ICON_INFO"); background-repeat: no-repeat; background-position: center; background-size: 16px; }
.cai-info span { font: 400 11.5px Heebo; color: rgba(236,237,230,.6); line-height: 1.5; }

/* personal: avatar + fields */
.cai-set-avwrap { display: flex; flex-direction: column; align-items: center; gap: 11px; padding: 10px 0 18px; }
.cai-set-avbig { width: 76px; height: 76px; border-radius: 22px; display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg,#AEB784,#8E9962); border: 1px solid rgba(196,206,146,.5);
  font: 700 34px 'Suez One', serif; color: #171A12; box-shadow: 0 8px 20px -8px rgba(var(--accent-rgb),.6); }
.cai-set-changephoto { display: flex; align-items: center; gap: 6px; font: 600 12px Heebo; color: rgba(196,206,146,.6); }
.cai-fld-label { font: 600 11px Heebo; color: rgba(236,237,230,.45); margin: 0 0 7px; }
.cai-lang-note { font: 400 11.5px Heebo; color: rgba(236,237,230,.5); margin: 6px 2px 14px; line-height: 1.55; }

/* language rows */
.cai-lang-card { border-radius: 15px; overflow: hidden; background: #1E2416; border: 1px solid rgba(236,237,230,.1); }
.cai-lang-row { display: flex; align-items: center; gap: 13px; padding: 15px 14px; }
.cai-lang-row .fl { font-size: 20px; flex: none; }
.cai-lang-row .nm { flex: 1; font: 600 14.5px Heebo; color: #ECEDE6; }
.cai-lang-row.dim .nm { color: rgba(236,237,230,.5); font-weight: 500; }
.cai-lang-row .def { font: 400 11px Heebo; color: rgba(236,237,230,.4); margin-top: 1px; }
.cai-lang-row .ok { color: var(--accent); font-size: 18px; font-weight: 700; }

/* ToS */
.cai-tos-lead { font: 400 21px 'Suez One', serif; color: #ECEDE6; margin-bottom: 4px; }
.cai-tos-sub { font: 500 12px Heebo; color: rgba(236,237,230,.4); margin-bottom: 20px; }
.cai-tos-h { font: 700 14.5px Heebo; color: var(--accent-bright); margin-bottom: 6px; }
.cai-tos-b { font: 400 13px Heebo; color: rgba(236,237,230,.78); line-height: 1.7; }
.cai-tos-sec { margin-bottom: 20px; }
.cai-set-foot { text-align: center; margin-top: 22px; padding-top: 16px; border-top: 1px solid rgba(236,237,230,.09); }
.cai-set-foot .a { font: 600 9px ui-monospace, Menlo, monospace; letter-spacing: 2px; color: rgba(236,237,230,.35); }
.cai-set-foot .b { font: 400 10.5px Heebo; color: rgba(236,237,230,.3); margin-top: 8px; line-height: 1.5; }

/* save / danger buttons */
.st-key-save_profile button {
  background: var(--accent) !important; border: none !important; color: #171A12 !important;
  border-radius: 13px !important; font: 700 14px Heebo !important; padding: 13px !important; margin-top: 22px !important;
}
.st-key-save_profile button p { color: #171A12 !important; font-weight: 700 !important; text-align: center !important; }
@media (hover: hover) { .st-key-save_profile button:hover { background: #A6AF76 !important; } }
[class*="st-key-danger_"] button {
  background: rgba(198,120,110,.1) !important; border: 1px solid rgba(198,120,110,.35) !important;
  color: #D89189 !important; border-radius: 13px !important; font: 600 13.5px Heebo !important;
  padding: 13px !important; margin-top: 20px !important;
}
[class*="st-key-danger_"] button p { color: #D89189 !important; font-weight: 600 !important; text-align: center !important; }
@media (hover: hover) { [class*="st-key-danger_"] button:hover { background: rgba(198,120,110,.18) !important; } }

/* privacy banner icon + real analytics toggle */
.cai-banner .bi { background-image: url("ICON_SHIELD"); }
.st-key-cai_analytics { border-radius: 15px; background: #1E2416; border: 1px solid rgba(236,237,230,.1); padding: 10px 14px 12px; margin-bottom: 8px; }
.st-key-cai_analytics [data-testid="stElementContainer"] { margin: 0 !important; }
.st-key-share_analytics_w label { font: 500 14px Heebo !important; color: #ECEDE6 !important; }
.st-key-share_analytics_w [data-baseweb="checkbox"] > div:first-child { background: var(--accent) !important; }
.cai-analytics-sub { font: 400 11px Heebo; color: rgba(236,237,230,.45); margin: 2px 0 0; }

/* personal-details native widgets styled to the mockup fields (8b) */
.st-key-pf_name_w [data-baseweb="input"], .st-key-pf_name_w [data-baseweb="base-input"] {
  background: transparent !important; border: none !important; }
.st-key-pf_name_w input {
  background: #22271A !important; border: 1px solid rgba(236,237,230,.13) !important;
  border-radius: 12px !important; color: #ECEDE6 !important;
  font: 500 14.5px Heebo !important; padding: 13px 15px !important; }
.st-key-pf_track_w [data-baseweb="select"] > div {
  background: #22271A !important; border: 1px solid rgba(236,237,230,.13) !important;
  border-radius: 12px !important; min-height: 48px !important; }
.st-key-pf_track_w [data-baseweb="select"] div, .st-key-pf_track_w [data-baseweb="select"] span {
  color: #ECEDE6 !important; font: 500 14px Heebo !important; }
/* service-type: 3 equal separated tabs */
.st-key-pf_type_w [data-testid="stButtonGroup"] {
  width: 100% !important; display: grid !important;
  grid-template-columns: 1fr 1fr 1fr !important; gap: 7px !important; }
.st-key-pf_type_w [data-testid="stButtonGroup"] button {
  width: 100% !important; border-radius: 11px !important; min-height: 44px !important;
  background: #22271A !important; border: 1px solid rgba(236,237,230,.13) !important;
  color: rgba(236,237,230,.7) !important; }
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


def _wipe_all():
    """Full on-device wipe: chats + profile back to defaults (mockup 8d)."""
    st.session_state.conversation_history = []
    st.session_state.messages = []
    st.session_state.profile_saved = []
    st.session_state.profile_customized = False
    st.session_state.profile_name = ""
    st.session_state.service_track = ""
    st.session_state.service_type = "סדיר"
    # drop the settings widgets' keys so they reseed from the reset mirrors
    for _k in ("profile_statuses", "pf_name_w", "pf_type_w", "pf_track_w"):
        st.session_state.pop(_k, None)


def _settings_hub():
    """8a — settings home: profile card + grouped nav + logout."""
    _svc = st.session_state.get("service_type") or "סדיר"
    _sub = ["שירות חובה" if _svc == "סדיר" else _svc]
    _pills = st.session_state.get("profile_saved") or []
    if _pills:
        _sub.append(_pills[0])
    st.markdown(
        "<div class='cai-set-profile'>"
        f"<div class='av'>{html.escape(role_label[:1])}</div>"
        f"<div class='m'><div class='nm'>{html.escape(role_label)}</div>"
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

    st.markdown("<div class='cai-set-seclabel'>שפה</div>", unsafe_allow_html=True)
    with st.container(key="cai_sgrp_lang"):
        if st.button("שפה", key="nav_language", use_container_width=True):
            st.session_state.settings_screen = "language"
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

    # logout = reset to the role picker (no real auth; mirrors switch-role)
    if st.button("התנתקות", key="danger_logout", use_container_width=True):
        archive_current_conversation()
        st.session_state.role = None
        st.session_state.messages = []
        st.session_state.pending_question = None
        st.session_state.pop("suggested", None)
        st.session_state.pop("orders_search", None)
        st.session_state.show_settings = False
        st.session_state.drawer_open = False
        st.rerun()
    st.markdown("<div class='cai-drawer-foot'>בלמ\"ס · לשימוש פנימי בלבד</div>", unsafe_allow_html=True)


def _settings_personal():
    """8b — personal details: name, service type/track, status pills."""
    st.markdown(
        "<div class='cai-set-avwrap'>"
        f"<div class='cai-set-avbig'>{html.escape(role_label[:1])}</div>"
        "<div class='cai-set-changephoto'>שינוי תמונה <span class='cai-bakrov'>בקרוב</span></div>"
        "</div>", unsafe_allow_html=True)

    # Widgets edit their OWN keys only; the stable mirrors that handle_question
    # reads are committed on Save. Seed each widget from its mirror on first
    # render — on close the widget key drops (widget not rendered) and reopen
    # reseeds it, so an unsaved edit is discarded rather than leaking.
    st.markdown("<div class='cai-set-seclabel'>פרטי זיהוי</div>", unsafe_allow_html=True)
    st.markdown("<div class='cai-fld-label'>שם מלא</div>", unsafe_allow_html=True)
    if "pf_name_w" not in st.session_state:
        st.session_state.pf_name_w = st.session_state.get("profile_name", "")
    st.text_input("שם מלא", key="pf_name_w",
                  label_visibility="collapsed", placeholder="ישראל ישראלי")

    st.markdown("<div class='cai-fld-label'>סוג שירות</div>", unsafe_allow_html=True)
    if "pf_type_w" not in st.session_state:
        st.session_state.pf_type_w = st.session_state.get("service_type", "סדיר")
    st.segmented_control("סוג שירות", _SERVICE_TYPES, key="pf_type_w",
                         selection_mode="single", label_visibility="collapsed")

    st.markdown("<div class='cai-fld-label'>מסלול השירות</div>", unsafe_allow_html=True)
    _tracks = ["בחר/י מסלול…"] + _SERVICE_TRACKS
    if "pf_track_w" not in st.session_state:
        _cur = st.session_state.get("service_track", "")
        st.session_state.pf_track_w = _cur if _cur in _SERVICE_TRACKS else _tracks[0]
    st.selectbox("מסלול השירות", _tracks, key="pf_track_w", label_visibility="collapsed")

    st.markdown("<div class='cai-set-seclabel'>התאמה אישית · סטטוס</div>", unsafe_allow_html=True)
    st.markdown("<div class='cai-lang-note'>בחירת הסטטוס מתאימה את החישובים והתשובות עבורך.</div>", unsafe_allow_html=True)
    if "profile_statuses" not in st.session_state and st.session_state.get("profile_saved"):
        st.session_state.profile_statuses = st.session_state.profile_saved
    st.pills("סטטוס", _STATUS_PILLS, selection_mode="multi",
             key="profile_statuses", label_visibility="collapsed")

    # Save COMMITS the widgets to their mirrors and flips profile_customized —
    # only now do the service fields reach the answer. An untouched user's API
    # turn stays byte-identical (see handle_question).
    if st.button("שמירת שינויים", key="save_profile", use_container_width=True):
        st.session_state.profile_name = st.session_state.get("pf_name_w", "") or ""
        st.session_state.service_type = st.session_state.get("pf_type_w") or "סדיר"
        _tr = st.session_state.get("pf_track_w")
        st.session_state.service_track = "" if (not _tr or _tr == _tracks[0]) else _tr
        st.session_state.profile_saved = list(st.session_state.get("profile_statuses") or [])
        st.session_state.profile_customized = True
        st.session_state.settings_screen = "hub"
        st.rerun()
    st.markdown(
        "<div class='cai-lang-note' style='text-align:center'>הפרטים נשמרים במכשיר בלבד לצורך התאמת החישובים.</div>",
        unsafe_allow_html=True)


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
        elif screen == "privacy":
            _settings_privacy()
        elif screen == "about":
            _settings_about()
        else:
            _settings_hub()


def handle_question(question: str):
    quota = metrics.reserve(st.session_state.session_id)
    if quota != "ok":
        st.session_state.messages.append({"role": "user", "content": question})
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
        profile_kw["profile"] = _injected or None
    try:
        with st.spinner("מחפש בפקודות..."):
            result = stream_ai_answer(question, history, role=st.session_state.role, **profile_kw)
            text_gen, sources = result[0], result[1]
            # Streamlit Cloud can pair a fresh app.py with a backend module
            # cached from a previous build (see note in backend.py) — older
            # builds returned 2 items and no sent-content
            if len(result) > 2:
                user_msg["api_content"] = result[2]
        with st.chat_message("assistant"):
            text = _stream_answer(text_gen)
    except (APIConnectionError, APITimeoutError):
        metrics.refund(st.session_state.session_id)  # failures don't burn quota
        st.session_state.messages.append({
            "role": "assistant",
            "content": "⚠️ **אין כרגע חיבור לשירות.**\n\n"
                       "בדוק את החיבור לאינטרנט ושלח את השאלה שוב בעוד רגע.",
            "error": True,
        })
        return
    except BadRequestError as e:
        # the monthly console spend limit returns a 400 with this exact
        # phrasing (hit live 2026-07-10); "try again" would gaslight the
        # user into resending a question that cannot succeed
        metrics.refund(st.session_state.session_id)
        if "usage limits" in str(e):
            msg = ("⏸️ **המערכת בהשהיה זמנית עקב מגבלת שימוש.**\n\n"
                   "זו לא תקלה אצלך ואין טעם לשלוח שוב עכשיו — נסה שוב מחר.")
        else:
            msg = "⚠️ **אירעה שגיאה זמנית בעיבוד השאלה.**\n\nנסה לשלוח אותה שוב."
        st.session_state.messages.append({"role": "assistant", "content": msg, "error": True})
        return
    except Exception as e:
        # last-resort catch: the refund + generic message already cover the
        # user, but without a log a real production fault leaves no trace
        safe_print(f"[chat] answer failed: {e!r}")
        metrics.refund(st.session_state.session_id)
        st.session_state.messages.append({
            "role": "assistant",
            "content": "⚠️ **אירעה שגיאה זמנית בעיבוד השאלה.**\n\n"
                       "נסה לשלוח אותה שוב.",
            "error": True,
        })
        return
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


def _order_link(title: str, url: str | None, date_badge: str | None = None) -> str:
    """One order line for the sidebar list. When the PDF is on disk the
    title itself is the tap target that opens it INLINE in a new tab.

    The href is relative on purpose: the app document sits at "/" locally
    but at "/~/+/" inside the Streamlit Cloud shell, and a relative
    "media/..." resolves correctly against both. `date_badge` is the
    order's own version date (doc_dates.badge) — orders without a
    confident date get no badge rather than a made-up one.
    """
    safe_title = html.escape(title)
    tail = f"<span class='cai-order-date'>נוסח {date_badge}</span>" if date_badge else ""
    if url:
        return (f"<a class='cai-order-link' href='{url.lstrip('/')}'"
                f" target='_blank' rel='noopener'>{safe_title}{tail}</a>")
    return f"<div class='cai-order-link'>{safe_title}{tail}</div>"


# ── Drawer (app-owned overlay) ──
# The native st.sidebar is force-suppressed by the cloud platform: on
# *.streamlit.app the frontend NEVER mounts stSidebar (verified 2026-07-13
# with a MutationObserver across the whole role-pick transition, on a build
# whose config.toml carries no toolbarMode override), even though the same
# code mounts it locally — the platform's client flags outrank config.toml.
# So the drawer is app-owned: an st.button hamburger toggles a fixed-position
# keyed container through session state. No stSidebar machinery anywhere, so
# no platform build can take it away again.
if "drawer_open" not in st.session_state:
    st.session_state.drawer_open = False

if st.button("תפריט", key="drawer_open_btn"):
    st.session_state.drawer_open = True
    st.rerun()

if st.session_state.drawer_open:
    # full-viewport click-catcher UNDER the panel — tapping outside closes
    if st.button("סגירת התפריט", key="drawer_backdrop"):
        st.session_state.drawer_open = False
        st.rerun()
    st.markdown(_DS_CSS, unsafe_allow_html=True)
    with st.container(key="cai_drawer"):
        # ── top row: settings gear (right/leading) + close « (left/trailing) ──
        _c_gear, _c_close = st.columns(2)
        with _c_gear:
            # opening settings keeps drawer_open=True on purpose — a dialog
            # dismiss doesn't rerun the full script, so flipping a layout flag
            # here would strand the drawer (painted, but its widgets no longer
            # render → the next tap inside it is silently lost).
            if st.button("⚙", key="open_settings"):
                st.session_state.show_settings = True
                st.session_state.settings_screen = "hub"
                st.rerun()
        with _c_close:
            if st.button("«", key="drawer_close"):
                st.session_state.drawer_open = False
                st.rerun()

        # ── role card (display only; role switching lives in Settings) ──
        _svc_type = st.session_state.get("service_type") or "סדיר"
        _role_badge = "שירות חובה" if _svc_type == "סדיר" else _svc_type
        st.markdown(
            "<div class='cai-role-card'>"
            f"<div class='cai-role-av'>{html.escape(role_label[:1])}</div>"
            "<div class='cai-role-meta'>"
            "<div class='cai-role-k'>מחובר כ־</div>"
            f"<div class='cai-role-nm'>{html.escape(role_label)}</div></div>"
            f"<span class='cai-role-badge'>{html.escape(_role_badge)}</span>"
            "</div>",
            unsafe_allow_html=True,
        )

        # ── knowledge base — orders list (expander styled as the accent card) ──
        st.markdown("<div class='cai-sec-label'>מאגר הידע</div>", unsafe_allow_html=True)
        docs = get_loaded_docs_info(role=st.session_state.role)
        if "orders_open" not in st.session_state:
            st.session_state.orders_open = False
        with st.container(key="cai_kb"):
            # The card visual carries the olive count PILL + the ‹ chevron —
            # a plain st.button/expander label can't style a badge, so we draw
            # the card in HTML and overlay a transparent st.button to capture
            # the tap (CSS positions it over the card).
            st.markdown(
                "<div class='cai-kb-card" + (" open" if st.session_state.orders_open else "") + "'>"
                "<span class='kb-ic'></span>"
                "<span class='kb-title'>פקודות מטכ\"ל במערכת</span>"
                f"<span class='kb-badge'>{len(docs)}</span>"
                "<span class='kb-chev'></span></div>",
                unsafe_allow_html=True)
            if st.button("פקודות מטכ\"ל במערכת", key="toggle_orders", use_container_width=True):
                st.session_state.orders_open = not st.session_state.orders_open
                st.rerun()
            if st.session_state.orders_open:
                if docs:
                    search = _search_norm(st.text_input(
                        "חיפוש פקודה",
                        key="orders_search",
                        label_visibility="collapsed",
                        placeholder="🔎 חיפוש פקודה...",
                    ))
                    # media URLs are registered for ALL docs, filtered or not: a
                    # media-manager entry whose coord isn't re-registered during a
                    # rerun is purged at that rerun's end — filtering registration
                    # would 404 a PDF the user already opened in another tab
                    rows = [
                        (doc, _pdf_media_url(doc["source_file"], f"pdfside_{doc['id']}")
                         if doc.get("source_file") else None)
                        for doc in docs
                    ]
                    shown = [
                        (doc, url) for doc, url in rows
                        if not search
                        or search in _search_norm(doc["title"])
                        or search in _search_norm(str(doc["id"]))
                    ]
                    if not shown:
                        st.caption("לא נמצאו פקודות מתאימות")
                    # each title is itself the tap target that opens the order's
                    # PDF inline (styled as a flat list line, not a button).
                    # ONE markdown for the whole list: the wrapper div is the
                    # inner scroll region — per-row st.markdown calls would each
                    # be siblings in the drawer flow and stretch it viewport-long
                    else:
                        st.markdown(
                            "<div class='cai-orders-scroll'>"
                            + "".join(_order_link(doc["title"], url, _doc_date_badge(doc["id"]))
                                      for doc, url in shown)
                            + "</div>",
                            unsafe_allow_html=True,
                        )
                else:
                    st.caption("אין פקודות טעונות")

        # ── tools (grouped card) ──
        st.markdown("<div class='cai-sec-label'>כלים</div>", unsafe_allow_html=True)
        with st.container(key="cai_tools"):
            # the tool buttons deliberately leave drawer_open=True (same reason
            # as the gear above — the dialog overlays a live, state-consistent
            # drawer, and closing it returns the user straight to it).
            if LETTER_TYPES and st.button("מחולל מכתבים", key="open_letters", use_container_width=True):
                _letters_dialog()
            # deterministic tools, zero-token, no quota — each gated on its module
            if _pa and st.button("בודק סמכות עונש", key="open_punishment", use_container_width=True):
                _punishment_dialog()
            if entitlements and st.button("מחשבון זכאויות", key="open_entitlements", use_container_width=True):
                _entitlements_dialog()

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
                    if st.button(f"💬 {conv['title']}", key=f"hist_{i}", use_container_width=True):
                        # archive the active chat first, exactly like "שיחה חדשה"
                        # and logout do — otherwise switching conversations drops
                        # the current one for good
                        archive_current_conversation()
                        st.session_state.messages = conv["messages"].copy()
                        st.session_state.drawer_open = False
                        st.rerun()
            else:
                st.caption("אין שיחות קודמות")

        # ── footer CTA ──
        if st.button("שיחה חדשה", key="new_chat", use_container_width=True):
            archive_current_conversation()
            st.session_state.messages = []
            st.session_state.drawer_open = False
            st.rerun()
        st.markdown("<div class='cai-drawer-foot'>בלמ\"ס · לשימוש פנימי בלבד</div>", unsafe_allow_html=True)

# settings overlay (state machine) — shown whenever the flag is set; opening it
# leaves the drawer open underneath so closing returns there.
if st.session_state.get("show_settings"):
    _render_settings()

# ── Header: wordmark + role pill ──
st.markdown(
    f"<div class='cai-header'>"
    f"<span class='cai-wordmark'>Command<span class='cai-wm-ai'>AI</span></span>"
    f"<span class='cai-pill'>מחובר כ־{role_label}</span>"
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
# A verdict must OPEN with one of these terms. The model sometimes opens
# the ruling line with a TOPIC ("בנוגע למסדר בוקר — ייתכן שאתה פטור...") —
# chipping that fragment produced a meaningless green badge on the pilot
# phone check (2026-07-10), so a non-term opener keeps the line as body
# text. A short qualifier may follow the term ("אסור בתנועה רגלית",
# pilot 2026-07-11). The qualifier bars ';' (a mid-list cut) and '*'
# (markdown residue), and its cap keeps the chip badge-sized: .verdict-chip
# is a nowrap pill with no max-width, and term+18 chars still fits the
# 290px breakpoint — this cap IS the overflow guard.
_VERDICT_TERM_RE = re.compile(
    r"^(?P<neg>לא\s+)?"
    r"(?P<term>מותר|אסור|מוסמך|רשאי|זכאי|פטור|חייב|ניתן|אפשר|מגיע(?:\s+ל[ךי])?)"
    r"(?P<qual>\s+[^;*]{1,18})?$"
)
# A qualifier that itself cites a verdict/ruling verb or a negation is a
# COMPOUND ruling ("מותר אך אסור במדים", "ניתן צו האוסר...") — one color
# would misstate it, so the line stays body text. Substring matching
# over-catches Hebrew prefixed forms (ואסור, שמותר); the failure mode is
# "no chip", the safe one. לא/אין are matched as words with ו/ש/כ/ב
# prefixes — bare substrings would hit מלא, אלא, לאחר.
_QUAL_CONFLICT_RE = re.compile(
    r"מותר|אסור|אוסר|מתיר|מוסמך|רשאי|זכאי|פטור|חייב|ניתן|אפשר|מגיע"
    r"|(?:^|\s)[ושכב]?(?:לא|אין)(?=\s|$)"
)


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
                icon, cls = "⚠", "cond"
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
            chip = f'<span class="verdict-chip verdict-{cls}">{icon} {html.escape(verdict)}</span>'
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
        return '<span class="verdict-chip verdict-none">ⓘ לא נמצא במאגר</span>', content
    return None, content


def _stream_answer(text_gen) -> str:
    """Render the live answer chip-first: hold the stream until the first
    line is complete; when it is a recognizable **פסיקה:** line, draw the
    chip immediately and stream only the body under it. Without this the
    raw ruling line flashes mid-stream and then jumps into a chip on the
    rerun (pilot phone feedback, 2026-07-10). Returns the FULL original
    text — session state and the copy/share payload keep the ruling line.
    """
    it = iter(text_gen)
    buf = ""
    ended = True
    for chunk in it:
        buf += chunk
        if "\n" in buf or len(buf) > 400:
            ended = False
            break
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
        /* text-size-adjust: iOS Safari inflates small text inside iframes,
           blowing the pills up until the row wraps and the last pill (פתח
           PDF) is clipped by the fixed iframe height */
        html, body {{ -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }}
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
        /* fit all pills WITHOUT scrolling on phones: tighten the chrome and
           shorten שלח בוואטסאפ → וואטסאפ, 🖼 כרטיס → 🖼. 480, not 380: the
           user's iPhone gave the iframe ~390-430px and full labels
           overflowed — shrink well before the overflow point. */
        @media (max-width: 480px) {{
          .act {{ padding:5px 10px; }}
          .xtra {{ display:none; }}
        }}
        </style>
        <div class="row">
          <button class="act" id="copy">⧉ העתק</button>
          <!-- one wrapping span: the pill is inline-flex with gap, so bare
               text + .xtra as separate flex items would put the 6px gap
               INSIDE the word ("שתף ב וואטסאפ") -->
          <a class="act" id="wa" target="_blank" rel="noopener"><span>✆ <span class="xtra">שלח ב</span>וואטסאפ</span></a>
          <button class="act" id="card"><span>🖼<span class="xtra"> כרטיס</span></span></button>
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
            const prev = btn.textContent;
            btn.textContent = ok ? "✓ הועתק" : "ההעתקה נכשלה";
            setTimeout(() => {{ btn.textContent = prev; }}, 1600);
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
        f"<span class='cai-escal-title'>🧭 למי פונים</span>"
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


@st.dialog("📄 סעיף המקור", width="large")
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
    st.markdown(_MODAL_CSS, unsafe_allow_html=True)

    # classification sub-label: "פ״מ {order} · עמוד {n} · בלמ״ס" (dynamic,
    # unlike the fixed sub on the side dialogs). doc_id is our own id ("35.0402"
    # / "PM-35.0402") — drop the "PM-" prefix so it reads as a plain order number.
    did = (primary.get("doc_id") or "").strip()
    order = did[3:] if did.upper().startswith("PM-") else did
    sub_parts = []
    if order:
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
    st.markdown(
        f"<div class='cai-sc-ctitle'>{html.escape(title)}</div>"
        "<div class='cai-sc-ccap'>הסעיף הרלוונטי מתוך נוסח הפקודה הרשמי</div>",
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
            st.markdown(body)
            if msg.get("truncated"):
                st.warning("✂️ התשובה נקטעה בגלל אורך. אפשר לשאול על חלק ממוקד יותר לתשובה שלמה.")
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
                if st.button("📄 הצג סעיף מקור", key=f"src_{msg_i}"):
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
                        role=st.session_state.role or "",
                        verdict="comment",
                        question=_question_for(msg_i),
                        answer=content,
                        sources=msg.get("sources"),
                        comment=fb_comment.strip(),
                    )
                    msg["fb_comment_sent"] = True
                    st.rerun()

# ── Greeting + suggested questions (only when no conversation yet) ──
if not st.session_state.messages:
    st.markdown(
        f"<div class='cai-greet'>במה אפשר לעזור?</div>"
        f"<div class='cai-greet-sub'>שאלות נפוצות מפקודות המטכ\"ל במערכת</div>",
        unsafe_allow_html=True,
    )
    for i, q in enumerate(suggested_questions):
        if st.button(q, key=f"sug_{i}", use_container_width=True):
            queue_question(q)

# ── Process pending question ──
if st.session_state.pending_question:
    q = st.session_state.pending_question
    st.session_state.pending_question = None
    handle_question(q)
    st.rerun()

# ── Chat input (always visible, sticky) ──
if prompt := st.chat_input("שאל על פקודה..."):
    handle_question(prompt)
    st.rerun()

# (the old "auto-collapse the sidebar after role pick" JS is gone — the
# app-owned drawer above renders closed by default and never auto-opens)