import html
import inspect
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
# Disciplinary-punishment-authority checker (grounded in PM-33.0302). Pure
# data + lookup, ZERO LLM tokens — so it needs no quota. A missing module
# (stale cloud build) just hides the sidebar button, same as letters above.
try:
    import punishment_authority as _pa
except Exception:
    _pa = None

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
@keyframes bootCurtainUp { from { transform:translateY(0); } to { transform:translateY(-101%); } }
.cai-splash {
    position: fixed; inset: 0; background: #99A26B; z-index: 999990;
    display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 18px;
    animation: bootCurtainUp .65s cubic-bezier(.7,0,.3,1) both; animation-delay: 30s;
    pointer-events: none;
}
.cai-splash-chev { display:flex; flex-direction:column; align-items:center;
    animation: bootEnterScale .6s cubic-bezier(.2,.7,.2,1) both; animation-delay: .1s; }
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
        "label": "חייל", "accent": "#99A26B", "accent_hover": "#AAB37C",
        "soft": "rgba(153,162,107,.14)", "border": "rgba(153,162,107,.35)",
    },
    "commander": {
        "label": "מפקד", "accent": "#B29A72", "accent_hover": "#C4AC84",
        "soft": "rgba(178,154,114,.14)", "border": "rgba(178,154,114,.4)",
    },
    "reserve": {
        "label": "מילואים", "accent": "#8A9BC0", "accent_hover": "#9DAECE",
        "soft": "rgba(138,155,192,.12)", "border": "rgba(138,155,192,.38)",
    },
}
role_meta = ROLE_META.get(st.session_state.role, ROLE_META["soldier"])
role_label = role_meta["label"]
ACCENT = role_meta["accent"]
ACCENT_HOVER = role_meta["accent_hover"]
ACCENT_SOFT = role_meta["soft"]
ACCENT_BORDER = role_meta["border"]

# chat screen needs room under the fixed header band; entry has no header
MAIN_TOP_PADDING = "12px" if st.session_state.role is None else "80px"

# entry elements start their stagger after the boot splash curtain lifts
# (splash_active is computed at the top of the script, where the splash
# renders as the first delta)
EHOLD = "1.35s" if splash_active else "0s"

# CSS-drawn role icons (chevron / bars / diamond) as inline SVG tiles
_ICON_SOLDIER = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='18' height='18'%3E%3Cpath d='M4 12 L9 6 L14 12' fill='none' stroke='%2399A26B' stroke-width='3'/%3E%3C/svg%3E\")"
_ICON_COMMANDER = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='18' height='18'%3E%3Crect x='1' y='4.5' width='16' height='3.5' rx='1' fill='%23B29A72'/%3E%3Crect x='1' y='11' width='16' height='3.5' rx='1' fill='%23B29A72'/%3E%3C/svg%3E\")"
_ICON_RESERVE = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='20' height='20'%3E%3Crect x='5.5' y='5.5' width='9' height='9' fill='none' stroke='%238A9BC0' stroke-width='2.5' transform='rotate(45 10 10)'/%3E%3C/svg%3E\")"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;600;700;800&family=Suez+One&display=swap');

:root {{
    --bg: #171A12;
    --surface: #21261A;
    --surface-hover: #2A3120;
    --text: #ECEDE6;
    --text-sec: rgba(236,237,230,.6);
    --text-dim: rgba(236,237,230,.5);
    --text-faint: rgba(236,237,230,.35);
    --border: rgba(236,237,230,.12);
    --border-strong: rgba(236,237,230,.15);
    --accent: {ACCENT};
    --accent-hover: {ACCENT_HOVER};
    --accent-soft: {ACCENT_SOFT};
    --accent-border: {ACCENT_BORDER};
    --ehold: {EHOLD};
}}

