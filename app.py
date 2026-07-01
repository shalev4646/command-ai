import random
import traceback
import streamlit as st

try:
    from backend import get_ai_response, get_loaded_docs_info, ensure_pdfs_ingested, get_suggested_questions
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

st.markdown("""
<style>
/* ── Reset & base ── */
html, body, [data-testid="stAppViewContainer"] {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
    background-color: #0d1117;
    color: #e6edf3;
}

/* Hide Streamlit chrome */
#MainMenu, footer, header { visibility: hidden; }
[data-testid="stToolbar"] { display: none; }

/* ── Sidebar open/close button ── */
[data-testid="collapsedControl"] {
    background-color: #161b22 !important;
    border: 1px solid #30363d !important;
    border-radius: 8px !important;
    top: 0.6rem !important;
}
[data-testid="collapsedControl"] svg { fill: #3b82f6 !important; }

/* ── Main container ── */
.main .block-container {
    max-width: 480px;
    padding: 1rem 1rem 5rem 1rem;
    margin: 0 auto;
}

/* ── Typography ── */
h1 {
    font-size: 1.8rem !important;
    font-weight: 800 !important;
    color: #3b82f6 !important;
    margin: 0.5rem 0 0.2rem 0 !important;
    text-align: center;
}
p { font-size: 0.95rem !important; line-height: 1.5 !important; }

/* ── Buttons — compact, tappable ── */
div[data-testid="stButton"] > button {
    width: 100%;
    border-radius: 10px;
    background-color: #161b22;
    border: 1px solid #30363d;
    color: #c9d1d9;
    font-size: 0.95rem;
    font-weight: 600;
    padding: 12px 14px;
    min-height: 48px;
    line-height: 1.3;
    margin-bottom: 8px;
    white-space: normal;
    text-align: right;
    transition: border-color 0.15s, color 0.15s;
}
div[data-testid="stButton"] > button:hover {
    border-color: #3b82f6;
    color: #3b82f6;
}

/* ── Entry screen role buttons ── */
.st-key-role_soldier button,
.st-key-role_commander button {
    min-height: 72px !important;
    font-size: 1.1rem !important;
    background-color: #161b22 !important;
}

/* ── Search / text input ── */
.stTextInput > div > div > input {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    color: #e6edf3;
    padding: 12px 14px;
    font-size: 0.95rem;
    min-height: 46px;
}
.stTextInput > div > div > input::placeholder { color: #6e7681; }

/* ── Chat input ── */
[data-testid="stChatInput"] textarea {
    background-color: #161b22 !important;
    border: 1px solid #30363d !important;
    border-radius: 10px !important;
    color: #e6edf3 !important;
    font-size: 0.95rem !important;
}

/* ── Chat messages ── */
[data-testid="stChatMessage"] {
    background-color: #161b22;
    border: 1px solid #21262d;
    border-radius: 10px;
    padding: 10px 14px;
    margin-bottom: 8px;
}

/* ── Section gaps ── */
[data-testid="stVerticalBlock"] > div { margin-bottom: 0.2rem; }
.stMarkdown { margin-bottom: 0.2rem !important; }

/* ── Sidebar (hidden by default on mobile) ── */
[data-testid="stSidebar"] {
    background-color: #0d1117;
    border-right: 1px solid #21262d;
}

/* ── Caption / small text ── */
.stCaption, small { color: #6e7681 !important; font-size: 0.8rem !important; }

/* ── Spinner ── */
.stSpinner > div { border-top-color: #3b82f6 !important; }
</style>
""", unsafe_allow_html=True)

# ── Session state ──
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None
if "role" not in st.session_state:
    st.session_state.role = None
if "conversation_history" not in st.session_state:
    st.session_state.conversation_history = []

# ── Entry / role gate ──
if st.session_state.role is None:
    st.markdown("<div style='text-align:center; padding-top:2rem;'>", unsafe_allow_html=True)
    st.markdown("# CommandAI")
    st.markdown("<p style='text-align:center; color:#6e7681;'>בחר את סוג הכניסה שלך</p>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

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
    role_label = "חייל" if st.session_state.role == "soldier" else "מפקד"
    st.caption(f"מחובר כ-{role_label}")
    if st.button("🔄 החלף תפקיד", key="switch_role"):
        st.session_state.role = None
        st.rerun()
    st.markdown("---")
    st.markdown("### 📋 פקודות טעונות")
    docs = get_loaded_docs_info()
    if docs:
        for doc in docs:
            st.markdown(
                f"""<div style='background:#161b22; border:1px solid #30363d; border-radius:8px;
                    padding:8px 12px; margin-bottom:6px;'>
                    <div style='color:#3b82f6; font-size:0.78rem; font-weight:600;'>{doc['id']}</div>
                    <div style='color:#e6edf3; font-size:0.88rem; margin-top:2px;'>{doc['title']}</div>
                </div>""",
                unsafe_allow_html=True,
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
role_label = "חייל" if st.session_state.role == "soldier" else "מפקד"
st.markdown(
    f"<div style='text-align:center; padding: 0.5rem 0 0.8rem 0;'>"
    f"<h1>CommandAI</h1>"
    f"<p style='color:#6e7681; margin:0; font-size:0.85rem;'>מערכת חכמה לניתוח פקודות מטכ\"ל · {role_label}</p>"
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
    st.markdown("<p style='color:#6e7681; font-size:0.85rem; text-align:center; margin-bottom:6px;'>שאלות נפוצות</p>", unsafe_allow_html=True)
    for i, q in enumerate(st.session_state.suggested):
        if st.button(q, key=f"sug_{i}", use_container_width=True):
            queue_question(q)

# ── Process pending question ──
if st.session_state.pending_question:
    q = st.session_state.pending_question
    st.session_state.pending_question = None
    handle_question(q)
    st.rerun()

# ── Chat input (always visible) ──
if prompt := st.chat_input("שאל שאלה על פקודות צבאיות..."):
    handle_question(prompt)
    st.rerun()

doc_count = len(get_loaded_docs_info())
st.caption(f"CommandAI · {doc_count} פקודות טעונות · v2.2")
