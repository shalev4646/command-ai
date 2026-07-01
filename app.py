import random
import traceback
import streamlit as st

try:
    from backend import get_ai_response, get_loaded_docs_info, get_pdf_bytes, ensure_pdfs_ingested, get_suggested_questions
except Exception:
    st.set_page_config(page_title="CommandAI - Error", layout="wide")
    st.error("שגיאה בטעינת המערכת (import של backend נכשל):")
    st.code(traceback.format_exc())
    st.stop()

@st.cache_resource(show_spinner=False)
def _startup_ingest():
    ensure_pdfs_ingested()

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

# ── Theme: olive for soldiers, gold for commanders ──
IS_COMMANDER = st.session_state.role == "commander"
ACCENT = "#c9a227" if IS_COMMANDER else "#7c8f52"
ACCENT_HOVER = "#e0bc3d" if IS_COMMANDER else "#96ab68"
ACCENT_SOFT = "rgba(201,162,39,0.14)" if IS_COMMANDER else "rgba(124,143,82,0.14)"

st.markdown(f"""
<style>
:root {{
    --bg: #0a0a0a;
    --bg-card: #17181a;
    --border: #2a2b2d;
    --text: #f0eee9;
    --text-dim: #918d87;
    --accent: {ACCENT};
    --accent-hover: {ACCENT_HOVER};
    --accent-soft: {ACCENT_SOFT};
}}

html, body, [data-testid="stApp"], [data-testid="stAppViewContainer"] {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
    background-color: var(--bg);
    color: var(--text);
}}

/* Hide Streamlit chrome, but keep the sidebar toggle (lives inside <header>) visible.
   Note: stToolbar itself must stay display:flex — it's the ancestor of the sidebar
   toggle button, and display:none on an ancestor can't be undone on a child. */
#MainMenu, footer {{ visibility: hidden; }}
header {{ visibility: hidden; }}
[data-testid="stToolbarActions"] {{ display: none; }}

/* ── Sidebar open/close buttons ── */
[data-testid="stExpandSidebarButton"],
[data-testid="stSidebarCollapseButton"] {{
    visibility: visible !important;
    background-color: var(--bg-card) !important;
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.4) !important;
}}
[data-testid="stExpandSidebarButton"] svg,
[data-testid="stSidebarCollapseButton"] svg {{ fill: var(--accent) !important; }}

/* ── Main container — tight, edge-to-edge feel ── */
[data-testid="stMainBlockContainer"], .main .block-container {{
    max-width: 480px;
    padding: 0.75rem 1rem 6.5rem 1rem !important;
    margin: 0 auto;
}}

/* ── Typography ── */
h1 {{
    font-size: 1.9rem !important;
    font-weight: 800 !important;
    color: var(--accent) !important;
    letter-spacing: -0.02em;
    margin: 0.4rem 0 0.15rem 0 !important;
    text-align: center;
}}
p {{ font-size: 1rem !important; line-height: 1.55 !important; }}

/* ── Buttons — rounded, soft shadow, tactile press ── */
div[data-testid="stButton"] > button {{
    width: 100%;
    border-radius: 14px;
    background-color: var(--bg-card);
    border: 1px solid var(--border);
    color: var(--text);
    font-size: 1rem;
    font-weight: 600;
    padding: 14px 16px;
    min-height: 52px;
    line-height: 1.35;
    margin-bottom: 10px;
    white-space: normal;
    text-align: right;
    box-shadow: 0 2px 10px rgba(0,0,0,0.35);
    transition: transform 0.1s ease, border-color 0.15s ease, box-shadow 0.15s ease;
}}
div[data-testid="stButton"] > button:hover {{
    border-color: var(--accent);
    box-shadow: 0 3px 14px rgba(0,0,0,0.5);
}}
div[data-testid="stButton"] > button:active {{
    transform: scale(0.97);
    box-shadow: 0 1px 4px rgba(0,0,0,0.4);
}}

/* ── Entry screen role buttons ── */
.st-key-role_soldier button,
.st-key-role_commander button {{
    min-height: 88px !important;
    font-size: 1.15rem !important;
    background: linear-gradient(180deg, #1c1d1f 0%, #17181a 100%) !important;
}}
.st-key-role_soldier button {{ border-color: #4c5738 !important; }}
.st-key-role_commander button {{ border-color: #6b5a17 !important; }}

/* ── Search / text input ── */
.stTextInput > div > div > input {{
    background-color: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 14px;
    color: var(--text);
    padding: 14px 16px;
    font-size: 1rem;
    min-height: 48px;
}}
.stTextInput > div > div > input::placeholder {{ color: var(--text-dim); }}

/* ── Chat input — sticky at the bottom for one-handed typing ──
   stChatInput is the real pill: several unnamed Streamlit wrapper divs
   inside it carry their own light-theme background/border, which showed
   up as a lighter box nested inside our dark pill. Blanket-clear every
   descendant first, then re-apply the pill look on stChatInput alone and
   the accent circle on the submit button (later rules win the tie). */
[data-testid="stBottom"] {{
    background: radial-gradient(ellipse 100% 100% at 50% 100%, var(--accent-soft) 0%, var(--bg) 65%) !important;
    padding-bottom: env(safe-area-inset-bottom, 0px);
}}
[data-testid="stBottomBlockContainer"] {{
    max-width: 480px;
    margin: 0 auto;
    padding: 1.1rem 1rem 1rem 1rem !important;
}}
[data-testid="stChatInput"] * {{
    background-color: transparent !important;
    border: none !important;
    box-shadow: none !important;
}}
[data-testid="stChatInput"] {{
    background-color: var(--bg-card) !important;
    border: 1px solid var(--border) !important;
    border-radius: 30px !important;
    box-shadow: 0 6px 20px rgba(0,0,0,0.4) !important;
    align-items: center !important;
    transition: border-color 0.15s ease;
}}
[data-testid="stChatInput"]:focus-within {{
    border-color: var(--accent) !important;
}}
[data-testid="stChatInputTextArea"] {{
    color: var(--text) !important;
    font-size: 1rem !important;
}}
[data-testid="stChatInputSubmitButton"] {{
    background-color: var(--accent) !important;
    border-radius: 50% !important;
    width: 34px !important;
    height: 34px !important;
    min-width: 34px !important;
    min-height: 34px !important;
    padding: 0 !important;
    box-shadow: none !important;
    border: none !important;
}}
[data-testid="stChatInputSubmitButton"]:hover {{
    background-color: var(--accent-hover) !important;
}}

/* ── Chat messages — user vs assistant visually distinct ── */
[data-testid="stChatMessage"] {{
    background-color: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 12px 16px;
    margin-bottom: 10px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
}}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {{
    background-color: var(--accent-soft);
    border-color: var(--accent);
}}

/* ── Section gaps ── */
[data-testid="stVerticalBlock"] > div {{ margin-bottom: 0.2rem; }}
.stMarkdown {{ margin-bottom: 0.2rem !important; }}

/* ── Sidebar ── */
[data-testid="stSidebar"] {{
    background-color: var(--bg);
    border-left: 1px solid var(--border);
}}
[data-testid="stSidebar"] * {{ text-align: right; }}

/* ── Expander (loaded documents) ── */
[data-testid="stExpander"] {{
    background-color: var(--bg-card) !important;
    border: 1px solid var(--border) !important;
    border-radius: 12px !important;
}}
[data-testid="stExpander"] summary {{
    color: var(--text) !important;
    font-weight: 600;
}}

/* ── Caption / small text ── */
.stCaption, small {{ color: var(--text-dim) !important; font-size: 0.82rem !important; }}

/* ── Spinner ── */
.stSpinner > div {{ border-top-color: var(--accent) !important; }}
</style>
""", unsafe_allow_html=True)