@keyframes enterUp {{ from {{ opacity:0; transform:translateY(18px); }} to {{ opacity:1; transform:none; }} }}
@keyframes enterScale {{ from {{ opacity:0; transform:scale(.6); }} to {{ opacity:1; transform:none; }} }}
@keyframes curtainUp {{ from {{ transform:translateY(0); }} to {{ transform:translateY(-101%); }} }}

html, body, [data-testid="stApp"], [data-testid="stAppViewContainer"] {{
    font-family: Heebo, -apple-system, "Segoe UI", Arial, sans-serif;
    background-color: var(--bg);
    color: var(--text);
}}
/* vertical gradient — dark at top, warming to olive toward the composer.
   NOTE: no `fixed` attachment — iOS Safari renders it black; vh fallback
   first for devices without dvh support */
[data-testid="stAppViewContainer"] {{
    background-image: linear-gradient(180deg, #171A12 0%, #171A12 42%, #1C2114 68%, #242C18 88%, #2A3420 100%) !important;
    background-size: 100% 100vh !important;
    background-size: 100% 100dvh !important;
    background-attachment: scroll !important;
    min-height: 100vh;
    min-height: 100dvh;
}}
/* hide the scroll bar (shows as a dark strip on the left edge in RTL) */
[data-testid="stAppViewContainer"], [data-testid="stMain"], body {{
    scrollbar-width: none !important;
}}
[data-testid="stAppViewContainer"]::-webkit-scrollbar,
[data-testid="stMain"]::-webkit-scrollbar,
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
    width: 44px !important;
    height: 44px !important;
}}
/* the hamburger lives INSIDE the fixed header band: same 430px column,
   vertically centered in the 64px bar, above it in z-order; drawn as 3
   bars per the design instead of Streamlit's arrow icon */
