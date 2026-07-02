import random
import traceback
import streamlit as st

try:
    from backend import get_ai_response, get_loaded_docs_info, get_pdf_bytes, ensure_pdfs_ingested, get_suggested_questions, warm_index
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

_startup_ingest()

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

# Splash shows once per app launch, only over the entry screen
splash_active = st.session_state.role is None and not st.session_state.get("splash_shown")
st.session_state.splash_shown = True
# entry elements start their stagger after the splash curtain lifts
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
/* vertical gradient — dark at top, warming to olive toward the composer */
[data-testid="stAppViewContainer"] {{
    background: linear-gradient(180deg, #171A12 0%, #171A12 42%, #1C2114 68%, #242C18 88%, #2A3420 100%) fixed !important;
}}
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
/* pin the open-drawer toggle at the top start corner (right, in RTL),
   above the sticky header, and draw it as a hamburger (3 bars) per the
   design instead of Streamlit's arrow icon */
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
    max-width: 430px;
    padding: 0.6rem 22px 7rem 22px !important;
    margin: 0 auto;
}}

/* ── Splash (entry animation): olive curtain, holds then slides up ── */
.cai-splash {{
    position: fixed; inset: 0; background: #99A26B; z-index: 999990;
    display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 18px;
    animation: curtainUp .65s cubic-bezier(.7,0,.3,1) both; animation-delay: 1.15s;
    pointer-events: none;
}}
.cai-splash-chev {{ display:flex; flex-direction:column; align-items:center;
    animation: enterScale .6s cubic-bezier(.2,.7,.2,1) both; animation-delay: .1s; }}