# ── Entry / role gate ──
if st.session_state.role is None:
    st.markdown(
        "<div style='text-align:center; padding-top:3rem;'>"
        "<div style='font-size:2.6rem;'>🛡️</div>"
        "<h1>CommandAI</h1>"
        "<p style='text-align:center; color:var(--text-dim); margin-top:0;'>בחר את סוג הכניסה שלך</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown("<br>", unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🪖 כניסת חיילים", key="role_soldier", use_container_width=True):
            st.session_state.role = "soldier"
            st.rerun()
    with col2:
        if st.button("⭐ כניסת מפקדים", key="role_commander", use_container_width=True):
            st.session_state.role = "commander"
            st.rerun()
    st.stop()

if "suggested" not in st.session_state:
    all_q = get_suggested_questions()
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
    })
    st.session_state.conversation_history = st.session_state.conversation_history[:10]


def handle_question(question: str):
    st.session_state.messages.append({"role": "user", "content": question})
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:-1]
    ]
    with st.spinner("מחפש בפקודות..."):
        answer = get_ai_response(question, history)
    st.session_state.messages.append({"role": "assistant", "content": answer})


# ── Sidebar ──
with st.sidebar:
    role_label = "מפקד" if IS_COMMANDER else "חייל"
    st.caption(f"מחובר כ-{role_label}")
    if st.button("🔄 החלף תפקיד", key="switch_role"):
        st.session_state.role = None
        st.rerun()
    st.markdown("---")
    docs = get_loaded_docs_info()
    with st.expander(f"📋 פקודות טעונות ({len(docs)})", expanded=False):
        if docs:
            for doc in docs:
                st.markdown(
                    f"""<div style='background:#1c1d1f; border:1px solid #2a2b2d; border-radius:10px;
                        padding:8px 12px; margin-bottom:4px;'>
                        <div style='color:var(--accent); font-size:0.78rem; font-weight:600;'>{doc['id']}</div>
                        <div style='color:#f0eee9; font-size:0.88rem; margin-top:2px;'>{doc['title']}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )
                pdf_bytes = get_pdf_bytes(doc["source_file"]) if doc.get("source_file") else None
                if pdf_bytes:
                    st.download_button(
                        "📄 פתח PDF מקורי",
                        data=pdf_bytes,
                        file_name=doc["source_file"],
                        mime="application/pdf",
                        key=f"pdf_{doc['id']}",
                        use_container_width=True,
                    )
        else:
            st.caption("אין פקודות טעונות")
    st.markdown("---")

    st.markdown("### 🕘 שיחות אחרונות")
    if st.session_state.conversation_history:
        for i, conv in enumerate(st.session_state.conversation_history):
            if st.button(f"💬 {conv['title']}", key=f"hist_{i}", use_container_width=True):
                st.session_state.messages = conv["messages"].copy()
                st.rerun()
    else:
        st.caption("אין שיחות קודמות")
    st.markdown("---")

    if st.button("🗑️ שיחה חדשה"):
        archive_current_conversation()
        st.session_state.messages = []
        st.rerun()

# ── Header ──
st.markdown(
    f"<div style='text-align:center; padding: 0.5rem 0 0.8rem 0;'>"
    f"<h1>CommandAI</h1>"
    f"<p style='color:var(--text-dim); margin:0; font-size:0.85rem;'>מערכת חכמה לניתוח פקודות מטכ\"ל · {role_label}</p>"
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

# ── Suggested questions (only when no conversation yet) ──
if not st.session_state.messages:
    st.markdown("<p style='color:var(--text-dim); font-size:0.85rem; text-align:center; margin-bottom:6px;'>שאלות נפוצות</p>", unsafe_allow_html=True)
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
if prompt := st.chat_input("שאל שאלה על פקודות..."):
    handle_question(prompt)
    st.rerun()

doc_count = len(get_loaded_docs_info())
st.caption(f"CommandAI · {doc_count} פקודות טעונות · v2.3")