[data-testid="stExpandSidebarButton"] {{
    position: fixed !important;
    top: 10px !important;
    inset-inline-start: 12px !important;
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
div[data-testid="stButton"] > button:hover {{
    background-color: var(--surface-hover);
    border-color: var(--accent-border);
    color: var(--text);
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
.st-key-role_soldier button:hover {{ border-color: rgba(153,162,107,.5) !important; }}
.st-key-role_commander button:hover {{ border-color: rgba(178,154,114,.5) !important; }}
.st-key-role_reserve button:hover {{ border-color: rgba(138,155,192,.5) !important; }}
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
    height: 64px; box-sizing: border-box;
    background: rgba(23,26,18,.92);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    display: flex; align-items: center; gap: 12px;
    padding: 0 68px 0 22px;
    border-bottom: 1px solid rgba(236,237,230,.1);
    /* no entrance animation: a transform on a fixed element re-anchors it
       and Streamlit can freeze the animation at its from-state (top: 18px) */
}}
.cai-wordmark {{ font: 400 19px 'Suez One', serif; color: var(--text); }}
.cai-pill {{
    margin-inline-start: auto;
    font: 600 12px Heebo, sans-serif; color: var(--accent);
    background: var(--accent-soft); border: 1px solid var(--accent-border);
    border-radius: 99px; padding: 5px 12px;
}}

/* ── Chat home greeting — top-anchored and centered, per the reference ── */
.cai-greet {{ font: 400 28px 'Suez One', serif; color: var(--text); margin: 20px 0 2px;
    text-align: center;
    animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .08s; }}
.cai-greet-sub {{ font: 400 13px Heebo, sans-serif; color: var(--text-dim); margin-bottom: 12px;
    text-align: center;
    animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .16s; }}

/* suggestion cards stagger */
.st-key-sug_0 button {{ animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .24s; }}
.st-key-sug_1 button {{ animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .32s; }}
.st-key-sug_2 button {{ animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .4s; }}
.st-key-sug_3 button {{ animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .48s; }}

/* ── Composer — pill bar + circular olive send ── */
/* the pinned composer strip shows the BOTTOM slice of the same viewport-
   sized gradient, continuing the backdrop seamlessly while masking content
   scrolling below (no `fixed` attachment — broken on iOS Safari; vh
   fallback first for devices without dvh) */
[data-testid="stBottom"] {{
    background-color: #242C18 !important;
    background-image: linear-gradient(180deg, #171A12 0%, #171A12 42%, #1C2114 68%, #242C18 88%, #2A3420 100%) !important;
    background-size: 100% 100vh !important;
    background-size: 100% 100dvh !important;
    background-position: bottom !important;
    padding-bottom: env(safe-area-inset-bottom, 0px);
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
    background-color: var(--surface) !important;
    border: 1px solid var(--border-strong) !important;
    border-radius: 99px !important;
    padding: 4px 6px 4px 4px !important;
    align-items: center !important;
    transition: border-color .15s ease;
}}
[data-testid="stChatInput"]:focus-within {{ border-color: var(--accent-border) !important; }}
[data-testid="stChatInputTextArea"] {{
    color: var(--text) !important; font: 400 14px Heebo, sans-serif !important; direction: rtl;
}}
[data-testid="stChatInput"] textarea::placeholder {{ color: rgba(236,237,230,.4) !important; }}
[data-testid="stChatInputSubmitButton"] {{
    background-color: var(--accent) !important;
    border-radius: 50% !important;
    width: 40px !important; height: 40px !important;
    min-width: 40px !important; min-height: 40px !important;
    padding: 0 !important; border: none !important;
}}
[data-testid="stChatInputSubmitButton"]:hover {{ background-color: var(--accent-hover) !important; }}
[data-testid="stChatInputSubmitButton"] svg {{ fill: #171A12 !important; }}
/* disclaimer under the composer */
[data-testid="stBottomBlockContainer"]::after {{
    content: "כלי עזר מבוסס בינה מלאכותית — אינו ייעוץ משפטי או פקודה מחייבת. בכל סתירה, פקודות מטכ״ל הרשמיות הן הקובעות.";
    display: block; text-align: center; margin-top: 8px;
    line-height: 1.45; max-width: 460px; margin-inline: auto;
    font: 400 10.5px Heebo, sans-serif; color: rgba(236,237,230,.5);
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
[class*="st-key-src_"] button:hover {{ background: var(--accent) !important; color: #171A12 !important; }}
[class*="st-key-src_"] button:hover p {{ color: #171A12 !important; }}
/* full-order link inside the clause dialog */
.cai-full-pdf {{
    display: inline-block; margin-top: 10px;
    font: 500 13px Heebo, sans-serif; color: var(--text-dim) !important;
    text-decoration: none !important;
}}
.cai-full-pdf:hover {{ color: var(--accent) !important; }}

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
[data-testid="stSidebarHeader"] {{ padding: calc(env(safe-area-inset-top, 0px) + 12px) 16px 0 !important; }}
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

/* switch-role: right-aligned label, olive ⇄ icon at the far (left) end */
.st-key-switch_role button {{ display: flex; align-items: center; justify-content: flex-start; }}
.st-key-switch_role button::after {{
    content: "⇄"; color: var(--accent); font-size: 16px; margin-inline-start: auto;
}}
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
    background: rgba(236,237,230,.05) !important;
    border: 1px solid rgba(236,237,230,.22) !important;
    border-radius: 99px !important;
    color: rgba(236,237,230,.75) !important;
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
    color: #171A12 !important;
    font: 700 15px Heebo, sans-serif !important;
    text-align: center !important;
    justify-content: center;
}}
.st-key-new_chat button:hover {{ background-color: var(--accent-hover) !important; }}
.st-key-new_chat button p {{ color: #171A12 !important; font-weight: 700 !important; text-align: center !important; }}

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
[data-testid="stExpander"] summary svg {{ fill: rgba(236,237,230,.4) !important; }}
/* only the orders list scrolls (capped like the design), not the drawer */
[data-testid="stExpanderDetails"] {{
    padding: 0 !important;
    max-height: 300px;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: rgba(236,237,230,.25) transparent;
}}
[data-testid="stExpanderDetails"]::-webkit-scrollbar {{ width: 5px; }}
[data-testid="stExpanderDetails"]::-webkit-scrollbar-thumb {{
    background: rgba(236,237,230,.25); border-radius: 3px;
}}

/* ── Loaded orders: each title IS the tap target that opens its PDF
   inline — styled as a flat list line (olive right rule, dim text) ── */
.cai-order-link {{
    display: block;
    border-right: 2px solid var(--accent-border);
    color: rgba(236,237,230,.65) !important;
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
    color: rgba(236,237,230,.38);
    margin-right: 6px;
    white-space: nowrap;
}}
/* orders search field — surface pill matching the drawer's dark theme */
[data-testid="stSidebar"] [data-testid="stTextInput"] {{ margin: 4px 8px 8px 0; }}
[data-testid="stSidebar"] [data-testid="stTextInput"] div[data-baseweb="input"],
[data-testid="stSidebar"] [data-testid="stTextInput"] div[data-baseweb="base-input"] {{
    background-color: var(--surface) !important;
    border: 1px solid var(--border-strong) !important;
    border-radius: 10px !important;
}}
[data-testid="stSidebar"] [data-testid="stTextInput"] div[data-baseweb="base-input"] {{ border: none !important; }}
[data-testid="stSidebar"] [data-testid="stTextInput"] input {{
    background-color: transparent !important;
    color: var(--text) !important;
    font: 400 13px Heebo, sans-serif !important;
    direction: rtl;
    padding: 8px 12px !important;
}}
[data-testid="stSidebar"] [data-testid="stTextInput"] input::placeholder {{
    color: rgba(236,237,230,.4) !important;
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
    killBadges();
    setInterval(killBadges, 1000);
    </script>""",
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
        st.session_state.close_drawer = True
        st.rerun()
    if st.button("**כניסת מפקדים**  \nקבע", key="role_commander", use_container_width=True):
        st.session_state.role = "commander"
        st.session_state.close_drawer = True
        st.rerun()
    if st.button("**כניסת מילואים**  \nמערך המילואים", key="role_reserve", use_container_width=True):
        st.session_state.role = "reserve"
        st.session_state.close_drawer = True
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


@st.dialog("📄 מחולל מכתבים", width="large")
def _letters_dialog():
    """Order-grounded formal-letter drafts (בקשת חופשה, ערר, קבילה...).

    One generation burns one daily-quota unit — the same reserve/refund
    contract as a chat question, so this flow cannot sidestep the global
    budget. The draft lands in an editable textarea; the download button
    exports whatever the user edited, not the raw model text.
    """
    kind = st.selectbox(
        "סוג המכתב",
        list(LETTER_TYPES),
        format_func=lambda k: LETTER_TYPES[k]["title"],
        key="letter_kind",
    )
    details = {}
    for i, (label, placeholder) in enumerate(LETTER_TYPES[kind]["fields"]):
        details[label] = st.text_input(
            label, placeholder=placeholder or None, key=f"letter_{kind}_{i}"
        )
    if st.button("✍️ נסח טיוטה", key="letter_go", use_container_width=True):
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
            except Exception:
                metrics.refund(st.session_state.session_id)
                st.error("⚠️ אירעה שגיאה זמנית בניסוח. נסה לשלוח שוב.")
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


# CSS is injected inside the dialog (scoped) rather than into the global
# f-string style block — keeps the whole feature self-contained and avoids
# touching that block's {{ }} escaping. :root tokens (--accent/--surface/...)
# are global, so they resolve here too.
_PA_CSS = """
<style>
.cai-pa-intro { direction: rtl; text-align: right; font: 400 12.5px/1.6 Heebo, sans-serif;
    color: var(--text-sec); margin: 2px 0 10px; }
.cai-pa-row { direction: rtl; display: flex; align-items: center; justify-content: space-between;
    gap: 10px; padding: 7px 2px; border-bottom: 1px solid var(--border); }
.cai-pa-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.cai-pa-pun { font: 500 13px Heebo, sans-serif; color: var(--text); }
.cai-pa-clause { font: 500 10.5px Heebo, sans-serif; color: var(--text-faint); }
.cai-pa-max { flex: 0 0 auto; border-radius: 8px; padding: 3px 11px; white-space: nowrap;
    font: 600 12.5px Heebo, sans-serif; border: 1px solid; }
.cai-pa-max.ok    { color:#A9C687; background:rgba(148,183,110,.13); border-color:rgba(148,183,110,.35); }
.cai-pa-max.plain { color:var(--text-sec); background:rgba(236,237,230,.05); border-color:var(--border); }
.cai-pa-max.no    { color:#D68C77; background:rgba(208,124,102,.10); border-color:rgba(208,124,102,.32); }
.cai-pa-box { direction: rtl; text-align: right; border: 1px solid var(--border);
    border-radius: 10px; padding: 11px 13px; margin-top: 14px; background: rgba(236,237,230,.03); }
.cai-pa-box-title { font: 600 13px Heebo, sans-serif; color: var(--text); margin-bottom: 5px; }
.cai-pa-box-body { font: 400 12.5px/1.65 Heebo, sans-serif; color: var(--text-sec); }
.cai-pa-tag { display:inline-block; font: 500 10.5px Heebo, sans-serif; color: var(--text-faint);
    margin-top: 5px; }
.cai-pa-note li { font: 400 12px/1.6 Heebo, sans-serif; color: var(--text-dim); margin-bottom: 6px; }
.cai-pa-disc { direction: rtl; text-align: right; font: 400 11.5px/1.6 Heebo, sans-serif;
    color: var(--text-faint); border-top: 1px solid var(--border); padding-top: 11px; margin-top: 14px; }
</style>
"""


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
    st.markdown(_PA_CSS, unsafe_allow_html=True)
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
    st.markdown("".join(rows_html), unsafe_allow_html=True)

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
        # empty selection -> None, so the composed user turn stays
        # byte-identical to the pre-profile format (prompt-cache prefix)
        profile_kw["profile"] = st.session_state.get("profile_statuses") or None
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
    except Exception:
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
    })
    metrics.log_question(
        session_id=st.session_state.session_id,
        role=st.session_state.role or "",
        question=question,
        answer=text,
        sources=sources,
        # getattr: a stale cached backend from a previous cloud build may
        # predate last_usage (see deploy note in backend.py)
        usage=getattr(backend, "last_usage", None),
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


# ── Sidebar (drawer) ──
with st.sidebar:
    st.markdown(f"<div class='cai-drawer-role'>מחובר כ־{role_label}</div>", unsafe_allow_html=True)
    if st.button("החלף תפקיד", key="switch_role", use_container_width=True):
        archive_current_conversation()
        st.session_state.role = None
        st.session_state.messages = []
        st.session_state.pending_question = None
        st.session_state.pop("suggested", None)
        # a stale search would silently filter the next role's orders list
        st.session_state.pop("orders_search", None)
        st.rerun()
    # personal statuses that change entitlements (lone soldier, new
    # immigrant...). The widget key IS the persistence: st.pills keeps the
    # selection list in session state under "profile_statuses" across
    # reruns, and handle_question threads it into the API user turn only
    # when non-empty (backend._compose_user_content) — so an empty
    # selection keeps requests byte-identical to the pre-profile format.
    st.markdown("<div class='cai-profile-label'>התאמה אישית</div>", unsafe_allow_html=True)
    st.pills(
        "התאמה אישית",
        ["חייל בודד", "עולה חדש", "הורה לילדים", "נשוי/אה"],
        selection_mode="multi",
        key="profile_statuses",
        label_visibility="collapsed",
    )
    st.markdown("---")
    docs = get_loaded_docs_info(role=st.session_state.role)
    with st.expander(f"פקודות מטכ\"ל במערכת ({len(docs)})", expanded=False):
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
            # each title is itself the tap target that opens the order's PDF
            # inline (styled as a flat list line, not a button — CSS above)
            for doc, url in shown:
                st.markdown(_order_link(doc["title"], url, _doc_date_badge(doc["id"])), unsafe_allow_html=True)
        else:
            st.caption("אין פקודות טעונות")
    if LETTER_TYPES and st.button("📄 מחולל מכתבים", key="open_letters", use_container_width=True):
        _letters_dialog()
    # deterministic, zero-token, no quota — gated only on the module importing
    if _pa and st.button("⚖️ בודק סמכות עונש", key="open_punishment", use_container_width=True):
        _punishment_dialog()
    st.markdown("---")

    st.markdown("<div class='cai-drawer-section'><span class='dot'></span>שיחות אחרונות</div>", unsafe_allow_html=True)
    # only this role's conversations: restoring a chat that ran under another
    # role's system prompt would mix personas/doc scopes in one thread
    role_history = [
        (i, conv) for i, conv in enumerate(st.session_state.conversation_history)
        if conv.get("role") == st.session_state.role
    ]
    if role_history:
        for i, conv in role_history:
            if st.button(f"💬 {conv['title']}", key=f"hist_{i}", use_container_width=True):
                st.session_state.messages = conv["messages"].copy()
                st.rerun()
    else:
        st.caption("אין שיחות קודמות")
    st.markdown("---")

    if st.button("+ שיחה חדשה", key="new_chat", use_container_width=True):
        archive_current_conversation()
        st.session_state.messages = []
        st.rerun()

# ── Header: wordmark + role pill ──
st.markdown(
    f"<div class='cai-header'>"
    f"<span class='cai-wordmark'>CommandAI</span>"
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
    payload = json.dumps(content + "\n\n— CommandAI")
    src_title = json.dumps(pdf[1] if pdf else None)
    # verdict clauses classified in Python (verdict.py) — the SINGLE source
    # of the card's colours; the card JS no longer classifies, only draws
    vclauses = json.dumps(_verdict_clauses(content))
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
    """
    title = primary.get("title", "")
    st.caption(title + (f" · עמוד {page}" if page else ""))
    img = _clause_image(primary.get("source_file"), page, primary.get("highlight", "")) if page else None
    if img:
        st.image(img, use_container_width=True)
    elif not full_href:
        st.info("לא נמצאה תצוגת סעיף לפקודה זו.")
    if full_href:
        st.markdown(
            f"<a class='cai-full-pdf' href='{full_href}' target='_blank' rel='noopener'>"
            f"⎙ פתח את הפקודה המלאה (PDF)</a>",
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
        f"<div class='cai-greet-sub'>שאלות נפוצות מפקודות המטכ\"ל במערכת ({len(docs)})</div>",
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

# ── Streamlit auto-opens a sidebar the first time it mounts mid-session,
# so picking a role landed users inside the drawer instead of the chat.
# Right after the role gate, click the collapse button once it appears. ──
if st.session_state.pop("close_drawer", False):
    components.html(
        """<script>
        const doc = window.parent.document;
        let tries = 30;
        const tick = setInterval(() => {
            const sb = doc.querySelector('[data-testid="stSidebar"]');
            const isOpen = sb && sb.getAttribute('aria-expanded') !== 'false'
                && getComputedStyle(sb).display !== 'none';
            const btn = doc.querySelector('[data-testid="stSidebarCollapseButton"] button')
                     || doc.querySelector('[data-testid="stSidebarCollapseButton"]');
            if (isOpen && btn) { btn.click(); clearInterval(tick); }
            else if (sb && !isOpen) { clearInterval(tick); }
            if (--tries <= 0) clearInterval(tick);
        }, 100);
        </script>""",
        height=0,
    )