.cai-splash-chev span {{ display:block; width:26px; height:26px;
    border-top:6px solid #171A12; border-left:6px solid #171A12; transform:rotate(45deg); }}
.cai-splash-chev span + span {{ border-color: rgba(23,26,18,.45); margin-top: -9px; }}
.cai-splash-title {{ font: 400 34px 'Suez One', serif; color: #171A12;
    animation: enterUp .6s cubic-bezier(.2,.7,.2,1) both; animation-delay: .3s; }}
.cai-splash-sub {{ font: 600 11px ui-monospace, Menlo, monospace; letter-spacing: 3px; color: rgba(23,26,18,.6);
    animation: enterUp .6s cubic-bezier(.2,.7,.2,1) both; animation-delay: .45s; }}

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

/* ── Chat header: sticky top bar, wordmark + role pill ──
   padding-inline-start clears the fixed drawer-toggle button so the
   wordmark never sits underneath it */
.cai-header {{
    position: sticky; top: 0; z-index: 100;
    background: rgba(23,26,18,.92);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    display: flex; align-items: center; gap: 12px;
    padding: 14px 52px 14px 0; margin-bottom: 4px;
    border-bottom: 1px solid rgba(236,237,230,.1);
    animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both;
}}
.cai-wordmark {{ font: 400 19px 'Suez One', serif; color: var(--text); }}
.cai-pill {{
    margin-inline-start: auto;
    font: 600 12px Heebo, sans-serif; color: var(--accent);
    background: var(--accent-soft); border: 1px solid var(--accent-border);
    border-radius: 99px; padding: 5px 12px;
}}

/* ── Chat home greeting — top-anchored, suggestions right under it ── */
.cai-greet {{ font: 400 28px 'Suez One', serif; color: var(--text); margin: 20px 0 2px;
    animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .08s; }}
.cai-greet-sub {{ font: 400 13px Heebo, sans-serif; color: var(--text-dim); margin-bottom: 12px;
    animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .16s; }}

/* suggestion cards stagger */
.st-key-sug_0 button {{ animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .24s; }}
.st-key-sug_1 button {{ animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .32s; }}
.st-key-sug_2 button {{ animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .4s; }}
.st-key-sug_3 button {{ animation: enterUp .5s cubic-bezier(.2,.7,.2,1) both; animation-delay: .48s; }}

/* ── Composer — pill bar + circular olive send ── */
/* same fixed gradient as the app container, so the pinned composer strip
   continues the backdrop seamlessly while masking content scrolling below */
[data-testid="stBottom"] {{
    background: linear-gradient(180deg, #171A12 0%, #171A12 42%, #1C2114 68%, #242C18 88%, #2A3420 100%) fixed !important;
    padding-bottom: env(safe-area-inset-bottom, 0px);
}}
[data-testid="stBottomBlockContainer"] {{
    max-width: 430px; margin: 0 auto; padding: 0.9rem 18px 0.4rem 18px !important;
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
    content: "המידע אינו מחליף ייעוץ משפטי מוסמך";
    display: block; text-align: center; margin-top: 8px;
    font: 400 10.5px Heebo, sans-serif; color: rgba(236,237,230,.3);
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

/* ── Section gaps ── */
[data-testid="stVerticalBlock"] > div {{ margin-bottom: 0.2rem; }}
.stMarkdown {{ margin-bottom: 0.2rem !important; }}

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
    height: 100vh !important;
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
/* switch-role: olive ⇄ icon at the far (left) end, per the design */
.st-key-switch_role button {{ display: flex; align-items: center; }}
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

/* ── Expander (loaded orders) — flat row with count ── */
[data-testid="stExpander"] {{
    background-color: transparent !important;
    border: none !important;
    border-radius: 0 !important;
}}
[data-testid="stExpander"] summary {{ color: var(--text) !important; font: 500 14.5px Heebo, sans-serif !important; padding: 10px 4px !important; }}
[data-testid="stExpander"] summary:hover {{ color: var(--accent) !important; }}

/* ── Loaded-order cards: olive right-border list items + inline PDF button ── */
[class*="st-key-doccard_"] {{
    background-color: transparent !important;
    border: none !important;
    border-right: 2px solid var(--accent-border) !important;
    border-radius: 0 !important;
    margin: 0 8px 2px 0 !important;
    padding: 2px 10px !important;
}}
[class*="st-key-doccard_"] div[data-testid="stDownloadButton"] > button {{
    min-height: 30px !important;
    background-color: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    padding: 2px 6px !important;
    margin-bottom: 0 !important;
    font-size: 0.85rem !important;
    width: 100%;
}}
[class*="st-key-doccard_"] div[data-testid="stDownloadButton"] > button:hover {{
    background-color: var(--surface-hover); border-color: var(--accent-border);
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

# ── Entry / role gate ──
if st.session_state.role is None:
    splash_html = (
        "<div class='cai-splash'>"
        "<div class='cai-splash-chev'><span></span><span></span></div>"
        "<div class='cai-splash-title'>CommandAI</div>"
        "<div class='cai-splash-sub'>מערכת פקודות · בלמ\"ס</div>"
        "</div>"
    ) if splash_active else ""

    st.markdown(
        splash_html +
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

if "suggested" not in st.session_state:
    all_q = get_suggested_questions(role=st.session_state.role)
    st.session_state.suggested = random.sample(all_q, min(4, len(all_q)))


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


def handle_question(question: str):
    st.session_state.messages.append({"role": "user", "content": question})
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:-1]
    ]
    with st.spinner("מחפש בפקודות..."):
        answer = get_ai_response(question, history, role=st.session_state.role)
    st.session_state.messages.append({"role": "assistant", "content": answer})


# ── Sidebar (drawer) ──
with st.sidebar:
    st.markdown(f"<div class='cai-drawer-role'>מחובר כ־{role_label}</div>", unsafe_allow_html=True)
    if st.button("החלף תפקיד", key="switch_role", use_container_width=True):
        archive_current_conversation()
        st.session_state.role = None
        st.session_state.messages = []
        st.session_state.pending_question = None
        st.session_state.pop("suggested", None)
        st.rerun()
    st.markdown("---")
    docs = get_loaded_docs_info(role=st.session_state.role)
    with st.expander(f"פקודות טעונות ({len(docs)})", expanded=False):
        if docs:
            for i, doc in enumerate(docs):
                with st.container(border=True, key=f"doccard_{i}"):
                    info_col, btn_col = st.columns([5, 1])
                    with info_col:
                        st.markdown(
                            f"<div style='color:var(--accent); font-size:0.75rem; font-weight:600;'>{doc['id']}</div>"
                            f"<div style='color:var(--text); font-size:0.85rem; margin-top:2px;'>{doc['title']}</div>",
                            unsafe_allow_html=True,
                        )
                    with btn_col:
                        pdf_bytes = get_pdf_bytes(doc["source_file"]) if doc.get("source_file") else None
                        if pdf_bytes:
                            st.download_button(
                                "📄",
                                data=pdf_bytes,
                                file_name=doc["source_file"],
                                mime="application/pdf",
                                key=f"pdf_{doc['id']}",
                                use_container_width=True,
                            )
        else:
            st.caption("אין פקודות טעונות")
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

# ── Conversation ──
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        content = msg["content"]
        if msg["role"] == "assistant":
            if "מותר בתנאים" in content:
                st.markdown("⚠ **מותר בתנאים**")
            elif "אסור" in content:
                st.markdown("✗ **אסור**")
            elif "מותר" in content:
                st.markdown("✓ **מותר**")
        st.markdown(content)

# ── Greeting + suggested questions (only when no conversation yet) ──
if not st.session_state.messages:
    st.markdown(
        f"<div class='cai-greet'>במה אפשר לעזור?</div>"
        f"<div class='cai-greet-sub'>שאלות נפוצות מהפקודות הטעונות ({len(docs)})</div>",
        unsafe_allow_html=True,
    )
    for i, q in enumerate(st.session_state.suggested):
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
